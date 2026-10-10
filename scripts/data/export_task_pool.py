"""Export a larger training-theorem pool from LeanDojo Benchmark 4 for curriculum experiments.

Selects random/train theorems with tactic proofs under the given mathlib directories and writes them
in the row format of scripts/data/build_proof_tasks.py (declaration_header taken from the source file).
Exclusions match build_sft_data.py: every theorem in a file that holds any validation or test theorem,
near-duplicate statements of evaluation statements (similarity >= 0.9), and proofs that cite one.
Theorems already in --skip (an earlier export) are left out, so no theorem is replayed twice.

Memory: streams train.json with ijson; caches source files (<300 MiB).
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import zipfile

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_leandojo import declaration_header, normalize, similarity  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--mathlib", type=Path, required=True)
    parser.add_argument("--prefix", action="append", required=True, help="mathlib directory, e.g. Mathlib/NumberTheory")
    parser.add_argument("--skip", type=Path, help="earlier train.jsonl or zip whose theorems are excluded")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evals = []
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            evals.extend(json.loads(line) for line in archive.open(archive.namelist()[0]))
    eval_files = {r["file_path"] for r in evals}
    eval_names = {r["full_name"] for r in evals}
    eval_norm = [normalize(r["declaration_header"]) for r in evals]
    skip = set()
    if args.skip:
        source = zipfile.ZipFile(args.skip).open("train.jsonl") if args.skip.suffix == ".zip" else args.skip.open("rb")
        skip = {json.loads(line)["full_name"] for line in source}
    counts, sources = Counter(), {}
    args.output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(args.archive) as archive, (args.output / "train.jsonl").open("x") as out:
        for row in ijson.items(archive.extractfile("leandojo_benchmark_4/random/train.json"), "item"):
            path = row["file_path"]
            if not row["traced_tactics"] or not any(path.startswith(p + "/") or path == p + ".lean" for p in args.prefix):
                continue
            counts["candidates"] += 1
            if path in eval_files:
                counts["excluded_eval_file"] += 1
                continue
            if row["full_name"] in skip:
                counts["skipped_already_exported"] += 1
                continue
            if path not in sources:
                file = args.mathlib / path
                sources[path] = file.read_text().split("\n") if file.exists() else None
            if sources[path] is None:
                counts["source_missing"] += 1
                continue
            header = declaration_header("\n".join(sources[path][row["start"][0] - 1:row["end"][0]]))
            if any(similarity(normalize(header), e) >= 0.9 for e in eval_norm):
                counts["excluded_near_duplicate"] += 1
                continue
            if {p["full_name"] for t in row["traced_tactics"] for p in t["annotated_tactic"][1]} & eval_names:
                counts["excluded_eval_premise"] += 1
                continue
            family = "/".join(path.split("/")[1:3]).removesuffix(".lean")
            record = {"theorem_id": hashlib.sha256(f'{path}|{row["full_name"]}|{row["start"][0]}'.encode()).hexdigest(), "full_name": row["full_name"], "file_path": path, "family": family,
                      "start": row["start"], "end": row["end"], "declaration_header": header, "imports": [],
                      "official_split": "train", "traced_tactics": row["traced_tactics"]}
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            counts["exported"] += 1
    manifest = {"counts": dict(counts), "prefixes": args.prefix, "evaluation_theorems": len(evals),
                "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
