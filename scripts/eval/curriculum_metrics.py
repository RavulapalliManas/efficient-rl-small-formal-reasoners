"""Curriculum metrics from run_tactic_rl.py output directories (assessment or training).

Reads run.json, rollouts.jsonl and steps.jsonl of each directory, line by line (rollouts.jsonl holds
prompt token IDs, so it is never loaded as one object). Standard library only; runs on python3.10.

Definitions (all counted from the files):
- Step record: one rollouts.jsonl line, keyed (run, group, sample, step). Duplicates keep the LAST line.
- Trajectory: (run, group, sample). Accepted = any step with accepted true.
- Group: (run, group), the G samples on one task. A group is COMPLETE when steps.jsonl holds a
  non-censored record for it whose `returns` length equals the number of sampled trajectories.
  Every other group is INCOMPLETE (censored, in progress, or inconsistent). Incomplete groups are
  excluded from every rate and listed with their reason.
- Mixed group: 0 < accepted < G. All-zero: accepted = 0. All-one: accepted = G.
- pass@1 (micro): accepted trajectories / trajectories. pass@1 (macro): mean over tasks of k/n.
  Wilson 95% intervals treat trajectories (or groups) as independent; trajectories of one task are
  clustered, so the interval is optimistic for pooled-over-task rates.
- Tokens: sum of len(action_ids). Lean calls and wall seconds are run-level only: no per-step
  record carries them (only replay.lean_calls on full-proof replays).
- Task join: tasks.jsonl rows (task_id, stage, family, level). Unjoined task IDs get stage "unknown".
- Paired mode: tasks with n > 0 in both arms. Exact two-sided McNemar on solved (k >= 1); exact
  two-sided sign test on per-task k (compared as k/n by cross-multiplication; ties dropped).
- Family-cluster bootstrap: the paired difference solved(A) - solved(B) is the sum over paired tasks of
  (solved_a - solved_b) divided by the paired task count. Each replicate resamples whole families (with
  replacement, family = the tasks.jsonl `family` field) and recomputes the ratio. The 95% interval is the
  percentile interval (sorted replicates at ranks round(0.025 (R-1)) and round(0.975 (R-1))). Seeded per
  slice with random.Random(seed); defaults R = 2000, seed = 0. With fewer than 2 families the interval is null.
  With 12 families the percentile interval is anti-conservative (simulated size about 0.08 at 0.05).
- Family sign-flip permutation test, on two statistics. PRIMARY: `family_permutation_mean_k`, T = sum over
  families of the paired difference in mean k/n (pass@1 estimate: per task k_a/n_a - k_b/n_b). Secondary:
  `family_permutation` (pass@8), T = sum over families of the paired difference in solved count
  (solved_a - solved_b). `family_bootstrap_mean_k` and `family_bootstrap` give the matching CIs. Under the null each family's difference flips sign with
  probability 1/2 independently. Two-sided p = P(|T*| >= |T|) over sign flips. With <= 16 families the
  distribution is enumerated exactly (a dynamic programme over all 2^F flips). With more families, 100,000
  seeded random flips give p = (1 + #{|T*| >= |T|}) / (1 + 100000) and mc_se = sqrt(p (1 - p) / 100000).
  Simulated size is <= 0.05 under family clustering, unlike McNemar and the sign test.
- Holm correction: step-down Holm over the m --pair contrasts, applied separately to the overall permutation,
  McNemar and sign-test p-values (key `holm` in each pair; stage and level slices are descriptive).
- Eval sets: `--eval-set NAME` labels every following `--run` (held_out, id_val, retention, external, ...).
  Each set gets its own arms, pairs and tests; `--pair` applies to every set that has both arms. Holm runs only
  within the primary set (`--primary-eval-set`, default the first). JSON then holds `eval_sets` and
  `eval_set_summary`; without `--eval-set` the original layout (`arms`, `pairs`, `per_level`) is unchanged.
- Per-level tables: `per_level` (level x arm) and the paired `by_level` slice, both in JSON and in the text.

Usage:
  curriculum_metrics.py --run ARM=DIR [--run ARM=DIR ...] --tasks tasks.jsonl [tasks2.jsonl ...] --output-json OUT.json
      [--output-text OUT.txt] [--pair ARM_A ARM_B ...] [--bootstrap-reps 2000] [--bootstrap-seed 0] [--permutation-flips 100000]
Several DIR with one ARM label pool together (for example three stage shards).
Memory: one step record at a time plus one small dict per step (no prompt_ids kept); well under 1 GiB
for 10^5 steps. Writes only to --output-json and --output-text.
"""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
from math import comb, lcm, sqrt
import json
from pathlib import Path
import random

