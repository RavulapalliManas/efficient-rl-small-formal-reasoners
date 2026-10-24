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
            cls.environment = __import__('rl_components.environment', fromlist=['LeanEpisode'])

    def setUp(self):
        self.assertTrue(PATH.exists(), 'Interactive tactic trainer is missing')

    def test_group_budget_reserves_candidates_fallbacks_and_native_setup(self):
        config = dict(group_size=4, max_episode_steps=6, step_candidates=3,
                      max_action_tokens=192, environment_version=2, max_chunk_tactics=4,
                      automation_fallback=['omega', 'simp'], max_generated_tokens=13831,
                      max_lean_calls=357)
        counts = dict(generated_tokens=7, lean_calls=11)
        self.rl.check_group_budget(config, counts, native_bank=False)
        self.assertEqual(counts, dict(generated_tokens=7, lean_calls=11))
        with self.assertRaisesRegex(self.rl.BudgetReached, 'token_cap'):
            self.rl.check_group_budget(config, dict(counts, generated_tokens=8), native_bank=False)
        with self.assertRaisesRegex(self.rl.BudgetReached, 'lean_call_cap'):
            self.rl.check_group_budget(config, dict(counts, lean_calls=12), native_bank=False)
        # Native roots require one additional setup call for every attempt.
        with self.assertRaisesRegex(self.rl.BudgetReached, 'lean_call_cap'):
            self.rl.check_group_budget(config, counts, native_bank=True)
        self.rl.check_group_budget(dict(config, max_lean_calls=361), counts, native_bank=True)

    def checkpoint_writers(self):
        def save_model(path):
            path.mkdir(parents=True, exist_ok=True)
            (path / 'model.safetensors').write_text('new weights')

        def save_tokenizer(path):
            (path / 'tokenizer.json').write_text('{}')

        model = SimpleNamespace(save_pretrained=save_model)
        tokenizer = SimpleNamespace(save_pretrained=save_tokenizer)
        optimizer = SimpleNamespace(state_dict=lambda: dict(step=9))
        torch = SimpleNamespace(save=lambda state, path: path.write_text(json.dumps(state)))
        return model, tokenizer, optimizer, torch

    def test_periodic_checkpoint_replaces_only_the_rolling_snapshot(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'checkpoint').mkdir()
            (output / 'checkpoint/old').write_text('old snapshot')
            (output / 'checkpoint_g1').mkdir()
            (output / 'checkpoint_g1/keep').write_text('retained snapshot')
            model, tokenizer, optimizer, torch = self.checkpoint_writers()
            self.rl.save_periodic_checkpoint(
                output, model, tokenizer, optimizer, torch, group=3,
                counts=dict(groups=4, optimizer_updates=2))
            checkpoint = output / 'checkpoint'
            self.assertEqual(set(path.name for path in checkpoint.iterdir()),
                             {'model.safetensors', 'tokenizer.json', 'optimizer.pt', 'checkpoint.json'})
            self.assertEqual(json.loads((checkpoint / 'optimizer.pt').read_text()), dict(step=9))
            self.assertEqual(json.loads((checkpoint / 'checkpoint.json').read_text()),
                             dict(groups=4, group=3, optimizer_updates=2))
            self.assertEqual((output / 'checkpoint_g1/keep').read_text(), 'retained snapshot')
            self.assertFalse((output / 'checkpoint.tmp').exists())

    def test_retained_checkpoint_omits_optimizer_and_refuses_replacement(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            model, tokenizer, _, _ = self.checkpoint_writers()
            counts = dict(groups=4, optimizer_updates=2)
            name = self.rl.save_retained_checkpoint(output, model, tokenizer, group=3, counts=counts)
            checkpoint = output / name
            self.assertEqual(name, 'checkpoint_g4')
            self.assertEqual(set(path.name for path in checkpoint.iterdir()),
                             {'model.safetensors', 'tokenizer.json', 'checkpoint.json'})
            metadata = (checkpoint / 'checkpoint.json').read_bytes()
            with self.assertRaises(OSError):
                self.rl.save_retained_checkpoint(output, model, tokenizer, group=99, counts=counts)
            self.assertEqual((checkpoint / 'checkpoint.json').read_bytes(), metadata)

    def test_generation_record_preserves_terminal_probability_and_physical_tokens(self):
        self.assertTrue(hasattr(self.rl, 'generation_record'), 'Shared generation boundary is missing')
        for raw, retained, violation in (([1, 0, 0], [1], False),
                                         ([0, 5, 1], [0, 5, 1], True),
                                         ([5, 0, 6, 1], [5, 0, 6, 1], True),
                                         ([5, 6, 5], [5, 6, 5], False)):
            with self.subTest(raw=raw):
                record = self.rl.generation_record(raw, 1, 0)
                self.assertEqual(record['raw_response_ids'], raw)
                self.assertEqual(record['action_ids'], retained)
                self.assertEqual(record['generated_tokens'], len(raw))
                self.assertEqual(record['retained_tokens'], len(retained))
                self.assertEqual(record['pad_before_eos'], violation)

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
        with TemporaryDirectory() as directory, patch.object(self.environment, 'Repl', Repl), \
                patch.object(self.environment, 'question', return_value=(q, None)):
            env = self.rl.LeanEpisode(ctx, task, Path(directory),
                                     dict(lean_calls=0, lean_seconds=0, lean_cpu_seconds=0))
            state = env.reset()
            self.assertNotIn('reference_rest', state)
            head, _ = split_prefix(q['prefix'])
            self.assertEqual(commands[0], {'cmd': head})
            local_q = dict(q, header='nonrec example : True ∧ True')
            self.assertEqual(commands[1], {'cmd': source_for(local_q, '  sorry\n')[len(head):], 'env': 7})
            self.assertEqual(commands[2], {'tactic': self.rl.tactic_block('  constructor'), 'proofState': 9})
            self.assertEqual(env.goals, ['case left\n⊢ True', 'case right\n⊢ True'])
            self.assertEqual(env.state, 10)
            self.assertEqual(env.reset(), state)
            self.assertEqual(len(commands), 3)
            with patch.object(env, 'send', return_value={'proofState': 10, 'goals': []}), \
                    patch.object(self.environment, 'verify_group', return_value=[dict(status='accepted', lean_calls=1,
                                 lean_seconds=0, lean_cpu_seconds=0)]) as replay:
                self.assertEqual(env.step('exact h', 0)['reward'], 1)
                self.assertEqual(replay.call_args.args[1], task)

    def test_anonymous_header_preserves_signature_and_universe_binders(self):
        cases = [
            ('theorem p : True', ('', 'nonrec example : True')),
            ('private theorem N.p (n : Nat) : n = n', ('', 'nonrec example (n : Nat) : n = n')),
            ('@[simp] protected theorem N.p : True', ('', 'nonrec example : True')),
            ('open Nat in\n@[simp]\nprivate\ntheorem p : True', ('', 'open Nat in\nnonrec example : True')),
            ('lemma N.«a b».{u, v} (α : Sort u) (β : Sort v) : True',
             ('universe u v\n', 'nonrec example (α : Sort u) (β : Sort v) : True')),
        ]
        for header, expected in cases:
            with self.subTest(header=header):
                self.assertEqual(self.rl.anonymous_header(header), expected)
        with self.assertRaisesRegex(ValueError, 'Unsupported declaration prefix'):
            self.rl.anonymous_header('open\n  Nat in\ntheorem p : True')

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

    def test_nontraining_assessment_requires_the_declared_split_and_training_exclusions(self):
        self.assertTrue(hasattr(self.rl, 'assessment_tasks'), 'Explicit split assessment admission is missing')
        task = dict(task_id='p:whole', name='p', header='theorem p : True', kind='whole',
                    given='', state='⊢ True')
        for split in ('validation', 'test'):
            pool = [dict(name='p', split=split, formal_statement='theorem p : True := by sorry')]
            with self.subTest(split=split):
                self.assertEqual(self.rl.assessment_tasks([task], pool, {'p'}, 3, split), [task])
                self.assertEqual(pool[0]['split'], split)
                with self.assertRaisesRegex(ValueError, 'excluded'):
                    self.rl.assessment_tasks([task], pool, set(), 3, split)
                with self.assertRaises(RuntimeError):
                    self.rl.training_tasks([task], pool, set(), 3)
                with self.assertRaises(RuntimeError):
                    self.rl.assessment_tasks([task], [dict(pool[0], split='train')], {'p'}, 3, split)
                self.assertEqual(self.rl.execution_mode(False, True, split), split + '_assessment')

    def test_assessment_split_cannot_enable_training_or_native_bank_evaluation(self):
        import contextlib
        import io
        for extra in (['--pool', 'unused', '--tasks', 'unused', '--exclude-names', 'unused'],
                      ['--bank', 'unused', '--train', 'unused', '--eval', 'unused',
                       '--exclude-files', 'unused', '--assessment-only']):
            command = ['run_tactic_rl.py'] + [part for name in
                ('config', 'model', 'checker', 'environment', 'mathlib-identity', 'repl', 'output')
                for part in ('--' + name, 'unused')] + extra + ['--assessment-split', 'validation']
            error = io.StringIO()
            with self.subTest(extra=extra), patch.object(self.rl.sys, 'argv', command), \
                    contextlib.redirect_stderr(error), self.assertRaises(SystemExit):
                self.rl.main()
            self.assertIn('assessment-split requires --assessment-only with pool tasks', error.getvalue())

    def test_assessment_freezes_truthful_split_and_ids_before_loading_models(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            config = json.loads((PATH.parents[2] / 'configs/eval/qwen_05b_curriculum_stage_probe_v21prompt.json').read_text())
            task = dict(task_id='p:whole', name='p', header='theorem p : True', kind='whole', given='', state='⊢ True')
            files = dict(config=json.dumps(config), pool=json.dumps(dict(name='p', split='validation',
                formal_statement='theorem p : True := by sorry')) + '\n', tasks=json.dumps(task) + '\n',
                exclude_names='p\n', checker='', mathlib_identity='{}')
            for name, text in files.items():
                (base / name).write_text(text)
            command = ['run_tactic_rl.py'] + [part for name in
                ('config', 'pool', 'tasks', 'exclude_names', 'model', 'checker', 'environment',
                 'mathlib_identity', 'repl', 'output') for part in ('--' + name.replace('_', '-'), str(base / name))]
            command += ['--assessment-only', '--assessment-split', 'validation']
            with patch.object(self.rl.sys, 'argv', command), patch.object(self.rl.signal, 'signal'), \
                    patch.object(self.rl.signal, 'alarm'), \
                    patch.object(self.rl, 'file_sha256', side_effect=RuntimeError('fixture stops before model load')):
                with self.assertRaisesRegex(RuntimeError, 'fixture stops'):
                    self.rl.main()
            run = json.loads((base / 'output/run.json').read_text())
            self.assertEqual(run['mode'], 'validation_assessment')
            self.assertEqual(run['assessment_split'], 'validation')
            self.assertEqual(run['counts']['optimizer_updates'], 0)
            self.assertEqual(json.loads((base / 'output/evaluation_task_ids.json').read_text()), ['p:whole'])

    def test_forbidden_tactic_is_rejected_without_calling_lean(self):
        with TemporaryDirectory() as directory:
            env = self.rl.LeanEpisode(None, dict(task_id='p', name='p', header='theorem p : False',
                                                given=''), Path(directory), None)
            result = env.step('sorry', 0.025)
            self.assertEqual(result['status'], 'forbidden_construct')
            self.assertEqual(result['reward'], 0)

    def test_v1_action_and_replay_crashes_censor_the_group(self):
        task = dict(task_id='p', name='p', header='theorem p : True', given='', kind='whole')
        for stage in ('action', 'replay'):
            with self.subTest(stage=stage), TemporaryDirectory() as directory:
                counts = dict(lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0)
                env = self.rl.LeanEpisode(SimpleNamespace(timeout=1), task, Path(directory), counts)
                env.state, env.path, env.seen = 1, [], {self.rl.state_key(['⊢ True'])}
                verdict = dict(status='crash', lean_calls=1, lean_seconds=0.0, lean_cpu_seconds=0.0)
                with patch.object(env, 'send', side_effect=EOFError() if stage == 'action' else None,
                                  return_value={'proofState': 2, 'goals': []}), \
                        patch.object(self.environment, 'verify_group', return_value=[verdict]):
                    result = env.step('trivial', 0)
                self.assertEqual(result['status'], 'crash' if stage == 'action' else 'replay_crash')
                self.assertTrue(result.get('censored'), 'A crashed verifier must not supply a training outcome')
                self.assertTrue(result['done'])
                self.assertFalse(result['accepted'])
                self.assertEqual(result['reward'], 0.0)

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
        cls.environment = __import__('rl_components.environment', fromlist=['LeanEpisode'])

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

    def test_candidate_menu_filters_without_changing_the_live_proof(self):
        responses = {'intro h': {'proofState': 1, 'goals': ['h : P\n⊢ Q']},
                     'exact missing': {'message': 'Lean error:\nunknown identifier missing'}}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses)
            send = env.send
            def counted_send(request, timeout):
                env.counts['lean_calls'] += 1
                return send(request, timeout)
            env.send = counted_send
            before = env.prompt_state()
            menu = env.candidate_menu(['intro h', 'exact missing', 'sorry', 'intro h\nsimp'],
                                      max_candidates=4, max_lean_calls=20)
            self.assertEqual(menu['actions'], [{'id': 0, 'tactic': 'intro h'}])
            self.assertEqual(env.prompt_state(), before)
            self.assertEqual(env.state, 0)
            self.assertEqual(env.steps_used, 0)
            self.assertEqual(env.failed, {})
            self.assertEqual(env.sent, ['intro h', 'exact missing'])
            self.assertEqual(env.counts['lean_calls'], 2)
            with self.assertRaisesRegex(ValueError, 'choice'):
                env.select_candidate(menu, 1)
            result = env.select_candidate(menu, 0)
            self.assertEqual(result['status'], 'changed')
            self.assertEqual(env.state, 1)
            self.assertEqual(env.counts['lean_calls'], 3)
            with self.assertRaisesRegex(ValueError, 'stale'):
                env.select_candidate(menu, 0)

    def test_candidate_menu_reserves_calls_and_requires_complete_proof_replay(self):
        responses = {'close': {'proofState': 1, 'goals': []}}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses)
            send = env.send
            def counted_send(request, timeout):
                env.counts['lean_calls'] += 1
                return send(request, timeout)
            env.send = counted_send
            with self.assertRaisesRegex(self.rl.BudgetReached, 'lean_call_cap'):
                env.candidate_menu(['close'], max_candidates=1, max_lean_calls=2)
            self.assertEqual(env.sent, [])
            rejected = dict(status='rejected', lean_calls=3, lean_seconds=0, lean_cpu_seconds=0)
            with patch.object(self.environment, 'lean_unit', return_value='unit'), \
                    patch.object(self.environment, 'verify_group', return_value=[rejected]):
                menu = env.candidate_menu(['close'], max_candidates=1, max_lean_calls=8)
            self.assertEqual(menu['status'], 'empty')
            self.assertEqual(menu['actions'], [])
            self.assertEqual(env.counts['lean_calls'], 4)
            self.assertEqual(env.state, 0)

    def test_candidate_menu_aborts_on_a_crash_instead_of_returning_a_partial_mask(self):
        responses = {'intro h': {'proofState': 1, 'goals': ['h : P\n⊢ Q']}, 'crash': EOFError()}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses)
            menu = env.candidate_menu(['intro h', 'crash'], max_candidates=2, max_lean_calls=12)
            self.assertEqual(menu['status'], 'crash')
            self.assertEqual(menu['actions'], [])
            self.assertTrue(menu['censored'])
            self.assertEqual(env.state, 0)

    def test_candidate_menu_counts_both_failed_replay_setup_attempts(self):
        verifier = __import__('bench_lean_verifier')
        def setup_failure(ctx, head, tag, record):
            record['lean_calls'] += 1
            record['setup_error'] = 'EOFError'
            return None, None
        unit = dict(kind='statement', task_id='t', name='t', header='theorem t : True', given_steps=[])
        with TemporaryDirectory() as directory:
            env = self.episode(directory, {'close': {'proofState': 1, 'goals': []}})
            env.ctx.local, env.ctx.pool_repls, env.ctx.restart_after = SimpleNamespace(), set(), 8
            send = env.send
            def counted_send(request, timeout):
                env.counts['lean_calls'] += 1
                return send(request, timeout)
            env.send = counted_send
            with patch.object(self.environment, 'lean_unit', return_value=unit), \
                    patch.object(verifier, 'open_repl', side_effect=setup_failure), \
                    self.assertRaisesRegex(RuntimeError, 'setup failed twice'):
                env.candidate_menu(['close'], max_candidates=1, max_lean_calls=8)
            self.assertEqual(env.counts['lean_calls'], 3)
            self.assertEqual(env.state, 0)
            self.assertEqual(env.steps_used, 0)

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
                    patch.object(self.environment, 'lean_unit', lambda task: 'unit'), \
                    patch.object(self.environment, 'verify_group', lambda ctx, unit, bodies: [
                        dict(status=verdict, lean_calls=1, lean_seconds=0.1, lean_cpu_seconds=0.1, body=bodies[0])]):
                env = self.episode(directory, responses)
                result = env.step('-- close it\nsimp\nomega', 0)
                self.assertTrue(result['done'])
                self.assertEqual(result['reward'], reward)
                self.assertEqual(env.sent, ['simp'])
                self.assertEqual(result['replay']['body'], '  first |\n    -- close it\n    simp\n')

    def test_final_body_preserves_verified_prefix_after_backtrack_and_chunk_truncation(self):
        responses = {'a': {'proofState': 1, 'goals': ['⊢ P']},
                     'split': {'proofState': 2, 'goals': ['⊢ Q']},
                     'bad': {'message': 'Lean error:\nunknown identifier bad'},
                     'close': {'proofState': 3, 'goals': []}}
        verdict = dict(status='accepted', lean_calls=1, lean_seconds=0, lean_cpu_seconds=0)
        with TemporaryDirectory() as directory, patch.object(self.environment, 'lean_unit', lambda task: 'unit'), \
                patch.object(self.environment, 'verify_group', return_value=[verdict]) as replay:
            env = self.episode(directory, responses, backtrack_owner='policy')
            env.step('a', 0)
            env.step('backtrack 0', 0)
            self.assertEqual(env.step('split\nbad', 0)['status'], 'partial')
            result = env.step('close\nunused', 0)
            expected = '  first |\n    split\n  first |\n    close\n'
            self.assertEqual(replay.call_args.args[2], [expected])
            self.assertIn('proof_body', result, 'The replayed body must survive the rollout handoff')
            self.assertEqual(result['proof_body'], expected)
            self.assertEqual(json.loads(json.dumps(result))['proof_body'], expected)

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

    def test_v2_rejects_unknown_rewards_repair_and_bad_settings(self):
        with TemporaryDirectory() as directory:
            for kwargs in (dict(reward_mode='goal_text_bonus'), dict(max_repair_attempts=1),
                           dict(reward_mode='error_feedback_v1', max_repair_attempts=1),
                           dict(backtrack_owner='search'), dict(max_tactics=0), dict(backtrack_after=0)):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    self.rl.LeanEpisode(None, {}, Path(directory), None, version=2, horizon=4, **kwargs)
            env = self.rl.LeanEpisode(None, {}, Path(directory), None, version=2, horizon=4,
                                      reward_mode='error_feedback_v1')
            self.assertEqual(env.reward_mode, 'error_feedback_v1')

    def test_v2_error_feedback_scores_each_action_outcome(self):
        def score(status, **fields):
            return self.rl.v2_feedback({**dict(status=status, reward=0.0, accepted=False, done=False), **fields})
        cases = [(score('accepted', accepted=True, done=True), 1.0, 'accepted'),
                 (score('format_error'), -0.2, 'format_error'),
                 (score('forbidden_construct'), -0.2, 'forbidden_construct'),
                 (score('invalid_backtrack'), -0.2, 'invalid_backtrack'),
                 (score('rejected', error="error: unknown identifier 'missing'"), -0.2, 'rejected'),
                 (score('rejected', error="error: unexpected token ')'; expected tactic"), -0.2, 'rejected'),
                 (score('rejected', error='error: type mismatch\n  0\nexpected True'), -0.1, 'rejected'),
                 (score('rejected', error='error: internal exception #4'), -0.1, 'rejected'),
                 (score('no_progress'), -0.1, 'no_progress'),
                 (score('cycle'), -0.1, 'cycle'),
                 (score('partial', failure='no_progress', error='the goals are unchanged.'), -0.1, 'partial'),
                 (score('partial', failure='rejected', error="error: unknown identifier 'x'"), -0.2, 'partial'),
                 (score('replay_rejected', done=True), -0.1, 'replay_rejected'),
                 (score('replay_forbidden_construct', done=True), -0.2, 'replay_forbidden_construct'),
                 (score('changed'), 0.0, 'changed'), (score('backtrack'), 0.0, 'backtrack'),
                 (score('timeout', done=True), 0.0, 'timeout'), (score('crash', done=True), 0.0, 'crash'),
                 (score('replay_timeout', done=True), 0.0, 'replay_timeout')]
        for result, reward, status in cases:
            with self.subTest(status=status, error=result.get('error')):
                self.assertEqual(result['reward'], reward)
                self.assertEqual(result['status'], status)  # The environment status survives scoring.

    def test_v2_error_feedback_penalizes_each_failed_action_and_not_valid_steps(self):
        same = {'proofState': 2, 'goals': ['n : ℕ\n⊢ n + 0 = n']}
        responses = {'exact x': {'proofState': 1, 'goals': [], 'messages': [
                         {'severity': 'error', 'data': 'unknown identifier x'}]},
                     'skip': same, 'intro h': {'proofState': 3, 'goals': ['h : P\n⊢ Q']},
                     'exact missing': {'message': 'Lean error:\nunknown identifier missing'}}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses, backtrack_after=10, reward_mode='error_feedback_v1')
            for text, status, reward in (('exact x', 'rejected', -0.2), ('skip', 'no_progress', -0.1),
                                         ('```lean\nsimp', 'format_error', -0.2),
                                         ('sorry', 'forbidden_construct', -0.2),
                                         ('intro h\nexact missing', 'partial', -0.2)):
                with self.subTest(text=text):
                    result = env.step(text, 0)
                    self.assertEqual((result['status'], result['reward']), (status, reward), result)
                    self.assertFalse(result['done'])
            self.assertEqual(env.shown, ['intro h'])  # The valid prefix of the partial action stays.
            plain = self.episode(directory, responses, backtrack_after=10)
            self.assertEqual(plain.step('intro h', 0)['reward'], 0.0)
            self.assertEqual(plain.step('skip', 0)['reward'], 0.0)  # The default v2 reward stays terminal.

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
        with TemporaryDirectory() as directory, patch.object(self.environment, 'lean_unit', lambda task: 'unit'), \
                patch.object(self.environment, 'verify_group', lambda ctx, unit, bodies: [crash]):
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
        with TemporaryDirectory() as directory, patch.object(self.environment, 'Repl', Repl):
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

    def replay(self, verdict):
        return patch.object(self.environment, 'verify_group', lambda ctx, unit, bodies: [
            dict(status=verdict, lean_calls=1, lean_seconds=0.1, lean_cpu_seconds=0.1, body=bodies[0])])

    def test_step_search_keeps_the_best_candidate_as_one_step(self):
        responses = {'exact missing': {'message': 'Lean error:\nunknown identifier missing'},
                     'intro h': {'proofState': 1, 'goals': ['h : P\n⊢ Q']},
                     'skip': {'proofState': 2, 'goals': ['n : ℕ\n⊢ n + 0 = n']}}
        with TemporaryDirectory() as directory:
            env = self.episode(directory, responses)
            search = env.search_step(['exact missing', 'intro h', 'skip'])
            self.assertEqual((search['chosen'], search['result']['status']), (1, 'changed'))
            self.assertEqual((env.state, env.shown, env.steps_used), (1, ['intro h'], 1))
            self.assertEqual(env.sent, ['exact missing', 'intro h', 'skip'])
            probes = {p['index']: p for p in search['probes']}
            # A rejected probe leaves the state unchanged and records the next observation for repair data.
            self.assertIn('unknown identifier missing', probes[0]['observation_after'])
            self.assertIn('exact missing', probes[0]['observation_after'])
            self.assertNotIn('observation_after', probes[1])
            self.assertEqual(probes[2]['status'], 'no_progress')
            failed = self.episode(directory, responses)
            search = failed.search_step(['exact missing', 'skip'])
            self.assertEqual((search['chosen'], failed.state, failed.steps_used), (0, 0, 1))

    def test_step_search_stops_at_a_verified_proof_and_at_a_timeout(self):
        responses = {'close': {'proofState': 1, 'goals': []}, 'intro h': {'proofState': 2, 'goals': ['⊢ Q']},
                     'slow': TimeoutError()}
        with TemporaryDirectory() as directory, patch.object(self.environment, 'lean_unit', lambda task: 'unit'), \
                self.replay('accepted'):
            env = self.episode(directory, responses)
            search = env.search_step(['intro h', 'close', 'intro h'])
            self.assertEqual(search['chosen'], 1)
            self.assertTrue(search['result']['accepted'])
            self.assertEqual(env.sent, ['intro h', 'close'])
            slow = self.episode(directory, responses)
            search = slow.search_step(['slow', 'intro h'])
            self.assertEqual((search['chosen'], search['result']['status']), (0, 'timeout'))
            self.assertEqual(slow.sent, ['slow'])
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'v2'):
                self.episode(directory, responses).search_step([])

    def test_automation_completes_only_through_replay_and_a_probe_does_not_commit(self):
        responses = {'omega': {'message': 'Lean error:\nomega could not prove the goal'},
                     'simp': {'proofState': 1, 'goals': []}}
        for verdict in ('accepted', 'rejected'):
            with TemporaryDirectory() as directory, patch.object(self.environment, 'lean_unit', lambda task: 'unit'), \
                    self.replay(verdict):
                env = self.episode(directory, responses)
                probe = env.try_automation(['omega', 'simp'], commit=False)
                self.assertEqual((env.state, env.path, env.steps_used), (0, [], 0))
                if verdict == 'rejected':
                    self.assertIsNone(probe)
                    continue
                self.assertEqual(probe['tactic'], 'simp')
                done = env.try_automation(['omega', 'simp'])
                self.assertTrue(done['result']['accepted'])
                self.assertEqual(env.shown, ['simp'])

    def test_search_automation_and_task_filter_settings_are_validated(self):
        base = json.loads((PATH.parents[2] / 'configs/eval/qwen_05b_curriculum_stage_probe_v21prompt.json').read_text())
        v1 = json.loads((PATH.parents[2] / 'configs/eval/qwen_05b_curriculum_stage_probe_v1prompt.json').read_text())
        spec = dict(pass_rates_from='/runs/train_assessment', min_exclusive=0, max_inclusive=0.75)
        self.rl.validate_config(dict(base, step_candidates=8, automation_fallback=['omega', 'simp_all'], task_filter=spec))
        bad = [dict(base, step_candidates=0), dict(base, step_candidates=True), dict(base, automation_fallback='omega'),
               dict(base, automation_fallback=['sorry']), dict(base, automation_fallback=['simp\nomega']),
               dict(v1, step_candidates=2), dict(base, task_filter=dict(spec, extra=1)),
               dict(base, task_filter=dict(spec, min_exclusive=0.8)), dict(base, task_filter=dict(spec, max_inclusive=1.5))]
        for config in bad:
            with self.subTest(config={k: config.get(k) for k in ('step_candidates', 'automation_fallback', 'task_filter')}):
                with self.assertRaises(ValueError):
                    self.rl.validate_config(config)
        self.rl.check_mode_settings(dict(base, step_candidates=8, automation_fallback=['omega']), evaluating=True)
        self.rl.check_mode_settings(dict(base, task_filter=spec), evaluating=False)
        for config, evaluating in ((dict(base, step_candidates=8), False), (dict(base, automation_fallback=['omega']), False),
                                   (dict(base, task_filter=spec), True)):
            with self.assertRaises(ValueError):
                self.rl.check_mode_settings(config, evaluating)

    def test_pass_rate_filter_keeps_the_measured_mixed_band_from_training_assessment_only(self):
        rows = [dict(group=0, task_id='a', returns=[0, 0, 0, 0]), dict(group=1, task_id='b', returns=[1, 0, 0, 0]),
                dict(group=2, task_id='c', returns=[1, 1, 1, 1]), dict(group=3, status='censored', reason='prompt_cap'),
                dict(group=4, task_id='b', returns=[1, 1, 0, 0])]
        tasks = [dict(task_id=name) for name in 'abcd']
        with TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'steps.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
            spec = dict(pass_rates_from=str(source), min_exclusive=0, max_inclusive=0.75)
            for run in (dict(mode='DEV_evaluation', status='completed'), dict(mode='training_state_assessment', status='running'),
                        dict(mode='training_state_assessment', status='failed')):
                (source / 'run.json').write_text(json.dumps(run))
                with self.assertRaisesRegex(ValueError, 'training-state assessment'):
                    self.rl.pass_rate_filter(tasks, spec)
            (source / 'run.json').write_text(json.dumps(dict(mode='training_state_assessment', status='completed')))
            kept, report = self.rl.pass_rate_filter(tasks, spec)
            self.assertEqual([t['task_id'] for t in kept], ['b'])  # 3 of 8 attempts accepted.
            self.assertEqual(report['dropped'], {'at_or_below_min': 1, 'above_max': 1, 'unmeasured': 1})
            self.assertEqual(report['kept'], 1)
            (source / 'run.json').write_text(json.dumps(dict(mode='training_state_assessment', status='capped')))
            self.assertEqual([t['task_id'] for t in self.rl.pass_rate_filter(tasks, spec)[0]], ['b'])
            (source / 'steps.jsonl').write_text(json.dumps(dict(task_id='dev:x', returns=[1, 0])) + '\n')
            with self.assertRaisesRegex(ValueError, 'training tasks'):
                self.rl.pass_rate_filter(tasks, spec)
        self.assertEqual(self.rl.filter_schedule(['a', 'b', 'a', 'c'], {'a', 'c'}), ['a', 'a', 'c'])
        with self.assertRaisesRegex(ValueError, 'every scheduled group'):
            self.rl.filter_schedule(['a'], {'b'})

    def test_retrieved_lemmas_enter_the_observation_only_when_configured(self):
        with TemporaryDirectory() as directory:
            plain = self.episode(directory, {}).prompt_state()
            self.assertNotIn('library lemmas', plain)
            env = self.episode(directory, {}, premises=lambda goals: ['Nat.add_zero : ∀ (n : ℕ), n + 0 = n'])
            text = env.prompt_state()
            self.assertIn('/- library lemmas that may help:\n  - Nat.add_zero : ∀ (n : ℕ), n + 0 = n\n  -/', text)
            self.assertLess(text.index('tactic state'), text.index('library lemmas'))
            self.assertLess(text.index('library lemmas'), text.index('last action result'))
            empty = self.episode(directory, {}, premises=lambda goals: []).prompt_state()
            self.assertIn('/- library lemmas that may help:\n  none\n  -/', empty)
        base = json.loads((PATH.parents[2] / 'configs/eval/qwen_05b_curriculum_stage_probe_v21prompt.json').read_text())
        v1 = json.loads((PATH.parents[2] / 'configs/eval/qwen_05b_curriculum_stage_probe_v1prompt.json').read_text())
        self.rl.validate_config(dict(base, premise_retrieval=dict(corpus='/runs/corpus.jsonl', k=8)))
        for bad in (dict(base, premise_retrieval=dict(corpus='c', k=0)), dict(base, premise_retrieval=dict(corpus='c')),
                    dict(v1, premise_retrieval=dict(corpus='c', k=4))):
            with self.assertRaises(ValueError):
                self.rl.validate_config(bad)

    def test_batch_padding_after_a_stop_is_not_part_of_a_candidate(self):
        stops = [151645, 151643]
        self.assertEqual(self.rl.stop_trimmed([5, 7, 151645, 151645, 151645], stops), [5, 7, 151645])
        self.assertEqual(self.rl.stop_trimmed([5, 7, 9], stops), [5, 7, 9])


