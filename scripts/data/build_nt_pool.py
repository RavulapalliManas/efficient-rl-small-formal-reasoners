"""Build the number-theory statement pool for RL from Lean-checked Lean Workbook statements.

Keeps statements that elaborate at our pin (status sorry_only in verify_external_proofs.py --mode statements) and,
by the heuristic in profile_external_proofs.py, have number-theory content, are not over ℝ or ℂ (REAL: also √, π,
Real.*, Complex.*, since v2; for example `10^n ∣ (5 + Real.sqrt 35)^(2*n-1)` is divisibility in a field, so it
is trivially true), and are not closed numeric claims. Statements equal up to renaming of bound variables are merged (the key renames every
binder name in order of first use). The key's sha256 assigns the split: train 80%, curriculum validation 10%,
held-out test 10%, so renamed copies of one problem never cross splits. Statements that match a LeanDojo
validation or test declaration (same key, or token Jaccard >= 0.8) are dropped. Each row records whether a
Lean-checked external proof exists (verify_external_proofs.py --mode proofs); proofs are not copied here.

Memory: ~27k statement rows and ~11k proof rows (<300 MiB).
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "audit"))
from profile_external_proofs import NUMBER_THEORY, closed, statement, tokens  # noqa: E402

BINDER = re.compile(r"[({\[⦃]\s*([^:(){}\[\]⦃⦄]+?)\s*:")
QUANTIFIED = re.compile(r"[∀∃λ]\s*([^,:]+?)\s*[:,]")
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9'₀-₉]*")
REAL = re.compile(r"[ℝℂ√π]|\bReal\.|\bComplex\.")


def key(stmt):
    """The statement with binder names renamed v0, v1, ... in order of first use, whitespace collapsed."""
    names = {n for group in BINDER.findall(stmt) + QUANTIFIED.findall(stmt) for n in group.split() if IDENT.fullmatch(n)}
    mapping = {}
    def rename(match):
        word = match.group(0)
        if word not in names:
            return word
        return mapping.setdefault(word, f"v{len(mapping)}")
    return re.sub(r"\s+", " ", re.sub(r"(?<![\w.'])[A-Za-z_][A-Za-z_0-9'₀-₉]*", rename, stmt)).strip()


def split_of(k):
    bucket = int(hashlib.sha256(k.encode()).hexdigest(), 16) % 10
    return "test" if bucket == 0 else "validation" if bucket == 1 else "train"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--statements", type=Path, action="append", required=True, help="results.jsonl, statements mode")
    parser.add_argument("--proofs", type=Path, action="append", default=[], help="results.jsonl, proofs mode")
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proved = Counter()
    for path in args.proofs:
        with path.open() as input_stream:
            for line in input_stream:
                r = json.loads(line)
                proved[r["name"]] += r["status"] == "accepted"
    eval_statements = []
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as input_stream:
                for line in input_stream:
                    header = json.loads(line)["declaration_header"]
                    eval_statements.append(re.split(r"\b(?:theorem|lemma)\s+\S+", header, maxsplit=1)[-1].strip())
    eval_keys = {key(s) for s in eval_statements}
    eval_tokens = [tokens(s) for s in eval_statements]
    counts, pool = Counter(), {}
    for path in args.statements:
        with path.open() as input_stream:
            for line in input_stream:
                r = json.loads(line)
                counts["rows"] += 1
                if r["status"] != "sorry_only":
                    counts["dropped_does_not_elaborate"] += 1
                    continue
                stmt = statement(r["formal_statement"])
                if not NUMBER_THEORY.search(stmt):
                    counts["dropped_no_number_theory_content"] += 1
                elif REAL.search(stmt):
                    counts["dropped_real_or_complex"] += 1
                elif closed(stmt):
                    counts["dropped_closed_numeric"] += 1
                else:
                    k = key(stmt)
                    t = tokens(stmt)
                    if k in eval_keys or any(len(t & e) / max(len(t | e), 1) >= 0.8 for e in eval_tokens):
                        counts["dropped_matches_evaluation"] += 1
                    elif k in pool:
                        counts["merged_renamed_duplicate"] += 1
                        pool[k]["duplicates"].append(r["name"])
                    else:
                        pool[k] = {"name": r["name"], "split": split_of(k), "formal_statement": r["formal_statement"],
                                   "natural_language_statement": r["natural_language_statement"], "tags": r["tags"],
                                   "answer": r["answer"], "key": k, "duplicates": []}
    for row in pool.values():
        row["checked_proofs"] = sum(proved[n] for n in [row["name"]] + row["duplicates"])
    args.output.mkdir(parents=True, exist_ok=False)
    rows = sorted(pool.values(), key=lambda r: r["name"])
    with (args.output / "pool.jsonl").open("x") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {"filter_counts": dict(counts), "statements_kept": len(rows),
               "by_split": dict(Counter(r["split"] for r in rows)),
               "with_checked_proof_by_split": dict(Counter(r["split"] for r in rows if r["checked_proofs"])),
               "evaluation_statements": len(eval_statements)}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