Z95 = 1.959964


def wilson(k, n, z=Z95):
    """Wilson score interval for k successes in n trials; (None, None) when n = 0."""
    if n <= 0:
        return None, None
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def exact_two_sided(smaller, total):
    """Two-sided exact binomial p-value at p = 1/2: min(1, 2 P(X <= smaller))."""
    if total == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(total, i) for i in range(smaller + 1)) / 2 ** total)


def holm(p_values):
    """Holm step-down adjusted p-values, returned in input order; each is capped at 1."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted, running = [None] * len(p_values), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


STATISTICS = ('solved', 'mean_k')


def task_difference(row, statistic):
    """Per-task paired difference A - B: solved indicator ('solved', pass@8) or k/n ('mean_k', pass@1 estimate)."""
    if statistic == 'solved':
        return Fraction(int(row['solved_a']) - int(row['solved_b']))
    return Fraction(row['k_a'], row['n_a']) - Fraction(row['k_b'], row['n_b'])


def family_sums(table, statistic):
    """Per-family sum of paired task differences; returns ({family: Fraction sum}, tasks)."""
    sums = defaultdict(Fraction)
    for row in table:
        sums[row['family']] += task_difference(row, statistic)
    return sums, len(table)


def family_bootstrap_ci(table, reps=2000, seed=0, statistic='solved'):
    """Family-cluster bootstrap of the mean paired difference over paired-task rows (see module docstring)."""
    sums, tasks = family_sums(table, statistic)
    counts = Counter(row['family'] for row in table)
    blocks = [(float(sums[f]), counts[f]) for f in sorted(sums, key=str)]
    result = dict(statistic=statistic, difference=ratio(sum(d for d, _ in blocks), tasks), ci95=[None, None],
                  families=len(blocks), tasks=tasks, reps=reps, seed=seed)
    if len(blocks) < 2:
        return result
    rng = random.Random(seed)
    stats = []
    for _ in range(reps):
        draw = [blocks[rng.randrange(len(blocks))] for _ in blocks]
        stats.append(sum(d for d, _ in draw) / sum(count for _, count in draw))
    stats.sort()
    result['ci95'] = [stats[round(0.025 * (reps - 1))], stats[round(0.975 * (reps - 1))]]
    return result


def family_permutation_test(table, flips=100000, seed=0, exact_max=16, statistic='solved'):
    """Two-sided family sign-flip permutation test on the paired difference (see module docstring)."""
    sums, tasks = family_sums(table, statistic)
    values = [sums[f] for f in sorted(sums, key=str)]
    scale = lcm(*[v.denominator for v in values]) if values else 1  # Integer units: 1 for solved, 1/n for mean_k.
    integers = [int(v * scale) for v in values]
    total = sum(values)
    observed = abs(sum(integers))
    result = dict(statistic_name=statistic, statistic=int(total) if total.denominator == 1 else float(total),
                  mean_difference=ratio(float(total), tasks), families=len(values), tasks=tasks, method='exact',
                  flips=2 ** len(values), p=1.0, mc_se=None, seed=seed)
    nonzero = [abs(v) for v in integers if v]
    if not nonzero:
        return result
    if len(values) <= exact_max:
        distribution = {0: 1}  # Flips with total t, over the nonzero families (zeros flip without effect).
        for v in nonzero:
            step = defaultdict(int)
            for t, count in distribution.items():
                step[t + v] += count
                step[t - v] += count
            distribution = step
        result['p'] = sum(c for t, c in distribution.items() if abs(t) >= observed) / 2 ** len(nonzero)
        return result
    rng = random.Random(seed)
    extreme = 0
    for _ in range(flips):
        bits = rng.getrandbits(len(nonzero))
        extreme += abs(sum(v if bits >> i & 1 else -v for i, v in enumerate(nonzero))) >= observed
    p = (1 + extreme) / (1 + flips)
    result.update(method='monte_carlo', flips=flips, p=p, mc_se=sqrt(p * (1 - p) / flips))
    return result


def ratio(a, b):
    return a / b if b else None


def read_jsonl(path):
    """Parse a JSONL file; a malformed final line (a file still being written) is counted, not fatal."""
    rows, bad = [], 0
    lines = Path(path).read_text(encoding='utf-8').splitlines() if Path(path).exists() else []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise
            bad += 1
    return rows, bad


def iter_jsonl(path):
    """Yield (row, is_bad_tail) one line at a time."""
    with open(path, encoding='utf-8') as stream:
        pending = None
        for line in stream:
            if pending is not None:
                yield json.loads(pending)  # Not the last line: malformed means corrupt.
                pending = None
            if line.strip():
                pending = line
        if pending is not None:
            try:
                yield json.loads(pending)
            except json.JSONDecodeError:
                yield None


def load_run(directory, run_index):
    """Return step records (deduplicated, last wins), group records and run.json of one directory."""
    directory = Path(directory)
    run = json.loads((directory / 'run.json').read_text()) if (directory / 'run.json').exists() else {}
    steps, duplicates, bad_tail = {}, 0, 0
    path = directory / 'rollouts.jsonl'
    if path.exists():
        for row in iter_jsonl(path):
            if row is None:
                bad_tail += 1
                continue
            key = (row['group'], row['sample'], row['step'])
            duplicates += key in steps
            steps[key] = dict(
                task_id=row['task_id'], kind=row.get('kind'), status=row.get('status'),
                accepted=bool(row.get('accepted')), tokens=len(row.get('action_ids') or []),
                backtrack=bool(row.get('harness_backtrack')),
                replay_calls=(row.get('replay') or {}).get('lean_calls', 0) or 0,
                replay_seconds=(row.get('replay') or {}).get('lean_seconds', 0.0) or 0.0)
    group_records = {}
    path = directory / 'steps.jsonl'
    if path.exists():
        for row in iter_jsonl(path):
            if row is None:
                bad_tail += 1
                continue
            group_records[row['group']] = row  # Last wins.
    return dict(index=run_index, directory=str(directory), run=run, steps=steps, group_records=group_records,
                duplicate_step_records=duplicates, malformed_tail_lines=bad_tail)


def build_trajectories_and_groups(loaded):
    """Return (trajectories, groups). Trajectory and group dicts carry the run index in their keys."""
    by_trajectory = defaultdict(list)
    for (group, sample, step), record in loaded['steps'].items():
        by_trajectory[(group, sample)].append((step, record))
    trajectories = {}
    for (group, sample), items in by_trajectory.items():
        items.sort(key=lambda item: item[0])
        records = [record for _, record in items]
        trajectories[(loaded['index'], group, sample)] = dict(
            task_id=records[0]['task_id'], accepted=any(r['accepted'] for r in records),
            first_status=records[0]['status'], last_status=records[-1]['status'], steps=len(records),
            format_errors=sum(r['status'] == 'format_error' for r in records),
            backtracks=sum(r['backtrack'] for r in records), tokens=sum(r['tokens'] for r in records),
            replay_calls=sum(r['replay_calls'] for r in records),
            replay_seconds=sum(r['replay_seconds'] for r in records))
    groups = {}
    sampled = defaultdict(list)
    for (run_index, group, sample), trajectory in trajectories.items():
        if run_index == loaded['index']:
            sampled[group].append(trajectory)
    for group in sorted(set(sampled) | set(loaded['group_records'])):
        record = loaded['group_records'].get(group)
        members = sampled.get(group, [])
        task_id = members[0]['task_id'] if members else (record or {}).get('task_id')
        if record is None:
            reason = 'no_steps_record'
        elif record.get('status') == 'censored':
            reason = 'censored:' + str(record.get('reason'))
        elif len(record.get('returns', [])) != len(members):
            reason = 'sample_count_mismatch'
        else:
            reason = None
        successes = sum(t['accepted'] for t in members)
        groups[(loaded['index'], group)] = dict(
            run=loaded['index'], group=group, task_id=task_id, n=len(members), k=successes,
            complete=reason is None, reason=reason, tokens=sum(t['tokens'] for t in members))
    return trajectories, groups


def summarize(groups, trajectories):
    """Rates over COMPLETE groups only; incomplete groups are counted and their tokens reported apart."""
    complete = [g for g in groups if g['complete']]
    incomplete = [g for g in groups if not g['complete']]
    members = [t for t in trajectories if t['complete']]
    attempts, accepted = sum(g['n'] for g in complete), sum(g['k'] for g in complete)
    per_task = defaultdict(lambda: [0, 0])
    for g in complete:
        per_task[g['task_id']][0] += g['k']
        per_task[g['task_id']][1] += g['n']
    n_groups = len(complete)
    mixed = sum(0 < g['k'] < g['n'] for g in complete)
    zero = sum(g['k'] == 0 for g in complete)
    full = sum(g['k'] == g['n'] and g['n'] > 0 for g in complete)
    solved = sum(k >= 1 for k, _ in per_task.values())
    first = Counter(t['first_status'] for t in members)
    steps = sum(t['steps'] for t in members)
    format_steps = sum(t['format_errors'] for t in members)

    def interval(k, n):
        low, high = wilson(k, n)
        return dict(k=k, n=n, rate=ratio(k, n), wilson95=[low, high])

    return dict(
        tasks=len(per_task), groups=n_groups, attempts=attempts, accepted=accepted,
        k_over_n=f'{accepted}/{attempts}', pass_at_1_micro=interval(accepted, attempts),
        pass_at_1_macro=ratio(sum(k / n for k, n in per_task.values() if n), len(per_task)),
        tasks_solved=interval(solved, len(per_task)),
        tasks_rate_in_0_to_075=sum(0 < k / n <= 0.75 for k, n in per_task.values() if n),
        mixed_groups=interval(mixed, n_groups), all_zero_groups=interval(zero, n_groups),
        all_one_groups=interval(full, n_groups),
        generated_tokens=sum(t['tokens'] for t in members),
        replay_lean_calls=sum(t['replay_calls'] for t in members),
        replay_lean_seconds=sum(t['replay_seconds'] for t in members),
        incomplete_groups=len(incomplete), incomplete_attempts=sum(g['n'] for g in incomplete),
        incomplete_tokens=sum(g['tokens'] for g in incomplete),
        first_action_status={s: dict(count=c, share=ratio(c, len(members))) for s, c in sorted(first.items(), key=str)},
        format_error_first_action_share=ratio(first.get('format_error', 0), len(members)),
        format_error_step_share=ratio(format_steps, steps),
        trajectories_with_format_error=sum(t['format_errors'] > 0 for t in members),
        harness_backtrack_steps=sum(t['backtracks'] for t in members),
        trajectories_with_harness_backtrack=sum(t['backtracks'] > 0 for t in members),
        steps=steps)


def load_tasks(*paths):
    table = {}
    for path in paths:
        rows, _ = read_jsonl(path)
        for row in rows:
            table[row['task_id']] = dict(stage=row.get('stage', 'unknown'), family=row.get('family', 'unknown'),
                                         level=row.get('level'))
    return table


def arm_report(label, loaded_runs, task_table):
    trajectories, groups = {}, {}
    for loaded in loaded_runs:
        t, g = build_trajectories_and_groups(loaded)
        trajectories.update(t)
        groups.update(g)
    unknown = set()

    def meta(task_id):
        if task_id not in task_table:
            unknown.add(task_id)
        return task_table.get(task_id, dict(stage='unknown', family='unknown', level=None))

    group_rows = []
    for key, g in groups.items():
        m = meta(g['task_id'])
        group_rows.append(dict(g, stage=m['stage'], family=m['family'], level=m['level']))
    complete_of = {(g['run'], g['group']): g['complete'] for g in group_rows}
    trajectory_rows = [dict(t, complete=complete_of.get((key[0], key[1]), False),
                            stage=meta(t['task_id'])['stage'], family=meta(t['task_id'])['family'],
                            level=meta(t['task_id'])['level'])
                       for key, t in trajectories.items()]

    def select(rows, **match):
        return [r for r in rows if all(r[k] == v for k, v in match.items())]

    report = dict(arm=label, directories=[r['directory'] for r in loaded_runs])
    report['runs'] = [dict(directory=r['directory'], run_id=r['run'].get('run_id'), status=r['run'].get('status'),
                           stop_reason=r['run'].get('stop_reason'), mode=r['run'].get('mode'),
                           environment_version=r['run'].get('environment_version'),
                           group_size=(r['run'].get('config') or {}).get('group_size'),
                           counts=r['run'].get('counts'), wall_seconds=r['run'].get('wall_seconds'),
                           duplicate_step_records=r['duplicate_step_records'],
                           malformed_tail_lines=r['malformed_tail_lines']) for r in loaded_runs]
    counts = [r['counts'] for r in report['runs'] if r['counts']]
    report['run_level_totals'] = dict(
        generated_tokens=sum(c['generated_tokens'] for c in counts), lean_calls=sum(c['lean_calls'] for c in counts),
        lean_seconds=sum(c['lean_seconds'] for c in counts),
        wall_seconds=sum(r['wall_seconds'] or 0.0 for r in report['runs']))
    report['unjoined_task_ids'] = sorted(t for t in unknown if t)
    report['overall'] = summarize(group_rows, trajectory_rows)
    report['by_stage'] = {s: summarize(select(group_rows, stage=s), select(trajectory_rows, stage=s))
                          for s in sorted({g['stage'] for g in group_rows})}
    report['by_family'] = {f: summarize(select(group_rows, family=f), select(trajectory_rows, family=f))
                           for f in sorted({g['family'] for g in group_rows})}
    report['by_level'] = {str(v): summarize(select(group_rows, level=v), select(trajectory_rows, level=v))
                          for v in sorted({g['level'] for g in group_rows}, key=str)}
    report['by_stage_family'] = {
        f'{s}/{f}': summarize(select(group_rows, stage=s, family=f), select(trajectory_rows, stage=s, family=f))
        for s, f in sorted({(g['stage'], g['family']) for g in group_rows})}
    per_task = defaultdict(lambda: dict(k=0, n=0, groups=0, mixed=0, tokens=0))
    for g in group_rows:
        if g['complete']:
            row = per_task[g['task_id']]
            row['k'] += g['k']
            row['n'] += g['n']
            row['groups'] += 1
            row['mixed'] += 0 < g['k'] < g['n']
            row['tokens'] += g['tokens']
    report['by_task'] = [dict(task_id=t, **meta(t), **row, pass_at_1=ratio(row['k'], row['n']),
                              solved=row['k'] >= 1) for t, row in sorted(per_task.items())]
    report['incomplete_group_list'] = [dict(run=g['run'], group=g['group'], task_id=g['task_id'], n=g['n'],
                                            reason=g['reason']) for g in group_rows if not g['complete']]
    return report


def paired(rows_a, rows_b, reps=2000, seed=0, flips=100000):
    """Paired comparison on tasks with n > 0 in both arms."""
    a = {r['task_id']: r for r in rows_a if r['n'] > 0}
    b = {r['task_id']: r for r in rows_b if r['n'] > 0}
    common = sorted(a.keys() & b.keys())
    only_a, only_b, both, neither = [], [], [], []
    wins_a = wins_b = ties = unequal_n = 0
    table = []
    for t in common:
        ra, rb = a[t], b[t]
        sa, sb = ra['k'] >= 1, rb['k'] >= 1
        (both if sa and sb else only_a if sa else only_b if sb else neither).append(t)
        left, right = ra['k'] * rb['n'], rb['k'] * ra['n']  # Compare k/n without division.
        wins_a += left > right
        wins_b += right > left
        ties += left == right
        unequal_n += ra['n'] != rb['n']
        table.append(dict(task_id=t, stage=ra['stage'], family=ra['family'], level=ra.get('level'), k_a=ra['k'], n_a=ra['n'],
                          k_b=rb['k'], n_b=rb['n'], solved_a=sa, solved_b=sb))
    return dict(
        n_paired_tasks=len(common), tasks_only_in_a=len(a.keys() - b.keys()), tasks_only_in_b=len(b.keys() - a.keys()),
        solved_both=len(both), solved_only_a=len(only_a), solved_only_b=len(only_b), solved_neither=len(neither),
        mcnemar_exact_p=exact_two_sided(min(len(only_a), len(only_b)), len(only_a) + len(only_b)),
        sign_test=dict(a_higher=wins_a, b_higher=wins_b, ties=ties, n_nonties=wins_a + wins_b,
                       exact_p=exact_two_sided(min(wins_a, wins_b), wins_a + wins_b), tasks_with_unequal_n=unequal_n),
        family_bootstrap=family_bootstrap_ci(table, reps, seed),
        family_bootstrap_mean_k=family_bootstrap_ci(table, reps, seed, 'mean_k'),
        family_permutation=family_permutation_test(table, flips, seed),
        family_permutation_mean_k=family_permutation_test(table, flips, seed, statistic='mean_k'), per_task=table)


def apply_holm(pairs):
    """Add `holm` (family size m = number of pairs) to each pair; corrects the overall slices only."""
    permutation_mean_k = holm([p['overall']['family_permutation_mean_k']['p'] for p in pairs])
    permutation = holm([p['overall']['family_permutation']['p'] for p in pairs])
    mcnemar = holm([p['overall']['mcnemar_exact_p'] for p in pairs])
    sign = holm([p['overall']['sign_test']['exact_p'] for p in pairs])
    for pair, k_adj, f_adj, m_adj, s_adj in zip(pairs, permutation_mean_k, permutation, mcnemar, sign):
        pair['holm'] = dict(family_size=len(pairs), family_permutation_mean_k_p_holm=k_adj,
                            family_permutation_p_holm=f_adj, mcnemar_exact_p_holm=m_adj, sign_exact_p_holm=s_adj)


def per_level_table(reports):
    """Level x arm table of tasks, tasks solved and micro pass@1, from each arm's by_level summaries."""
    levels = sorted({lv for r in reports.values() for lv in r['by_level']}, key=str)
    return {lv: {label: dict(tasks=r['by_level'][lv]['tasks'], tasks_solved=r['by_level'][lv]['tasks_solved']['k'],
                             k_over_n=r['by_level'][lv]['k_over_n'], pass_at_1=r['by_level'][lv]['pass_at_1_micro']['rate'])
                 for label, r in reports.items() if lv in r['by_level']} for lv in levels}


