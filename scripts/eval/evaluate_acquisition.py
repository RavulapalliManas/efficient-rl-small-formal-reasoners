"""Evaluation only: recognition, recall, and elementary number-theory application.

One checkpoint per process. FP32 weights: 0.5e9 x 4 bytes <2 GB.
Legacy probes use batch one. Free responses use at most batch four,
2048 prompt tokens and 1024 completion tokens. With logits_to_keep=1,
logits <=4 x 1 x 151936 x 4 <0.003 GB; KV cache
<=4 x 24 x 2 x 2 x 64 x 3072 x 4 <0.31 GB. SDPA prefill attention
if materialized <=4 x 14 x 2048 x 2048 x 4 <0.94 GB; MLP intermediate
<=4 x 2048 x 4864 x 4 <0.16 GB. Allow 8 GiB for loading, runtime and
workspaces; enforce this with the launcher's cgroup. No optimizer or teacher.
"""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import resource
import signal
import time


def parse_integer(text):
    text = text.strip()
    for pattern in (r'([+-]?\d+)\.?', r'\$([+-]?\d+)\$', r'\\boxed\{\s*([+-]?\d+)\s*\}'):
        match = re.fullmatch(pattern, text)
        if match:
            return int(match.group(1))
    return None


def task_messages(task):
    # Review fields never enter the chat. Supplied premises belong in prompt.
    return [{'role': 'user', 'content': task['prompt']}]


def trim_completion(ids, eos_ids, pad_id, limit):
    for index, token in enumerate(ids):
        if token in eos_ids:
            return ids[:index + 1], False
    capped = len(ids) >= limit
    while ids and ids[-1] == pad_id:
        ids = ids[:-1]
    return ids, capped


def generate_tasks(model, tokenizer, cfg, label):
    """Yield one auditable row per task; preserve each batch before the next."""
    import torch

    batch_size = cfg.get('batch_size', 4)
    limit = cfg['max_new_tokens']
    if not (1 <= batch_size <= 4 and 1 <= limit <= 1024
            and 1 <= cfg['max_prompt_tokens'] <= 2048):
        raise ValueError('Free-response settings exceed the documented memory bound')
    if any(not 1 <= n <= limit for n in cfg['length_views']):
        raise ValueError('Length views must fit within the generation limit')
    eos_ids = set()
    for owner in (tokenizer, model.config, model.generation_config):
        value = getattr(owner, 'eos_token_id', None)
        eos_ids.update(value if isinstance(value, (list, tuple)) else [value] if value is not None else [])
    if not eos_ids:
        raise ValueError('No EOS token configured')
    tokenizer.padding_side = 'left'
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = min(eos_ids)
    for offset in range(0, len(cfg['tasks']), batch_size):
        tasks = cfg['tasks'][offset:offset + batch_size]
        messages = [task_messages(task) for task in tasks]
        batch = tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, padding=True,
            return_tensors='pt', return_dict=True)
        width = batch['input_ids'].shape[1]
        if width > cfg['max_prompt_tokens']:
            raise ValueError('Prompt exceeds registered limit; no truncation permitted')
        started = time.monotonic()
        with torch.inference_mode():
            sequences = model.generate(
                **batch, max_new_tokens=limit, do_sample=False, num_beams=1,
                pad_token_id=tokenizer.pad_token_id, eos_token_id=sorted(eos_ids),
                logits_to_keep=1)
        elapsed = time.monotonic() - started
        for index, task in enumerate(tasks):
            raw_ids = sequences[index, width:].tolist()
            ids, capped = trim_completion(raw_ids, eos_ids, tokenizer.pad_token_id, limit)
            row = {key: task[key] for key in
                   ('id', 'family', 'category', 'condition', 'prompt', 'rubric', 'reference', 'source')}
            row.update(model=label, kind=task['category'], accepted=None, parsed=None,
                       response=tokenizer.decode(ids, skip_special_tokens=True),
                       completion_ids=ids, raw_completion_ids=raw_ids,
                       generated_tokens=sum(t != tokenizer.pad_token_id or t in eos_ids for t in ids),
                       prompt_tokens=int(batch['attention_mask'][index].sum()),
                       capped=capped, eos_token_ids=sorted(eos_ids),
                       batch_index=offset // batch_size, batch_wall_seconds=elapsed,
                       length_views={str(n): {
                           'response': tokenizer.decode(ids[:n], skip_special_tokens=True),
                           'capped': not any(t in eos_ids for t in ids[:n]) and len(ids) >= n,
                           'completion_ids': ids[:n]} for n in cfg['length_views']})
            yield row
        del batch, sequences


