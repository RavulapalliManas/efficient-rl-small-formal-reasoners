import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'data'))
import audit_sft_corpus as audit  # noqa: E402

MATHD = "theorem mathd_numbertheory_3 (a b : ℕ) (h₀ : a % 5 = 3) (h₁ : b % 5 = 4) :\n  (a + b) % 5 = 2 := by"


class OverlapKeyTest(unittest.TestCase):
    def test_bare_statement_drops_name_comment_and_tail(self):
        self.assertEqual(audit.bare_statement("/- c -/ theorem x (n : ℕ) : 0 ≤ n := by sorry"), "(n : ℕ) : 0 ≤ n")
        self.assertEqual(audit.bare_statement(MATHD), "(a b : ℕ) (h₀ : a % 5 = 3) (h₁ : b % 5 = 4) :\n  (a + b) % 5 = 2")
        self.assertEqual(audit.bare_statement("theorem x: IsLeast {n : ℕ | 0 < n} 1 := by"), ": IsLeast {n : ℕ | 0 < n} 1")
        self.assertEqual(audit.bare_statement("theorem x(n : ℕ) : 0 ≤ n := by"), "(n : ℕ) : 0 ≤ n")

    def test_keys_accept_a_statement_without_colon(self):
        self.assertEqual(audit.keys("(Nat.digits 10 5)[0]! = 5")[1], "( Nat.digits 10 5 ) [ 0 ] ! = 5")

    def test_split_ignores_colons_inside_binders_and_goal(self):
        binders, goal = audit.split_statement("(x : ℕ) (h : ∀ y : ℕ, y ≤ x) : ∀ z : ℕ, z = z")
        self.assertEqual(binders, "(x : ℕ) (h : ∀ y : ℕ, y ≤ x)")
        self.assertEqual(goal, "∀ z : ℕ, z = z")
        self.assertEqual(audit.split_statement(": 1 + 1 = 2"), ("", "1 + 1 = 2"))

    def test_renamed_copy_matches_and_different_statement_does_not(self):
        base = audit.keys(audit.bare_statement(MATHD))
        renamed = audit.keys("(x y : ℕ)  (hx : x % 5 = 3) (hy : y % 5 = 4) : (x + y) % 5 = 2")
        other = audit.keys("(x y : ℕ) (hx : x % 5 = 3) (hy : y % 5 = 4) : (x + y) % 5 = 3")
        self.assertNotEqual(base[0], renamed[0])
        self.assertEqual(base[1], renamed[1])
        self.assertNotEqual(base[1], other[1])

    def test_ngram_windows_need_thirteen_shared_tokens(self):
        statement = audit.bare_statement(MATHD)
        self.assertTrue(audit.keys(statement)[2] & audit.keys(statement + " ∧ True")[2])
        self.assertFalse(audit.keys("(n : ℕ) : n = n")[2])

    def test_goal_text_drops_shared_binder_blocks(self):
        binders = "(p : ℕ) (hp : Nat.Prime p) (hp1 : p ≠ 2) (a : ℕ) (h : a < p)"
        one, two = audit.goal_text(binders + " : a ^ p % p = a"), audit.goal_text(binders + " : p % 4 = 1 ∨ p % 4 = 3")
        self.assertTrue(audit.keys(binders + " : x = x")[2])  # the binder block alone fills 13-token windows
        self.assertFalse(audit.keys(one)[2] & audit.keys(two)[2])
        self.assertEqual(audit.goal_text("(Nat.digits 10 5)[0]! = 5"), "(Nat.digits 10 5)[0]! = 5")

    def test_old_and_new_sum_notation_match(self):
        old = audit.keys("(a : ℕ → ℝ) : (∑ x in Finset.range 9, a x) = 1")
        new = audit.keys("(b : ℕ → ℝ) : (∑ y ∈ Finset.range 9, b y) = 1")
        self.assertEqual(old[1], new[1])
        self.assertNotEqual(old[1], audit.keys("(b : ℕ → ℝ) : (∑ y ∈ Finset.range 8, b y) = 1")[1])

    def test_preamble_turns_open_lines_into_one_prefix(self):
        text = "import Mathlib\nopen Real\nopen Finset Nat\n\n/-- doc -/\ntheorem t (n : ℕ) : 0 ≤ n := by sorry"
        self.assertEqual(audit.preamble(text), ("open Real in\nopen Finset Nat in\n", False))
        scoped = "import Mathlib\nopen Real Set\nopen scoped BigOperators\ntheorem t : (1 : ℝ) ≤ 2 := by sorry"
        self.assertEqual(audit.preamble(scoped), ("open Real Set in\nopen scoped BigOperators in\n", False))
        self.assertEqual(audit.preamble("import Mathlib\ntheorem t : 1 = 1 := by sorry"), ("", False))

    def test_preamble_keeps_helper_definitions_and_marks_solo(self):
        text = "import Mathlib\nopen Real\ndef f (n : ℕ) : ℕ := n + 1\ntheorem t : f 1 = 2 := by sorry"
        prefix, solo = audit.preamble(text)
        self.assertTrue(solo)
        self.assertEqual(prefix, "open Real\ndef f (n : ℕ) : ℕ := n + 1\n")
        self.assertEqual(audit.bare_statement(text), ": f 1 = 2")

    def test_own_line_moves_only_the_first_by(self):
        self.assertEqual(audit.own_line("theorem c : 0 ≤ 1 := by norm_num"), "theorem c : 0 ≤ 1 := by\n  norm_num")
        multi = "theorem c : 0 ≤ 1 := by\n  have h : 0 ≤ 1 := by norm_num\n  exact h"
        self.assertEqual(audit.own_line(multi), multi)
        self.assertEqual(audit.own_line("theorem c : 1 = 1 := by -- note\n  rfl"), "theorem c : 1 = 1 := by -- note\n  rfl")


if __name__ == '__main__':
    unittest.main()
