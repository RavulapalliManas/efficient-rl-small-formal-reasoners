"""Audit saved primitive RLP records on CPU; no model inference.

Memory: one JSONL row/group (8 x <64 KiB), evaluation <108 x 32 KiB,
plus scalar aggregates <20 MiB. Run under a 2 GiB service cap.
Input records are immutable. One process owns the diagnosis JSON outputs.
"""
import argparse
from collections import Counter, defaultdict
import itertools
import json
import math
from pathlib import Path
import statistics as st
import sys


def rows(path):
    with path.open() as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def mean(values):
    return st.mean(values) if values else None


def describe(items, parse, cap):
    for x in items:
        assert parse(x['response']) == x['parsed_answer']
        assert x['accepted'] == (x['parsed_answer'] == x['answer'])
    return dict(n=len(items), accepted=sum(x['accepted'] for x in items),
                unparsed=sum(x['parsed_answer'] is None for x in items),
                capped=sum(len(x['completion_ids']) >= cap for x in items),
                code=sum('```' in x['response'] for x in items),
                mean_tokens=mean([len(x['completion_ids']) for x in items]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--train', type=Path, required=True)
    args = ap.parse_args()
    sys.path.insert(0, str(args.root / 'code'))
    from run_rlp_pilot import boxed_integer
    # The parser must recognize chosen answers and reject missing answers.
    assert boxed_integer(r'answer \boxed{23}') == 23
    assert boxed_integer(r'answer \boxed{-7}') == -7
    assert boxed_integer('No final answer') is None
    assert boxed_integer(r'\boxed{2} then \boxed{3}') == 3
    out = args.root / 'runtime/out'
    run = json.loads((out / 'run.json').read_text())
    cap = run['config']['max_completion_tokens']
    report = {'evaluations': {}, 'prediction': {}, 'family': {}, 'training': {}}
    for path in sorted(out.glob('*_primitives.jsonl')):
        report['evaluations'][path.stem] = describe(list(rows(path)), boxed_integer, cap)
    for path in sorted(out.glob('*_prediction.jsonl')):
        data = list(rows(path))
        report['prediction'][path.stem] = {'n': len(data),
            **{key: mean([x[key] for x in data]) for key in ['plain_logp', 'reasoned_logp']},
            **{key: sum(x[key] for x in data) for key in ['plain_correct', 'reasoned_correct']}}
    before, after = (list(rows(out / f'{phase}_primitives.jsonl')) for phase in ['baseline', 'after'])
    assert [x['id'] for x in before] == [x['id'] for x in after]
    paired = Counter()
    for b, a in zip(before, after):
        if b['accepted'] and not a['accepted']:
            paired['lost'] += 1
            paired['lost_unparsed' if a['parsed_answer'] is None else 'lost_wrong_integer'] += 1
            if a['parsed_answer'] is None:
                paired['lost_unparsed_capped' if len(a['completion_ids']) >= cap else 'lost_unparsed_not_capped'] += 1
        elif not b['accepted'] and a['accepted']:
            paired['gained'] += 1
        elif b['accepted']:
            paired['retained_correct'] += 1
        else:
            paired['both_failed'] += 1
    report['paired'] = dict(paired)
    for family in sorted({x['family'] for x in before}):
        report['family'][family] = {phase: describe([x for x in data if x['family'] == family], boxed_integer, cap)
                                    for phase, data in [('before', before), ('after', after)]}
    train = {x['id']: x for x in rows(args.train)}
    expected = [x['id'] for x in rows(out/'positions.jsonl') if x['split'] == 'train']
    summaries = defaultdict(list)
    counts = Counter()
    examples = []
    covariance = xx = yy = 0.0
    tokens = 0
    baseline_cancel_error = 0.0
    for step, group in itertools.groupby(rows(out/'rollouts.jsonl'), key=lambda x: x['step']):
        g = list(group)
        assert len(g) == run['config']['num_generations']
        assert {x['id'] for x in g} == {expected[step]}
        assert len({x['baseline_logp'] for x in g}) == 1
        rewards = [x['reward'] for x in g]
        advantages = [v-st.mean(rewards) for v in rewards]
        logps = [x['reasoned_logp'] for x in g]
        lengths = [len(x['completion_ids']) for x in g]
        for x in g:
            assert math.isclose(x['information_gain'], x['reasoned_logp']-x['baseline_logp'], abs_tol=1e-6)
        alternative = [len(g)/(len(g)-1)*(v-st.mean(logps)) for v in logps]
        baseline_cancel_error = max(baseline_cancel_error, max(abs(a-b) for a,b in zip(advantages, alternative)))
        for i, x in enumerate(g):
            counts['rollouts'] += 1
            tokens += lengths[i]
            dx, dy = lengths[i]-st.mean(lengths), advantages[i]
            covariance += dx*dy; xx += dx*dx; yy += dy*dy
            x['parsed'] = boxed_integer(x['response'])
            x['answer'] = train[x['id']]['answer']
            x['advantage'] = advantages[i]
            counts['capped'] += lengths[i] >= cap
            counts['boxed'] += x['parsed'] is not None
            counts['boxed_correct'] += x['parsed'] == x['answer']
            if x['parsed'] is not None and x['parsed'] != x['answer'] and x['advantage'] > 0:
                counts['wrong_boxed_positive_advantage'] += 1
                if len(examples) < 6:
                    examples.append({k: x[k] for k in ['step','id','answer','parsed','advantage','reasoned_logp','response']})
        winner = max(g, key=lambda x:x['reward'])
        if min(lengths) < cap <= max(lengths):
            counts['mixed_length_groups'] += 1
            counts['capped_winner_in_mixed_groups'] += len(winner['completion_ids']) >= cap
        correct = [x for x in g if x['parsed'] == x['answer']]
        wrong = [x for x in g if x['parsed'] is not None and x['parsed'] != x['answer']]
        if correct and wrong:
            counts['mixed_boxed_correctness_groups'] += 1
            counts['wrong_beats_correct_group'] += max(x['reward'] for x in wrong) > max(x['reward'] for x in correct)
        summaries[step//100].append({'length':st.mean(lengths),'capped':sum(n>=cap for n in lengths)/len(g),
                                    'reward':st.mean(rewards),'reward_std':st.pstdev(rewards)})
    assert tokens == run['training_generated_tokens']
    assert counts['rollouts'] == run['optimizer_steps'] * run['config']['num_generations']
    report['training'] = {'counts':dict(counts),'tokens':tokens,
        'within_group_length_advantage_correlation':covariance/math.sqrt(xx*yy),
        'ema_cancellation_max_error':baseline_cancel_error,
        'step_blocks':{str(k):{'n_steps':len(v), **{q:mean([x[q] for x in v]) for q in v[0]}} for k,v in summaries.items()}}
    trainer = [x for x in rows(out/'trainer.jsonl') if 'loss' in x and 'grad_norm' in x]
    missing = sorted(set(range(1, run['optimizer_steps']+1)) - {x['step'] for x in trainer})
    report['missing_optimizer_log_steps'] = missing
    validation_logs = [x['step'] for x in rows(out/'trainer.jsonl') if 'capability/accepted' in x]
    assert missing == sorted(validation_logs), 'Unexplained missing optimizer logs'
    report['trainer_blocks'] = {label:{'n':len(part), **{key:{'n':sum(key in x for x in part), 'mean':mean([x[key] for x in part if key in x])} for key in ['entropy','grad_norm','loss']}}
                               for label, part in [('first100',trainer[:100]),('last100',trainer[-100:])]}
    probes = list(rows(out/'prompt_probe.jsonl'))
    report['prompt_probe'] = {variant:{'n':len(g), 'mean_gain':mean([x['reasoned_logp']-x['plain_logp'] for x in g])}
        for variant,g in ((v,[x for x in probes if x['variant']==v]) for v in ['legacy','chat'])}
    import re
    report['noninteger_box_examples'] = [{'id':x['id'], 'question':x['question'], 'answer':x['answer'],
        'boxes':re.findall(r'\\boxed\{([^}]*)\}',x['response'])} for x in after
        if x['parsed_answer'] is None and re.findall(r'\\boxed\{([^}]*)\}',x['response'])]
    report['first_update'] = json.loads((out/'first_update.json').read_text())
    (args.root/'diagnosis.json').write_text(json.dumps(report,indent=2)+'\n')
    (args.root/'reward_examples.json').write_text(json.dumps(examples,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
