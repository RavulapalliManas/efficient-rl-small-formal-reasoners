"""SFT rows in the environment v2.1 format, from existing Lean-checked number-theory proofs.

Every prompt is `render_observation` of a reference cut, the exact text RL shows: the theorem, the earlier
reference steps, the goal at the cut, and empty result sections. The target is the next chunk of at most
--max-tactics tactics in one Lean fence. Sources (select with --sources):
  tasks   nt_rl_tasks_v3 prefix_top cuts. RL shows `given` as supplied lines, so the prompt does too.
          These problems are the RL pool (heldout_proof_problems): their reference proofs are RL training tasks.
  aug     nt_aug_v1 proofs: the first k source steps plus one new closing tactic (always a single tactic).
  cuts    nt_aug_v1 cuts.jsonl states with the SOURCE proof's next steps (nt_sft_v5 train.jsonl), so targets
          can be 1-4 real tactics. Needs --sft-train and --cuts.
Cuts of aug and cuts rows show the earlier steps either as "your steps" or as supplied lines (hash of name and k,
--supplied-share), because RL has both task layouts. prefix_have tasks are not built: RL never runs them.
Alignment (checked 2026-10-13): nt_aug_v1 `k` and parse_action split `text` into the same tactics for all 2,211
proofs. parse_action cut "rcases h <;>" and the next same-indent line into two tactics (the first is a syntax
error alone; 2 of 14 sampled prefix_top targets failed so in Lean); `merge_continuations` joins them, and
every row must round-trip through parse_action unchanged (counted as roundtrip_failed otherwise).
No Lean runs here: goal text and target executability are re-checked by retrace_v21_states.py.
Only TRAIN pool names outside DEV and the training exclusions are kept; --exclude-names adds more.
At most --per-problem rows per theorem and --auto-per-problem single-automation targets per theorem; a theorem-level
hash split holds out --val-percent percent of theorems for loss evaluation (train and val share no theorem).
Memory: a few thousand rows of text, well under 100 MiB.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re
import sys
import textwrap

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ('train', 'data')]
from build_nt_sft_data import body_of, strip_comments  # noqa: E402
from build_proof_tasks import top_level_steps  # noqa: E402
from rl_components.observations import render_observation  # noqa: E402
from rl_components.actions import parse_action  # noqa: E402
from rl_components.sft import chat_token_lengths  # noqa: E402

AUTOMATION = re.compile(r'(aesop|simp_all|omega|tauto|norm_num|simp|nlinarith|linarith|rfl|ring_nf|decide|'
                        r'push_cast|field_simp|positivity)\b')


def split_tactics(text):
    """The tactics parse_action finds in text (RL's own splitter), or None."""
    action = parse_action(text, max_tactics=10**6)
    return action['tactics'] if action['kind'] == 'tactics' else None


def merge_continuations(tactics):
    """Join the line after a trailing "<;>", which parse_action cuts into a dangling tactic; indent the joined line."""
    merged = []
    for tactic in tactics:
        if merged and merged[-1].rstrip().endswith('<;>'):
            merged[-1] += '\n' + textwrap.indent(tactic, '  ')
        else:
            merged.append(tactic)
    return merged


def rl_tactics(text):
    """Tactics as RL will see them after merging; None if the text does not round-trip through parse_action."""
    tactics = split_tactics(text)
    if tactics is None:
        return None
    merged = merge_continuations(tactics)
    return merged if split_tactics('\n'.join(merged)) == merged else None


def is_automation(chunk):
    return len(chunk) == 1 and '\n' not in chunk[0] and bool(AUTOMATION.match(chunk[0].strip()))


def supplied_text(done):
    return ''.join(textwrap.indent(t, '  ') + '\n' for t in done)


def row(name, header, mode, done, given, goal, rest, max_tactics, horizon, source,
        *, tokenizer=None, max_action_tokens=None):
    """One example. mode 'supplied': `given` is shown as supplied lines. mode 'shown': `done` as policy steps."""
    chunk = rest[:max_tactics]
    target = '```lean\n' + '\n'.join(chunk) + '\n```'
    if split_tactics(target) != chunk:
        return None
    if mode == 'supplied':
        task, shown, remaining = dict(kind='prefix_top', header=header, given=given), [], horizon
    else:
        # Assume one action per earlier tactic, the slowest RL path to this cut; keep one action for the target.
        task, shown, remaining = dict(kind='whole', header=header, given=''), done, max(horizon - len(done), 1)
    prompt = render_observation(task, shown, [goal], '', [], remaining, max_tactics)
    if max_action_tokens is not None:
        if tokenizer is None or max_action_tokens < 1:
            raise ValueError('A positive action budget requires a tokenizer')
        # Never cut inside a nested tactic or leave an unclosed Lean fence.
        while chunk:
            target = '```lean\n' + '\n'.join(chunk) + '\n```'
            messages = [dict(role='user', content=prompt), dict(role='assistant', content=target)]
            if chat_token_lengths(messages, tokenizer)[1] <= max_action_tokens:
                break
            chunk = chunk[:-1]
        if not chunk:
            return None
    return dict(format='v21_observation', source=source, theorem=name, steps_done=len(done), mode=mode,
                header=header, given=given, done=done, state=goal, horizon=horizon, max_tactics=max_tactics, target_tactics=chunk, automation=is_automation(chunk),
                messages=[{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': target}])


def recovery_row(example, last_error, failed, *, tokenizer, max_action_tokens=192, max_tokens=2048):
    """One failed action at a verified source cut, followed by its unchanged correct target.

    The caller supplies real environment feedback. Format-only failures use the parser's
    self-loop feedback and leave ``failed`` empty, as the live environment does.
    """
    supplied = example['mode'] == 'supplied'
    shown = [] if supplied else example['done']
    remaining = example['horizon'] - len(shown) - 1
    if remaining < 1:
        return None
    if not last_error:
        raise ValueError('A recovery row requires failure feedback')
    target = example['messages'][1]
    action = parse_action(target['content'], example['max_tactics'])
    if action.get('tactics') != example['target_tactics']:
        raise ValueError('Recovery target differs from the verified source tactics')
    task = dict(kind='prefix_top' if supplied else 'whole', header=example['header'],
                given=example['given'] if supplied else '')
    prompt = render_observation(task, shown, [example['state']], last_error, failed,
                                remaining, example['max_tactics'])
    messages = [dict(role='user', content=prompt), dict(target)]
    total, completion = chat_token_lengths(messages, tokenizer)
    if total >= max_tokens or completion > max_action_tokens:
        return None
    return dict(example, messages=messages, tokens=total, completion_tokens=completion,
                recovery_remaining_actions=remaining, recovery_last_error=last_error,
                recovery_failed_tactics=list(failed))


def pool_header(statement):
    return re.sub(r':=\s*(by\s*)?sorry\s*$', '', statement.strip()).rstrip()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('pool', 'splits', 'tasks', 'aug', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--sft-train', type=Path, help='nt_sft_v5 train.jsonl (source proofs of the cuts)')
    parser.add_argument('--cuts', type=Path, help='nt_aug_v1 cuts.jsonl (state at every source cut)')
    parser.add_argument('--sources', default='tasks,aug,cuts', help='comma list of tasks, aug, cuts')
    parser.add_argument('--exclude-names', type=Path, action='append', default=[])
    parser.add_argument('--max-tactics', type=int, default=4)
    parser.add_argument('--horizon', type=int, default=6)
    parser.add_argument('--per-problem', type=int, default=8)
    parser.add_argument('--auto-per-problem', type=int, default=2, help='single bare-automation targets kept per theorem')
    parser.add_argument('--supplied-share', type=float, default=0.5)
    parser.add_argument('--val-percent', type=int, default=5)
    parser.add_argument('--seed', type=int, default=20261013)
    parser.add_argument('--tokenizer', type=Path, help='Count chat-template input_ids and drop rows over --max-tokens')
    parser.add_argument('--max-tokens', type=int, default=2048)
    args = parser.parse_args()
    sources = set(args.sources.split(','))
    if not sources <= {'tasks', 'aug', 'cuts'} or ('cuts' in sources and not (args.cuts and args.sft_train)):
        parser.error('--sources takes tasks, aug, cuts; cuts needs --cuts and --sft-train')
    with args.pool.open() as input_stream:
        pool = {r['name']: r for r in map(json.loads, input_stream)}
    excluded = set((args.splits / 'excluded_from_training.txt').read_text().split())
    excluded |= set((args.splits / 'dev.txt').read_text().split())
    for path in args.exclude_names:
        excluded |= set(path.read_text().split())
    counts, rows = Counter(), defaultdict(list)

    def admit(name):
        ok = name in pool and pool[name]['split'] == 'train' and name not in excluded
        counts['admitted' if ok else 'excluded_name'] += 1
        return ok

    def add(example, key):
        if example is None:
            counts['roundtrip_failed_' + key] += 1
        else:
            rows[example['theorem']].append(example)

    def supplied(name, k):
        digest = hashlib.sha256(f'{name}/{k}/{args.seed}'.encode()).hexdigest()
        return k > 0 and int(digest, 16) % 100 < args.supplied_share * 100

    if 'tasks' in sources:
        with args.tasks.open() as input_stream:
            for task in map(json.loads, input_stream):
                if task['kind'] != 'prefix_top' or not task['state'] or not task['reference_rest']:
                    counts['task_skipped_kind_or_reference'] += 1
                    continue
                if not admit(task['name']):
                    continue
                done = rl_tactics(task['given']) if task['given'].strip() else []
                rest = rl_tactics(task['reference_rest'])
                if done is None or not rest or (done and done[-1].rstrip().endswith('<;>')):
                    counts['task_unparsed'] += 1
                    continue
                add(row(task['name'], task['header'], 'supplied', done, task['given'], task['state'], rest,
                        args.max_tactics, args.horizon, 'nt_rl_tasks_v3:prefix_top'), 'tasks')

    def cut_row(name, steps, k, state, source):
        """steps: top-level steps of a whole proof body; the cut is after the first k of them."""
        header = pool_header(pool[name]['formal_statement'])
        done = rl_tactics('\n'.join(steps[:k])) if k else []
        rest = rl_tactics('\n'.join(steps[k:]))
        if done is None or not rest or not state:
            counts['cut_unparsed_' + source] += 1
            return None
        mode = 'supplied' if supplied(name, k) else 'shown'
        return row(name, header, mode, done, supplied_text(done), state, rest, args.max_tactics, args.horizon, source)

    if 'aug' in sources:
        with args.aug.open() as input_stream:
            for proof in map(json.loads, input_stream):
                if not admit(proof['name']):
                    continue
                steps, _ = top_level_steps(proof['text'])
                if not 0 <= proof['k'] < len(steps):
                    counts['aug_unparsed'] += 1
                    continue
                if split_tactics(proof['text']) is None or len(split_tactics(proof['text'])) != len(steps):
                    counts['aug_split_disagrees'] += 1
                    continue
                add(cut_row(proof['name'], steps, proof['k'], proof['state'], 'nt_aug_v1'), 'aug')
    if 'cuts' in sources:
        with args.sft_train.open() as input_stream:
            bodies = [json.loads(line) for line in input_stream]
        with args.cuts.open() as input_stream:
            for cut in map(json.loads, input_stream):
                if not cut['state'] or not admit(cut['name']):
                    counts['cut_without_state_or_name'] += 1
                    continue
                record = bodies[cut['source_row']]
                code = record['messages'][1]['content'].removeprefix('```lean\n').removesuffix('\n```')
                body = body_of(code, pool_header(pool[cut['name']]['formal_statement']))
                body = strip_comments(body) if body else None
                steps = top_level_steps(body)[0] if body else []
                if record['theorem'] != cut['name'] or len(steps) != cut['steps_source'] or cut['k'] >= len(steps):
                    counts['cut_source_mismatch'] += 1
                    continue
                add(cut_row(cut['name'], steps, cut['k'], cut['state'], 'nt_aug_v1:source_cut'), 'cuts')

    rng = random.Random(args.seed)
    tokenizer = None
    if args.tokenizer:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer)
    args.output.mkdir(exist_ok=False)
    lengths, theorems = [], {'train': set(), 'val': set()}
    with (args.output / 'sft_train.jsonl').open('x') as train, (args.output / 'sft_val.jsonl').open('x') as val:
        for name in sorted(rows):
            chosen = rows[name]
            rng.shuffle(chosen)
            # Not sha256(name): the nt_sft_v5 builder holds out its own loss-validation problems by that hash.
            held = int(hashlib.sha256(('v21-val/' + name).encode()).hexdigest(), 16) % 100 < args.val_percent
            kept, seen, automatic = [], set(), 0
            for example in chosen:
                pair = (example['messages'][0]['content'], example['messages'][1]['content'])
                if pair in seen:
                    counts['duplicate_pair'] += 1
                elif example['automation'] and automatic >= args.auto_per_problem:
                    counts['capped_automation'] += 1
                elif len(kept) >= args.per_problem:
                    counts['capped_rows'] += 1
                else:
                    seen.add(pair)
                    automatic += example['automation']
                    kept.append(example)
            for example in kept:
                if tokenizer is not None:
                    ids = tokenizer.apply_chat_template(example['messages'], tokenize=True, return_dict=True)['input_ids']
                    example['tokens'] = len(ids)
                    lengths.append(len(ids))
                    if len(ids) > args.max_tokens:
                        counts['over_token_cap'] += 1
                        continue
                (val if held else train).write(json.dumps(example, ensure_ascii=False) + '\n')
                split = 'val' if held else 'train'
                counts[split + '_rows'] += 1
                counts[f'{split}_source_{example["source"]}_{example["mode"]}'] += 1
                counts[split + '_single_automation'] += example['automation']
                theorems[split].add(name)
    counts.update(train_theorems=len(theorems['train']), val_theorems=len(theorems['val']))
    manifest = dict(counts=dict(counts), sources=sorted(sources), max_tactics=args.max_tactics, horizon=args.horizon,
                    per_problem=args.per_problem, auto_per_problem=args.auto_per_problem,
                    supplied_share=args.supplied_share, val_percent=args.val_percent, seed=args.seed,
                    max_tokens=args.max_tokens,
                    token_lengths=dict(max=max(lengths), mean=sum(lengths) / len(lengths)) if lengths else None,
                    inputs={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (args.pool, args.tasks, args.aug, args.cuts, args.sft_train) if p},
                    limits='Goal text is the source builder state until retrace_v21_states.py re-derives it; '
                           'empty error and failed-tactic sections')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
