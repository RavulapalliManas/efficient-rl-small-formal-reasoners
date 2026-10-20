"""Freeze existing training proof starts and paired starting-state schedules.

Peak: <=300 MiB source rows + tokenizer <1 GiB + one Lean worker <=4 GiB.
Use an 8 GiB managed cap, one CPU, <=1200 seconds. No model or optimizer.
Reference block count describes the saved proof, not minimum proof difficulty.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ('data', 'train', 'eval', 'audit')]
from build_nt_hole_tasks import state_at, top_level_steps  # noqa: E402
from profile_external_proofs import statement, tokens  # noqa: E402
from run_tactic_rl import STEP, load_checker, mathlib_identity, repl_context, task_units, verify_group  # noqa: E402


def policy_row(task):
    return {k: task[k] for k in ('task_id', 'name', 'kind', 'header', 'given', 'state', 'lines_remaining')}


def reserved_template(header):
    # Reserve both authored DEV conjunction templates conservatively.
    return '∧' in header and any(mark in header for mark in ('∣', 'dvd', '≤', '≥'))


def schedules(bank, groups, seed):
    names = sorted(bank)
    if not names or groups % 4 or (groups // 4) % len(names):
        raise ValueError('Each of four stages must expose every theorem equally')
    rng = random.Random(seed)
    rng.shuffle(names)
    per_stage = groups // 4
    labels = ('one', 'two', 'longer', 'whole')
    uniform, reverse = [], []
    for label in labels:
        for i in range(per_stage):
            starts = bank[names[i % len(names)]]
            uniform.append(starts[rng.choice(labels)]['task_id'])
            reverse.append(starts[label]['task_id'])
    return uniform, reverse


def read_rows(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('tasks', 'proofs', 'pool', 'exclude-names', 'eval-states', 'eval-pool',
                 'checker', 'environment', 'repl', 'mathlib-identity', 'tokenizer', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--max-theorems', type=int, default=12)
    parser.add_argument('--groups', type=int, default=48)
    parser.add_argument('--seed', type=int, default=20261011)
    args = parser.parse_args()
    if not 1 <= args.max_theorems <= 12:
        raise ValueError('Construction supports at most twelve theorems')
    args.output.mkdir(parents=True, exist_ok=False)
    private = args.output / 'private'
    private.mkdir()
    pin = '29dcec074de168ac2bf835a77ef68bbe069194c5'
    identity = mathlib_identity(args.mathlib_identity, pin)
    pool, tasks = read_rows(args.pool), read_rows(args.tasks)
    task_units(tasks, pool)  # Validate all supplied rows before selection.
    excluded = set(args.exclude_names.read_text().split()) | {'burden_dev_dvd', 'burden_dev_le'}
    if any(t['name'] in excluded for t in tasks):
        raise ValueError('Training task input contains excluded names')
    references = {r['name']: r for r in read_rows(args.proofs) if r['status'] == 'accepted' and r['selected']}
    by_name = {r['name']: r for r in pool}
    evaluation = read_rows(args.eval_states)
    eval_states = [(r.get('state_id', r.get('name', r.get('task_id'))), tokens(r['state']))
                   for r in evaluation if r.get('state')]
    eval_headers = [(r['name'], tokens(statement(r['formal_statement']))) for r in read_rows(args.eval_pool)]
    eval_headers += [(r.get('name', r.get('task_id')), tokens(statement(r['header'])))
                     for r in evaluation if r.get('header')]
    if not eval_states or not eval_headers:
        raise ValueError('Evaluation state and statement controls must be nonempty')
    bank, failures = {}, []
    for name in sorted(references):
        candidates = [t for t in tasks if t['name'] == name and t['kind'] in ('whole', 'prefix_top')]
        if not candidates or reserved_template(candidates[0]['header']):
            failures.append(dict(name=name, reason='missing_starts_or_reserved_template'))
            continue
        steps, _ = top_level_steps(references[name]['body'])
        starts = {}
        for t in candidates:
            if t['kind'] == 'whole':
                starts['whole'] = dict(t, remaining_blocks=len(steps), reference_rest=references[name]['body'])
                continue
            matches = [k for k in range(1, len(steps)) if '\n'.join(steps[:k]) + '\n' == t['given']]
            if len(matches) != 1:
                continue
            k = matches[0]
            remaining = len(steps) - k
            label = 'one' if remaining == 1 else 'two' if remaining == 2 else 'longer'
            candidate = dict(t, remaining_blocks=remaining, reference_rest='\n'.join(steps[k:]))
            if label not in starts or remaining < starts[label]['remaining_blocks']:
                starts[label] = candidate
        if set(starts) != {'one', 'two', 'longer', 'whole'}:
            failures.append(dict(name=name, reason='missing_one_two_longer_or_whole'))
            continue
        if len({' '.join(t['state'].split()) for t in starts.values()}) != len(starts):
            failures.append(dict(name=name, reason='duplicate_start_state'))
            continue
        near = []
        for label, t in starts.items():
            state_tokens = tokens(t['state'])
            score, match = max((len(state_tokens & e) / max(len(state_tokens | e), 1), n) for n, e in eval_states)
            near.append(dict(label=label, state_jaccard=score, state_match=match))
        mine = tokens(statement(by_name[name]['formal_statement']))
        score, match = max((len(mine & e) / max(len(mine | e), 1), n) for n, e in eval_headers)
        if score >= 0.85 or any(r['state_jaccard'] >= 0.7 for r in near):
            failures.append(dict(name=name, reason='near_duplicate_eval', header_jaccard=score,
                                 header_match=match, states=near))
            continue
        bank[name] = starts
        references[name]['duplicate_checks'] = dict(header_jaccard=score, header_match=match, states=near)
    (private / 'selection_exclusions.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in failures))
    selection = dict(status='selected_unverified', eligible_theorems=len(bank),
                     exclusion_counts=dict(Counter(r['reason'] for r in failures)))
    rank = lambda n: hashlib.sha256(f'tactic-curriculum-v1/{n}'.encode()).hexdigest()
    selected = sorted(bank, key=rank)[:args.max_theorems]
    bank = {name: bank[name] for name in selected}
    selection['selected_theorems'] = selected
    (args.output / 'manifest.json').write_text(json.dumps(selection, indent=2) + '\n')
    if not bank:
        selection['status'] = 'failed_no_four_stratum_pool'
        (args.output / 'manifest.json').write_text(json.dumps(selection, indent=2) + '\n')
        raise ValueError('No common theorem has every required start')
    try:
        uniform, reverse = schedules(bank, args.groups, args.seed)
    except ValueError:
        selection['status'] = 'failed_schedule_divisibility'
        (args.output / 'manifest.json').write_text(json.dumps(selection, indent=2) + '\n')
        raise
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    checker = load_checker(args.checker, args.environment, private)
    ctx = repl_context(checker, args.environment, args.repl, private, 20, 120, 64)
    rows, replay_records = [], []
    try:
        with (private / 'verification.jsonl').open('x') as stream:
            for name, starts in bank.items():
                for label, task in starts.items():
                    state, reason, wall = state_at(ctx, task['header'], task['given'], [2])
                    unit = dict(task_id=task['task_id'], name=name, kind='statement', header=task['header'],
                                given=task['given'], given_steps=[])
                    result = verify_group(ctx, unit, [task['reference_rest']])[0]
                    if state:
                        text = tokenizer.apply_chat_template([{'role': 'user', 'content': STEP.format(state=state)}],
                                                             tokenize=False, add_generation_prompt=True)
                        length = len(tokenizer(text, add_special_tokens=False)['input_ids'])
                    else:
                        length = None
                    record = dict(task_id=task['task_id'], label=label, state_reason=reason, state_seconds=wall,
                                  state_matches=' '.join((state or '').split()) == ' '.join(task['state'].split()),
                                  prompt_tokens=length, replay=result)
                    stream.write(json.dumps(record) + '\n')
                    stream.flush()
                    replay_records.append(record)
                    if reason or not record['state_matches'] or result['status'] != 'accepted' or length > 1024:
                        raise RuntimeError('Frozen selected start failed verification: ' + task['task_id'])
                    rows.append(policy_row(dict(task, state=state)))
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()
    task_units(rows, pool)
    public_pool = [{k: by_name[n][k] for k in ('name', 'split', 'formal_statement')} for n in sorted(bank)]
    for filename, content in [('tasks.jsonl', rows), ('pool.jsonl', public_pool)]:
        (args.output / filename).write_text(''.join(json.dumps(r) + '\n' for r in content))
    for name, order in [('uniform_order', uniform), ('reverse_order', reverse),
                        ('signal_order', [bank[n]['one']['task_id'] for n in sorted(bank)])]:
        (args.output / (name + '.json')).write_text(json.dumps(order, indent=2) + '\n')
    (args.output / 'excluded.txt').write_text('\n'.join(sorted(excluded)) + '\n')
    (private / 'references.jsonl').write_text(''.join(json.dumps(dict(references[n], starts=bank[n])) + '\n' for n in sorted(bank)))
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    labels = {t['task_id']: label for starts in bank.values() for label, t in starts.items()}
    manifest = dict(status='prepared', selection=selection, seed=args.seed, theorems=list(sorted(bank)), starts=len(rows), groups=args.groups,
                    remaining_blocks={t['task_id']: t['remaining_blocks'] for starts in bank.values() for t in starts.values()},
                    schedules={k: dict(Counter(labels[x] for x in order)) for k, order in [('uniform', uniform), ('reverse', reverse)]},
                    lean=(args.environment / 'mathlib/lean-toolchain').read_text().strip(), mathlib=identity,
                    input_sha256={k: digest(v) for k, v in vars(args).items() if isinstance(v, Path) and v.is_file()},
                    output_sha256={p.name: digest(p) for p in args.output.iterdir() if p.is_file()},
                    limitation='Fixed reference-prefix starting distribution, not adaptive reverse curriculum. Reference block count is heuristic. Semantic family independence remains unverified. Starts supply training proof context; endpoint evaluation must use unassisted roots. No model, optimizer or learning benefit measured.')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(status=manifest['status'], theorems=len(bank), starts=len(rows), groups=args.groups)), flush=True)


if __name__ == '__main__':
    main()
