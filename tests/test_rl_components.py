"""Reusable admission and objective components, without Lean or model execution."""
from contextlib import nullcontext
import importlib
import hashlib
from itertools import islice
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


TRAIN = Path(__file__).resolve().parents[1] / 'scripts/train'
sys.path.insert(0, str(TRAIN))


class ComponentsTest(unittest.TestCase):
    def test_proof_rewards_refuse_censored_batches_and_keep_opaque_rejections(self):
        module = importlib.import_module('rl_components.objectives')
        self.assertTrue(hasattr(module, 'proof_rewards'), 'Censored PPO reward guard is missing')
        self.assertEqual(module.proof_rewards([dict(status='accepted'), dict(status='changed'),
            dict(status='rejected', error='internal exception #4')]), [1.0, 0.0, 0.0])
        for outcome in (dict(status='crash'), dict(status='replay_crash'),
                        dict(status='rejected', censored=True), dict(status='accepted', censored=True)):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(RuntimeError, 'no PPO update'):
                module.proof_rewards([dict(status='accepted'), outcome])

    def test_sft_admission_counts_completion_terminator_before_loading_weights(self):
        class Tokenizer:
            def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False):
                text = messages[0]['content'] + '<assistant>'
                if len(messages) > 1:
                    text += messages[1]['content'] + '<eos>'
                return list(text)

        record = dict(prompt=[dict(role='user', content='p')],
                      completion=[dict(role='assistant', content='target')])
        dataset = self.components().SFTDataset([record])
        self.assertEqual(dataset.validate_token_budget(Tokenizer(), 100, 11), [23])
        with self.assertRaisesRegex(ValueError, 'action token'):
            dataset.validate_token_budget(Tokenizer(), 100, 10)
        with self.assertRaisesRegex(ValueError, 'max_length'):
            dataset.validate_token_budget(Tokenizer(), 23, 11)

    def components(self):
        self.assertTrue((TRAIN / 'rl_components/__init__.py').exists(), 'Reusable RL components are missing')
        return importlib.import_module('rl_components')

    def pool(self, module):
        rows = [dict(name=name, split=split, formal_statement=f'theorem {name} : True')
                for name, split in [('a', 'train'), ('b', 'validation'), ('c', 'test')]]
        return module.PoolTaskAdmission(rows, header=lambda row: row['formal_statement'],
            unit_factory=lambda row: dict(name=row['name'], task_id=row['task_id']), forbidden=re.compile(r'\bsorry\b'))

    def task(self, name='a', kind='whole', remaining=None):
        return dict(task_id=name + ':' + kind, name=name, header=f'theorem {name} : True',
                    given='', state='⊢ True', kind=kind, lines_remaining=remaining)

    def test_pool_admission_keeps_split_metadata_and_checks_every_supplied_task(self):
        module = self.components()
        pool = self.pool(module)
        self.assertEqual([row['name'] for row in pool.train_statements()], ['a'])
        self.assertEqual(pool.units([self.task('b')], split='validation')[0]['state'], '⊢ True')
        self.assertEqual(pool.rows[1]['split'], 'validation')
        for tasks, split in (([self.task('b')], 'train'), ([self.task('c')], 'validation'),
                             ([dict(self.task(), header='theorem a : False')], 'train'),
                             ([dict(self.task(), given='  sorry')], 'train'),
                             ([self.task(), self.task()], 'train')):
            with self.subTest(tasks=tasks, split=split), self.assertRaises(RuntimeError):
                pool.units(tasks, split=split)

    def test_curriculum_composes_admission_before_prefix_filtering(self):
        module = self.components()
        pool = self.pool(module)
        validate = lambda tasks, rows, **kwargs: pool.units(tasks, **kwargs)
        curriculum = module.TaskCurriculum(pool.rows, {'b', 'c'}, 2, validate_units=validate)
        easy, hard = self.task(kind='prefix_top', remaining=1), self.task(kind='prefix_have', remaining=9)
        self.assertEqual(curriculum.training([easy, hard]), [easy])
        self.assertEqual(curriculum.assessment([self.task('b')], 'validation'), [self.task('b')])
        with self.assertRaises(RuntimeError):
            curriculum.training([easy, dict(hard, name='b')])
        with self.assertRaisesRegex(ValueError, 'excluded'):
            module.TaskCurriculum(pool.rows, set(), 2, validate_units=validate).assessment([self.task('b')], 'validation')

    def test_schedule_is_reusable_and_preserves_seeded_order_and_explicit_repeats(self):
        module = self.components()
        tasks = [self.task(), self.task(kind='prefix_top', remaining=1),
                 dict(self.task(kind='prefix_top', remaining=1), task_id='other')]
        schedule = module.TaskSchedule(tasks)
        first = list(islice(schedule.balanced(7), 12))
        self.assertEqual(first, list(islice(schedule.balanced(7), 12)))
        self.assertEqual([row['kind'] for row in first], ['prefix_top', 'whole'] * 6)
        ids = [tasks[1]['task_id'], tasks[1]['task_id'], tasks[0]['task_id']]
        self.assertEqual([row['task_id'] for row in schedule.frozen(ids, 3)], ids)
        with self.assertRaisesRegex(ValueError, 'unknown'):
            schedule.frozen(['missing'], 1)

    def test_leave_one_out_objective_keeps_discounted_rewards_and_null_groups(self):
        module = self.components()
        objective = module.LeaveOneOutObjective(0.5)
        self.assertEqual(objective.advantages([[0, 1], [0]]), [[0.5, 0.5], [-0.5]])
        self.assertEqual(objective.advantages([[0, 1], [0, 1]]), [[0, 0], [0, 0]])
        with self.assertRaises(ValueError):
            objective.advantages([[1]])

    def updater(self, module, values, finite=True):
        events = []

        class Scalar:
            def __init__(self, value):
                self.value = value

            def __rmul__(self, value):
                return Scalar(value * self.value)

            def __truediv__(self, value):
                return Scalar(self.value / value)

            def backward(self):
                events.append(('backward', self.value))

            def detach(self):
                return self.value

        model = SimpleNamespace(train=lambda: events.append('train'), eval=lambda: events.append('eval'),
                                parameters=lambda: [])
        optimizer = SimpleNamespace(zero_grad=lambda **kwargs: events.append(('zero_grad', kwargs)),
                                    step=lambda: events.append('step'))
        def logprob(model, prompt, action, torch):
            events.append(('logprob', prompt, action))
            return Scalar(-2)
        def clip(parameters, maximum, **kwargs):
            events.append(('clip', maximum, kwargs))
            return 0.75
        torch = SimpleNamespace(isfinite=lambda loss: finite,
                                nn=SimpleNamespace(utils=SimpleNamespace(clip_grad_norm_=clip)))
        updater = module.PolicyGradientUpdater(model, optimizer, torch, nullcontext,
                                               advantages=lambda rewards: values, action_logprob=logprob)
        return updater, events

    def test_updater_streams_actions_then_clips_and_updates_once(self):
        module = self.components()
        updater, events = self.updater(module, [[1, 0], [-1]])
        trajectories = [[dict(reward=1, prompt_ids=[1], action_ids=[2]),
                         dict(reward=0, prompt_ids=[3], action_ids=[4])],
                        [dict(reward=0, prompt_ids=[5], action_ids=[6])]]
        result = updater.update(trajectories, checkpointing=True)
        self.assertEqual(result, dict(loss=0.0, gradient_norm=0.75, optimizer_updated=True, advantages=[[1, 0], [-1]]))
        self.assertEqual(events, [('zero_grad', dict(set_to_none=True)), 'train',
            ('logprob', [1], [2]), ('backward', 1.0), ('logprob', [5], [6]), ('backward', -1.0),
            'eval', ('clip', 1.0, dict(error_if_nonfinite=True)), 'step'])

    def test_null_group_skips_update_and_nonfinite_loss_restores_eval_mode(self):
        module = self.components()
        trajectories = [[dict(reward=0, prompt_ids=[1], action_ids=[2])]] * 2
        updater, events = self.updater(module, [[0], [0]])
        self.assertFalse(updater.update(trajectories)['optimizer_updated'])
        self.assertEqual(events, [('zero_grad', dict(set_to_none=True)), 'eval'])
        updater, events = self.updater(module, [[1], [-1]], finite=False)
        with self.assertRaisesRegex(RuntimeError, 'Non-finite'):
            updater.update(trajectories, checkpointing=True)
        self.assertEqual(events[-1], 'eval')
        self.assertNotIn('step', events)

    def test_legacy_adapters_keep_live_validator_and_objective_callbacks(self):
        self.components()
        import run_tactic_rl
        with patch.object(run_tactic_rl, 'task_units') as validate:
            self.assertEqual(run_tactic_rl.training_tasks([self.task()], [], set(), 2), [self.task()])
            validate.assert_called_once_with([self.task()], [])
        with patch.object(run_tactic_rl, 'advantages', return_value=[[0], [0]]) as objective:
            events = []
            model = SimpleNamespace(eval=lambda: events.append('eval'))
            optimizer = SimpleNamespace(zero_grad=lambda **kwargs: None)
            result = run_tactic_rl.update(model, optimizer, [[dict(reward=1)], [dict(reward=0)]],
                                          None, nullcontext, 0.5)
            objective.assert_called_once_with([[1], [0]], 0.5)
            self.assertFalse(result['optimizer_updated'])
            self.assertEqual(events, ['eval'])

    def test_sft_dataset_preserves_message_conversion_and_existing_limit_semantics(self):
        module = self.components()
        messages = [dict(role='user', content='goal'), dict(role='assistant', content='proof')]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'sft.jsonl'
            path.write_text(''.join(json.dumps(dict(messages=messages, format='proof', ignored=True)) + '\n'
                                    for _ in range(2)))
            dataset = module.SFTDataset.from_jsonl(path, limit=1)
            self.assertEqual(dataset.records, [dict(prompt=messages[:1], completion=messages[1:], format='proof')])
            self.assertEqual(len(module.SFTDataset.from_jsonl(path, limit=0).records), 2)
            self.assertEqual(len(module.SFTDataset.from_jsonl(path, limit=-1).records), 1)

    def test_sft_worker_receipt_binds_read_bytes_selected_rows_and_model_metadata(self):
        module = importlib.import_module('rl_components.sft')
        with TemporaryDirectory() as directory:
            root = Path(directory)
            messages = [dict(role='user', content='goal'), dict(role='assistant', content='proof')]
            row = json.dumps(dict(messages=messages, format='proof')) + '\n'
            path = root / 'train.jsonl'
            path.write_text(row * 2)
            train = module.SFTDataset.from_jsonl(path, limit=1)
            self.assertEqual(train.source_receipt['file_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(train.source_receipt['selected_rows'], 1)
            path.write_text(row * 3)
            changed = module.SFTDataset.from_jsonl(path, limit=1)
            self.assertNotEqual(train.source_receipt['file_sha256'], changed.source_receipt['file_sha256'])
            self.assertEqual(train.source_receipt['selected_records_sha256'], changed.source_receipt['selected_records_sha256'])
            config = root / 'sft_config.json'
            config.write_text('{"max_length": 2048}\n')
            model = root / 'model'
            model.mkdir()
            (model / 'config.json').write_text('{"model_type": "qwen2"}')
            (model / 'tokenizer.json').write_text('{"vocab": {}}')
            (model / 'model.safetensors').write_bytes(b'not loaded or hashed')
            receipt = module.sft_input_receipt(config, model, train, changed)
            self.assertEqual(receipt['train'], train.source_receipt)
            self.assertEqual(receipt['config_sha256'], hashlib.sha256(config.read_bytes()).hexdigest())
            self.assertEqual(receipt['model_metadata_sha256']['tokenizer.json'],
                             hashlib.sha256((model / 'tokenizer.json').read_bytes()).hexdigest())
            self.assertFalse(receipt['weight_content_sha256_recorded'])
            self.assertEqual(receipt['weight_files'], [{'name': 'model.safetensors', 'bytes': len(b'not loaded or hashed')}])

    def test_proof_leaf_and_component_receipt_cover_the_extracted_source(self):
        module = self.components()
        proofs = importlib.import_module('rl_components.proofs')
        self.assertEqual(proofs.tactic_block('  have h : True := by\n    trivial\n  exact h'),
                         'first |\n  have h : True := by\n    trivial\n  exact h')
        receipt = module.component_fingerprints()
        files = sorted((TRAIN / 'rl_components').glob('*.py'))
        self.assertEqual(set(receipt), {'rl_components/' + path.name for path in files})
        self.assertTrue(all(receipt['rl_components/' + path.name] == hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in files))


if __name__ == '__main__':
    unittest.main()
