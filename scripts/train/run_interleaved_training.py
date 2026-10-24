"""Alternate existing SFT, hidden-target RLP, and Lean GRPO runners.

One subprocess owns each stage and loads one policy at a time. The orchestrator
holds JSON metadata (<200 MiB); run the training service under a 56 GiB host cap.
The managed lease enforces the monetary ceiling; this runner enforces wall caps.
Optimizer state resets at objective boundaries. Failed parents cannot propagate.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import time


def balanced_prompt_order(rows, seed):
    """Present one task per theorem before presenting another cut of that theorem."""
    families = defaultdict(list)
    for row in rows:
        families[row['problem_name']].append(row)
    rng = random.Random(seed)
    names = sorted(families)
    rng.shuffle(names)
    for family in families.values():
        rng.shuffle(family)
    return [families[name][i] for i in range(max(map(len, families.values()), default=0))
            for name in names if i < len(families[name])]


def prefix_curriculum(tasks, max_remaining, include_whole):
    selected = [t for t in tasks if t['kind'] in ('prefix_top', 'prefix_have')
                and 0 < t['lines_remaining'] <= max_remaining]
    if include_whole:
        # Whole proofs occupy at most a quarter, so they cannot swamp assisted tasks.
        whole = [t for t in tasks if t['kind'] == 'whole']
        random.Random(20261010).shuffle(whole)
        selected += whole[:len(selected) // 3]
    if not selected:
        raise ValueError('Proof curriculum is empty')
    return selected


def rlp_curriculum(rows, max_stage, limit):
    families = defaultdict(list)
    for row in rows:
        if row['curriculum_stage'] <= max_stage:
            families[row['template_family']].append(row)
    for name, family in families.items():
        new = [r for r in family if r['curriculum_stage'] == max_stage and not r.get('replay', False)]
        prior = defaultdict(list)
        for row in family:
            if row['curriculum_stage'] < max_stage and not row.get('replay', False):
                prior[row['curriculum_stage']].append(row)
        replay = [prior[s][i] for i in range(max(map(len, prior.values()), default=0))
                  for s in sorted(prior) if i < len(prior[s])]
        mixed = []
        while new or replay:
            if replay and (not new or len(mixed) % 4 == 1):
                mixed.append(replay.pop(0))
            else:
                mixed.append(new.pop(0))
        families[name] = mixed
    selected = [family[i] for i in range(max(map(len, families.values()), default=0))
                for family in families.values() if i < len(family)][:limit]
    if not selected:
        raise ValueError('RLP curriculum is empty')
    return selected


def run_command(command, log, timeout):
    with log.open('x') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        if code:
            raise subprocess.CalledProcessError(code, command)


def weight_digest(checkpoint):
    digest = hashlib.sha256()
    with (checkpoint / 'model.safetensors').open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def reuse_completed_stage(source, stage, root):
    """Import a completed objective boundary, never an interrupted optimizer step."""
    metadata = source / stage['id'] / 'stage.json'
    if not metadata.exists():
        return None
    item = json.loads(metadata.read_text())
    if item.get('status') != 'complete':
        return None
    prior = json.loads((source / 'plan.json').read_text())
    if stage not in prior['stages'] or any(item.get(k) != stage.get(k) for k in ('id', 'kind', 'parent')):
        raise ValueError('Completed stage protocol differs from recovery plan')
    config = json.loads((root / stage['config']).read_text())
    config.update(stage.get('overrides', {}))
    if stage['kind'] == 'rlp':
        config.update(initialization='pipeline_handoff', max_steps=len(item['training_ids']), training_authorized=True)
    if config != item['resolved_config']:
        raise ValueError('Completed stage resolved config differs from recovery inputs')
    final = 'policy_final' if stage['kind'] == 'rlp' else 'final'
    checkpoint = (source / stage['id'] / 'training' / final).resolve()
    if Path(item['checkpoint']).parts[-3:] != (stage['id'], 'training', final):
        raise ValueError('Completed stage names a different checkpoint boundary')
    result = json.loads((checkpoint.parent / 'run.json').read_text())
    if result.get('status', 'complete' if stage['kind'] == 'sft' else None) != 'complete':
        raise ValueError('Source training did not report complete')
    if not (checkpoint / 'tokenizer_config.json').exists() or not (checkpoint / 'model.safetensors').exists():
        raise ValueError('Completed checkpoint model or tokenizer missing')
    if weight_digest(checkpoint) != item['weights_sha256']:
        raise ValueError('Completed checkpoint weights differ from source record')
    return dict(item, checkpoint=str(checkpoint), reused=True, source_stage_record=str(metadata.resolve()))


def main():
    parser = argparse.ArgumentParser()
    for name in ('plan', 'root', 'model', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--resume-from', type=Path, help='Immutable archived stage directory; reuse complete boundaries only')
    args = parser.parse_args()
    root = args.root.resolve()
    plan = json.loads(args.plan.read_text())
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    record = {'status': 'running', 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'initial_model': str(args.model.resolve()), 'stages': []}
    if args.resume_from:
        args.resume_from = args.resume_from.resolve()
        record['resume_from'] = str(args.resume_from)
    started = time.monotonic()
    completed = {}
    train_dir = Path(__file__).resolve().parent
    for stage in plan['stages']:
        work = args.output / stage['id']
        work.mkdir(exist_ok=False)
        item = {'id': stage['id'], 'kind': stage['kind'], 'parent': stage.get('parent'), 'status': 'starting'}
        record['stages'].append(item)
        stage_started = time.monotonic()
        try:
            if stage.get('parent') and stage['parent'] not in completed:
                raise RuntimeError('Parent stage did not complete')
            reused = reuse_completed_stage(args.resume_from, stage, root) if args.resume_from else None
            if reused is not None:
                item.update(reused)
                completed[stage['id']] = item.copy()
                (work / 'stage.json').write_text(json.dumps(item, indent=2) + '\n')
                (args.output / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
                print(json.dumps({'id': stage['id'], 'status': 'complete', 'reused': True}), flush=True)
                continue
            if stage.get('parent'):
                if stage['parent'] not in completed:
                    raise RuntimeError('Parent stage did not complete')
                parent = completed[stage['parent']]
                model = Path(parent['checkpoint'])
            else:
                model = args.model.resolve()
                parent = None
            cfg = json.loads((root / stage['config']).read_text())
            cfg.update(stage.get('overrides', {}))
            command = [sys.executable, str(train_dir / {'sft': 'run_sft.py', 'rlp': 'run_rlp_pilot.py',
                                                       'grpo': 'run_lean_grpo.py'}[stage['kind']])]
            output = work / 'training'
            command += ['--config', str(work / 'config.json'), '--output', str(output)]
            if stage['kind'] == 'sft':
                command += ['--model', str(model), '--data', str(root / stage['data'])]
            elif stage['kind'] == 'rlp':
                if parent is None:
                    raise RuntimeError('RLP interleaving requires a completed parent')
                source = root / stage['data']
                data = work / 'data'
                shutil.copytree(source, data)
                with (source / 'train.jsonl').open() as input_stream:
                    rows = [json.loads(line) for line in input_stream]
                selected = rlp_curriculum(rows, stage['max_curriculum_stage'], stage['presentations'])
                selected_ids = {r['id'] for r in selected}
                with (source / 'private/train_references.jsonl').open() as input_stream:
                    refs = [json.loads(line) for line in input_stream]
                (data / 'train.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in selected))
                (data / 'private/train_references.jsonl').write_text(
                    ''.join(json.dumps(r) + '\n' for r in refs if r['id'] in selected_ids))
                cfg.update(initialization='pipeline_handoff', max_steps=len(selected), training_authorized=True)
                command += ['--initial-model', str(model), '--data', str(data),
                            '--parent-metadata', str(args.output / stage['parent'] / 'stage.json')]
                item['training_ids'] = [r['id'] for r in selected]
            else:
                command += ['--model', str(model)]
                for key, value in stage['arguments'].items():
                    for path in value if isinstance(value, list) else [value]:
                        command += ['--' + key, str(root / path)]
            (work / 'config.json').write_text(json.dumps(cfg, indent=2) + '\n')
            item.update(command=command, initial_model=str(model), resolved_config=cfg)
            remaining = plan['max_wall_seconds'] - (time.monotonic() - started)
            timeout = min(stage['max_wall_seconds'], remaining)
            if timeout <= 0:
                raise TimeoutError('Interleaving wall cap reached')
            (work / 'stage.json').write_text(json.dumps(item, indent=2) + '\n')
            run_command(command, work / 'training.log', timeout)
            result = json.loads((output / 'run.json').read_text())
            if result.get('status', 'complete' if stage['kind'] == 'sft' else None) != 'complete':
                raise RuntimeError('Training did not report complete')
            checkpoint = output / ('policy_final' if stage['kind'] == 'rlp' else 'final')
            if not (checkpoint / 'tokenizer_config.json').exists():
                raise RuntimeError('Checkpoint tokenizer missing')
            item.update(status='complete', checkpoint=str(checkpoint), weights_sha256=weight_digest(checkpoint),
                        optimizer_steps=result.get('optimizer_steps', result.get('global_steps')),
                        training_generated_tokens=result.get('training_generated_tokens',
                                                           result.get('counts', {}).get('generated_tokens', 0)))
            completed[stage['id']] = item.copy()
        except Exception as error:
            item.update(status='failed', error=f'{type(error).__name__}: {error}')
        item['wall_seconds'] = time.monotonic() - stage_started
        (work / 'stage.json').write_text(json.dumps(item, indent=2) + '\n')
        (args.output / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps({k: item[k] for k in ('id', 'status', 'wall_seconds')}), flush=True)
    record.update(status='complete' if len(completed) == len(plan['stages']) else 'failed',
                  wall_seconds=time.monotonic() - started)
    (args.output / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    if record['status'] != 'complete':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
