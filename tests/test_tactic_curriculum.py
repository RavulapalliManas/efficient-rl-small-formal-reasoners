import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class CurriculumTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'scripts/data/prepare_tactic_curriculum.py'
        spec = importlib.util.spec_from_file_location('curriculum', path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_reference_continuation_never_enters_policy_row(self):
        task = dict(task_id='p:prefix_top:1', name='p', kind='prefix_top', header='theorem p : True',
                    given='  skip\n', state='⊢ True', lines_remaining=1,
                    reference_rest='  exact secret', remaining_blocks=1)
        self.assertNotIn('reference_rest', self.module.policy_row(task))
        self.assertNotIn('secret', repr(self.module.policy_row(task)))

    def test_schedules_share_theorem_sequence_but_change_start_distribution(self):
        bank = {name: {label: dict(task_id=f'{name}:{label}', name=name)
                      for label in ('one', 'two', 'longer', 'whole')} for name in ('p', 'q', 'r')}
        uniform, reverse = self.module.schedules(bank, 12, 7)
        self.assertEqual([x.split(':')[0] for x in uniform], [x.split(':')[0] for x in reverse])
        self.assertEqual([x.split(':')[1] for x in reverse],
                         ['one'] * 3 + ['two'] * 3 + ['longer'] * 3 + ['whole'] * 3)
        self.assertEqual((uniform, reverse), self.module.schedules(bank, 12, 7))
        self.assertNotEqual(uniform, reverse)

    def test_reserved_conjunction_templates_cannot_enter_training(self):
        self.assertTrue(self.module.reserved_template('theorem p (a b c : Nat) : a ∣ c ∧ a ∣ b'))
        self.assertTrue(self.module.reserved_template('theorem p (a b : Nat) : a ≤ b ∧ b ≤ a'))
        self.assertFalse(self.module.reserved_template('theorem p (a b : Nat) : a ∣ b'))

    def test_empty_common_pool_preserves_selection_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = {
                'tasks': [dict(task_id='p:whole:0', name='p', kind='whole', header='theorem p : True',
                               given='', state='⊢ True', lines_remaining=None)],
                'proofs': [dict(name='p', status='accepted', selected=True, body='  rfl')],
                'pool': [dict(name='p', split='train', formal_statement='theorem p : True := by sorry')],
                'eval-states': [dict(name='e', state='⊢ False')],
                'eval-pool': [dict(name='e', formal_statement='theorem e : False := by sorry')],
            }
            for name, rows in fixtures.items():
                (root / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))
            (root / 'exclude-names').write_text('e\n')
            (root / 'mathlib-identity').write_text(json.dumps(dict(
                mathlib_revision='29dcec074de168ac2bf835a77ef68bbe069194c5', mismatches=[], source='test')))
            names = list(fixtures) + ['exclude-names', 'mathlib-identity', 'checker', 'environment', 'repl', 'tokenizer', 'output']
            argv = ['prepare'] + [value for name in names for value in ['--' + name, str(root / name)]]
            with patch('sys.argv', argv), self.assertRaisesRegex(ValueError, 'every required start'):
                self.module.main()
            manifest = json.loads((root / 'output/manifest.json').read_text())
            self.assertEqual(manifest['status'], 'failed_no_four_stratum_pool')
            exclusions = (root / 'output/private/selection_exclusions.jsonl').read_text()
            self.assertIn('missing_one_two_longer_or_whole', exclusions)


if __name__ == '__main__':
    unittest.main()
