"""Sampling backends, stop-token handling, and reproducible generation seeds.

Model libraries are imported only when a backend is constructed or called.
Generation records preserve terminal tokens and count the sampled tokens.
"""
import hashlib


def generation_record(ids, eos_token_id, pad_token_id):
    """Keep EOS in the action likelihood; distinguish retained tokens from decode work."""
    stops = eos_token_id if isinstance(eos_token_id, list) else [eos_token_id]
    end = next((i + 1 for i, token in enumerate(ids) if token in stops), len(ids))
    action = ids[:end]
    return dict(raw_response_ids=ids, action_ids=action, generated_tokens=len(ids),
                retained_tokens=len(action), pad_before_eos=any(
                    token == pad_token_id and token not in stops for token in action))


def stop_trimmed(ids, stops):
    """Cut batch padding after the first stop token; one unpadded sample is unchanged."""
    end = next((i + 1 for i, token in enumerate(ids) if token in stops), len(ids))
    return ids[:end]


def pad_left(prompts, pad_id):
    """Left-pad token lists for one batched generate call; the attention mask hides every pad."""
    width = max(len(p) for p in prompts)
    return ([[pad_id] * (width - len(p)) + list(p) for p in prompts],
            [[0] * (width - len(p)) + [1] * len(p) for p in prompts], width)


def vllm_action_ids(completion, stops):
    """Token ids of one vLLM completion with its terminating stop token kept, as the HF path keeps it."""
    ids = list(completion.token_ids)
    if completion.finish_reason == 'stop' and (not ids or ids[-1] not in stops):
        reason = completion.stop_reason
        ids.append(reason if isinstance(reason, int) and reason in stops else stops[0])
    return ids


def step_seed(seed, task_id, visit, sample, step):
    """Reproducible sampling stream per (task visit, sample, step) for lockstep rollouts."""
    return int(hashlib.sha256(f'{seed}|{task_id}|{visit}|{sample}|{step}'.encode()).hexdigest()[:8], 16)


class HFGenerator:
    """Batched sampling from the live training model: several prompts, n samples each, one generate call.

    Uses the trainer's untruncated temperature-1 policy. Rows share one random stream seeded from the
    batch's per-sample seeds, so a sample is reproducible for the same active set, not for any batch."""

    def __init__(self, model, tokenizer, torch, autocast, device, stops, max_new_tokens, set_seed):
        self.model, self.tokenizer, self.torch, self.autocast, self.device = model, tokenizer, torch, autocast, device
        self.stops, self.max_new_tokens, self.set_seed = stops, max_new_tokens, set_seed

    def __call__(self, prompts, n, seeds):
        torch = self.torch
        self.set_seed(int(hashlib.sha256(repr(list(seeds)).encode()).hexdigest()[:8], 16))
        ids, mask, width = pad_left(prompts, self.tokenizer.pad_token_id)
        with torch.no_grad(), self.autocast():
            rows = self.model.generate(
                input_ids=torch.tensor(ids, device=self.device), attention_mask=torch.tensor(mask, device=self.device),
                do_sample=True, temperature=1.0, top_p=1.0, top_k=0, repetition_penalty=1.0,
                max_new_tokens=self.max_new_tokens, pad_token_id=self.tokenizer.eos_token_id,
                num_return_sequences=n)[:, width:].tolist()
        return [[generation_record(stop_trimmed(rows[i * n + j], self.stops), self.stops, self.tokenizer.pad_token_id)
                 for j in range(n)] for i in range(len(prompts))]


class VLLMGenerator:
    """Evaluation-only sampling through vLLM with the same untruncated temperature-1 policy.

    Each prompt carries its own request seed, so a sample does not depend on the rest of the batch.
    vLLM samples in bfloat16; check_rollout_backends.py must pass before its results count."""

    def __init__(self, model_path, stops, pad_id, max_new_tokens, memory_fraction, seed):
        from vllm import LLM
        self.llm = LLM(model=str(model_path), dtype='bfloat16', seed=seed, gpu_memory_utilization=memory_fraction,
                       enable_prefix_caching=True)
        self.stops, self.pad_id, self.max_new_tokens = stops, pad_id, max_new_tokens

    def __call__(self, prompts, n, seeds):
        from vllm import SamplingParams
        params = [SamplingParams(n=n, temperature=1.0, top_p=1.0, top_k=-1, repetition_penalty=1.0, seed=s,
                                 max_tokens=self.max_new_tokens, stop_token_ids=list(self.stops), detokenize=False)
                  for s in seeds]
        outputs = self.llm.generate([dict(prompt_token_ids=list(p)) for p in prompts], params, use_tqdm=False)
        return [[generation_record(stop_trimmed(vllm_action_ids(c, self.stops), self.stops), self.stops, self.pad_id)
                 for c in output.outputs] for output in outputs]
