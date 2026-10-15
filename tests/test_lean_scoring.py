"""Pure-Python checks of the shared Lean scoring path, the split guard, the budget stop, the shuffled-reward control,
and RL tasks (no Lean, no torch).

The Lean integration tests are scripts/eval/check_scoring_paths.py, check_checker_attacks.py, and
check_task_scoring.py; they need the pinned Lean environment.
"""
from argparse import Namespace
import json
from pathlib import Path
import random
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path[:0] = [str(ROOT / d) for d in ("data", "audit", "eval", "train")]

import bench_lean_verifier  # noqa: E402
import probe_nt_pool  # noqa: E402
import run_lean_grpo  # noqa: E402

POOL = [{"name": "t1", "split": "train"}, {"name": "t2", "split": "train"},
        {"name": "v1", "split": "validation"}, {"name": "x1", "split": "test"}]
CONFIG = {"max_wall_seconds": 100, "max_generated_tokens": 1000, "max_lean_calls": 50}


def statement_ctx():
    return Namespace(local=threading.local(), pool_repls=set(), restart_after=400)


class SplitGuardTest(unittest.TestCase):
    def test_default_keeps_only_train(self):
        self.assertEqual([r["name"] for r in run_lean_grpo.train_statements(POOL)], ["t1", "t2"])

    def test_validation_name_stops_the_run(self):
        with self.assertRaises(RuntimeError):
            run_lean_grpo.train_statements(POOL, {"t1", "v1"})

    def test_test_name_stops_the_run(self):
        with self.assertRaises(RuntimeError):
            run_lean_grpo.train_statements(POOL, {"x1"})

    def test_unknown_name_stops_the_run(self):
        with self.assertRaises(RuntimeError):
            run_lean_grpo.train_statements(POOL, {"t1", "missing"})

    def test_train_names_pass(self):
        self.assertEqual([r["name"] for r in run_lean_grpo.train_statements(POOL, {"t2"})], ["t2"])


class BudgetTest(unittest.TestCase):
    def counts(self, tokens=0, calls=0):
        return {"generated_tokens": tokens, "lean_calls": calls}

    def test_under_every_budget(self):
        self.assertFalse(run_lean_grpo.over_budget(self.counts(999, 49), 99.0, CONFIG))

    def test_token_budget_stops(self):
        self.assertTrue(run_lean_grpo.over_budget(self.counts(1000, 0), 0.0, CONFIG))

    def test_lean_call_budget_stops(self):
        self.assertTrue(run_lean_grpo.over_budget(self.counts(0, 50), 0.0, CONFIG))

    def test_wall_budget_stops(self):
        self.assertTrue(run_lean_grpo.over_budget(self.counts(), 100.0, CONFIG))


class PrecheckTest(unittest.TestCase):
    """Candidates rejected before Lean: no REPL opens, so these run without Lean."""

    def verdicts(self, texts, kind="statement"):
        unit = {"task_id": "pool:t1", "kind": kind, "name": "t1", "header": "theorem t1 : True", "given_steps": []}
        return bench_lean_verifier.verify_group(statement_ctx(), unit, texts)

    def test_forbidden_constructs(self):
        texts = ["  sorry", "  exact?", "  native_decide", "  set_option maxHeartbeats 0 in simp", "  admit", "  #eval 1",
                 '  run_tac Lean.logInfo "x"', "  exact by_elab return Lean.mkConst ``True.intro", "  conv => run_conv pure ()",
                 "  observe h : 2 ∣ 4\n  simp"]
        self.assertEqual({r["status"] for r in self.verdicts(texts)}, {"forbidden_construct"})

    def test_forbidden_ignores_comments_only(self):
        from probe_start_depth import FORBIDDEN
        allowed = ["  -- we observe that a hint or sorry is not needed\n  simp",
                   "  /- outer /- inner sorry -/ still a comment with # and axiom -/\n  omega",
                   "  /-- doc: admit nothing -/\n  norm_num"]
        blocked = ["  -- note\n  exact?", "  simp -- fine\n  sorry", '  exact (show "--" = "--" from rfl) ▸ sorry',
                   "  /- a -/ observe h : 2 ∣ 4", '  have h : "/-" = "/-" := rfl\n  admit']
        self.assertEqual([bool(FORBIDDEN.search(t)) for t in allowed], [False] * len(allowed))
        self.assertEqual([bool(FORBIDDEN.search(t)) for t in blocked], [True] * len(blocked))

    def test_empty_and_top_level_lines(self):
        texts = ["", "   \n", "begin\n  simp\nend", "  simp\ntheorem x : True := trivial"]
        self.assertEqual({r["status"] for r in self.verdicts(texts)}, {"empty_or_top_level_line"})

    def test_rejections_cost_no_lean_call(self):
        self.assertEqual(sum(r["lean_calls"] for r in self.verdicts(["  sorry", ""])), 0)


