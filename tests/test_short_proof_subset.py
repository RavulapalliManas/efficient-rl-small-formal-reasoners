import importlib.util
from pathlib import Path
import unittest


class ShortProofSubsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'scripts/data/prepare_short_proof_subset.py'
        spec = importlib.util.spec_from_file_location('short_subset', path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def fixture(self):
        row = dict(theorem_id='a', official_split='train', full_name='Nat.a',
                   file_path='Mathlib/Data/Nat/A.lean', declaration_header='theorem a : True',
                   family='arithmetic', traced_tactics=[{'tactic': 'hidden_solution'}],
                   url='mathlib', commit='pin', imports=[], start=[1, 1], end=[2, 1])
        bank = [dict(theorem_id='a', task_id='a:k0', full_name='Nat.a', file_path=row['file_path'],
                     header=row['declaration_header'], k=0, steps_total=1, steps_remaining=1,
                     state='⊢ True', state_source='trace', given_steps=[],
                     reference_rest=['  hidden_solution'], premises=['hidden_lemma'], topic='arithmetic')]
        config = dict(max_theorems=4, max_reference_blocks=6, max_reference_chars=2048,
                      suffix_blocks=[1, 2], families=['arithmetic'], seed=7,
                      source={'url': 'mathlib', 'commit': 'pin'})
        return bank, [row], config

    def test_short_whole_proof_is_kept_without_four_depths_or_answer_hints(self):
        bank, rows, config = self.fixture()
        public, context, private, report = self.module.select(bank, rows, set(), config)
        self.assertEqual([r['task_id'] for r in public], ['a:k0'])
        self.assertNotIn('hidden_solution', repr(public) + repr(context))
        self.assertNotIn('hidden_lemma', repr(public) + repr(context))
        self.assertEqual(private[0]['reference_rest'], ['  hidden_solution'])
        self.assertEqual(report['selected_theorems'], 1)

    def test_evaluation_files_and_nontraining_rows_cannot_enter(self):
        bank, rows, config = self.fixture()
        public, _, _, report = self.module.select(bank, rows, {rows[0]['file_path']}, config)
        self.assertEqual(public, [])
        self.assertEqual(report['excluded']['evaluation_or_dev_file'], 1)
        rows[0]['official_split'] = 'test'
        with self.assertRaisesRegex(ValueError, 'official train'):
            self.module.select(bank, rows, set(), config)

    def test_masked_suffix_does_not_admit_a_long_parent_proof(self):
        bank, rows, config = self.fixture()
        bank[0].update(steps_total=9, steps_remaining=9, reference_rest=['  step'] * 9)
        bank.append(dict(bank[0], task_id='a:k8', k=8, steps_remaining=1,
                         given_steps=['  step'] * 8, reference_rest=['  step']))
        public, _, _, report = self.module.select(bank, rows, set(), config)
        self.assertEqual(public, [])
        self.assertEqual(report['excluded']['long_reference'], 1)


if __name__ == '__main__':
    unittest.main()
