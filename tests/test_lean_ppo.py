"""CPU integration of the pinned TRL PPO bridge; no downloaded model or Lean process."""
import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'train'))


def prescribed_generation(model, queries, pad_token_id, generation_config):
    """Planted valid trajectories with actual policy logits; no sampling-quality claim."""
    import torch
    from trl.trainer.utils import forward
    response = torch.tensor([[1, pad_token_id, pad_token_id], [5, 1, pad_token_id], [5, 6, 5]],
                            device=queries.device)
    offset = getattr(prescribed_generation, 'calls', 0)
    prescribed_generation.calls = offset + queries.shape[0]
    response = response[(torch.arange(queries.shape[0], device=queries.device) + offset) % 3]
    sequences = torch.cat([queries, response], 1)
    logits = forward(model, sequences, pad_token_id).logits[:, queries.shape[1] - 1:-1]
    return sequences, logits / generation_config.temperature


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Torch checks run on the CPU fleet')
class LeanPPO(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('lean_ppo'), 'PPO adapter is missing')
        import torch
        torch.set_num_threads(1)
        torch.manual_seed(7)
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import Whitespace
        from transformers import PreTrainedTokenizerFast
        backend = Tokenizer(WordLevel({'<pad>': 0, '<eos>': 1, '<unk>': 2, 'goal': 3,
                                      'other': 4, 'good': 5, 'bad': 6}, unk_token='<unk>'))
        backend.pre_tokenizer = Whitespace()
        self.tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, pad_token='<pad>', eos_token='<eos>',
                                                 unk_token='<unk>', padding_side='left')

    def test_generation_boundary_records_raw_tokens_before_pad_abort(self):
        import torch
        import lean_ppo
        from trl.trainer.utils import first_true_indices, truncate_response
        self.assertTrue(hasattr(lean_ppo, 'checked_generate'), 'Raw generation check is missing')
        query = torch.tensor([[3], [3], [3], [3]])
        responses = torch.tensor([[1, 0, 0], [0, 5, 1], [5, 0, 1], [5, 6, 5]])
        seen = []
        def generate(*_):
            return torch.cat([query, responses], 1), torch.zeros(4, 3, 7)
        with self.assertRaisesRegex(RuntimeError, 'PAD before EOS'):
            lean_ppo.checked_generate(generate, self.tokenizer, lambda q, r: seen.extend(r),
                                      None, query, 0, None)
        self.assertEqual(sum(row['generated_tokens'] for row in seen), 12)
        self.assertEqual([row['action_ids'] for row in seen], [[1], [0, 5, 1], [5, 0, 1], [5, 6, 5]])
        self.assertEqual([row['pad_before_eos'] for row in seen], [False, True, True, False])
        # The pinned PPO mask retains EOS, including an EOS-first response.
        valid = responses[[0, 3]]
        processed = truncate_response(1, 0, valid)
        lengths = first_true_indices(processed == 0) - 1
        for row, length, expected in zip(valid.tolist(), lengths.tolist(), ([1], [5, 6, 5])):
            self.assertEqual(row[:length + 1], expected)
        result = (torch.cat([query[:2], valid], 1), torch.zeros(2, 3, 7))
        self.assertIs(lean_ppo.checked_generate(lambda *_: result, self.tokenizer, None,
                                               None, query[:2], 0, None), result)

    def test_bridge_preserves_verdicts_and_rejects_unregistered_prompt(self):
        import torch
        from trl.trainer.utils import get_reward
        from lean_ppo import LeanRewardModel
        seen = []

        def reward(task_ids, texts, token_ids):
            seen.append((task_ids, texts, token_ids))
            return [float(text == 'good') for text in texts]

        model = LeanRewardModel(self.tokenizer, [([3], 'a'), ([4], 'b')], reward)
        model.context_length = 2
        _, scores, _ = get_reward(model, torch.tensor([[0, 3, 5, 1], [0, 4, 6, 1]]), 0, 2)
        self.assertEqual(scores.tolist(), [1.0, 0.0])
        self.assertEqual(seen[0][0], ['a', 'b'])
        self.assertEqual(seen[0][2], [[5, 1], [6, 1]])
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            get_reward(model, torch.tensor([[0, 2, 5, 1]]), 0, 2)

    def test_pinned_ppo_updates_policy_and_critic_on_prescribed_valid_trajectories(self):
        import torch
        from datasets import Dataset
        from transformers import Qwen2Config, Qwen2ForCausalLM, Qwen2ForSequenceClassification
        from trl.trainer import ppo_trainer
        from lean_ppo import LeanRewardModel, make_trainer, ppo_masked_mean
        config = Qwen2Config(vocab_size=7, max_position_embeddings=16, hidden_size=16,
                             intermediate_size=32, num_hidden_layers=1, num_attention_heads=2,
                             num_key_value_heads=1, pad_token_id=0, eos_token_id=1, num_labels=1)
        policy, critic = Qwen2ForCausalLM(config), Qwen2ForSequenceClassification(config)
        before_policy = {k: v.detach().clone() for k, v in policy.named_parameters()}
        before_critic = {k: v.detach().clone() for k, v in critic.named_parameters()}
        seen = []

        def score(tids, texts, ids):
            seen.extend(tids)
            return [float(tid == 'a' and bool(text.strip())) for tid, text in zip(tids, texts)]

        reward = LeanRewardModel(self.tokenizer, [([3], 'a'), ([4, 4, 4], 'b')], score)
        settings = {'max_steps': 2, 'num_generations': 4, 'prompts_per_step': 1, 'max_completion_tokens': 3,
                    'temperature': 1.0, 'top_p': 1.0, 'top_k': 0, 'repetition_penalty': 1.0,
                    'use_vllm': False, 'shuffle_rewards': False, 'num_ppo_epochs': 1, 'seed': 7,
                    'learning_rate': 0.001, 'bf16': False, 'gradient_checkpointing': True, 'beta': 0.0,
                    'ppo_micro_batch_size': 1}
        data = Dataset.from_list([{'input_ids': ids} for ids in [[3]] * 4 + [[4, 4, 4]] * 4])
        means, generations = [], []
        original_mean, original_generation = ppo_masked_mean, ppo_trainer.batch_generation
        pinned_mean = ppo_trainer.masked_mean

        def audit_mean(values, mask, *args, **kwargs):
            result = original_mean(values, mask, *args, **kwargs)
            frame = sys._getframe(1)
            means.append(dict(line=frame.f_lineno, update=frame.f_locals['update'],
                              tokens=frame.f_locals['mb_responses'].tolist(), mask=mask.tolist(),
                              numerator=(values.detach() * mask).sum().item(), denominator=mask.sum().item(),
                              result=result.item() if torch.isfinite(result) else str(result.item())))
            return result

        def audit_generation(*args, **kwargs):
            result = original_generation(*args, **kwargs)
            generations.append(result[0][:, args[1].shape[1]:].tolist())
            return result

        with tempfile.TemporaryDirectory() as output:
            with patch('lean_ppo.ppo_masked_mean', audit_mean), \
                    patch('trl.trainer.utils.generate', prescribed_generation), \
                    patch.object(ppo_trainer, 'batch_generation', audit_generation):
                trainer = make_trainer(settings, self.tokenizer, policy, critic, copy.deepcopy(policy), data, reward, output, [])
                trainer.train()
            self.assertIs(ppo_trainer.masked_mean, pinned_mean)
            trainer.save_model(output)
            critic.save_pretrained(Path(output) / 'critic')
            torch.save(dict(policy=before_policy, critic=before_critic), Path(output) / 'before_parameters.pt')
            parameters = {}
            for name, model, before in [('policy', policy, before_policy), ('critic', critic, before_critic)]:
                values = dict(model.named_parameters())
                delta = sum((v.detach().double() - before[k].double()).square().sum().item() for k, v in values.items())
                parameters[name] = dict(nonfinite=[k for k, v in values.items() if not torch.isfinite(v).all()],
                                        parameter_count=sum(v.numel() for v in values.values()),
                                        delta_squared=delta, delta_finite=math.isfinite(delta))
            diagnosis = dict(generations=generations, masked_means=means,
                             metrics=trainer.state.log_history, parameters=parameters)
            (Path(output) / 'numerical_diagnosis.json').write_text(json.dumps(diagnosis, indent=2) + '\n')
            if os.environ.get('PPO_DIAGNOSTICS_DIR'):
                shutil.copytree(output, os.environ['PPO_DIAGNOSTICS_DIR'])
            print('NUMERICAL_DIAGNOSIS ' + json.dumps(diagnosis), flush=True)
            self.assertTrue((Path(output) / 'model.safetensors').is_file())
        for name, result in parameters.items():
            self.assertEqual(result['nonfinite'], [], name)
            self.assertTrue(result['delta_finite'], name)
            self.assertGreater(result['delta_squared'], 0.0, name)
        sampled = [row for batch in generations for row in batch]
        self.assertFalse(any(row[0] == self.tokenizer.pad_token_id for row in sampled))
        self.assertTrue(any(row[0] == self.tokenizer.eos_token_id for row in sampled))
        empty_means = [row for row in means if row['denominator'] == 0]
        self.assertFalse(empty_means)
        self.assertTrue(all(math.isfinite(row['loss/policy_avg']) for row in trainer.state.log_history))
        self.assertTrue(policy.is_gradient_checkpointing)
        self.assertTrue(critic.is_gradient_checkpointing)
        self.assertEqual(trainer.state.global_step, 2)
        self.assertEqual(seen, ['a'] * 4 + ['b'] * 4)

    def test_empty_mask_zero_and_scoped_restoration(self):
        import torch
        from datasets import Dataset
        from transformers import GPT2Config, GPT2LMHeadModel, GPT2ForSequenceClassification, TrainerCallback
        from trl.core import masked_mean, masked_whiten
        from trl.trainer import ppo_trainer
        from trl.trainer.utils import get_reward
        from lean_ppo import LeanRewardModel, make_trainer, ppo_masked_mean
        for axis in (None, 0, 1):
            values = torch.tensor([[2.0, 4.0], [6.0, 8.0]], requires_grad=True)
            mask = torch.tensor([[True, False], [True, True]])
            expected, actual = masked_mean(values, mask, axis), ppo_masked_mean(values, mask, axis)
            self.assertTrue(torch.equal(expected, actual))
            self.assertTrue(torch.equal(torch.autograd.grad(expected.sum(), values, retain_graph=True)[0],
                                        torch.autograd.grad(actual.sum(), values)[0]))
        values = torch.tensor([2.0, 4.0], requires_grad=True)
        empty = ppo_masked_mean(values, torch.zeros(2, dtype=torch.bool))
        self.assertEqual(empty.item(), 0.0)
        self.assertEqual(torch.autograd.grad(empty, values)[0].tolist(), [0.0, 0.0])
        with self.assertRaisesRegex(ValueError, 'sum of the mask is zero'):
            masked_whiten(torch.zeros(4, 3), torch.zeros(4, 3, dtype=torch.bool))
        seen = []
        def score(tids, texts, ids):
            seen.extend(ids)
            return [float(bool(text.strip())) for text in texts]
        reward = LeanRewardModel(self.tokenizer, [([3], 'a')], score)
        reward.context_length = 1
        _, scores, _ = get_reward(reward, torch.tensor([[3, 0, 5, 1], [3, 1, 0, 0]]), 0, 1)
        self.assertEqual(scores.tolist(), [0.0, 0.0])
        self.assertEqual(seen, [[], [1]])
        class StopBeforeGeneration(TrainerCallback):
            def on_train_begin(self, *args, **kwargs):
                raise RuntimeError('planted pre-generation stop')
        settings = dict(max_steps=1, num_generations=4, prompts_per_step=1, max_completion_tokens=3,
                        temperature=1.0, top_p=1.0, top_k=0, repetition_penalty=1.0, use_vllm=False,
                        shuffle_rewards=False, num_ppo_epochs=1, seed=7, learning_rate=0.001,
                        bf16=False, gradient_checkpointing=True, beta=0.0, ppo_micro_batch_size=1)
        cfg = GPT2Config(vocab_size=7, n_positions=16, n_embd=16, n_layer=1, n_head=1,
                         pad_token_id=0, eos_token_id=1, num_labels=1)
        policy, critic = GPT2LMHeadModel(cfg), GPT2ForSequenceClassification(cfg)
        pinned_mean = ppo_trainer.masked_mean
        with tempfile.TemporaryDirectory() as output:
            trainer = make_trainer(settings, self.tokenizer, policy, critic, copy.deepcopy(policy),
                Dataset.from_list([{'input_ids': [3]}] * 4), reward, output, [StopBeforeGeneration()])
            with self.assertRaisesRegex(RuntimeError, 'planted pre-generation stop'):
                trainer.train()
        self.assertIs(ppo_trainer.masked_mean, pinned_mean)

    def test_runner_rejects_nonfinite_loss_without_export(self):
        from datasets import Dataset
        from lean_ppo import run_ppo
        for value in (float('nan'), float('inf')):
            with self.subTest(loss=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                settings = dict(seed=7, num_generations=4, verifier_workers=1)
                def trainer(*args):
                    callback = args[-1][0]
                    return Namespace(train=lambda: callback.on_log(None, Namespace(global_step=0), None,
                        logs={'loss/policy_avg': value}))
                with patch('transformers.AutoModelForCausalLM.from_pretrained', return_value=object()), \
                        patch('transformers.AutoModelForSequenceClassification.from_pretrained', return_value=object()), \
                        patch('lean_ppo.make_trainer', trainer):
                    with self.assertRaisesRegex(FloatingPointError, 'Non-finite PPO loss'):
                        run_ppo(Namespace(model=output, output=output), settings, Namespace(pool_repls=set()), {},
                                Dataset.from_list([{'prompt': 'goal', 'task_id': 'a'}]), self.tokenizer, {})
                self.assertEqual(json.loads((output / 'run.json').read_text())['status'], 'failed')
                self.assertFalse((output / 'final').exists())
                self.assertFalse(math.isfinite(json.loads((output / 'steps.jsonl').read_text())['loss/policy_avg']))

    def test_runner_preserves_pad_failure_before_scoring_or_checkpoint_export(self):
        import torch
        from datasets import Dataset
        from lean_ppo import checked_generate, run_ppo
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            settings = dict(seed=7, num_generations=4, verifier_workers=1)
            def trainer(*args):
                bridge = args[6]
                result = Namespace(state=Namespace(global_step=0))
                def train():
                    query = torch.tensor([[3]])
                    generated = (torch.tensor([[3, 0, 5, 1]]), torch.zeros(1, 3, 7))
                    checked_generate(lambda *_: generated, self.tokenizer, bridge.record_generation,
                                     None, query, 0, None)
                result.train = train
                return result
            with patch('transformers.AutoModelForCausalLM.from_pretrained', return_value=object()), \
                    patch('transformers.AutoModelForSequenceClassification.from_pretrained', return_value=object()), \
                    patch('lean_ppo.make_trainer', trainer), patch('run_lean_grpo.score') as scorer:
                with self.assertRaisesRegex(RuntimeError, 'PAD before EOS'):
                    run_ppo(Namespace(model=output, output=output), settings, Namespace(pool_repls=set()), {},
                            Dataset.from_list([{'prompt': 'goal', 'task_id': 'a'}]), self.tokenizer, {})
            scorer.assert_not_called()
            run = json.loads((output / 'run.json').read_text())
            self.assertEqual(run['status'], 'failed')
            self.assertEqual(run['counts']['generated_tokens'], 3)
            self.assertEqual(run['counts']['samples'], 0)
            row = json.loads((output / 'generations.jsonl').read_text())
            self.assertEqual(row['raw_response_ids'], [0, 5, 1])
            self.assertEqual(row['status'], 'generation_contract_violation')
            self.assertFalse((output / 'final').exists())

    def test_response_stops_at_first_pad_and_preserves_eos(self):
        import torch
        from trl.trainer.utils import get_reward
        from lean_ppo import LeanRewardModel
        seen = []

        def score(tids, texts, ids):
            seen.append((tids, ids))
            return [float(text == 'good') for text in texts]

        reward = LeanRewardModel(self.tokenizer, [([3], 'a')], score)
        reward.context_length = 2
        _, scores, _ = get_reward(reward, torch.tensor([[0, 3, 5, 0, 6, 1],
                                                        [0, 3, 5, 0, 4, 1],
                                                        [0, 3, 5, 1, 0, 0]]), 0, 2)
        self.assertEqual(seen, [(['a', 'a', 'a'], [[5], [5], [5, 1]])])
        self.assertEqual(scores.tolist(), [1.0, 1.0, 1.0])

    def test_loader_preserves_current_width_and_prompt_identity(self):
        import torch
        from datasets import Dataset
        from transformers import GPT2Config, GPT2LMHeadModel, GPT2ForSequenceClassification
        from trl.trainer.utils import get_reward
        from lean_ppo import LeanRewardModel, make_trainer
        settings = {'max_steps': 2, 'num_generations': 2, 'prompts_per_step': 2, 'max_completion_tokens': 3,
                    'temperature': 0.6, 'top_p': 1.0, 'top_k': 0, 'repetition_penalty': 1.0,
                    'use_vllm': False, 'shuffle_rewards': False, 'num_ppo_epochs': 1, 'seed': 7,
                    'learning_rate': 0.001, 'bf16': False, 'gradient_checkpointing': False, 'beta': 0.02}
        orders = {'increasing': [[3]] * 4 + [[4, 4, 4]] * 4,
                  'decreasing': [[4, 4, 4]] * 4 + [[3]] * 4,
                  'mixed': [[3], [4, 4, 4], [3], [4, 4, 4]] + [[3]] * 4}
        for name, rows in orders.items():
            with self.subTest(order=name), tempfile.TemporaryDirectory() as output:
                seen = []

                def score(tids, texts, ids):
                    seen.extend(tids)
                    self.assertEqual(ids, [[5, 1]] * len(tids))
                    return [1.0] * len(tids)

                reward = LeanRewardModel(self.tokenizer, [([3], 'a'), ([4, 4, 4], 'b')], score)
                config = GPT2Config(vocab_size=7, n_positions=16, n_embd=16, n_layer=1, n_head=1,
                                    pad_token_id=0, eos_token_id=1, num_labels=1)
                policy, critic = GPT2LMHeadModel(config), GPT2ForSequenceClassification(config)
                data = Dataset.from_list([{'input_ids': ids} for ids in rows])
                trainer = make_trainer(settings, self.tokenizer, policy, critic, copy.deepcopy(policy),
                                       data, reward, output, [])
                for batch in trainer.dataloader:
                    width = batch['input_ids'].shape[1]
                    self.assertEqual(reward.context_length, width)
                    response = torch.tensor([[5, 1]] * batch['input_ids'].shape[0])
                    _, scores, _ = get_reward(reward, torch.cat([batch['input_ids'], response], dim=1), 0, width)
                    self.assertEqual(scores.tolist(), [1.0] * len(response))
                self.assertEqual(seen, ['a' if ids == [3] else 'b' for ids in rows])

    def test_runner_stops_at_cap_and_saves_policy_critic_and_records(self):
        from datasets import Dataset
        from transformers import GPT2Config, GPT2LMHeadModel
        from lean_ppo import run_ppo

        def planted_score(ctx, by_id, task_ids, completions, executor):
            verdicts = [dict(status='accepted' if tid == 'a' else 'rejected', lean_calls=1,
                             lean_seconds=0.0, lean_cpu_seconds=0.0) for tid in task_ids]
            return completions, {}, verdicts

        settings = {'max_steps': 2, 'num_generations': 2, 'prompts_per_step': 2, 'max_completion_tokens': 3,
                    'temperature': 0.6, 'top_p': 1.0, 'top_k': 0, 'repetition_penalty': 1.0,
                    'use_vllm': False, 'shuffle_rewards': False, 'num_ppo_epochs': 1, 'seed': 7,
                    'learning_rate': 0.001, 'bf16': False, 'gradient_checkpointing': False, 'beta': 0.02,
                    'verifier_workers': 1, 'max_wall_seconds': 90, 'max_generated_tokens': 100,
                    'max_lean_calls': 1}
        data = Dataset.from_list([{'prompt': 'goal', 'task_id': 'a'}, {'prompt': 'other', 'task_id': 'b'}] * 2)
        with tempfile.TemporaryDirectory() as directory:
            parent, output = Path(directory) / 'parent', Path(directory) / 'out'
            model = GPT2LMHeadModel(GPT2Config(vocab_size=7, n_positions=16, n_embd=16, n_layer=1,
                                              n_head=1, pad_token_id=0, eos_token_id=1))
            model.save_pretrained(parent)
            output.mkdir()
            run = {'config': settings, 'status': 'running'}
            with patch('run_lean_grpo.score', side_effect=planted_score), \
                    patch('trl.trainer.utils.generate', prescribed_generation):
                run_ppo(Namespace(model=parent, output=output), settings, Namespace(pool_repls=set()),
                        {}, data, self.tokenizer, run)
            record = json.loads((output / 'run.json').read_text())
            self.assertEqual(record['stop_reason'], 'resource_cap')
            self.assertEqual(record['rollout_batches'], 1)
            self.assertEqual(record['counts']['samples'], 4)
            self.assertTrue((output / 'final/model.safetensors').is_file())
            self.assertTrue((output / 'final/tokenizer.json').is_file())
            self.assertTrue((output / 'critic/model.safetensors').is_file())
            rows = [json.loads(line) for line in (output / 'rollouts.jsonl').read_text().splitlines()]
            self.assertEqual([r['reward'] for r in rows], [1.0, 1.0, 0.0, 0.0])
            generated = [json.loads(line) for line in (output / 'generations.jsonl').read_text().splitlines()]
            self.assertEqual(record['counts']['generated_tokens'], sum(r['generated_tokens'] for r in generated))
            self.assertEqual(record['counts']['generated_tokens'], 12)
            self.assertLess(record['counts']['retained_tokens'], record['counts']['generated_tokens'])


if __name__ == '__main__':
    unittest.main()
