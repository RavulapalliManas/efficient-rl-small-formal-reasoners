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


class TestSetRuleTest(unittest.TestCase):
    def test_labels_come_from_each_source(self):
        m = lambda source, name, split="test": {"source": source, "name": name, "split": split}
        domains = {"a": "Mathematics -> Number Theory -> Primes;", "b": "Mathematics -> Algebra -> Equations;"}
        tags = {"putnam_1": ["number_theory"], "putnam_2": ["analysis"]}
        self.assertTrue(audit.nt_label(m("formalmath", "a"), domains, tags))
        self.assertFalse(audit.nt_label(m("formalmath", "b"), domains, tags))
        self.assertTrue(audit.nt_label(m("putnambench", "putnam_1"), domains, tags))
        self.assertFalse(audit.nt_label(m("putnambench", "putnam_2"), domains, tags))
        self.assertTrue(audit.nt_label(m("proverbench", "x", "number_theory"), domains, tags))
        self.assertFalse(audit.nt_label(m("proverbench", "x", "calculus"), domains, tags))
        self.assertIsNone(audit.nt_label(m("proverbench", "x", "aime"), domains, tags))
        self.assertTrue(audit.nt_label(m("minif2f_v2s", "mathd_numbertheory_3"), domains, tags))
        self.assertFalse(audit.nt_label(m("minif2f_v2s", "mathd_algebra_478"), domains, tags))
        self.assertIsNone(audit.nt_label(m("minif2f_v2s", "aime_1983_p1"), domains, tags))
        self.assertTrue(audit.nt_label(m("exp028_selection", "ntb_0"), domains, tags))

    def test_level_comes_from_each_source(self):
        def m(source, name, split=""):
            return {"source": source, "name": name, "split": split}
        origins = {f: o for f, o in zip("abcdefgh", ("olympid", "omni", "blue_prove", "hardmath",
                                                      "aime", "blue_quant", "u-math", "DEMIMATH"))}
        for name in "abcd":
            self.assertEqual(audit.level(m("formalmath", name), origins), "olympiad")
        for name in "efgh":
            self.assertEqual(audit.level(m("formalmath", name), origins), "standard")
        self.assertEqual(audit.level(m("putnambench", "putnam_1"), origins), "olympiad")
        self.assertEqual(audit.level(m("minif2f_v2s", "imo_1959_p1"), origins), "olympiad")
        self.assertEqual(audit.level(m("minif2f_v1_aimo", "imosl_2007_algebra_p6"), origins), "olympiad")
        for name in ("mathd_numbertheory_3", "algebra_sqineq_at2malt1", "amc12a_2019_p21", "aime_1983_p1",
                     "induction_12dvd4expnp1p20", "numbertheory_4x3m7y3neq2003"):
            self.assertEqual(audit.level(m("minif2f_v2c", name), origins), "standard")
        self.assertEqual(audit.level(m("proverbench", "x", "aime"), origins), "standard")
        for pool in ("prepared_v1", "release_v3", "exp028_selection"):
            self.assertEqual(audit.level(m(pool, "ntb_0"), origins), "standard")
        with self.assertRaises(KeyError):
            audit.level(m("formalmath", "missing"), origins)

    def test_problem_is_standard_only_if_every_member_is(self):
        self.assertEqual(audit.problem_level(["standard", "standard"]), "standard")
        self.assertEqual(audit.problem_level(["standard", "olympiad"]), "olympiad")
        self.assertEqual(audit.problem_level(["olympiad"]), "olympiad")

    def test_keywords_match_number_theory_text_only(self):
        self.assertTrue(audit.NT_KEYWORDS.search(audit.bare_statement(MATHD)))
        self.assertTrue(audit.NT_KEYWORDS.search("(p : ℕ) (hp : Nat.Prime p) : p ∣ 2 ^ p - 2"))
        self.assertIsNone(audit.NT_KEYWORDS.search("(x : ℝ) (h : 2 * x + 3 = 7) : x = 2"))

    def test_hash_order_is_stable_when_the_pool_grows(self):
        small = audit.hash_order("general", ["r1", "r2", "r3"])
        large = audit.hash_order("general", ["r1", "r2", "r3", "r4", "r5"])
        self.assertEqual([i for i in large if i in small], small)


if __name__ == '__main__':
    unittest.main()
