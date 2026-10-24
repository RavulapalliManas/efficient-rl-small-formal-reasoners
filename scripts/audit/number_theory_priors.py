"""Rank the prior knowledge that mathlib number-theory proofs use, from LeanDojo premise annotations.

Consumers: random/train theorems in number-theory files (path patterns below), outside evaluation files.
Important priors: premises ranked by how many consumer proofs use them (each proof counted once).
Structural priors: the share of consumer proofs that use each proof-structure class of tactics.
For each prior, the output says whether it is itself a random/train theorem with a tactic proof
(a possible task), lives in Lean core or Batteries, or sits in an evaluation file.

Memory: streams train.json twice with ijson; keeps per-premise counters (<200 MiB).
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import tarfile
import zipfile

CONSUMER = re.compile(r"NumberTheory|Nat/Prime|Nat/GCD|ModEq|ZMod|Factorization|Nat/Factors|Totient|Nat/Digits|"
                      r"ChineseRemainder|Squarefree|Multiplicity|Int/GCD|Nat/Choose|Nat/Fib|Nat/Parity|Int/Parity")
STRUCTURE = {
    "induction": r"^(induction|induction'|Nat\.le_induction|Nat\.strong_induction_on|Nat\.strongRecOn|refine Nat\.(le_induction|strong)|strong_induction)",
    "case_split": r"^(cases|cases'|rcases|obtain|rintro|match|split|by_cases|interval_cases|fin_cases|·)",
    "contradiction": r"^(by_contra|by_contra!|exfalso|absurd|contradiction|push_neg)",
    "witness": r"^(use|exists|refine ⟨|exact ⟨|constructor)",
    "computation": r"^(decide|norm_num|omega|linarith|nlinarith|positivity|ring|ring_nf|field_simp|simp_arith)",
    "rewriting": r"^(rw|rwa|simp|simp only|simpa|conv|calc|subst)",
    "apply_lemma": r"^(apply|exact|refine|have|suffices|convert|gcongr)",
}


def main():
    import ijson  # only main streams train.json; importers that need STRUCTURE do not need ijson
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--pools", type=Path, action="append", default=[], help="train.jsonl or zip of exported pools")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top", type=int, default=400)
    args = parser.parse_args()
    eval_files, eval_names = set(), set()
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as input_stream:
                for line in input_stream:
                    r = json.loads(line)
                    eval_files.add(r["file_path"]); eval_names.add(r["full_name"])
    pooled = set()
    for path in args.pools:
        source = zipfile.ZipFile(path).open("train.jsonl") if path.suffix == ".zip" else path.open("rb")
        pooled |= {json.loads(line)["full_name"] for line in source}
    uses, paths, structure, consumers = Counter(), {}, Counter(), 0
    train_tactic = {}
    with tarfile.open(args.archive) as archive:
        for row in ijson.items(archive.extractfile("leandojo_benchmark_4/random/train.json"), "item"):
            if row["traced_tactics"]:
                train_tactic[row["full_name"]] = row["file_path"]
            if not row["traced_tactics"] or row["file_path"] in eval_files or not CONSUMER.search(row["file_path"]):
                continue
            consumers += 1
            premises = {}
            for t in row["traced_tactics"]:
                for p in t["annotated_tactic"][1]:
                    premises[p["full_name"]] = p["def_path"]
            for name, path in premises.items():
                uses[name] += 1
                paths[name] = path
            classes = {k for t in row["traced_tactics"] for k, pat in STRUCTURE.items() if re.match(pat, t["tactic"].strip())}
            structure.update(classes)
    ranked = []
    for name, count in uses.most_common(args.top):
        path = paths[name]
        kind = ("evaluation_theorem" if name in eval_names else "core_or_batteries" if path.startswith(".lake/")
                else "train_tactic_theorem" if name in train_tactic else "definition_or_term_proof")
        ranked.append({"premise": name, "def_path": path, "consumer_proofs": count, "kind": kind, "in_pool": name in pooled})
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "priors.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ranked))
    kinds = Counter(r["kind"] for r in ranked)
    summary = {"consumer_proofs": consumers, "distinct_premises": len(uses), "top": args.top,
               "top_kinds": dict(kinds), "top_tactic_theorems_in_pool": sum(r["in_pool"] for r in ranked if r["kind"] == "train_tactic_theorem"),
               "structure_share": {k: round(v / consumers, 3) for k, v in structure.most_common()},
               "top_20": [(r["premise"], r["consumer_proofs"], r["kind"]) for r in ranked[:20]]}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
