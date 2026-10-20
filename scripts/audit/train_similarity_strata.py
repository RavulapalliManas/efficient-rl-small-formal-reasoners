"""TRAIN-similarity screen: how close each held-out statement is to a TRAIN statement, with an optional copy baseline.

Each held-out statement (pool rows of --split) is compared with every TRAIN statement of the --train files (for
example the canonical roots `actor/train_tasks.jsonl` and the full-proof SFT rows that carry the alias variants;
rows give `header`, `formal_statement`, or a whole proof in `text` or the assistant message). Measures, all on the
statement after the theorem name with binders renamed by build_nt_pool.key:
- token Jaccard (tokens of profile_external_proofs, the selection builder's measure), maximum over TRAIN;
- character 4-gram Jaccard on the whitespace-collapsed text, maximum over TRAIN;
- numeric template match (numerals -> NUM, the selection builder's template key);
- construction signature match: binder renaming, every numeral -> N, every MOD/ZMOD modulus -> N, and the
  top-level conjuncts of each hypothesis binder sorted (heldout_screens.signature).
max_similarity is the larger Jaccard; band is >=0.7, 0.5-0.7, 0.3-0.5 or <0.3. The nearest TRAIN statement ranks
signature match first, then template match, then max_similarity, ties to the smaller name.

--copy-baseline (REWARD_HACKING L3) reads TRAIN proofs only (--train-proofs; a proof whose name is a held-out name
stops the run): it submits every distinct proof body of the nearest TRAIN statement that has a proof, verbatim (no
hypothesis renaming), under the held-out header, scored by check_candidates of probe_nt_pool.py with the RL reward's
verify_group and --lean-timeout seconds per candidate. --resume skips candidates already in checked.jsonl.
Output: rows.jsonl (one row per held-out statement) and summary.json; with --copy-baseline also candidates.jsonl and
checked.jsonl. Text similarity is a duplicate control; it does not certify that two statements share a proof idea.

Memory: <= 4,096 pool rows and TRAIN rows (<300 MiB with SFT messages) + 4-gram sets (<250 MiB worst case); without
--copy-baseline run under 1 GiB. --copy-baseline adds one REPL per check worker (3.7 GB RSS measured, about 3.4 GB
shared mathlib pages): run under 4.5 GiB x --check-workers + 2 GiB.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from bench_lean_verifier import repl_context  # noqa: E402
from build_nt_sft_data import body_of  # noqa: E402
from heldout_screens import (  # noqa: E402
    BANDS, append, attempts_by_name, counts, features, header_of, load_pool, name_of, nearest, proof_text, read,
    statement, write_json,
)
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import check_candidates  # noqa: E402

ARM = "copy_nearest"


def train_library(paths, held_names):
    """name -> features of every distinct TRAIN statement; a held-out name in a TRAIN file stops the run."""
    library = {}
    for path in paths:
        for row in read(path):
            name = name_of(row)
            if name in held_names:
                raise ValueError(f"held-out name {name} appears in TRAIN file {path}")
            library.setdefault(name, features(statement(header_of(row))))
    return library


def proof_library(paths, held_names):
    """name -> distinct proof bodies, in file order, from TRAIN proof rows."""
    bodies = {}
    for path in paths:
        if "references" in path.name or "private" in path.parts:
            raise ValueError(f"{path} looks like a private reference file; the copy baseline reads TRAIN proofs only")
        for row in read(path):
            name = name_of(row)
            if name in held_names:
                raise ValueError(f"held-out name {name} appears in TRAIN proof file {path}")
            text = proof_text(row)
            body = body_of(text, header_of({"text": text}))
            if body and body not in bodies.setdefault(name, []):
                bodies[name].append(body)
    return {n: b for n, b in bodies.items() if b}


def similarity_rows(held, library):
    return [dict(nearest(features(statement(header_of(r))), library), name=r["name"], split=r["split"], topic=r["topic"])
            for r in held]


def copy_candidates(rows, held, library, bodies):
    """Candidate rows: the proofs of the nearest TRAIN statement that has a proof."""
    with_proof = {n: f for n, f in library.items() if n in bodies}
    by_name = {r["name"]: r for r in held}
    out = []
    for row in rows:
        neighbour = nearest(features(statement(header_of(by_name[row["name"]]))), with_proof)["nearest"]
        row["copy_neighbour"] = neighbour
        out += [{"arm": ARM, "name": row["name"], "sample": i, "continuation": body, "generated_tokens": 0,
                 "neighbour": neighbour} for i, body in enumerate(bodies.get(neighbour, []))]
    return out


def add_copy_status(rows, checked):
    by_name = attempts_by_name(checked, ARM)
    for row in rows:
        statuses = list(by_name.get(row["name"], {}).values())
        row["copy_status"] = ("accepted" if "accepted" in statuses else "timeout" if "timeout" in statuses
                              else "rejected" if statuses else "not_run")


def summarize(rows, library_size, copy):
    out = {"statements": len(rows), "train_statements": library_size, "bands": [label for _, label in BANDS],
           "by_band": counts(rows, "band"), "signature_match": sum(bool(r["signature_matches"]) for r in rows),
           "template_match": sum(bool(r["template_matches"]) for r in rows),
           "max_similarity_ge_0.5": sum(r["max_similarity"] >= 0.5 for r in rows),
           "by_topic_band": {t: dict(Counter(r["band"] for r in rows if r["topic"] == t)) for t in counts(rows, "topic")}}
    if copy:
        out["copy_status"] = counts(rows, "copy_status")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--train", type=Path, action="append", required=True, help="TRAIN statement JSONL; repeat")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, help="tasks_dev.jsonl or similar, for the topic of each name")
    parser.add_argument("--split", default="validation")
    parser.add_argument("--copy-baseline", action="store_true")
    parser.add_argument("--train-proofs", type=Path, action="append", default=[], help="TRAIN proof JSONL; repeat")
    for name in ("environment", "checker", "repl"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--lean-timeout", type=float, default=20)
    parser.add_argument("--setup-timeout", type=float, default=120)
    parser.add_argument("--repl-restart-after", type=int, default=400)
    parser.add_argument("--check-workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.copy_baseline and not (args.train_proofs and args.environment and args.checker and args.repl):
        parser.error("--copy-baseline needs --train-proofs, --environment, --checker and --repl")
    started = time.monotonic()
    held = load_pool(args.pool, args.split, args.tasks)
    held_names = {r["name"] for r in held}
    library = train_library(args.train, held_names)
    rows = similarity_rows(held, library)
    out = args.output
    out.mkdir(parents=True, exist_ok=args.resume)
    if args.copy_baseline:
        candidates = copy_candidates(rows, held, library, proof_library(args.train_proofs, held_names))
        done = {(c["arm"], c["name"], c["sample"]) for c in read(out / "candidates.jsonl")}
        append(out / "candidates.jsonl", [c for c in candidates if (c["arm"], c["name"], c["sample"]) not in done])
        environment = args.environment.resolve()
        checker = load_checker(args.checker, environment, out)
        ctx = repl_context(checker, environment, args.repl, out, args.lean_timeout, args.setup_timeout,
                           args.repl_restart_after)
        check_candidates(ctx, {r["name"]: r for r in held}, out, args.check_workers)
        add_copy_status(rows, read(out / "checked.jsonl"))
    with (out / "rows.jsonl").open("w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    summary = dict(summarize(rows, len(library), args.copy_baseline), split=args.split,
                   train_files=[str(p) for p in args.train], train_proof_files=[str(p) for p in args.train_proofs],
                   wall_seconds_this_invocation=round(time.monotonic() - started, 1))
    write_json(out / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
