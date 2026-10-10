"""False-hypothesis sweep: which statements of the number-theory pool have hypotheses that prove False.

For every statement (all splits), each candidate body is `exfalso` followed by one closing tactic of TACTICS.
A statement is vacuous when at least one candidate is accepted. Candidates are scored by check_candidates of
probe_nt_pool.py, that is verify_group of bench_lean_verifier.py: the pool header, the target guard, no error,
axioms within propext, Classical.choice, Quot.sound, and --lean-timeout seconds per candidate (the probe's values).
Only the hypotheses before the colon are tested: `exfalso` does not introduce premises inside the goal.
A timeout or a REPL crash counts as a rejection, so the sweep can miss a vacuous statement; summary.json counts both.

Output: candidates.jsonl and checked.jsonl as in probe_nt_pool.py (append-only; --resume continues), then
vacuous.jsonl (name, split, tactics that proved False), vacuous_<split>.txt, and summary.json.

Memory: pool rows (<50 MiB) + one REPL per check worker (3.7 GB RSS measured, of which about 3.4 GB is shared
mathlib file pages). Run under 2 GiB per worker + 4 GiB.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval")]
from bench_lean_verifier import repl_context  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import append, check_candidates, read  # noqa: E402

TACTICS = ["simp_all", "omega", "linarith", "nlinarith", "aesop", "tauto", "norm_num at *", "contradiction"]


def main():
    parser = argparse.ArgumentParser()
    for name in ("pool", "environment", "checker", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--lean-timeout", type=float, default=20, help="seconds per candidate, as in probe_nt_pool.py")
    parser.add_argument("--setup-timeout", type=float, default=120)
    parser.add_argument("--repl-restart-after", type=int, default=400)
    parser.add_argument("--check-workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    pool = {r["name"]: r for r in map(json.loads, args.pool.open())}
    out = args.output
    out.mkdir(parents=True, exist_ok=args.resume)
    done = {(c["name"], c["sample"]) for c in read(out / "candidates.jsonl")}
    append(out / "candidates.jsonl", [{"arm": "exfalso", "name": n, "sample": i, "continuation": "  exfalso\n  " + t,
                                       "generated_tokens": 0}
                                      for n in pool for i, t in enumerate(TACTICS) if (n, i) not in done])
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, out)
    ctx = repl_context(checker, environment, args.repl, out, args.lean_timeout, args.setup_timeout, args.repl_restart_after)
    check_candidates(ctx, pool, out, args.check_workers)

    checked = read(out / "checked.jsonl")
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
    summary = {"statements": len(pool), "candidates": len(checked), "tactics": TACTICS,
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
