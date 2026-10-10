"""Build Lean 4 SFT data from LeanDojo Benchmark 4 training proofs at the pinned mathlib commit.

Every source proof compiled in mathlib's own build at this commit. Exclusions, applied in order:
1. every theorem in a file that holds any of our validation or test theorems;
2. statements whose normalized header is a near-duplicate (similarity >= 0.9) of an evaluation statement;
3. proofs that reference an evaluation theorem as a premise;
4. proofs with sorry, admit, native_decide, or a new axiom.
Formats (chat messages, the assistant turn is the training target):
  step:  goal state -> next tactic, for every traced tactic
  proof: initial goal -> the whole original tactic proof, for proofs that are a top-level `by` block
The proof format matches scripts/eval/lean_eval_statements.py.

Memory: streams train.json with ijson; caches source files (<300 MiB); writes output line by line.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile
import zipfile

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_leandojo import declaration_header, normalize, similarity  # noqa: E402

FORBIDDEN = re.compile(r"\b(sorry|admit|axiom|native_decide)\b")
STEP = "Lean 4 goal state:\n\n{state}\n\nWrite the next tactic."
PROOF = ("Prove this Lean 4 theorem. The environment is Lean 4 with mathlib. The goal state is:\n\n{state}\n\n"
         "Write the tactic proof, indented by two spaces, then close the code block.")


def evaluation(paths):
    rows = []
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            rows.extend(json.loads(line) for line in archive.open(archive.namelist()[0]))
    return rows


def proof_block(code, header):
    match = re.match(r"\s*:=\s*by\b", code[len(header):]) if code.startswith(header) else None
    if match is None:
        return None
    rest = code[len(header) + match.end():].split("\n")
    body = (["  " + rest[0].strip()] if rest[0].strip() else []) + rest[1:]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body) or None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--mathlib", type=Path, required=True, help="mathlib source tree at the pinned commit")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-chars", type=int, default=4000, help="Drop examples whose state or proof is longer")
    parser.add_argument("--val-fraction", type=float, default=0.01)
    args = parser.parse_args()
    evals = evaluation(args.eval)
    eval_files = {r["file_path"] for r in evals}
    eval_names = {r["full_name"] for r in evals}
    eval_norm = [normalize(r["declaration_header"]) for r in evals]
    args.output.mkdir(parents=True, exist_ok=False)
    counts, sources = Counter(), {}
    with tarfile.open(args.archive) as archive, (args.output / "sft_train.jsonl").open("x") as train, \
            (args.output / "sft_val.jsonl").open("x") as val:
        member = archive.extractfile("leandojo_benchmark_4/random/train.json")
        for row in ijson.items(member, "item"):
            counts["theorems"] += 1
            tactics = row["traced_tactics"]
            if not tactics:
                counts["no_tactics"] += 1
                continue
            if row["file_path"] in eval_files:
                counts["excluded_eval_file"] += 1
                continue
            path = args.mathlib / row["file_path"]
            if row["file_path"] not in sources:
                sources[row["file_path"]] = path.read_text().split("\n") if path.exists() else None
            lines = sources[row["file_path"]]
            if lines is None:
                counts["source_missing"] += 1
                continue
            code = "\n".join(lines[row["start"][0] - 1:row["end"][0]])
            header = declaration_header(code)
            norm = normalize(header)
            if any(similarity(norm, e) >= 0.9 for e in eval_norm):
                counts["excluded_near_duplicate"] += 1
                continue
            premises = {p["full_name"] for t in tactics for p in t["annotated_tactic"][1]}
            if premises & eval_names:
                counts["excluded_eval_premise"] += 1
                continue
            if FORBIDDEN.search(code[len(header):]):
                counts["excluded_forbidden"] += 1
                continue
            out = val if int(hashlib.sha256(row["full_name"].encode()).hexdigest(), 16) % 10000 < args.val_fraction * 10000 else train
            counts["kept_theorems"] += 1
            for t in tactics:
                if len(t["state_before"]) <= args.max_chars and t["tactic"].strip():
                    out.write(json.dumps({"format": "step", "theorem": row["full_name"], "messages": [
                        {"role": "user", "content": STEP.format(state=t["state_before"])},
                        {"role": "assistant", "content": t["tactic"]}]}, ensure_ascii=False) + "\n")
                    counts["step_examples"] += 1
            body = proof_block(code, header)
            if body and len(body) <= args.max_chars and len(tactics[0]["state_before"]) <= args.max_chars:
                out.write(json.dumps({"format": "proof", "theorem": row["full_name"], "messages": [
                    {"role": "user", "content": PROOF.format(state=tactics[0]["state_before"])},
                    {"role": "assistant", "content": "```lean\n" + header + " := by\n" + body + "\n```"}]},
                    ensure_ascii=False) + "\n")
                counts["proof_examples"] += 1
            if counts["theorems"] % 10000 == 0:
                print(json.dumps(dict(counts)), flush=True)
    manifest = {"counts": dict(counts), "evaluation_theorems": len(evals), "evaluation_files": len(eval_files),
                "near_duplicate_threshold": 0.9, "max_chars": args.max_chars, "split": "random/train",
                "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
