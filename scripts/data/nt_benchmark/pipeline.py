"""Compose replaceable selection, verification, and export components."""
from collections import Counter
import json

from verify_external_proofs import HEADER
from .selection import BenchmarkSelector
from .storage import file_sha256, rows, source_hashes, write_rows
from .verification import BenchmarkVerifier, SCREEN_TACTICS


class BenchmarkPipeline:
    """Coordinate artifacts while component objects own their individual policies."""

    def __init__(self, config, selector=None, verifier=None, protected_packages=(), protected_statements=(),
                 excluded_ids=()):
        cap = config.get('max_protected', 256)
        if (not 1 <= config['max_candidates'] <= 256 or not 1 <= config['max_per_topic'] <= 24
                or not 0 < config['near_duplicate_threshold'] <= 1
                or not 0 < config['dev_percent'] + config['test_percent'] < 100
                or min(config['dev_percent'], config['test_percent'], config['batch'], config['timeout']) <= 0
                or isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 4096):
            raise ValueError('Invalid selection limits or split configuration')
        self.config = dict(config)
        protected, self.protected_inputs = [], []
        if (protected_packages or protected_statements or excluded_ids) and selector is not None:
            raise ValueError('Prior-package exclusions require the default selector')
        for package in protected_packages:
            manifest = json.loads((package / 'manifest.json').read_text())
            candidates = package / 'private/candidates.jsonl'
            if file_sha256(candidates) != manifest['candidates_sha256']:
                raise ValueError('Prior benchmark candidate file changed')
            for row in rows(candidates):
                protected.append(dict(statement=row['statement']))
                if len(protected) > cap:
                    raise ValueError('Prior benchmark exclusion cap exceeded')
            self.protected_inputs.append(dict(package=package.name, version=manifest['config']['version'],
                manifest_sha256=file_sha256(package / 'manifest.json'),
                candidates_sha256=manifest['candidates_sha256']))
        # Statement lists protect sources that are not benchmark packages, such as curriculum TRAIN roots.
        for path in protected_statements:
            count = 0
            for row in rows(path):
                protected.append(dict(statement=row['statement']))
                count += 1
                if len(protected) > cap:
                    raise ValueError('Prior benchmark exclusion cap exceeded')
            self.protected_inputs.append(dict(statements=path.name, sha256=file_sha256(path), rows=count))
        ids = set()
        for path in excluded_ids:
            ids |= {line.strip() for line in path.read_text().splitlines() if line.strip()}
            self.protected_inputs.append(dict(excluded_ids=path.name, sha256=file_sha256(path)))
        self.selector = selector if selector is not None else BenchmarkSelector(
            self.config, protected=protected, excluded_ids=ids)
        self.verifier = verifier if verifier is not None else BenchmarkVerifier()

    def select(self, paths, output, max_input_bytes=512 * 1024**2):
        config = self.config
        if sum(path.stat().st_size for path in paths) > max_input_bytes:
            raise ValueError('Input bytes exceed the configured cap')
        inputs = []
        for index, path in enumerate(paths):
            receipt = dict(index=index, name=path.name, bytes=path.stat().st_size, sha256=file_sha256(path))
            manifest_path = path.parent / 'manifest.json'
            if path.name == 'corpus.jsonl' and manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text())
                if 'corpus_sha256' in manifest:
                    if manifest['status'] != 'candidates_not_verified' or manifest['corpus_sha256'] != receipt['sha256']:
                        raise ValueError('Corpus differs from its completed build manifest')
                    receipt['manifest_sha256'] = file_sha256(manifest_path)
            inputs.append(receipt)
        candidates, counts = self.selector.select(paths, max_input_bytes)
        candidates, audit = self.selector.partition(candidates)
        output.mkdir(parents=True, exist_ok=False)
        (output / 'private').mkdir()
        write_rows(output / 'private/candidates.jsonl', candidates)
        write_rows(output / 'duplicate_audit.jsonl', audit)
        manifest = dict(status='frozen_pending_verification', config=config, inputs=inputs, counts=counts,
                        protected_inputs=self.protected_inputs,
                        selected=len(candidates), by_topic=dict(Counter(r['topic'] for r in candidates)),
                        by_split=dict(Counter(r['split'] for r in candidates)),
                        candidates_sha256=file_sha256(output / 'private/candidates.jsonl'),
                        grouping='binder-normalized numeric templates and transitive token-Jaccard components',
                        imports=HEADER, screen_tactics=SCREEN_TACTICS,
                        builder_sha256=source_hashes()['build_nt_benchmark.py'], source_sha256=source_hashes())
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps(manifest), flush=True)
        return manifest

    def verify(self, package, output, environment, identity_path):
        return self.verifier.verify(package, output, environment, identity_path)
