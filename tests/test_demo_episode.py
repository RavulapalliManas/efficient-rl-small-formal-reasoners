"""scripts/eval/demo_episode.py with a fake Lean environment and a scripted policy (no Lean, no model)."""
from contextlib import redirect_stdout
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'eval'))
import demo_episode as demo  # noqa: E402


class FakeEpisode:
    """Scripted action -> result. 'good' closes the goal; 'bad' is a self-loop; 'hang' and 'boom' end it;
    'raise' makes Lean raise; 'tactic_ok' leaves a new goal. Screen tactics: only 'omega' is accepted."""
    resets = 0

    def __init__(self, ctx, task, output, counts, version, horizon):
        self.task, self.counts, self.horizon, self.output = task, counts, horizon, output
        self.goals, self.shown, self.closed = [], [], 0

    def reset(self):
        FakeEpisode.resets += 1
        if self.output is not None and not (Path(self.output) / 'repl').is_dir():
            raise FileNotFoundError('repl log directory missing')  # The real REPL wrapper fails the same way.
        if self.task['name'] == 'broken':
            raise RuntimeError('Training root does not replay: unknown identifier')
        self.goals, self.shown = ['n : Nat |- n + 0 = n'], []

    def observation(self, omit=0):
        return f"OBS goals={self.goals} shown={self.shown}"

    def step(self, action, bonus):
        self.counts['lean_calls'] += 1
        if action in ('good', 'omega'):
            self.shown.append(action)
            self.goals = []
            return dict(status='accepted', reward=1.0, done=True, accepted=True)
        if action == 'tactic_ok':
            self.shown.append(action)
            self.goals = ['n : Nat |- True']
            return dict(status='changed', reward=0.0, done=False, accepted=False, tactics_applied=1)
        if action == 'hang':
            return dict(status='timeout', reward=0.0, done=True, accepted=False)
        if action == 'boom':
            return dict(status='crash', reward=0.0, done=True, accepted=False, censored=True)
        if action == 'raise':
            raise TimeoutError('REPL did not answer')
        return dict(status='rejected', reward=0.0, done=False, accepted=False, error='error: unknown\n tactic',
                    harness_backtrack=action == 'bad_backtrack')

    def close(self):
        self.closed += 1


class FakePolicy:
    def __init__(self, script):
        self.script, self.seeds, self.sample, self.base = script, [], -1, None

    def seed(self, value):
        self.seeds.append(value)
        self.base = value if self.base is None else self.base
        self.sample = value - self.base  # Seeds restart for each problem, like the real run.
        self.index = 0

    def act(self, env):
        actions = self.script[self.sample]
        action = actions[min(self.index, len(actions) - 1)]
        self.index += 1
        if action == 'cap':
            raise demo.PromptCap('prompt has 3000 tokens, above the cap 2048')
        if action == 'generate_fails':
            raise ValueError('CUDA out of memory')
        return action, env.observation()


def args(**kw):
    base = dict(samples=4, horizon=3, seed=100, show_observation=False, model='m', problems_dir=None, screen=False)
    base.update(kw)
    return SimpleNamespace(**base)


def step_records(trace):
    return [r for r in map(json.loads, trace.splitlines()) if r['event'] == 'step']


def attempt(policy_script, sample=0, **kw):
    out = io.StringIO()
    trace = io.StringIO()
    env = FakeEpisode(None, dict(name='p', header='theorem p (n : Nat) : n + 0 = n'), None, dict(lean_calls=0), 2, 3)
    a = args(**kw)
    with redirect_stdout(out):
        result = demo.run_attempt(env, FakePolicy(policy_script), sample, a, trace, 'p')
    return result, out.getvalue(), trace.getvalue(), env


