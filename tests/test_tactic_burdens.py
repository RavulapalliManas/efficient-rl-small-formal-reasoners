"""Focused contracts for the frozen inference diagnostic; no Lean or model."""
import importlib.util
import inspect
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / 'scripts/eval/probe_tactic_burdens.py'


class TacticBurdens(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATH.exists(), 'Frozen condition evaluator is not implemented')
        spec = importlib.util.spec_from_file_location('burdens', PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.state = {'id': 's', 'task': {'name': 'target', 'given': ''},
                      'goals': 'a : Nat\n⊢ a = a', 'assistance': 'none',
                      'cards': [{'name': 'Nat.add_comm', 'type': '(a b : Nat) : a+b=b+a',
                                 'target_independent': True}]}

    def test_conditions_change_only_instruction_and_cards(self):
        prompts = {c: self.module.condition_prompt(self.state, c) for c in 'ABCD'}
        self.assertIn('Write the next tactic.', prompts['A'])
        self.assertIn('Return only a raw Lean tactic block', prompts['B'])
        for text in prompts.values():
            self.assertIn(self.state['goals'], text)
            self.assertNotIn('JSON', text)
            self.assertNotIn('target :=', text)
        self.assertNotIn('Nat.add_comm', prompts['A'])
        self.assertEqual(prompts['C'].split('Available declarations:')[1],
                         prompts['D'].split('Available declarations:')[1])

    def test_target_card_and_unchecked_dependency_are_rejected(self):
        self.state['cards'][0]['name'] = 'target'
        with self.assertRaises(ValueError):
            self.module.condition_prompt(self.state, 'C')
        self.state['cards'][0].update(name='alias', target_independent=False)
        with self.assertRaises(ValueError):
            self.module.condition_prompt(self.state, 'D')

    def test_cap_is_independent_from_error_class(self):
        self.assertTrue(self.module.completion_capped([4, 5], {9}, 2))
        self.assertFalse(self.module.completion_capped([4, 9], {9}, 2))
        self.assertEqual(self.module.outcome_class({'status': 'format_error'}, True), 'format')
        self.assertEqual(self.module.outcome_class({'status': 'accepted', 'accepted': True}, False),
                         'complete')

    def test_paired_summary_retains_missing_and_assistance(self):
        states = [self.state, dict(self.state, id='assisted', assistance='reference_prefix')]
        rows = [{'state_id': 's', 'condition': c, 'sample': 0, 'accepted': c == 'B',
                 'valid': c in 'AB', 'capped': False} for c in 'ABCD']
        summary = self.module.summarize(states, rows, 1)
        self.assertEqual(summary['conditions']['A']['planned'], 2)
        self.assertEqual(summary['conditions']['A']['pending'], 1)
        self.assertEqual(summary['paired']['B-A']['complete_pairs'], 1)
        self.assertEqual(summary['paired']['B-A']['proof_wins'], 1)
        self.assertEqual(summary['paired']['D-C']['proof_wins'], 0)
        self.assertEqual(summary['conditions']['B']['unassisted_root_success'], 1)

    def test_name_type_tactic_and_replay_failures_are_distinct(self):
        for error, expected in [("error: unknown identifier 'bogus'", 'unresolved_name'),
                                ('error: application type mismatch', 'type_or_elaboration'),
                                ("error: tactic 'simp' failed, made no progress", 'tactic_failure')]:
            self.assertEqual(self.module.outcome_class({'status': 'rejected', 'error': error}, False), expected)
        self.assertEqual(self.module.outcome_class({'status': 'replay_timeout'}, False), 'infrastructure_timeout')
        self.assertEqual(self.module.outcome_class({'status': 'replay_crash'}, False), 'infrastructure_crash')

    def test_episode_endpoint_keeps_pending_and_assisted_completions(self):
        self.assertIn('continuations', inspect.signature(self.module.summarize).parameters)
        states = [self.state, dict(self.state, id='assisted', assistance='reference_prefix')]
        rows = [{'state_id': 's', 'condition': 'A', 'sample': 0, 'accepted': False,
                 'valid': True, 'capped': False, 'result': {'status': 'changed'}},
                {'state_id': 's', 'condition': 'B', 'sample': 0, 'accepted': True,
                 'valid': True, 'capped': False, 'result': {'status': 'accepted'}},
                {'state_id': 'assisted', 'condition': 'A', 'sample': 0, 'accepted': False,
                 'valid': True, 'capped': False, 'result': {'status': 'changed'}}]
        continuation = [{'state_id': 'assisted', 'condition': 'A', 'sample': 0,
                         'accepted': True, 'complete_budget': True}]
        summary = self.module.summarize(states, rows, 1, continuations=continuation)
        self.assertEqual(summary['conditions']['A']['episode']['pending'], 1)
        self.assertEqual(summary['conditions']['A']['episode']['assisted_success'], 1)
        self.assertEqual(summary['conditions']['A']['episode']['unassisted_root_success'], 0)
        self.assertEqual(summary['paired']['B-A']['episode_complete_pairs'], 0)


if __name__ == '__main__':
    unittest.main()
