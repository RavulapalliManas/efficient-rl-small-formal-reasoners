"""Inspect, check, and prepare the documented number-theory study without launching jobs."""
import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess

REPOSITORY = Path(__file__).resolve().parents[1]
CONTRACT = REPOSITORY / 'configs/reproduction_v1.json'
STAGES = ('sft', 'dev', 'rl-shuffled', 'rl-curriculum')


def load_contract(path=CONTRACT):
    """Load the versioned data, model, and recipe definitions."""
    return json.loads(path.read_text())


def sha256(path):
    """Hash a file in bounded memory, including large model weights."""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def input_model(contract, stage, requested=None):
    """Keep the model comparison and training initialization explicit."""
    expected = contract['stages'][stage]['input_model']
    model = requested or expected
    if stage != 'dev' and model != expected:
        raise ValueError(f'{stage} starts from {expected}; use --model {expected}.')
    return model


def file_check(path, expected=None, rows=None):
    """Compare a local file to its recorded identity without modifying it."""
    if not path.is_file():
        return dict(item=str(path), status='missing', detail='Obtain this file from the frozen study archive.')
    if expected is not None and sha256(path) != expected:
        return dict(item=str(path), status='mismatch', detail='SHA-256 differs from the recorded study file.')
    if rows is not None:
        with path.open('rb') as stream:
            actual = sum(1 for _ in stream)
        if actual != rows:
            return dict(item=str(path), status='mismatch', detail=f'Expected {rows} rows; found {actual}.')
    return dict(item=str(path), status='ok', detail='Recorded file hash matches.' if expected else 'File is present.')


def check_inputs(contract, root, stage, model, weights):
    """Check files and package presence; do not execute Lean or a model."""
    checks = []
    for artifact in contract['artifacts']:
        if stage in artifact['stages']:
            checks.append(file_check(root / artifact['path'], artifact['sha256'], artifact.get('rows')))

    checks.append(file_check(weights / 'model.safetensors', contract['models'][model]['sha256']))
    for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'generation_config.json'):
        checks.append(file_check(weights / name))

    if stage != 'sft':
        environment = root / 'lean_env'
        lean = environment / 'lean-4.10.0-rc1-linux/bin/lean'
        executable = file_check(lean)
        if lean.is_file() and not os.access(lean, os.X_OK):
            executable.update(status='mismatch', detail='The Lean binary is not executable.')
        checks.append(executable)
        toolchain = environment / 'mathlib/lean-toolchain'
        check = file_check(toolchain)
        if toolchain.is_file() and toolchain.read_text().strip() != contract['lean']['toolchain']:
            check.update(status='mismatch', detail='Lean toolchain differs from the recorded pin.')
        checks.append(check)
        checks.append(file_check(environment / 'mathlib/lake-manifest.json'))
        for name in ('lean-4.10.0-rc1-linux/lib/lean', 'mathlib/.lake/build/lib', 'mathlib/.lake/packages'):
            directory = environment / name
            checks.append(dict(item=str(directory), status='ok' if directory.is_dir() else 'missing',
                               detail='Compiled runtime directory.'))
        if platform.system() != 'Linux':
            checks.append(dict(item='operating system', status='mismatch', detail='The archived Lean runtime requires Linux.'))

    packages = ['torch', 'transformers']
    if stage == 'sft':
        packages += ['trl', 'datasets', 'tensorboardX']
    for package in packages:
        try:
            version = importlib.metadata.version(package)
            expected = contract['reference_packages'].get(package)
            matches = expected is None or version.split('+', 1)[0] == expected
            detail = 'Installed version: ' + version
            if not matches:
                detail += f'; expected {expected}. Use uv sync --group training.'
            checks.append(dict(item=package, status='ok' if matches else 'mismatch', detail=detail))
        except importlib.metadata.PackageNotFoundError:
            checks.append(dict(item=package, status='missing', detail='Install on the run host with uv sync --group training.'))

    return dict(stage=stage, model=model, inputs_match=all(check['status'] == 'ok' for check in checks),
                scope='File hashes, row counts, metadata presence and selected package versions only. No Lean, model, GPU or tokenizer-equivalence check.',
                checks=checks)


def command_arguments(root, stage, weights, config, output):
    """Build argument lists for the existing SFT and interactive RL commands."""
    prefix = ['uv', 'run', '--group', 'training', 'python']
    if stage == 'sft':
        return prefix + ['scripts/train/run_sft.py', '--config', str(config),
                         '--data', str(root / 'data/sft_action'), '--model', str(weights),
                         '--output', str(output)]
    command = prefix + ['scripts/train/run_tactic_rl.py', '--config', str(config),
                        '--model', str(weights), '--exclude-names', str(root / 'data/actor/exclude_names.txt'),
                        '--checker', str(root / 'runtime/inputs/checker.py'),
                        '--environment', str(root / 'lean_env'),
                        '--mathlib-identity', str(root / 'runtime/inputs/mathlib_source_identity.json'),
                        '--repl', str(root / 'runtime/inputs/repl'), '--output', str(output)]
    if stage == 'dev':
        command += ['--pool', str(root / 'runtime/inputs/dev_pool.jsonl'),
                    '--evaluation-names', str(root / 'runtime/inputs/dev_names.txt')]
    else:
        schedule = 'order_R_runtime.json' if stage == 'rl-shuffled' else 'order_C_AI_runtime.json'
        command += ['--pool', str(root / 'data/actor/train_pool.jsonl'),
                    '--tasks', str(root / 'data/actor/train_tasks.jsonl'),
                    '--task-order', str(root / 'schedules' / schedule)]
    return command


