"""Nearest-proof baseline: how many held-out statements a copied train proof solves, with no learning.

For each statement of --split (default validation) of the pool, the --k most similar train statements that have a
Lean-accepted external proof (verify_external_proofs.py results) give their proof bodies. Similarity is the token
Jaccard of the statement text after the theorem name (statement() and tokens() of profile_external_proofs.py); ties go
to the smaller name. --library-exclude removes train statements from the library (for example the names excluded
from training as near-duplicates of held-out statements). Bodies come from build_nt_sft_data.py body_of (the proof's statement must equal the pool statement;
comments kept), distinct per train statement, in file order. Each body goes under the held-out header verbatim (no
renaming) and is scored by check_candidates of probe_nt_pool.py, that is verify_group, the scorer of the pass-rate map
and the GRPO reward. A statement is solved when any of its bodies is accepted.
Output (append-only; --resume continues the Lean checks): candidates.jsonl (arm "nearest_proof", sample = rank x 100 +
body index, with the neighbour and its Jaccard) and checked.jsonl as in probe_nt_pool.py; summary.json with solved
counts on all statements, on the non-trivial ones (minus the names --single-tactic-dir solved), and, with --vacuous,
minus the statements whose hypotheses prove False; each also with only the first body of every neighbour.

Memory: pool and proof rows (<300 MiB) + one REPL per check worker (3.7 GB RSS measured, about 3.4 GB of it shared
mathlib pages). Run under 4.5 GiB x --check-workers + 2 GiB.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from bench_lean_verifier import repl_context  # noqa: E402
from build_nt_sft_data import body_of  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import append, check_candidates, header, read  # noqa: E402
from profile_external_proofs import statement, tokens  # noqa: E402


def neighbours(target, library, k):
    """The k (name, Jaccard) pairs of `library` (name -> token set) most similar to the token set `target`."""
    scored = [(len(target & t) / max(len(target | t), 1), name) for name, t in library.items()]
    return [(name, j) for j, name in sorted(scored, key=lambda x: (-x[0], x[1]))[:k]]


def proof_library(pool, proof_paths):
    """Distinct Lean-accepted proof bodies per train statement, in file order."""
    train = {r["name"]: r for r in pool if r["split"] == "train"}
    bodies = defaultdict(list)
    for path in proof_paths:
        for line in path.open():
            r = json.loads(line)
            if r["status"] == "accepted" and r["name"] in train:
                body = body_of(r["text"], header(train[r["name"]]))
                if body and body not in bodies[r["name"]]:
                    bodies[r["name"]].append(body)
    return dict(bodies)


def solved_counts(names, checked, first_body_only):
    solved = {c["name"] for c in checked if c["status"] == "accepted" and (not first_body_only or c["body_index"] == 0)}
    return {"statements": len(names), "solved": len(solved & names)}


def main():
    parser = argparse.ArgumentParser()
    for name in ("pool", "environment", "checker", "repl", "output", "single_tactic_dir"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--proofs", type=Path, action="append", required=True, help="verify_external_proofs.py results")
    parser.add_argument("--vacuous", type=Path, help="names whose hypotheses prove False, one per line")
    parser.add_argument("--library-exclude", type=Path, help="train names whose proofs are not copied, one per line")
    parser.add_argument("--split", default="validation")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--lean-timeout", type=float, default=20, help="seconds per candidate, as in the probe and trainer")
    parser.add_argument("--setup-timeout", type=float, default=120)
    parser.add_argument("--repl-restart-after", type=int, default=400)
    parser.add_argument("--check-workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    out = args.output
    out.mkdir(parents=True, exist_ok=args.resume)
    pool = [json.loads(line) for line in args.pool.open()]
    held = [r for r in pool if r["split"] == args.split]
    bodies = proof_library(pool, args.proofs)
    removed = {line.strip() for line in args.library_exclude.open() if line.strip()} & set(bodies) if args.library_exclude else set()
    bodies = {name: b for name, b in bodies.items() if name not in removed}
    by_name = {r["name"]: r for r in pool}
    library = {name: tokens(statement(by_name[name]["formal_statement"])) for name in bodies}
    if not (out / "candidates.jsonl").exists():
        rows = []
        for r in held:
            for rank, (name, jaccard) in enumerate(neighbours(tokens(statement(r["formal_statement"])), library, args.k)):
                rows += [{"arm": "nearest_proof", "name": r["name"], "sample": rank * 100 + i, "continuation": body,
                          "generated_tokens": 0, "neighbour": name, "rank": rank, "body_index": i, "jaccard": jaccard}
                         for i, body in enumerate(bodies[name])]
        append(out / "candidates.jsonl", rows)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, out)
    ctx = repl_context(checker, environment, args.repl, out, args.lean_timeout, args.setup_timeout, args.repl_restart_after)
    check_candidates(ctx, by_name, out, args.check_workers)

    checked = read(out / "checked.jsonl")
    trivial = {r["name"] for r in read(args.single_tactic_dir / "statements.jsonl") if r.get("single_tactic", 0) > 0}
    vacuous = {line.strip() for line in args.vacuous.open() if line.strip()} if args.vacuous else set()
    names = {r["name"] for r in held}
    subsets = {"all": names, "non_trivial (minus single-tactic solved)": names - trivial}
    if args.vacuous:
        subsets["non_trivial minus vacuous"] = names - trivial - vacuous
    solvers = defaultdict(list)
    for c in checked:
        if c["status"] == "accepted":
            solvers[c["name"]].append({"neighbour": c["neighbour"], "rank": c["rank"], "jaccard": round(c["jaccard"], 3),
                                       "body_lines": c["continuation"].count("\n") + 1})
    statuses = defaultdict(int)
    for c in checked:
        statuses[c["status"]] += 1
    summary = {"split": args.split, "k": args.k, "library_exclude": str(args.library_exclude),
               "library_statements_removed": len(removed), "train_statements_with_proofs": len(bodies),
               "proof_bodies": sum(map(len, bodies.values())), "candidates": len(read(out / "candidates.jsonl")),
               "checked": len(checked), "statuses": dict(statuses),
               "lean_calls": sum(c.get("lean_calls", 0) for c in checked),
               "lean_seconds": sum(c.get("lean_seconds", 0.0) for c in checked),
               "single_tactic_solved_names": len(trivial & names), "vacuous_names": len(vacuous & names),
               "solved_all_bodies": {k: solved_counts(v, checked, False) for k, v in subsets.items()},
               "solved_first_body_per_neighbour": {k: solved_counts(v, checked, True) for k, v in subsets.items()},
               "solved": {name: {"single_tactic_solved": name in trivial, "vacuous": name in vacuous, "by": solvers[name]}
                          for name in sorted(solvers)},
               "wall_seconds_this_invocation": time.monotonic() - started}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("candidates", "checked", "statuses", "solved_all_bodies",
                                              "solved_first_body_per_neighbour")}, indent=1), flush=True)


if __name__ == "__main__":
    main()