def recognition_prompt(item, rotation):
    choices = item['choices'][rotation:] + item['choices'][:rotation]
    target = 'ABCD'[(item['correct'] - rotation) % 4]
    prompt = item['recognition'] + '\n' + '\n'.join(
        f'{label}. {choice}' for label, choice in zip('ABCD', choices, strict=True))
    return prompt + '\nReturn only the answer letter.', target


def application_answer(item):
    op, args = item['operation'], item['operands']
    if op == 'quotient':
        assert args[0] % args[1] == 0
        return args[0] // args[1]
    if op == 'remainder':
        return args[0] % args[1]
    if op == 'gcd':
        return math.gcd(*args)
    if op == 'linear':
        a, x, b, y = args
        return a * x + b * y
    if op in ('coprime', 'cancel'):
        return int(math.gcd(*args) == 1)
    if op == 'congruence':
        a, b, m = args
        return int((a - b) % m == 0)
    if op == 'inverse':
        return pow(args[0], -1, args[1])
    if op == 'prime':
        n = args[0]
        return int(n > 1 and all(n % d for d in range(2, math.isqrt(n) + 1)))
    if op == 'euclid_factor':
        p, a, b = args
        assert a * b % p == 0 and b % p != 0
        return a
    if op == 'fermat':
        a, p = args
        assert p > 1 and all(p % d for d in range(2, math.isqrt(p) + 1)) and a % p
        return pow(a, p - 1, p)
    if op == 'factor_count':
        n = args[0]
        return sum(n % p == 0 and all(p % d for d in range(2, math.isqrt(p) + 1))
                   for p in range(2, n + 1))
    raise ValueError(op)