def prepare_plan(contract, root, stage, model, weights, run_id, output):
    """Write a new reviewable plan; never execute its command."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', run_id):
        raise ValueError('Use a run ID containing letters, numbers, dots, underscores or hyphens.')
    if output.is_relative_to(REPOSITORY):
        raise ValueError('Use an output directory outside the repository.')
    recipe = contract['stages'][stage]
    config = copy.deepcopy(recipe['config'])
    config.update(experiment_id=recipe['experiment_id'], run_id=f'{run_id}-{stage}-{model}',
                  purpose=contract['recipe_status'])
    checks = check_inputs(contract, root, stage, model, weights)
    arguments = command_arguments(root, stage, weights, output / 'config.json', output / 'run')
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPOSITORY,
                              text=True, capture_output=True, check=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=REPOSITORY,
                           text=True, capture_output=True, check=True).stdout != ''
    plan = dict(status='prepared_not_run', source_record=contract['source'],
                recipe_status=contract['recipe_status'], software_status=contract['software_status'],
                source_git_revision=revision, source_dirty=dirty,
                contract_sha256=sha256(CONTRACT), config=config, input_check=checks,
                working_directory=str(REPOSITORY), command=arguments,
                output=str(output / 'run'), result=recipe['result'],
                execution=contract['execution'])
    output.mkdir(parents=True, exist_ok=False)
    (output / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    (output / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    (output / 'command.txt').write_text(shlex.join(arguments) + '\n')
    return plan


def print_overview(contract):
    print('Number-theory study: documented recipe of 18-19 October 2026')
    print('B -> supervised fine-tuning -> S -> shuffled RL (R) or curriculum RL (C)')
    for model, definition in contract['models'].items():
        print(f"  {model}: {definition['meaning']}")
    sizes = {entry['path']: entry.get('rows') for entry in contract['artifacts']}
    print(f"\nData: validated_v2 supplies {sizes['data/actor/train_pool.jsonl']} TRAIN theorems "
          f"and {sizes['data/sft_action/sft_train.jsonl']} action SFT examples.")
    print(f"SFT loss validation: {sizes['data/sft_action/sft_val.jsonl']} TRAIN-only examples. "
          f"Research DEV: {sizes['runtime/inputs/dev_pool.jsonl']} separate theorems.")
    print(f"TEST: {contract['reserved_test_theorems']} reserved theorems; this helper never selects or reads them.")
    print('\nAvailable stages:')
    for stage, recipe in contract['stages'].items():
        print(f"  {stage}: start from {recipe['input_model']}; {recipe['result']}")
    print('\nAccess: ' + contract['access'])
    print('Recipe: ' + contract['recipe_status'])
    print('Execution: ' + contract['execution'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='action', required=True)
    subparsers.add_parser('overview', help='Explain the model, data and stage names.')
    for name in ('check', 'prepare'):
        subparser = subparsers.add_parser(name)
        subparser.add_argument('--root', type=Path, required=True, help='Extracted study artifacts outside Git.')
        subparser.add_argument('--stage', choices=STAGES, default='dev')
        subparser.add_argument('--model', choices=('B', 'S', 'R', 'C'))
        subparser.add_argument('--weights', type=Path, help='Model directory; defaults to ROOT/models/MODEL.')
        subparser.add_argument('--json', action='store_true')
        if name == 'prepare':
            subparser.add_argument('--run-id', required=True, help='Unique prefix for this proposed run.')
            subparser.add_argument('--output', type=Path, required=True, help='New plan directory outside the repository.')
    args = parser.parse_args()
    contract = load_contract()
    if args.action == 'overview':
        print_overview(contract)
        return 0
    try:
        root = args.root.resolve()
        model = input_model(contract, args.stage, args.model)
        weights = (args.weights or root / 'models' / model).resolve()
        if args.action == 'check':
            report = check_inputs(contract, root, args.stage, model, weights)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                for check in report['checks']:
                    print(f"{check['status'].upper():8} {check['item']}: {check['detail']}")
                print(report['scope'])
            return 0 if report['inputs_match'] else 1
        plan = prepare_plan(contract, root, args.stage, model, weights, args.run_id, args.output.resolve())
        if args.json:
            print(json.dumps(plan, indent=2))
        else:
            print('Prepared, not run: ' + str(args.output.resolve()))
            print('Input checks: ' + ('matched' if plan['input_check']['inputs_match'] else 'missing or mismatched inputs'))
            print('Review config.json, plan.json and command.txt before any execution.')
            print(contract['execution'])
        return 0
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