class AttemptTests(unittest.TestCase):
    def test_proved_prints_goal_action_result_and_proof(self):
        result, text, trace, env = attempt([['tactic_ok', 'good']])
        self.assertEqual((result['outcome'], result['steps']), ('proved', 2))
        for expected in ('Goal:', 'n : Nat |- n + 0 = n', 'Step 1: model action', 'Lean', 'changed', 'Goals now:',
                         'Verified proof', 'theorem p (n : Nat) : n + 0 = n := by', '  tactic_ok', '  good',
                         'Outcome: proved'):
            self.assertIn(expected, text)
        self.assertEqual(result['proof'], 'theorem p (n : Nat) : n + 0 = n := by\n  tactic_ok\n  good')
        records = step_records(trace)
        self.assertEqual([r['step'] for r in records], [0, 1])
        self.assertTrue(records[-1]['accepted'])

    def test_not_proved_when_horizon_exhausted(self):
        result, text, _, _ = attempt([['bad']])
        self.assertEqual((result['outcome'], result['steps'], result['proof']), ('not_proved', 3, None))
        self.assertIn('Lean message: error: unknown tactic', text)
        self.assertNotIn('Verified proof', text)

    def test_harness_backtrack_is_announced(self):
        _, text, _, _ = attempt([['bad_backtrack']])
        self.assertIn('The harness reverted the last verified step.', text)

    def test_timeout_and_crash_end_attempt_gracefully(self):
        result, text, _, _ = attempt([['hang', 'good']])
        self.assertEqual((result['outcome'], result['steps']), ('timeout', 1))
        self.assertIn('Lean timed out', text)
        result, text, _, _ = attempt([['boom']])
        self.assertEqual(result['outcome'], 'crash')
        self.assertIn('REPL crashed', text)

    def test_lean_exception_is_caught_and_environment_closed(self):
        result, text, trace, env = attempt([['raise']])
        self.assertEqual(result['outcome'], 'error')
        self.assertEqual(env.closed, 1)
        self.assertEqual(step_records(trace)[0]['status'], 'error')
        self.assertIn('Lean failed during the step', text)

    def test_reset_failure_skips_attempt(self):
        env = FakeEpisode(None, dict(name='broken', header='h'), None, dict(lean_calls=0), 2, 3)
        out = io.StringIO()
        with redirect_stdout(out):
            result = demo.run_attempt(env, FakePolicy([['good']]), 0, args(), io.StringIO(), 'broken')
        self.assertEqual((result['outcome'], result['steps']), ('error', 0))
        self.assertIn('Lean setup failed', out.getvalue())

    def test_prompt_cap_and_generation_failure(self):
        self.assertEqual(attempt([['cap']])[0]['outcome'], 'prompt_cap')
        result, text, _, _ = attempt([['generate_fails']])
        self.assertEqual(result['outcome'], 'error')
        self.assertIn('Generation failed: ValueError', text)

    def test_show_observation_and_seed(self):
        _, text, trace, _ = attempt([['good']], sample=2, show_observation=True)
        self.assertIn('Prompt shown to the model at step 1', text)
        self.assertIn('OBS goals=', text)
        self.assertIn('(seed 102)', text)
        self.assertTrue(step_records(trace)[0]['observation'].startswith('OBS goals=['))
        _, quiet, _, _ = attempt([['good']])
        self.assertNotIn('Prompt shown to the model', quiet)


class RunDemoTests(unittest.TestCase):
    def test_k_over_n_and_seeds(self):
        policy = FakePolicy([['good'], ['bad'], ['hang'], ['good']])
        env = FakeEpisode(None, dict(name='p', header='theorem p'), None, dict(lean_calls=0), 2, 3)
        out = io.StringIO()
        with redirect_stdout(out):
            summary = demo.run_demo(env, policy, args(samples=4), io.StringIO(), dict(name='p'))
        self.assertEqual((summary['accepted'], summary['k_over_n']), (2, '2/4'))
        self.assertEqual(summary['outcomes'], {'proved': 2, 'not_proved': 1, 'timeout': 1})
        self.assertEqual(policy.seeds, [100, 101, 102, 103])
        self.assertEqual(len(summary['proofs']), 2)
        self.assertIn('k/n = 2/4', out.getvalue())

    def test_negative_no_success_gives_zero(self):
        policy = FakePolicy([['bad']] * 3)
        env = FakeEpisode(None, dict(name='p', header='theorem p'), None, dict(lean_calls=0), 2, 3)
        with redirect_stdout(io.StringIO()):
            summary = demo.run_demo(env, policy, args(samples=3), io.StringIO(), dict(name='p'))
        self.assertEqual((summary['accepted'], summary['proofs']), (0, []))

    def test_keyboard_interrupt_keeps_partial_summary(self):
        class Interrupting(FakePolicy):
            def act(self, env):
                if self.sample == 1:
                    raise KeyboardInterrupt
                return super().act(env)
        env = FakeEpisode(None, dict(name='p', header='theorem p'), None, dict(lean_calls=0), 2, 3)
        with redirect_stdout(io.StringIO()):
            summary = demo.run_demo(env, Interrupting([['good'], ['good']]), args(samples=4), io.StringIO(), dict(name='p'))
        self.assertTrue(summary['interrupted'])
        self.assertEqual(summary['k_over_n'], '1/1')


