"""Answer scoring must not count a copied question or an absent final answer."""

import unittest

from scripts.probe_qwen import score_answer


class AnswerScoringTest(unittest.TestCase):
    def test_gsm_final_answer_and_missing_answer(self):
        self.assertEqual(score_answer("gsm8k", "#### 1,200", "work\n#### 1200"), (True, True))
        self.assertEqual(score_answer("gsm8k", "#### 12", "#### 13"), (False, True))
        self.assertEqual(score_answer("gsm8k", "#### 12", "There are 12 apples."), (False, False))

    def test_math_equivalence_and_wrong_answer(self):
        gold = r"$\boxed{\frac{1}{2}}$"
        self.assertEqual(score_answer("math_number_theory", gold, r"$\boxed{0.5}$"), (True, True))
        self.assertEqual(score_answer("math_number_theory", gold, r"$\boxed{2}$"), (False, True))
        self.assertEqual(score_answer("math_number_theory", gold, "1/2"), (False, False))

    def test_next_question_is_not_scored(self):
        self.assertEqual(score_answer("gsm8k", "#### 12", "#### 13\nQuestion: copied\n#### 12"), (False, True))


if __name__ == "__main__":
    unittest.main()
