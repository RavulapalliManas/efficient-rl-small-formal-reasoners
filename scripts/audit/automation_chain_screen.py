"""Automation-chain screen: which held-out statements a short, statement-independent automation chain proves.

Each chain of CHAINS (frozen; no statement-specific text) is one whole-proof candidate under the theorem header.
Candidates are scored by check_candidates of probe_nt_pool.py, that is verify_group of bench_lean_verifier.py, the
scorer of the pass-rate maps and the RL reward: pool header, target guard, no error, axioms within propext,
Classical.choice and Quot.sound, and --lean-timeout seconds per candidate (default 20). Every chain runs on every
statement, in name order then CHAINS order, so per-chain counts do not depend on which chain succeeded first.
A timeout or a REPL crash is not an acceptance; theorems.jsonl keeps timeout apart from rejected.

Output in --output (append-only; --resume skips every (name, chain) already in checked.jsonl, so a stage stopped at
its wall cap continues): candidates.jsonl and checked.jsonl as in probe_nt_pool.py, then theorems.jsonl (one row per
statement: every chain with its status, the accepting chains, the first accepting chain) and summary.json.
Limit: a chain that fails here can still prove the statement with a longer timeout or another simp set.

Memory: pool rows (<50 MiB) + one REPL per check worker (3.7 GB RSS measured, about 3.4 GB of it shared mathlib
pages). Run under 4.5 GiB x --check-workers + 2 GiB.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from bench_lean_verifier import repl_context  # noqa: E402
from heldout_screens import append, attempts_by_name, counts, load_pool, pending_candidates, read, write_json  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import check_candidates  # noqa: E402

ARM = "automation_chain"
CHAINS = (
    "omega",
    "norm_num",
    "simp",
    "simp_all",
    "decide",
    "linarith",
    "nlinarith",
    "positivity",
    "aesop",
    "tauto",
    "intros; omega",
    "simp_all <;> omega",
    "norm_num at * <;> omega",
    "norm_num [Nat.ModEq] at * <;> omega",
    "simp [Int.ModEq] at * <;> omega",
    "simp [Nat.dvd_iff_mod_eq_zero] at * <;> omega",
    "intros; norm_num at * <;> nlinarith",
)


def theorem_rows(pool, checked):
    """One row per pool statement with every chain's status (`missing` when not yet checked)."""
    by_name = attempts_by_name(checked, ARM)
    rows = []
    for r in pool:
        status = by_name.get(r["name"], {})
        chains = [{"chain": c, "status": status.get(i, "missing")} for i, c in enumerate(CHAINS)]
        accepting = [c["chain"] for c in chains if c["status"] == "accepted"]
        rows.append({"name": r["name"], "split": r["split"], "topic": r["topic"], "chains": chains,
                     "accepting_chains": accepting, "first_accepting_chain": accepting[0] if accepting else None,
                     "complete": all(c["status"] != "missing" for c in chains)})
    return rows


def summarize(rows, checked):
    statuses = Counter(c["status"] for r in rows for c in r["chains"])
    solved = [r for r in rows if r["accepting_chains"]]
    return {"chains": list(CHAINS), "statements": len(rows), "complete_statements": sum(r["complete"] for r in rows),
            "solved_any": len(solved), "solved_by_topic": counts(solved, "topic"), "statements_by_topic": counts(rows, "topic"),
            "accepted_by_chain": {c: sum(c in r["accepting_chains"] for r in rows) for c in CHAINS},
            "first_accepting_chain": dict(Counter(r["first_accepting_chain"] for r in solved)),
            "statuses": dict(sorted(statuses.items())),
            "lean_calls": sum(c.get("lean_calls", 0) for c in checked if c["arm"] == ARM),
            "lean_seconds": round(sum(c.get("lean_seconds", 0.0) for c in checked if c["arm"] == ARM), 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for name in ("pool", "environment", "checker", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--tasks", type=Path, help="tasks_dev.jsonl or similar, for the topic of each name")
    parser.add_argument("--split", default="validation", help="pool split to screen; 'all' for every row")
    parser.add_argument("--lean-timeout", type=float, default=20, help="seconds per candidate")
    parser.add_argument("--setup-timeout", type=float, default=120)
    parser.add_argument("--repl-restart-after", type=int, default=400)
    parser.add_argument("--check-workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    pool = load_pool(args.pool, None if args.split == "all" else args.split, args.tasks)
    out = args.output
    out.mkdir(parents=True, exist_ok=args.resume)
    append(out / "candidates.jsonl", pending_candidates([r["name"] for r in pool], CHAINS, ARM, read(out / "candidates.jsonl")))
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, out)
    ctx = repl_context(checker, environment, args.repl, out, args.lean_timeout, args.setup_timeout, args.repl_restart_after)
    check_candidates(ctx, {r["name"]: r for r in pool}, out, args.check_workers)

    checked = read(out / "checked.jsonl")
    rows = theorem_rows(pool, checked)
    with (out / "theorems.jsonl").open("w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    summary = dict(summarize(rows, checked), split=args.split, lean_timeout=args.lean_timeout,
                   check_workers=args.check_workers, wall_seconds_this_invocation=round(time.monotonic() - started, 1))
    write_json(out / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
