"""Pinned TRL PPO with the existing binary Lean scorer, not a learned reward model.

One process, one GPU, no automatic evaluation or checkpoint selection.
Peak estimate: policy + critic + reference, 3 x 0.5e9 x 4 bytes = 6 GB weights;
2 x 0.5e9 x 4 = 4 GB gradients; 2 x 0.5e9 x 8 = 8 GB Adam moments.
Reserve 12 GB for activations and 8 GB for two Lean workers. Use a 56 GiB host cap.
With microbatch 2, one full-vocabulary logits tensor is 2 x 2304 x 152000 x 4 = 2.8 GB.
This is an estimate, not measured GPU memory. The managed runner supplies hard caps.
"""
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from rl_components.objectives import proof_rewards
import json
import math
import time
from types import SimpleNamespace

import torch


def checked_generate(generate, tokenizer, observe, model, queries, pad_token_id, generation_config):
    """Record decode work before TRL padding and stop before an ambiguous likelihood update."""
    from rl_components.generation import generation_record
    result = generate(model, queries, pad_token_id, generation_config)
    sequences, logits = result
    responses = sequences[:, queries.shape[1]:]
    if responses.shape[1] != logits.shape[1]:
        raise RuntimeError('Generation score count differs from response width')
    records = [generation_record(ids, tokenizer.eos_token_id, tokenizer.pad_token_id)
               for ids in responses.tolist()]
    if observe is not None:
        observe(queries.tolist(), records)
    if any(row['pad_before_eos'] for row in records):
        raise RuntimeError('Sampled PAD before EOS; refuse an unmatched training update')
    return result


def ppo_masked_mean(values, mask, axis=None):
    """An empty scalar policy reduction contributes zero loss and zero gradient."""
    from trl.core import masked_mean
    if axis is None and not mask.any():
        return (values * mask).sum()
    return masked_mean(values, mask, axis=axis)


class _LeanBackbone(torch.nn.Module):
    def __init__(self, evaluate):
        super().__init__()
        self.evaluate = evaluate

    def forward(self, input_ids, attention_mask, **kwargs):
        rewards = self.evaluate(input_ids, attention_mask)
        hidden = input_ids.new_tensor(rewards, dtype=torch.float32)[:, None, None]
        return SimpleNamespace(hidden_states=(hidden.expand(-1, input_ids.shape[1], 1),))


class LeanRewardModel(torch.nn.Module):
    """TRL get_reward interface. Exact query identity comes from the collated prompt width."""
    base_model_prefix = 'lean'

    def __init__(self, tokenizer, prompts, reward):
        super().__init__()
        self.tokenizer, self.reward = tokenizer, reward
        self.prompts = {}
        for ids, task_id in prompts:
            key = tuple(ids)
            if key in self.prompts and self.prompts[key] != task_id:
                raise ValueError('ambiguous registered prompt')
            self.prompts[key] = task_id
        self.context_length = None
        self.lean = _LeanBackbone(self.evaluate)
        self.score = torch.nn.Identity()

    def evaluate(self, input_ids, attention_mask):
        if self.context_length is None:
            raise ValueError('prompt width was not recorded by the collator')
        width = self.context_length
        task_ids, responses = [], []
        for ids, mask in zip(input_ids.tolist(), attention_mask.tolist()):
            query = tuple(token for token, keep in zip(ids[:width], mask[:width]) if keep)
            if query not in self.prompts:
                raise ValueError('unregistered prompt passed to Lean reward')
            task_ids.append(self.prompts[query])
            response = []
            for token, keep in zip(ids[width:], mask[width:]):
                if not keep:
                    break
                response.append(token)
            responses.append(response)
        texts = self.tokenizer.batch_decode(responses, skip_special_tokens=True)
        rewards = self.reward(task_ids, texts, responses)
        if len(rewards) != len(task_ids) or any(r not in (0.0, 1.0) for r in rewards):
            raise ValueError('Lean scorer must return one binary reward per response')
        return rewards


