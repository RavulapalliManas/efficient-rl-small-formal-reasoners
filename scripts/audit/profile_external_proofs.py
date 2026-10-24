"""Profile the Lean-checked external proofs (verify_external_proofs.py output) before any use in training.

Per accepted proof: tactic lines (comments and blank lines removed), first tactic, natural-language comments.
Per statement (heuristic, from the formal statement text):
  real: mentions ℝ or ℂ; number theory: divisibility, mod, primes, gcd/lcm, digits, totient, parity;
  closed: no binders before the colon and no ∀/∃ (a fixed numeric claim, usually computation).
Contamination: exact and near-duplicate (token Jaccard >= 0.8) match of each statement against the
validation and test declaration headers, and accepted proofs that cite a validation or test theorem by name
(full name, or without the `Nat.` prefix because the header opens Nat). Dot-notation uses are not detected.

Memory: all result rows of the 4 shards (~11k rows, <200 MiB).
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import zipfile

NUMBER_THEORY = re.compile(r"Prime|∣|%|gcd|lcm|ZMod|MOD|digits|totient|factorization|Even|Odd|Coprime")
TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9.']*|\d+|[^\sA-Za-z_0-9]")


def body(text):
    """Tactic lines after the first `:= by`, without comments."""
    rest = re.split(r":=\s*by\b", text, maxsplit=1)
    if len(rest) < 2:
        return [], False
    proof = rest[1]
    has_nl = "/-" in proof or bool(re.search(r"^\s*--", proof, re.M))
    proof = re.sub(r"/-.*?-/", "", proof, flags=re.S)
    lines = [re.sub(r"--.*", "", line).rstrip() for line in proof.split("\n")]
    return [line for line in lines if line.strip()], has_nl


def statement(formal):
    text = re.sub(r":=\s*(by\s*)?sorry\s*$", "", formal.strip())
    return re.sub(r"^theorem\s+\S+", "", text).strip()


def closed(stmt):
    return not stmt.startswith(("(", "{", "[")) and not re.search(r"[∀∃]", stmt)


def tokens(text):
    return set(TOKEN.findall(text))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, action="append", required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for path in args.results:
        with path.open() as input_stream:
            rows.extend(json.loads(line) for line in input_stream)
    eval_headers, eval_names = [], []
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as input_stream:
                for line in input_stream:
                    row = json.loads(line)
                    eval_headers.append(row["declaration_header"])
                    eval_names += [row["full_name"]] + ([row["full_name"][4:]] if row["full_name"].startswith("Nat.") else [])
    cites = re.compile(r"(?<![\w.'])(" + "|".join(map(re.escape, sorted(eval_names, key=len, reverse=True))) + r")(?![\w'])")
    eval_statements = [re.split(r"\b(?:theorem|lemma)\s+\S+", h, maxsplit=1)[-1].strip() for h in eval_headers]
    eval_tokens = [tokens(s) for s in eval_statements]
    accepted = [r for r in rows if r["status"] == "accepted"]
    proofs = Counter()
    lengths, first, nl, citing = Counter(), Counter(), 0, Counter()
    for r in accepted:
        lines, has_nl = body(r["text"])
        for name in set(cites.findall("\n".join(lines))):
            citing[name] += 1
        proofs[r["source"].split("_proof")[0]] += 1
        lengths[min(len(lines), 10)] += 1
        nl += has_nl
        if len(lines) == 1:
            first[lines[0].split()[0]] += 1
    problems = {}
    for r in accepted:
        lines, _ = body(r["text"])
        if r["name"] not in problems or len(lines) > problems[r["name"]][1]:
            problems[r["name"]] = (r, len(lines))
    kinds, exact, near = Counter(), [], []
    for name, (r, n) in problems.items():
        stmt = statement(r["formal_statement"])
        real = bool(re.search(r"[ℝℂ]", stmt))
        nt = bool(NUMBER_THEORY.search(stmt))
        kinds["real" if real else "not_real"] += 1
        kinds["number_theory_content"] += nt
        kinds["number_theory_content_not_real"] += nt and not real
        kinds["closed_statement"] += closed(stmt)
        kinds["number_theory_not_real_not_closed"] += nt and not real and not closed(stmt)
        kinds["longest_proof_ge_3_lines"] += n >= 3
        kinds["number_theory_not_real_not_closed_ge_3_lines"] += nt and not real and not closed(stmt) and n >= 3
        norm = re.sub(r"\s+", " ", stmt)
        if any(norm == re.sub(r"\s+", " ", e) for e in eval_statements):
            exact.append(name)
        t = tokens(stmt)
        if any(len(t & e) / max(len(t | e), 1) >= 0.8 for e in eval_tokens):
            near.append(name)
    summary = {"result_rows": len(rows), "statuses": dict(Counter(r["status"] for r in rows)),
               "accepted_proofs_by_source": dict(proofs), "accepted_problems": len(problems),
               "accepted_proof_tactic_lines (10 = 10+)": dict(sorted(lengths.items())),
               "single_line_proofs_by_tactic": dict(first.most_common(12)),
               "accepted_proofs_with_natural_language_comments": nl,
               "problems_by_statement_kind (heuristic; longest accepted proof per problem)": dict(kinds),
               "evaluation_headers": len(eval_headers), "exact_matches_to_evaluation": exact,
               "near_duplicates_to_evaluation (token Jaccard >= 0.8)": near,
               "accepted_proofs_citing_evaluation_theorems": dict(citing.most_common())}
    args.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=1, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
