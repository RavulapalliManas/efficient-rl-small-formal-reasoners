"""GPU check that batched HF and vLLM sampling match single-prompt HF sampling of the tactic policy.

Usage: check_rollout_backends.py --model DIR (--rollouts ROLLOUTS.jsonl | --tasks TASKS.jsonl) --output DIR [--vllm]
Run it as the first GPU stage before a lockstep or vLLM run counts; exit 1 on failure.

Prompts are real observations: prompt_ids from an existing rollouts.jsonl, or first-step observations of
--tasks rendered with the trainer's render_observation and chat template. Backends: HF one prompt per
call (the sequential trainer path), HF left-padded batch (HFGenerator) and, with --vllm, VLLMGenerator.
1. Greedy decoding: the action tokens of each backend must equal single-prompt HF for at least
   --min-greedy-match of prompts (bfloat16 can flip near ties, so exact equality is not required).
2. Sampling at temperature 1: per prompt, the mean per-token log-probability of the samples under the HF
   model, the mean action length and the stop fraction. A paired sign-flip permutation test against
   single-prompt HF must give p >= --alpha for each statistic and backend.
Memory (GPU): one FP32 0.5B HF model (~2 GiB) plus vLLM at --vllm-memory-fraction.
"""
import argparse
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'train'))


def sign_flip_p(differences, replicates=10000, seed=20261018):
    """Two-sided paired permutation p-value for a zero mean difference; (count + 1) / (replicates + 1)."""
    n = len(differences)
    observed = abs(sum(differences)) / n
    rng = random.Random(seed)
    extreme = sum(abs(sum(d if rng.random() < 0.5 else -d for d in differences)) / n >= observed - 1e-12
                  for _ in range(replicates))
    return (extreme + 1) / (replicates + 1)


def divergence_gaps(reference, others, logprobs_at):
    """Diagnostic for greedy mismatches: at the first differing token of each pair, the reference model's
    log-probability of the reference token minus that of the other token. A gap near zero is a near tie that
    a precision change can flip; a large gap points to a real backend difference. logprobs_at(i, prefix) gives
    the reference log-probabilities after prompt i plus prefix. Pairs that never differ, or differ only in
    length, are counted, not scored. Returns (gaps, identical, length_only)."""
    gaps, identical, length_only = [], 0, 0
    for i, (a, b) in enumerate(zip(reference, others)):
        k = next((j for j, (x, y) in enumerate(zip(a, b)) if x != y), None)
        if k is None:
            identical += a == b
            length_only += a != b
            continue
        lp = logprobs_at(i, a[:k])
        gaps.append(float(lp[a[k]] - lp[b[k]]))
    return gaps, identical, length_only


