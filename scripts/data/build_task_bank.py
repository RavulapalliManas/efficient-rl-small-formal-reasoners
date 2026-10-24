"""Merge Lean-checked proof tasks into one task bank with curriculum metadata.

A task unit is (theorem, cut k): the first k top-level steps are given and the model writes the rest.
k = 0 is the whole theorem; its goal is LeanDojo's first traced state. For k >= 1 the goal is the
state Lean printed at that cut (build_proof_tasks.py). Only replay-accepted theorems and Lean-checked
cuts enter, and no task from a file that holds a validation or test theorem, or a file listed in
--exclude-files (for example the development-set files). Metadata per unit:
  topic, structure classes of the remaining steps, priors used (ranked by number_theory_priors.py),
  bank theorems the proof depends on (premises that are themselves bank theorems), dependency level,
  steps given and remaining.
Training proofs only.

Memory: task records and source rows for ~12k theorems (<1 GiB).
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "audit"))
from number_theory_priors import STRUCTURE  # noqa: E402

NUMBER_THEORY = re.compile(r"NumberTheory|Nat/Prime|Nat/GCD|ModEq|ZMod|Factorization|Factors|Totient|Digits|ChineseRemainder|"
                           r"Squarefree|Multiplicity|Int/GCD|Choose|Fib|Parity|Divisors|PrimeFin|Wilson|LucasLehmer|Padics|"
                           r"LegendreSymbol|Pell|SumTwoSquares|SumFourSquares|PythagoreanTriples|FermatPsp|Zsqrtd|GaussianInt")


def topic(path):
    if NUMBER_THEORY.search(path):
        return "number_theory"
    if re.search(r"Data/(Nat|Int|Rat)", path):
        return "integer_arithmetic"
    if re.search(r"Algebra/|RingTheory/", path):
        return "algebra"
    if re.search(r"Order", path):
        return "order"
    return "sets_logic"


def classes(texts):
    return sorted({k for t in texts for line in t.split("\n") for k, pat in STRUCTURE.items() if re.match(pat, line.strip())})


def load_rows(paths):
    rows = {}
    for path in paths:
        source = zipfile.ZipFile(path).open("train.jsonl") if path.suffix == ".zip" else path.open("rb")
        for line in source:
            r = json.loads(line)
            rows[r["theorem_id"]] = r
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, action="append", required=True, help="tasks.jsonl from build_proof_tasks.py")
    parser.add_argument("--rows", type=Path, action="append", required=True, help="the train.jsonl or zip each pool came from")
    parser.add_argument("--priors", type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", default=[],
                        help="validation or test export; every task in a file that holds one is dropped")
    parser.add_argument("--exclude-files", type=Path, help="text file of mathlib file paths, one per line; their tasks are dropped")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = load_rows(args.rows)
    eval_files = set()
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as _input_stream:
                eval_files |= {json.loads(line)["file_path"] for line in _input_stream}
    excluded = set(args.exclude_files.read_text().split()) if args.exclude_files else set()
    with args.priors.open() as input_stream:
        prior_count = {row["premise"]: row["consumer_proofs"] for row in map(json.loads, input_stream)}
    records = []
    for path in args.tasks:
        with path.open() as input_stream:
            records.extend(map(json.loads, input_stream))
    accepted = [t for t in records if t["status"] == "accepted" and t["file_path"] not in eval_files | excluded]
    dropped_eval_file = sum(t["status"] == "accepted" and t["file_path"] in eval_files for t in records)
    dropped_excluded = sum(t["status"] == "accepted" and t["file_path"] in excluded - eval_files for t in records)
    names = {t["full_name"]: t["theorem_id"] for t in accepted}
    premises, depends = {}, {}
    for t in accepted:
        row = rows[t["theorem_id"]]
        premises[t["theorem_id"]] = sorted({p["full_name"] for x in row["traced_tactics"] for p in x["annotated_tactic"][1]})
        depends[t["theorem_id"]] = sorted({names[p] for p in premises[t["theorem_id"]] if p in names and names[p] != t["theorem_id"]})
    level = {}
    def depth(tid, seen=()):
        if tid in level:
            return level[tid]
        if tid in seen:
            return 0  # break cycles, which annotation noise can create
        level[tid] = 1 + max((depth(d, seen + (tid,)) for d in depends[tid]), default=-1)
        return level[tid]
    for t in accepted:
        depth(t["theorem_id"])
    args.output.mkdir(parents=True, exist_ok=False)
    counts = defaultdict(Counter)
    with (args.output / "bank.jsonl").open("x") as out:
        for t in accepted:
            row, steps = rows[t["theorem_id"]], t["steps"]
            used = premises[t["theorem_id"]]
            base = {"theorem_id": t["theorem_id"], "full_name": t["full_name"], "file_path": t["file_path"],
                    "topic": topic(t["file_path"]), "header": row["declaration_header"], "steps_total": len(steps),
                    "premises": used, "prior_score": sum(prior_count.get(p, 0) for p in used),
                    "top_priors": sorted((p for p in used if p in prior_count), key=lambda p: -prior_count[p])[:5],
                    "depends_on": depends[t["theorem_id"]], "dependency_level": level[t["theorem_id"]]}
            cuts = [{"k": 0, "ok": True, "state": row["traced_tactics"][0]["state_before"], "state_source": "leandojo_trace"}]
            cuts += [dict(c, state_source="lean_trace_state") for c in t["cuts"] if c["ok"]]
            for c in cuts:
                k = c["k"]
                unit = dict(base, task_id=f"{t['theorem_id']}:k{k}", k=k, steps_remaining=len(steps) - k, state=c["state"],
                            state_source=c["state_source"], given_steps=steps[:k], reference_rest=steps[k:],
                            structure_remaining=classes(steps[k:]))
                out.write(json.dumps(unit, ensure_ascii=False) + "\n")
                counts["topic"][unit["topic"]] += 1
                counts["steps_remaining"][min(unit["steps_remaining"], 8)] += 1
                for s in unit["structure_remaining"]:
                    counts["structure"][s] += 1
                counts["units"]["all"] += 1
                counts["units"]["whole_theorem" if k == 0 else "cut"] += 1
    theorems = Counter(topic(t["file_path"]) for t in accepted)
    summary = {"theorems": len(accepted), "accepted_dropped_for_evaluation_file": dropped_eval_file,
               "evaluation_files": len(eval_files), "accepted_dropped_for_excluded_file": dropped_excluded,
               "excluded_files": len(excluded), "theorems_by_topic": dict(theorems),
               "theorems_with_bank_dependencies": sum(bool(v) for v in depends.values()),
               "max_dependency_level": max(level.values(), default=0),
               "units": dict(counts["units"]), "units_by_topic": dict(counts["topic"]),
               "units_by_steps_remaining (8 = 8+)": dict(sorted(counts["steps_remaining"].items())),
               "units_by_structure_remaining": dict(counts["structure"].most_common())}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
