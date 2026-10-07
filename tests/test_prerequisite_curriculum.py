"""Focused checks for proof diagnostics and matched assistance schedules."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import types

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path[:0] = [str(SCRIPTS / d) for d in ('data', 'train')]


class PrerequisiteChecks(unittest.TestCase):
    def test_number_theory_bootstrap_keeps_evaluation_fixed(self):
        import prepare_prerequisite_curriculum as prepare
        self.assertTrue(hasattr(prepare, "NUMBER_THEORY_BOOTSTRAP"))
        tasks = prepare.NUMBER_THEORY_BOOTSTRAP
        self.assertEqual([q for q in tasks if q[0] != "train"],
                         [q for q in prepare.TASKS if q[0] != "train"])
        self.assertEqual(len([q for q in tasks if q[0] == "train"]), 32)
        self.assertTrue(any("%" in q[3] for q in tasks if q[0] == "train"))
        self.assertTrue(any("∣" in q[3] and q[3].startswith(":")
                            for q in tasks if q[0] == "train"))

    def test_completion_prefixes_leave_a_nonempty_verified_reference_suffix(self):
        import prepare_prerequisite_curriculum as prepare
        self.assertTrue(hasattr(prepare, "completion_forms"), "Completion tasks are missing")
        for split, _, _, _, reference in prepare.TASKS:
            if split != "train":
                continue
            canonical, prefixes = prepare.completion_forms(reference)
            self.assertEqual(prefixes["body"], "")
            for prefix in prefixes.values():
                self.assertTrue(canonical.startswith(prefix))
                self.assertGreater(len(canonical), len(prefix), "The answer must not be supplied in full")
        canonical, prefixes = prepare.completion_forms("exact h1.trans h2")
        self.assertEqual(prefixes["expression"], "exact ")
        self.assertEqual(prefixes["tactic"], "")
        self.assertEqual(canonical, "exact h1.trans h2")

    def test_prefilling_supplies_prefix_but_never_the_reference_suffix(self):
        import run_grpo_pilot as runner
        self.assertTrue(hasattr(runner, "completion_prompt"), "Prefilled prompts are missing")
        q = {"name": "Demo.t", "header": "theorem t (a b c : Nat) (h1 : a = b) (h2 : b = c) : a = c",
             "initial_state": "h1 : a = b\nh2 : b = c\n⊢ a = c", "completion_prefixes": {
                 "expression": "exact ", "tactic": "", "body": ""}}
        messages = runner.completion_prompt(q, "expression")
        self.assertEqual(messages[-1], {"role": "assistant", "content": "```lean\nexact "})
        self.assertNotIn("h1.trans h2", str(messages))
        self.assertEqual(runner.completion_prompt(q, "body")[-1]["content"], "```lean\n")
        with self.assertRaises(ValueError):
            runner.completion_prompt(dict(q, split="heldout"), "expression")

    def test_assistance_decreases_only_when_both_arms_pass_validation(self):
        import run_grpo_pilot as runner
        self.assertTrue(hasattr(runner, "next_completion_stage"), "Shared validation schedule is missing")
        self.assertEqual(runner.next_completion_stage({"random": 2, "curriculum": 1}, 0, 2), 0)
        self.assertEqual(runner.next_completion_stage({"random": 2, "curriculum": 2}, 0, 2), 1)
        self.assertEqual(runner.next_completion_stage({"random": 4, "curriculum": 4}, 2, 2), 2)

    def test_diagnostics_keep_truncation_separate_from_compiler_error(self):
        path = SCRIPTS / "audit/audit_proofs.py"
        self.assertTrue(path.exists(), "The failure classifier is not implemented")
        spec = importlib.util.spec_from_file_location("audit", path)
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
        row = {"generated_tokens": 256, "completion_ids": [7] * 256,
               "verification": {"accepted": False, "status": "returned",
                                "output": "error: unknown identifier 'Nat.imaginary_lemma'"}}
        self.assertEqual(audit.labels(row, 256, {151643, 151645}),
                         ["unknown_symbol", "token_cap_reached"])
        row["verification"]["output"] = "error: unexpected token 'import'; expected tactic"
        row["completion_ids"][-1] = 151645
        self.assertEqual(audit.labels(row, 256, {151643, 151645}), ["syntax_error"])
        row["verification"]["output"] = "error: expected '{' or indented tactic sequence"
        self.assertEqual(audit.labels(row, 256, {151643, 151645}), ["syntax_error"])
        row["verification"]["output"] = "error: unsolved goals"
        self.assertEqual(audit.labels(row, 256, {151643, 151645}), ["proof_not_completed"])
        row["verification"]["accepted"] = True
        self.assertEqual(audit.labels(row, 256, {151643, 151645}), ["accepted"])

    def test_assistance_changes_information_but_not_theorem_or_premises(self):
        import run_grpo_pilot as runner
        q = {"name": "Demo.t", "header": "theorem t (n : Nat) : n = n",
             "initial_state": "n : Nat\n⊢ n = n", "support_names": ["Nat.add_zero"],
             "support_statements": ["Nat.add_zero (n : Nat) : n + 0 = n"],
             "definitions": "Natural numbers use Nat.", "guidance": "Use equality tactics."}
        full = runner.prompt(q, "full")[-1]["content"]
        names = runner.prompt(q, "names")[-1]["content"]
        none = runner.prompt(q, "none")[-1]["content"]
        self.assertIn(q["support_statements"][0], full)
        self.assertNotIn(q["support_statements"][0], names)
        self.assertIn("Nat.add_zero", names)
        self.assertNotIn("Nat.add_zero", none)
        for value in (full, names, none):
            self.assertIn(q["header"], value)
            self.assertNotIn("TARGET_ABSENT", value)

    def test_ranked_orders_preserve_occurrences_and_assistance_passes(self):
        from prepare_grpo_pilot import ordered_ids
        import run_grpo_pilot as runner
        qs = [{"id": "late", "family": "logic", "prerequisite_rank": 2},
              {"id": "early", "family": "divisibility", "prerequisite_rank": 0}]
        self.assertEqual(ordered_ids(qs, "curriculum", 6, 42), ["early", "late"] * 3)
        config = {"assistance_passes": ["full", "names", "none"]}
        self.assertEqual([runner.assistance_at(config, i, 2) for i in range(6)],
                         ["full", "full", "names", "names", "none", "none"])

    def test_tracker_records_readiness_without_inventing_training_metrics(self):
        import json
        writers = {}
        class Writer:
            def __init__(self, path, **kwargs):
                self.values = []
                writers[Path(path).name] = self
            def add_scalar(self, tag, value, step):
                self.values.append((tag, value, step))
            def flush(self):
                pass
            def close(self):
                pass
        fake = types.ModuleType("tensorboardX")
        fake.SummaryWriter = Writer
        spec = importlib.util.spec_from_file_location("tracker", SCRIPTS / "monitor/track_tensorboard.py")
        tracker = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"tensorboardX": fake}):
            spec.loader.exec_module(tracker)
        record = {"status": "no_informative_training_groups", "counts": {
            "generated_tokens": 40, "physical_lean_calls": 30, "rollout_attempts": 4}, "wall_seconds": 10}
        with patch.object(tracker, "fetch", return_value=json.dumps(record).encode()), \
             patch.object(tracker.time, "time", side_effect=[1, 20]), \
             patch.object(tracker.time, "sleep") as sleep:
            tracker.export_grpo({"training_logs": {}, "run_record": "run", "stop_at": 10}, Path("/unused"))
        self.assertIn("progress", writers)
        self.assertIn(("pilot/generated_tokens", 40, 4), writers["progress"].values)
        self.assertFalse(any(tag.startswith("grpo/") for tag, _, _ in writers["progress"].values))
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
