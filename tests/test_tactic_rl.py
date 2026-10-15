import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace


PATH = Path(__file__).resolve().parents[1] / 'scripts/train/run_tactic_rl.py'


class TacticRLTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if PATH.exists():
            spec = importlib.util.spec_from_file_location('tactic_rl', PATH)
            cls.rl = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.rl)

    def setUp(self):
        self.assertTrue(PATH.exists(), 'Interactive tactic trainer is missing')

    def test_changed_goals_get_bounded_credit_even_when_goal_count_increases(self):
        seen = {self.rl.state_key(['⊢ P ∧ Q'])}
        response = {'proofState': 2, 'goals': ['⊢ P', '⊢ Q']}
        result = self.rl.transition(response, seen, 0.025)
        self.assertEqual(result['status'], 'changed')
        self.assertEqual(result['reward'], 0.025)
        self.assertFalse(result['done'])

    def test_no_op_and_repeated_states_end_without_credit(self):
        seen = {self.rl.state_key(['n : Nat\n⊢ n = n'])}
        response = {'proofState': 2, 'goals': [' n : Nat\n\n⊢ n = n ' ]}
        result = self.rl.transition(response, seen, 0.025)
        self.assertEqual(result['status'], 'repeated_state')
        self.assertEqual(result['reward'], 0)
        self.assertTrue(result['done'])

    def test_closed_goals_are_not_a_verified_proof(self):
        result = self.rl.transition({'proofState': 2, 'goals': []}, set(), 0.025)
        self.assertEqual(result['status'], 'needs_replay')
        self.assertEqual(result['reward'], 0)
        self.assertFalse(result['accepted'])
        self.assertEqual(self.rl.finish(result, {'status': 'rejected'})['reward'], 0)
        verified = self.rl.finish(result, {'status': 'accepted'})
        self.assertEqual(verified['reward'], 1)
        self.assertTrue(verified['accepted'])

    def test_errors_sorries_and_missing_state_never_get_credit(self):
        for response in ({'proofState': 2, 'goals': ['⊢ True'], 'messages': [
                {'severity': 'error', 'data': 'bad tactic'}]},
                {'proofState': 2, 'goals': [], 'sorries': [{'proofState': 3}]},
                {'message': 'Lean error:\nbad tactic'}, {'proofState': 2}):
            with self.subTest(response=response):
                self.assertEqual(self.rl.transition(response, set(), 0.025)['reward'], 0)

    def test_future_rewards_credit_prior_actions_without_self_in_baseline(self):
        rewards = [[0.025, 1], [0]]
        self.assertEqual(self.rl.advantages(rewards), [[1.025, 1], [-1.025]])
        self.assertEqual(self.rl.advantages([[0], [0]]), [[0], [0]])

    def test_large_next_state_terminates_episode_without_discarding_sample(self):
        self.assertTrue(hasattr(self.rl, 'bound_state'), 'State cap handling is missing')
        result = dict(status='changed', reward=0.025, done=False, accepted=False)
        capped = self.rl.bound_state(result, 1025, 1024)
        self.assertTrue(capped['done'])
        self.assertEqual(capped['status'], 'prompt_cap')
        self.assertEqual(capped['reward'], 0.025)
        self.assertEqual(self.rl.advantages([[capped['reward']], [0]]), [[0.025], [-0.025]])

    def test_interrupted_update_cannot_export_an_unknown_checkpoint(self):
        self.assertTrue(hasattr(self.rl, 'can_export'), 'Checkpoint export guard is missing')
        self.assertFalse(self.rl.can_export(dict(status='capped', phase='update'), 1))
        self.assertTrue(self.rl.can_export(dict(status='capped', phase='sampling'), 1))
        self.assertFalse(self.rl.can_export(dict(status='completed', phase='idle'), 0))

    def test_multiline_block_has_one_outer_tactic_and_preserves_indentation(self):
        self.assertTrue(hasattr(self.rl, 'tactic_block'), 'REPL action serialization is missing')
        raw = '  have h : True := by\n    trivial\n  exact h'
        self.assertEqual(self.rl.tactic_block(raw), 'first |\n  have h : True := by\n    trivial\n  exact h')

    def test_portable_mathlib_uses_source_receipt_and_rejects_mismatches(self):
        self.assertTrue(hasattr(self.rl, 'mathlib_identity'), 'Portable source identity handling is missing')
        with TemporaryDirectory() as directory:
            p = Path(directory) / 'identity.json'
            p.write_text(json.dumps(dict(mathlib_revision='pin', mismatches=[], source='primary-source')))
            self.assertEqual(self.rl.mathlib_identity(p, 'pin')['mathlib_revision'], 'pin')
            with self.assertRaises(ValueError):
                self.rl.mathlib_identity(p, 'other')
            p.write_text(json.dumps(dict(mathlib_revision='pin', mismatches=['changed.lean'], source='primary-source')))
            with self.assertRaises(ValueError):
                self.rl.mathlib_identity(p, 'pin')

    def test_train_split_and_exclusions_are_enforced_before_sampling(self):
        pool = [{'name': 'p', 'split': 'train', 'formal_statement': 'theorem p : True := by sorry'}]
        task = dict(task_id='p:whole', name='p', header='theorem p : True', kind='whole',
                    given='', state='⊢ True')
        self.assertEqual(len(self.rl.training_tasks([task], pool, set(), 3)), 1)
        with self.assertRaisesRegex(ValueError, 'excluded'):
            self.rl.training_tasks([task], pool, {'p'}, 3)
        with self.assertRaises(RuntimeError):
            self.rl.training_tasks([task], [dict(pool[0], split='validation')], set(), 3)
        nested = dict(task, task_id='p:nested', kind='prefix_have', given='  have : True := by\n')
        self.assertEqual(self.rl.training_tasks([nested], pool, set(), 3), [])

    def test_native_bank_admission_strips_reference_suffix_and_rejects_leaks(self):
        self.assertTrue(hasattr(self.rl, 'bank_tasks'), 'Native bank admission is missing')
        row = dict(theorem_id='t', full_name='N.p', file_path='N.lean', official_split='train',
                   declaration_header='theorem p : True')
        unit = dict(task_id='t:k1', theorem_id='t', full_name='N.p', file_path='N.lean',
                    header=row['declaration_header'], k=1, steps_total=2, steps_remaining=1,
                    given_steps=['  have h : True := True.intro'], state='h : True\n⊢ True',
                    reference_rest=['  exact h'], top_priors=['hidden_answer_hint'])
        tasks = self.rl.bank_tasks([unit], [row], set(), {})
        self.assertEqual(tasks[0]['name'], 'N.p')
        self.assertEqual(tasks[0]['kind'], 'bank')
        self.assertNotIn('reference_rest', tasks[0])
        self.assertNotIn('top_priors', tasks[0])
        for bank, rows, excluded, evaluation in (
                ([unit], [dict(row, official_split='val')], set(), {}),
                ([unit], [], set(), {}),
                ([unit], [row], {'N.lean'}, {}),
                ([unit], [row], set(), {'N.lean': [(1, 2, 'theorem q : True')]}),
                ([dict(unit, header='theorem q : True')], [row], set(), {}),
                ([dict(unit, given_steps=['  sorry'])], [row], set(), {}),
                ([dict(unit, k=0)], [row], set(), {}),
                ([unit, unit], [row], set(), {})):
            with self.subTest(bank=bank, excluded=excluded):
                with self.assertRaises(ValueError):
                    self.rl.bank_tasks(bank, rows, excluded, evaluation)

    def test_native_bank_reset_preserves_scoped_prefix_and_replays_bank_unit(self):
        self.assertTrue(hasattr(self.rl, 'question'), 'Native file-context reset is missing')
        from bench_lean_verifier import split_prefix
        task = dict(task_id='t:k1', theorem_id='t', name='N.p', kind='bank',
                    header='theorem p : True ∧ True', given_steps=['  constructor'])
        q = dict(prefix='import Lean.Elab.Tactic\nnamespace N\nopen Nat in\n',
                 header=task['header'], name='N.p', closing=['end N'])
        commands = []

        class Repl:
            def __init__(self, *_):
                pass

            def send(self, request, _):
                commands.append(request)
                if len(commands) == 1:
                    return {'env': 7}, 0, 0
                if len(commands) == 2:
                    return {'env': 8, 'sorries': [{'proofState': 9, 'goal': '⊢ True ∧ True'}]}, 0, 0
                return {'proofState': 10, 'goals': ['case left\n⊢ True', 'case right\n⊢ True']}, 0, 0

        def source_for(query, body):
            return query['prefix'] + query['header'] + ' := by\n  guard\n' + body + '\n' + '\n'.join(query['closing']) + '\n'

        ctx = SimpleNamespace(checker=SimpleNamespace(source_for=source_for), rows={'t': {}},
                              environment=Path('/unused'), sources={}, eval_spans={}, setup_timeout=1, timeout=1)
        with TemporaryDirectory() as directory, patch.object(self.rl, 'Repl', Repl), \
                patch.object(self.rl, 'question', return_value=(q, None)):
            env = self.rl.LeanEpisode(ctx, task, Path(directory),
                                     dict(lean_calls=0, lean_seconds=0, lean_cpu_seconds=0))
            state = env.reset()
            self.assertNotIn('reference_rest', state)
            head, _ = split_prefix(q['prefix'])
            self.assertEqual(commands[0], {'cmd': head})
            self.assertEqual(commands[1], {'cmd': source_for(q, '  sorry\n')[len(head):], 'env': 7})
            self.assertEqual(commands[2], {'tactic': self.rl.tactic_block('  constructor'), 'proofState': 9})
            self.assertEqual(env.goals, ['case left\n⊢ True', 'case right\n⊢ True'])
            self.assertEqual(env.state, 10)
            self.assertEqual(env.reset(), state)
            self.assertEqual(len(commands), 3)
            with patch.object(env, 'send', return_value={'proofState': 10, 'goals': []}), \
                    patch.object(self.rl, 'verify_group', return_value=[dict(status='accepted', lean_calls=1,
                                 lean_seconds=0, lean_cpu_seconds=0)]) as replay:
                self.assertEqual(env.step('exact h', 0)['reward'], 1)
                self.assertEqual(replay.call_args.args[1], task)

    def test_evaluation_uses_dev_statements_without_proof_prefixes(self):
        self.assertTrue(hasattr(self.rl, 'evaluation_tasks'), 'Interactive DEV evaluation is missing')
        pool = [{'name': 'p', 'split': 'train', 'formal_statement': 'theorem p : True := by sorry'}]
        tasks = self.rl.evaluation_tasks(pool, {'p'}, {'p'})
        self.assertEqual(tasks[0]['given'], '')
        self.assertEqual(tasks[0]['header'], 'theorem p : True')
        self.assertEqual(tasks[0]['kind'], 'whole')
        with self.assertRaises(ValueError):
            self.rl.evaluation_tasks(pool, {'p'}, set())
        with self.assertRaises(ValueError):
            self.rl.evaluation_tasks(pool, {'missing'}, {'missing'})

    def test_explicit_schedule_preserves_repeats_and_rejects_unknown_roots(self):
        self.assertTrue(hasattr(self.rl, 'scheduled_tasks'), 'Explicit task schedules are missing')
        tasks = [dict(task_id='a'), dict(task_id='b')]
        self.assertEqual([t['task_id'] for t in self.rl.scheduled_tasks(tasks, ['b', 'a', 'b'], 3)],
                         ['b', 'a', 'b'])
        with self.assertRaisesRegex(ValueError, 'unknown'):
            self.rl.scheduled_tasks(tasks, ['other'], 1)
        with self.assertRaisesRegex(ValueError, 'max_groups'):
            self.rl.scheduled_tasks(tasks, ['a'], 2)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.rl.scheduled_tasks(tasks + [dict(task_id='a')], ['a'], 1)

    def test_training_state_assessment_is_distinct_from_dev_and_training(self):
        self.assertTrue(hasattr(self.rl, 'execution_mode'), 'Frozen training-state assessment is missing')
        self.assertEqual(self.rl.execution_mode(False, False), 'training')
        self.assertEqual(self.rl.execution_mode(False, True), 'training_state_assessment')
        self.assertEqual(self.rl.execution_mode(True, False), 'DEV_evaluation')
        with self.assertRaisesRegex(ValueError, 'DEV'):
            self.rl.execution_mode(True, True)

    def test_forbidden_tactic_is_rejected_without_calling_lean(self):
        with TemporaryDirectory() as directory:
            env = self.rl.LeanEpisode(None, dict(task_id='p', name='p', header='theorem p : False',
                                                given=''), Path(directory), None)
            result = env.step('sorry', 0.025)
            self.assertEqual(result['status'], 'forbidden_construct')
            self.assertEqual(result['reward'], 0)

    def test_one_repair_keeps_verified_state_and_preserves_error_reward(self):
        with TemporaryDirectory() as directory:
            env = self.rl.LeanEpisode(None, {}, Path(directory), None,
                reward_mode='error_feedback_v1', max_repair_attempts=1)
            env.state, env.goals, env.path = 7, ['⊢ True'], []
            env._step = lambda *_: dict(status='rejected', reward=0, accepted=False,
                                       done=True, error="error: unknown identifier 'missing'")
            result = env.step('exact missing', 0)
            self.assertFalse(result['done'])
            self.assertEqual(result['reward'], -0.2)
            self.assertEqual(env.state, 7)
            self.assertEqual(env.path, [])
            self.assertIn("unknown identifier 'missing'", env.prompt_state())
            self.assertTrue(env.step('exact missing', 0)['done'])

    def test_repair_cannot_retry_forbidden_or_infrastructure_failures(self):
        with TemporaryDirectory() as directory:
            env = self.rl.LeanEpisode(None, {}, Path(directory), None, max_repair_attempts=1)
            for status in ('forbidden_construct', 'timeout', 'crash', 'replay_rejected', 'repeated_state'):
                env._step = lambda *_, status=status: dict(status=status, reward=0, accepted=False, done=True)
                self.assertTrue(env.step('tactic', 0)['done'])

    def test_ambiguous_fences_and_surrounding_prose_never_reach_lean(self):
        with TemporaryDirectory() as directory:
            env = self.rl.LeanEpisode(None, {}, Path(directory), None)
            for text in ('```lean\ntrivial', '```lean\n```',
                         'Explanation\n```lean\ntrivial\n```',
                         '```lean\ntrivial\n```\nExplanation',
                         '```python\ntrivial\n```',
                         '```lean\ntrivial\n```\n```lean\ntrivial\n```'):
                with self.subTest(text=text):
                    self.assertEqual(env.step(text, 0.025)['status'], 'format_error')

    def test_fenced_forbidden_code_never_reaches_lean(self):
        with TemporaryDirectory() as directory:
            env = self.rl.LeanEpisode(None, {}, Path(directory), None)
            self.assertEqual(env.step('```lean\nsorry\n```', 0.025)['status'], 'forbidden_construct')

    def test_local_feedback_distinguishes_invalid_actions_from_inapplicable_actions(self):
        self.assertTrue(hasattr(self.rl, 'local_feedback'), 'Local verifier reward is missing')
        cases = [("unexpected token ')'; expected tactic", -0.2, 'syntax_or_structure'),
                 ("<input>:2:9: unexpected end of input; expected ')'", -0.2, 'syntax_or_structure'),
                 ("unknown identifier 'missing_fixture'", -0.2, 'unresolved_name'),
                 ('type mismatch\n  0\nexpected True', -0.1, 'name_or_type_resolution'),
                 ('tactic \'rewrite\' failed, did not find instance of the pattern', -0.1, 'proof_construction')]
        for message, reward, component in cases:
            with self.subTest(message=message):
                result = self.rl.local_feedback(dict(status='rejected', reward=0.0, accepted=False,
                                                     done=True, error='error: ' + message))
                self.assertEqual(result['reward'], reward)
                self.assertEqual(result['reward_component'], component)

    def test_known_parser_errors_receive_syntax_penalty_and_opaque_errors_stay_unknown(self):
        for message in ('expected term', 'expected token', 'tabs are not allowed'):
            with self.subTest(message=message):
                result = self.rl.local_feedback(dict(status='rejected', accepted=False, done=True,
                                                     error='error: <input>:2:9: ' + message))
                self.assertEqual(result['reward'], -0.2)
                self.assertEqual(result['reward_component'], 'syntax_or_structure')
        opaque = self.rl.local_feedback(dict(status='rejected', accepted=False, done=True,
                                            error='error: internal exception #4'))
        self.assertEqual(opaque['reward'], 0.0)
        self.assertEqual(opaque['reward_component'], 'unclassified_rejection')

    def test_local_feedback_does_not_reward_state_text_or_goal_count_changes(self):
        self.assertTrue(hasattr(self.rl, 'local_feedback'), 'Local verifier reward is missing')
        for status in ('changed', 'repeated_state', 'needs_replay', 'timeout', 'crash', 'prompt_cap'):
            result = self.rl.local_feedback(dict(status=status, reward=0.025, accepted=False, done=True))
            self.assertEqual(result['reward'], 0.0)
        unknown = self.rl.local_feedback(dict(status='rejected', reward=0.0, accepted=False,
                                             done=True, error='error: unrecognized diagnostic'))
        self.assertEqual(unknown['reward'], 0.0)

    def test_local_feedback_requires_complete_proof_acceptance(self):
        self.assertTrue(hasattr(self.rl, 'local_feedback'), 'Local verifier reward is missing')
        result = self.rl.local_feedback(dict(status='accepted', reward=1.0, accepted=True, done=True))
        self.assertEqual(result['reward'], 1.0)
        failed = self.rl.local_feedback(dict(status='replay_rejected', reward=0.0, accepted=False, done=True))
        self.assertEqual(failed['reward'], -0.1)
        for status in ('replay_timeout', 'replay_crash'):
            result = self.rl.local_feedback(dict(status=status, reward=0.0, accepted=False, done=True))
            self.assertEqual(result['reward'], 0.0)

    def test_feedback_mode_penalizes_forbidden_and_malformed_actions_before_lean(self):
        self.assertTrue(hasattr(self.rl, 'local_feedback'), 'Local verifier reward is missing')
        with TemporaryDirectory() as directory:
            env = self.rl.LeanEpisode(None, {}, Path(directory), None, reward_mode='error_feedback_v1')
            for text in ('sorry', '```lean\ntrivial'):
                result = env.step(text, 0.025)
                self.assertEqual(result['reward'], -0.2)
                self.assertTrue(result['done'])

    def test_negative_feedback_provides_relative_learning_signal(self):
        self.assertEqual(self.rl.advantages([[0.0], [-0.2]]), [[0.2], [-0.2]])