class ScoreTest(unittest.TestCase):
    def test_cut_at_fence_group_by_task_and_keep_order(self):
        seen = []

        def fake(ctx, unit, texts):
            seen.append((unit["task_id"], list(texts)))
            return [{"status": "accepted" if t == "  rfl" else "rejected"} for t in texts]

        original = run_lean_grpo.verify_group
        run_lean_grpo.verify_group = fake
        try:
            from concurrent.futures import ThreadPoolExecutor
            by_id = {"a": {"task_id": "a"}, "b": {"task_id": "b"}}
            with ThreadPoolExecutor(2) as executor:
                texts, groups, results = run_lean_grpo.score(
                    None, by_id, ["a", "b", "a", "b"], ["  rfl\n```\nmore", "  simp", "  rfl", "  rfl```"], executor)
        finally:
            run_lean_grpo.verify_group = original
        self.assertEqual(texts, ["  rfl\n", "  simp", "  rfl", "  rfl"])
        self.assertEqual(groups, {"a": [0, 2], "b": [1, 3]})
        self.assertEqual([r["status"] for r in results], ["rejected", "rejected", "accepted", "accepted"])
        self.assertEqual(sorted(seen), [("a", ["  rfl\n", "  rfl"]), ("b", ["  simp", "  rfl"])])



# The checker module (guard, source_for) lives outside the repository (proof_replay_v1/code/checker.py); same text.
def guard(name):
    return ('  run_tac do\n    if (← Lean.getEnv).contains (' + json.dumps(name) +
            '.toName) then\n      throwError "TARGET_PRESENT"\n    else\n      Lean.logInfo "TARGET_ABSENT"\n')


def source_for(q, body):
    return q['prefix'] + q['header'] + ' := by\n' + guard(q['name']) + body + '\n' + '\n'.join(q['closing']) + '\n'


class FakeRepl:
    """Answers each command with respond(cmd); records the commands."""

    def __init__(self, respond):
        self.respond, self.commands = respond, []

    def send(self, request, timeout):
        self.commands.append(request["cmd"])
        return self.respond(request["cmd"]), 0.01, 0.0

    def close(self):
        return {}


def info(line, data):
    return {"severity": "info", "pos": {"line": line, "column": 0}, "data": data}


def score_with(respond, unit, texts):
    """verify_group on a FakeRepl; returns the verdicts and the commands sent."""
    repl = FakeRepl(respond)
    original = bench_lean_verifier.open_repl
    bench_lean_verifier.open_repl = lambda ctx, head, tag, record: (repl, 0)
    try:
        ctx = Namespace(local=threading.local(), pool_repls=set(), restart_after=400, timeout=20,
                        checker=Namespace(source_for=source_for, guard=guard))
        return bench_lean_verifier.verify_group(ctx, unit, texts), repl.commands
    finally:
        bench_lean_verifier.open_repl = original


UNIT = {"task_id": "pool:t1", "kind": "statement", "name": "t1", "header": "theorem t1 : True", "given_steps": []}


class ReplOutputTest(unittest.TestCase):
    def test_detailed_error_response_preserves_the_lean_cause(self):
        response = {"message": "Lean error:\nunknown identifier 'missing_fixture'"}
        code, output = bench_lean_verifier.repl_output(response)
        self.assertEqual(code, 1)
        self.assertEqual(bench_lean_verifier.first_error(output), "unknown identifier 'missing_fixture'")

    def test_opaque_error_response_remains_an_error(self):
        code, output = bench_lean_verifier.repl_output({"message": "Lean error:\ninternal exception #4"})
        self.assertEqual(code, 1)
        self.assertEqual(output, "error: internal exception #4")


