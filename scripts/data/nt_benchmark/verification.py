"""Pinned Lean replay, goal extraction, and complete baseline screening."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from probe_nt_pool import states
from single_tactic_baseline import TACTICS
from probe_start_depth import FORBIDDEN
from verify_external_proofs import check_batch
from .artifacts import BenchmarkExporter
from .identity import check_repositories
from .storage import file_sha256, rows, source_hashes

SCREEN_TACTICS = [tactic for tactic in TACTICS if not FORBIDDEN.search(tactic)]


class LeanVerifier:
    """Own pinned Lean process access; each call preserves the existing timeout."""

    def __init__(self, config, environment, identity_path):
        identity = json.loads(identity_path.read_text())
        mathlib = next(r for r in identity['repositories'] if r['name'] == 'mathlib')
        if not identity['clean'] or mathlib['revision'] != config['mathlib'] or not identity['toolchain'].endswith(config['lean']):
            raise ValueError('Verifier identity differs from benchmark pin')
        environment = environment.resolve()
        mathlib_root = environment / 'mathlib'
        if not (mathlib_root / '.git').exists():
            if any(not repository.get('source_blobs') for repository in identity['repositories']):
                raise ValueError('Cannot verify live mathlib identity without Git metadata or registered source hashes')
            packages = json.loads((mathlib_root / 'lake-manifest.json').read_text())['packages']
            expected = {'mathlib': config['mathlib'], **{p['name']: p['rev'] for p in packages}}
            if {r['name']: r['revision'] for r in identity['repositories']} != expected:
                raise ValueError('Snapshot dependency revisions differ from registered identity')
            for repository in identity['repositories']:
                root = (mathlib_root if repository['name'] == 'mathlib' else
                        mathlib_root / '.lake/packages' / repository['name'])
                live = {}
                # Match the established pinned-tree checker; build/cache directories are separate.
                for directory, directories, files in os.walk(root):
                    directories[:] = [d for d in directories if d not in
                                      {'.lake', '.git', '.cache', '.github', '.vscode', '.devcontainer', '.docker'}]
                    for name in files:
                        if name.endswith(('.lean', 'lean-toolchain', 'lake-manifest.json', 'lakefile.lean', 'lakefile.toml')):
                            path = Path(directory) / name
                            data = path.read_bytes()
                            live[str(path.relative_to(root))] = hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
                if live != repository['source_blobs']:
                    raise ValueError('Live source snapshot differs from registered identity: ' + repository['name'])
            self.live_mathlib_revision = config['mathlib']
            self.mathlib_identity_source = 'live_source_blob_hashes'
        else:
            live_revision = subprocess.run(['git', '-C', str(mathlib_root), 'rev-parse', '--verify', 'HEAD'],
                                          capture_output=True, text=True, check=True).stdout.strip()
            if live_revision != config['mathlib']:
                raise ValueError('Live mathlib revision differs from registered pin')
            live_changes = subprocess.run(['git', '-C', str(mathlib_root), 'status', '--porcelain'],
                                         capture_output=True, text=True, check=True).stdout
            if live_changes:
                raise ValueError('Live mathlib worktree differs from registered identity')
            registered = {repository['name']: repository['revision'] for repository in identity['repositories']}
            live = {repository['name']: repository['revision'] for repository in check_repositories(environment, config)}
            if len(registered) != len(identity['repositories']) or registered != live:
                raise ValueError('Live dependency revisions differ from registered identity')
            self.live_mathlib_revision = live_revision
            self.mathlib_identity_source = 'live_git_revision_and_clean_worktree'
        if (environment / 'mathlib/lean-toolchain').read_text().strip() != identity['toolchain']:
            raise ValueError('Environment toolchain differs from identity receipt')
        lean = environment / f"lean-{config['lean'].removeprefix('v')}-linux/bin/lean"
        version = subprocess.run([str(lean), '--version'], capture_output=True, text=True, check=True).stdout.strip()
        if config['lean'].removeprefix('v') not in version:
            raise ValueError('Lean binary differs from registered pin')
        libs = [environment / 'mathlib/.lake/build/lib'] + sorted((environment / 'mathlib/.lake/packages').glob('*/.lake/build/lib'))
        env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
        self.lean, self.env, self.version = lean, env, version
        self.timeout = config['timeout']

    def check(self, path, items):
        return check_batch(self.lean, self.env, path, items, self.timeout, strict=True)

    def goal_states(self, path, items):
        return states(self.lean, self.env, path, items, self.timeout)


class BenchmarkVerifier:
    """Preserve raw outcomes before exporting admitted tasks from every split."""

    def __init__(self, exporter=None):
        self.exporter = exporter if exporter is not None else BenchmarkExporter()

    def verify(self, package, output, environment, identity_path):
        manifest = json.loads((package / 'manifest.json').read_text())
        config = manifest['config']
        candidates_path = package / 'private/candidates.jsonl'
        if file_sha256(candidates_path) != manifest['candidates_sha256']:
            raise ValueError('Frozen candidate file changed')
        screen_tactics = manifest.get('screen_tactics', SCREEN_TACTICS)
        if not screen_tactics or any(FORBIDDEN.search(tactic) for tactic in screen_tactics):
            raise ValueError('Selection contains unsupported screen tactics; create a new selection with the current builder')
        backend = LeanVerifier(config, environment, identity_path)
        output.mkdir(parents=True, exist_ok=False)
        (output / 'private').mkdir()
        frozen = list(rows(candidates_path))
        if len(frozen) > config['max_candidates']:
            raise ValueError('Candidate cap exceeded')
        admitted, counts = [], Counter()
        started = time.monotonic()
        with (output / 'private/verification.jsonl').open('x') as results:
            for start in range(0, len(frozen), config['batch']):
                batch = frozen[start:start + config['batch']]
                pending = [r for r in batch if not r['excluded_reason']]
                for row in batch:
                    if row['excluded_reason']:
                        results.write(json.dumps(dict(instance_id=row['instance_id'], status=row['excluded_reason'])) + '\n')
                        counts[row['excluded_reason']] += 1
                if not pending:
                    results.flush()
                    continue
                items = [dict(text=r['header'] + ' := by\n' + r['reference_body']) for r in pending]
                checked, seconds = backend.check(output / 'private/reference_batch.lean', items)
                counts['lean_process_calls'] += 1
                if len(checked) != len(pending):
                    raise RuntimeError('Verifier returned an incomplete reference batch')
                goal_states = backend.goal_states(output / 'private/state_batch.lean',
                                             [dict(formal_statement=r['header']) for r in pending])
                counts['lean_process_calls'] += 1
                if len(goal_states) != len(pending):
                    raise RuntimeError('Verifier returned incomplete goal states')
                screens = [[] for _ in pending]
                for tactic in screen_tactics:
                    screen, _ = backend.check(output / 'private/screen_batch.lean',
                        [dict(text=r['header'] + ' := by\n  ' + tactic) for r in pending])
                    counts['lean_process_calls'] += 1
                    if len(screen) != len(pending):
                        raise RuntimeError('Verifier returned an incomplete screen batch')
                    for trace, result in zip(screens, screen):
                        trace.append(dict(tactic=tactic, **result))
                for row, reference, state, screen in zip(pending, checked, goal_states, screens):
                    solved = any(r['status'] == 'accepted' for r in screen)
                    complete = all(r['status'] in ('accepted', 'rejected', 'sorry_only') for r in screen)
                    row.update(reference_status=reference['status'], state=state,
                               screen_status='solved' if solved else 'not_solved' if complete else 'incomplete')
                    status = ('reference_' + reference['status'] if reference['status'] != 'accepted' else
                              'state_unavailable' if not state or state == 'no goals' else
                              'screen_' + row['screen_status'] if row['screen_status'] != 'not_solved' else 'admitted')
                    results.write(json.dumps(dict(instance_id=row['instance_id'], status=status, reference=reference,
                                                 state=state, screen=screen), ensure_ascii=False) + '\n')
                    counts[status] += 1
                    if status == 'admitted':
                        admitted.append(row)
                results.flush()
                print(json.dumps(dict(done=start + len(batch), total=len(frozen), counts=dict(counts))), flush=True)
        split_counts = Counter(row['split'] for row in admitted)
        complete = all(split_counts[split] for split in ('train', 'validation', 'test'))
        summary = (self.exporter.export(admitted, output / 'data') if complete else
                   dict(by_split=dict(split_counts), by_topic=dict(Counter(row['topic'] for row in admitted))))
        if complete:
            admitted_ids = {row['instance_id'] for row in admitted}
            excluded = [r['name'] for r in frozen if r['split'] != 'train' or r['instance_id'] not in admitted_ids]
            (output / 'data/excluded_from_training.txt').write_text(''.join(name + '\n' for name in sorted(excluded)))
            summary['data_sha256'] = {str(path.relative_to(output / 'data')): file_sha256(path)
                                      for path in sorted((output / 'data').rglob('*')) if path.is_file()}
        summary.update(status='exploratory_verified', config=config, counts=dict(counts), lean_version=backend.version,
                       screen_tactics=screen_tactics,
                       builder_sha256=source_hashes()['build_nt_benchmark.py'], source_sha256=source_hashes(),
                       identity_sha256=file_sha256(identity_path),
                       selection_manifest_sha256=file_sha256(package / 'manifest.json'),
                       wall_seconds=time.monotonic() - started,
                       limitations=['Topic and duplicate groups use text rules; mathematical families remain unreviewed.',
                                    'Known parent exposure uses the source corpus audit; near exposure and pretraining remain unresolved.',
                                    'Reference depth and strategies are unannotated; policy difficulty is unmeasured.',
                                    'This exploratory partition does not replace the approved EXP-024 family benchmark.'])
        summary['status'] = 'incomplete' if not complete else summary['status']
        (output / 'manifest.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary), flush=True)
        if summary['status'] == 'incomplete':
            raise RuntimeError('Benchmark has an empty split; preserve this failed build without relabeling or resampling')