def summarize(rows):
    result = {}
    for kind in sorted({r['kind'] for r in rows}):
        subset = [r for r in rows if r['kind'] == kind]
        result[kind] = {'n': len(subset)}
        if all(r['accepted'] is not None for r in subset):
            result[kind].update(correct=sum(r['accepted'] for r in subset),
                                unparsed=sum(not r['parsed'] for r in subset))
        else:
            result[kind]['status'] = 'Requires blinded semantic review against frozen rubric'
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--revision')
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    def timeout(signum, frame):
        raise TimeoutError('Registered per-model wall limit reached')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(cfg['max_wall_seconds_per_model'])
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    torch.set_num_threads(cfg['cpu_threads'])
    torch.set_num_interop_threads(1)
    torch.manual_seed(cfg['seed'])
    kwargs = {'revision': args.revision} if args.revision else {'local_files_only': True}
    tokenizer = AutoTokenizer.from_pretrained(args.model, **kwargs)
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32,
                                               attn_implementation='sdpa', **kwargs).eval()
    for param in model.parameters():
        param.requires_grad_(False)
    run = {'status': 'running', 'model': args.model, 'label': args.label,
           'revision': args.revision, 'config': cfg, 'device': 'cpu', 'dtype': 'float32',
           'versions': {p: importlib.metadata.version(p) for p in ('torch', 'transformers', 'safetensors')},
           'config_sha256': hashlib.sha256(args.config.read_bytes()).hexdigest(),
           'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           'chat_template_sha256': hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
           'model_config': model.config.to_dict(), 'generated_tokens': 0, 'prompt_tokens': 0,
           'training_updates': 0, 'physical_lean_calls': 0, 'rows': 0}
    rows = []
    def save():
        run['wall_seconds'] = time.monotonic() - started
        run['max_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        (args.output / 'run.json').write_text(json.dumps(run, indent=2) + '\n')
        (args.output / 'summary.json').write_text(json.dumps(summarize(rows), indent=2) + '\n')
    save()
    if 'tasks' in cfg:
        run['tokenizer'] = {'class': type(tokenizer).__name__,
                            'name_or_path': tokenizer.name_or_path,
                            'padding_side': 'left',
                            'eos_token_id': tokenizer.eos_token_id,
                            'pad_token_id': tokenizer.pad_token_id}
        run['generation_config'] = model.generation_config.to_dict()
        with (args.output / 'responses.jsonl').open('x') as handle:
            for row in generate_tasks(model, tokenizer, cfg, args.label):
                handle.write(json.dumps(row) + '\n')
                handle.flush()
                rows.append(row)
                run['rows'] = len(rows)
                run['generated_tokens'] += row['generated_tokens']
                run['prompt_tokens'] += row['prompt_tokens']
                save()
                print(json.dumps({'model': args.label, 'id': row['id'],
                                  'kind': row['kind'], 'rows': len(rows),
                                  'generated_tokens': row['generated_tokens'],
                                  'capped': row['capped']}), flush=True)
        run['status'] = 'complete'
        save()
        print(json.dumps(summarize(rows)), flush=True)
        return
    label_ids = [tokenizer.encode(c, add_special_tokens=False) for c in 'ABCD']
    if any(len(ids) != 1 for ids in label_ids):
        raise ValueError('Recognition requires single-token option labels')
    label_ids = [ids[0] for ids in label_ids]
    with (args.output / 'responses.jsonl').open('x') as handle:
        for item in cfg['items']:
            conditions = [('recall', None), ('application', None)] + [
                ('recognition', rotation) for rotation in cfg['recognition_rotations']]
            for kind, rotation in conditions:
                target = None
                if kind == 'recognition':
                    prompt, target = recognition_prompt(item, rotation)
                elif kind == 'recall':
                    prompt = item['recall'] + '\nAnswer concisely. Do not give a proof or write code.'
                else:
                    prompt = item['application'] + '\nReturn only the final integer. Do not explain or write code.'
                    target = application_answer(item)
                messages = [{'role': 'user', 'content': prompt}]
                batch = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                       return_tensors='pt', return_dict=True)
                n_prompt = batch['input_ids'].shape[1]
                if n_prompt > cfg['max_prompt_tokens']:
                    raise ValueError('Prompt exceeds registered limit; no truncation permitted')
                run['prompt_tokens'] += n_prompt
                row = {'id': item['id'], 'model': args.label, 'kind': kind, 'rotation': rotation,
                       'prompt': prompt, 'target': target, 'prompt_tokens': n_prompt}
                with torch.inference_mode():
                    if kind == 'recognition':
                        logits = model(**batch, use_cache=False).logits[0, -1].float()
                        logp = torch.log_softmax(logits, dim=0)
                        scores = logp[label_ids].tolist()
                        answer = 'ABCD'[max(range(4), key=lambda i: scores[i])]
                        row.update(option_logp=dict(zip('ABCD', scores, strict=True)),
                                   option_probability_mass=sum(math.exp(v) for v in scores),
                                   response=answer, parsed=True, accepted=answer == target,
                                   completion_ids=[], generated_tokens=0, capped=False)
                    else:
                        limit = cfg['max_new_tokens'][kind]
                        seq = model.generate(**batch, max_new_tokens=limit, do_sample=False,
                                             pad_token_id=tokenizer.eos_token_id)
                        ids = seq[0, n_prompt:].tolist()
                        response = tokenizer.decode(ids, skip_special_tokens=True)
                        answer = parse_integer(response) if kind == 'application' else None
                        row.update(response=response, completion_ids=ids, generated_tokens=len(ids),
                                   capped=len(ids) >= limit,
                                   parsed=answer is not None if kind == 'application' else None,
                                   accepted=answer == target if kind == 'application' else None,
                                   parsed_answer=answer, rubric=item['rubric'] if kind == 'recall' else None)
                        run['generated_tokens'] += len(ids)
                handle.write(json.dumps(row) + '\n')
                handle.flush()
                rows.append(row)
                run['rows'] = len(rows)
                save()
                print(json.dumps({'model': args.label, 'id': item['id'], 'kind': kind,
                                  'accepted': row['accepted'], 'rows': len(rows)}), flush=True)
    run['status'] = 'complete'
    counts = Counter(application_answer(item) for item in cfg['items'])
    run['constant_application_baseline'] = {'correct': max(counts.values()), 'n': len(cfg['items'])}
    recognition_targets = [recognition_prompt(item, r)[1] for item in cfg['items']
                           for r in cfg['recognition_rotations']]
    run['constant_recognition_baseline'] = dict(Counter(recognition_targets))
    save()
    print(json.dumps(summarize(rows)), flush=True)


if __name__ == '__main__':
    main()