class FakeEpisode:
    """Scripted episode: results[step] per step; optional barrier proves concurrent stepping."""

    def __init__(self, name, results, barrier=None, reset_error=None, automation=None, prompt=(1, 2, 3), root=None):
        self.name, self.results, self.barrier, self.reset_error = name, list(results), barrier, reset_error
        self.root, self.resets = root, 0
        self.automation, self.prompt, self.steps, self.closed, self.threads = automation, list(prompt), [], False, set()

    def reset(self):
        self.resets += 1
        if self.reset_error:
            raise self.reset_error
        return f'state {self.name}'

    def step(self, text, bonus):
        import threading
        self.threads.add(threading.get_ident())
        barrier, self.barrier = self.barrier, None  # Only the first step must meet the other samples.
        if barrier:
            barrier.wait()
        self.steps.append(text)
        return self.results.pop(0)

    def search_step(self, texts):
        self.steps.append(tuple(texts))
        result = self.results.pop(0)
        return dict(result=result, chosen=len(texts) - 1,
                    probes=[dict(index=k, status='rejected', error='', tactics_applied=0) for k in range(len(texts))])

    def prompt_state(self):
        return f'state {self.name} after {len(self.steps)}'

    def try_automation(self, tactics, commit=True):
        return self.automation if commit else self.root

    def close(self):
        self.closed = True


