"""Classify the Lean errors in a saved whole-proof diagnostic; no model or Lean execution.

Input: historical verification.jsonl, or probe checked.jsonl with --arm.
Memory: historical rows <10 MiB; --arm streams rows <=1 MiB and retains diagnostic counters.
Classes are exclusive and checked in this order: forbidden construct, accepted,
empty proof, parse or file-structure error, proof failure on well-formed Lean.
The Lean 3 count is a pattern match on the submitted code, not a parser result.
With --arm, classes locate the first diagnostic only. They do not certify well-formed Lean or diagnose mathematics.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ERROR = re.compile(r":\d+:\d+: error: ([^\n]*)")
PARSE = re.compile(r"unexpected (?:token|end of input)|^expected (?:'|tactic\b|end\b|term\b|token\b)|"
                   r"^tabs are not allowed|invalid 'import'|invalid 'end'|unterminated")
LEAN3 = re.compile(r"^\s*import data\.|^\s*begin\b|\bassume\b|λ\s*\w+\s*,|^\s*end\s*$|\bnat\.\w", re.M)
DECLARATION = re.compile(r"^\s*(theorem|lemma|example)\b", re.M)


def classify(row):
    if 'first_error' in row or 'continuation' in row:
        # Exclusive first-error location. This does not certify the remaining proof or its mathematics.
        status = row['status']
        if status == 'accepted':
            return 'accepted'
        if status in ('timeout', 'crash'):
            return 'verifier_timeout_or_crash'
        if status in ('format_error', 'empty_or_top_level_line', 'forbidden_construct'):
            return status
        message = row.get('first_error') or ''
        if PARSE.search(message):
            return 'syntax_or_structure'
        if re.search(r'unknown (?:identifier|constant|tactic)|type mismatch|failed to synthesize|'
                     r'function expected|invalid field|ambiguous|numerals are data|'
                     r'equality or iff proof expected', message):
            return 'name_or_type_resolution'
        if re.search(r'unsolved goals|tactic |could not prove|failed to rewrite|no goals to be solved|'
                     r'invalid alternative name|index in target|made no progress|'
                     r'failed to (?:find a contradiction|solve some goals)|not a positivity goal', message):
            return 'proof_construction'
        return 'unclassified_rejection'
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
    parser.add_argument('--arm', help='Filter checked.jsonl by model arm; classify its first error only')
    parser.add_argument('--output', type=Path, help='Write the summary exclusively; never overwrite evidence')
    args = parser.parse_args()
    if args.arm:
        counts, messages, examples, digest = Counter(), Counter(), {}, hashlib.sha256()
        names, tokens, n = set(), 0, 0
        with args.verification.open('rb') as source:
            for line in source:
                if len(line) > 1024**2:
                    raise ValueError('Input row exceeds 1 MiB')
                digest.update(line)
                row = json.loads(line)
                if row.get('arm') != args.arm:
                    continue
                label = classify(row)
                counts[label] += 1
                messages[row.get('first_error') or ''] += 1
                names.add(row['name'])
                tokens += row['generated_tokens']
                n += 1
                examples.setdefault(label, {'name': row['name'], 'sample': row['sample'],
                                            'first_error': row.get('first_error')})
        summary = {'arm': args.arm, 'attempts': n, 'statements': len(names), 'classes': dict(counts),
                   'generated_tokens': tokens, 'sha256': digest.hexdigest(), 'examples': examples,
                   'first_errors': dict(messages.most_common()),
                   'policy': 'Exclusive first-error locations, not causes or mathematical correctness. '
                             'Later errors and token truncation may coexist.'}
        if args.output:
            with args.output.open('x') as out:
                out.write(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary, indent=2))
        return
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
    if args.output:
        with args.output.open('x') as out:
            out.write(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