class AxiomReportTest(unittest.TestCase):
    """Regression for the forged-report and namespace-shadow attacks (check_checker_attacks.py, 2026-10-10)."""

    def test_report_at_the_print_line(self):
        self.assertEqual(bench_lean_verifier.axiom_report({"messages": [info(9, "'t1' depends on axioms: [propext]")]}, 9, "t1"),
                         ["propext"])
        self.assertEqual(bench_lean_verifier.axiom_report({"messages": [info(9, "'t1' does not depend on any axioms")]}, 9, "t1"), [])

    def test_forged_report_elsewhere_does_not_count(self):
        messages = [info(3, "'t1' does not depend on any axioms"), info(9, "'t1' depends on axioms: [propext, sorryAx]")]
        self.assertEqual(bench_lean_verifier.axiom_report({"messages": messages}, 9, "t1"), ["propext", "sorryAx"])

    def test_report_on_another_constant_does_not_count(self):
        response = {"messages": [info(9, "'Shadow.t1' does not depend on any axioms")]}
        self.assertIsNone(bench_lean_verifier.axiom_report(response, 9, "t1"))

    def test_private_target(self):
        response = {"messages": [info(9, "'_private.0.Nat.t1' depends on axioms: [propext, Classical.choice, Quot.sound]")]}
        self.assertEqual(bench_lean_verifier.axiom_report(response, 9, "Nat.t1"), ["propext", "Classical.choice", "Quot.sound"])
        response = {"messages": [info(9, "'_private.0.Shadow.Nat.t1' does not depend on any axioms")]}
        self.assertIsNone(bench_lean_verifier.axiom_report(response, 9, "Nat.t1"))

    def test_missing_report(self):
        self.assertIsNone(bench_lean_verifier.axiom_report({"messages": [info(3, "TARGET_ABSENT")]}, 9, "t1"))


class ScorerCommandTest(unittest.TestCase):
    def respond(self, report, extra=()):
        def answer(cmd):
            return {"env": 1, "messages": [info(2, "TARGET_ABSENT"), *extra, info(cmd.count("\n"), report)]}
        return answer

    def test_true_report_is_accepted(self):
        results, _ = score_with(self.respond("'t1' depends on axioms: [propext]"), UNIT, ["  trivial"])
        self.assertEqual(results[0]["status"], "accepted")

    def test_forged_report_is_rejected(self):
        forged = [info(12, "'t1' does not depend on any axioms")]
        results, _ = score_with(self.respond("'t1' depends on axioms: [sorryAx]", forged), UNIT,
                                ['  trace "\'t1\' does not depend on any axioms"\n  exact sorryAx _ false'])
        self.assertEqual(results[0]["status"], "rejected")

    def test_shadowed_report_is_rejected(self):
        results, _ = score_with(self.respond("'Shadow.t1' does not depend on any axioms"), UNIT,
                                ["  exact sorryAx _ false\n  namespace Shadow\n  theorem t1 : True := trivial"])
        self.assertEqual(results[0]["status"], "rejected")

    def test_command_checks_one_declaration_then_prints_axioms(self):
        _, commands = score_with(self.respond("'t1' depends on axioms: []"), UNIT, ["  trivial"])
        declaration = "theorem t1 : True := by\n" + guard("t1") + "  trivial\n\n"
        self.assertTrue(commands[0].startswith(bench_lean_verifier.one_command_check(declaration)))
        self.assertTrue(commands[0].endswith(declaration + "\n#print axioms t1\n"))

    def test_lean_string_escapes(self):
        self.assertEqual(bench_lean_verifier.lean_string('a"b\\c\nd\x01'), '"a\\"b\\\\c\\nd\\x01"')


class ShuffleTest(unittest.TestCase):
    """Shuffled-reward control: same multiset per group, no link to correctness, reproducible."""

    def groups(self, count=2000, size=8, seed=1):
        rng = random.Random(seed)
        out = []
        for _ in range(count):
            p = rng.random()
            out += [float(rng.random() < p) for _ in range(size)]
        return out

    def test_multiset_kept_per_group(self):
        rewards = self.groups(200)
        shuffled = run_lean_grpo.shuffle_within_groups(rewards, 8, random.Random(0))
        for i in range(0, len(rewards), 8):
            self.assertEqual(sorted(shuffled[i:i + 8]), sorted(rewards[i:i + 8]))
        self.assertNotEqual(shuffled, rewards)

    def test_uncorrelated_with_correctness_within_groups(self):
        def centered(values):
            out = []
            for i in range(0, len(values), 8):
                mean = sum(values[i:i + 8]) / 8
                out += [v - mean for v in values[i:i + 8]]
            return out

        def corr(a, b):
            num = sum(x * y for x, y in zip(a, b))
            return num / (sum(x * x for x in a) * sum(y * y for y in b)) ** 0.5

        correct = self.groups()
        shuffled = run_lean_grpo.shuffle_within_groups(correct, 8, random.Random(0))
        self.assertAlmostEqual(corr(centered(correct), centered(correct)), 1.0)
        self.assertLess(abs(corr(centered(shuffled), centered(correct))), 0.05)

    def test_seed_reproducible(self):
        rewards = self.groups(100)
        first = run_lean_grpo.shuffle_within_groups(rewards, 8, random.Random(20261009))
        self.assertEqual(first, run_lean_grpo.shuffle_within_groups(rewards, 8, random.Random(20261009)))
        self.assertNotEqual(first, run_lean_grpo.shuffle_within_groups(rewards, 8, random.Random(7)))

    def test_partial_group_raises(self):
        with self.assertRaises(ValueError):
            run_lean_grpo.shuffle_within_groups([1.0] * 12, 8, random.Random(0))


