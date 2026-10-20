"""One-action environment contract. CPU integration uses the existing tiny PPO fixture."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class TacticPPO(unittest.TestCase):
    def driver(self):
        path = ROOT / 'scripts/train/run_tactic_ppo.py'
        self.assertTrue(path.exists(), 'One-action driver is missing')
        spec = importlib.util.spec_from_file_location('tactic_ppo', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_hook_signature(self):
        tree = ast.parse((ROOT / 'scripts/train/lean_ppo.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_ppo')
        self.assertIn('score_fn', [a.arg for a in fn.args.args], 'Explicit scorer hook is missing')

    def test_state_drift_and_ambiguous_queries_fail(self):
        driver = self.driver()
        task = dict(task_id='a', state='⊢ True')
        with self.assertRaisesRegex(ValueError, 'state'):
            driver.checked_state(SimpleNamespace(reset=lambda: '⊢ False'), task)
        class Tokenizer:
            def apply_chat_template(self, *a, **k): return 'same'
            def __call__(self, *a, **k): return dict(input_ids=[1])
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            driver.prompt_rows([task, dict(task_id='b', state='⊢ True')], Tokenizer(), 16)

    def test_actual_episode_parser_closure_and_no_repair(self):
        driver = self.driver()
        from run_tactic_rl import LeanEpisode
        task = dict(task_id='a', name='t', kind='whole', state='⊢ True', header='theorem t : True', given='')
        ctx = SimpleNamespace(timeout=1, setup_timeout=1, output=Path('/unused'),
                              checker=SimpleNamespace(guard=lambda name: ''))
        for text, response, replay, expected in [
                ('```lean\ntrivial\n```', {'proofState': 2, 'goals': []}, 'accepted', 'accepted'),
                ('trivial', {'proofState': 2, 'goals': []}, 'rejected', 'rejected'),
                ('constructor', {'proofState': 2, 'goals': ['⊢ P']}, 'accepted', 'rejected'),
                ('```lean\ntrivial', {}, 'accepted', 'rejected')]:
            episodes = []
            replies = iter([{'env': 0}, {'env': 0, 'sorries': [{'proofState': 1, 'goal': '⊢ True'}]}, response])
            def send(env, request, timeout):
                episodes.append(env)
                env.counts['lean_calls'] += 1
                return next(replies)
            verdict = dict(status=replay, lean_calls=1, lean_seconds=0.0, lean_cpu_seconds=0.0)
            with patch.object(LeanEpisode, 'send', send), patch.object(LeanEpisode, 'close'), \
                    patch('run_tactic_rl.Repl', return_value=SimpleNamespace()), \
                    patch('run_tactic_rl.verify_group', return_value=[verdict]):
                result = driver.one_action_score(ctx, {'a': task}, ['a'], [text], None)[2][0]
            self.assertEqual(result['status'], expected)
            self.assertEqual(episodes[0].max_repair_attempts, 0)
            self.assertEqual(result['lean_calls'], episodes[0].counts['lean_calls'])
            self.assertGreaterEqual(result['lean_calls'], 2)

    def test_repeated_schedule_order_and_unknown_id_refusal(self):
        driver = self.driver()
        tasks = [dict(task_id='a'), dict(task_id='b')]
        rows = [dict(task_id='a', prompt='A'), dict(task_id='b', prompt='B')]
        schedule = ['b', 'b', 'a', 'a']
        result = driver.ordered_prompt_rows(tasks, rows, 4, schedule)
        self.assertEqual([r['task_id'] for r in result], schedule)
        with self.assertRaisesRegex(ValueError, 'unknown'):
            driver.ordered_prompt_rows(tasks, rows, 4, ['a', 'b', 'x', 'a'])

    def test_censored_episode_keeps_its_status_for_the_ppo_reward_boundary(self):
        driver = self.driver()
        result = dict(status='replay_crash', accepted=False, censored=True, done=True, reward=0.0)
        env = SimpleNamespace(reset=lambda: 'goal', step=lambda *_: result, close=lambda: None)
        with patch.object(driver, 'LeanEpisode', return_value=env):
            verdict = driver.one_action_score(SimpleNamespace(output=Path('/unused')),
                {'a': dict(state='goal')}, ['a'], ['trivial'], None)[2][0]
        self.assertTrue(verdict.get('censored'))
        self.assertEqual(verdict['status'], 'replay_crash')

    def test_main_preparation_failure_closes_context(self):
        driver = self.driver()
        with TemporaryDirectory() as directory:
            base = Path(directory)
            config = dict(interaction_contract='one_action', temperature=1.0, beta=0.0, verifier_workers=1,
                prompts_per_step=1, top_p=1.0, top_k=0, repetition_penalty=1.0, use_vllm=False,
                shuffle_rewards=False, lean='pin', mathlib='revision', lean_timeout_seconds=1,
                lean_setup_timeout_seconds=1, max_prompt_tokens=16)
            files = {'config': json.dumps(config), 'pool': '{}\n', 'tasks': json.dumps(
                dict(task_id='a', name='t', kind='whole', state='⊢ True')) + '\n', 'exclude_names': ''}
            for name, text in files.items(): (base / name).write_text(text)
            (base / 'mathlib').mkdir()
            (base / 'mathlib/lean-toolchain').write_text('pin')
            args = SimpleNamespace(**{name: base / name for name in files}, model=base,
                checker=base, environment=base, mathlib_identity=base, repl=base, output=base / 'out', task_order=None)
            closed = []
            repl = SimpleNamespace(close=lambda: closed.append(True))
            ctx = SimpleNamespace(pool_repls=[repl])
            with patch.object(driver.argparse.ArgumentParser, 'parse_args', return_value=args), \
                    patch.object(driver, 'task_units'), patch.object(driver, 'mathlib_identity'), \
                    patch.object(driver, 'load_checker', return_value=SimpleNamespace(LEAN=base)), \
                    patch.object(driver.subprocess, 'run', return_value=SimpleNamespace(stdout='4.10.0-rc1')) as command_run, \
                    patch.object(driver, 'repl_context', return_value=ctx), \
                    patch('transformers.AutoTokenizer.from_pretrained'), \
                    patch.object(driver, 'prompt_rows', side_effect=ValueError('state drift')):
                with self.assertRaisesRegex(ValueError, 'state drift'): driver.main()
            command_run.assert_called_once_with([str(base / 'lean'), '--version'],
                                                check=True, capture_output=True, text=True)
            self.assertEqual(closed, [True])

    def test_tiny_ppo_uses_explicit_hook(self):
        from test_lean_ppo import LeanPPO
        import lean_ppo
        import run_lean_grpo
        fixture = LeanPPO('test_runner_stops_at_cap_and_saves_policy_critic_and_records')
        fixture.setUp()
        original, calls = lean_ppo.run_ppo, []
        def hooked(*args, **kwargs):
            def scorer(*items):
                calls.append(True)
                return run_lean_grpo.score(*items)
            return original(*args, **kwargs, score_fn=scorer)
        with patch('lean_ppo.run_ppo', side_effect=hooked):
            fixture.test_runner_stops_at_cap_and_saves_policy_critic_and_records()
        self.assertTrue(calls)
