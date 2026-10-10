"""Number-theory knowledge items from mathlib at the pin: doc comment (informal) + Lean statement + proof as written.

Files: Mathlib/ files whose path matches NUMBER_THEORY of build_task_bank.py, minus every file that holds a LeanDojo
validation or test theorem (--eval) and every file in --exclude-files (the dev-set files). Items: `theorem` and `lemma`
commands with a doc comment, found by `declarations` (a scan for column-0 commands on the source with comments and
strings masked). Skipped and counted: no doc comment; wrapped in an `open/set_option/... in` line (the item text alone
lacks that context); no `:=` or `|` proof start at bracket depth 0; a FORBIDDEN construct of probe_start_depth.py in
the masked statement or proof.
Replay (every item, not a sample): one REPL per file. The first command is file_prefix of replay_training_proofs.py up
to the first item (the file's imports, `import Lean.Elab.Tactic`, `set_option autoImplicit false`, the source). After
an accepted check, the source lines after the item up to the next item run in the check's environment, which holds the
item as written; after a rejection, the item's own lines and the lines up to the next item run in the environment
before the check. So every item is checked in the context of question(..., "file"): its file up to its doc comment
(`#align` lines dropped, as in file_prefix). These files hold no evaluation
theorem, so nothing is blanked. The check command is one_command_check(item text) + the item text (doc comment through
proof, as in the source) + `#print axioms <full name>`. Accepted: no error, and the axioms of that line (axiom_report)
within ALLOWED_AXIOMS. The target cannot already exist: Lean would report it as already declared. After a timeout or
crash, the REPL restarts with file_prefix up to the next item.
Leakage: check_leakage.py on the statement; an item with a hit is not written. An item whose proof names a LeanDojo
evaluation theorem is flagged (cites_eval_theorem, as in profile_external_proofs.py) and kept.
Output (a new directory): replay.jsonl (every candidate: status, Lean time, leakage hits), items.jsonl (accepted, no
leakage hit), summary.json. sample200 marks the first 200 candidates by sha256("nt_knowledge_v1/" + file + "/" + name).

Memory: one REPL per worker; one file context peaked at 1.91 GiB RSS per process in bench_v1, plus ~3.4 GB of shared
olean pages: 8 workers about 19 GiB; run under a 24 GiB cap.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval", "audit")]
import bench_lean_verifier  # noqa: E402
from bench_lean_verifier import axiom_report, first_error, one_command_check, open_repl, repl_context, repl_output, split_prefix  # noqa: E402
from build_task_bank import NUMBER_THEORY  # noqa: E402
from check_leakage import Leakage, statement_of  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_start_depth import FORBIDDEN  # noqa: E402
from replay_training_proofs import ALLOWED_AXIOMS, file_prefix, source_scope  # noqa: E402

OPEN, CLOSE = "([{⟨⦃‹", ")]}⟩⦄›"
MODIFIERS = re.compile(r"(?:(?:private|protected|noncomputable|nonrec|unsafe|partial)\s+)*")
KEYWORD = re.compile(r"(theorem|lemma)\s+([^\s({:\[⦃]+)")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mask(source):
    """`source` with comments and string literals replaced by spaces; same length, line breaks kept."""
    out, i, n = list(source), 0, len(source)
    while i < n:
        if source.startswith("/-", i):
            j, depth = i, 0
            while j < n:
                if source.startswith("/-", j):
                    depth, j = depth + 1, j + 2
                elif source.startswith("-/", j):
                    depth, j = depth - 1, j + 2
                    if depth == 0:
                        break
                else:
                    j += 1
        elif source.startswith("--", i):
            j = source.find("\n", i)
            j = n if j < 0 else j
        elif source[i] == '"':
            j = i + 1
            while j < n and source[j] != '"':
                j += 2 if source[j] == "\\" else 1
            j += 1
        else:
            i += 1
            continue
        out[i:j] = ["\n" if c == "\n" else " " for c in source[i:j]]
        i = j
    return "".join(out)


def declarations(source):
    """Top-level theorem and lemma commands. A command starts at a line with code at column 0, or with a doc comment
    `/--` at column 0 (joined to the command after it), and ends at its last line with code before the next command.
    Fields: start, end, keyword_line (1-based lines; start is the doc comment line), text (the source lines start..end),
    name, full_name, docstring (or None), statement (keyword to the proof start), proof (after `:=`, or from the first
    `|` line), kind (tactic, term, equations; None when no proof start is found), wrapped (the command before ends in
    ` in`)."""
    masked = mask(source)
    raw, lines = source.split("\n"), masked.split("\n")
    offsets = [0]
    for line in raw:
        offsets.append(offsets[-1] + len(line) + 1)
    starts = [i for i, (r, m) in enumerate(zip(raw, lines)) if r[:1].strip() and (m[:1].strip() or r.startswith("/--"))]
    spans, pending = [], None
    for a, b in zip(starts, starts[1:] + [len(lines)]):
        a = a if pending is None else pending
        while b > a and not lines[b - 1].strip():
            b -= 1
        pending = a if b == a else None  # a doc comment alone joins the next command
        if b > a:
            spans.append((a, b))
    found, previous = [], ""
    for a, b in spans:
        text, m = source[offsets[a]:offsets[b] - 1], masked[offsets[a]:offsets[b] - 1]
        wrapped, previous = bool(re.search(r"\sin$", previous)), m.strip()
        p = len(m) - len(m.lstrip())
        doc = text[:p].strip()
        while m.startswith("@[", p):
            depth, p = 0, p + 1
            while p < len(m):
                depth += (m[p] == "[") - (m[p] == "]")
                p += 1
                if depth == 0:
                    break
            p += len(m[p:]) - len(m[p:].lstrip())
        p = MODIFIERS.match(m, p).end()
        key = KEYWORD.match(m, p)
        if key is None:
            continue
        depth, j, assign = 0, key.end(), None
        while j < len(m):
            depth += (m[j] in OPEN) - (m[j] in CLOSE)
            if depth == 0 and m.startswith(":=", j):
                assign = j
                break
            j += 1
        bar = None if assign is not None else re.search(r"\n[ \t]*\|", m[key.end():])
        if assign is not None:
            statement, proof = text[p:assign].rstrip(), text[assign + 2:].strip()
            kind = "tactic" if re.match(r"by\b", proof) else "term"
        elif bar is not None:
            cut = key.end() + bar.start() + 1
            statement, proof, kind = text[p:cut].rstrip(), text[cut:].strip(), "equations"
        else:
            statement, proof, kind = text[p:].rstrip(), None, None
        keyword_line = a + 1 + m[:p].count("\n")
        name = key[2]
        namespace = source_scope(source, keyword_line)[2]
        full_name = name.removeprefix("_root_.") if name.startswith("_root_.") else ".".join(x for x in (namespace, name) if x)
        found.append({"start": a + 1, "end": b, "keyword_line": keyword_line, "text": text, "name": name,
                      "full_name": full_name, "docstring": doc[3:-2].strip() if doc.startswith("/--") and doc.endswith("-/") else None,
                      "statement": statement, "proof": proof, "kind": kind, "wrapped": wrapped,
                      "forbidden": bool(FORBIDDEN.search(m[p:]))})
    return found


def replay_file(ctx, path, source, items):
    """Verdicts for one file's items, in source order, in one REPL (see the module docstring)."""
    lines = source.split("\n")
    segment = lambda a, b: "\n".join("" if x.startswith("#align") else x for x in lines[a - 1:b - 1]) + "\n"
    results, repl, env = [], None, None
    try:
        for i, item in enumerate(items):
            record = {"lean_calls": 0, "lean_seconds": 0.0, "lean_cpu_seconds": 0.0}
            if repl is None:
                head = file_prefix(source, item["start"])
                if split_prefix(head)[1].strip():
                    results.append(dict(record, status="boundary_error"))
                    continue
                repl, env = open_repl(ctx, head, f"{path.stem}_{i}", record)
                if repl is None:
                    results.append(dict(record, status="setup_error", first_error=record.get("setup_error")))
                    continue
            command = one_command_check(item["text"]) + item["text"] + "\n#print axioms " + item["full_name"] + "\n"
            record["lean_calls"] += 1
            start = time.monotonic()
            try:
                response, wall, _ = repl.send({"cmd": command, "env": env}, ctx.timeout)
            except (TimeoutError, EOFError) as exc:
                repl.close()
                repl = None
                results.append(dict(record, status="timeout" if isinstance(exc, TimeoutError) else "crash",
                                    lean_seconds=record["lean_seconds"] + time.monotonic() - start))
                continue
            code, output = repl_output(response)
            axioms = axiom_report(response, command.count("\n"), item["full_name"])
            ok = code == 0 and "error:" not in output and axioms is not None and set(axioms) <= ALLOWED_AXIOMS
            results.append(dict(record, status="accepted" if ok else "rejected", axioms=axioms,
                                first_error=None if ok else first_error(output), lean_seconds=record["lean_seconds"] + wall))
            if i + 1 == len(items):
                break
            # An accepted check's environment holds the item exactly as in the source; continue after its last line.
            # After a rejection, the source text of the item runs again from the environment before the check.
            env, text = (response["env"], segment(item["end"] + 1, items[i + 1]["start"])) if ok \
                else (env, segment(item["start"], items[i + 1]["start"]))
            if not text.strip():
                continue
            try:
                response, wall, _ = repl.send({"cmd": text, "env": env}, ctx.setup_timeout)
                results[-1]["segment_seconds"] = wall
            except (TimeoutError, EOFError):
                response = {}
            if "env" not in response or split_prefix(text)[1].strip():
                results[-1]["segment_error"] = response.get("message", "timeout, crash, or boundary")[:200]
                repl.close()
                repl = None
                continue
            results[-1]["segment_errors"] = sum(m["severity"] == "error" for m in response.get("messages", []))
            env = response["env"]
    finally:
        if repl is not None:
            repl.close()
    return results


