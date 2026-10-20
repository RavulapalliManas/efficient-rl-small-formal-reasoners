"""Re-derive each v2.1 SFT prompt's goal through the RL environment and keep only rows whose target RL can run.

Input: sft_train.jsonl and sft_val.jsonl of build_v21_sft.py. For each row this opens a LeanEpisode (the RL class),
shows the row's supplied lines or replays its earlier tactics as actions, and reads the goals RL would print.
It then runs the row's target as one action. A row is kept when every target tactic is applied and the proof state
changes or closes (a closed proof must pass the full replay check). The prompt is re-rendered with the RL goal text.
Dropped rows go to rejected.jsonl with the status. No row is edited otherwise.
Shards: --shard K/N keeps rows with index % N == K, so each service fits a time cap; --merge joins shard outputs.
Memory: one Lean REPL per worker, about 2 GiB each; run under a systemd MemoryMax of at least 2 GiB x --workers + 4 GiB.
Time: measured 2.2 s of wall per row with 4 workers on 400% CPU (about 27 rows per minute, noheldout build, 2026-10-13).
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / d) for d in ('train', 'data', 'eval', 'audit')]
from bench_lean_verifier import repl_context  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from run_tactic_rl import LeanEpisode, render_observation  # noqa: E402

squash = lambda text: re.sub(r'\s+', ' ', text).strip()


def retrace(ctx, output, counts, example):
    """(row with the RL goal, None) or (None, reason)."""
    supplied = example['mode'] == 'supplied'
    task = dict(task_id=example['theorem'] + ':v21sft', name=example['theorem'], header=example['header'],
                kind='prefix_top' if supplied else 'whole', given=example['given'] if supplied else '')
    env = LeanEpisode(ctx, task, output, counts, version=2, horizon=example['horizon'], max_tactics=example['max_tactics'])
    try:
        env.reset()
        for tactic in [] if supplied else example['done']:
            result = env.step(tactic, 0.0)
            if result['status'] != 'changed':
                return None, 'replay_' + result['status']
        if not env.goals:
            return None, 'no_goal_at_cut'
        goal = '\n\n'.join(env.goals)
        shown, remaining = ([], example['horizon']) if supplied else (example['done'], max(example['horizon'] - len(example['done']), 1))
        render = lambda state: render_observation(task, shown, [state], '', [], remaining, example['max_tactics'])
        if render(example['state']) != example['messages'][0]['content']:
            return None, 'builder_prompt_not_reproducible'
        result = env.step(example['messages'][1]['content'], 0.0)
        if result.get('tactics_applied') != len(example['target_tactics']) or result['status'] not in ('changed', 'accepted'):
            return None, 'target_' + result['status'] + ':' + str(result.get('error'))[:120]
    finally:
        env.close()
    updated = dict(example, state=goal, state_source='rl_replay', source_state=example['state'],
                   goal_changed=goal != example['state'], goal_changed_beyond_whitespace=squash(goal) != squash(example['state']))
    updated['messages'] = [dict(example['messages'][0], content=render(goal)), example['messages'][1]]
    return updated, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('data', 'output', 'checker', 'environment', 'repl'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--shard', default='0/1')
    parser.add_argument('--merge', type=Path, nargs='+', help='Shard output directories, in order; writes --output')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--lean-timeout', type=float, default=30)
    parser.add_argument('--limit', type=int, help='Smoke test: first N rows of each file')
    args = parser.parse_args()
    if args.merge:
        return merge(args.merge, args.output)
    if not all((args.data, args.output, args.checker, args.environment, args.repl)):
        parser.error('--data --output --checker --environment --repl are required')
    index, shards = map(int, args.shard.split('/'))
    args.output.mkdir(parents=True, exist_ok=False)
    checker = load_checker(args.checker, args.environment, args.output)
    ctx = repl_context(checker, args.environment.resolve(), args.repl, args.output, args.lean_timeout, 180, 32)
    counts, tally = defaultdict(float), Counter()
    with (args.output / 'rejected.jsonl').open('x') as rejected:
        for split in ('train', 'val'):
            rows = [json.loads(line) for line in (args.data / f'sft_{split}.jsonl').open()][:args.limit]
            rows = [r for i, r in enumerate(rows) if i % shards == index]
            with ThreadPoolExecutor(args.workers) as pool, (args.output / f'sft_{split}.jsonl').open('x') as out:
                for example, (updated, reason) in zip(rows, pool.map(lambda r: retrace_safe(ctx, args.output, counts, r), rows)):
                    tally[split + '_input'] += 1
                    if updated is None:
                        tally['rejected_' + reason.split(':')[0]] += 1
                        rejected.write(json.dumps(dict(split=split, theorem=example['theorem'], source=example['source'],
                                                       steps_done=example['steps_done'], reason=reason), ensure_ascii=False) + '\n')
                        continue
                    tally[split + '_kept'] += 1
                    tally['goal_changed'] += updated['goal_changed']
                    tally['goal_changed_beyond_whitespace'] += updated['goal_changed_beyond_whitespace']
                    out.write(json.dumps(updated, ensure_ascii=False) + '\n')
    manifest = dict(counts=dict(tally), lean=dict(counts), builder_manifest=json.loads((args.data / 'manifest.json').read_text()))
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest['counts']))


def merge(directories, output):
    output.mkdir(parents=True, exist_ok=False)
    total, manifests = Counter(), []
    for name in ('sft_train.jsonl', 'sft_val.jsonl', 'rejected.jsonl'):
        with (output / name).open('x') as out:
            for directory in directories:
                out.write((directory / name).read_text())
    for directory in directories:
        manifest = json.loads((directory / 'manifest.json').read_text())
        total.update(manifest['counts'])
        manifests.append(dict(directory=directory.name, counts=manifest['counts'], lean=manifest['lean']))
    builder = json.loads((directories[0] / 'manifest.json').read_text())['builder_manifest']
    (output / 'manifest.json').write_text(json.dumps(dict(counts=dict(total), shards=manifests, builder_manifest=builder), indent=2) + '\n')
    print(json.dumps(dict(total)))


def retrace_safe(ctx, output, counts, example):
    try:
        return retrace(ctx, output, counts, example)
    except Exception as exc:  # A root that does not replay is a rejected row, not a crash.
        return None, 'exception:' + repr(exc)[:120]


if __name__ == '__main__':
    main()