def outcome(status='changed', accepted=False, done=False, **extra):
    return dict(status=status, accepted=accepted, done=done, reward=1.0 if accepted else 0.0, **extra)


class LockstepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('tactic_rl_lockstep', PATH)
        cls.rl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.rl)
        cls.environment = __import__('rl_components.environment', fromlist=['LeanEpisode'])

    def generation(self, ids=(7, 151645), pad=False):
        return dict(raw_response_ids=list(ids), action_ids=list(ids), generated_tokens=len(ids),
                    retained_tokens=len(ids), pad_before_eos=pad)

    def run_group(self, envs, candidates=1, automation=(), depth=3, max_prompt_tokens=10, gen=None):
        from concurrent.futures import ThreadPoolExecutor
        calls, records = [], []
        counts = dict(generated_tokens=0, retained_tokens=0, accepted=0, prompt_cap_ends=0,
                      automation_accepted=0, automation_root_solved=0)

        def generate(prompts, n, seeds):
            calls.append((len(prompts), n, list(seeds)))
            return [[(gen or self.generation)() for _ in range(n)] for _ in prompts]
        with ThreadPoolExecutor(max_workers=len(envs)) as pool:
            out = self.rl.lockstep_group(
                envs, generate, lambda ids: f'tactic {ids[0]}', lambda env: env.prompt, pool, depth=depth,
                candidates=candidates, automation=list(automation), max_prompt_tokens=max_prompt_tokens, bonus=0.0,
                seeds=lambda i, step: 100 * i + step, emit=records.append, counts=counts, solved=solved, name='t')
        return out, calls, records, counts

    def setUp(self):
        global solved
        solved = []

    def test_samples_advance_together_drop_out_when_done_and_step_concurrently(self):
        import threading
        barrier = threading.Barrier(2, timeout=5)
        envs = [FakeEpisode(0, [outcome('accepted', True, True)], barrier),
                FakeEpisode(1, [outcome(), outcome('accepted', True, True)], barrier)]
        (trajectories, censored, reason, _, _), calls, records, counts = self.run_group(envs)
        self.assertFalse(censored)
        self.assertEqual([c[0] for c in calls], [2, 1])  # Step 0 batches both samples; step 1 only the active one.
        self.assertEqual(calls[0][2], [0, 100])
        self.assertEqual([(r['sample'], r['step']) for r in records], [(0, 0), (1, 0), (1, 1)])
        self.assertEqual([len(t) for t in trajectories], [1, 2])
        self.assertEqual((counts['accepted'], counts['generated_tokens'], solved), (2, 6, ['t']))
        self.assertEqual(records[2]['state'], 'state 1 after 1')

    def test_prompt_cap_setup_failure_and_episode_censoring_censor_the_group(self):
        envs = [FakeEpisode(0, [outcome()]), FakeEpisode(1, [outcome()], prompt=range(20))]
        self.assertEqual(self.run_group(envs)[0][1:3], (True, 'prompt_cap'))
        envs = [FakeEpisode(0, []), FakeEpisode(1, [], reset_error=TimeoutError('slow setup'))]
        result = self.run_group(envs)[0]
        self.assertTrue(result[1] and result[2].startswith('setup_failure'))
        envs = [FakeEpisode(0, [outcome('crash', done=True, censored=True)]), FakeEpisode(1, [outcome()])]
        self.assertEqual(self.run_group(envs)[0][1:3], (True, 'crash'))

    def test_search_records_the_chosen_candidate_and_automation_finishes_unfinished_paths(self):
        automated = dict(tactic='omega', result=outcome('accepted', True, True))
        envs = [FakeEpisode(0, [outcome(), outcome()], automation=automated), FakeEpisode(1, [outcome('accepted', True, True)])]
        (trajectories, censored, _, root, done), calls, records, counts = self.run_group(
            envs, candidates=2, automation=['omega'], depth=2)
        self.assertFalse(censored)
        self.assertEqual(calls[0][1], 2)
        self.assertEqual(envs[0].steps[0], ('tactic 7', 'tactic 7'))
        self.assertEqual(records[0]['chosen'], 1)
        self.assertEqual(len(records[0]['candidates']), 2)
        self.assertEqual((done, counts['automation_accepted'], counts['accepted']), ([True, False], 1, 1))
        self.assertTrue(records[-1]['automation'])
        self.assertEqual(sum(t[-1]['reward'] for t in trajectories), 1.0)  # Automation never enters policy returns.

    def test_a_root_automation_timeout_rebuilds_the_root_instead_of_censoring(self):
        timeout = dict(tactic='aesop', result=outcome('timeout', done=True))
        envs = [FakeEpisode(0, [outcome('accepted', True, True)], root=timeout), FakeEpisode(1, [outcome()])]
        (trajectories, censored, reason, root, _), _, _, counts = self.run_group(envs, automation=['aesop'], depth=1)
        self.assertFalse(censored)
        self.assertEqual((envs[0].resets, envs[1].resets), (2, 1))
        self.assertEqual((root['result']['status'], counts['automation_root_solved'], counts['accepted']), ('timeout', 0, 1))
        crash = dict(tactic='aesop', result=outcome('crash', done=True, censored=True))
        envs = [FakeEpisode(0, [outcome()], root=crash), FakeEpisode(1, [outcome()])]
        self.assertEqual(self.run_group(envs, automation=['aesop'], depth=1)[0][1:3], (True, 'automation_root_crash'))

    def test_pad_before_eos_is_recorded_and_refused(self):
        envs = [FakeEpisode(0, [outcome()]), FakeEpisode(1, [outcome()])]
        with self.assertRaisesRegex(RuntimeError, 'PAD before EOS'):
            self.run_group(envs, gen=lambda: self.generation(pad=True))

    def test_counters_stay_exact_under_concurrent_lean_calls(self):
        import threading
        counts = dict(lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0)
        envs = []
        for _ in range(8):
            env = self.rl.LeanEpisode(SimpleNamespace(timeout=1), dict(task_id='t', name='t', kind='whole', header='h',
                                                                          given=''), Path('.'), counts, version=2, horizon=6)
            env.repl = SimpleNamespace(send=lambda request, timeout: ({}, 0.001, 0.001))
            envs.append(env)
        threads = [threading.Thread(target=lambda e=e: [e.send({}, 1) for _ in range(2000)]) for e in envs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(counts['lean_calls'], 16000)
        self.assertAlmostEqual(counts['lean_seconds'], 16.0, places=6)

    def test_batching_helpers_split_rows_and_keep_stop_tokens(self):
        rl = self.rl
        ids, mask, width = rl.pad_left([[5, 6, 7], [8]], 0)
        self.assertEqual((ids, mask, width), ([[5, 6, 7], [0, 0, 8]], [[1, 1, 1], [0, 0, 1]], 3))
        stops = [151645, 151643]
        done = SimpleNamespace(token_ids=[4, 5], finish_reason='stop', stop_reason=None)
        self.assertEqual(rl.vllm_action_ids(done, stops), [4, 5, 151645])
        self.assertEqual(rl.vllm_action_ids(SimpleNamespace(token_ids=[4, 151643], finish_reason='stop', stop_reason=151643), stops), [4, 151643])
        self.assertEqual(rl.vllm_action_ids(SimpleNamespace(token_ids=[4], finish_reason='stop', stop_reason=151643), stops), [4, 151643])
        self.assertEqual(rl.vllm_action_ids(SimpleNamespace(token_ids=[4, 5], finish_reason='length', stop_reason=None), stops), [4, 5])
        self.assertEqual(rl.step_seed(1, 't', 0, 0, 0), rl.step_seed(1, 't', 0, 0, 0))
        self.assertEqual(len({rl.step_seed(1, 't', 0, s, k) for s in range(4) for k in range(6)}), 24)

    def test_hf_generator_returns_n_samples_per_prompt_in_row_order(self):
        class Rows(list):
            def __getitem__(self, key):
                if isinstance(key, tuple):
                    return Rows([row[key[1]] for row in self])
                return list.__getitem__(self, key)

            def tolist(self):
                return [list(row) for row in self]
        seen = {}

        def generate(**kwargs):
            seen.update(kwargs)
            width = len(kwargs['input_ids'][0])
            return Rows([[0] * width + [10 * r + 1, 151645, 151645] for r in range(len(kwargs['input_ids']) * kwargs['num_return_sequences'])])
        torch = SimpleNamespace(tensor=lambda data, device=None: data, no_grad=lambda: __import__('contextlib').nullcontext())
        tokenizer = SimpleNamespace(pad_token_id=151643, eos_token_id=151645)
        generator = self.rl.HFGenerator(SimpleNamespace(generate=generate), tokenizer, torch,
                                        __import__('contextlib').nullcontext, 'cpu', [151645, 151643], 192, lambda seed: None)
        out = generator([[1, 2, 3], [4]], 2, [11, 12])
        self.assertEqual([[g['action_ids'] for g in row] for row in out], [[[1, 151645], [11, 151645]], [[21, 151645], [31, 151645]]])
        self.assertEqual(seen['attention_mask'], [[1, 1, 1], [0, 0, 1]])
        self.assertEqual((seen['temperature'], seen['top_p'], seen['top_k'], seen['num_return_sequences']), (1.0, 1.0, 0, 2))

    def test_lockstep_and_backend_settings_are_validated(self):
        base = json.loads((PATH.parents[2] / 'configs/eval/qwen_05b_curriculum_stage_probe_v21prompt.json').read_text())
        v1 = json.loads((PATH.parents[2] / 'configs/eval/qwen_05b_curriculum_stage_probe_v1prompt.json').read_text())
        self.rl.validate_config(dict(base, rollout='lockstep', lean_workers=4, generation_backend='vllm', vllm_memory_fraction=0.4))
        for bad in (dict(base, rollout='batch'), dict(v1, rollout='lockstep'), dict(base, rollout='lockstep', lean_workers=0),
                    dict(base, generation_backend='vllm'), dict(base, rollout='lockstep', generation_backend='tgi'),
                    dict(base, rollout='lockstep', generation_backend='vllm', vllm_memory_fraction=0.95)):
            with self.assertRaises(ValueError):
                self.rl.validate_config(bad)
        vllm = dict(base, rollout='lockstep', generation_backend='vllm')
        self.rl.check_mode_settings(vllm, evaluating=True)
        with self.assertRaisesRegex(ValueError, 'evaluation-only'):
            self.rl.check_mode_settings(vllm, evaluating=False)


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
        cls.environment = __import__('rl_components.environment', fromlist=['LeanEpisode'])

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
