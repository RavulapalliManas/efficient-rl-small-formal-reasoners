"""Syntax SFT from existing replay-accepted bank proofs, at the project's Lean pin.

Every admitted theorem is rechecked with verify_group. Each emitted continuation
must reconstruct that exact proof with its given steps. Cut states come from the
bank's earlier Lean replay. These are file-context proofs, not standalone proofs.
Exclude evaluation/dev files, evaluation premises, and check_leakage.py hits.
Split by theorem, never by cut. Keep at most --max-units-per-theorem examples.

Memory: <=1,500 theorems x 100 KiB of metadata = 150 MiB; source cache <=300 MiB;
--workers x 2 GiB private Lean memory + 3.4 GiB shared oleans. Four workers need
about 12 GiB. Use a managed service with MemoryMax=16G. Results stream per theorem.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile


def same_proof(whole, cut):
    return cut["given_steps"] + cut["reference_rest"] == whole["reference_rest"]


def exclusion(unit, excluded_files, evaluation_names, heldout_names):
    if unit["file_path"] in excluded_files:
        return "excluded_file"
    if unit["full_name"] in evaluation_names | heldout_names:
        return "heldout_name"
    if set(unit["premises"]) & evaluation_names:
        return "evaluation_premise"
    return None


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval", "audit")]
    from bench_lean_verifier import repl_context, verify_group
    from check_leakage import Leakage, statement_of
    from prepare_grpo_pilot import load_checker
    from probe_start_depth import FORBIDDEN
    from replay_training_proofs import load

    parser = argparse.ArgumentParser()
    for name in ("bank", "pool", "splits", "minif2f", "exclude-files", "environment", "checker", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--train", type=Path, action="append", required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-theorems", type=int, default=1500)
    parser.add_argument("--max-units-per-theorem", type=int, default=4)
    parser.add_argument("--max-chars", type=int, default=4000)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--setup-timeout", type=float, default=120)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or args.max_theorems < 1 or args.max_units_per_theorem < 1:
        parser.error("Use 1..8 workers and positive data caps")
    started = time.monotonic()
    leak = Leakage(args.pool, args.splits, args.eval, args.minif2f)
    with args.pool.open() as input_stream:
        pool = {r["name"]: r for r in map(json.loads, input_stream)}
    excluded = set(args.exclude_files.read_text().split())
    evaluation_names, eval_spans = set(), defaultdict(list)
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as input_stream:
                for line in input_stream:
                    row = json.loads(line)
                    excluded.add(row["file_path"])
                    evaluation_names.add(row["full_name"])
                    eval_spans[row["file_path"]].append((row["start"][0], row["end"][0], row["declaration_header"]))
    units, counts = defaultdict(list), Counter()
    allowed_path = re.compile(r"^Mathlib/(?:Logic/|Data/(?:Set/|Finset/|Nat/|Int/|ZMod/)|NumberTheory/)")
    with args.bank.open() as input_stream:
        for line in input_stream:
            unit = json.loads(line)
            units[unit["theorem_id"]].append(unit)
    candidates = []
    for tid, group in sorted(units.items()):
        whole = next(u for u in group if u["k"] == 0)
        reason = exclusion(whole, excluded, evaluation_names, leak.held_out_names)
        if reason is None and not allowed_path.search(whole["file_path"]):
            reason = "outside_syntax_topics"
        hits = leak.hits(statement_of(whole["header"])) if reason is None else []
        if hits:
            reason = "leakage_hit"
        body = "\n".join(whole["reference_rest"])
        if reason is None and (FORBIDDEN.search(body) or len(body) > args.max_chars or "/-" in body or "--" in body):
            reason = "forbidden_or_long_proof"
        counts[reason or "eligible"] += 1
        if reason is None:
            candidates.append((whole, group))
    candidates = candidates[:args.max_theorems]
    wanted = {whole["theorem_id"] for whole, _ in candidates}
    args.output.mkdir(parents=True, exist_ok=False)
    sft_excluded = sorted(n for n in leak.sft_problems if leak.hits(statement_of(pool[n]["formal_statement"])))
    (args.output / "excluded_sft_problems.txt").write_text("".join(n + "\n" for n in sft_excluded))
    environment = args.environment.resolve()
    ctx = repl_context(load_checker(args.checker, environment, args.output), environment, args.repl,
                       args.output, args.timeout, args.setup_timeout, 400)
    ctx.eval_spans = eval_spans
    for path in args.train:
        ctx.rows.update({r["theorem_id"]: r for r in load(path) if r["theorem_id"] in wanted})
    if wanted - ctx.rows.keys():
        raise RuntimeError("Selected bank theorems are missing from the training exports")
    lean_version = subprocess.run([str(ctx.checker.LEAN / "lean"), "--version"],
                                  capture_output=True, text=True, check=True).stdout.strip()
    if "4.10.0-rc1" not in lean_version:
        raise RuntimeError("Lean version differs from the project pin")
    inputs = [args.bank, args.pool, args.minif2f, args.exclude_files, *args.train, *args.eval]
    inputs += sorted(args.splits.glob("*.txt"))
    run = {"lean": lean_version, "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5",
           "mathlib_revision_source": "Project pin and existing delivery; environment has no Git metadata",
           "inputs": {f"{p.parent.name}/{p.name}": sha256(p) for p in inputs},
           "scorer": {p.name: sha256(p) for p in (Path(sys.modules["bench_lean_verifier"].__file__),
                                                   Path(sys.modules["probe_start_depth"].__file__))},
           "workers": args.workers, "timeout": args.timeout, "setup_timeout": args.setup_timeout,
           "max_theorems": args.max_theorems, "max_units_per_theorem": args.max_units_per_theorem,
           "max_chars": args.max_chars, "state_evidence": "Existing bank replay, not re-traced by this build"}
    (args.output / "run.json").write_text(json.dumps(run, indent=2) + "\n")

    def verify(entry):
        whole, group = entry
        unit = dict(whole, kind="bank")
        return verify_group(ctx, unit, ["\n".join(whole["reference_rest"])])[0]

    with ThreadPoolExecutor(args.workers) as executor, (args.output / "checks.jsonl").open("x") as checks, \
            (args.output / "sft_train.jsonl").open("x") as train, (args.output / "sft_val.jsonl").open("x") as val:
        for (whole, group), result in zip(candidates, executor.map(verify, candidates)):
            checks.write(json.dumps({"theorem_id": whole["theorem_id"], "theorem": whole["full_name"], **result}) + "\n")
            checks.flush()
            counts["check_" + result["status"]] += 1
            if result["status"] != "accepted":
                continue
            held = int(hashlib.sha256(whole["full_name"].encode()).hexdigest(), 16) % 100 < 5
            for unit in sorted(group, key=lambda u: u["k"])[:args.max_units_per_theorem]:
                if not same_proof(whole, unit):
                    raise RuntimeError("Bank cut does not reconstruct the checked proof")
                given = "\n".join(unit["given_steps"])
                rest = "\n".join(unit["reference_rest"])
                prompt = ("Continue this Lean 4 tactic proof in its original mathlib file context.\n"
                          f"File: {unit['file_path']}\nTheorem: {unit['full_name']}\n"
                          f"Goal state:\n{unit['state']}\n\nProof so far:\n{unit['header']} := by\n{given}\n\n"
                          "Write the remaining tactics in a Lean code block. Keep their indentation.")
                if len(prompt) > args.max_chars or len(rest) > args.max_chars or not unit["state"]:
                    counts["unit_long_or_no_state"] += 1
                    continue
                row = {"format": "completion", "source": "mathlib_syntax", "theorem": unit["full_name"],
                       "task_id": unit["task_id"], "topic": unit["topic"], "has_comments": False,
                       "syntax": unit["structure_remaining"], "file_path": unit["file_path"],
                       "messages": [{"role": "user", "content": prompt},
                                    {"role": "assistant", "content": "```lean\n" + rest + "\n```"}]}
                (val if held else train).write(json.dumps(row, ensure_ascii=False) + "\n")
                counts["kept_val" if held else "kept_train"] += 1
            train.flush()
            val.flush()
            if sum(counts["check_" + s] for s in ("accepted", "rejected", "timeout", "crash")) % 100 == 0:
                print(json.dumps(dict(counts)), flush=True)
    manifest = {"counts": dict(counts), "additional_sft_exclusions": len(sft_excluded),
                "wall_seconds": time.monotonic() - started,
                "selection": "Pinned-bank order; theorem caps; theorem-level hash split",
                "limits": "File-context syntax examples; no measured training benefit"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