def paired_report(label_a, label_b, reports, reps=2000, seed=0, flips=100000):
    a, b = reports[label_a]['by_task'], reports[label_b]['by_task']
    result = dict(a=label_a, b=label_b, overall=paired(a, b, reps, seed, flips), by_stage={}, by_level={})
    for stage in sorted({r['stage'] for r in a + b}):
        result['by_stage'][stage] = paired([r for r in a if r['stage'] == stage], [r for r in b if r['stage'] == stage],
                                           reps, seed, flips)
    for level in sorted({r['level'] for r in a + b}, key=str):
        result['by_level'][str(level)] = paired([r for r in a if r['level'] == level], [r for r in b if r['level'] == level],
                                                reps, seed, flips)
    return result


def pct(x):
    return '   n/a' if x is None else f'{100 * x:5.1f}%'


def interval_text(block):
    low, high = block['wilson95']
    return f"{pct(block['rate'])} [{pct(low).strip()},{pct(high).strip()}]"


def text_table(headers, rows):
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda row: '  '.join(str(x).ljust(w) for x, w in zip(row, widths)).rstrip()
    return '\n'.join([line(headers), line(['-' * w for w in widths])] + [line(r) for r in rows])


def summary_row(name, s):
    return [name, s['tasks'], s['groups'], s['attempts'], s['k_over_n'], interval_text(s['pass_at_1_micro']),
            f"{s['tasks_solved']['k']}/{s['tasks_solved']['n']}", pct(s['mixed_groups']['rate']),
            pct(s['all_zero_groups']['rate']), pct(s['all_one_groups']['rate']), s['generated_tokens'],
            s['incomplete_groups'], pct(s['format_error_first_action_share']), s['harness_backtrack_steps']]


