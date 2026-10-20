"""False-hypothesis sweep: which statements of the number-theory pool have hypotheses that prove False.

For every statement (all splits), each candidate body is `exfalso` followed by one closing tactic of TACTICS.
A statement is vacuous when at least one candidate is accepted. Candidates are scored by check_candidates of
probe_nt_pool.py, that is verify_group of bench_lean_verifier.py: the pool header, the target guard, no error,
axioms within propext, Classical.choice, Quot.sound, and --lean-timeout seconds per candidate (the probe's values).
Without --intros only the hypotheses before the colon are tested: `exfalso` does not introduce premises inside the goal.
With --intros each candidate is `intros`, `exfalso`, then the tactic (arm "intros_exfalso"), so the goal used is False
from the named hypotheses plus every premise `intros` introduces: leading `∀ x,` binders and `P → ...` arrows of the
goal, also after `∀`. It cannot use a premise that `intros` does not introduce: an implication inside `¬`, `≠`, `↔`,
`∃`, `∧` or `∨`, or a hypothesis hidden behind a definition (`intros` introduces only syntactic binders, so `¬ P`
stays the goal and P never becomes a premise). A statement whose conclusion is literally False is `undetermined`,
because proving False from its premises is proving the statement itself.
A timeout or a REPL crash counts as a rejection, so the sweep can miss a vacuous statement; summary.json counts both.
It does not detect all vacuous statements: a failed tactic is not a consistency proof.
Per statement (theorems.jsonl): `contradictory` when a candidate is accepted; `consistent` only when the statement has
no premise at all (every binder before the colon has a plain data type such as ℕ, ℤ, ZMod n or ℕ → ℕ, no instance
binder, and no arrow in the conclusion); `undetermined` otherwise. `consistent` is a syntactic fact, not a Lean check.

Output: candidates.jsonl and checked.jsonl as in probe_nt_pool.py (append-only; --resume continues), then
vacuous.jsonl (name, split, tactics that proved False), vacuous_<split>.txt, theorems.jsonl (every statement of
--split with its status, premises, goal and per-tactic statuses) and summary.json.

Memory: pool rows (<50 MiB) + one REPL per check worker (3.7 GB RSS measured, of which about 3.4 GB is shared
mathlib file pages). Run under 2 GiB per worker + 4 GiB.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from bench_lean_verifier import repl_context  # noqa: E402
from heldout_screens import attempts_by_name, counts, load_pool, premises, statement, vacuity_status  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import append, check_candidates, read  # noqa: E402

TACTICS = ["simp_all", "omega", "linarith", "nlinarith", "aesop", "tauto", "norm_num at *", "contradiction"]
GOAL = {"exfalso": "False from the hypotheses before the colon",
        "intros_exfalso": "False from the hypotheses before the colon and the goal premises that intros introduces"}


def theorem_rows(pool, checked, arm):
    """One row per statement: vacuity status, the premises found, the goal used and every tactic's status."""
    by_name = attempts_by_name(checked, arm)
    rows = []
    for r in pool:
        status = [by_name.get(r["name"], {}).get(i, "missing") for i in range(len(TACTICS))]
        premise = premises(statement(r["formal_statement"]))
        rows.append({"name": r["name"], "split": r["split"], "topic": r["topic"],
                     "status": vacuity_status(premise, status), "premises": premise, "arm": arm, "goal": GOAL[arm],
                     "tactics": [{"tactic": t, "status": s} for t, s in zip(TACTICS, status)],
                     "complete": "missing" not in status})
    return rows


def main():
    parser = argparse.ArgumentParser()
    for name in ("pool", "environment", "checker", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--lean-timeout", type=float, default=20, help="seconds per candidate, as in probe_nt_pool.py")
    parser.add_argument("--setup-timeout", type=float, default=120)
    parser.add_argument("--repl-restart-after", type=int, default=400)
    parser.add_argument("--check-workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--intros", action="store_true", help="run `intros` first, so goal premises become hypotheses")
    parser.add_argument("--split", default="all", help="pool split to sweep (default: every row)")
    parser.add_argument("--tasks", type=Path, help="tasks_dev.jsonl or similar, for the topic of each name")
    args = parser.parse_args()
    started = time.monotonic()
    rows = load_pool(args.pool, None if args.split == "all" else args.split, args.tasks)
    pool = {r["name"]: r for r in rows}
    arm, prefix = ("intros_exfalso", "  intros\n  exfalso\n  ") if args.intros else ("exfalso", "  exfalso\n  ")
    out = args.output
    out.mkdir(parents=True, exist_ok=args.resume)
    done = {(c["arm"], c["name"], c["sample"]) for c in read(out / "candidates.jsonl")}
    append(out / "candidates.jsonl", [{"arm": arm, "name": n, "sample": i, "continuation": prefix + t,
                                       "generated_tokens": 0}
                                      for n in pool for i, t in enumerate(TACTICS) if (arm, n, i) not in done])
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, out)
    ctx = repl_context(checker, environment, args.repl, out, args.lean_timeout, args.setup_timeout, args.repl_restart_after)
    check_candidates(ctx, pool, out, args.check_workers)

    checked = [c for c in read(out / "checked.jsonl") if c["arm"] == arm and c["name"] in pool]
    theorems = theorem_rows(rows, checked, arm)
    with (out / "theorems.jsonl").open("w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in theorems)
    proved, statuses = defaultdict(list), defaultdict(Counter)
    for c in checked:
        statuses[TACTICS[c["sample"]]][c["status"]] += 1
        if c["status"] == "accepted":
            proved[c["name"]].append(TACTICS[c["sample"]])
    splits = sorted({r["split"] for r in pool.values()})
    with (out / "vacuous.jsonl").open("w") as f:
        for name in sorted(proved):
            f.write(json.dumps({"name": name, "split": pool[name]["split"], "tactics": proved[name]}, ensure_ascii=False) + "\n")
    for split in splits:
        (out / f"vacuous_{split}.txt").write_text("".join(n + "\n" for n in sorted(proved) if pool[n]["split"] == split))
    summary = {"statements": len(pool), "candidates": len(checked), "tactics": TACTICS, "arm": arm, "goal": GOAL[arm],
               "split": args.split, "status_counts": counts(theorems, "status"),
               "complete_statements": sum(r["complete"] for r in theorems),
               "statements_by_split": dict(Counter(r["split"] for r in pool.values())),
               "vacuous_by_split": {s: sum(pool[n]["split"] == s for n in proved) for s in splits},
               "vacuous_by_tactic": dict(Counter(t for ts in proved.values() for t in ts)),
               "statuses_by_tactic": {t: dict(statuses[t]) for t in TACTICS},
               "lean_calls": sum(c.get("lean_calls", 0) for c in checked),
               "lean_seconds": sum(c.get("lean_seconds", 0.0) for c in checked),
               "lean_timeout": args.lean_timeout, "check_workers": args.check_workers,
               "wall_seconds_last_invocation": time.monotonic() - started}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
