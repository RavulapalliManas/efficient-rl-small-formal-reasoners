"""Integration test: RL tasks score the same in the pass-rate map (probe_nt_pool.py) and in the GRPO reward.

Needs the pinned Lean environment and the REPL (Lean test group; not part of unittest discovery). Exit code 1 on any
failed requirement.
1. The trainer's task guard (task_units of run_lean_grpo.py) accepts every task of --tasks: train names only, headers
   equal to the pool headers, no FORBIDDEN given text; with --exclude-names, no task name is excluded.
2. Candidates for every task with a reference (prefix tasks) and for --whole whole tasks: the reference rest
   (must be accepted), the reference rest followed by an indented `theorem` (must be rejected), "  sorry" and an
   empty text (rejected without Lean), and "  simp" (no requirement). P1: check_candidates of probe_nt_pool.py (the map);
   P2: score of run_lean_grpo.py on the same texts followed by the closing fence and more text (the reward).
   Requirement: P1 = P2 on every status.
3. With --probe-dir (a probe_nt_pool.py --tasks run), its checked candidates are scored again by P2; the statuses must
   be equal.

Memory: --workers REPLs at once (3.7 GB RSS each measured, about 3.4 GB of it shared mathlib pages) + rows.
Run under 4.5 GiB x --workers + 2 GiB.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval", "train")]
from bench_lean_verifier import repl_context  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import append, check_candidates, read  # noqa: E402
from run_lean_grpo import excluded_names, score, task_units  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    for name in ("tasks", "pool", "checker", "environment", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--exclude-names", type=Path)
    parser.add_argument("--probe-dir", type=Path, help="output of probe_nt_pool.py --tasks")
    parser.add_argument("--whole", type=int, default=20, help="whole tasks to include (first by task_id)")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--lean-timeout", type=float, default=20)
    parser.add_argument("--setup-timeout", type=float, default=120)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    failures, report = [], {}
    with args.tasks.open() as input_stream:
        tasks = [json.loads(line) for line in input_stream]
    with args.pool.open() as input_stream:
        pool = [json.loads(line) for line in input_stream]
    units = task_units(tasks, pool)  # raises on any guard failure
    report["tasks"] = len(tasks)
    report["kinds"] = dict(Counter(t["kind"] for t in tasks))
    if args.exclude_names:
        with args.exclude_names.open() as input_stream:
            hits = excluded_names(units, {line.strip() for line in input_stream if line.strip()})
        report["excluded_name_hits"] = hits
        if hits:
            failures.append(f"{len(hits)} task names are in --exclude-names")
    by_id = {u["task_id"]: u for u in units}
    chosen = [t for t in tasks if t["reference_rest"]] + sorted((t for t in tasks if t["kind"] == "whole"),
                                                                key=lambda t: t["task_id"])[:args.whole]
    cases = {"reference": lambda t: t["reference_rest"], "reference_then_theorem": lambda t: t["reference_rest"] + "\n  theorem extra_thm : True := trivial",
             "sorry": lambda t: "  sorry", "empty": lambda t: "", "simp": lambda t: "  simp"}
    candidates = [{"arm": "control", "name": t["task_id"], "sample": i, "case": case, "continuation": make(t), "generated_tokens": 0}
                  for t in chosen for i, (case, make) in enumerate(cases.items())
                  if t["reference_rest"] or case not in ("reference", "reference_then_theorem")]
    append(args.output / "candidates.jsonl", candidates)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)
    ctx = repl_context(checker, environment, args.repl, args.output, args.lean_timeout, args.setup_timeout, 400)
    try:
        check_candidates(ctx, {t["task_id"]: t for t in tasks}, args.output, args.workers)  # P1
        p1 = {(c["name"], c["sample"]): c for c in read(args.output / "checked.jsonl")}
        with ThreadPoolExecutor(args.workers) as executor:
            _, _, p2 = score(ctx, by_id, [c["name"] for c in candidates],
                             [c["continuation"] + "\n```\ntrailing text" for c in candidates], executor)
            probe_rows = read(args.probe_dir / "checked.jsonl") if args.probe_dir else []
            _, _, p3 = score(ctx, by_id, [c["name"] for c in probe_rows],
                             [c["continuation"] + "```" for c in probe_rows], executor) if probe_rows else (None, None, [])
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()
    rows = []
    for c, r in zip(candidates, p2):
        first = p1[(c["name"], c["sample"])]
        rows.append({"task_id": c["name"], "case": c["case"], "p1": first["status"], "p2": r["status"],
                     "p1_first_error": first.get("first_error"), "p2_first_error": r.get("first_error")})
    with (args.output / "comparison.jsonl").open("x") as out:
        out.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    differ = [r for r in rows if r["p1"] != r["p2"]]
    if differ:
        failures.append(f"P1 != P2 on {len(differ)} of {len(rows)} candidates")
    by_case = {case: dict(Counter(r["p1"] for r in rows if r["case"] == case)) for case in cases}
    for case, wanted in (("reference", {"accepted"}), ("reference_then_theorem", {"rejected"}),
                         ("sorry", {"forbidden_construct"}), ("empty", {"empty_or_top_level_line"})):
        bad = [r["task_id"] for r in rows if r["case"] == case and r["p1"] not in wanted]
        if bad:
            failures.append(f"{case}: {len(bad)} tasks not {sorted(wanted)}, for example {bad[:3]}")
    report.update(candidates=len(rows), tasks_checked=len(chosen), p1_equals_p2=len(rows) - len(differ),
                  statuses_by_case=by_case, rejected_references=[(r["task_id"], r["p1"], r["p1_first_error"])
                                                                  for r in rows if r["case"] == "reference" and r["p1"] != "accepted"],
                  lean_calls=sum(c.get("lean_calls", 0) for c in p1.values()))
    if args.probe_dir:
        mismatch = [(c["name"], c["sample"]) for c, r in zip(probe_rows, p3) if c["status"] != r["status"]]
        report.update(probe_dir=str(args.probe_dir), probe_candidates=len(probe_rows), probe_equals_p2=len(probe_rows) - len(mismatch),
                      probe_statuses=dict(Counter(c["status"] for c in probe_rows)))
        if mismatch:
            failures.append(f"probe run != P2 on {len(mismatch)} of {len(probe_rows)}")
    report["failures"] = failures
    (args.output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=1, ensure_ascii=False)[:4000], flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