class EnvironmentV2Test(unittest.TestCase):
    """v2 transitions against a scripted REPL; real Lean belongs to the integration group."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('tactic_rl_v2', PATH)
        cls.rl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.rl)

    def episode(self, directory, responses, task=None, horizon=6, **kwargs):
        task = task or dict(task_id='t', name='t', kind='whole', header='theorem t (n : ℕ) : n + 0 = n',
                            given='  intro m\n')
        counts = dict(lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0)
        env = self.rl.LeanEpisode(SimpleNamespace(timeout=1), task, Path(directory), counts,
                                  version=2, horizon=horizon, **kwargs)
        env.state, env.goals = 0, ['n : ℕ\n⊢ n + 0 = n']
        env.frames, env.shown, env.path = [(0, list(env.goals), self.rl.state_key(env.goals))], [], []
        env.steps_used, env.last_error, env.failed = 0, '', {}
        env.sent = []

        def send(request, timeout):
            tactic = request['tactic'].split('\n', 1)[1].strip().splitlines()[-1].strip()
            env.sent.append(tactic)
            reply = responses[tactic]
            if isinstance(reply, Exception):
                raise reply
            return reply
        env.send = send
        return env

    def test_observation_is_a_lean_file_with_tactic_state_and_budget(self):
        with TemporaryDirectory() as directory:
            env = self.episode(directory, {'induction n': {'proofState': 1, 'goals': ['⊢ 0 + 0 = 0']}})
            env.step('-- split on n\ninduction n', 0)
            text = env.prompt_state()
            for part in ('```lean\ntheorem t (n : ℕ) : n + 0 = n := by\n  -- supplied lines (already checked):\n  intro m\n',
                         '  -- your steps:\n  -- step 1\n  -- split on n\n  induction n\n',
                         '  /- tactic state:\n  ⊢ 0 + 0 = 0\n  -/', '/- actions remaining: 5 -/', 'up to 4 tactics'):
                self.assertIn(part, text)
            self.assertNotIn('backtrack', text)
            self.assertIn('(steps 1-1 omitted)', env.observation(1))
            self.assertNotIn('induction n', env.observation(1))
            policy = self.episode(directory, {}, backtrack_owner='policy')
            self.assertIn('"backtrack k"', policy.prompt_state())

    def test_actions_that_apply_no_tactic_are_self_loops_that_use_a_step(self):
        error = {'proofState': 1, 'goals': [], 'messages': [{'severity': 'error', 'data': 'unknown identifier x'}]}
        same = {'proofState': 2, 'goals': ['n : ℕ\n⊢ n + 0 = n']}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, {'exact x': error, 'skip': same}, backtrack_after=10)
            for text, status in (('exact x', 'rejected'), ('skip', 'no_progress'), ('```lean\nsimp', 'format_error'),
                                 ('sorry', 'forbidden_construct'), ('-- only a comment', 'format_error'),
                                 ('backtrack 0', 'format_error')):
                with self.subTest(text=text):
                    result = env.step(text, 0)
                    self.assertEqual(result['status'], status)
                    self.assertFalse(result['done'])
                    self.assertEqual(result['reward'], 0)
                    self.assertEqual((env.state, env.shown, env.path), (0, [], []))
                    self.assertIn(status, env.prompt_state())
            self.assertEqual(env.steps_used, 6)
            self.assertIn('actions remaining: 0', env.prompt_state())
            self.assertEqual(env.sent, ['exact x', 'skip'])  # Other actions never reach Lean.

    def test_chunk_keeps_the_valid_prefix_and_stops_at_the_first_error(self):
        responses = {'induction n': {'proofState': 1, 'goals': ['⊢ 0 + 0 = 0', '⊢ n + 1 + 0 = n + 1']},
                     'simp': {'proofState': 2, 'goals': ['⊢ n + 1 + 0 = n + 1']},
                     'exact x': {'proofState': 3, 'goals': [], 'messages': [{'severity': 'error', 'data': 'bad x'}]}}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses)
            result = env.step('induction n\nsimp\nexact x\nomega', 0)
            self.assertEqual((result['status'], result['tactics_applied'], result['done']), ('partial', 2, False))
            self.assertEqual(env.shown, ['induction n', 'simp'])
            self.assertEqual(env.sent, ['induction n', 'simp', 'exact x'])
            self.assertEqual(env.state, 2)
            text = env.prompt_state()
            self.assertIn('2 tactic(s) applied, then rejected', text)
            self.assertIn('tactics that already failed here:\n  - exact x', text)
            self.assertEqual(env.step('a\nb\nc\nd\ne', 0)['status'], 'format_error')

    def test_harness_reverts_one_step_after_repeated_failures(self):
        bad = {'proofState': 9, 'goals': [], 'messages': [{'severity': 'error', 'data': 'no'}]}
        responses = {'a': {'proofState': 1, 'goals': ['⊢ P']}, 'x': bad, 'y': bad, 'z': bad}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses, backtrack_after=2)
            env.step('a', 0)
            self.assertFalse(env.step('x', 0)['harness_backtrack'])
            result = env.step('y', 0)
            self.assertTrue(result['harness_backtrack'])
            self.assertEqual((env.state, env.shown, env.path), (0, [], []))
            self.assertEqual(env.failed[env.frames[-1][2]], ['a'])
            self.assertIn('The harness reverted your last step.', env.prompt_state())
            self.assertFalse(env.step('z', 0)['harness_backtrack'])  # Root has no step to revert.
            self.assertEqual(env.failed[env.frames[-1][2]], ['a', 'z'])

    def test_policy_backtrack_arm_restores_the_verified_state(self):
        responses = {'induction n': {'proofState': 1, 'goals': ['⊢ 0 + 0 = 0', '⊢ n + 1 + 0 = n + 1']},
                     'simp': {'proofState': 2, 'goals': ['⊢ n + 1 + 0 = n + 1']},
                     'cases n': {'proofState': 3, 'goals': ['⊢ 0 + 0 = 0']}}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses, backtrack_owner='policy')
            env.step('induction n\nsimp', 0)
            self.assertEqual(env.step('backtrack 2', 0)['status'], 'invalid_backtrack')
            result = env.step('-- wrong split\nbacktrack 0', 0)
            self.assertEqual(result['status'], 'backtrack')
            self.assertEqual((env.state, env.shown, env.path, len(env.frames)), (0, [], [], 1))
            self.assertIn('backtracked to the state after step 0', env.prompt_state())
            env.step('cases n', 0)
            self.assertEqual((env.shown, env.state), (['cases n'], 3))

    def test_cycle_on_current_path_stops_the_chunk(self):
        responses = {'a': {'proofState': 1, 'goals': ['⊢ P']}, 'b': {'proofState': 2, 'goals': ['⊢ Q']},
                     'c': {'proofState': 3, 'goals': ['n : ℕ\n⊢ n + 0 = n']}}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses)
            result = env.step('a\nb\nc', 0)
            self.assertEqual((result['status'], result['tactics_applied']), ('partial', 2))
            self.assertIn('cycle', env.prompt_state())

    def test_closing_tactic_ends_the_chunk_and_only_replay_earns_reward(self):
        responses = {'simp': {'proofState': 1, 'goals': []}}
        for verdict, reward in (('accepted', 1.0), ('rejected', 0.0)):
            with TemporaryDirectory() as directory, \
                    patch.object(self.rl, 'lean_unit', lambda task: 'unit'), \
                    patch.object(self.rl, 'verify_group', lambda ctx, unit, bodies: [
                        dict(status=verdict, lean_calls=1, lean_seconds=0.1, lean_cpu_seconds=0.1, body=bodies[0])]):
                env = self.episode(directory, responses)
                result = env.step('-- close it\nsimp\nomega', 0)
                self.assertTrue(result['done'])
                self.assertEqual(result['reward'], reward)
                self.assertEqual(env.sent, ['simp'])
                self.assertEqual(result['replay']['body'], '  first |\n    -- close it\n    simp\n')

    def test_timeout_ends_the_episode_and_a_crash_censors_it(self):
        with TemporaryDirectory() as directory:
            env = self.episode(directory, {'slow': TimeoutError(), 'boom': EOFError()})
            timeout = env.step('slow', 0)
            self.assertEqual((timeout['status'], timeout['done'], timeout['reward']), ('timeout', True, 0.0))
            self.assertFalse(timeout.get('censored'))
            crash = env.step('boom', 0)
            self.assertTrue(crash['done'] and crash['censored'])

    def test_bank_tasks_show_given_steps(self):
        task = dict(task_id='b', name='b', kind='bank', header='theorem b : True', given_steps=['  have h := 1', '  skip'])
        with TemporaryDirectory() as directory:
            text = self.episode(directory, {}, task).prompt_state()
            self.assertIn('theorem b : True := by\n  -- supplied lines (already checked):\n  have h := 1\n  skip\n', text)

    def test_v2_rejects_heuristic_rewards_repair_and_bad_settings(self):
        with TemporaryDirectory() as directory:
            for kwargs in (dict(reward_mode='error_feedback_v1'), dict(max_repair_attempts=1),
                           dict(backtrack_owner='search'), dict(max_tactics=0), dict(backtrack_after=0)):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    self.rl.LeanEpisode(None, {}, Path(directory), None, version=2, horizon=4, **kwargs)

    def test_discounted_advantages_prefer_shorter_proofs(self):
        short, long, failed = [1.0], [0.0, 0.0, 1.0], [0.0, 0.0]
        values = self.rl.advantages([short, long, failed], 0.95)
        # Baselines are the other trajectories' discounted returns from the start.
        self.assertAlmostEqual(values[0][0], 1 - (0.95 ** 2 + 0) / 2)
        self.assertAlmostEqual(values[1][0], 0.95 ** 2 - (1 + 0) / 2)
        self.assertAlmostEqual(values[1][2], 0.95 ** 2 - (1 + 0) / 2)
        self.assertAlmostEqual(values[2][0], 0 - (1 + 0.95 ** 2) / 2)
        self.assertGreater(values[0][0], values[1][0])
        self.assertEqual(self.rl.advantages([[0.025, 1], [0]], 1.0), [[1.025, 1], [-1.025]])

    def test_restated_header_from_sft_proof_format_is_dropped_not_executed(self):
        parse = self.rl.parse_action
        self.assertEqual(parse('```lean\ntheorem t (n : ℕ) : n + 0 = n := by\n  simp\n```'),
                         dict(kind='tactics', tactics=['simp']))
        self.assertEqual(parse('lemma t (a b : ℕ)\n    (h : a ∣ b) : a ∣ b + b := by\n  -- split\n  exact dvd_add h h')['tactics'],
                         ['-- split\nexact dvd_add h h'])
        self.assertEqual(parse('theorem t : True\n  trivial')['kind'], 'malformed')
        self.assertEqual(parse('theorem t : True := by')['kind'], 'malformed')

    def test_block_comments_attach_to_the_next_tactic(self):
        self.assertEqual(self.rl.parse_action('/- tactic state:\n⊢ P\n-/\nsimp\n/- done -/\nomega')['tactics'],
                         ['/- tactic state:\n⊢ P\n-/\nsimp', '/- done -/\nomega'])

    def test_shared_renderer_shows_every_section_even_when_empty(self):
        task = dict(kind='whole', header='theorem t : True := by', given='')
        text = self.rl.render_observation(task, [], ['⊢ True'], '', [], 3)
        for part in ('theorem t : True := by\n  -- supplied lines (already checked):\n  -- (none)\n  -- your steps:\n  -- (none)',
                     '/- last action result:\n  none\n  -/', '/- tactics that already failed here:\n  none\n  -/',
                     '/- actions remaining: 3 -/'):
            self.assertIn(part, text)
        with TemporaryDirectory() as directory:
            env = self.episode(directory, {}, task, horizon=3)
            env.goals = ['⊢ True']
            self.assertEqual(env.prompt_state(), text)

    def test_discounted_equal_returns_give_zero_advantage(self):
        for gamma in (1.0, 0.95):
            for rows in ([[0, 0, 1]] * 3, [[0, 0]] * 4, [[1]] * 2):
                with self.subTest(gamma=gamma, rows=rows):
                    self.assertTrue(all(v == 0 for row in self.rl.advantages(rows, gamma) for v in row))

    def test_terminal_reward_gives_one_advantage_per_trajectory(self):
        values = self.rl.advantages([[0, 0, 1], [1], [0, 0]], 0.9)
        self.assertTrue(all(abs(v - values[0][0]) < 1e-12 for v in values[0]))
        self.assertAlmostEqual(values[0][0], 0.81 - (1 + 0) / 2)
        self.assertAlmostEqual(values[1][0], 1 - (0.81 + 0) / 2)

    def test_config_validation_runs_before_any_output(self):
        base = json.loads((PATH.parents[2] / 'configs/eval/qwen_05b_curriculum_stage_probe_v21prompt.json').read_text())
        self.rl.validate_config(base)
        v1 = json.loads((PATH.parents[2] / 'configs/eval/qwen_05b_curriculum_stage_probe_v1prompt.json').read_text())
        self.rl.validate_config(v1)
        self.rl.validate_config(dict(base, checkpoint_keep=[75], task_sampler='band', band_cap_ratio=1.5, gradient_checkpointing=True))
        bad = [dict(base, max_chunk_tactics=0), dict(base, backtrack_owner='search'), dict(base, checkpoint_every=-1),
               {k: v for k, v in base.items() if k != 'discount'}, dict(v1, discount=0.95),
               {k: v for k, v in base.items() if k != 'max_lean_calls'},
               dict(base, checkpoint_keep=[0]), dict(base, checkpoint_keep=5), dict(base, task_sampler='random'),
               dict(base, band_cap_ratio=0.5), dict(base, gradient_checkpointing='yes')]
        for config in bad:
            with self.subTest(config={k: config.get(k) for k in ('max_chunk_tactics', 'backtrack_owner', 'discount')}):
                with self.assertRaises(ValueError):
                    self.rl.validate_config(config)

    def test_lean_reservation_counts_full_chunks_and_replays(self):
        self.assertEqual(self.rl.lean_reservation(8, 6, 4, 5, 2), 8 * (24 + 5 + 2))
        self.assertEqual(self.rl.lean_reservation(8, 6, 1, 5, 0), 88)

    def test_trajectory_seed_is_reproducible_and_distinct(self):
        seed = self.rl.trajectory_seed
        self.assertEqual(seed(1, 't:whole:0', 0, 3), seed(1, 't:whole:0', 0, 3))
        self.assertEqual(len({seed(1, 't:whole:0', v, s) for v in range(3) for s in range(8)}), 24)

    def test_action_parser_splits_top_level_tactics(self):
        parse = self.rl.parse_action
        self.assertEqual(parse('-- why\nomega'), dict(kind='tactics', tactics=['-- why\nomega']))
        self.assertEqual(parse('constructor\n· simp\n· omega')['tactics'], ['constructor', '· simp', '· omega'])
        self.assertEqual(parse('  have h : p := by\n    simp\n  -- use it\n  exact h')['tactics'],
                         ['have h : p := by\n  simp', '-- use it\nexact h'])
        self.assertEqual(parse('-- a\n  simp\n  omega')['tactics'], ['-- a\nsimp', 'omega'])
        self.assertEqual(parse('```lean\n-- undo\nbacktrack 0\n```', allow_backtrack=True), dict(kind='backtrack', step=0))
        self.assertEqual(parse('backtrack 2')['kind'], 'malformed')
        for text in ('', '-- note', 'Text\n```lean\nsimp\n```', '```lean\nsimp\n```\n```lean\nsimp\n```'):
            with self.subTest(text=text):
                self.assertEqual(parse(text)['kind'], 'malformed')


    # Environment audit, 13 October 2026. Each test below failed on the unaudited environment.

    def test_continuation_lines_stay_with_their_tactic(self):
        parse = self.rl.parse_action
        for text in ('induction n with\n| zero => simp\n| succ k ih => simp',
                     'constructor\n<;> simp',
                     'if p then\n  simp\nelse\n  omega',
                     'calc a = b := h1\n_ = c := h2',
                     'exact ⟨h1,\nh2\n⟩',
                     'induction n\nwith\n| zero => simp'):
            with self.subTest(text=text):
                self.assertEqual(parse(text + '\nomega')['tactics'], [text, 'omega'])
        self.assertEqual(parse('-- zero\n| zero => simp')['tactics'], ['-- zero\n| zero => simp'])
        # Bullets, case tags, next and a semicolon chain are separate top-level tactics or one line.
        self.assertEqual(parse('case h => simp\nnext => omega\nintro x; simp')['tactics'],
                         ['case h => simp', 'next => omega', 'intro x; simp'])

    def test_block_comment_followed_by_code_is_a_tactic(self):
        parse = self.rl.parse_action
        self.assertEqual(parse('/- split -/ constructor\nsimp')['tactics'], ['/- split -/ constructor', 'simp'])
        self.assertEqual(parse('/- one\ntwo -/ constructor\nsimp')['tactics'], ['/- one\ntwo -/ constructor', 'simp'])
        # A comment that opens after code and closes on a shallower line stays with that tactic.
        self.assertEqual(parse('simp /- start\nmore -/\nomega')['tactics'], ['simp /- start\nmore -/', 'omega'])

    def test_comment_markers_inside_strings_chars_and_raw_strings_are_not_comments(self):
        parse = self.rl.parse_action
        for line in ('trace "a -- b"', "have h := '\"'", 'have h := r"\\"', 'have h := r#"a "-- b"#', 'trace "/-"'):
            with self.subTest(line=line):
                self.assertEqual(parse(line + '\nomega')['tactics'], [line, 'omega'])

    def test_leading_tabs_become_spaces_because_lean_rejects_tabs(self):
        parse = self.rl.parse_action
        self.assertEqual(parse('refine ⟨?_, ?_⟩\n\texact a')['tactics'], ['refine ⟨?_, ?_⟩\n  exact a'])
        self.assertEqual(parse('\thave h : p := by\n\t\tsimp\n\texact h')['tactics'],
                         ['have h : p := by\n  simp', 'exact h'])
        for tactic in parse('a\n\tb\n\t\tc')['tactics']:
            self.assertNotIn('\t', tactic)

    def test_fence_labels(self):
        parse = self.rl.parse_action
        for label in ('lean', 'lean4', 'Lean', 'LEAN4', 'lean '):
            with self.subTest(label=label):
                self.assertEqual(parse(f'```{label}\nsimp\n```')['tactics'], ['simp'])
        for text in ('```\nsimp\n```', '```bash\nsimp\n```', '```tonight\nsimp\n```', '```lean5\nsimp\n```',
                     '```lean4x\nsimp\n```', '```lean\nsimp', '```lean\n```lean\nsimp\n```\n```',
                     '```lean\nsimp\n```\nDone.', '```lean\n\n```'):
            with self.subTest(text=text):
                self.assertEqual(parse(text)['kind'], 'malformed')
        self.assertEqual(parse('```lean\r\nsimp\r\nomega\r\n```')['tactics'], ['simp', 'omega'])

    def test_forbidden_filter_ignores_comments_and_sees_code_after_char_literals(self):
        forbidden = self.rl.forbidden
        for text in ('simp -- no sorry needed', '/- native_decide, exact? -/ simp', '/- a /- sorry -/ sorry -/ decide',
                     '-- set_option\ndecide', 'decide', 'simp [Nat.add_comm]'):
            with self.subTest(text=text):
                self.assertFalse(forbidden(text))
        for text in ('sorry', 'native_decide', 'exact?', 'set_option maxRecDepth 9 in\nsimp', '#eval 1', 'observe h : p',
                     '/- note -/ sorry',
                     # A char literal and a raw string make an ordinary-string scanner treat code as a comment.
                     "have h := ('\"', \"--\"); sorry", "have h := ('\"', \"--\"); run_tac pure ()",
                     'have h := (r"\\", "--"); sorry'):
            with self.subTest(text=text):
                self.assertTrue(forbidden(text))

    def test_char_literal_bypass_never_reaches_lean(self):
        with TemporaryDirectory() as directory:
            env = self.episode(directory, {})
            result = env.step("have h := ('\"', \"--\"); run_tac pure ()", 0)
            self.assertEqual(result['status'], 'forbidden_construct')
            self.assertEqual(env.sent, [])

    def test_harness_revert_note_survives_a_long_lean_error(self):
        bad = {'proofState': 9, 'goals': [], 'messages': [{'severity': 'error', 'data': 'e' * 2000}]}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, {'a': {'proofState': 1, 'goals': ['⊢ P']}, 'x': bad, 'y': bad},
                               backtrack_after=2)
            env.step('a', 0)
            env.step('x', 0)
            result = env.step('y', 0)
            self.assertTrue(result['harness_backtrack'])
            self.assertIn('The harness reverted your last step.', env.last_error)
            self.assertLessEqual(len(env.last_error), 600)
            self.assertTrue(result['error'].endswith('The harness reverted your last step.'))

    def test_partial_result_keeps_the_revert_note_and_the_cap(self):
        bad = {'proofState': 9, 'goals': [], 'messages': [{'severity': 'error', 'data': 'e' * 2000}]}
        responses = {'a': {'proofState': 1, 'goals': ['⊢ P']}, 'b': {'proofState': 2, 'goals': ['⊢ Q']}, 'x': bad}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses, backtrack_after=1)
            env.step('a', 0)
            result = env.step('b\nx', 0)
            self.assertEqual((result['status'], result['harness_backtrack']), ('partial', True))
            self.assertIn('then rejected', env.last_error)
            self.assertTrue(env.last_error.endswith('The harness reverted your last step.'))
            self.assertLessEqual(len(env.last_error), 600)

    def test_parse_errors_report_positions_in_the_action_not_the_wrapper(self):
        self.assertEqual(self.rl.action_positions('error: <input>:3:5: expected x'), 'error: <input>:2:3: expected x')
        self.assertEqual(self.rl.action_positions('<input>:2:0: a <input>:1:1: b'), '<input>:1:0: a <input>:1:0: b')
        error = {'message': 'Lean error:\n<input>:2:18: unexpected end of input; expected \'|\''}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, {'induction n with': error})
            self.assertEqual(env.step('induction n with', 0)['status'], 'rejected')
            self.assertIn("<input>:1:16: unexpected end of input", env.last_error)

    def test_replay_crash_censors_the_group_instead_of_scoring_a_failed_proof(self):
        crash = dict(status='crash', lean_calls=1, lean_seconds=0, lean_cpu_seconds=0)
        with TemporaryDirectory() as directory, patch.object(self.rl, 'lean_unit', lambda task: 'unit'), \
                patch.object(self.rl, 'verify_group', lambda ctx, unit, bodies: [crash]):
            env = self.episode(directory, {'simp': {'proofState': 1, 'goals': []}})
            result = env.step('simp', 0)
            self.assertTrue(result['censored'] and result['done'])
            self.assertFalse(result['accepted'])
            self.assertEqual(result['reward'], 0.0)

    def test_failed_setup_closes_the_half_built_repl(self):
        closed = []

        class Repl:
            def __init__(self, *_):
                stream = SimpleNamespace(close=lambda: None)
                self.proc = SimpleNamespace(stdin=stream, stdout=stream)

            def send(self, request, _):
                return ({'env': 1} if 'env' not in request else {'env': 2, 'sorries': []}), 0, 0

            def close(self):
                closed.append(True)

        ctx = SimpleNamespace(checker=SimpleNamespace(guard=lambda name: ''), setup_timeout=1, timeout=1)
        task = dict(task_id='t', name='t', kind='whole', header='theorem t : True', given='')
        with TemporaryDirectory() as directory, patch.object(self.rl, 'Repl', Repl):
            env = self.rl.LeanEpisode(ctx, task, Path(directory), dict(lean_calls=0, lean_seconds=0, lean_cpu_seconds=0),
                                      version=2, horizon=3)
            with self.assertRaises(RuntimeError):
                env.reset()
            self.assertIsNone(env.repl)
            self.assertEqual(closed, [True])


    def test_a_dead_repl_is_a_crash_not_a_broken_pipe_exception(self):
        def dead_stream():
            def close():
                raise BrokenPipeError
            return SimpleNamespace(close=close)

        class Repl:
            proc = SimpleNamespace(stdin=dead_stream(), stdout=dead_stream())

            def send(self, request, timeout):
                raise EOFError('REPL closed its input')

            def close(self):
                pass

        with TemporaryDirectory() as directory:
            env = self.episode(directory, {})
            del env.send
            env.repl = Repl()
            result = env.step('simp', 0)
            self.assertEqual((result['status'], result['done'], result['censored']), ('crash', True, True))
            self.assertIsNone(env.repl)


try:
    import torch as _torch
    import transformers as _transformers
except ImportError:  # The local environment has no torch; the remote one does.
    _torch = None


@unittest.skipIf(_torch is None, 'torch and transformers are required')
class ActionLogprobTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('tactic_rl_logprob', PATH)
        cls.rl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.rl)

    @staticmethod
    def full_logits_logprob(model, prompt_ids, action_ids, torch):
        """The previous implementation: logits at every position."""
        ids = torch.tensor(prompt_ids + action_ids, device=next(model.parameters()).device)[None]
        logits = model(input_ids=ids, attention_mask=torch.ones_like(ids), use_cache=False).logits
        predicted = logits[0, len(prompt_ids) - 1:-1].float().log_softmax(-1)
        targets = torch.tensor(action_ids, device=ids.device)
        return predicted.gather(1, targets[:, None]).sum()

    def tiny_model(self):
        _torch.manual_seed(0)
        config = _transformers.Qwen2Config(vocab_size=64, hidden_size=32, intermediate_size=64,
                                           num_hidden_layers=2, num_attention_heads=2,
                                           num_key_value_heads=2, max_position_embeddings=128)
        return _transformers.Qwen2ForCausalLM(config).float().eval()

    def gradients(self, model, function, prompt_ids, action_ids):
        model.zero_grad(set_to_none=True)
        value = function(model, prompt_ids, action_ids, _torch)
        value.backward()
        grads = {name: p.grad.clone() for name, p in model.named_parameters() if p.grad is not None}
        return value.detach(), grads

    def test_value_and_gradient_equal_the_full_logits_path(self):
        model = self.tiny_model()
        generator = _torch.Generator().manual_seed(1)
        for prompt_length, action_length in ((1, 1), (5, 1), (7, 3), (30, 12), (64, 1), (20, 40)):
            prompt = _torch.randint(0, 64, (prompt_length,), generator=generator).tolist()
            action = _torch.randint(0, 64, (action_length,), generator=generator).tolist()
            with self.subTest(prompt=prompt_length, action=action_length):
                old_value, old_grads = self.gradients(model, self.full_logits_logprob, prompt, action)
                new_value, new_grads = self.gradients(model, self.rl.action_logprob, prompt, action)
                self.assertLess(float((old_value - new_value).abs()), 1e-5)
                self.assertEqual(set(old_grads), set(new_grads))
                worst = max(float((old_grads[name] - new_grads[name]).abs().max()) for name in old_grads)
                self.assertLess(worst, 1e-5)
                self.assertGreater(max(float(g.abs().max()) for g in new_grads.values()), 0.0)

    def test_a_forward_without_logits_to_keep_falls_back_to_the_full_path(self):
        model = self.tiny_model()
        real_forward = model.forward

        def old_forward(*args, **kwargs):
            if 'logits_to_keep' in kwargs:
                raise TypeError("forward() got an unexpected keyword argument 'logits_to_keep'")
            return real_forward(*args, **kwargs)

        prompt, action = [3, 9, 27, 1, 5], [8, 2, 40]
        expected = self.full_logits_logprob(model, prompt, action, _torch)
        model.forward = old_forward
        self.assertLess(float((self.rl.action_logprob(model, prompt, action, _torch) - expected).abs()), 1e-6)

    def test_the_logits_tensor_covers_only_the_action_positions(self):
        model = self.tiny_model()
        seen = []
        real_forward = model.forward

        def spy(*args, **kwargs):
            out = real_forward(*args, **kwargs)
            seen.append(tuple(out.logits.shape))
            return out

        model.forward = spy
        self.rl.action_logprob(model, list(range(1, 31)), [4, 5, 6], _torch)
        self.assertEqual(seen, [(1, 4, 64)])


class GradientCheckpointTest(unittest.TestCase):
    """update() with checkpointing gives the same gradients; needs torch (runs on the CPU fleet)."""

    def setUp(self):
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:
            self.skipTest('torch and transformers are not installed locally')
        spec = importlib.util.spec_from_file_location('tactic_rl_gc', PATH)
        self.rl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.rl)

    def test_checkpointing_preserves_update_gradients(self):
        import torch
        from contextlib import nullcontext
        from transformers import Qwen2Config, Qwen2ForCausalLM
        config = Qwen2Config(vocab_size=64, hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                             num_attention_heads=2, num_key_value_heads=2, attention_dropout=0.0)
        torch.manual_seed(0)
        base = Qwen2ForCausalLM(config).eval()
        trajectories = [[dict(reward=0.0, prompt_ids=list(range(1, 9)), action_ids=[3, 4, 5]),
                         dict(reward=1.0, prompt_ids=list(range(2, 12)), action_ids=[6, 7])],
                        [dict(reward=0.0, prompt_ids=list(range(5, 15)), action_ids=[8])]]
        grads = []
        for checkpointing in (False, True):
            model = Qwen2ForCausalLM(config)
            model.load_state_dict(base.state_dict())
            model.eval()
            if checkpointing:
                model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
            optimizer = torch.optim.SGD(model.parameters(), lr=0.0)
            self.rl.update(model, optimizer, trajectories, torch, nullcontext, 0.95, checkpointing)
            self.assertFalse(model.training)
            grads.append([p.grad.clone() for p in model.parameters() if p.grad is not None])
        self.assertEqual(len(grads[0]), len(grads[1]))
        self.assertLess(max(float((a - b).abs().max()) for a, b in zip(*grads)), 1e-5)


if __name__ == '__main__':
    unittest.main()
