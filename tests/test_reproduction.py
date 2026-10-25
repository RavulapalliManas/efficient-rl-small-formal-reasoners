"""Check reproduction plans with synthetic files, without Lean or model execution."""
import copy
import importlib.metadata
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import reproduce


class ReproductionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='formal-rl-reproduction-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'study'
        self.contract = copy.deepcopy(reproduce.load_contract())
        self.versions = dict(self.contract['reference_packages'], datasets='fixture', tensorboardX='fixture')
        self.package_patch = patch.object(reproduce.importlib.metadata, 'version', side_effect=self.versions.__getitem__)
        self.package_patch.start()
        self.addCleanup(self.package_patch.stop)
        self.platform_patch = patch.object(reproduce.platform, 'system', return_value='Linux')
        self.platform_patch.start()
        self.addCleanup(self.platform_patch.stop)

    def write(self, path, content=b'fixture\n'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def stage_inputs(self, stage, model='S'):
        """Supply tiny fixture files; substitute their hashes in the test copy only."""
        for artifact in self.contract['artifacts']:
            if stage in artifact['stages']:
                path = self.write(self.root / artifact['path'])
                artifact['sha256'] = reproduce.sha256(path)
                if 'rows' in artifact:
                    artifact['rows'] = 1
        weights = self.root / 'models' / model
        path = self.write(weights / 'model.safetensors', model.encode())
        self.contract['models'][model]['sha256'] = reproduce.sha256(path)
        for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'generation_config.json'):
            self.write(weights / name, b'{}\n')
        if stage != 'sft':
            environment = self.root / 'lean_env'
            self.write(environment / 'lean-4.10.0-rc1-linux/bin/lean').chmod(0o700)
            self.write(environment / 'mathlib/lean-toolchain', self.contract['lean']['toolchain'].encode())
            self.write(environment / 'mathlib/lake-manifest.json', b'{}\n')
            for name in ('lean-4.10.0-rc1-linux/lib/lean', 'mathlib/.lake/build/lib', 'mathlib/.lake/packages'):
                (environment / name).mkdir(parents=True, exist_ok=True)
        return weights

    def check(self, stage='dev', model='S'):
        return reproduce.check_inputs(self.contract, self.root, stage, model, self.root / 'models' / model)

    def test_contract_identifies_recorded_files_and_usable_rl_settings(self):
        contract = reproduce.load_contract()
        source_path = contract['source'].split('#', 1)[0]
        catalogue = (reproduce.REPOSITORY / source_path).read_text()
        for entry in [*contract['artifacts'], *contract['models'].values()]:
            self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
            self.assertIn(entry['sha256'], catalogue)
        self.assertEqual(set(contract['stages']), set(reproduce.STAGES))
        sys.path.insert(0, str(reproduce.REPOSITORY / 'scripts/train'))
        self.addCleanup(sys.path.remove, str(reproduce.REPOSITORY / 'scripts/train'))
        from rl_components.configuration import validate_config, validate_environment_settings
        for stage, recipe in contract['stages'].items():
            if stage != 'sft':
                config = dict(recipe['config'], run_id='fixture')
                validate_config(config, forbidden=lambda text: bool(re.search(r'\bsorry\b', text)))
                validate_environment_settings(config)

    def test_each_stage_checks_only_its_inputs(self):
        for stage in reproduce.STAGES:
            with self.subTest(stage=stage):
                model = reproduce.input_model(self.contract, stage)
                self.stage_inputs(stage, model)
                report = self.check(stage, model)
                self.assertTrue(report['inputs_match'], report)
                self.assertFalse(any('test' in Path(row['item']).name.lower() for row in report['checks']))

    def test_missing_and_corrupt_files_are_actionable(self):
        self.assertFalse(self.check()['inputs_match'])
        self.stage_inputs('dev')
        pool = self.root / 'runtime/inputs/dev_pool.jsonl'
        pool.write_bytes(b'changed\n')
        report = self.check()
        failure = next(row for row in report['checks'] if row['item'] == str(pool))
        self.assertEqual(failure['status'], 'mismatch')
        self.assertIn('SHA-256', failure['detail'])
        self.assertFalse(report['inputs_match'])

    def test_row_count_and_wrong_model_weights_fail(self):
        self.stage_inputs('dev')
        self.assertEqual(reproduce.file_check(self.root / 'runtime/inputs/dev_pool.jsonl', rows=2)['status'], 'mismatch')
        (self.root / 'models/S/model.safetensors').write_bytes(b'other model')
        self.assertFalse(self.check()['inputs_match'])

    def test_runtime_pin_permissions_and_package_versions_fail_independently(self):
        self.stage_inputs('dev')
        toolchain = self.root / 'lean_env/mathlib/lean-toolchain'
        toolchain.write_text('leanprover/lean4:other')
        (self.root / 'lean_env/lean-4.10.0-rc1-linux/bin/lean').chmod(0o600)
        self.versions['transformers'] = '5.0.0'
        with patch.object(reproduce.importlib.metadata, 'version', side_effect=importlib.metadata.PackageNotFoundError):
            self.assertFalse(self.check()['inputs_match'])
        failures = {row['item']: row for row in self.check()['checks'] if row['status'] != 'ok'}
        self.assertIn(str(toolchain), failures)
        self.assertIn('transformers', failures)
        self.assertIn('not executable', str(failures))

    def test_dev_accepts_each_model_but_training_requires_its_documented_start(self):
        for model in self.contract['models']:
            self.assertEqual(reproduce.input_model(self.contract, 'dev', model), model)
        for stage, model in [('sft', 'S'), ('rl-shuffled', 'B'), ('rl-curriculum', 'R')]:
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, 'starts from'):
                reproduce.input_model(self.contract, stage, model)

    def test_commands_separate_dev_from_training_and_preserve_quoted_paths(self):
        weights = self.root / 'models/S with spaces'
        for stage in reproduce.STAGES:
            command = reproduce.command_arguments(self.root, stage, weights, self.root / 'config.json', self.root / 'out')
            self.assertEqual(shlex.split(shlex.join(command)), command)
            self.assertIn(str(weights), command)
            self.assertFalse(any('test' in Path(arg).name.lower() for arg in command))
            self.assertEqual('--evaluation-names' in command, stage == 'dev')
            self.assertEqual('--tasks' in command, stage.startswith('rl-'))
            if stage.startswith('rl-'):
                schedule = command[command.index('--task-order') + 1]
                self.assertEqual(Path(schedule).name, 'order_R_runtime.json' if stage == 'rl-shuffled' else 'order_C_AI_runtime.json')

    def test_plan_records_missing_inputs_without_launching_and_refuses_overwrite(self):
        output = self.root.parent / 'plan'
        git_run = subprocess.run
        calls = []

        def only_git(arguments, **kwargs):
            self.assertEqual(arguments[0], 'git', 'Preparing a plan must not launch a job.')
            calls.append(arguments)
            return git_run(arguments, **kwargs)

        with patch.object(reproduce.subprocess, 'run', side_effect=only_git):
            plan = reproduce.prepare_plan(self.contract, self.root, 'dev', 'S', self.root / 'models/S', 'replicate-001', output)
        self.assertEqual(len(calls), 2)
        self.assertEqual(plan['status'], 'prepared_not_run')
        self.assertFalse(plan['input_check']['inputs_match'])
        self.assertEqual(plan['config']['run_id'], 'replicate-001-dev-S')
        self.assertEqual(json.loads((output / 'config.json').read_text()), plan['config'])
        self.assertEqual(shlex.split((output / 'command.txt').read_text()), plan['command'])
        self.assertFalse((output / 'run').exists())
        with self.assertRaises(FileExistsError):
            reproduce.prepare_plan(self.contract, self.root, 'dev', 'S', self.root / 'models/S', 'replicate-002', output)

    def test_plan_rejects_repository_outputs_and_ambiguous_run_ids(self):
        for run_id, output in [('bad id', self.root / 'plan'), ('valid', reproduce.REPOSITORY / 'artifacts/new-plan')]:
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                reproduce.prepare_plan(self.contract, self.root, 'dev', 'S', self.root / 'models/S', run_id, output)


if __name__ == '__main__':
    unittest.main()
