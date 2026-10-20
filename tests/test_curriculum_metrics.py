"""Planted fixtures with known answers for scripts/eval/curriculum_metrics.py (no Lean, no model)."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'eval'))
import curriculum_metrics as cm  # noqa: E402

TASKS = [
    dict(task_id='t1:whole:0', stage='S0', family='fx', level=0),
    dict(task_id='t2:whole:0', stage='S0', family='fx', level=0),
    dict(task_id='t3:whole:0', stage='S1', family='fy', level=1),
    dict(task_id='t4:whole:0', stage='S1', family='fy', level=1),
    dict(task_id='t5:whole:0', stage='S1', family='fy', level=1),
    dict(task_id='t6:whole:0', stage='S1', family='fy', level=1),
]


def step(group, sample, index, task, status='rejected', accepted=False, tokens=3, backtrack=None):
    row = dict(group=group, sample=sample, step=index, task_id=task + ':whole:0', kind='whole',
               prompt_ids=[1, 2, 3], action_ids=[7] * tokens, tactic='x', state='s', status=status,
               reward=float(accepted), done=accepted, accepted=accepted)
    if backtrack is not None:
        row['harness_backtrack'] = backtrack
    return row


def write_run(directory, rollouts, group_records, run=None, tail=''):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'rollouts.jsonl').write_text('\n'.join(json.dumps(r) for r in rollouts) + '\n' + tail)
    (directory / 'steps.jsonl').write_text('\n'.join(json.dumps(r) for r in group_records) + '\n')
    (directory / 'run.json').write_text(json.dumps(run or dict(
        run_id='fixture', status='completed', environment_version=2, mode='training_state_assessment',
        config=dict(group_size=4),
        counts=dict(generated_tokens=999, lean_calls=50, lean_seconds=5.0, trajectories=0), wall_seconds=60.0)))


def wins(group, task, winners, size=4, base=0):
    """One group: samples in `winners` accept on step 1 after a rejected step 0; others reject twice."""
    rows = []
    for sample in range(size):
        rows.append(step(group, sample, 0, task, 'rejected'))
        rows.append(step(group, sample, 1, task, 'accepted' if sample in winners else 'rejected',
                         accepted=sample in winners))
    return rows


def group_record(group, task, k, size=4):
    return dict(group=group, task_id=task + ':whole:0', kind='whole', returns=[1.0] * k + [0.0] * (size - k),
                loss=None, gradient_norm=None, optimizer_updated=False)


def build_arm_a(root):
    rollouts, records = [], []
    rollouts += wins(0, 't1', {0, 1, 2, 3})                      # all-one
    rollouts += wins(1, 't2', set())                             # all-zero
    # Duplicate step record: a stale accepted=true line that the later line overrides to false.
    rollouts.append(step(1, 0, 1, 't2', 'accepted', accepted=True))
    rollouts.append(step(1, 0, 1, 't2', 'rejected'))
    rollouts += wins(2, 't3', set())                             # becomes k=1 through a duplicate fix
    rollouts.append(step(2, 3, 1, 't3', 'accepted', accepted=True))
    rollouts += wins(3, 't4', {0, 1})                            # mixed, k=2
    rollouts += wins(4, 't5', {0, 1})                            # censored group: must not count
    rollouts += wins(5, 't6', {0})                               # rollouts but no steps record
    records += [group_record(0, 't1', 4), group_record(1, 't2', 0), group_record(2, 't3', 1),
                group_record(3, 't4', 2), dict(group=4, status='censored', reason='prompt_cap')]
    write_run(root, rollouts, records)


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.root / 'tasks.jsonl').write_text('\n'.join(json.dumps(t) for t in TASKS) + '\n')
        self.table = cm.load_tasks(self.root / 'tasks.jsonl')

    def report(self, label, *dirs):
        return cm.arm_report(label, [cm.load_run(d, i) for i, d in enumerate(dirs)], self.table)

    def test_known_counts_incomplete_and_last_record_wins(self):
        build_arm_a(self.root / 'a')
        overall = self.report('A', self.root / 'a')['overall']
        self.assertEqual((overall['groups'], overall['attempts'], overall['accepted']), (4, 16, 7))
        self.assertEqual(overall['k_over_n'], '7/16')
        self.assertEqual((overall['mixed_groups']['k'], overall['all_zero_groups']['k'], overall['all_one_groups']['k']),
                         (2, 1, 1))
        self.assertEqual((overall['tasks_solved']['k'], overall['tasks_solved']['n']), (3, 4))
        self.assertAlmostEqual(overall['pass_at_1_macro'], (1 + 0 + 0.25 + 0.5) / 4)
        low, high = overall['pass_at_1_micro']['wilson95']
        self.assertAlmostEqual(low, 0.2314, places=3)
        self.assertAlmostEqual(high, 0.6686, places=3)
        self.assertEqual(overall['incomplete_groups'], 2)        # censored group 4 and unrecorded group 5
        self.assertEqual(overall['incomplete_attempts'], 8)
        self.assertEqual(overall['tasks_rate_in_0_to_075'], 2)    # t3 (0.25) and t4 (0.5)
        # Tokens: 4 complete groups x 4 samples x 2 steps x 3 tokens (duplicate lines replace, never add) = 96.
        self.assertEqual(overall['generated_tokens'], 96)
        self.assertEqual(overall['incomplete_tokens'], 2 * 4 * 2 * 3)
        self.assertEqual(overall['first_action_status']['rejected']['count'], 16)

    def test_stage_family_slices_and_unjoined(self):
        build_arm_a(self.root / 'a')
        report = self.report('A', self.root / 'a')
        self.assertEqual(report['by_stage']['S0']['k_over_n'], '4/8')
        self.assertEqual(report['by_stage']['S1']['k_over_n'], '3/8')
        self.assertEqual(report['by_family']['fx']['mixed_groups']['k'], 0)
        self.assertEqual(report['by_stage_family']['S1/fy']['mixed_groups']['k'], 2)
        self.assertEqual(report['unjoined_task_ids'], [])
        self.assertEqual(report['by_level']['0']['k_over_n'], '4/8')
        self.assertEqual(report['by_level']['1']['k_over_n'], '3/8')
        (self.root / 'tasks.jsonl').write_text(json.dumps(TASKS[0]) + '\n')
        partial = cm.arm_report('A', [cm.load_run(self.root / 'a', 0)], cm.load_tasks(self.root / 'tasks.jsonl'))
        self.assertIn('unknown', partial['by_stage'])
        self.assertEqual(len(partial['unjoined_task_ids']), 5)

    def test_negative_case_no_effect_no_mixed_groups(self):
        rollouts, records = [], []
        for g, task in enumerate(['t1', 't2']):
            rollouts += wins(g, task, set())
            records.append(group_record(g, task, 0))
        write_run(self.root / 'z', rollouts, records)
        overall = self.report('Z', self.root / 'z')['overall']
        self.assertEqual((overall['accepted'], overall['mixed_groups']['k'], overall['all_zero_groups']['k']), (0, 0, 2))
        self.assertEqual(overall['tasks_solved']['k'], 0)
        self.assertEqual(overall['tasks_rate_in_0_to_075'], 0)
        self.assertEqual(cm.wilson(0, 10)[0], 0.0)
        self.assertAlmostEqual(cm.wilson(0, 10)[1], 0.2775, places=4)
        self.assertEqual(cm.wilson(0, 0), (None, None))

    def test_format_error_and_harness_backtrack_counts(self):
        rows = [step(0, 0, 0, 't1', 'format_error'), step(0, 0, 1, 't1', 'partial', backtrack=True),
                step(0, 0, 2, 't1', 'rejected', backtrack=False),
                step(0, 1, 0, 't1', 'changed'), step(0, 1, 1, 't1', 'format_error'),
                step(0, 1, 2, 't1', 'accepted', accepted=True)]
        write_run(self.root / 'f', rows, [dict(group=0, task_id='t1:whole:0', kind='whole', returns=[0.0, 1.0])])
        overall = self.report('F', self.root / 'f')['overall']
        self.assertEqual(overall['format_error_first_action_share'], 0.5)
        self.assertAlmostEqual(overall['format_error_step_share'], 2 / 6)
        self.assertEqual((overall['harness_backtrack_steps'], overall['trajectories_with_harness_backtrack']), (1, 1))
        self.assertEqual(overall['trajectories_with_format_error'], 2)
        self.assertEqual((overall['accepted'], overall['attempts']), (1, 2))

    def test_sample_count_mismatch_is_incomplete(self):
        rows = wins(0, 't1', {0}, size=3)  # Three trajectories sampled, record says four returns.
        write_run(self.root / 'm', rows, [group_record(0, 't1', 1, size=4)])
        overall = self.report('M', self.root / 'm')['overall']
        self.assertEqual((overall['groups'], overall['incomplete_groups']), (0, 1))

    def test_malformed_tail_line_is_counted_not_fatal(self):
        write_run(self.root / 'x', wins(0, 't1', {0}), [group_record(0, 't1', 1)], tail='{"group": 1, "sam')
        loaded = cm.load_run(self.root / 'x', 0)
        self.assertEqual(loaded['malformed_tail_lines'], 1)
        self.assertEqual(self.report('X', self.root / 'x')['overall']['accepted'], 1)

    def test_pooling_shards_under_one_arm_keeps_groups_apart(self):
        write_run(self.root / 's1', wins(0, 't1', {0}), [group_record(0, 't1', 1)])
        write_run(self.root / 's2', wins(0, 't2', {0, 1}), [group_record(0, 't2', 2)])  # Same group index.
        overall = self.report('P', self.root / 's1', self.root / 's2')['overall']
        self.assertEqual((overall['groups'], overall['accepted'], overall['attempts']), (2, 3, 8))

    def test_exact_tests_known_values(self):
        self.assertAlmostEqual(cm.exact_two_sided(0, 8), 2 / 256)
        self.assertEqual(cm.exact_two_sided(3, 6), 1.0)
        self.assertAlmostEqual(cm.exact_two_sided(1, 6), 2 * 7 / 64)
        self.assertEqual(cm.exact_two_sided(0, 0), 1.0)

    def row(self, task, k, n=8, stage='S1'):
        return dict(task_id=task, stage=stage, family='f', k=k, n=n)

    def test_paired_positive_effect(self):
        a = [self.row(f't{i}', 2) for i in range(8)]
        b = [self.row(f't{i}', 0) for i in range(8)]
        result = cm.paired(a, b)
        self.assertEqual((result['solved_only_a'], result['solved_only_b'], result['n_paired_tasks']), (8, 0, 8))
        self.assertAlmostEqual(result['mcnemar_exact_p'], 2 / 256)
        self.assertEqual((result['sign_test']['a_higher'], result['sign_test']['b_higher']), (8, 0))
        self.assertAlmostEqual(result['sign_test']['exact_p'], 2 / 256)

    def test_paired_negative_no_difference(self):
        a = [self.row('t0', 1), self.row('t1', 0), self.row('t2', 3), self.row('t3', 0), self.row('t4', 2)]
        b = [self.row('t0', 0), self.row('t1', 1), self.row('t2', 3), self.row('t3', 0), self.row('t4', 4)]
        result = cm.paired(a, b)
        self.assertEqual((result['solved_only_a'], result['solved_only_b'], result['solved_both']), (1, 1, 2))
        self.assertEqual(result['solved_neither'], 1)
        self.assertEqual(result['mcnemar_exact_p'], 1.0)
        self.assertEqual((result['sign_test']['a_higher'], result['sign_test']['b_higher'], result['sign_test']['ties']),
                         (1, 2, 2))
        self.assertEqual(result['sign_test']['exact_p'], 1.0)

    def test_paired_uses_only_common_tasks_and_unequal_n(self):
        a = [self.row('t0', 2, n=4), self.row('t1', 1), self.row('only_a', 1)]
        b = [self.row('t0', 2, n=8), self.row('t1', 1), self.row('only_b', 1), self.row('empty', 0, n=0)]
        result = cm.paired(a, b)
        self.assertEqual((result['n_paired_tasks'], result['tasks_only_in_a'], result['tasks_only_in_b']), (2, 1, 1))
        self.assertEqual(result['sign_test']['a_higher'], 1)  # 2/4 against 2/8.
        self.assertEqual(result['sign_test']['tasks_with_unequal_n'], 1)

    def family_rows(self, plan):
        """plan: per family, (tasks, solved_a, solved_b) counts; solved tasks come first within a family."""
        a, b = [], []
        for f, (tasks, sol_a, sol_b) in enumerate(plan):
            for i in range(tasks):
                for rows, solved in ((a, sol_a), (b, sol_b)):
                    rows.append(dict(task_id=f'f{f}t{i}', stage='S1', family=f'fam{f}', level=f % 2,
                                     k=1 if i < solved else 0, n=8))
        return a, b

    def test_holm_known_values(self):
        self.assertEqual(cm.holm([]), [])
        self.assertEqual(cm.holm([0.03]), [0.03])
        for got, want in zip(cm.holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06]):
            self.assertAlmostEqual(got, want)
        self.assertEqual(cm.holm([0.4, 0.6]), [0.8, 0.8])          # Monotone step-down: 0.6 x 1 is lifted to 0.8.
        self.assertEqual(cm.holm([0.6, 0.7]), [1.0, 1.0])          # 2 x 0.6 = 1.2 is capped at 1.
        for got, want in zip(cm.holm([0.001, 0.2, 0.04, 0.5]), [0.004, 0.4, 0.12, 0.5]):
            self.assertAlmostEqual(got, want)

    def test_bootstrap_positive_effect_constant_difference(self):
        a, b = self.family_rows([(8, 8, 2)] * 12)               # A solves 8 of 8, B 2 of 8, in every family.
        boot = cm.paired(a, b)['family_bootstrap']
        self.assertEqual((boot['families'], boot['tasks']), (12, 96))
        self.assertAlmostEqual(boot['difference'], 0.75)
        self.assertEqual(boot['ci95'], [0.75, 0.75])             # Every resample gives the same ratio.

    def test_bootstrap_positive_effect_varying_across_families(self):
        a, b = self.family_rows([(8, 8, 2), (8, 8, 4)] * 6)      # Differences 6 and 4 per family.
        boot = cm.paired(a, b)['family_bootstrap']
        self.assertAlmostEqual(boot['difference'], 60 / 96)
        low, high = boot['ci95']
        self.assertTrue(0.5 < low < 60 / 96 < high < 0.75)       # Excludes 0 and brackets the estimate.

    def test_bootstrap_null_effect_interval_contains_zero(self):
        a, b = self.family_rows([(8, 5, 2), (8, 2, 5)] * 6)      # Families alternate +3 and -3 solved tasks.
        boot = cm.paired(a, b)['family_bootstrap']
        self.assertAlmostEqual(boot['difference'], 0.0)
        low, high = boot['ci95']
        self.assertTrue(low < 0 < high)
        self.assertEqual(cm.paired(a, b)['mcnemar_exact_p'], 1.0)

    def test_bootstrap_is_seeded_and_deterministic(self):
        a, b = self.family_rows([(8, 8, 2), (8, 8, 4), (8, 3, 5)] * 4)
        first = cm.paired(a, b, reps=500, seed=7)['family_bootstrap']
        self.assertEqual(first, cm.paired(a, b, reps=500, seed=7)['family_bootstrap'])
        self.assertNotEqual(first['ci95'], cm.paired(a, b, reps=500, seed=8)['family_bootstrap']['ci95'])
        self.assertEqual((first['reps'], first['seed']), (500, 7))

    def test_bootstrap_widens_interval_when_effect_sits_in_few_families(self):
        # Two families carry all 16 discordant tasks (A only): McNemar ignores families and calls it decisive,
        # but 10 of 12 families show nothing, so resamples that miss both give a zero difference.
        a, b = self.family_rows([(8, 8, 0)] * 2 + [(8, 0, 0)] * 10)
        result = cm.paired(a, b)
        self.assertLess(result['mcnemar_exact_p'], 1e-4)
        low, high = result['family_bootstrap']['ci95']
        self.assertEqual(low, 0.0)                                # P(no informative family) = (10/12)^12 = 0.11.
        self.assertGreater(high, 0.25)

    def test_bootstrap_needs_two_families(self):
        a, b = self.family_rows([(8, 4, 0)])
        boot = cm.paired(a, b)['family_bootstrap']
        self.assertEqual((boot['ci95'], boot['families']), ([None, None], 1))
        self.assertAlmostEqual(boot['difference'], 0.5)
        self.assertEqual(cm.paired([], [])['family_bootstrap']['difference'], None)

    def test_paired_report_per_level_slice(self):
        a, b = self.family_rows([(8, 8, 0), (8, 4, 4), (8, 8, 0), (8, 4, 4)])   # Even families level 0, odd level 1.
        reports = {'A': dict(by_task=a), 'B': dict(by_task=b)}
        result = cm.paired_report('A', 'B', reports, reps=200, seed=1)
        self.assertEqual(sorted(result['by_level']), ['0', '1'])
        self.assertEqual((result['by_level']['0']['solved_only_a'], result['by_level']['0']['n_paired_tasks']), (16, 16))
        self.assertEqual((result['by_level']['1']['solved_only_a'], result['by_level']['1']['solved_both']), (0, 8))
        self.assertEqual(result['by_level']['1']['mcnemar_exact_p'], 1.0)
        self.assertEqual(result['by_level']['0']['family_bootstrap']['families'], 2)

    def test_end_to_end_three_arms_holm_and_per_level(self):
        build_arm_a(self.root / 'a')
        for name in ('b', 'w'):                                   # B and W solve nothing on t1..t4.
            rows, records = [], []
            for g, task in enumerate(['t1', 't2', 't3', 't4']):
                rows += wins(g, task, set())
                records.append(group_record(g, task, 0))
            write_run(self.root / name, rows, records)
        out_json, out_text = self.root / 'out3' / 'm.json', self.root / 'out3' / 'm.txt'
        argv = ['curriculum_metrics.py', '--run', 'A=' + str(self.root / 'a'), '--run', 'B=' + str(self.root / 'b'),
                '--run', 'W=' + str(self.root / 'w'), '--tasks', str(self.root / 'tasks.jsonl'),
                '--output-json', str(out_json), '--output-text', str(out_text), '--pair', 'A', 'B',
                '--pair', 'A', 'W', '--pair', 'B', 'W', '--bootstrap-reps', '300', '--bootstrap-seed', '3']
        with mock.patch.object(sys, 'argv', argv), redirect_stdout(io.StringIO()):
            cm.main()
        data = json.loads(out_json.read_text())
        p = [pair['overall']['mcnemar_exact_p'] for pair in data['pairs']]
        self.assertEqual(p, [0.25, 0.25, 1.0])                    # 3 A-only tasks against 0; B = W.
        holm_p = [pair['holm']['mcnemar_exact_p_holm'] for pair in data['pairs']]
        for got, want in zip(holm_p, [0.75, 0.75, 1.0]):
            self.assertAlmostEqual(got, want)
        self.assertEqual(data['pairs'][0]['holm']['family_size'], 3)
        perm = [pair['overall']['family_permutation']['p'] for pair in data['pairs']]
        self.assertEqual(perm, [0.5, 0.5, 1.0])                   # Two nonzero families (T = 3), so 2 of 4 flips; B = W.
        self.assertEqual([pair['holm']['family_permutation_p_holm'] for pair in data['pairs']], [1.0, 1.0, 1.0])
        self.assertEqual((data['pairs'][0]['overall']['family_bootstrap']['reps'],
                          data['pairs'][0]['overall']['family_bootstrap']['seed']), (300, 3))
        # Level 0 holds t1 and t2 (A solves t1); level 1 holds t3 and t4 (A solves both).
        level = data['pairs'][0]['by_level']
        self.assertEqual((level['0']['solved_only_a'], level['1']['solved_only_a']), (1, 2))
        table = data['per_level']
        self.assertEqual((table['0']['A']['tasks_solved'], table['0']['A']['tasks'], table['1']['A']['tasks_solved']), (1, 2, 2))
        self.assertEqual(table['1']['W']['tasks_solved'], 0)
        text = out_text.read_text()
        self.assertIn('family-cluster bootstrap 95% CI', text)
        self.assertIn('Holm over 3 pair(s)', text)
        self.assertIn('PRIMARY: family sign-flip permutation test', text)
        self.assertIn('== Per-level', text)


    def diff_rows(self, diffs):
        """One family per entry; the entry is the family's solved(A) - solved(B) (positive: A solves tasks B does not)."""
        a, b = self.family_rows([(8, max(d, 0), max(-d, 0)) for d in diffs])
        return a, b

    def test_permutation_known_exact_values(self):
        a, b = self.diff_rows([1, 1, 1])                         # |T| = 3 occurs for 2 of 8 flips.
        perm = cm.paired(a, b)['family_permutation']
        self.assertEqual((perm['method'], perm['statistic'], perm['families']), ('exact', 3, 3))
        self.assertAlmostEqual(perm['p'], 0.25)
        a, b = self.diff_rows([6] * 12)                          # Only the two all-same flips reach |T| = 72.
        self.assertAlmostEqual(cm.paired(a, b)['family_permutation']['p'], 2 / 4096)
        a, b = self.diff_rows([3, -3] * 6)                       # T = 0: every flip is as extreme.
        self.assertEqual(cm.paired(a, b)['family_permutation']['p'], 1.0)
        self.assertEqual(cm.paired([], [])['family_permutation']['p'], 1.0)
        a, b = self.diff_rows([0] * 4)
        self.assertEqual(cm.paired(a, b)['family_permutation']['p'], 1.0)

    def test_permutation_exact_matches_brute_force_enumeration(self):
        import itertools
        diffs = [5, -2, 3, 0, 1, -4, 2, 2]
        a, b = self.diff_rows(diffs)
        observed = abs(sum(diffs))
        brute = sum(abs(sum(s * d for s, d in zip(signs, diffs))) >= observed
                    for signs in itertools.product([1, -1], repeat=len(diffs))) / 2 ** len(diffs)
        self.assertAlmostEqual(cm.paired(a, b)['family_permutation']['p'], brute)

    def test_permutation_null_is_not_anticonservative(self):
        import random
        rng = random.Random(11)
        p_values = []
        for _ in range(400):                                     # Symmetric null: each family difference is +-d.
            a, b = self.diff_rows([rng.choice([-1, 1]) * rng.randint(0, 8) for _ in range(12)])
            p_values.append(cm.family_permutation_test(
                [dict(family=r['family'], solved_a=r['k'] >= 1, solved_b=s['k'] >= 1) for r, s in zip(a, b)])['p'])
        self.assertLessEqual(sum(p <= 0.05 for p in p_values) / 400, 0.085)   # 0.05 + 3 SD at n = 400.
        self.assertGreater(sum(p_values) / 400, 0.4)                          # Large p under the null.

    def test_permutation_strong_effect_even_when_one_family_disagrees(self):
        a, b = self.diff_rows([6, 7, 5, 8, 6, 7, 5, 6, -2, 7, 6, 8])
        self.assertLess(cm.paired(a, b)['family_permutation']['p'], 0.002)

    def test_permutation_monte_carlo_matches_exact_and_is_deterministic(self):
        a, b = self.diff_rows([4, 3, 5, 1, -2, 4, 3, 0, 2, 3, -1, 4])
        table = [dict(family=r['family'], solved_a=r['k'] >= 1, solved_b=s['k'] >= 1) for r, s in zip(a, b)]
        exact = cm.family_permutation_test(table)
        forced = cm.family_permutation_test(table, flips=20000, seed=5, exact_max=0)
        self.assertEqual((exact['method'], forced['method']), ('exact', 'monte_carlo'))
        self.assertEqual(forced, cm.family_permutation_test(table, flips=20000, seed=5, exact_max=0))
        self.assertNotEqual(forced['p'], cm.family_permutation_test(table, flips=20000, seed=6, exact_max=0)['p'])
        self.assertLess(abs(forced['p'] - exact['p']), 4 * forced['mc_se'])
        self.assertAlmostEqual(forced['mc_se'], (forced['p'] * (1 - forced['p']) / 20000) ** 0.5)

    def test_permutation_uses_monte_carlo_above_16_families(self):
        a, b = self.diff_rows([5] * 20)
        perm = cm.paired(a, b, flips=2000)['family_permutation']
        self.assertEqual((perm['method'], perm['flips'], perm['families']), ('monte_carlo', 2000, 20))
        self.assertAlmostEqual(perm['p'], 1 / 2001)               # No flip reaches |T| = 100 except the identity.

    def test_permutation_resists_clustering_where_mcnemar_does_not(self):
        a, b = self.family_rows([(8, 8, 0)] * 2 + [(8, 0, 0)] * 10)   # 16 A-only tasks, all in 2 of 12 families.
        result = cm.paired(a, b)
        self.assertLess(result['mcnemar_exact_p'], 1e-4)
        self.assertAlmostEqual(result['family_permutation']['p'], 0.5)   # Two nonzero families: 2 of 4 flips reach |T| = 16.
        self.assertGreater(result['family_bootstrap']['ci95'][1], 0.25)


    def k_rows(self, families):
        """families: list of lists of (k_a, n_a, k_b, n_b) per task; returns permutation/bootstrap table rows."""
        return [dict(family=f'fam{f}', k_a=ka, n_a=na, k_b=kb, n_b=nb, solved_a=ka >= 1, solved_b=kb >= 1)
                for f, tasks in enumerate(families) for ka, na, kb, nb in tasks]

    def test_mean_k_permutation_known_values_and_differs_from_solved(self):
        # Both arms solve every task, so the solved statistic is blind; A's mean k/8 is 1.0 against B's 0.125.
        table = self.k_rows([[(8, 8, 1, 8)] * 8] * 12)
        mean_k = cm.family_permutation_test(table, statistic='mean_k')
        solved = cm.family_permutation_test(table, statistic='solved')
        self.assertEqual(solved['p'], 1.0)
        self.assertAlmostEqual(mean_k['p'], 2 / 4096)
        self.assertAlmostEqual(mean_k['statistic'], 12 * 8 * 7 / 8)           # 84 = sum of (1 - 1/8) over 96 tasks.
        self.assertAlmostEqual(mean_k['mean_difference'], 7 / 8)
        table = self.k_rows([[(4, 8, 0, 8)]] * 3)                              # Three families, each +0.5.
        self.assertAlmostEqual(cm.family_permutation_test(table, statistic='mean_k')['p'], 0.25)

    def test_mean_k_permutation_unequal_n_uses_exact_fractions(self):
        table = self.k_rows([[(2, 4, 2, 8)]] * 12)                             # 2/4 against 2/8: +0.25 per family.
        result = cm.family_permutation_test(table, statistic='mean_k')
        self.assertAlmostEqual(result['statistic'], 3.0)
        self.assertAlmostEqual(result['p'], 2 / 4096)

    def test_mean_k_permutation_matches_brute_force_and_null_is_large(self):
        import itertools
        from fractions import Fraction
        families = [[(3, 8, 1, 8), (0, 8, 2, 8)], [(1, 8, 4, 8)], [(7, 8, 0, 8), (2, 8, 2, 8)], [(0, 8, 5, 8)],
                    [(6, 8, 1, 8)], [(2, 8, 3, 8)], [(4, 8, 0, 8)], [(1, 8, 1, 8), (5, 8, 2, 8)]]
        values = [sum(Fraction(ka, na) - Fraction(kb, nb) for ka, na, kb, nb in tasks) for tasks in families]
        observed = abs(sum(values))
        brute = sum(abs(sum(sg * v for sg, v in zip(signs, values))) >= observed
                    for signs in itertools.product([1, -1], repeat=len(values))) / 2 ** len(values)
        self.assertAlmostEqual(cm.family_permutation_test(self.k_rows(families), statistic='mean_k')['p'], brute)
        symmetric = self.k_rows([[(5, 8, 1, 8)], [(1, 8, 5, 8)]] * 6)         # Opposite families cancel: T = 0.
        self.assertEqual(cm.family_permutation_test(symmetric, statistic='mean_k')['p'], 1.0)

    def test_mean_k_permutation_size_under_clustering(self):
        import random
        rng = random.Random(5)
        rejected, p_values = 0, []
        for _ in range(400):                                                   # Symmetric family effects, 12 families.
            families = []
            for _ in range(12):
                d = rng.choice([-1, 1]) * rng.randint(0, 6)
                families.append([(max(d, 0), 8, max(-d, 0), 8)] * 2)
            p = cm.family_permutation_test(self.k_rows(families), statistic='mean_k')['p']
            p_values.append(p)
            rejected += p <= 0.05
        self.assertLessEqual(rejected / 400, 0.085)
        self.assertGreater(sum(p_values) / 400, 0.4)

    def test_mean_k_permutation_monte_carlo_and_determinism(self):
        table = self.k_rows([[(a, 8, b, 8)] for a, b in
                             [(5, 1), (3, 1), (6, 2), (2, 2), (4, 0), (1, 3), (7, 2), (3, 0), (2, 1), (5, 4), (4, 3), (6, 1)]])
        exact = cm.family_permutation_test(table, statistic='mean_k')
        forced = cm.family_permutation_test(table, flips=20000, seed=2, exact_max=0, statistic='mean_k')
        self.assertEqual((exact['method'], forced['method']), ('exact', 'monte_carlo'))
        self.assertLess(abs(forced['p'] - exact['p']), 4 * forced['mc_se'])
        self.assertEqual(forced, cm.family_permutation_test(table, flips=20000, seed=2, exact_max=0, statistic='mean_k'))

    def test_mean_k_bootstrap_constant_and_paired_keys(self):
        table = self.k_rows([[(6, 8, 2, 8)] * 8] * 12)
        boot = cm.family_bootstrap_ci(table, statistic='mean_k')
        self.assertEqual((boot['statistic'], boot['families'], boot['tasks']), ('mean_k', 12, 96))
        self.assertAlmostEqual(boot['difference'], 0.5)
        self.assertEqual(boot['ci95'], [0.5, 0.5])
        a, b = self.family_rows([(8, 8, 2)] * 12)
        result = cm.paired(a, b)
        for key in ('family_permutation', 'family_permutation_mean_k', 'family_bootstrap', 'family_bootstrap_mean_k'):
            self.assertIn(key, result)
        self.assertAlmostEqual(result['family_bootstrap_mean_k']['difference'], 6 / 64)   # k is 1 or 0 of 8 here.

    def run_cli(self, argv):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['curriculum_metrics.py'] + argv), redirect_stdout(out):
            cm.main()
        return out.getvalue()

    def test_eval_sets_side_by_side_with_holm_in_primary_only(self):
        build_arm_a(self.root / 'a')
        for name in ('b', 'w'):
            rows, records = [], []
            for g, task in enumerate(['t1', 't2', 't3', 't4']):
                rows += wins(g, task, set())
                records.append(group_record(g, task, 0))
            write_run(self.root / name, rows, records)
        r = lambda name: str(self.root / name)
        out_json, out_text = self.root / 'sets' / 'm.json', self.root / 'sets' / 'm.txt'
        self.run_cli(['--eval-set', 'held_out', '--run', 'W=' + r('w'), '--run', 'A=' + r('a'), '--run', 'B=' + r('b'),
                      '--eval-set', 'id_val', '--run', 'W=' + r('w'), '--run', 'A=' + r('a'),
                      '--tasks', str(self.root / 'tasks.jsonl'), '--pair', 'A', 'B', '--pair', 'A', 'W', '--pair', 'B', 'W',
                      '--bootstrap-reps', '100', '--output-json', str(out_json), '--output-text', str(out_text)])
        data = json.loads(out_json.read_text())
        self.assertNotIn('arms', data)
        self.assertEqual((data['primary_eval_set'], sorted(data['eval_sets'])), ('held_out', ['held_out', 'id_val']))
        held, val = data['eval_sets']['held_out'], data['eval_sets']['id_val']
        self.assertEqual([(p['a'], p['b']) for p in held['pairs']], [('A', 'B'), ('A', 'W'), ('B', 'W')])
        self.assertTrue(all('holm' in p for p in held['pairs']))
        self.assertEqual([(p['a'], p['b']) for p in val['pairs']], [('A', 'W')])
        self.assertNotIn('holm', val['pairs'][0])                    # Secondary set: no Holm.
        self.assertEqual([(s['a'], s['b']) for s in val['skipped_pairs']], [('A', 'B'), ('B', 'W')])
        self.assertEqual(held['arms']['A']['overall']['k_over_n'], val['arms']['A']['overall']['k_over_n'])
        # A against W: t1 (k=4), t3 (k=1), t4 (k=2) over 8 samples of 4 each; mean k/n difference per task.
        mean_k = held['pairs'][1]['overall']['family_permutation_mean_k']
        self.assertAlmostEqual(mean_k['statistic'], (4 + 1 + 2 + 0) / 4)
        summary = data['eval_set_summary']
        self.assertEqual([(row['eval_set'], row['a'], row['b']) for row in summary],
                         [('held_out', 'A', 'B'), ('held_out', 'A', 'W'), ('held_out', 'B', 'W'), ('id_val', 'A', 'W')])
        self.assertIsNotNone(summary[0]['mean_k_perm_p_holm'])
        self.assertIsNone(summary[3]['mean_k_perm_p_holm'])
        text = out_text.read_text()
        self.assertIn('Eval set held_out (PRIMARY, Holm applied)', text)
        self.assertIn('Eval set id_val (secondary, no Holm)', text)
        self.assertIn('Side by side', text)
        self.assertIn('PRIMARY: family sign-flip permutation test on mean k/n', text)

    def test_eval_set_argument_errors(self):
        r = str(self.root / 'a')
        build_arm_a(self.root / 'a')
        base = ['--tasks', str(self.root / 'tasks.jsonl'), '--output-json', str(self.root / 'e.json')]
        with mock.patch('sys.stderr', io.StringIO()):
            with self.assertRaises(SystemExit):                      # A --run before any --eval-set.
                self.run_cli(['--run', 'A=' + r, '--eval-set', 'x', '--run', 'A=' + r] + base)
            with self.assertRaises(SystemExit):                      # Unknown primary set.
                self.run_cli(['--eval-set', 'x', '--run', 'A=' + r, '--primary-eval-set', 'y'] + base)


    def test_end_to_end_cli_pair_and_text(self):
        build_arm_a(self.root / 'a')
        # Arm B solves nothing on the same tasks.
        rows, records = [], []
        for g, task in enumerate(['t1', 't2', 't3', 't4']):
            rows += wins(g, task, set())
            records.append(group_record(g, task, 0))
        write_run(self.root / 'b', rows, records)
        out_json, out_text = self.root / 'out' / 'm.json', self.root / 'out' / 'm.txt'
        argv = ['curriculum_metrics.py', '--run', 'A=' + str(self.root / 'a'), '--run', 'B=' + str(self.root / 'b'),
                '--tasks', str(self.root / 'tasks.jsonl'), '--output-json', str(out_json),
                '--output-text', str(out_text), '--pair', 'A', 'B']
        with mock.patch.object(sys, 'argv', argv), redirect_stdout(io.StringIO()):
            cm.main()
        data = json.loads(out_json.read_text())
        pair = data['pairs'][0]['overall']
        self.assertEqual((pair['n_paired_tasks'], pair['solved_only_a'], pair['solved_only_b']), (4, 3, 0))
        self.assertAlmostEqual(pair['mcnemar_exact_p'], 0.25)
        text = out_text.read_text()
        self.assertIn('== Arm A ==', text)
        self.assertIn('7/16', text)
        self.assertIn('Paired A (A) vs B (B)', text)


if __name__ == '__main__':
    unittest.main()
