"""Check the training side of a split for leakage into validation, test, and miniF2F.

The training side is the union of sft_problems, heldout_proof_problems, dev, and rl_pool_statements of a split
directory (build_nt_splits.py). The script reports the maximum token Jaccard (tokens() in profile_external_proofs.py)
from any training-side statement to any validation or test statement of the pool and to any miniF2F test statement,
plus disjointness checks, and writes them to one JSON file.

Memory: about 4,300 pool statements and 244 miniF2F statements as token sets (<100 MiB); time is quadratic in counts.
"""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from profile_external_proofs import statement, tokens  # noqa: E402

TRAIN_SIDE = ["sft_problems", "heldout_proof_problems", "dev", "rl_pool_statements"]


def jaccard(a, b):
    return len(a & b) / max(len(a | b), 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--minif2f", type=Path, required=True, help="AI-MO/minif2f_test parquet")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import pyarrow.parquet as pq
    pool = {r["name"]: r for r in map(json.loads, args.pool.open())}
    lists = {n: set((args.splits / f"{n}.txt").read_text().split()) for n in TRAIN_SIDE + ["excluded_from_training"]}
    train_side = set().union(*(lists[n] for n in TRAIN_SIDE))
    evals = [tokens(statement(r["formal_statement"])) for r in pool.values() if r["split"] in ("validation", "test")]
    minif2f = []
    for row in pq.read_table(args.minif2f).to_pylist():
        text = row["formal_statement"][re.search(r"\btheorem\b", row["formal_statement"]).start():]
        minif2f.append(tokens(statement(re.sub(r":=\s*(by\s*)?(sorry)?\s*$", "", text.strip()))))
    to_eval, to_minif2f = 0.0, 0.0
    for name in train_side:
        t = tokens(statement(pool[name]["formal_statement"]))
        to_eval = max(to_eval, max(jaccard(t, e) for e in evals))
        to_minif2f = max(to_minif2f, max(jaccard(t, m) for m in minif2f))
    result = {"splits": str(args.splits.name), "train_side_statements": len(train_side),
              "train_side_overlap_with_excluded": len(train_side & lists["excluded_from_training"]),
              "train_side_not_train_split": sum(pool[n]["split"] != "train" for n in train_side),
              "pairwise_overlaps": {f"{a}&{b}": len(lists[a] & lists[b]) for i, a in enumerate(TRAIN_SIDE)
                                    for b in TRAIN_SIDE[i + 1:]},
              "max_jaccard_to_validation_or_test": round(to_eval, 3), "max_jaccard_to_minif2f": round(to_minif2f, 3),
              "minif2f_statements": len(minif2f), "evaluation_statements": len(evals)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as out:
        out.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