def make_trainer(config, tokenizer, policy, critic, reference, dataset, reward, output, callbacks):
    from importlib.metadata import version
    from torch.utils.data import DataLoader
    from transformers import DataCollatorWithPadding
    from trl import PPOConfig, PPOTrainer
    from trl.trainer.utils import generate as pinned_generate
    from unittest.mock import patch

    if version('trl') != '0.24.0':
        raise ValueError('Lean PPO requires TRL 0.24.0')
    if (config['top_p'] != 1.0 or config['top_k'] != 0 or config['repetition_penalty'] != 1.0
            or config['use_vllm'] or config['shuffle_rewards']):
        raise ValueError('Pinned PPO supports full-distribution HF sampling and true Lean rewards only')
    if tokenizer.pad_token_id is None or tokenizer.pad_token_id == tokenizer.eos_token_id:
        raise ValueError('PPO requires distinct PAD and EOS IDs')
    tokenizer.padding_side = 'left'
    critic.config.pad_token_id = tokenizer.pad_token_id
    policy.generation_config.update(do_sample=True, top_p=1.0, top_k=0, repetition_penalty=1.0,
                                    pad_token_id=tokenizer.pad_token_id, temperature=config['temperature'])
    padding = DataCollatorWithPadding(tokenizer)

    def collate(rows):
        batch = padding(rows)
        reward.context_length = batch['input_ids'].shape[1]
        return batch

    episodes = config['max_steps'] * config['prompts_per_step'] * config['num_generations']
    batch_size = config['prompts_per_step'] * config['num_generations']
    micro_batch = config.get('ppo_micro_batch_size', 2)
    if batch_size % micro_batch:
        raise ValueError('PPO microbatch must divide the rollout batch size')
    settings = PPOConfig(
        output_dir=str(output), total_episodes=episodes, num_mini_batches=1,
        per_device_train_batch_size=micro_batch,
        gradient_accumulation_steps=batch_size // micro_batch, num_ppo_epochs=config['num_ppo_epochs'],
        local_rollout_forward_batch_size=micro_batch, num_sample_generations=0,
        response_length=config['max_completion_tokens'], stop_token='eos', temperature=config['temperature'],
        kl_coef=config['beta'], seed=config['seed'], learning_rate=config['learning_rate'],
        lr_scheduler_type='constant', weight_decay=0.0, bf16=config['bf16'],
        gradient_checkpointing=config['gradient_checkpointing'], missing_eos_penalty=None,
        logging_steps=1, save_strategy='no', report_to='none', disable_tqdm=True)
    if config['gradient_checkpointing']:
        policy.gradient_checkpointing_enable()
        critic.gradient_checkpointing_enable()
    trainer = PPOTrainer(args=settings, processing_class=tokenizer, model=policy, value_model=critic,
                         ref_model=reference, reward_model=reward, train_dataset=dataset,
                         eval_dataset=dataset, data_collator=collate, callbacks=callbacks)
    if trainer.accelerator.num_processes != 1:
        raise ValueError('Lean PPO pilot supports one process only')
    if config['gradient_checkpointing']:
        # TRL 0.24 unwraps this wrapper, then generates with its policy.
        wrapper = trainer.accelerator.unwrap_model(trainer.model)
        wrapper.gradient_checkpointing_disable = wrapper.policy.gradient_checkpointing_disable
        wrapper.gradient_checkpointing_enable = wrapper.policy.gradient_checkpointing_enable
    # A synchronous loader keeps the collator's prompt width valid until this batch is scored.
    trainer.dataloader = DataLoader(
        dataset, batch_size=trainer.local_dataloader_batch_size, shuffle=False, drop_last=True,
        num_workers=0, collate_fn=collate)
    def generate(model, queries, pad_token_id, generation_config):
        reserve = getattr(reward, 'reserve_generation', None)
        if reserve is not None:
            reserve(queries.shape[0] * generation_config.max_new_tokens)
        return checked_generate(pinned_generate, tokenizer, getattr(reward, 'record_generation', None),
                                model, queries, pad_token_id, generation_config)
    # The utility sees each physical microbatch before batch_generation adds synthetic padding.
    trainer.train = patch('trl.trainer.utils.generate', new=generate)(trainer.train)
    # Scope the scalar reduction to this call; core rollout whitening stays unchanged.
    trainer.train = patch('trl.trainer.ppo_trainer.masked_mean', new=ppo_masked_mean)(trainer.train)
    return trainer


class ResourceCapReached(RuntimeError):
    pass


