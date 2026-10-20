import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/data'))
import merge_sft_rows  # noqa: E402


class CharTokenizer:
    """One token per character plus role and end markers; honors add_generation_prompt."""

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False):
        ids = []
        for message in messages:
            ids += [1 if message['role'] == 'user' else 2] + [ord(c) for c in message['content']] + [3]
        return ids + ([2] if add_generation_prompt else [])


def row(prompt, target):
    return dict(theorem='t', messages=[dict(role='user', content=prompt), dict(role='assistant', content=target)])


class MergeSFTRowsTest(unittest.TestCase):
    def test_extras_join_train_only_and_oversized_or_duplicate_rows_are_counted(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'base').mkdir()
            (root / 'base/sft_train.jsonl').write_text(json.dumps(row('p1', 'a')) + '\n' + json.dumps(row('p2', 'b')) + '\n')
            (root / 'base/sft_val.jsonl').write_text(json.dumps(row('v1', 'c')) + '\n')
            extra = root / 'mined.jsonl'
            extra.write_text(json.dumps(row('p1', 'a')) + '\n' + json.dumps(row('p3', 'x' * 200)) + '\n'
                             + json.dumps(row('p4', 'd')) + '\n')
            receipt = merge_sft_rows.merge(root / 'base', CharTokenizer(), root / 'out', [extra])
            train = [json.loads(l)['messages'][0]['content'] for l in (root / 'out/sft_train.jsonl').read_text().splitlines()]
            val = (root / 'out/sft_val.jsonl').read_text().splitlines()
        self.assertEqual(train, ['p1', 'p2', 'p4'])
        self.assertEqual(len(val), 1)
        counts = receipt['splits']['sft_train.jsonl']
        self.assertEqual((counts['written'], counts['duplicate'], counts['omitted_length']), (3, 1, 1))
        self.assertEqual(counts['by_source'], {'sft_train.jsonl': 2, 'mined.jsonl': 1})

    def test_output_must_be_new(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'base').mkdir()
            for name in ('sft_train.jsonl', 'sft_val.jsonl'):
                (root / 'base' / name).write_text(json.dumps(row('p', 'a')) + '\n')
            (root / 'out').mkdir()
            with self.assertRaises(FileExistsError):
                merge_sft_rows.merge(root / 'base', CharTokenizer(), root / 'out')


if __name__ == '__main__':
    unittest.main()
