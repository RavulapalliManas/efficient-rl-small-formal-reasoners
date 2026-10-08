"""Classify the Lean errors in a saved whole-proof diagnostic; no model or Lean execution.

Input: verification.jsonl from the Instruct proof diagnostic (one row per attempt, <=1 MiB).
Memory: all rows in memory, <10 MiB for the 63-row diagnostic.
Classes are exclusive and checked in this order: forbidden construct, accepted,
empty proof, parse or file-structure error, proof failure on well-formed Lean.
The Lean 3 count is a pattern match on the submitted code, not a parser result.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

ERROR = re.compile(r":\d+:\d+: error: ([^\n]*)")
PARSE = re.compile(r"unexpected token|expected|invalid 'import'|invalid 'end'|unknown tactic|unterminated")
LEAN3 = re.compile(r"^\s*import data\.|^\s*begin\b|\bassume\b|λ\s*\w+\s*,|^\s*end\s*$|\bnat\.\w", re.M)
DECLARATION = re.compile(r"^\s*(theorem|lemma|example)\b", re.M)


def classify(row):
    code = (row.get("code") or "").strip()
    if row["status"] == "forbidden_construct":
        return "forbidden_construct"
    if row.get("accepted"):
        return "accepted"
    if code in ("", "by"):
        return "empty_proof"
    if any(PARSE.search(message) for message in ERROR.findall(row.get("output", ""))):
        return "parse_or_structure_error"
    return "well_formed_proof_failure"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("verification", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.verification.open()]
    checked = [row for row in rows if row["status"] != "forbidden_construct"]
    messages = Counter()
    for row in checked:
        for message in set(ERROR.findall(row.get("output", ""))):
            messages[re.sub(r"'[^']*'", "'X'", message)[:60]] += 1
    summary = {
        "attempts": len(rows),
        "classes": dict(Counter(classify(row) for row in rows)),
        "checked_attempts": len(checked),
        "checked_with_import_in_body": sum(any("invalid 'import' command" in m for m in ERROR.findall(r["output"])) for r in checked),
        "checked_with_backtick_token": sum(any("unexpected token '`" in m for m in ERROR.findall(r["output"])) for r in checked),
        "code_with_declaration": sum(bool(DECLARATION.search(r.get("code") or "")) for r in rows),
        "code_with_lean3_pattern": sum(bool(LEAN3.search(r.get("code") or "")) for r in rows),
        "error_messages": dict(messages.most_common()),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
