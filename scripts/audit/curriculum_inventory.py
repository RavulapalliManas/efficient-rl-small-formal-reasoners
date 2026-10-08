"""Count start points for a reverse proof-state curriculum in a LeanDojo export; read-only.

Input: a training JSONL file, or a delivery zip that contains train.jsonl.
Memory: all training rows in memory, <200 MiB for the 2,658-theorem pool.
A proof is linear when each tactic's state_after equals the next state_before and the
last state is "no goals". This string test is conservative; Lean has not replayed these
proofs. Evaluation records carry no traces, so this script reads training proofs only.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import zipfile


def load(path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return [json.loads(line) for line in archive.open("train.jsonl")]
    return [json.loads(line) for line in path.open()]


def linear(tactics):
    return bool(tactics) and tactics[-1]["state_after"] == "no goals" and all(
        tactics[i]["state_after"] == tactics[i + 1]["state_before"] for i in range(len(tactics) - 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("train", type=Path)
    args = parser.parse_args()
    rows = load(args.train)
    names = {row["full_name"] for row in rows}
    lengths = [len(row["traced_tactics"]) for row in rows]
    chains = [len(row["traced_tactics"]) for row in rows if linear(row["traced_tactics"])]
    nested = [n for row, n in zip(rows, lengths) if n and not linear(row["traced_tactics"])]
    premises = [{p["full_name"] for t in row["traced_tactics"] for p in t["annotated_tactic"][1]} for row in rows]
    summary = {
        "theorems": len(rows),
        "term_mode_proofs": lengths.count(0),
        "tactic_proofs": len(rows) - lengths.count(0),
        "linear_proofs": len(chains),
        "linear_length_histogram": dict(sorted(Counter(chains).items())),
        "linear_at_least_2": sum(n >= 2 for n in chains),
        "linear_at_least_3": sum(n >= 3 for n in chains),
        "linear_at_least_5": sum(n >= 5 for n in chains),
        "assisted_start_points": sum(n - 1 for n in chains),
        "nested_tactic_proofs": len(nested),
        "nested_median_tactics": statistics.median(nested) if nested else None,
        "training_premise_edges": sum(len(p & names) for p in premises),
        "theorems_using_a_training_theorem": sum(bool(p & names) for p in premises),
        "families": dict(Counter(row["family"] for row in rows).most_common()),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
