import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/eval/probe_nt_pool.py'
spec = importlib.util.spec_from_file_location('probe_nt_pool', PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

HEADER = 'theorem ntb_x (n : ℕ) (h : 2 ∣ n) : 2 ∣ n * n'


def output(*blocks, closed=True):
    text = '<think>\nTry factoring.\n```lean4\ntheorem draft : True := by\n  trivial\n```\n</think>\nFinal proof:\n'
    for block in blocks:
        text += '```lean4\n' + block + ('\n```\n' if closed else '\n')
    return text


class KiminaExtractionTest(unittest.TestCase):
    def test_body_comes_from_the_last_fence_that_restates_the_theorem(self):
        text = output('import Mathlib\nimport Aesop\n\n' + HEADER + ' := by\n  exact Dvd.dvd.mul_right h n')
        self.assertEqual(probe.kimina_continuation(text, HEADER), '  exact Dvd.dvd.mul_right h n')
        two = output(HEADER + ' := by\n  sorry', HEADER + ' := by\n  omega')
        self.assertEqual(probe.kimina_continuation(two, HEADER), '  omega')
        lemma = output(HEADER.replace('theorem', 'lemma', 1) + ' := by\n  simp [h]')
        self.assertEqual(probe.kimina_continuation(lemma, HEADER), '  simp [h]')

    def test_changed_statement_or_capped_output_gives_no_candidate(self):
        changed = output('theorem ntb_x (n : ℕ) (h : 2 ∣ n) : 4 ∣ n * n := by\n  omega')
        self.assertEqual(probe.kimina_continuation(changed, HEADER), '')
        capped = output(HEADER + ' := by\n  omega', closed=False)
        self.assertEqual(probe.kimina_continuation(capped, HEADER), '')
        self.assertEqual(probe.kimina_continuation('no code at all', HEADER), '')

    def test_prompt_follows_the_model_card_with_the_checking_header(self):
        seen = {}

        class Tokenizer:
            def apply_chat_template(self, messages, tokenize, add_generation_prompt):
                seen.update(messages=messages, tokenize=tokenize, add=add_generation_prompt)
                return 'rendered'
        self.assertEqual(probe.kimina_prompt(Tokenizer(), dict(header=HEADER)), 'rendered')
        system, user = seen['messages']
        self.assertEqual(system, dict(role='system', content='You are an expert in mathematics and Lean 4.'))
        self.assertTrue(user['content'].startswith('Think about and solve the following problem step by step in Lean 4.\n# Problem:\n'))
        self.assertIn('```lean4\nimport Mathlib\nimport Aesop\nset_option maxHeartbeats 400000\n', user['content'])
        self.assertTrue(user['content'].endswith(HEADER + ' := by\n```\n'))
        self.assertEqual((seen['tokenize'], seen['add']), (False, True))


if __name__ == '__main__':
    unittest.main()
