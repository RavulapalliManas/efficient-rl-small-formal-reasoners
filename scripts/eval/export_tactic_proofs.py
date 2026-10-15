"""Export complete model proof paths and replay with the existing strict scorer.

No model or optimizer. Metadata <16 MiB, source context <256 MiB,
one Lean process at a time <=4 GiB. Managed 8 GiB, two cores, 600 seconds.
Only original starts enter this demo. It is not a new accuracy evaluation.
"""
from pathlib import Path
import argparse
import json
import re
import sys
import tarfile
import zipfile

def main():
    parser = argparse.ArgumentParser()
    for name in ('code', 'run', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.code / 'scripts' / folder) for folder in ('train', 'eval', 'data', 'audit')]
    from run_tactic_rl import bank_tasks, evaluation_tasks, training_tasks, tactic_block, close_repl
    from prepare_grpo_pilot import load_checker
    from bench_lean_verifier import repl_context, verify_group
    from probe_nt_pool import lean_unit
    from replay_training_proofs import load, question
    from verify_external_proofs import HEADER

    run = json.loads((args.run / 'run.json').read_text())
    if run['status'] != 'completed' or run['mode'] not in ('DEV_evaluation', 'training_state_assessment'):
        raise RuntimeError('Export requires a completed frozen-checkpoint assessment')
    cfg = run['config']
    inputs = {name: Path(value['path']) for name, value in run['inputs'].items()}
    environment = inputs['mathlib_identity'].parent.parent.parent / 'lean_env_full'
    # The registered source uses the shared environment; derive it from the command record.
    root = args.run.parents[0]
    while not (root / 'lean_env_full').is_dir():
        if root.parent == root:
            raise RuntimeError('Cannot locate the registered Formal-RL environment')
        root = root.parent
    environment = root / 'lean_env_full'
    if (environment / 'mathlib/lean-toolchain').read_text().strip() != run['lean']:
        raise RuntimeError('Replay Lean pin differs from the generation run')
    identity = json.loads(inputs['mathlib_identity'].read_text())
    if identity['mathlib_revision'] != run['mathlib'] or identity['mismatches']:
        raise RuntimeError('Replay mathlib source identity differs from the generation run')
    args.output.mkdir(exist_ok=False)
    (args.output / 'proofs').mkdir()
    checker = load_checker(inputs['checker'], environment, args.output)
    ctx = repl_context(checker, environment.resolve(), root / 'rlvr_verifier/src/repl/.lake/build/bin/repl',
                       args.output, cfg['lean_timeout_seconds'], cfg['lean_setup_timeout_seconds'], 32)
    if 'bank' in inputs:
        for key, path in inputs.items():
            if key.startswith('eval_'):
                with zipfile.ZipFile(path) as archive:
                    with archive.open(archive.namelist()[0]) as stream:
                        for line in stream:
                            row = json.loads(line)
                            ctx.eval_spans.setdefault(row['file_path'], []).append((row['start'][0], row['end'][0], row['declaration_header']))
        rows = [row for key, path in inputs.items() if key.startswith('train_') for row in load(path)]
        ctx.rows = {row['theorem_id']: row for row in rows}
        with inputs['bank'].open() as stream:
            tasks = bank_tasks([json.loads(line) for line in stream], rows,
                              set(inputs['exclude_files'].read_text().split()), ctx.eval_spans)
    else:
        with inputs['pool'].open() as stream:
            pool = [json.loads(line) for line in stream]
        excluded = set(inputs['exclude_names'].read_text().split())
        if run['mode'] == 'training_state_assessment':
            with inputs['tasks'].open() as stream:
                tasks = training_tasks([json.loads(line) for line in stream], pool, excluded,
                                       cfg['curriculum_max_lines_remaining'])
            task_ids = json.loads((args.run / 'evaluation_task_ids.json').read_text())
            by_id = {task['task_id']: task for task in tasks}
            if not task_ids or len(set(task_ids)) != len(task_ids) or not set(task_ids) <= by_id.keys():
                raise RuntimeError('Frozen assessment task IDs differ from admitted TRAIN tasks')
            tasks = [by_id[task_id] for task_id in task_ids]
        else:
            tasks = evaluation_tasks(pool, set(inputs['evaluation_names'].read_text().split()), excluded)
    units = {task['task_id']: task for task in tasks if not task.get('given') and not task.get('given_steps')
             and (task['kind'] == 'whole' or task.get('k') == 0)}
    paths = {}
    with (args.run / 'rollouts.jsonl').open() as stream:
        for row in map(json.loads, stream):
            paths.setdefault((row['group'], row['sample']), []).append(row)
    records, exported = [], set()
    try:
        for (group, sample), path in paths.items():
            last = path[-1]
            task = units.get(last['task_id'])
            if not last['accepted'] or task is None or task['task_id'] in exported:
                continue
            if (last.get('status') != 'accepted' or last.get('done') is not True or
                    last.get('replay', {}).get('status') != 'accepted'):
                raise RuntimeError('Accepted trajectory lacks a complete verified final proof')
            if [row['step'] for row in path] != list(range(len(path))):
                raise RuntimeError('Accepted trajectory is not contiguous')
            blocks = []
            for index, row in enumerate(path):
                if row['status'] not in ('changed', 'accepted'):
                    # A repair leaves the Lean proof state unchanged and never enters LeanEpisode.path.
                    diagnostic = '\n\nThe last tactic failed. The verified goal is unchanged. Lean diagnostic:\n'
                    next_state = path[index + 1]['state'].split(diagnostic, 1)[0] if index + 1 < len(path) else None
                    unchanged = bool(row.get('state')) and next_state == row['state'].split(diagnostic, 1)[0]
                    if (row['status'] in ('rejected', 'format_error') and row.get('repair_offered') is True
                            and row.get('done') is False and row.get('accepted') is False
                            and row.get('replay') is None and unchanged):
                        continue
                    raise RuntimeError('Accepted path includes a failed tactic')
                text = row['tactic']
                fence = re.fullmatch(r'```lean[ \t]*\r?\n(.*?)\r?\n```', text.strip(), re.S)
                blocks.append(tactic_block(fence.group(1) if fence else text))
            body = '\n'.join('  ' + line for block in blocks for line in block.splitlines()) + '\n'
            unit = task if task['kind'] == 'bank' else lean_unit(task)
            verdict = verify_group(ctx, unit, [body])[0]
            proof = None
            if verdict['status'] == 'accepted':
                if task['kind'] == 'bank':
                    q, skip = question(ctx.rows[task['theorem_id']], environment, ctx.sources, 'file', ctx.eval_spans)
                    if q is None:
                        raise RuntimeError('Accepted theorem context failed to reconstruct: ' + str(skip))
                else:
                    q = dict(prefix=HEADER, header=task['header'], name=task['name'], closing=[])
                proof = 'proofs/' + re.sub(r'[^A-Za-z0-9_.-]', '_', task['name']) + '.lean'
                (args.output / proof).write_text(checker.source_for(q, body) + '\n#print axioms ' + q['name'] + '\n')
                exported.add(task['task_id'])
            records.append(dict(task_id=task['task_id'], name=task['name'], group=group, sample=sample,
                proof=proof, body=body, replay=verdict, model_source=run['model_source'],
                run_id=run['run_id'], supplied_prefix=False, raw_path=path))
    finally:
        for repl in ctx.pool_repls:
            close_repl(repl)
    summary = dict(status='completed', source_run=run['run_id'], model_source=run['model_source'],
        lean=run['lean'], mathlib=run['mathlib'], records=records,
        complete_proofs_exported=len(exported), limitation='Selected demonstrations from an existing evaluation; not a fresh accuracy estimate.')
    (args.output / 'replay.json').write_text(json.dumps(summary, indent=2) + '\n')
    with tarfile.open(args.output / 'demo.tar.gz', 'w:gz') as archive:
        archive.add(args.output / 'proofs', arcname='proofs')
        archive.add(args.output / 'replay.json', arcname='replay.json')
    print(json.dumps({key: value for key, value in summary.items() if key != 'records'}))
    if any(row['replay']['status'] != 'accepted' for row in records):
        raise RuntimeError('A stored accepted path failed fresh replay')


if __name__ == "__main__":
    main()