class FakeTokenizer:
    def apply_chat_template(self, messages, tokenize, add_generation_prompt):
        return "<user>" + messages[0]["content"] + "<assistant>"


TASK_POOL = [{"name": "t1", "split": "train", "formal_statement": "theorem t1 (n : ℕ) : n + 0 = n := by sorry"},
             {"name": "t2", "split": "train", "formal_statement": "theorem t2 : (2 : ℕ) + 2 = 4 := by sorry"},
             {"name": "v1", "split": "validation", "formal_statement": "theorem v1 : True := by sorry"}]
TASKS = [{"task_id": "t1:prefix_top:1", "name": "t1", "kind": "prefix_top", "header": "theorem t1 (n : ℕ) : n + 0 = n",
          "given": "  induction n with\n  | zero => rfl\n", "state": "case succ ...", "reference_rest": "  | succ k ih => rfl",
          "lines_remaining": 1},
         {"task_id": "t2:whole:0", "name": "t2", "kind": "whole", "header": "theorem t2 : (2 : ℕ) + 2 = 4", "given": "",
          "state": "⊢ 2 + 2 = 4", "reference_rest": "", "lines_remaining": None}]


class TaskTest(unittest.TestCase):
    def test_units_carry_given_and_state(self):
        units = run_lean_grpo.task_units(TASKS, TASK_POOL)
        self.assertEqual([(u["task_id"], u["name"], u["given"], u["state"]) for u in units],
                         [(t["task_id"], t["name"], t["given"], t["state"]) for t in TASKS])

    def test_validation_name_stops_the_run(self):
        with self.assertRaises(RuntimeError):
            run_lean_grpo.task_units(TASKS + [dict(TASKS[1], task_id="v1:whole:0", name="v1", header="theorem v1 : True")], TASK_POOL)

    def test_changed_header_stops_the_run(self):
        with self.assertRaises(RuntimeError):
            run_lean_grpo.task_units([dict(TASKS[1], header="theorem t2 : (2 : ℕ) + 2 = 5")], TASK_POOL)

    def test_forbidden_given_stops_the_run(self):
        with self.assertRaises(RuntimeError):
            run_lean_grpo.task_units([dict(TASKS[0], given="  sorry\n")], TASK_POOL)

    def test_excluded_names(self):
        units = run_lean_grpo.task_units(TASKS, TASK_POOL)
        self.assertEqual(run_lean_grpo.excluded_names(units, {"t2", "x"}), ["t2"])
        self.assertEqual(run_lean_grpo.excluded_names(units, {"x"}), [])

    def test_trainer_and_probe_prompts_and_units_match(self):
        units = run_lean_grpo.task_units(TASKS, TASK_POOL)
        for task, unit in zip(TASKS, units):
            probe_text = probe_nt_pool.prompt(FakeTokenizer(), probe_nt_pool.lean_unit(task), task["state"])
            self.assertEqual(run_lean_grpo.prompt_text(FakeTokenizer(), unit), probe_text)
            self.assertTrue(probe_text.endswith("```lean\n" + task["header"] + " := by\n" + task["given"]))
            self.assertEqual({k: v for k, v in unit.items() if k != "state"}, probe_nt_pool.lean_unit(task))

    def test_whole_task_prompt_equals_pool_statement_prompt(self):
        row, state = TASK_POOL[1], "⊢ 2 + 2 = 4"
        self.assertEqual(probe_nt_pool.prompt(FakeTokenizer(), probe_nt_pool.lean_unit(row), state),
                         probe_nt_pool.prompt(FakeTokenizer(), probe_nt_pool.lean_unit(TASKS[1]), state))

    def test_lean_checks_header_given_then_continuation(self):
        unit = run_lean_grpo.task_units(TASKS, TASK_POOL)[0]
        _, commands = score_with(lambda cmd: {"env": 1, "messages": []}, unit, ["  | succ k ih => rfl"])
        self.assertIn("theorem t1 (n : ℕ) : n + 0 = n := by\n" + guard("t1") + TASKS[0]["given"] + "  | succ k ih => rfl\n",
                      commands[0])


if __name__ == "__main__":
    unittest.main()
