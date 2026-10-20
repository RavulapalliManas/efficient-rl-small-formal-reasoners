"""Planted compiler diagnostics distinguish parsing from type and proof errors."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'audit'))
from classify_proof_failures import classify


class FailureClasses(unittest.TestCase):
    def row(self, message, status='rejected'):
        return {'status': status, 'first_error': message, 'continuation': '  exact h'}

    def test_parser_failure(self):
        self.assertEqual(classify(self.row("unexpected token '#print'; expected ']'")), 'syntax_or_structure')

    def test_known_parser_diagnostics_do_not_become_unclassified(self):
        for message in ('expected term', 'expected token', 'tabs are not allowed',
                        "unexpected end of input; expected ']'"):
            with self.subTest(message=message):
                self.assertEqual(classify(self.row(message)), 'syntax_or_structure')

    def test_expected_type_is_not_syntax(self):
        self.assertEqual(classify(self.row('function expected at f')), 'name_or_type_resolution')
        self.assertEqual(classify(self.row('type mismatch')), 'name_or_type_resolution')

    def test_proof_strategy_failure(self):
        self.assertEqual(classify(self.row('omega could not prove the goal:')), 'proof_construction')
        self.assertEqual(classify(self.row('unsolved goals')), 'proof_construction')
        self.assertEqual(classify(self.row('simp made no progress')), 'proof_construction')

    def test_unknown_and_infrastructure_stay_separate(self):
        self.assertEqual(classify(self.row('unrecognized diagnostic')), 'unclassified_rejection')
        self.assertEqual(classify(self.row('', 'timeout')), 'verifier_timeout_or_crash')
        self.assertEqual(classify(self.row('', 'accepted')), 'accepted')
        self.assertEqual(classify({'continuation': '  exact h', 'status': 'timeout'}), 'verifier_timeout_or_crash')


if __name__ == '__main__':
    unittest.main()