SUMMARY_HEADERS = ['arm/slice', 'tasks', 'groups', 'traj', 'k/n', 'pass@1 [95% Wilson]', 'solved', 'mixed',
                   'all0', 'all1', 'tokens', 'incompl', 'fmt1st', 'hbt']


def render_text(reports, pairs, per_level=None):
    out = []
    for label, report in reports.items():
        out.append(f'== Arm {label} ==')
        for run in report['runs']:
            out.append(f"run {run['run_id']} status={run['status']} stop={run['stop_reason']} v={run['environment_version']} "
                       f"G={run['group_size']} dir={run['directory']}")
        totals = report['run_level_totals']
        out.append(f"run-level totals: tokens={totals['generated_tokens']} lean_calls={totals['lean_calls']} "
                   f"lean_s={totals['lean_seconds']:.0f} wall_s={totals['wall_seconds']:.0f} (all groups, incl. incomplete)")
        if report['unjoined_task_ids']:
            out.append(f"unjoined task IDs (stage unknown): {len(report['unjoined_task_ids'])}")
        rows = [summary_row('overall', report['overall'])]
        rows += [summary_row('stage ' + k, v) for k, v in report['by_stage'].items()]
        rows += [summary_row('family ' + k, v) for k, v in report['by_family'].items()]
        if len(report['by_level']) > 1:
            rows += [summary_row('level ' + k, v) for k, v in report['by_level'].items()]
        out += [text_table(SUMMARY_HEADERS, rows), '']
        out.append('first-action status (complete groups): ' + ', '.join(
            f"{s}={v['count']} ({pct(v['share']).strip()})" for s, v in report['overall']['first_action_status'].items()))
        if report['incomplete_group_list']:
            reasons = Counter(g['reason'] for g in report['incomplete_group_list'])
            out.append('incomplete groups excluded from rates: ' + ', '.join(f'{r}={c}' for r, c in reasons.items()))
        out.append('')
    for result in pairs:
        out.append(f"== Paired {result['a']} (A) vs {result['b']} (B), exact tests ==")
        rows = []
        for name, block in [('overall', result['overall'])] + list(result['by_stage'].items()):
            sign = block['sign_test']
            rows.append([name, block['n_paired_tasks'], block['solved_both'], block['solved_only_a'],
                         block['solved_only_b'], block['solved_neither'], f"{block['mcnemar_exact_p']:.4f}",
                         f"{sign['a_higher']}/{sign['b_higher']}/{sign['ties']}", f"{sign['exact_p']:.4f}"])
        out.append(text_table(['slice', 'n_paired', 'both', 'A_only', 'B_only', 'neither', 'McNemar_p',
                               'k A>B/B>A/tie', 'sign_p'], rows))
        out.append('')
        slices = ([('overall', result['overall'])] + [('stage ' + k, v) for k, v in result['by_stage'].items()]
                  + [('level ' + k, v) for k, v in result['by_level'].items()])
        rows = []
        for name, block in slices:
            cells = [name, block['n_paired_tasks'], block['family_bootstrap']['families']]
            for key in ('family_bootstrap_mean_k', 'family_bootstrap'):
                boot = block[key]
                low, high = boot['ci95']
                cells += [pct(boot['difference']), 'n/a' if low is None else f'[{pct(low).strip()},{pct(high).strip()}]']
            rows.append(cells)
        boot = result['overall']['family_bootstrap']
        out.append(f"{result['a']} minus {result['b']}, family-cluster bootstrap 95% CI (reps={boot['reps']}, seed={boot['seed']}); "
                   'mean k/n is the primary metric, solved (k >= 1) the secondary:')
        out.append(text_table(['slice', 'n_paired', 'families', 'mean_k diff', 'CI95', 'solved diff', 'CI95'], rows))
        for title, key in (('PRIMARY: family sign-flip permutation test on mean k/n (T = sum over families of the paired '
                            'difference in k/n)', 'family_permutation_mean_k'),
                           ('Secondary: family sign-flip permutation test on solved (T = sum over families of the paired '
                            'difference in solved count)', 'family_permutation')):
            rows = []
            for name, block in slices:
                perm = block[key]
                rows.append([name, perm['families'], f"{perm['statistic']:.4g}", perm['method'], f"{perm['p']:.4g}",
                             'n/a' if perm['mc_se'] is None else f"{perm['mc_se']:.2g}"])
            out.append(title + ':')
            out.append(text_table(['slice', 'families', 'T', 'method', 'perm_p', 'mc_se'], rows))
        if 'holm' in result:
            holm_block = result['holm']
            out.append(f"Holm over {holm_block['family_size']} pair(s), overall slice: perm_p_mean_k_holm="
                       f"{holm_block['family_permutation_mean_k_p_holm']:.4g} perm_p_solved_holm="
                       f"{holm_block['family_permutation_p_holm']:.4g} McNemar_p_holm="
                       f"{holm_block['mcnemar_exact_p_holm']:.4f} sign_p_holm={holm_block['sign_exact_p_holm']:.4f}")
        out.append('')
    if per_level:
        labels = list(reports)
        out.append('== Per-level (tasks solved / tasks, micro pass@1) ==')
        rows = [[lv] + [f"{cell['tasks_solved']}/{cell['tasks']} {pct(cell['pass_at_1']).strip()}" if cell else 'n/a'
                        for cell in (by_arm.get(label) for label in labels)] for lv, by_arm in per_level.items()]
        out += [text_table(['level'] + labels, rows), '']
    return '\n'.join(out)


