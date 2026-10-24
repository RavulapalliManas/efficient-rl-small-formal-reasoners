"""Frozen prompt/premise diagnostic. No optimizer or generated-code execution.

Peak: one FP32 0.5B policy <2 GiB, batch4 x1536tokens bounded KV/logits
and SDPA workspaces <2 GiB, two warm mathlib processes x4 GiB, runtime
reserve <8 GiB; managed cap24 GiB. Stream each paired sample immediately.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re
import signal
import sys
import time


CONDITIONS = 'ABCD'
CURRENT = 'Lean 4 goal state:\n\n{state}\n\nWrite the next tactic.'
RAW = ('Lean 4 goal state:\n\n{state}\n\nReturn only a raw Lean tactic block for the next step. '
       'Do not include explanations, code fences, theorem declarations, or imports.')


def condition_prompt(state, condition):
    if condition not in CONDITIONS:
        raise ValueError('Unknown condition')
    prompt = (CURRENT if condition in 'AC' else RAW).format(state=state['goals'])
    if condition in 'CD':
        cards = state['cards']
        if any(c['name'] == state['task']['name'] or not c.get('target_independent') for c in cards):
            raise ValueError('Target card or unchecked dependency')
        prompt += '\n\nAvailable declarations:\n' + '\n'.join(
            c.get('display', c['name'] + ' : ' + c['type']) for c in cards)
    return prompt


def completion_capped(ids, eos_ids, maximum):
    return len(ids) >= maximum and not any(i in eos_ids for i in ids)


def outcome_class(result, capped):
    # Truncation is a separate flag, not an exclusive error class.
    if result.get('accepted'):
        return 'complete'
    status = result['status']
    if status == 'format_error':
        return 'format'
    if status == 'changed':
        return 'valid_unfinished'
    if status in ('timeout', 'replay_timeout'):
        return 'infrastructure_timeout'
    if status in ('crash', 'replay_crash'):
        return 'infrastructure_crash'
    if status in ('forbidden_construct', 'replay_forbidden_construct', 'repeated_state'):
        return status
    message = result.get('error') or result.get('replay', {}).get('output', '')
    first = re.search(r'error:\s*([^\n]*)', message)
    message = first.group(1) if first else message
    message = re.sub(r'^<input>:\d+:\d+:\s*', '', message)
    if re.search(r'unknown (?:identifier|constant|tactic)|unknown namespace', message):
        return 'unresolved_name'
    if re.search(r'type mismatch|failed to synthesize|function expected|invalid field|ambiguous|'
                 r'numerals are data|equality or iff proof expected', message):
        return 'type_or_elaboration'
    if re.search(r'unexpected token|unexpected end of input|^expected |unterminated|invalid alternative name', message):
        return 'syntax_or_structure'
    if re.search(r'unsolved goals|tactic |could not prove|failed to rewrite|made no progress|'
                 r'failed to (?:find a contradiction|solve some goals)', message):
        return 'tactic_failure'
    return 'verification_rejection' if status.startswith('replay_') else 'unclassified_rejection'


def summarize(states, rows, samples, continuations=()):
    by_id = {s['id']: s for s in states}
    indexed = {(r['state_id'], r['sample'], r['condition']): r for r in rows}
    continued = {(r['state_id'], r['sample'], r['condition']): r for r in continuations}
    def episode_outcome(row):
        if row['accepted']:
            return True
        if row.get('result', {}).get('status') == 'changed':
            item = continued.get((row['state_id'], row['sample'], row['condition']))
            return item['accepted'] if item and item.get('complete_budget', True) else None
        return False
    conditions = {}
    for condition in CONDITIONS:
        subset = [r for r in rows if r['condition'] == condition]
        conditions[condition] = dict(planned=len(states) * samples, attempted=len(subset),
            pending=len(states) * samples - len(subset), accepted=sum(r['accepted'] for r in subset),
            valid=sum(r['valid'] for r in subset), capped=sum(r['capped'] for r in subset),
            unassisted_root_success=sum(r['accepted'] and by_id[r['state_id']]['assistance'] == 'none'
                                        for r in subset),
            assisted_success=sum(r['accepted'] and by_id[r['state_id']]['assistance'] != 'none'
                                 for r in subset),
            error_classes=dict(Counter(r.get('error_class', 'not_classified') for r in subset)))
        resolved = [r for r in subset if episode_outcome(r) is not None]
        conditions[condition]['episode'] = dict(planned=len(states) * samples,
            resolved=len(resolved), pending=len(states) * samples - len(resolved),
            accepted=sum(episode_outcome(r) is True for r in resolved),
            unassisted_root_success=sum(episode_outcome(r) is True and by_id[r['state_id']]['assistance']=='none'
                                        for r in resolved),
            assisted_success=sum(episode_outcome(r) is True and by_id[r['state_id']]['assistance']!='none'
                                 for r in resolved))
        for field in ('assistance', 'stratum', 'family'):
            breakdown = {}
            for label in sorted({s.get(field, 'not_recorded') for s in states}):
                group = [r for r in subset if by_id[r['state_id']].get(field, 'not_recorded') == label]
                planned = sum(s.get(field, 'not_recorded') == label for s in states) * samples
                breakdown[label] = dict(planned=planned, attempted=len(group),
                    primary_success=sum(r['accepted'] for r in group), valid=sum(r['valid'] for r in group),
                    episode_success=sum(episode_outcome(r) is True for r in group),
                    episode_pending=planned-sum(episode_outcome(r) is not None for r in group))
            conditions[condition]['by_' + field] = breakdown
    paired = {}
    for left, right in [('B', 'A'), ('C', 'A'), ('D', 'B'), ('D', 'C')]:
        pairs = [(indexed[(s['id'], i, left)], indexed[(s['id'], i, right)])
                 for s in states for i in range(samples)
                 if (s['id'], i, left) in indexed and (s['id'], i, right) in indexed]
        paired[left + '-' + right] = dict(complete_pairs=len(pairs),
            proof_wins=sum(a['accepted'] and not b['accepted'] for a, b in pairs),
            proof_losses=sum(b['accepted'] and not a['accepted'] for a, b in pairs),
            valid_wins=sum(a['valid'] and not b['valid'] for a, b in pairs),
            valid_losses=sum(b['valid'] and not a['valid'] for a, b in pairs))
        complete = [(a, b) for a, b in pairs if episode_outcome(a) is not None and episode_outcome(b) is not None]
        paired[left + '-' + right].update(episode_complete_pairs=len(complete),
            episode_wins=sum(episode_outcome(a) and not episode_outcome(b) for a, b in complete),
            episode_losses=sum(episode_outcome(b) and not episode_outcome(a) for a, b in complete))
        paired[left + '-' + right]['by_state'] = {}
        for state in states:
            local = [(a, b) for a, b in pairs if a['state_id'] == state['id']]
            resolved = [(a, b) for a, b in local if episode_outcome(a) is not None and episode_outcome(b) is not None]
            paired[left + '-' + right]['by_state'][state['id']] = dict(
                root_name=state.get('root_name', state['task']['name']), assistance=state['assistance'],
                primary_complete_pairs=len(local),
                primary_success_difference=sum(int(a['accepted'])-int(b['accepted']) for a, b in local),
                episode_complete_pairs=len(resolved),
                episode_success_difference=sum(int(episode_outcome(a))-int(episode_outcome(b)) for a, b in resolved))
    return {'conditions': conditions, 'paired': paired,
            'strata': dict(Counter(s['stratum'] if 'stratum' in s else s['assistance'] for s in states)),
            'root_ids': sorted({s.get('root_name', s['task']['name']) for s in states}),
            'limitation': 'Exploratory paired samples; cluster-aware inference by original theorem is required. '
                          'Valid unfinished actions do not establish progress. Assisted success is not unassisted root success.'}


class CapReached(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'states', 'model', 'checker', 'environment', 'mathlib-identity', 'repl', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    with args.states.open() as input_stream:
        states = [json.loads(line) for line in input_stream]
    if len({s['id'] for s in states}) != len(states):
        raise ValueError('Duplicate state ID')
    for state in states:
        for condition in CONDITIONS:
            condition_prompt(state, condition)
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ('train', 'eval', 'data', 'audit')]
    import run_tactic_rl as rl
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    counts = dict(generated_tokens=0, lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0)
    run = dict(status='running', mode='DEV_evaluation', optimizer_updates=0, config=cfg, counts=counts,
               run_id=cfg['run_id'], model_source=str(args.model), device='cpu',
               states_sha256=hashlib.sha256(args.states.read_bytes()).hexdigest(),
               primary_planned=len(states) * cfg['samples'] * 4, phase='primary')
    started = time.monotonic()
    rows, continuations = [], []
    def save():
        run['wall_seconds'] = time.monotonic() - started
        (args.output / 'run.json').write_text(json.dumps(run, indent=2) + '\n')
        (args.output / 'summary.json').write_text(json.dumps(summarize(states, rows, cfg['samples'], continuations), indent=2) + '\n')
    def limit(tokens=0, calls=0):
        token_cap = cfg['max_primary_tokens'] if run['phase'] == 'primary' else cfg['max_generated_tokens']
        if counts['generated_tokens'] + tokens > token_cap:
            raise CapReached('token_cap')
        call_cap = cfg['max_primary_lean_calls'] if run['phase'] == 'primary' else cfg['max_lean_calls']
        if counts['lean_calls'] + calls > call_cap:
            raise CapReached('lean_call_cap')
    def alarm(*_):
        raise CapReached('wall_cap')
    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(cfg['max_wall_seconds'])
    ctx, episode = None, None
    save()
    try:
        run['mathlib_source_identity'] = rl.mathlib_identity(args.mathlib_identity, cfg['mathlib'])
        run['lean'] = (args.environment / 'mathlib/lean-toolchain').read_text().strip()
        if run['lean'] != cfg['lean']:
            raise ValueError('Lean pin differs')
        checker = rl.load_checker(args.checker, args.environment, args.output)
        ctx = rl.repl_context(checker, args.environment.resolve(), args.repl, args.output,
                              cfg['lean_timeout_seconds'], cfg['lean_setup_timeout_seconds'], 32)
        torch.set_num_threads(cfg['cpu_threads'])
        torch.set_num_interop_threads(1)
        tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
        tokenizer.padding_side = 'left'
        tokenizer.pad_token_id = tokenizer.eos_token_id
        model = AutoModelForCausalLM.from_pretrained(args.model, local_files_only=True,
                                                   dtype=torch.float32, attn_implementation='sdpa').eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        eos = model.generation_config.eos_token_id
        eos = set(eos if isinstance(eos, list) else [eos]) | {tokenizer.eos_token_id}
        def generate(prompts, seed):
            batch = tokenizer.apply_chat_template([[{'role': 'user', 'content': p}] for p in prompts],
                tokenize=True, add_generation_prompt=True, padding=True, return_tensors='pt', return_dict=True)
            if batch['input_ids'].shape[1] > cfg['max_prompt_tokens']:
                raise ValueError('Frozen prompt exceeds context cap; no truncation')
            limit(tokens=len(prompts) * cfg['max_action_tokens'])
            torch.manual_seed(seed)
            generation_started = time.monotonic()
            with torch.inference_mode():
                sequences = model.generate(**batch, do_sample=True, temperature=cfg['temperature'], top_p=1.0,
                    top_k=0, repetition_penalty=1.0, max_new_tokens=cfg['max_action_tokens'],
                    logits_to_keep=1, pad_token_id=tokenizer.pad_token_id, eos_token_id=sorted(eos))
            outputs = []
            for sequence in sequences[:, batch['input_ids'].shape[1]:].tolist():
                ids = sequence
                for j, token in enumerate(ids):
                    if token in eos:
                        ids = ids[:j + 1]
                        break
                counts['generated_tokens'] += len(ids)
                outputs.append(dict(action_ids=ids, tactic=tokenizer.decode(ids, skip_special_tokens=True),
                    capped=completion_capped(ids, eos, cfg['max_action_tokens']), generated_tokens=len(ids),
                    eos_seen=any(i in eos for i in ids), generation_batch_seconds=time.monotonic()-generation_started))
            return outputs
        with (args.output / 'primary.jsonl').open('x') as stream, (args.output / 'candidates.jsonl').open('x') as candidates:
            for index, state in enumerate(states):
                limit(calls=8)
                episode = rl.LeanEpisode(ctx, state['task'], args.output, counts, max_repair_attempts=0)
                actual = episode.reset()
                if ' '.join(actual.split()) != ' '.join(state['goals'].split()):
                    raise ValueError('Frozen state differs after replay: ' + state['id'])
                for sample in range(cfg['samples']):
                    seed = cfg['seed'] + index * cfg['samples'] + sample
                    order = list(CONDITIONS)
                    random.Random(seed).shuffle(order)
                    limit(calls=32)
                    outputs = generate([condition_prompt(state, c) for c in order], seed)
                    for condition, output in zip(order, outputs):
                        candidates.write(json.dumps(dict(state_id=state['id'], sample=sample, condition=condition,
                                                         seed=seed, **output), ensure_ascii=False)+'\n')
                    candidates.flush()
                    for condition, output in zip(order, outputs):
                        limit(calls=8)
                        episode.reset()
                        result = episode.step(output['tactic'], 0.0)
                        row = dict(state_id=state['id'], root_name=state['task']['name'], stratum=state['stratum'],
                            assistance=state['assistance'], condition=condition, sample=sample, seed=seed,
                            condition_order=order, **output, result=result,
                            accepted=bool(result.get('accepted')),
                            valid=result['status'] in ('changed','accepted','repeated_state'),
                            error_class=outcome_class(result, output['capped']))
                        rows.append(row)
                        stream.write(json.dumps(row, ensure_ascii=False) + '\n'); stream.flush(); save()
                episode.close(); episode = None
        run['phase'] = 'continuation'
        save()
        lookup = {s['id']: s for s in states}
        with (args.output / 'continuation.jsonl').open('x') as stream, (args.output / 'candidates.jsonl').open('a') as candidates, (args.output / 'continuation_steps.jsonl').open('x') as step_stream:
            for row in rows:
                if row['result']['status'] != 'changed':
                    continue
                state = lookup[row['state_id']]
                limit(calls=8)
                episode = rl.LeanEpisode(ctx, state['task'], args.output, counts, max_repair_attempts=0)
                episode.reset()
                repeated_first = episode.step(row['tactic'], 0.0)
                if repeated_first['status'] != 'changed':
                    raise RuntimeError('Accepted primary action did not reproduce its verified state')
                steps = []
                for depth in range(cfg['continuation_steps']):
                    current = dict(state, goals=episode.prompt_state())
                    output = generate([condition_prompt(current, row['condition'])], row['seed'] + 100000 + depth)[0]
                    candidates.write(json.dumps(dict(state_id=row['state_id'], sample=row['sample'],
                        condition=row['condition'], phase='continuation', depth=depth, **output), ensure_ascii=False)+'\n')
                    candidates.flush()
                    limit(calls=8)
                    result = episode.step(output['tactic'], 0.0)
                    steps.append(dict(**output, result=result))
                    step_stream.write(json.dumps(dict(state_id=row['state_id'], sample=row['sample'],
                        condition=row['condition'], depth=depth, **steps[-1]), ensure_ascii=False)+'\n')
                    step_stream.flush(); save()
                    if result['done']:
                        break
                item = dict(state_id=row['state_id'], root_name=row['root_name'], condition=row['condition'],
                            sample=row['sample'], assistance=row['assistance'], steps=steps,
                            accepted=bool(steps[-1]['result'].get('accepted')), complete_budget=True)
                continuations.append(item)
                stream.write(json.dumps(item, ensure_ascii=False)+'\n'); stream.flush(); save()
                episode.close(); episode = None
        run['status'] = 'completed'
    except CapReached as error:
        run.update(status='capped', stop_reason=str(error))
    except Exception as error:
        run.update(status='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        signal.alarm(0)
        if episode is not None:
            episode.close()
        if ctx is not None:
            for repl in ctx.pool_repls:
                rl.close_repl(repl)
        run['continuation_attempted'] = len(continuations)
        run['continuation_success'] = sum(r['accepted'] for r in continuations)
        save()
    print(json.dumps(run))


if __name__ == '__main__':
    main()
