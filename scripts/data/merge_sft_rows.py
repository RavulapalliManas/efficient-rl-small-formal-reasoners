"""Assemble an SFT data directory from base rows plus optional extra TRAIN rows, within the SFT token limits.

Usage: merge_sft_rows.py BASE_DIR TOKENIZER_DIR OUTPUT_DIR [EXTRA_ROWS.jsonl ...]

BASE_DIR holds sft_train.jsonl and sft_val.jsonl. Extra rows join TRAIN only; validation rows never change.
A row whose chat reaches max_length tokens, or whose completion exceeds max_action_tokens, is omitted and
counted, matching the refusal rule of run_sft.py without truncating any row. Identical message lists are
written once. Peak memory: the tokenizer plus one row at a time and the set of row hashes.
"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'train'))
from rl_components.sft import chat_token_lengths  # noqa: E402

MAX_LENGTH, MAX_ACTION_TOKENS = 2048, 192


def merge(base_dir, tokenizer, output_dir, extras=()):
    base_dir, output_dir = Path(base_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    receipt = dict(base=str(base_dir), extras=[str(p) for p in extras], max_length=MAX_LENGTH,
                   max_action_tokens=MAX_ACTION_TOKENS, splits={})
    for split, sources in (('sft_train.jsonl', [base_dir / 'sft_train.jsonl', *map(Path, extras)]),
                           ('sft_val.jsonl', [base_dir / 'sft_val.jsonl'])):
        seen, counts = set(), dict(written=0, omitted_length=0, duplicate=0, by_source={})
        with (output_dir / split).open('x') as out:
            for source in sources:
                kept = 0
                with source.open() as stream:
                    for line in stream:
                        row = json.loads(line)
                        digest = hashlib.sha256(json.dumps(row['messages'], ensure_ascii=False).encode()).hexdigest()
                        if digest in seen:
                            counts['duplicate'] += 1
                            continue
                        total, completion = chat_token_lengths(row['messages'], tokenizer)
                        if total >= MAX_LENGTH or completion > MAX_ACTION_TOKENS:
                            counts['omitted_length'] += 1
                            continue
                        seen.add(digest)
                        out.write(json.dumps(row, ensure_ascii=False) + '\n')
                        kept += 1
                counts['by_source'][source.name] = kept
                counts['written'] += kept
        receipt['splits'][split] = counts
    (output_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    from transformers import AutoTokenizer
    print(json.dumps(merge(sys.argv[1], AutoTokenizer.from_pretrained(sys.argv[2]), sys.argv[3], sys.argv[4:])))