def parse_run(spec):
    label, sep, directory = spec.partition('=')
    return (label, directory) if sep else (None, spec)


class SetEvalSet(argparse.Action):
    """--eval-set NAME: every later --run belongs to NAME (until the next --eval-set)."""

    def __call__(self, parser, namespace, values, option_string=None):
        namespace.current_set = values
        namespace.eval_sets.append(values)


class AddRun(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        namespace.runs.append((namespace.current_set, values))


def analyse_set(arm_specs, table, pair_specs, options, primary, set_name, parser):
    """Reports, paired results and per-level table of one eval set; Holm only when `primary`."""
    reports = {label: arm_report(label, runs, table) for label, runs in arm_specs.items()}
    pairs, skipped = [], []
    for a, b in pair_specs:
        if a not in reports or b not in reports:
            if set_name is None or primary:
                parser.error(f'Unknown arm in --pair {a} {b}; arms: {sorted(reports)}')
            skipped.append(dict(a=a, b=b, reason='arm_missing_in_eval_set'))
            continue
        pairs.append(paired_report(a, b, reports, *options))
    if pairs and primary:
        apply_holm(pairs)
    return dict(arms=reports, pairs=pairs, per_level=per_level_table(reports), skipped_pairs=skipped)


def eval_set_summary(analysed, primary):
    """One row per (eval set, contrast): the numbers to read side by side."""
    rows = []
    for name, block in analysed.items():
        for pair in block['pairs']:
            overall = pair['overall']
            mean_k, solved = overall['family_permutation_mean_k'], overall['family_permutation']
            holm_block = pair.get('holm')
            rows.append(dict(eval_set=name, primary=name == primary, a=pair['a'], b=pair['b'],
                             n_paired_tasks=overall['n_paired_tasks'], families=mean_k['families'],
                             mean_k_difference=mean_k['mean_difference'], mean_k_perm_p=mean_k['p'],
                             mean_k_perm_p_holm=holm_block and holm_block['family_permutation_mean_k_p_holm'],
                             solved_difference=solved['mean_difference'], solved_perm_p=solved['p'],
                             solved_perm_p_holm=holm_block and holm_block['family_permutation_p_holm'],
                             mcnemar_exact_p=overall['mcnemar_exact_p']))
    return rows


def render_eval_sets(analysed, primary, summary):
    out = []
    for name, block in analysed.items():
        out.append(f"######## Eval set {name} ({'PRIMARY, Holm applied' if name == primary else 'secondary, no Holm'}) ########")
        out.append(render_text(block['arms'], block['pairs'], block['per_level']))
        for skip in block['skipped_pairs']:
            out.append(f"pair {skip['a']} {skip['b']} skipped in {name}: {skip['reason']}")
    out.append('######## Side by side (mean k/n difference A - B; permutation p; Holm within the primary eval set) ########')
    fmt = lambda x: 'n/a' if x is None else f'{x:.4g}'
    rows = [[r['eval_set'], f"{r['a']}-{r['b']}", r['n_paired_tasks'], r['families'], fmt(r['mean_k_difference']),
             fmt(r['mean_k_perm_p']), fmt(r['mean_k_perm_p_holm']), fmt(r['solved_difference']), fmt(r['solved_perm_p']),
             fmt(r['solved_perm_p_holm'])] for r in summary]
    out.append(text_table(['eval_set', 'contrast', 'n', 'families', 'meank_diff', 'meank_p', 'meank_p_holm',
                           'solved_diff', 'solved_p', 'solved_p_holm'], rows))
    return '\n'.join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', action=AddRun, required=True, help='ARM=DIR or DIR; repeat; same ARM pools')
    parser.add_argument('--eval-set', action=SetEvalSet, metavar='NAME',
                        help='Label the following --run options as eval set NAME (held_out, id_val, retention, ...)')
    parser.add_argument('--primary-eval-set', help='Eval set that receives Holm (default: the first --eval-set)')
    parser.add_argument('--tasks', type=Path, nargs='+', required=True, help='One or more tasks.jsonl files')
    parser.add_argument('--output-json', type=Path, required=True)
    parser.add_argument('--output-text', type=Path)
    parser.add_argument('--pair', nargs=2, action='append', default=[], metavar=('ARM_A', 'ARM_B'))
    parser.add_argument('--bootstrap-reps', type=int, default=2000)
    parser.add_argument('--bootstrap-seed', type=int, default=0, help='Seed for the bootstrap and the permutation test')
    parser.add_argument('--permutation-flips', type=int, default=100000, help='Monte Carlo flips when families > 16')
    parser.set_defaults(runs=[], eval_sets=[], current_set=None)
    args = parser.parse_args()
    if args.eval_sets and any(name is None for name, _ in args.runs):
        parser.error('Every --run must follow an --eval-set when --eval-set is used')
    if len(set(args.eval_sets)) != len(args.eval_sets):
        parser.error('Each --eval-set name may be given once (pool shards of one arm with the same ARM label)')
    primary = args.primary_eval_set or (args.eval_sets[0] if args.eval_sets else None)
    if args.eval_sets and primary not in args.eval_sets:
        parser.error(f'--primary-eval-set {primary} is not among {args.eval_sets}')
    sets = defaultdict(lambda: defaultdict(list))
    for index, (set_name, spec) in enumerate(args.runs):
        label, directory = parse_run(spec)
        loaded = load_run(directory, index)
        sets[set_name][label or loaded['run'].get('run_id') or Path(directory).name].append(loaded)
    table = load_tasks(*args.tasks)
    options = (args.bootstrap_reps, args.bootstrap_seed, args.permutation_flips)
    analysed = {name: analyse_set(arm_specs, table, args.pair, options, name == primary or name is None, name, parser)
                for name, arm_specs in sets.items()}
    for path in (args.output_json, args.output_text):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
    if args.eval_sets:
        summary = eval_set_summary(analysed, primary)
        document = dict(tasks_files=[str(p) for p in args.tasks], primary_eval_set=primary, eval_sets=analysed,
                        eval_set_summary=summary)
        text = render_eval_sets(analysed, primary, summary)
    else:
        only = analysed[None]
        document = dict(tasks_files=[str(p) for p in args.tasks], arms=only['arms'], pairs=only['pairs'],
                        per_level=only['per_level'])
        text = render_text(only['arms'], only['pairs'], only['per_level'])
    args.output_json.write_text(json.dumps(document, indent=2, ensure_ascii=False) + '\n')
    if args.output_text is not None:
        args.output_text.write_text(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