def main():
    parser = argparse.ArgumentParser()
    for name in ("environment", "exclude_files", "pool", "splits", "minif2f", "checker", "repl", "output"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True, help="LeanDojo validation or test zip")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=60, help="seconds per item check")
    parser.add_argument("--setup-timeout", type=float, default=900, help="seconds per file prefix or segment")
    parser.add_argument("--jaccard", type=float, default=0.85)
    parser.add_argument("--minif2f-jaccard", type=float, default=0.7)
    parser.add_argument("--limit-files", type=int, help="first files only (smoke test)")
    args = parser.parse_args()
    started = time.monotonic()
    environment = args.environment.resolve()
    mathlib = environment / "mathlib"
    eval_files, eval_names = set(), []
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            for line in archive.open(archive.namelist()[0]):
                r = json.loads(line)
                eval_files.add(r["file_path"])
                eval_names += [r["full_name"]] + ([r["full_name"][4:]] if r["full_name"].startswith("Nat.") else [])
    cites = re.compile(r"(?<![\w.'])(" + "|".join(map(re.escape, sorted(eval_names, key=len, reverse=True))) + r")(?![\w'])")
    excluded = set(args.exclude_files.read_text().split())
    matched = sorted(str(p.relative_to(mathlib)) for p in (mathlib / "Mathlib").rglob("*.lean") if NUMBER_THEORY.search(str(p.relative_to(mathlib))))
    files = [f for f in matched if f not in eval_files | excluded][:args.limit_files]
    leak = Leakage(args.pool, args.splits, args.eval, args.minif2f, args.jaccard, args.minif2f_jaccard)

    counts, work = Counter(), []
    for f in files:
        source = (mathlib / f).read_text()
        items = []
        for d in declarations(source):
            counts["theorems"] += 1
            reason = ("no_docstring" if d["docstring"] is None else "wrapped_in_command" if d["wrapped"]
                      else "no_proof_start" if d["kind"] is None else "forbidden_construct" if d["forbidden"] else None)
            counts["skipped_" + reason if reason else "candidates"] += 1
            if reason is None:
                if d["full_name"] in leak.eval_names:
                    raise RuntimeError(f"{f}: {d['full_name']} is an evaluation theorem")
                items.append(dict(d, file=f))
        if items:
            work.append((f, source, items))
    args.output.mkdir(parents=True, exist_ok=False)
    checker = load_checker(args.checker, environment, args.output)
    ctx = repl_context(checker, environment, args.repl, args.output, args.timeout, args.setup_timeout, 1)
    work.sort(key=lambda w: -len(w[2]))  # long files first, for balance
    rank = lambda d: hashlib.sha256(f"nt_knowledge_v1/{d['file']}/{d['full_name']}".encode()).hexdigest()
    sample = {(d["file"], d["start"]) for d in sorted((d for _, _, its in work for d in its), key=rank)[:200]}
    statuses, drops, kept, lean_seconds, lean_calls = Counter(), Counter(), [], 0.0, 0
    with ThreadPoolExecutor(args.workers) as threads, (args.output / "replay.jsonl").open("x") as out, \
            (args.output / "items.jsonl").open("x") as items_out:
        for (f, _, items), results in zip(work, threads.map(lambda w: replay_file(ctx, mathlib / w[0], w[1], w[2]), work)):
            for d, res in zip(items, results):
                hits = leak.hits(statement_of(d["statement"]))
                in_sample = (d["file"], d["start"]) in sample
                statuses[res["status"]] += 1
                statuses["sample200_" + res["status"]] += in_sample
                lean_seconds, lean_calls = lean_seconds + res["lean_seconds"], lean_calls + res["lean_calls"]
                out.write(json.dumps({k: d[k] for k in ("file", "full_name", "start", "end", "kind")} |
                                     {k: res.get(k) for k in ("status", "first_error", "axioms", "lean_seconds", "segment_errors", "segment_error")} |
                                     {"leakage_hits": hits, "sample200": in_sample}, ensure_ascii=False) + "\n")
                if res["status"] != "accepted":
                    continue
                for h in hits:
                    drops[h["target"]] += 1
                if hits:
                    drops["items_dropped"] += 1
                    continue
                kept.append(d)
                items_out.write(json.dumps({k: d[k] for k in ("file", "full_name", "docstring", "statement", "proof", "kind", "start", "end")} |
                                           {"cites_eval_theorem": sorted(set(cites.findall(mask(d["proof"]))))}, ensure_ascii=False) + "\n")
            out.flush()
            items_out.flush()
    candidates = sum(len(w[2]) for w in work)
    summary = {
        "files_matching_number_theory": len(matched), "files_with_evaluation_theorem": len(set(matched) & eval_files),
        "files_in_exclude_list": len(set(matched) & (excluded - eval_files)), "files_scanned": len(files),
        "files_with_candidates": len(work), "counts": dict(counts), "candidates": candidates, "statuses": dict(statuses),
        "acceptance_rate": round(statuses["accepted"] / max(candidates, 1), 4),
        "sample200_acceptance_rate": round(statuses["sample200_accepted"] / max(min(200, candidates), 1), 4),
        "leakage_targets": leak.counts(), "leakage_drops_of_accepted": dict(drops), "items": len(kept),
        "items_by_kind": dict(Counter(d["kind"] for d in kept)),
        "items_citing_eval_theorem": sum(bool(cites.findall(mask(d["proof"]))) for d in kept),
        "lean_calls_item_checks": lean_calls, "lean_seconds_item_checks": round(lean_seconds, 1),
        "wall_seconds": round(time.monotonic() - started, 1), "workers": args.workers, "timeout": args.timeout,
        "setup_timeout": args.setup_timeout,
        "inputs": {f"{p.parent.name}/{p.name}": sha256(p) for p in (args.exclude_files, args.pool, args.minif2f, *args.eval)},
        "code": {Path(f).name: sha256(f) for f in (bench_lean_verifier.__file__, __file__)},
        "lean_version": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}
    with (args.output / "summary.json").open("x") as out:
        out.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=1, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
