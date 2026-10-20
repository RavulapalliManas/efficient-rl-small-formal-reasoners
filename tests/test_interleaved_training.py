"""Curriculum, checkpoint handoff, and failed-stage contracts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/train'))


class InterleavedChecks(unittest.TestCase):
    def test_recovery_imports_completed_weights_and_rejects_changed_protocol(self):
        import run_interleaved_training as training
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            stage = {'id': 'common_sft', 'kind': 'sft', 'max_wall_seconds': 5,
                     'config': 'config.json', 'overrides': {'max_steps': 2}}
            checkpoint = source / 'common_sft/training/final'
            checkpoint.mkdir(parents=True)
            (checkpoint / 'model.safetensors').write_bytes(b'weights')
            (checkpoint / 'tokenizer_config.json').write_text('{}')
            (source / 'plan.json').write_text(json.dumps({'stages': [stage]}))
            (source / 'config.json').write_text(json.dumps({'max_steps': 1}))
            (checkpoint.parent / 'run.json').write_text('{}')
            record = {'id': 'common_sft', 'kind': 'sft', 'status': 'complete',
                      'checkpoint': '/old/run/common_sft/training/final',
                      'resolved_config': {'max_steps': 2},
                      'weights_sha256': hashlib.sha256(b'weights').hexdigest()}
            (source / 'common_sft/stage.json').write_text(json.dumps(record))
            imported = training.reuse_completed_stage(source, stage, source)
            self.assertEqual(imported['checkpoint'], str(checkpoint.resolve()))
            self.assertTrue(imported['reused'])
            self.assertEqual(json.loads((source / 'common_sft/stage.json').read_text()), record)
            output = source / 'retry'
            command = [sys.executable, training.__file__, '--plan', str(source / 'plan.json'),
                       '--root', str(source), '--model', str(source / 'unused'),
                       '--output', str(output), '--resume-from', str(source)]
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            retry = json.loads((output / 'common_sft/stage.json').read_text())
            self.assertEqual(retry['checkpoint'], str(checkpoint.resolve()))
            self.assertEqual(json.loads((output / 'run.json').read_text())['status'], 'complete')
            with self.assertRaisesRegex(ValueError, 'protocol'):
                training.reuse_completed_stage(source, dict(stage, max_wall_seconds=6), source)
            (source / 'config.json').write_text(json.dumps({'max_steps': 1, 'seed': 99}))
            with self.assertRaisesRegex(ValueError, 'config'):
                training.reuse_completed_stage(source, stage, source)
            (source / 'config.json').write_text(json.dumps({'max_steps': 1}))
            (checkpoint / 'model.safetensors').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'weights'):
                training.reuse_completed_stage(source, stage, source)

    def test_recovery_does_not_promote_interrupted_stage(self):
        import run_interleaved_training as training
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            stage = {'id': 'rlp_2', 'kind': 'rlp', 'parent': 'rlp_sft_1'}
            (source / 'plan.json').write_text(json.dumps({'stages': [stage]}))
            (source / 'rlp_2').mkdir()
            record = {'id': 'rlp_2', 'kind': 'rlp', 'status': 'starting'}
            (source / 'rlp_2/stage.json').write_text(json.dumps(record))
            self.assertIsNone(training.reuse_completed_stage(source, stage, source))

    def test_prompt_order_covers_problems_before_repeating_cuts(self):
        import run_interleaved_training as training
        rows = [{'problem_name': name, 'task_id': f'{name}{i}'}
                for name, n in [('a', 10), ('b', 1), ('c', 2)] for i in range(n)]
        ordered = training.balanced_prompt_order(rows, 20261010)
        self.assertEqual({r['problem_name'] for r in ordered[:3]}, {'a', 'b', 'c'})
        self.assertEqual({r['task_id'] for r in ordered}, {r['task_id'] for r in rows})
        self.assertEqual(ordered, training.balanced_prompt_order(rows, 20261010))

    def test_prefix_curriculum_keeps_easier_tasks_and_excludes_whole(self):
        import run_interleaved_training as training
        tasks = [{'task_id': str(n), 'kind': 'prefix_top', 'lines_remaining': n}
                 for n in (1, 2, 5)] + [{'task_id': 'whole', 'kind': 'whole'}]
        self.assertEqual([r['task_id'] for r in training.prefix_curriculum(tasks, 2, False)], ['1', '2'])
        self.assertEqual(len(training.prefix_curriculum(tasks, 5, True)), 4)
        crowded = tasks + [{'task_id': f'whole{i}', 'kind': 'whole'} for i in range(20)]
        self.assertEqual(len(training.prefix_curriculum(crowded, 5, True)), 4)
        with self.assertRaisesRegex(ValueError, 'empty'):
            training.prefix_curriculum(tasks, 0, False)

    def test_rlp_slice_balances_families_without_exposing_references(self):
        import run_interleaved_training as training
        rows = [{'id': f'{f}{s}{i}', 'template_family': f, 'curriculum_stage': s,
                 'public': {'prompt': 'public task'}}
                for s in (0, 1, 2) for f in ('a', 'b') for i in range(4)]
        selected = training.rlp_curriculum(rows, 1, 6)
        self.assertEqual(len(selected), 6)
        self.assertEqual({r['template_family'] for r in selected}, {'a', 'b'})
        self.assertTrue(all(r['curriculum_stage'] <= 1 for r in selected))
        self.assertEqual(len({r['id'] for r in selected}), 6)
        self.assertTrue(any(r['curriculum_stage'] == 1 for r in selected))
        self.assertTrue(any(r['curriculum_stage'] == 0 for r in selected))

    def test_rlp_replay_covers_prior_stages_and_all_current_examples(self):
        import run_interleaved_training as training
        rows = [{'id': f'{s}{i}', 'template_family': 'a', 'curriculum_stage': s,
                 'replay': False} for s in range(4) for i in range(8)]
        selected = training.rlp_curriculum(rows, 3, 12)
        self.assertEqual(sum(r['curriculum_stage'] == 3 for r in selected), 8)
        self.assertEqual({r['curriculum_stage'] for r in selected if r['curriculum_stage'] < 3}, {0, 1, 2})

    def test_rlp_handoff_rejects_failed_parent_and_changed_weights(self):
        from run_rlp_pilot import pipeline_checkpoint
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.safetensors').write_bytes(b'weights')
            parent = {'status': 'complete', 'checkpoint': str(root), 'optimizer_steps': 3,
                      'weights_sha256': hashlib.sha256(b'weights').hexdigest()}
            record = root / 'stage.json'
            record.write_text(json.dumps(parent))
            self.assertEqual(pipeline_checkpoint(root, record)['optimizer_steps'], 3)
            parent['status'] = 'failed'
            record.write_text(json.dumps(parent))
            with self.assertRaisesRegex(ValueError, 'complete'):
                pipeline_checkpoint(root, record)
            parent['status'] = 'complete'
            record.write_text(json.dumps(parent))
            (root / 'model.safetensors').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'weights'):
                pipeline_checkpoint(root, record)

    def test_stage_failure_and_timeout_propagate(self):
        import run_interleaved_training as training
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(subprocess.CalledProcessError):
                training.run_command([sys.executable, '-c', 'raise SystemExit(7)'], Path(directory)/'fail.log', 5)
            with self.assertRaises(subprocess.TimeoutExpired):
                training.run_command([sys.executable, '-c', 'import time; time.sleep(5)'], Path(directory)/'timeout.log', .05)


if __name__ == '__main__':
    unittest.main()