def normalize(text):
    """Timings differ between a live run and its replay; the text must match once they are masked."""
    return re.sub(r'\d+\.\d s', 'N s', text)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def live_trace(self, scripts, name='p', informal='n + 0 = n for a natural number n.', show_observation=False):
        """Run real run_demo code on the fakes. Return (live output, trace path, summary)."""
        policy = FakePolicy(scripts)
        env = FakeEpisode(None, dict(name=name, header=f'theorem {name} (n : Nat) : n + 0 = n'), None,
                          dict(lean_calls=0), 2, 3)
        problem = dict(name=name, informal=informal, header=f'theorem {name} (n : Nat) : n + 0 = n', stage='S0')
        path = self.root / name / 'trace.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        out = io.StringIO()
        with redirect_stdout(out), path.open('w') as trace:
            demo.show_problem(problem)
            demo.write_trace(trace, dict(event='problem', problem=problem))
            summary = demo.run_demo(env, policy, args(samples=len(scripts), show_observation=show_observation),
                                    trace, problem)
        return out.getvalue(), path, summary

    def replay(self, path, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = demo.main(['--replay', str(path), '--replay-delay', '0', *extra])
        return code, out.getvalue()

    def test_replay_text_matches_live_text(self):
        scripts = [['tactic_ok', 'good'], ['bad'], ['hang'], ['raise'], ['cap'], ['generate_fails']]
        live, path, _ = self.live_trace(scripts, show_observation=True)
        code, replayed = self.replay(path, '--show-observation')
        self.assertEqual(code, 0)
        self.assertEqual(normalize(replayed), normalize(live))

    def test_replay_needs_no_model_lean_or_output_arguments(self):
        _, path, _ = self.live_trace([['good']])
        with mock.patch.object(demo.rl, 'repl_context', side_effect=AssertionError('Lean must not start')):
            code, text = self.replay(path.parent)  # A run directory also works.
        self.assertEqual(code, 0)
        self.assertIn('Verified proof', text)

    def test_replay_does_not_write_anything(self):
        _, path, _ = self.live_trace([['good']])
        before = sorted(p.name for p in self.root.rglob('*'))
        self.replay(path)
        self.assertEqual(sorted(p.name for p in self.root.rglob('*')), before)

    def test_replay_prints_recorded_timings_and_pauses(self):
        _, path, _ = self.live_trace([['good']])
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for r in records:
            if r.get('event') == 'step':
                r.update(generated_seconds=12.3, lean_seconds=4.5)
        path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(demo.time, 'sleep') as sleep:
            demo.main(['--replay', str(path), '--replay-delay', '0.25'])
        self.assertIn('(12.3 s)', out.getvalue())
        self.assertIn('Lean (4.5 s)', out.getvalue())
        self.assertTrue(sleep.called and all(c.args == (0.25,) for c in sleep.call_args_list))

    def test_replay_batch_directory_and_exclude(self):
        self.live_trace([['good']], name='alpha')
        self.live_trace([['bad']], name='beta')
        self.live_trace([['good']], name='gamma')
        (self.root / 'skip.txt').write_text('# withdrawn\nbeta\n')
        code, text = self.replay(self.root, '--exclude', str(self.root / 'skip.txt'), '--summary-table')
        self.assertEqual(code, 0)
        self.assertIn('Skipping excluded problem beta', text)
        rows = text.split('=== Summary table')[1].splitlines()[1:]
        self.assertEqual([row.split(' | ')[0] for row in rows], ['alpha', 'gamma'])

    def test_older_trace_without_event_records_still_replays(self):
        old = self.root / 'old'
        old.mkdir()
        (old / 'trace.jsonl').write_text(json.dumps(dict(
            problem='old', sample=0, step=0, observation='o', action='good', status='accepted',
            accepted=True, done=True, reward=1.0)) + '\n')
        (old / 'summary.json').write_text(json.dumps(dict(problem=dict(name='old', informal='x', header='theorem old'))))
        code, text = self.replay(old, '--summary-table')
        self.assertEqual(code, 0)
        self.assertIn('proof text not recorded', text)
        self.assertIn('old | x | 1/1 | —', text)

    def test_screen_trace_and_empty_trace_are_refused(self):
        screen = self.root / 'screen.jsonl'
        screen.write_text(json.dumps(dict(tactic='rfl', status='rejected', accepted=False)) + '\n')
        with self.assertRaises(SystemExit):
            self.replay(screen)
        empty = self.root / 'empty.jsonl'
        empty.write_text('')
        with self.assertRaises(SystemExit):
            self.replay(empty)
        with self.assertRaises(SystemExit):
            self.replay(self.root / 'nothing_here')

    def test_replay_rejects_model_and_screen(self):
        for extra in (['--model', 'm'], ['--screen']):
            with self.assertRaises(SystemExit), mock.patch.object(sys, 'stderr', io.StringIO()):
                demo.parse_args(['--replay', 'x', *extra])


class SummaryTableTests(unittest.TestCase):
    def test_shortest_proof_prefers_fewest_tactic_lines_then_length(self):
        long_ = 'theorem t : P := by\n  a\n  b\n  c'
        short = 'theorem t : P := by\n  simp_all_the_things'
        tie = 'theorem t : P := by\n  x'
        self.assertEqual(demo.shortest_proof([long_, short, tie]), tie)
        self.assertIsNone(demo.shortest_proof([]))

    def test_rows_use_dash_when_nothing_is_proved(self):
        problem = dict(name='p', informal='Statement.')
        self.assertEqual(demo.summary_row(problem, 0, 4, [])['proof'], '—')
        row = demo.summary_row(problem, 1, 4, ['theorem p := by\n  intro n\n  omega'])
        self.assertEqual((row['k_over_n'], row['proof']), ('1/4', 'intro n; omega'))


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.problems = self.root / 'problems'
        self.problems.mkdir()
        for name, header in (('alpha', 'theorem alpha : True'), ('beta', 'theorem beta : True')):
            (self.problems / f'{name}.json').write_text(json.dumps(
                dict(name=name, informal=f'{name} is true', header=header, stage='S0')))
        self.patches = [
            mock.patch.object(demo.rl, 'LeanEpisode', FakeEpisode),
            mock.patch.object(demo.rl, 'load_checker', lambda *a: object()),
            mock.patch.object(demo.rl, 'repl_context', lambda *a: SimpleNamespace(pool_repls=[])),
            mock.patch.object(demo.rl, 'close_repl', lambda repl: None)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def argv(self, *extra, output='out'):
        base = ['--checker', 'c', '--environment', 'e', '--repl', 'r', '--output', str(self.root / output)]
        return base + list(extra)

    def run_main(self, *extra, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            code = demo.main(self.argv(*extra, **kw), policy_factory=kw.pop('factory', None) or self.factory)
        return code, out.getvalue()

    def factory(self, model, max_action_tokens, max_prompt_tokens):
        return FakePolicy([['good'], ['bad'], ['good']])

    def test_batch_mode_prints_one_line_table(self):
        code, text = self.run_main('--problems-dir', str(self.problems), '--model', 'm', '--samples', '3')
        self.assertEqual(code, 0)
        table = text.split('=== Batch summary ===')[1].strip().splitlines()
        self.assertEqual(len(table), 2)
        self.assertTrue(table[0].startswith('alpha') and 'k/n 2/3' in table[0])
        self.assertTrue(table[1].startswith('beta'))
        batch = json.loads((self.root / 'out' / 'batch_summary.json').read_text())
        self.assertEqual([b['problem']['name'] for b in batch], ['alpha', 'beta'])
        self.assertTrue((self.root / 'out' / 'alpha' / 'trace.jsonl').exists())
        self.assertNotIn('proofs', batch[0])

    def test_single_problem_layout_and_nonzero_on_error(self):
        code, _ = self.run_main('--problem', str(self.problems / 'alpha.json'), '--model', 'm', '--samples', '3')
        self.assertEqual(code, 0)
        summary = json.loads((self.root / 'out' / 'summary.json').read_text())
        self.assertEqual((summary['accepted'], summary['samples'], summary['seed']), (2, 3, 20261013))
        (self.problems / 'broken.json').write_text(json.dumps(dict(name='broken', informal='x', header='theorem broken')))
        code, text = self.run_main('--problem', str(self.problems / 'broken.json'), '--model', 'm', '--samples', '2',
                                   output='out2')
        self.assertEqual(code, 1)
        self.assertIn('Lean setup failed', text)

    def test_screen_mode_no_model(self):
        code, text = self.run_main('--problems-dir', str(self.problems), '--screen')
        self.assertEqual(code, 0)
        self.assertIn('TRIVIAL', text)  # The fake accepts `omega`, so the screen must flag it.
        record = json.loads((self.root / 'out' / 'alpha' / 'summary.json').read_text())
        self.assertEqual((record['compiles'], record['trivial'], record['solved_by']), (True, True, ['omega']))

    def test_screen_reports_non_compiling_statement(self):
        (self.problems / 'alpha.json').unlink()
        (self.problems / 'beta.json').unlink()
        (self.problems / 'broken.json').write_text(json.dumps(dict(name='broken', informal='x', header='theorem broken')))
        code, text = self.run_main('--problems-dir', str(self.problems), '--screen')
        self.assertEqual(code, 1)
        self.assertIn('does not compile', text)

    def test_argument_validation(self):
        for extra in (['--problem', 'x.json'],                                  # Neither --screen nor --model.
                      ['--problem', 'x.json', '--screen', '--model', 'm'],      # Both.
                      ['--screen']):                                            # No problem source.
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), mock.patch.object(sys, 'stderr', io.StringIO()):
                demo.main(self.argv(*extra))

    def test_bad_problem_file_fails_before_output_is_created(self):
        (self.problems / 'bad.json').write_text(json.dumps(dict(name='bad')))
        with self.assertRaises(ValueError):
            demo.main(self.argv('--problems-dir', str(self.problems), '--screen'))
        self.assertFalse((self.root / 'out').exists())

    def test_exclude_file_skips_listed_names_in_batch(self):
        exclude = self.root / 'withdrawn.txt'
        exclude.write_text('# withdrawn problems\nbeta\nnot_a_problem\n')
        code, text = self.run_main('--problems-dir', str(self.problems), '--model', 'm', '--samples', '3',
                                   '--exclude', str(exclude))
        self.assertEqual(code, 0)
        self.assertIn('names no problem in the directory', text)
        self.assertTrue((self.root / 'out' / 'alpha').exists())
        self.assertFalse((self.root / 'out' / 'beta').exists())
        self.assertNotIn('beta', text.split('=== Batch summary ===')[1])

    def test_exclude_everything_stops_before_output(self):
        exclude = self.root / 'all.txt'
        exclude.write_text('alpha\nbeta\n')
        with self.assertRaises(SystemExit):
            self.run_main('--problems-dir', str(self.problems), '--model', 'm', '--exclude', str(exclude))
        self.assertFalse((self.root / 'out').exists())

    def test_summary_table_at_end_of_live_batch_and_replay_agrees(self):
        code, text = self.run_main('--problems-dir', str(self.problems), '--model', 'm', '--samples', '3',
                                   '--summary-table')
        self.assertEqual(code, 0)
        rows = text.split('=== Summary table')[1].splitlines()[1:]
        self.assertEqual(rows[0], 'alpha | alpha is true | 2/3 | good')
        self.assertTrue(rows[1].startswith('beta | beta is true | '))
        out = io.StringIO()
        with redirect_stdout(out):
            demo.main(['--replay', str(self.root / 'out'), '--replay-delay', '0', '--summary-table'])
        self.assertEqual(out.getvalue().split('=== Summary table')[1].splitlines()[1:], rows)

    def test_summary_table_rejected_with_screen(self):
        with self.assertRaises(SystemExit), mock.patch.object(sys, 'stderr', io.StringIO()):
            demo.main(self.argv('--problems-dir', str(self.problems), '--screen', '--summary-table'))


if __name__ == '__main__':
    unittest.main()
