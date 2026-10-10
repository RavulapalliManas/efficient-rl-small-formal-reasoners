"""No-model baseline: each fixed tactic alone as the whole proof of each curriculum-validation theorem.

`lean_eval_statements.py` runs `first | rfl | decide | ...`. `first` stops at the first tactic that does not
fail, even when that tactic leaves goals open, so it can miss a theorem that a later tactic proves alone.
This script tries every tactic as a separate attempt, with the same file context and `check` as the model
evaluation. `exact?` is included because the current forbidden-construct filter does not block it.

Memory: up to --workers Lean processes, each capped by `lean -M2048`, so about 8 GiB at 4 workers.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from lean_eval_statements import export  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_start_depth import check  # noqa: E402
from replay_training_proofs import direct_lean_call, question  # noqa: E402

TACTICS = ["rfl", "decide", "norm_num", "simp", "omega", "linarith", "aesop", "simp_all", "tauto", "exact?"]


def main():
    parser = argparse.ArgumentParser()
    for name in ("validation", "test", "checker", "environment", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    checker = load_checker(args.checker, args.environment, args.output)
    checker.lean_call = direct_lean_call(checker, args.environment.resolve(), args.timeout)
    validation, test = export(args.validation), export(args.test)
    spans = defaultdict(list)
    for r in validation + test:
        spans[r["file_path"]].append((r["start"][0], r["end"][0], r["declaration_header"]))
    sources, jobs, skipped = {}, [], {}
    for row in validation:
        others = {p: [s for s in v if p != row["file_path"] or s[0] != row["start"][0]] for p, v in spans.items()}
        q, skip = question(row, args.environment.resolve(), sources, "file", others)
        if q is None:
            skipped[row["full_name"]] = skip
            continue
        jobs.extend((row, q, i, tactic) for i, tactic in enumerate(TACTICS))

    def attempt(job):
        row, q, i, tactic = job
        result = check(checker, q, [], "  " + tactic, f"{row['theorem_id'][:16]}_t{i}")
        return {"theorem_id": row["theorem_id"], "full_name": row["full_name"], "tactic": tactic, **result}

    solved = defaultdict(set)
    with ThreadPoolExecutor(args.workers) as pool, (args.output / "attempts.jsonl").open("x") as out:
        for record in pool.map(attempt, jobs):
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            if record["status"] == "accepted":
                solved[record["tactic"]].add(record["full_name"])
    union = set().union(*solved.values())
    summary = {"validation_theorems": len(validation), "skipped": skipped, "attempts": len(jobs),
               "solved_by_tactic": {t: len(solved[t]) for t in TACTICS},
               "solved_any": len(union), "solved_without_exact_search": len(set().union(
                   *(v for t, v in solved.items() if t != "exact?"))), "solved_names": sorted(union)}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
