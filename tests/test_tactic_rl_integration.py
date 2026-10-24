"""Marked integration group. Run on CPU fleet with TACTIC_RL_ROOT set.

Peak: two mathlib REPLs x 4 GiB + tiny Torch policy <1 GiB; cap 12 GiB.
The synthetic proofs test the environment, not benchmark performance.
"""
from contextlib import nullcontext
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('TACTIC_RL_ROOT'), 'Fleet Lean/Torch integration only')
class TacticIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(os.environ['TACTIC_RL_ROOT'])
        cls.output = Path(os.environ['TACTIC_RL_OUTPUT'])
        cls.output.mkdir()
        path = Path(__file__).resolve().parents[1] / 'scripts/train/run_tactic_rl.py'
        spec = importlib.util.spec_from_file_location('tactic_integration', path)
        cls.rl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.rl)
        cls.environment = __import__('rl_components.environment', fromlist=['LeanEpisode'])
        environment = cls.root / 'lean_env_full'
        checker = cls.rl.load_checker(cls.root / 'proof_replay_v1/code/checker.py', environment, cls.output)
        cls.ctx = cls.rl.repl_context(checker, environment,
            cls.root / 'rlvr_verifier/src/repl/.lake/build/bin/repl', cls.output, 20, 120, 32)
        cls.counts = dict(lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0)

    @classmethod
    def tearDownClass(cls):
        for repl in cls.ctx.pool_repls:
            cls.rl.close_repl(repl)
        (cls.output / 'costs.json').write_text(json.dumps(cls.counts, indent=2) + '\n')

    def episode(self, header):
        task = dict(task_id='synthetic', name='tactic_fixture', kind='whole', given='',
                    state='', header=header)
        return self.rl.LeanEpisode(self.ctx, task, self.output, self.counts)

    def episode_v2(self, header, **kwargs):
        task = dict(task_id='synthetic', name='tactic_fixture', kind='whole', given='', state='', header=header)
        return self.rl.LeanEpisode(self.ctx, task, self.output, self.counts, version=2, horizon=6, **kwargs)

    def test_candidate_menu_preserves_live_state_and_selected_path_replays(self):
        """Authored fixture only; requires a source receipt, not benchmark proofs."""
        import copy
        pin = (self.root / 'lean_env_full/mathlib/lean-toolchain').read_text().strip()
        self.assertEqual(pin, 'leanprover/lean4:v4.10.0-rc1')
        identity = self.rl.mathlib_identity(Path(os.environ['TACTIC_RL_IDENTITY']),
            '29dcec074de168ac2bf835a77ef68bbe069194c5')
        version = subprocess.run([str(self.ctx.checker.LEAN / 'lean'), '--version'],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertIn('4.10.0-rc1', version)
        evidence = dict(lean=pin, lean_binary=version, mathlib=identity, fixture='authored synthetic',
                        benchmark_performance=False, menus=[])
        env = self.episode_v2('theorem tactic_fixture (p q : Prop) (hp : p) (hq : q) : p ∧ q',
                              max_tactics=1)
        try:
            env.reset()
            for candidates, chosen in [
                    (['constructor', 'exact hp', 'exact missing_candidate', 'skip', 'sorry'], 'constructor'),
                    (['assumption', 'exact hq'], 'assumption'),
                    (['exact hq', 'exact missing_candidate'], 'exact hq')]:
                fields = ('state', 'goals', 'path', 'shown', 'steps_used', 'failed', 'frames')
                before = copy.deepcopy({key: getattr(env, key) for key in fields})
                calls = self.counts['lean_calls']
                menu = env.candidate_menu(candidates, max_candidates=5, max_lean_calls=200)
                self.assertEqual(menu['status'], 'ready', menu)
                self.assertEqual({key: getattr(env, key) for key in fields}, before)
                self.assertGreater(self.counts['lean_calls'], calls)
                admitted = {action['tactic']: action['id'] for action in menu['actions']}
                self.assertIn(chosen, admitted)
                self.assertNotIn('exact missing_candidate', admitted)
                self.assertNotIn('skip', admitted)
                self.assertNotIn('sorry', admitted)
                result = env.select_candidate(menu, admitted[chosen])
                evidence['menus'].append(dict(menu=menu, selected=chosen, result=result))
            self.assertTrue(result['accepted'], result)
            self.assertEqual(result['replay']['status'], 'accepted')
            self.assertEqual(env.shown, ['constructor', 'assumption', 'exact hq'])
            self.assertNotIn('missing_candidate', result['proof_body'])
        finally:
            env.close()
            evidence['counts'] = dict(self.counts)
            (self.output / 'candidate_menu.json').write_text(json.dumps(evidence, indent=2) + '\n')

    def test_v2_commented_chunk_closes_and_replays(self):
        env = self.episode_v2('theorem tactic_fixture : True ∧ True')
        try:
            env.reset()
            with patch.object(self.environment, 'verify_group', wraps=self.rl.verify_group) as replay:
                result = env.step('-- split the conjunction\nconstructor\n/- both sides -/\nexact True.intro\nexact True.intro', 0)
            self.assertTrue(result['accepted'], result)
            self.assertEqual((result['tactics_applied'], result['replay']['status']), (3, 'accepted'))
            expected = ('  first |\n    -- split the conjunction\n    constructor\n'
                        '  first |\n    /- both sides -/\n    exact True.intro\n'
                        '  first |\n    exact True.intro\n')
            self.assertEqual(result['proof_body'], expected)
            self.assertEqual(replay.call_args.args[2], [result['proof_body']])
        finally:
            env.close()

    def test_v2_first_error_truncates_chunk_and_self_loops(self):
        # K = 3: two distinct failures here must not trigger the harness revert.
        env = self.episode_v2('theorem tactic_fixture : True ∧ True', backtrack_after=3)
        try:
            env.reset()
            result = env.step('constructor\nexact missing_fixture\nexact True.intro', 0)
            self.assertEqual((result['status'], result['tactics_applied']), ('partial', 1), result)
            self.assertEqual(len(env.goals), 2)
            state = env.state
            loop = env.step('exact missing_other', 0)
            self.assertEqual((loop['status'], loop['done'], env.state), ('rejected', False, state), loop)
            self.assertIn('missing_fixture', env.prompt_state())
            with patch.object(self.environment, 'verify_group', wraps=self.rl.verify_group) as replay:
                final = env.step('exact True.intro\nexact True.intro', 0)
            self.assertTrue(final['accepted'], final)
            expected = ('  first |\n    constructor\n'
                        '  first |\n    exact True.intro\n  first |\n    exact True.intro\n')
            self.assertEqual(final['proof_body'], expected)
            self.assertNotIn('missing_fixture', final['proof_body'])
            self.assertNotIn('missing_other', final['proof_body'])
            self.assertEqual(replay.call_args.args[2], [final['proof_body']])
        finally:
            env.close()

    def test_v2_harness_revert_reuses_stored_root_state(self):
        env = self.episode_v2('theorem tactic_fixture (p q : Prop) (hp : p) (hq : q) : p ∧ q', backtrack_after=2)
        try:
            env.reset()
            root = env.state
            self.assertEqual(env.step('constructor', 0)['status'], 'changed')
            env.step('exact missing_a', 0)
            reverted = env.step('exact missing_b', 0)
            self.assertTrue(reverted['harness_backtrack'], reverted)
            self.assertEqual((env.state, env.shown), (root, []))
            final = env.step('exact ⟨hp, hq⟩', 0)
            self.assertTrue(final['accepted'], final)
        finally:
            env.close()

    def test_v2_policy_backtrack_reuses_stored_root_state(self):
        env = self.episode_v2('theorem tactic_fixture (p q : Prop) (hp : p) (hq : q) : p ∧ q', backtrack_owner='policy')
        try:
            env.reset()
            env.step('constructor', 0)
            self.assertEqual(env.step('backtrack 0', 0)['status'], 'backtrack')
            with patch.object(self.environment, 'verify_group', wraps=self.rl.verify_group) as replay:
                final = env.step('exact ⟨hp, hq⟩', 0)
            self.assertTrue(final['accepted'], final)
            self.assertEqual(final['proof_body'], '  first |\n    exact ⟨hp, hq⟩\n')
            self.assertNotIn('constructor', final['proof_body'])
            self.assertEqual(replay.call_args.args[2], [final['proof_body']])
        finally:
            env.close()

    def test_v2_restated_header_is_not_executed(self):
        env = self.episode_v2('theorem tactic_fixture : True')
        try:
            env.reset()
            result = env.step('```lean\ntheorem tactic_fixture : True := by\n  /- note -/\n  trivial\n```', 0)
            self.assertTrue(result['accepted'], result)
        finally:
            env.close()

    # Environment audit, 13 October 2026 (exp023-env-audit-v1): real Lean for parsing, replay and REPL reuse.
    PQ = 'theorem tactic_fixture (p q : Prop) (hp : p) (hq : q) : p ∧ q'

    def assertCloses(self, header, action, **kwargs):
        env = self.episode_v2(header, **kwargs)
        try:
            env.reset()
            result = env.step(action, 0)
            self.assertTrue(result['accepted'], (action, result))
            self.assertEqual(result['replay']['status'], 'accepted', (action, result))
            return env, result
        finally:
            env.close()

    def test_v2_every_construct_reaches_the_same_verdict_in_the_repl_and_in_file_replay(self):
        nat = 'theorem tactic_fixture (n : ℕ) : n + 0 = n'
        calc = 'theorem tactic_fixture (a b c : ℕ) (h1 : a = b) (h2 : b = c) : a = c'
        cases = [
            (self.PQ, 'constructor\n· exact hp\n· exact hq'),
            (self.PQ, 'constructor\ncase left => exact hp\ncase right =>\n  exact hq'),
            (self.PQ, 'constructor\nnext => exact hp\nnext => exact hq'),
            (self.PQ, 'constructor; exact hp; exact hq'),
            (self.PQ, 'constructor\n<;> assumption'),
            (self.PQ, 'have h : p := by\n  exact hp\nexact ⟨h, hq⟩'),
            (self.PQ, 'have h : p := by\n\texact hp\nexact ⟨h, hq⟩'),
            (self.PQ, '-- split\nconstructor /- both -/\n/- left -/ exact hp -- done\nexact hq\n-- end'),
            (self.PQ, 'exact ⟨hp,\nhq⟩'),
            (self.PQ, 'rcases (Or.inl hp : p ∨ q) with h | h\n· exact ⟨h, hq⟩\n· exact ⟨hp, h⟩'),
            (self.PQ, '```lean4\r\nconstructor\r\nexact hp\r\nexact hq\r\n```'),
            (self.PQ, '```Lean\nexact ⟨hp, hq⟩\n```'),
            (self.PQ, 'have s : "a -- b" = "a -- b" := rfl\nhave c := (\'"\', "--")\nexact ⟨hp, hq⟩'),
            (self.PQ, 'trace "info message"; exact ⟨hp, hq⟩'),
            (self.PQ, '-- sorry and native_decide are only words here\nexact ⟨hp, hq⟩'),
            (nat, 'induction n with\n| zero => rfl\n| succ k ih => rfl'),
            (nat, 'induction n with\n  | zero => rfl\n  | succ k ih => rfl'),
            ('theorem tactic_fixture (r : Prop) [Decidable r] : True', 'if r then\n  trivial\nelse\n  trivial'),
            (calc, 'calc a = b := h1\n_ = c := h2'),
            (calc, 'calc a = b := h1\n     _ = c := h2'),
            (calc, 'calc\n  a = b := h1\n  _ = c := h2'),
            ('theorem tactic_fixture (r : Prop) : r → r', 'exact fun h => by\n  exact h'),
        ]
        for header, action in cases:
            with self.subTest(action=action):
                self.assertCloses(header, action)

    def test_v2_closing_with_sorryax_is_not_accepted_by_replay(self):
        # The forbidden filter matches the word sorry only; the full-file axiom check must catch sorryAx.
        env = self.episode_v2('theorem tactic_fixture : True ∧ True')
        try:
            env.reset()
            result = env.step('exact sorryAx (True ∧ True) false', 0)
            self.assertEqual((result['accepted'], result['reward'], result['status']), (False, 0.0, 'replay_rejected'))
        finally:
            env.close()

    def test_v2_forbidden_code_hidden_after_a_char_literal_never_reaches_lean(self):
        env = self.episode_v2('theorem tactic_fixture : True')
        try:
            env.reset()
            calls = self.counts['lean_calls']
            result = env.step("have h := ('\"', \"--\"); run_tac pure ()\ntrivial", 0)
            self.assertEqual(result['status'], 'forbidden_construct')
            self.assertEqual(self.counts['lean_calls'], calls)
        finally:
            env.close()

    def test_v2_parse_error_positions_refer_to_the_action(self):
        env = self.episode_v2('theorem tactic_fixture (n : ℕ) : n + 0 = n')
        try:
            env.reset()
            result = env.step('induction n with', 0)
            self.assertEqual(result['status'], 'rejected')
            self.assertIn('<input>:1:16:', result['error'])
        finally:
            env.close()

    def test_v2_first_wrapper_keeps_error_recovery_errors(self):
        # A bullet admits its goal after an error; the error message must still reject the step.
        env = self.episode_v2(self.PQ)
        try:
            env.reset()
            result = env.step('constructor\n· exact missing_name\n· exact hq', 0)
            self.assertEqual((result['status'], result['tactics_applied']), ('partial', 1), result)
            self.assertEqual(len(env.goals), 2)
        finally:
            env.close()

    def test_v2_backtracks_and_group_reuse_keep_stored_proof_states_valid(self):
        env = self.episode_v2(self.PQ, backtrack_owner='policy')
        try:
            state = env.reset()
            calls = self.counts['lean_calls']
            env.step('constructor', 0)
            env.step('swap', 0)
            env.step('exact hq', 0)
            self.assertEqual(env.shown, ['constructor', 'swap', 'exact hq'])
            self.assertEqual(env.step('backtrack 1', 0)['status'], 'backtrack')
            self.assertEqual(env.shown, ['constructor'])
            result = env.step('exact hp\nexact hq', 0)
            self.assertTrue(result['accepted'], result)
            self.assertEqual(len(env.path), 3)
            used = self.counts['lean_calls']
            self.assertEqual(env.reset(), state)  # Same REPL, same stored root state: no setup calls.
            self.assertEqual(self.counts['lean_calls'], used)
            self.assertTrue(env.step('exact ⟨hp, hq⟩', 0)['accepted'])
            self.assertGreater(used, calls)
        finally:
            env.close()

    def test_v2_harness_revert_then_new_path_closes_and_replays(self):
        env = self.episode_v2(self.PQ, backtrack_after=2)
        try:
            env.reset()
            env.step('constructor', 0)
            env.step('exact hq', 0)  # The first goal is p, so this fails.
            result = env.step('exact (hq)', 0)
            self.assertTrue(result['harness_backtrack'], result)
            self.assertEqual((env.shown, len(env.frames)), ([], 1))
            self.assertTrue(env.step('exact ⟨hp, hq⟩', 0)['accepted'])
            self.assertEqual(len(env.path), 1)
        finally:
            env.close()

    def test_v2_timeout_and_repl_crash_end_the_episode_and_the_next_reset_rebuilds(self):
        env = self.episode_v2(self.PQ)
        try:
            env.reset()
            with patch.object(self.ctx, 'timeout', 0.0):
                result = env.step('constructor', 0)
            self.assertEqual((result['status'], result['done'], result['reward']), ('timeout', True, 0.0))
            self.assertIsNone(env.repl)
            env.reset()
            self.assertTrue(env.step('exact ⟨hp, hq⟩', 0)['accepted'])
            env.reset()
            env.repl.proc.kill()
            env.repl.proc.wait()
            crash = env.step('constructor', 0)
            self.assertEqual((crash['status'], crash['done'], crash['censored']), ('crash', True, True))
            self.assertIsNone(env.repl)
            env.reset()
            self.assertTrue(env.step('exact ⟨hp, hq⟩', 0)['accepted'])
        finally:
            env.close()

    def test_v2_state_keys_repeat_for_the_same_state_and_differ_after_progress(self):
        env = self.episode_v2('theorem tactic_fixture (a b c : ℕ) (h1 : a ≤ b) (h2 : b ≤ c) : a ≤ c')
        try:
            env.reset()
            keys = []
            for tactic in ('apply Nat.le_trans', 'apply Nat.le_trans', 'intros', 'refine Nat.le_trans h1 ?_'):
                reply = env.send({'tactic': self.rl.tactic_block(tactic), 'proofState': env.state}, 20)
                keys.append(self.rl.state_key(reply.get('goals', [])))
            self.assertEqual(keys[0], keys[1])
            self.assertEqual(len({keys[0], keys[3], self.rl.state_key(env.goals)}), 3)
            self.assertEqual(env.step('skip', 0)['status'], 'no_progress')
            first = env.step('refine Nat.le_trans h1 ?_', 0)
            self.assertEqual(first['status'], 'changed')
            self.assertEqual(env.step('exact h2', 0)['status'], 'accepted')
        finally:
            env.close()

    def test_real_goal_split_and_complete_proof_replay(self):
        env = self.episode('theorem tactic_fixture : True ∧ True')
        try:
            env.reset()
            result = env.step('constructor', 0.025)
            self.assertEqual(result['status'], 'changed')
            self.assertEqual(len(env.goals), 2)
            self.assertEqual(env.step('exact True.intro', 0.025)['status'], 'changed')
            final = env.step('exact True.intro', 0.025)
            self.assertTrue(final['accepted'])
            self.assertEqual(final['replay']['status'], 'accepted')
        finally:
            env.close()

    def test_local_root_hides_target_and_preserves_named_replay(self):
        cases = [
            ('tactic_fixture', 'theorem tactic_fixture : True', 'exact True.intro'),
            ('tactic_fixture', 'private theorem tactic_fixture : True', 'exact True.intro'),
            ('tactic_fixture', '@[simp] theorem tactic_fixture : True', 'exact True.intro'),
            ('Fixture.target', 'theorem Fixture.target (p : Prop) (hp : p) : p', 'exact hp'),
            ('tactic_fixture', 'theorem tactic_fixture.{u} (α : Type u) (a : α) : a = a', 'rfl'),
        ]
        for name, header, proof in cases:
            with self.subTest(header=header):
                task = dict(task_id='synthetic', name=name, kind='whole', given='', state='', header=header)
                env = self.rl.LeanEpisode(self.ctx, task, self.output, self.counts, version=2, horizon=6)
                try:
                    observation = env.reset()
                    self.assertIn(header, observation)
                    goals = list(env.goals)
                    reference = env.step('have stolen := ' + name, 0)
                    self.assertEqual(reference['status'], 'rejected', reference)
                    self.assertNotIn('replay', reference)
                    self.assertEqual(env.goals, goals)
                    anonymous = env.step('have stolen := _example', 0)
                    self.assertEqual(anonymous['status'], 'rejected', anonymous)
                    self.assertNotIn('replay', anonymous)
                    self.assertEqual(env.goals, goals)
                    final = env.step(proof, 0)
                    self.assertTrue(final['accepted'], final)
                    self.assertEqual(final['replay']['status'], 'accepted')
                    self.assertEqual(task['header'], header)
                finally:
                    env.close()

    def test_native_bank_scoped_context_reset_and_complete_replay(self):
        """Synthetic file-context fixture; no library source or reference proof is loaded."""
        task = dict(task_id='native-fixture:k2', theorem_id='native-fixture', kind='bank',
                    name='NativeBankFixture.target', header='theorem target : True ∧ True',
                    given_steps=['  have h : True := premise', '  constructor'], state='HIDDEN_REFERENCE_SENTINEL')
        q = dict(name=task['name'], header=task['header'], closing=['end NativeBankFixture'],
                 prefix=('import Lean.Elab.Tactic\nnamespace NativeBankFixture\n'
                         'theorem premise : True := True.intro\nopen Nat in\n@[simp]\nprivate\n'))
        self.ctx.rows['native-fixture'] = {}
        try:
            with patch.object(self.environment, 'question', return_value=(q, None)), \
                    patch('bench_lean_verifier.question', return_value=(q, None)):
                env = self.rl.LeanEpisode(self.ctx, task, self.output, self.counts)
                try:
                    state = env.reset()
                    self.assertIn('h : True', state)
                    self.assertEqual(len(env.goals), 2)
                    self.assertNotIn(task['state'], state)
                    original = env.state
                    reference = env.step('have stolen := target', 0)
                    self.assertEqual(reference['status'], 'rejected', reference)
                    self.assertNotIn('replay', reference)
                    self.assertEqual(env.state, original)
                    reference = env.step('have stolen := NativeBankFixture.target', 0)
                    self.assertEqual(reference['status'], 'rejected', reference)
                    self.assertNotIn('replay', reference)
                    self.assertEqual(env.state, original)
                    result = env.step('exact NativeBankFixture.target', 0)
                    self.assertFalse(result['accepted'], result)
                    self.assertEqual(result['reward'], 0)
                    self.assertEqual(env.reset(), state)
                    self.assertEqual(env.state, original)
                    final = env.step('all_goals exact h', 0)
                    self.assertTrue(final['accepted'], final)
                    self.assertEqual(final['reward'], 1)
                    self.assertEqual(final['replay']['status'], 'accepted')
                    env.reset()
                    self.assertEqual(len(env.goals), 2)
                    indirect = env.step('all_goals exact sorryAx _ false', 0)
                    self.assertFalse(indirect['accepted'], indirect)
                    self.assertEqual(indirect['status'], 'replay_rejected')
                    self.assertEqual(indirect['reward'], 0)
                finally:
                    env.close()
        finally:
            del self.ctx.rows['native-fixture']

    def test_single_lean_fence_reaches_full_proof_check(self):
        env = self.episode('theorem tactic_fixture : True')
        try:
            env.reset()
            result = env.step('```lean\n  have h : True := by\n    trivial\n  exact h\n```', 0.025)
            self.assertTrue(result['accepted'], result)
            self.assertEqual(result['replay']['status'], 'accepted')
        finally:
            env.close()

    def test_real_local_feedback_and_complete_proof_reward(self):
        env = self.episode('theorem tactic_fixture : True')
        env.reward_mode = 'error_feedback_v1'
        try:
            for tactic, reward, component in (
                    ('exact missing_fixture', -0.2, 'unresolved_name'),
                    ('exact (0 : Nat)', -0.1, 'name_or_type_resolution'),
                    ('exact (', -0.2, 'syntax_or_structure')):
                env.reset()
                result = env.step(tactic, 0.025)
                self.assertEqual(result['reward'], reward, result)
                self.assertEqual(result['reward_component'], component, result)
            env.reset()
            self.assertEqual(env.step('skip', 0.025)['reward'], 0.0)
            env.reset()
            result = env.step('have h : True := by trivial', 0.025)
            self.assertEqual(result['status'], 'changed', result)
            self.assertEqual(result['reward'], 0.0)
            final = env.step('```lean\nexact h\n```', 0.025)
            self.assertEqual(final['reward'], 1.0, final)
            self.assertEqual(final['replay']['status'], 'accepted')
        finally:
            env.close()

    def test_real_v2_error_feedback_penalties(self):
        """Real Lean diagnostics map to the v2 penalties; only the accepted proof earns positive reward."""
        env = self.episode_v2('theorem tactic_fixture : True', reward_mode='error_feedback_v1')
        try:
            env.reset()
            for text, status, reward in (('exact missing_fixture', 'rejected', -0.2),
                                         ('exact (0 : Nat)', 'rejected', -0.1),
                                         ('skip', 'no_progress', -0.1),
                                         ('```lean\nsimp', 'format_error', -0.2),
                                         ('have h : True := by trivial\nexact missing_fixture', 'partial', -0.2)):
                result = env.step(text, 0.0)
                self.assertEqual((result['status'], result['reward']), (status, reward), result)
                self.assertFalse(result['done'], result)
            final = env.step('exact h', 0.0)
            self.assertEqual((final['status'], final['reward']), ('accepted', 1.0), final)
            self.assertEqual(final['replay']['status'], 'accepted')
        finally:
            env.close()

    def test_real_multiline_action_replays_with_relative_indentation(self):
        env = self.episode('theorem tactic_fixture : True')
        try:
            env.reset()
            result = env.step('  have h : True := by\n    trivial\n  exact h', 0.025)
            self.assertEqual(result['status'], 'accepted')
        finally:
            env.close()

    def test_real_no_op_error_and_forbidden_actions(self):
        env = self.episode('theorem tactic_fixture : True')
        try:
            env.reset()
            self.assertEqual(env.step('skip', 0.025)['status'], 'repeated_state')
            env.reset()
            self.assertEqual(env.step('exact missing_fixture', 0.025)['status'], 'rejected')
            before = self.counts['lean_calls']
            self.assertEqual(env.step('sorry', 0.025)['status'], 'forbidden_construct')
            self.assertEqual(self.counts['lean_calls'], before)
        finally:
            env.close()

    def test_actual_train_root_and_assisted_prefix_rebuild(self):
        selected = {}
        excluded = set((self.root / 'nt_splits_v3/dev.txt').read_text().split())
        with (self.root / 'nt_rl_tasks_v3/tasks.jsonl').open() as source:
            for line in source:
                task = json.loads(line)
                if task['kind'] in ('whole', 'prefix_top') and task['name'] not in excluded:
                    selected.setdefault(task['kind'], task)
                if len(selected) == 2:
                    break
        self.assertEqual(set(selected), {'whole', 'prefix_top'})
        for task in selected.values():
            env = self.rl.LeanEpisode(self.ctx, task, self.output, self.counts)
            try:
                self.assertTrue(env.reset().strip())
            finally:
                env.close()

    def test_real_error_repair_uses_last_verified_state_and_excludes_failed_tactic(self):
        task = dict(task_id='repair_fixture', name='repair_fixture', kind='whole',
                    header='theorem repair_fixture : True', given='')
        env = self.rl.LeanEpisode(self.ctx, task, self.output, self.counts,
                                 reward_mode='error_feedback_v1', max_repair_attempts=1)
        try:
            env.reset()
            before = env.state
            failed = env.step('exact missing_fixture', 0)
            self.assertFalse(failed['done'])
            self.assertEqual(failed['reward'], -0.2)
            self.assertEqual(env.state, before)
            self.assertEqual(env.path, [])
            self.assertIn('missing_fixture', env.prompt_state())
            accepted = env.step('exact True.intro', 0)
            self.assertTrue(accepted['accepted'])
            self.assertEqual(accepted['reward'], 1)
            self.assertEqual(len(env.path), 1)
            self.assertNotIn('missing_fixture', env.path[0])
            env.reset()
            self.assertFalse(env.step('exact missing_fixture', 0)['done'])
            self.assertTrue(env.step('exact missing_fixture', 0)['done'])
        finally:
            env.close()

    def test_torch_update_increases_rewarded_action_and_null_group_does_not_update(self):
        import torch
        torch.set_num_threads(2)

        class TinyPolicy(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.logits = torch.nn.Parameter(torch.zeros(3))

            def forward(self, input_ids, **_):
                return type('Output', (), {'logits': self.logits.expand(1, input_ids.shape[1], 3)})()

        model = TinyPolicy().eval()
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.01, weight_decay=0)
        chosen = dict(prompt_ids=[0], action_ids=[1, 2], reward=1.0)
        other = dict(prompt_ids=[0], action_ids=[0, 2], reward=0.0)
        # Token 2 represents sampled EOS, and must contribute to likelihood.
        expected = -2 * torch.log(torch.tensor(3.0))
        self.assertAlmostEqual(float(self.rl.action_logprob(model, [0], [1, 2], torch).detach()), float(expected), places=6)
        result = self.rl.update(model, optimizer, [[chosen], [other]], torch, nullcontext)
        self.assertTrue(result['optimizer_updated'])
        self.assertGreater(float(model.logits[1].detach()), float(model.logits[0].detach()))
        previous = model.logits.detach().clone()
        null = self.rl.update(model, optimizer, [[dict(chosen, reward=0)], [other]], torch, nullcontext)
        self.assertFalse(null['optimizer_updated'])
        self.assertTrue(torch.equal(previous, model.logits.detach()))

    def test_real_environment_control_matrix(self):
        """One REPL at a time, no Torch/model. Reserve 3.7 GiB plus 0.2 GiB Python; cap 4 GiB.

        Replay uses the same unchanged imported environment as the normal cached
        verifier, sharing the episode process to stay within the control-job cap.
        This checks outcomes, not the normal two-process resource envelope.
        """
        from unittest.mock import patch
        current = {'case': None}
        original_send = self.rl.Repl.send
        environment = self.root / 'lean_env_full'
        identity = self.rl.mathlib_identity(
            self.root / 'tactic_feedback_ready_20261011_v4/inputs/mathlib_source_identity.json',
            '29dcec074de168ac2bf835a77ef68bbe069194c5')
        toolchain = (environment / 'mathlib/lean-toolchain').read_text().strip()
        self.assertEqual(toolchain, 'leanprover/lean4:v4.10.0-rc1')
        version = subprocess.run([str(environment / 'lean-4.10.0-rc1-linux/bin/lean'), '--version'],
                                 check=True, capture_output=True, text=True).stdout.strip()
        self.assertIn('4.10.0-rc1', version)
        (self.output / 'pins.json').write_text(json.dumps(
            dict(lean=toolchain, lean_binary=version, mathlib=identity), indent=2) + '\n')

        def traced_send(repl, request, timeout):
            row = dict(case=current['case'], request=request, timeout_seconds=timeout)
            try:
                response, wall, cpu = original_send(repl, request, timeout)
                row.update(response=response, wall_seconds=wall, cpu_seconds=cpu)
                return response, wall, cpu
            except (TimeoutError, EOFError) as exc:
                row.update(infrastructure_error=type(exc).__name__)
                raise
            finally:
                with (self.output / 'control_repl.jsonl').open('a') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')

        cases = [
            ('complete', 'True', ['exact True.intro'], 'accepted'),
            ('incomplete', 'True ∧ True', ['constructor'], 'changed'),
            ('unknown_identifier', 'True', ['exact missing_fixture'], 'rejected'),
            ('type_mismatch', 'True', ['exact (0 : Nat)'], 'rejected'),
            ('malformed_syntax', 'True', ['exact ('], 'rejected'),
            ('truncated_fence', 'True', ['```lean\nexact True.intro'], 'format_error'),
            ('unchanged_valid_tactic', 'True', ['skip'], 'repeated_state'),
            ('repair', 'True', ['exact missing_fixture', 'exact True.intro'], 'accepted'),
            ('indirect_sorry', 'True', ['have h : True := sorryAx _ false\nexact h'], 'replay_rejected'),
            ('unauthorized_axiom', '(true : Bool) = Lean.reduceBool true',
             ['exact Lean.ofReduceBool true (Lean.reduceBool true) rfl'], 'replay_rejected'),
        ]
        with patch.object(self.rl.Repl, 'send', traced_send):
            for case, target, actions, expected in cases:
                with self.subTest(case=case):
                    current['case'] = case
                    env = self.episode('theorem tactic_fixture : ' + target)
                    env.reward_mode = 'error_feedback_v1'
                    env.max_repair_attempts = int(case == 'repair')
                    try:
                        env.reset()
                        # One physical process, original HEADER env for replay.
                        self.ctx.local.__dict__.update(repl=env.repl, env=env.env, uses=0)
                        self.ctx.pool_repls.add(env.repl)
                        for index, action in enumerate(actions):
                            before_state, before_path = env.state, list(env.path)
                            before_calls = self.counts['lean_calls']
                            result = env.step(action, 0)
                            record = dict(case=case, action_index=index, tactic=action,
                                expected_final_status=expected, result=result, goals=env.goals,
                                proof_path=env.path, unchanged_state=env.state == before_state,
                                physical_lean_calls=self.counts['lean_calls'] - before_calls)
                            with (self.output / 'controls.jsonl').open('a') as stream:
                                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                            if case == 'repair' and index == 0:
                                self.assertFalse(result['done'], result)
                                self.assertEqual(env.state, before_state)
                                self.assertEqual(env.path, before_path)
                            if case == 'truncated_fence':
                                self.assertEqual(self.counts['lean_calls'], before_calls)
                            if case in ('unknown_identifier', 'type_mismatch', 'malformed_syntax'):
                                self.assertEqual(env.state, before_state)
                                self.assertEqual(env.path, before_path)
                        self.assertEqual(result['status'], expected, result)
                        self.assertEqual(result['accepted'], expected == 'accepted', result)
                        if case == 'unknown_identifier':
                            self.assertEqual(result['reward_component'], 'unresolved_name')
                        if case == 'type_mismatch':
                            self.assertEqual(result['reward_component'], 'name_or_type_resolution')
                        if case == 'malformed_syntax':
                            self.assertEqual(result['reward_component'], 'syntax_or_structure')
                        if case == 'incomplete':
                            self.assertFalse(result['done'])
                            self.assertEqual(result['reward'], 0)
                        if case == 'repair':
                            self.assertNotIn('missing_fixture', '\n'.join(env.path))
                        if case in ('indirect_sorry', 'unauthorized_axiom'):
                            dependency = 'sorryAx' if case == 'indirect_sorry' else 'Lean.ofReduceBool'
                            raw = [json.loads(line) for line in
                                   (self.output / 'control_repl.jsonl').read_text().splitlines()]
                            reports = [message['data'] for row in raw if row['case'] == case
                                       and '#print axioms' in row['request'].get('cmd', '')
                                       for message in row.get('response', {}).get('messages', [])
                                       if message['severity'] == 'info' and 'depends on axioms:' in message['data']]
                            self.assertTrue(any(dependency in report for report in reports), reports)
                    finally:
                        self.ctx.pool_repls.discard(env.repl)
                        self.ctx.local.__dict__.clear()
                        env.close()

    def test_real_reserved_dev_prefix_fixtures(self):
        """Authored assisted DEV states, not evidence of policy competence."""
        fixtures = [
            dict(name='burden_dev_dvd',
                 header='theorem burden_dev_dvd (a b c : Nat) (hab : a ∣ b) (hbc : b ∣ c) : a ∣ c ∧ a ∣ b',
                 given='  have hac : a ∣ c := dvd_trans hab hbc\n',
                 suffix='exact ⟨hac, hab⟩'),
            dict(name='burden_dev_le',
                 header='theorem burden_dev_le (a b : Nat) (hab : a ≤ b) (hba : b ≤ a) : a = b ∧ b = a',
                 given='  have heq : a = b := Nat.le_antisymm hab hba\n',
                 suffix='exact ⟨heq, heq.symm⟩'),
        ]
        cards = ['dvd_trans', 'Nat.dvd_mul_right', 'Nat.dvd_add', 'Nat.add_mod', 'Nat.mul_mod',
                 'Nat.gcd_dvd_left', 'Nat.Prime.ne_zero', 'Nat.Prime.two_le',
                 'Nat.le_antisymm', 'And.intro', 'Nat.add_comm', 'Nat.mul_comm', 'Nat.le_refl',
                 'Int.ModEq.trans', 'Int.ModEq.mul']
        target_names = (self.root / 'monday_recovery_20261011_v2/names.txt').read_text().split()
        target_names += [fixture['name'] for fixture in fixtures]
        for fixture in fixtures:
            task = dict(fixture, task_id='reserved-dev:' + fixture['name'], kind='prefix_top')
            env = self.rl.LeanEpisode(self.ctx, task, self.output, self.counts,
                                      reward_mode='error_feedback_v1')
            try:
                state = env.reset()
                # Check cards in the imported environment before the target exists.
                absent = env.send({'cmd': '#check ' + fixture['name'], 'env': env.env}, 20)
                self.assertTrue(self.rl.repl_output(absent)[0], absent)
                target_checks = []
                for name in target_names:
                    response = env.send({'cmd': '#check ' + name, 'env': env.env}, 20)
                    target_checks.append(dict(name=name, response=response))
                    self.assertIn('unknown identifier', self.rl.repl_output(response)[1], response)
                card_results = []
                for name in cards:
                    response = env.send({'cmd': '#check ' + name, 'env': env.env}, 20)
                    card_results.append(dict(name=name, response=response,
                        available=not bool(self.rl.repl_output(response)[0])))
                self.ctx.local.__dict__.update(repl=env.repl, env=env.env, uses=0)
                self.ctx.pool_repls.add(env.repl)
                final = env.step(fixture['suffix'], 0)
                record = dict(fixture, state=state, goals=env.root['goal'], final=final,
                    cards=card_results, target_absence_response=absent, target_absence_checks=target_checks,
                    source='authored synthetic DEV; reference prefix assistance',
                    training_admission=False,
                    dependency_boundary='Cards resolve in original imported environment before target declaration')
                with (self.output / 'dev_fixtures.jsonl').open('a') as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                self.assertTrue(final['accepted'], final)
                self.assertEqual(final['replay']['status'], 'accepted')
            finally:
                self.ctx.pool_repls.discard(env.repl)
                self.ctx.local.__dict__.clear()
                env.close()

    def test_variable_length_estimator_matches_analytic_gradient(self):
        p, bonus = 0.6, 0.025
        episodes = [([0], [0], 1 - p), ([1, 0], [bonus, 0], p * (1 - p)),
                    ([1, 1], [bonus, 1], p * p)]
        estimated = 0.0
        for actions_a, rewards_a, prob_a in episodes:
            for actions_b, rewards_b, prob_b in episodes:
                adv = self.rl.advantages([rewards_a, rewards_b])
                gradient = sum(value * (action - p) for actions, values in
                               zip((actions_a, actions_b), adv) for action, value in zip(actions, values)) / 2
                estimated += prob_a * prob_b * gradient
        exact = p * (1 - p) * (bonus + 2 * p)
        self.assertAlmostEqual(estimated, exact, places=12)

    def test_full_trainer_command_records_actual_model_rollouts(self):
        repo = Path(__file__).resolve().parents[1]
        config = json.loads((repo / 'configs/rl/qwen_05b_tactic_rl_pilot.json').read_text())
        config.update(run_id='exp019-tactic-cpu-command-s20261011-v1', max_groups=1,
                      group_size=2, max_episode_steps=2, max_action_tokens=8,
                      max_wall_seconds=180, max_generated_tokens=32, max_lean_calls=14)
        config_path = self.output / 'smoke_config.json'
        config_path.write_text(json.dumps(config))
        excluded = set((self.root / 'nt_splits_v3/dev.txt').read_text().split())
        chosen = {}
        with (self.root / 'nt_rl_tasks_v3/tasks.jsonl').open() as source:
            for line in source:
                row = json.loads(line)
                if (row['kind'] == 'whole' or row['kind'] == 'prefix_top' and row['lines_remaining'] <= 3) and row['name'] not in excluded:
                    chosen.setdefault(row['kind'], row)
                if len(chosen) == 2:
                    break
        tasks = self.output / 'smoke_tasks.jsonl'
        # Strip reference text from the smoke inputs. The policy never receives it.
        with tasks.open('w') as target:
            for row in chosen.values():
                target.write(json.dumps({k: row[k] for k in
                    ('task_id', 'name', 'kind', 'header', 'given', 'state', 'lines_remaining') if k in row}) + '\n')
        out = self.output / 'trainer_command'
        command = [sys.executable, str(repo / 'scripts/train/run_tactic_rl.py')]
        arguments = dict(config=config_path, model=self.root / 'sft_checkpoints/v2_final',
            pool=self.root / 'nt_pool_v2/pool.jsonl', tasks=tasks,
            exclude_names=self.root / 'nt_splits_v3/dev.txt',
            checker=self.root / 'proof_replay_v1/code/checker.py', environment=self.root / 'lean_env_full',
            mathlib_identity=self.root / 'interleaved_training_stage_20261010_v3/mathlib_source_identity.json',
            repl=self.root / 'rlvr_verifier/src/repl/.lake/build/bin/repl', output=out)
        for key, value in arguments.items():
            command.extend(['--' + key.replace('_', '-'), str(value)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=240)
        (self.output / 'trainer_command.log').write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = json.loads((out / 'run.json').read_text())
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['counts']['groups'], 1)
        self.assertGreater(run['counts']['generated_tokens'], 0)
        with (out / 'rollouts.jsonl').open() as source:
            rows = [json.loads(line) for line in source]
        self.assertTrue(rows)
        self.assertEqual({row['sample'] for row in rows}, {0, 1})
        names = self.output / 'smoke_dev_names.txt'
        names.write_text(sorted(excluded)[0] + '\n')
        evaluation_config = self.output / 'smoke_eval_config.json'
        evaluation_config.write_text(json.dumps(dict(config, run_id='exp019-tactic-dev-command-s20261011-v1')))
        evaluation_arguments = dict(arguments, config=evaluation_config, evaluation_names=names,
                                    output=self.output / 'trainer_evaluation')
        del evaluation_arguments['tasks']
        command = [sys.executable, str(repo / 'scripts/train/run_tactic_rl.py')]
        for key, value in evaluation_arguments.items():
            command.extend(['--' + key.replace('_', '-'), str(value)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=240)
        (self.output / 'trainer_evaluation.log').write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = json.loads((evaluation_arguments['output'] / 'run.json').read_text())
        self.assertEqual(run['mode'], 'DEV_evaluation')
        self.assertEqual(run['population'], [sorted(excluded)[0]])
        self.assertEqual(run['counts']['optimizer_updates'], 0)
        self.assertFalse((evaluation_arguments['output'] / 'final').exists())


if __name__ == '__main__':
    unittest.main()