def main():
    parser = argparse.ArgumentParser()
    for name in ('model', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--rollouts', type=Path)
    source.add_argument('--tasks', type=Path)
    parser.add_argument('--horizon', type=int, default=6)
    parser.add_argument('--max-tactics', type=int, default=4)
    parser.add_argument('--n-prompts', type=int, default=48)
    parser.add_argument('--samples', type=int, default=4)
    parser.add_argument('--max-new-tokens', type=int, default=192)
    parser.add_argument('--min-greedy-match', type=float, default=0.8)
    parser.add_argument('--alpha', type=float, default=0.01)
    parser.add_argument('--vllm', action='store_true')
    parser.add_argument('--vllm-memory-fraction', type=float, default=0.3)
    # Diagnostics only; neither changes the pass criterion. --device cpu allows a CPU smoke run without --vllm.
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--precision-control', action='store_true',
                        help='also report greedy HF FP32 (no autocast) against HF autocast, and divergence gaps')
    args = parser.parse_args()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig, set_seed
    from run_tactic_rl import action_logprob, render_observation
    from rl_components.generation import (
        HFGenerator, VLLMGenerator, generation_record, pad_left, stop_trimmed, vllm_action_ids,
    )
    args.output.mkdir(parents=True, exist_ok=False)
    prompts, seen = [], set()
    if args.tasks:
        tokenizer = AutoTokenizer.from_pretrained(args.model)
        with args.tasks.open() as stream:
            for line in list(stream)[:args.n_prompts]:
                task = json.loads(line)
                content = render_observation(task, [], [task['state']], '', [], args.horizon, args.max_tactics)
                text = tokenizer.apply_chat_template([{'role': 'user', 'content': content}], tokenize=False,
                                                     add_generation_prompt=True)
                prompts.append(tokenizer(text, add_special_tokens=False)['input_ids'])
    for line in (args.rollouts.open() if args.rollouts else []):
        ids = json.loads(line).get('prompt_ids')
        if ids and tuple(ids) not in seen:
            seen.add(tuple(ids))
            prompts.append(ids)
        if len(prompts) == args.n_prompts:
            break
    device = args.device
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.float32).to(device).eval()
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    stops = GenerationConfig.from_pretrained(args.model).eos_token_id
    stops = stops if isinstance(stops, list) else [stops]
    autocast = lambda: torch.autocast(device, dtype=torch.bfloat16)  # noqa: E731
    batched = HFGenerator(model, tokenizer, torch, autocast, device, stops, args.max_new_tokens, set_seed)

    def single(batch, n, seeds, greedy=False):
        out = []
        for ids, seed in zip(batch, seeds):
            set_seed(seed)
            with torch.no_grad(), autocast():
                rows = model.generate(input_ids=torch.tensor([ids], device=device), do_sample=not greedy,
                                      temperature=1.0, top_p=1.0, top_k=0, repetition_penalty=1.0,
                                      max_new_tokens=args.max_new_tokens, pad_token_id=tokenizer.eos_token_id,
                                      num_return_sequences=n)[:, len(ids):].tolist()
            out.append([generation_record(stop_trimmed(r, stops), stops, tokenizer.pad_token_id) for r in rows])
        return out
    backends = {'hf_single': single, 'hf_batched': batched}
    if args.vllm:
        backends['vllm'] = VLLMGenerator(args.model, stops, tokenizer.pad_token_id, args.max_new_tokens,
                                         args.vllm_memory_fraction, 20261018)
    seeds = list(range(len(prompts)))
    report = dict(prompts=len(prompts), samples=args.samples, greedy={}, sampled={}, passed=True)
    # Greedy: HF backends via do_sample=False; vLLM via temperature 0 in a dedicated call.
    greedy = {'hf_single': [g[0]['action_ids'] for g in single(prompts, 1, seeds, greedy=True)]}
    with torch.no_grad(), autocast():
        ids, mask, width = pad_left(prompts, tokenizer.pad_token_id)
        # Explicit settings: the checkpoint's generation_config carries repetition_penalty 1.1 (first Lease 3 run).
        rows = model.generate(input_ids=torch.tensor(ids, device=device), attention_mask=torch.tensor(mask, device=device),
                              do_sample=False, temperature=1.0, top_p=1.0, top_k=0, repetition_penalty=1.0,
                              max_new_tokens=args.max_new_tokens, pad_token_id=tokenizer.eos_token_id)[:, width:].tolist()
    greedy['hf_batched'] = [stop_trimmed(r, stops) for r in rows]
    if args.vllm:
        from vllm import SamplingParams
        outputs = backends['vllm'].llm.generate([dict(prompt_token_ids=p) for p in prompts], SamplingParams(
            temperature=0.0, max_tokens=args.max_new_tokens, stop_token_ids=stops, detokenize=False), use_tqdm=False)
        greedy['vllm'] = [stop_trimmed(vllm_action_ids(o.outputs[0], stops), stops) for o in outputs]
    def first_divergence(a, b):
        return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    for name in [b for b in backends if b != 'hf_single']:
        match = sum(a == b for a, b in zip(greedy['hf_single'], greedy[name])) / len(prompts)
        report['greedy'][name] = match
        # Diagnostic only; the pass criterion stays exact action match.
        report.setdefault('greedy_first_divergence_mean', {})[name] = sum(
            first_divergence(a, b) for a, b in zip(greedy['hf_single'], greedy[name])) / len(prompts)
        report['passed'] &= match >= args.min_greedy_match
    if args.precision_control:
        # Precision floor: the same HF model and prompts, FP32 without autocast. Not part of the pass criterion.
        fp32 = []
        for ids in prompts:
            with torch.no_grad():
                row = model.generate(input_ids=torch.tensor([ids], device=device), do_sample=False, temperature=1.0,
                                     top_p=1.0, top_k=0, repetition_penalty=1.0, max_new_tokens=args.max_new_tokens,
                                     pad_token_id=tokenizer.eos_token_id)[0, len(ids):].tolist()
            fp32.append(stop_trimmed(row, stops))

        def logprobs_at(i, prefix):
            with torch.no_grad(), autocast():
                ids = torch.tensor([prompts[i] + list(prefix)], device=device)
                return model(input_ids=ids).logits[0, -1].float().log_softmax(-1).tolist()
        control = report['precision_control'] = {}
        for name, rows in [('hf_single_fp32', fp32)] + [(b, greedy[b]) for b in backends if b != 'hf_single']:
            gaps, identical, length_only = divergence_gaps(greedy['hf_single'], rows, logprobs_at)
            ordered = sorted(gaps)
            control[name] = dict(exact_match=identical / len(prompts), length_only=length_only, scored=len(gaps),
                                 gap_median=ordered[len(ordered) // 2] if ordered else None,
                                 gap_max=ordered[-1] if ordered else None, gaps=gaps)
    # Sampling: statistics per prompt for each backend, then paired tests against single-prompt HF.
    stats = {}
    for name, generate in backends.items():
        per_prompt = []
        for ids, gens in zip(prompts, generate(prompts, args.samples, [1000 + s for s in seeds])):
            logprobs, lengths, stopped = [], [], []
            for g in gens:
                with torch.no_grad(), autocast():
                    total = float(action_logprob(model, ids, g['action_ids'], torch))
                logprobs.append(total / max(1, len(g['action_ids'])))
                lengths.append(len(g['action_ids']))
                stopped.append(bool(g['action_ids']) and g['action_ids'][-1] in stops)
            n = len(gens)
            per_prompt.append(dict(logprob=sum(logprobs) / n, length=sum(lengths) / n, stopped=sum(stopped) / n))
        stats[name] = per_prompt
    for name in [b for b in backends if b != 'hf_single']:
        report['sampled'][name] = {}
        for key in ('logprob', 'length', 'stopped'):
            diffs = [b[key] - a[key] for a, b in zip(stats['hf_single'], stats[name])]
            p = sign_flip_p(diffs)
            report['sampled'][name][key] = dict(mean_difference=sum(diffs) / len(diffs), p=p,
                                                hf_single_mean=sum(a[key] for a in stats['hf_single']) / len(diffs))
            report['passed'] &= p >= args.alpha
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    sys.exit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
