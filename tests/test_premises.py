import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/train'))
sys.path.insert(0, str(ROOT / 'scripts/data'))
from rl_components.premises import PremiseIndex, load_index, tokens  # noqa: E402
from run_tactic_rl import render_observation  # noqa: E402
from add_premises_sft import augment  # noqa: E402

CORPUS = [dict(name='Nat.Coprime.mul_right', statement='Nat.Coprime k m → Nat.Coprime k n → Nat.Coprime k (m * n)'),
          dict(name='Nat.add_comm', statement='∀ (n m : ℕ), n + m = m + n'),
          dict(name='Int.emod_emod_of_dvd', statement='∀ (n : ℤ) {m k : ℤ}, m ∣ k → n % k % m = n % m'),
          dict(name='Nat.pow_mod', statement='∀ (n m k : ℕ), n ^ m % k = (n % k) ^ m % k')]


class PremiseIndexTest(unittest.TestCase):
    def test_tokens_split_names_and_keep_symbols(self):
        self.assertEqual(tokens('Nat.Coprime.mul_right'), ['nat', 'coprime', 'mul', 'right'])
        self.assertEqual(tokens('m ∣ k → n % k'), ['m', '∣', 'k', '→', 'n', '%', 'k'])
        self.assertEqual(tokens('Int.ModEq'), ['int', 'mod', 'eq'])

    def test_retrieval_recovers_the_matching_lemma_and_not_an_unrelated_one(self):
        index = PremiseIndex(CORPUS, k=2)
        self.assertEqual(index(['k m n : ℕ\nh₁ : Nat.Coprime k m\n⊢ Nat.Coprime k (m * n)'])[0],
                         'Nat.Coprime.mul_right : ' + CORPUS[0]['statement'])
        self.assertTrue(index(['a b : ℕ\n⊢ 2 ^ a % 3 = (2 % 3) ^ a % 3'])[0].startswith('Nat.pow_mod'))
        self.assertFalse(index(['a b : ℕ\n⊢ a + b = b + a'])[0].startswith('Nat.Coprime'))
        self.assertEqual(len(index(['⊢ True'])), 0)  # No shared token, no lemma.

    def test_bad_corpora_and_project_names_are_refused(self):
        for records, k in ((CORPUS, 0), (CORPUS, 17), ([], 2), (CORPUS + CORPUS[:1], 2)):
            with self.assertRaises(ValueError):
                PremiseIndex(records, k)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'corpus.jsonl'
            path.write_text(''.join(json.dumps(r) + '\n' for r in CORPUS))
            self.assertEqual(load_index(path, 2, {'xstp_theorem'}).k, 2)
            with self.assertRaisesRegex(ValueError, 'project theorem'):
                load_index(path, 2, {'Nat.add_comm'})


class AddPremisesSFTTest(unittest.TestCase):
    def row(self, state, last_error=''):
        task = dict(kind='whole', header='theorem xstp_t (k m n : ℕ) : Nat.Coprime k (m * n)', given='')
        prompt = render_observation(task, ['intro h'], [state], last_error, [], 5, 4)
        return dict(mode='shown', theorem='xstp_t', header=task['header'], given='', done=['intro h'], state=state,
                    last_error=last_error, failed_tactics=[], remaining_actions=5, max_tactics=4,
                    messages=[dict(role='user', content=prompt), dict(role='assistant', content='```lean\nsimp\n```')])

    def test_rows_gain_the_runtime_lemma_section_and_keep_targets(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sft').mkdir()
            state = 'k m n : ℕ\n⊢ Nat.Coprime k (m * n)'
            for split in ('sft_train.jsonl', 'sft_val.jsonl'):
                (root / 'sft' / split).write_text(json.dumps(self.row(state, 'rejected: x')) + '\n')
            (root / 'corpus.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in CORPUS))
            receipt = augment(root / 'sft', root / 'corpus.jsonl', 2, root / 'out')
            row = json.loads((root / 'out/sft_train.jsonl').read_text())
            self.assertEqual(receipt['rows'], {'sft_train.jsonl': 1, 'sft_val.jsonl': 1})
            premises = PremiseIndex(CORPUS, 2)([state])
            self.assertEqual(row['premises'], premises)
            task = dict(kind='whole', header=row['header'], given='')
            self.assertEqual(row['messages'][0]['content'],
                             render_observation(task, ['intro h'], [state], 'rejected: x', [], 5, 4, premises=premises))
            self.assertIn('/- library lemmas that may help:\n  - Nat.Coprime.mul_right', row['messages'][0]['content'])
            self.assertEqual(row['messages'][1]['content'], '```lean\nsimp\n```')

    def test_a_row_that_does_not_re_render_exactly_is_refused(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sft').mkdir()
            row = self.row('⊢ True')
            row['messages'][0]['content'] += ' edited'
            for split in ('sft_train.jsonl', 'sft_val.jsonl'):
                (root / 'sft' / split).write_text(json.dumps(row) + '\n')
            (root / 'corpus.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in CORPUS))
            with self.assertRaisesRegex(ValueError, 're-render exactly'):
                augment(root / 'sft', root / 'corpus.jsonl', 2, root / 'out')


if __name__ == '__main__':
    unittest.main()
