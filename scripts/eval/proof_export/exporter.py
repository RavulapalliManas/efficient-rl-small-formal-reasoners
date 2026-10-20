"""Re-admit saved tasks, replay complete proofs, and serialize export artifacts."""
from pathlib import Path
import hashlib
import json
import re
import sys
import tarfile
import zipfile

from .trajectory import ProofTrajectory


class ProofExporter:
    """Keep task admission, pinned replay, and delivery in one auditable operation."""

    def __init__(self, code, run, output, trajectory_type=ProofTrajectory, verifier=None):
        self.code, self.run, self.output = code, run, output
        self.trajectory_type = trajectory_type
        self.verifier = verifier

    def _source_hashes(self):
        from rl_components import component_fingerprints

        package = Path(__file__).resolve().parent
        root = package.parents[2]
        paths = [package.parent / 'export_tactic_proofs.py', *sorted(package.glob('*.py'))]
        sources = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in paths}
        sources.update({'scripts/train/' + name: value for name, value in component_fingerprints().items()})
        for name, folder in (('run_tactic_rl', 'train'), ('run_lean_grpo', 'train'), ('bench_lean_verifier', 'eval')):
            path = Path(sys.modules[name].__file__)
            sources[f'scripts/{folder}/{path.name}'] = hashlib.sha256(path.read_bytes()).hexdigest()
        return sources

    def export(self):
        sys.path[:0] = [str(self.code / 'scripts' / folder) for folder in ('train', 'eval', 'data', 'audit')]
        from run_tactic_rl import assessment_tasks, bank_tasks, evaluation_tasks, execution_mode, close_repl
        from prepare_grpo_pilot import load_checker
        from bench_lean_verifier import repl_context, verify_group
        from probe_nt_pool import lean_unit
        from replay_training_proofs import load, question
        from verify_external_proofs import HEADER

        verifier = self.verifier if self.verifier is not None else verify_group

        run = json.loads((self.run / 'run.json').read_text())
        if run['status'] != 'completed' or run['mode'] not in (
                'DEV_evaluation', 'training_state_assessment', 'validation_assessment', 'test_assessment'):
            raise RuntimeError('Export requires a completed frozen-checkpoint assessment')
        assessment_split = run.get('assessment_split') or 'train'
        if run['mode'] != 'DEV_evaluation' and run['mode'] != execution_mode(False, True, assessment_split):
            raise RuntimeError('Assessment split differs from the recorded mode')
        cfg = run['config']
        inputs = {name: Path(value['path']) for name, value in run['inputs'].items()}
        environment = inputs['mathlib_identity'].parent.parent.parent / 'lean_env_full'
        # The registered source uses the shared environment; derive it from the command record.
        root = self.run.parents[0]
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
        self.output.mkdir(exist_ok=False)
        (self.output / 'proofs').mkdir()
        checker = load_checker(inputs['checker'], environment, self.output)
        ctx = repl_context(checker, environment.resolve(), root / 'rlvr_verifier/src/repl/.lake/build/bin/repl',
                           self.output, cfg['lean_timeout_seconds'], cfg['lean_setup_timeout_seconds'], 32)
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
            if run['mode'] != 'DEV_evaluation':
                with inputs['tasks'].open() as stream:
                    tasks = assessment_tasks([json.loads(line) for line in stream], pool, excluded,
                                             cfg['curriculum_max_lines_remaining'], assessment_split)
                task_ids = json.loads((self.run / 'evaluation_task_ids.json').read_text())
                by_id = {task['task_id']: task for task in tasks}
                if not task_ids or len(set(task_ids)) != len(task_ids) or not set(task_ids) <= by_id.keys():
                    raise RuntimeError('Frozen assessment task IDs differ from admitted tasks')
                tasks = [by_id[task_id] for task_id in task_ids]
            else:
                tasks = evaluation_tasks(pool, set(inputs['evaluation_names'].read_text().split()), excluded)
        units = {task['task_id']: task for task in tasks if not task.get('given') and not task.get('given_steps')
                 and (task['kind'] == 'whole' or task.get('k') == 0)}
        paths = {}
        with (self.run / 'rollouts.jsonl').open() as stream:
            for row in map(json.loads, stream):
                paths.setdefault((row['group'], row['sample']), []).append(row)
        records, exported = [], set()
        try:
            for (group, sample), path in paths.items():
                last = path[-1]
                task = units.get(last['task_id'])
                if not last['accepted'] or task is None or task['task_id'] in exported:
                    continue
                body = self.trajectory_type(path, run.get('environment_version', 1)).proof_body()
                unit = task if task['kind'] == 'bank' else lean_unit(task)
                verdict = verifier(ctx, unit, [body])[0]
                proof = None
                if verdict['status'] == 'accepted':
                    if task['kind'] == 'bank':
                        q, skip = question(ctx.rows[task['theorem_id']], environment, ctx.sources, 'file', ctx.eval_spans)
                        if q is None:
                            raise RuntimeError('Accepted theorem context failed to reconstruct: ' + str(skip))
                    else:
                        q = dict(prefix=HEADER, header=task['header'], name=task['name'], closing=[])
                    proof = 'proofs/' + re.sub(r'[^A-Za-z0-9_.-]', '_', task['name']) + '.lean'
                    (self.output / proof).write_text(checker.source_for(q, body) + '\n#print axioms ' + q['name'] + '\n')
                    exported.add(task['task_id'])
                records.append(dict(task_id=task['task_id'], name=task['name'], group=group, sample=sample,
                    proof=proof, body=body, replay=verdict, model_source=run['model_source'],
                    run_id=run['run_id'], supplied_prefix=False, raw_path=path))
        finally:
            for repl in ctx.pool_repls:
                close_repl(repl)
        summary = dict(status='completed', source_run=run['run_id'], model_source=run['model_source'],
            lean=run['lean'], mathlib=run['mathlib'], records=records,
            export_source_sha256=self._source_hashes(),
            complete_proofs_exported=len(exported), limitation='Selected demonstrations from an existing evaluation; not a fresh accuracy estimate.')
        (self.output / 'replay.json').write_text(json.dumps(summary, indent=2) + '\n')
        with tarfile.open(self.output / 'demo.tar.gz', 'w:gz') as archive:
            archive.add(self.output / 'proofs', arcname='proofs')
            archive.add(self.output / 'replay.json', arcname='replay.json')
        print(json.dumps({key: value for key, value in summary.items() if key not in ('records', 'export_source_sha256')}))
        if any(row['replay']['status'] != 'accepted' for row in records):
            raise RuntimeError('A stored accepted path failed fresh replay')