def run_ppo(args, config, ctx, by_id, dataset, tokenizer, run, score_fn=None):
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, TrainerCallback, set_seed
    from run_lean_grpo import over_budget, score

    scorer = score if score_fn is None else score_fn
    started = time.monotonic()
    counts = dict(generated_tokens=0, retained_tokens=0, generation_samples=0,
                  lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0,
                  samples=0, accepted=0, forbidden=0, timeouts=0, capped=0)
    rollouts = (args.output / 'rollouts.jsonl').open('x')
    generations = (args.output / 'generations.jsonl').open('x')
    pending_generations = deque()
    steps = (args.output / 'steps.jsonl').open('x')
    executor = ThreadPoolExecutor(config['verifier_workers'])
    gpus = torch.cuda.device_count() if torch.cuda.is_available() else 0
    trainer = None
    presented = set()
    stop_reason = 'requested_steps'

    def reserve_generation(tokens):
        if counts['generated_tokens'] + tokens > config['max_generated_tokens']:
            raise ResourceCapReached('physical generation token cap reached before next microbatch')

    def record_generation(queries, records):
        for query, record in zip(queries, records):
            key = tuple(token for token in query if token != tokenizer.pad_token_id)
            task_id = bridge.prompts[key]
            for field in ('generated_tokens', 'retained_tokens'):
                counts[field] += record[field]
            counts['generation_samples'] += 1
            pending_generations.append(record)
            generations.write(json.dumps(dict(step=trainer.state.global_step, task_id=task_id,
                status='generation_contract_violation' if record['pad_before_eos'] else 'generated',
                **record)) + '\n')
        generations.flush()

    def reward(task_ids, completions, completion_ids):
        presented.update(task_ids)
        texts, _, verdicts = scorer(ctx, by_id, task_ids, completions, executor)
        rewards = [float(v['status'] == 'accepted') for v in verdicts]
        for tid, text, ids, result, value in zip(task_ids, texts, completion_ids, verdicts, rewards):
            generation = pending_generations.popleft()
            if ids != generation['action_ids']:
                raise RuntimeError('PPO reward tokens differ from recorded action likelihood boundary')
            for key in ('lean_calls', 'lean_seconds', 'lean_cpu_seconds'):
                counts[key] += result[key]
            counts['samples'] += 1
            counts['accepted'] += value
            counts['capped'] += (len(ids) >= config['max_completion_tokens']
                                 and ids[-1] != tokenizer.eos_token_id)
            counts['forbidden'] += result['status'] == 'forbidden_construct'
            counts['timeouts'] += result['status'] in ('timeout', 'crash')
            rollouts.write(json.dumps(dict(step=trainer.state.global_step, task_id=tid, text=text,
                completion_ids=ids, **generation,
                reward=None if result.get('censored') or result['status'] in ('crash', 'replay_crash') else value,
                **result)) + '\n')
        rollouts.flush()
        return proof_rewards(verdicts)

    class Records(TrainerCallback):
        def on_log(self, _args, state, control, logs=None, **kwargs):
            wall = time.monotonic() - started
            record = dict(step=state.global_step, wall_seconds=wall, gpu_seconds=wall * gpus,
                          **counts, **(logs or {}))
            steps.write(json.dumps(record) + '\n')
            steps.flush()
            print(json.dumps(record), flush=True)
            if any(not math.isfinite(value) for key, value in (logs or {}).items()
                   if key in ('loss/policy_avg', 'loss/value_avg')):
                raise FloatingPointError('Non-finite PPO loss; raw metrics preserved')

        def on_step_end(self, _args, state, control, **kwargs):
            # TRL 0.24 PPO does not consume should_training_stop. An exception stops it after this batch.
            if over_budget(counts, time.monotonic() - started, config):
                raise ResourceCapReached('resource cap reached after rollout batch')

    try:
        set_seed(config['seed'])
        prompts = [(tokenizer(r['prompt'], add_special_tokens=False)['input_ids'], r['task_id']) for r in dataset]
        bridge = LeanRewardModel(tokenizer, prompts, reward)
        bridge.reserve_generation = reserve_generation
        bridge.record_generation = record_generation
        token_data = Dataset.from_list([{'input_ids': ids} for ids, _ in prompts
                                       for _ in range(config['num_generations'])])
        policy = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32)
        critic = AutoModelForSequenceClassification.from_pretrained(args.model, num_labels=1, dtype=torch.float32)
        reference = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32)
        trainer = make_trainer(config, tokenizer, policy, critic, reference, token_data, bridge, args.output, [Records()])
        try:
            trainer.train()
        except ResourceCapReached:
            stop_reason = 'resource_cap'
        trainer.save_model(str(args.output / 'final'))
        tokenizer.save_pretrained(str(args.output / 'final'))
        critic.save_pretrained(str(args.output / 'critic'))
        run.update(status='complete', algorithm='ppo', rollout_batches=trainer.state.global_step,
                   stop_reason=stop_reason, requested_rollout_batches=config['max_steps'],
                   unique_presented_tasks=len(presented), critic_initialization='parent backbone, new scalar head')
    except BaseException as error:
        run.update(status='failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        wall = time.monotonic() - started
        run.update(wall_seconds=wall, gpu_seconds=wall * gpus, counts=counts)
        (args.output / 'run.json').write_text(json.dumps(run, indent=2) + '\n')
        rollouts.close()
        generations.close()
        steps.close()
        executor.shutdown()
        for repl in list(ctx.pool_repls):
            repl.close()
