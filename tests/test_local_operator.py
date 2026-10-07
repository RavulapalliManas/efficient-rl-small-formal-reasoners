"""Candidate-expression boundary checks; no Lean process or inference."""
import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class LocalOperatorChecks(unittest.TestCase):
    def test_expression_boundary(self):
        self.assertIsNotNone(importlib.util.find_spec('verify_local_operator'),
                             'Local mathematical action adapter is missing')
        from verify_local_operator import lean_expression
        self.assertEqual(lean_expression(' k * l ', ['k', 'l']), '(k * l)')
        self.assertEqual(lean_expression('l*k + 0', ['k', 'l']), '((l * k) + 0)')
        self.assertEqual(lean_expression('-(u-v)', ['u', 'v']), '(-(u - v))')
        for bad in ('k/l', 'k**l', 'True', 'k.real', 'f(k)', 'unknown',
                    '__import__("os")', 'k\naxiom bad : False', 'k # comment',
                    '1.0', '9999999999999999999999', 'k+' * 300 + 'l'):
            with self.subTest(candidate=bad), self.assertRaises(ValueError):
                lean_expression(bad, ['k', 'l', 'u', 'v'])

    def test_extract_explicit_witness_without_repair(self):
        import verify_local_operator as op
        self.assertTrue(hasattr(op, 'extract_witness'))
        for response, expected in [
            (r"Reasoning here. Thus \boxed{k}", 'k'),
            (r"Thus the final solution is: \[ w = k \]", 'k'),
            (r"So, the final witness is \( k \).", 'k'),
            (r"Final witness: \(k*l\)", 'k*l'),
            (r"\boxed{w = k+l}", 'k+l'),
            ("Reasoning.\nFinal witness: k+l", 'k+l'),
            (r"\boxed{k \cdot l}", 'k * l'),
            ("k*l", 'k*l'),
        ]:
            self.assertEqual(op.extract_witness(response), expected)
        for response in [r"\boxed{k} or \boxed{l}",
                         "Final witness: k\nFinal witness: l",
                         r"\boxed{\frac{c}{a}}", "Perhaps k would work.",
                         "Final witness: k\nActually the answer is l."]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                op.extract_witness(response)

    def test_custom_task_preserves_goal_and_variable_boundary(self):
        import verify_local_operator as op
        self.assertTrue(hasattr(op, 'task_obligation'))
        task = dict(op.OPERATORS['scale'], id='scale_shift', target='(t+1)*b = a * {candidate}')
        q, _ = op.task_obligation(task, '(t+1)*k')
        self.assertIn('(t+1)*b = a * ((t + 1) * k)', q['header'])
        with self.assertRaises(ValueError):
            op.task_obligation(task, 'b')

    def test_operator_reward_preserves_action_and_abstains_on_infra(self):
        self.assertIsNotNone(importlib.util.find_spec('local_operator_training'))
        from local_operator_training import score_action
        from verify_local_operator import OPERATORS
        class Checker:
            result = {'status': 'returned', 'exit_code': 1, 'accepted': False, 'output': 'unsolved goals'}
            def check_candidate(self, q, body, stem):
                self.goal = q['header']
                return self.result
        checker = Checker()
        task = dict(OPERATORS['compose'], id='compose')
        record = score_action(task, 'Final witness: k+l', checker, 'test')
        self.assertEqual(record['reward'], 0)
        self.assertIn('(k + l)', checker.goal)
        checker.result = {'status': 'timeout', 'exit_code': None, 'accepted': False, 'output': ''}
        self.assertIsNone(score_action(task, 'Final witness: k*l', checker, 'test')['reward'])
        self.assertEqual(score_action(task, 'No answer', checker, 'test')['status'], 'format_error')

    def test_obligation_uses_submitted_witness(self):
        import verify_local_operator as op
        self.assertTrue(hasattr(op, 'make_obligation'), 'Fixed obligation builder is missing')
        q, body = op.make_obligation('compose', 'l*k')
        wrong, _ = op.make_obligation('compose', 'k+l')
        self.assertIn('c = a * (l * k)', q['header'])
        self.assertIn('c = a * (k + l)', wrong['header'])
        self.assertEqual(q['prefix'], wrong['prefix'])
        self.assertNotIn('sorry', body)
        with self.assertRaises(ValueError):
            op.make_obligation('compose', 'c')

    def test_infrastructure_failure_is_not_zero_math_reward(self):
        import verify_local_operator as op
        self.assertTrue(hasattr(op, 'reward_value'), 'Reward classification is missing')
        self.assertIsNone(op.reward_value({'status': 'returned', 'exit_code': 1,
                                          'output': 'permission denied', 'accepted': False}))
        self.assertIsNone(op.reward_value({'status': 'returned', 'exit_code': 1,
                                          'output': 'error: unknown tactic', 'accepted': False}))
        self.assertIsNone(op.reward_value({'status': 'timeout', 'exit_code': None,
                                          'output': '', 'accepted': False}))
        self.assertEqual(op.reward_value({'status': 'returned', 'exit_code': 1,
                                         'output': 'unsolved goals', 'accepted': False}), 0)
        self.assertEqual(op.reward_value({'status': 'returned', 'exit_code': 0,
                                         'output': '', 'accepted': True}), 1)


if __name__ == '__main__':
    unittest.main()
