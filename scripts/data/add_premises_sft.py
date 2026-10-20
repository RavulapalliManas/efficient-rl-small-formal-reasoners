"""Add the retrieved-lemma section to existing v2 action SFT rows.

Usage: add_premises_sft.py SFT_DIR CORPUS K OUTPUT_DIR

Each row is re-rendered from its stored fields with the shared renderer. The script first checks that
the re-render without lemmas equals the stored prompt byte for byte, then renders the row with the
lemmas retrieved for its goal state, exactly as the environment does at run time. Targets do not change.
The corpus must not contain a project theorem name. run_sft.py still refuses rows over its token limit.
Peak memory: the premise index plus one row at a time.
"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/train'))
from run_tactic_rl import render_observation  # noqa: E402
from rl_components.premises import load_index  # noqa: E402

SPLITS = ('sft_train.jsonl', 'sft_val.jsonl')


def render(row, premises):
    supplied = row['mode'] == 'supplied'
    task = dict(kind='prefix_top' if supplied else 'whole', header=row['header'], given=row['given'] if supplied else '')
    return render_observation(task, [] if supplied else row['done'], [row['state']], row.get('last_error') or '',
                              row.get('failed_tactics') or [], row['remaining_actions'], row['max_tactics'],
                              premises=premises)


def augment(sft_dir, corpus, k, output_dir):
    sft_dir, output_dir = Path(sft_dir), Path(output_dir)
    theorems = set()
    for split in SPLITS:
        with (sft_dir / split).open() as stream:
            theorems |= {json.loads(line)['theorem'] for line in stream if line.strip()}
    index = load_index(corpus, k, theorems)
    output_dir.mkdir(parents=True, exist_ok=False)
    receipt = dict(source=str(sft_dir), corpus=str(corpus), k=k,
                   corpus_sha256=hashlib.sha256(Path(corpus).read_bytes()).hexdigest(), rows={}, source_sha256={})
    for split in SPLITS:
        receipt['source_sha256'][split] = hashlib.sha256((sft_dir / split).read_bytes()).hexdigest()
        count = 0
        with (sft_dir / split).open() as source, (output_dir / split).open('x') as out:
            for number, line in enumerate(source, 1):
                row = json.loads(line)
                if render(row, None) != row['messages'][0]['content']:
                    raise ValueError(f'{split} row {number} does not re-render exactly; refuse to edit it')
                premises = index([row['state']])
                prompt = dict(row['messages'][0], content=render(row, premises))
                out.write(json.dumps(dict(row, premises=premises, messages=[prompt] + row['messages'][1:]),
                                     ensure_ascii=False) + '\n')
                count += 1
        receipt['rows'][split] = count
    (output_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    print(json.dumps(augment(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4])))
