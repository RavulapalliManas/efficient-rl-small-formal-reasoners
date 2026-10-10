"""Check syntax-data boundaries without Lean or model inference."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/eval"))
from bench_lean_verifier import split_prefix
import build_syntax_sft


class SyntaxDataTest(unittest.TestCase):
    def test_doc_comment_with_trailing_line_comment(self):
        prefix = "import Mathlib\n/-- A declaration description. -/\n-- Porting note\n"
        head, tail = split_prefix(prefix)
        self.assertEqual(head, "import Mathlib\n")
        self.assertEqual(tail, "/-- A declaration description. -/\n-- Porting note\n")
        self.assertEqual(head + tail, prefix)

    def test_scoped_attribute_stays_with_declaration(self):
        prefix = "import Mathlib\nattribute [local simp] eq_iff_true_of_subsingleton in\n"
        head, tail = split_prefix(prefix)
        self.assertEqual(head, "import Mathlib\n")
        self.assertEqual(tail, "attribute [local simp] eq_iff_true_of_subsingleton in\n")
        self.assertEqual(head + tail, prefix)
        ordinary = "import Mathlib\nattribute [local simp] eq_iff_true_of_subsingleton\n"
        self.assertEqual(split_prefix(ordinary), (ordinary, ""))

    def test_changed_suffix_is_not_admitted(self):
        whole = {"reference_rest": ["  intro h", "  exact h"]}
        cut = {"given_steps": ["  intro h"], "reference_rest": ["  exact invented"]}
        self.assertFalse(build_syntax_sft.same_proof(whole, cut))
        cut["reference_rest"] = ["  exact h"]
        self.assertTrue(build_syntax_sft.same_proof(whole, cut))

    def test_file_exclusion_and_heldout_premises(self):
        unit = {"file_path": "Mathlib/Logic/Basic.lean", "full_name": "logic_demo",
                "premises": ["heldout_lemma"], "header": "theorem logic_demo : True"}
        self.assertEqual(build_syntax_sft.exclusion(unit, {unit["file_path"]}, set(), set()), "excluded_file")
        self.assertEqual(build_syntax_sft.exclusion(unit, set(), {"heldout_lemma"}, set()), "evaluation_premise")
        self.assertIsNone(build_syntax_sft.exclusion(unit, set(), set(), set()))


if __name__ == "__main__":
    unittest.main()
