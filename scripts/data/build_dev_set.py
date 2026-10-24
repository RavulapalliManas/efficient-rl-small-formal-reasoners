"""Build a development set of LeanDojo Benchmark 4 random/val (+ test) theorems for comparing Lean 4 proof models.

Selection rule, fixed before any probe outcome was seen:
1. Eligible: theorems of the --splits splits (default `val`, so v1 reproduces; `val,test` adds random/test) with
   >= 1 traced tactic, whose file_path lies under one of the task bank v3
   prefixes (the "prefixes" list of the export_task_pool.py manifest), that are not evaluation theorems and
   do not lie in an evaluation file.
2. The eligible theorems of all selected splits, in archive order (splits in --splits order), are shuffled together
   with --seed. Each row keeps its split in `official_split`; probe.jsonl records it as `split`, and summary.json
   counts eligibility, probes, and kept theorems by split. Each row has the evaluation export format
   of prepare_leandojo.py: header, imports, and context digest from corpus.jsonl, and the same theorem_id digest.
   `family` follows export_task_pool.py (the two path parts after `Mathlib/`); the config families of
   prepare_leandojo.py cover only 51 files.
3. In shuffled order, each theorem gets the file-context ready probe of lean_eval_statements.py
   (`trace_state` + `sorry`), with the evaluation declarations and the dev declarations kept so far in the same
   file blanked. A probe waits until earlier probes of the same file finish, so the blanking is deterministic.
   A theorem is kept only if its status is "ready"; selection stops at --target kept theorems. A row that
   question() cannot build gets a status without a Lean call; "unsupported_import" marks a corpus import that
   module_name() rejects, which would also stop lean_eval_statements.py.
4. Confirmation: each kept theorem's Lean source is rebuilt with every other kept declaration blanked, as
   lean_eval_statements.py does with this export. A changed source is probed again; a theorem that is then not
   ready is dropped and selection resumes.
Before any probe, the row builder must reproduce the theorem_id of every evaluation row, or the script stops.

Outputs in --output: dev_set.zip (dev.jsonl first, so lean_eval_statements.export reads it; probe.jsonl;
files.txt; summary.json), contamination.json (file overlap with the SFT data and the --tasks files, reported under
--tasks-label, default task_bank_v3; a task row without `status` is not counted by status), lean/ (probes).

Memory: --workers Lean processes, each capped by `lean -M2048` (8 GiB at 4 workers), plus Python < 1 GiB:
val.json and test.json (13.6 MB) loaded; train.json streamed with ijson into a name -> files map (~120k names);
corpus.jsonl and the SFT files streamed line by line; probed mathlib source files cached.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import datetime
import hashlib
import json
from pathlib import Path
import random
import re
import resource
import subprocess
import sys
import tarfile
import time
import zipfile

import ijson

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from lean_eval_statements import export  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from prepare_leandojo import declaration_header, digest, encode, normalize, similarity  # noqa: E402
from replay_training_proofs import direct_lean_call, question  # noqa: E402

PROBE = "  trace_state\n  sorry"
ID_FIELDS = ("url", "commit", "file_path", "full_name", "start", "end", "declaration_header", "imports",
             "context_digest")


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def under(path, prefixes):
    """The prefix rule of export_task_pool.py."""
    return next((p for p in prefixes if path.startswith(p + "/") or path == p + ".lean"), None)


def export_row(original, entry, split):
    """The export row of prepare_leandojo.py, with the family rule of export_task_pool.py."""
    row = {k: original[k] for k in ("url", "commit", "file_path", "full_name", "start", "end")}
    row.update(entry, family="/".join(row["file_path"].split("/")[1:3]).removesuffix(".lean"), official_split=split)
    row["theorem_id"] = digest({k: row[k] for k in ID_FIELDS})
    return row


def read_archive(path, eval_keys, prefixes, splits):
    """One streaming pass: selected split rows, evaluation originals, corpus entries, and train name -> files."""
    selected, originals, corpus, train_files, metadata = {}, {}, {}, defaultdict(set), None
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            name = member.name.removeprefix("leandojo_benchmark_4/")
            if name == "random/train.json":
                for row in ijson.items(archive.extractfile(member), "item"):
                    train_files[row["full_name"]].add(row["file_path"])
            elif name in ("random/val.json", "random/test.json"):
                rows = json.load(archive.extractfile(member))
                originals.update({k: r for r in rows if (k := (r["file_path"], r["full_name"])) in eval_keys})
                if (split := Path(name).stem) in splits:
                    selected[split] = rows
            elif name == "corpus.jsonl":  # after val.json and test.json in the archive
                wanted = eval_keys | {(r["file_path"], r["full_name"]) for s in splits for r in selected[s]
                                      if r["traced_tactics"] and under(r["file_path"], prefixes)}
                paths = {p for p, _ in wanted}
                for line in archive.extractfile(member):
                    file = json.loads(line)
                    if file["path"] not in paths:
                        continue
                    context = digest(file)
                    for premise in file["premises"]:
                        if (file["path"], premise["full_name"]) in wanted:
                            corpus[(file["path"], premise["full_name"])] = {
                                "declaration_header": declaration_header(premise["code"]),
                                "imports": file["imports"], "context_digest": context}
            elif name == "metadata.json":
                metadata = json.load(archive.extractfile(member))
    return selected, originals, corpus, train_files, metadata


def probe(checker, source, stem):
    """The ready probe and status rule of lean_eval_statements.py."""
    try:
        result = checker.lean_call(source, stem)
    except RuntimeError as exc:  # direct_lean_call raises on a verifier infrastructure failure
        return {"status": "verifier_failure", "first_error": str(exc)[:300]}
    output = result["output"]
    ok = result["exit_code"] == 0 and "TARGET_ABSENT" in output and "error:" not in output
    record = {"status": "ready" if ok else ("target_present" if re.search(r"error: TARGET_PRESENT", output)
                                            else "context_error"),
              "lean_status": result["status"], "exit_code": result["exit_code"],
              "wall_seconds": round(result["wall_seconds"], 3)}
    if ok:
        record["state"] = "\n".join(l for l in output.split("TARGET_ABSENT", 1)[-1].splitlines()
                                    if ".lean:" not in l and not l.startswith("warning")).strip()
    else:
        record["first_error"] = next(iter(re.findall(r"error: ([^\n]*)", output)), None)
    return record


def contamination(dev, train_files, sft_dir, task_paths, tasks_label):
    dev_files = {r["file_path"] for r in dev}
    report = {"read_utc": utc(), "dev_theorems": len(dev), "dev_files": len(dev_files), "sft": {}}
    for split in ("sft_train", "sft_val"):
        with (sft_dir / f"{split}.jsonl").open() as source:
            names = {json.loads(line)["theorem"] for line in source}
        files, unmapped, several = defaultdict(set), 0, 0
        for name in names:
            paths = train_files.get(name)
            unmapped += not paths
            several += bool(paths) and len(paths) > 1
            for path in paths or ():
                files[path].add(name)
        shared = dev_files & files.keys()
        report["sft"][split] = {
            "theorems": len(names), "names_not_in_random_train": unmapped, "names_in_several_files": several,
            "dev_theorems_whose_file_holds_sft_theorems": sum(r["file_path"] in shared for r in dev),
            "dev_files_holding_sft_theorems": len(shared),
            "sft_theorems_in_dev_files": len(set().union(*(files[p] for p in shared)))}
    shards, ids, total = [], set(), Counter()
    for path in task_paths:
        stat = path.stat()
        *lines, tail = path.read_bytes().split(b"\n")
        records, unparsed = [], 0
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                unparsed += 1
        hits = [r for r in records if r["file_path"] in dev_files]
        ids.update(r["theorem_id"] for r in hits)
        total.update(records=len(records), records_in_dev_files=len(hits))
        shards.append({"path": str(path), "bytes": stat.st_size,
                       "mtime_utc": datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).isoformat(timespec="seconds"),
                       "records": len(records), "unparsed_lines": unparsed, "partial_last_line": bool(tail.strip()),
                       "records_in_dev_files": len(hits),
                       "status_in_dev_files": dict(Counter(r["status"] for r in hits if "status" in r))})
    report[tasks_label] = {"note": "snapshot of files that other jobs may still be writing", "shards": shards,
                           **total, "distinct_theorems_in_dev_files": len(ids)}
    return report


def main():
    parser = argparse.ArgumentParser()
    for name in ("archive", "pool_manifest", "checker", "environment", "sft", "output"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--tasks", type=Path, nargs="*", default=[])
    parser.add_argument("--tasks-label", default="task_bank_v3")
    parser.add_argument("--splits", type=lambda s: s.split(","), default=["val"])
    parser.add_argument("--target", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    started, started_utc = time.monotonic(), utc()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "lean").mkdir()
    environment = args.environment.resolve()
    checker = load_checker(args.checker, args.environment, args.output)
    checker.lean_call = direct_lean_call(checker, environment, args.timeout)
    lean_version = subprocess.run([str(checker.LEAN / "lean"), "--version"], capture_output=True, text=True).stdout.strip()
    prefixes = json.loads(args.pool_manifest.read_text())["prefixes"]
    evals = [r for path in args.eval for r in export(path)]
    eval_keys = {(r["file_path"], r["full_name"]) for r in evals}
    eval_files = {r["file_path"] for r in evals}
    with args.archive.open("rb") as source:
        md5 = hashlib.file_digest(source, "md5").hexdigest()
    selected, originals, corpus, train_files, metadata = read_archive(args.archive, eval_keys, prefixes, args.splits)
    print(json.dumps({"archive_read_seconds": round(time.monotonic() - started, 1)}), flush=True)

    reproduced = sum(k in originals and k in corpus and
                     export_row(originals[k], corpus[k], r["official_split"])["theorem_id"] == r["theorem_id"]
                     for r in evals for k in [(r["file_path"], r["full_name"])])
    if reproduced != len(evals):
        raise ValueError(f"Row builder reproduces {reproduced} of {len(evals)} evaluation theorem_ids")

    by_split, eligible = {}, []
    for split in args.splits:
        counts = by_split[split] = Counter({f"{split}_theorems": len(selected[split])})
        for original in selected[split]:
            key = (original["file_path"], original["full_name"])
            if not original["traced_tactics"]:
                continue
            counts["with_traced_tactics"] += 1
            if not under(original["file_path"], prefixes):
                continue
            counts["under_prefixes"] += 1
            if key in eval_keys:
                counts["excluded_evaluation_theorem"] += 1
            elif original["file_path"] in eval_files:
                counts["excluded_evaluation_file"] += 1
            else:
                eligible.append(export_row(original, corpus[key], split) if key in corpus else
                                {k: original[k] for k in ("file_path", "full_name", "start", "end")}
                                | {"official_split": split})
        counts["eligible"] = sum(r["official_split"] == split for r in eligible)
    counts = Counter()
    for split_counts in by_split.values():
        counts.update(split_counts)
    del selected
    random.Random(args.seed).shuffle(eligible)

    eval_spans = defaultdict(list)
    for r in evals:
        eval_spans[r["file_path"]].append((r["start"][0], r["end"][0], r["declaration_header"]))
    members, sources, statuses, confirmed = {}, {}, Counter(), Counter()
    log = (args.output / "probe.jsonl").open("x")

    def blanks(row):
        """Spans blanked for `row`, built as lean_eval_statements.py builds them from its exports."""
        spans = defaultdict(list, {p: list(v) for p, v in eval_spans.items()})
        for m in members.values():
            spans[m["row"]["file_path"]].append((m["row"]["start"][0], m["row"]["end"][0], m["row"]["declaration_header"]))
        return {p: [s for s in v if p != row["file_path"] or s[0] != row["start"][0]] for p, v in spans.items()}

    def build(row):
        if "theorem_id" not in row:
            return None, "missing_declaration", 0
        others = blanks(row)
        try:
            q, skip = question(row, environment, sources, "file", others)
        except OSError:
            return None, "source_missing", 0
        except ValueError as exc:  # module_name rejects package imports other than lean4 and batteries
            if not str(exc).startswith("Unsupported import path"):
                raise
            return None, "unsupported_import", 0
        return (checker.source_for(q, PROBE) if q else None), skip, len(others.get(row["file_path"], ()))

    def write(row, order, phase, record):
        record = {"order": order, "phase": phase, "theorem_id": row.get("theorem_id"), "full_name": row["full_name"],
                  "file_path": row["file_path"], "family": row.get("family"), "split": row["official_split"], **record}
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.flush()
        print(json.dumps({k: record[k] for k in ("order", "phase", "status")} | {"kept": len(members)}), flush=True)

    next_index = 0
    with ThreadPoolExecutor(args.workers) as pool:
        for round_ in range(1, len(eligible) + 1):
            inflight = {}
            while True:  # selection, in shuffled order
                while (len(inflight) < args.workers and next_index < len(eligible)
                       and len(members) + len(inflight) < args.target):
                    row, order = eligible[next_index], next_index
                    if any(r["file_path"] == row["file_path"] for r, _, _ in inflight.values()):
                        break  # wait for the earlier probe of the same file
                    next_index += 1
                    source, skip, blanked = build(row)
                    if source is None:
                        statuses[skip] += 1
                        write(row, order, "select", {"status": skip})
                        continue
                    inflight[pool.submit(probe, checker, source, row["theorem_id"] + "_state")] = (row, order, (sha(source), blanked))
                if not inflight:
                    break
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for future in done:
                    row, order, (digest_, blanked) = inflight.pop(future)
                    record = future.result()
                    statuses[record["status"]] += 1
                    if record["status"] == "ready":
                        members[row["theorem_id"]] = {"row": row, "order": order, "sha256": digest_}
                    write(row, order, "select", {"blanked_in_file": blanked, "source_sha256": digest_, **record})
            jobs = []  # confirmation with the final blanking
            for tid, m in sorted(members.items(), key=lambda kv: kv[1]["order"]):
                source, skip, blanked = build(m["row"])
                if source is None or sha(source) != m["sha256"]:
                    jobs.append((tid, m, source, skip, blanked))
            dropped = 0
            futures = {j[0]: pool.submit(probe, checker, j[2], f"{j[0]}_confirm{round_}") for j in jobs if j[2] is not None}
            for tid, m, source, skip, blanked in jobs:
                if source is None:
                    record = {"status": skip}
                else:
                    record = {"blanked_in_file": blanked, "source_sha256": sha(source), **futures[tid].result()}
                confirmed[record["status"]] += 1
                if record["status"] == "ready":
                    m["sha256"] = sha(source)
                else:
                    dropped += 1
                    del members[tid]
                write(m["row"], m["order"], f"confirm{round_}", record)
            confirmed["dropped"] += dropped
            if not dropped:
                break
    log.close()

    dev = [m["row"] for m in sorted(members.values(), key=lambda m: m["order"])]
    files = sorted({r["file_path"] for r in dev})
    eval_norm = [normalize(r["declaration_header"]) for r in evals]
    with (args.output / "probe.jsonl").open() as input_stream:
        probed = [json.loads(line) for line in input_stream]
    summary = {
        "rule": "eligible " + " and ".join(f"random/{s}" for s in args.splits) + " theorems (>=1 traced tactic, task "
                "bank v3 prefix, not an evaluation theorem or file), shuffled with the seed, file-context ready probe "
                "in order, first target ready theorems kept",
        "splits": args.splits,
        "seed": args.seed, "target": args.target, "timeout_seconds": args.timeout, "workers": args.workers,
        "archive": str(args.archive), "archive_md5": md5, "source_metadata": metadata, "lean_version": lean_version,
        "lean_toolchain": (environment / "mathlib/lean-toolchain").read_text().strip(),
        "environment": str(environment), "prefixes": prefixes, "evaluation_exports": [str(p) for p in args.eval],
        "evaluation_theorems": len(evals), "evaluation_files": len(eval_files),
        "evaluation_theorem_ids_reproduced": reproduced, "eligibility": dict(counts),
        "eligibility_by_split": {s: dict(c) for s, c in by_split.items()},
        "eligible_by_prefix": dict(Counter(under(r["file_path"], prefixes) for r in eligible)),
        "probed_theorems": sum(r["phase"] == "select" for r in probed), "status_counts": dict(statuses),
        "probed_by_split": dict(Counter(r["split"] for r in probed if r["phase"] == "select")),
        "confirmation_status_counts": dict(confirmed), "lean_calls": sum("source_sha256" in r for r in probed),
        "dev_theorems": len(dev), "dev_by_split": dict(Counter(r["official_split"] for r in dev)),
        "dev_files": len(files),
        "family_rule": "export_task_pool.py: '/'.join(file_path.split('/')[1:3]) without .lean",
        "family_counts": dict(Counter(r["family"] for r in dev).most_common()),
        "prefix_counts": dict(Counter(under(r["file_path"], prefixes) for r in dev).most_common()),
        "near_duplicates_of_evaluation_statements": sum(
            any(similarity(normalize(r["declaration_header"]), e) >= 0.9 for e in eval_norm) for r in dev),
        "not_applied": "prepare_leandojo.py header filters (excluded_header_tokens, ambiguous_header)",
        "start_utc": started_utc, "end_utc": utc(), "wall_seconds": round(time.monotonic() - started, 1),
        "python_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "largest_lean_peak_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with zipfile.ZipFile(args.output / "dev_set.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("dev.jsonl", "".join(encode(r) + "\n" for r in dev))
        archive.write(args.output / "probe.jsonl", "probe.jsonl")
        archive.writestr("files.txt", "".join(p + "\n" for p in files))
        archive.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("eligibility", "probed_theorems", "status_counts", "dev_theorems")}), flush=True)
    report = contamination(dev, train_files, args.sft, args.tasks, args.tasks_label)
    report["total_wall_seconds"] = round(time.monotonic() - started, 1)
    (args.output / "contamination.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
