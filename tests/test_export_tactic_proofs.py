"""Export serialization checks. The substituted verifier does not run Lean."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts' / folder) for folder in ('train', 'eval', 'data', 'audit')]
import bench_lean_verifier  # noqa: E402


class ExportTacticProofsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'scripts/eval/export_tactic_proofs.py'
        spec = importlib.util.spec_from_file_location('export_tactic_proofs', path)
        cls.exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.exporter)

    def export(self, root, path, verdict='accepted', assessment_tasks=None, assessment_ids=None,
               environment_version=1, assessment_split='train'):
        environment = root / 'lean_env_full'
        (environment / 'mathlib').mkdir(parents=True)
        (environment / 'mathlib/lean-toolchain').write_text('leanprover/lean4:v4.10.0-rc1')
        run = root / 'assessment'
        run.mkdir()
        identity = root / 'identity.json'
        identity.write_text(json.dumps(dict(mathlib_revision='pin', mismatches=[])))
        checker = root / 'checker.py'
        checker.write_text(
            "def source_for(q, body):\n"
            "    return q['prefix'] + q['header'] + ' := by\\n' + body\n")
        pool, names = root / 'pool.jsonl', root / 'names.txt'
        pool.write_text(json.dumps(dict(name='Demo', split='train', formal_statement='theorem Demo : True := by sorry')) + '\n')
        names.write_text('Demo\n')
        inputs = {key: dict(path=str(value)) for key, value in dict(
            checker=checker, mathlib_identity=identity, pool=pool,
            evaluation_names=names, exclude_names=names).items()}
        record = dict(status='completed', mode='DEV_evaluation', lean='leanprover/lean4:v4.10.0-rc1',
                      mathlib='pin', config=dict(lean_timeout_seconds=20, lean_setup_timeout_seconds=120),
                      inputs=inputs, model_source='frozen-fixture', run_id='serialization-fixture',
                      environment_version=environment_version)
        if assessment_tasks is not None:
            task_file = root / 'tasks.jsonl'
            task_file.write_text(''.join(json.dumps(task) + '\n' for task in assessment_tasks))
            inputs['tasks'] = dict(path=str(task_file))
            del inputs['evaluation_names']
            names.write_text('' if assessment_split == 'train' else 'Demo\n')
            record['mode'] = 'training_state_assessment' if assessment_split == 'train' else assessment_split + '_assessment'
            record['assessment_split'] = assessment_split
            pool.write_text(json.dumps(dict(name='Demo', split=assessment_split,
                formal_statement='theorem Demo : True := by sorry')) + '\n')
            record['config']['curriculum_max_lines_remaining'] = 3
            task_ids = assessment_ids if assessment_ids is not None else [task['task_id'] for task in assessment_tasks]
            (run / 'evaluation_task_ids.json').write_text(json.dumps(task_ids))
        (run / 'run.json').write_text(json.dumps(record))
        rows = [dict(group=0, sample=0, step=i, task_id='dev:Demo') | row
                for i, row in enumerate(path)]
        (run / 'rollouts.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        output = root / 'export'
        command = ['export_tactic_proofs.py', '--code', str(ROOT), '--run', str(run), '--output', str(output)]
        with patch.object(sys, 'argv', command), patch.object(
                bench_lean_verifier, 'verify_group', return_value=[dict(status=verdict)]) as verifier:
            self.exporter.main()
        replay = json.loads((output / 'replay.json').read_text())
        return output, replay, verifier.call_args.args[2][0] if verifier.call_args else None

    def accepted(self, tactic, state='⊢ True'):
        return dict(tactic=tactic, state=state, status='accepted', accepted=True,
                    done=True, replay=dict(status='accepted'))

    def test_trajectory_object_reconstructs_only_the_verified_body(self):
        self.assertTrue(hasattr(self.exporter, 'ProofTrajectory'), 'Expose proof reconstruction separately')
        body = '  first |\n    exact True.intro\n'
        row = self.accepted('exact True.intro\nexact missing') | dict(step=0, proof_body=body)
        self.assertEqual(self.exporter.ProofTrajectory([row], 2).proof_body(), body)
        with self.assertRaisesRegex(RuntimeError, 'contiguous'):
            self.exporter.ProofTrajectory([dict(row, step=1)], 2).proof_body()

    def test_export_receipt_hashes_replay_modules_and_shared_serializer(self):
        with TemporaryDirectory() as directory:
            _, replay, _ = self.export(Path(directory), [self.accepted('exact True.intro')])
            self.assertIn('export_source_sha256', replay)
            for relative in ('scripts/eval/export_tactic_proofs.py',
                             'scripts/eval/proof_export/exporter.py',
                             'scripts/eval/proof_export/trajectory.py',
                             'scripts/train/rl_components/proofs.py'):
                expected = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(replay['export_source_sha256'][relative], expected)

    def test_diagnosed_multiline_indentation_reaches_replay_and_export_unchanged(self):
        raw = '  refine hn.trans ?_\n  simp_all [Nat.mul_comm]'
        expected = '  first |\n    refine hn.trans ?_\n    simp_all [Nat.mul_comm]\n'
        with TemporaryDirectory() as directory:
            output, replay, submitted = self.export(Path(directory), [self.accepted(raw)])
            self.assertEqual(submitted, expected)
            self.assertEqual(replay['records'][0]['body'], expected)
            self.assertIn(expected, (output / 'proofs/Demo.lean').read_text())
            with tarfile.open(output / 'demo.tar.gz') as archive:
                self.assertIn(expected.encode(), archive.extractfile('proofs/Demo.lean').read())

    def test_nested_and_fenced_blocks_preserve_relative_indentation(self):
        expected = '  first |\n    have h : True := by\n      trivial\n    exact h\n'
        raw = '  have h : True := by\n    trivial\n  exact h'
        for tactic in (raw, '```lean\n' + raw + '\n```'):
            with self.subTest(tactic=tactic), TemporaryDirectory() as directory:
                _, _, submitted = self.export(Path(directory), [self.accepted(tactic)])
                self.assertEqual(submitted, expected)

    def test_single_line_repaired_path_excludes_rejected_action(self):
        diagnostic = '\n\nThe last tactic failed. The verified goal is unchanged. Lean diagnostic:\nbad'
        failed = dict(tactic='  exact unavailable', state='⊢ True', status='rejected',
                      accepted=False, done=False, repair_offered=True, replay=None)
        with TemporaryDirectory() as directory:
            _, replay, submitted = self.export(Path(directory), [failed, self.accepted('  exact True.intro', '⊢ True' + diagnostic)])
            self.assertEqual(submitted, '  first |\n    exact True.intro\n')
            self.assertNotIn('unavailable', submitted)
            self.assertEqual(len(replay['records'][0]['raw_path']), 2)

    def test_failed_action_without_verified_repair_is_rejected(self):
        failed = dict(tactic='  exact unavailable', state='⊢ True', status='rejected',
                      accepted=False, done=False)
        with TemporaryDirectory() as directory, self.assertRaisesRegex(RuntimeError, 'failed tactic'):
            self.export(Path(directory), [failed, self.accepted('  exact True.intro')])

    def test_replay_failure_preserves_failed_delivery(self):
        with TemporaryDirectory() as directory, self.assertRaisesRegex(RuntimeError, 'failed fresh replay'):
            self.export(Path(directory), [self.accepted('  exact True.intro')], verdict='rejected')

    def test_v2_export_replays_final_body_without_failed_or_discarded_actions(self):
        body = '  first |\n    have h := True.intro\n  first |\n    exact h\n'
        path = [dict(tactic='have discarded := True.intro', state='⊢ True', status='changed', accepted=False, done=False),
                dict(tactic='backtrack 0', state='discarded : True\n⊢ True', status='backtrack', accepted=False, done=False),
                dict(tactic='have h := True.intro\nexact missing', state='⊢ True', status='partial',
                     tactics_applied=1, accepted=False, done=False),
                self.accepted('exact h\nunused', state='h : True\n⊢ True') | dict(proof_body=body)]
        with TemporaryDirectory() as directory:
            output, replay, submitted = self.export(Path(directory), path, environment_version=2)
            self.assertEqual(submitted, body)
            self.assertEqual(replay['records'][0]['body'], body)
            self.assertIn(body, (output / 'proofs/Demo.lean').read_text())

    def test_v2_export_preserves_fresh_replay_rejection_and_writes_no_proof(self):
        row = self.accepted('exact True.intro') | dict(proof_body='  first |\n    exact True.intro\n')
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'failed fresh replay'):
                self.export(root, [row], verdict='rejected', environment_version=2)
            replay = json.loads((root / 'export/replay.json').read_text())
            self.assertEqual(replay['records'][0]['replay']['status'], 'rejected')
            self.assertIsNone(replay['records'][0]['proof'])
            self.assertEqual(list((root / 'export/proofs').iterdir()), [])
            with tarfile.open(root / 'export/demo.tar.gz') as archive:
                self.assertEqual(json.load(archive.extractfile('replay.json')), replay)

    def test_v2_export_requires_recorded_body_instead_of_guessing_from_raw_chunks(self):
        for body in (None, '', 42):
            row = self.accepted('exact True.intro\nunused')
            if body is not None:
                row['proof_body'] = body
            with self.subTest(body=body), TemporaryDirectory() as directory, \
                    self.assertRaisesRegex(RuntimeError, 'recorded proof body'):
                self.export(Path(directory), [row], environment_version=2)

    def assessment_task(self, supplied=False):
        return dict(task_id='Demo:prefix_top:1' if supplied else 'Demo:whole:0', name='Demo',
                    header='theorem Demo : True', kind='prefix_top' if supplied else 'whole',
                    given='  have curriculum_fact : True := True.intro\n' if supplied else '',
                    state='curriculum_fact : True\n⊢ True' if supplied else '⊢ True', lines_remaining=1)

    def test_training_assessment_uses_tasks_without_evaluation_names(self):
        task = self.assessment_task()
        row = self.accepted('  exact True.intro') | dict(task_id=task['task_id'])
        with TemporaryDirectory() as directory:
            try:
                output, replay, submitted = self.export(Path(directory), [row], assessment_tasks=[task])
            except KeyError as error:
                self.fail(f'TRAIN assessment incorrectly requires missing field {error}')
            self.assertEqual(replay['complete_proofs_exported'], 1)
            self.assertEqual(replay['records'][0]['task_id'], task['task_id'])
            self.assertEqual(submitted, '  first |\n    exact True.intro\n')
            self.assertTrue((output / 'proofs/Demo.lean').is_file())

    def test_frozen_assessment_exports_truthful_validation_and_test_splits(self):
        task = self.assessment_task()
        row = self.accepted('  exact True.intro') | dict(task_id=task['task_id'])
        for split in ('validation', 'test'):
            with self.subTest(split=split), TemporaryDirectory() as directory:
                _, replay, submitted = self.export(Path(directory), [row], assessment_tasks=[task],
                                                   assessment_split=split)
                self.assertEqual(replay['complete_proofs_exported'], 1)
                self.assertEqual(submitted, '  first |\n    exact True.intro\n')

    def test_training_assessment_excludes_supplied_prefix_success(self):
        task = self.assessment_task(supplied=True)
        row = self.accepted('  exact curriculum_fact') | dict(task_id=task['task_id'])
        with TemporaryDirectory() as directory:
            try:
                output, replay, submitted = self.export(Path(directory), [row], assessment_tasks=[task])
            except KeyError as error:
                self.fail(f'TRAIN assessment incorrectly requires missing field {error}')
            self.assertEqual(replay['complete_proofs_exported'], 0)
            self.assertEqual(replay['records'], [])
            self.assertIsNone(submitted)
            self.assertEqual(list((output / 'proofs').iterdir()), [])

    def test_training_assessment_rejects_ids_outside_admitted_tasks(self):
        task = self.assessment_task()
        row = self.accepted('  exact True.intro') | dict(task_id=task['task_id'])
        with TemporaryDirectory() as directory:
            try:
                with self.assertRaisesRegex(RuntimeError, 'assessment task IDs'):
                    self.export(Path(directory), [row], assessment_tasks=[task], assessment_ids=['unknown:whole:0'])
            except KeyError as error:
                self.fail(f'TRAIN assessment incorrectly requires missing field {error}')


if __name__ == '__main__':
    unittest.main()
