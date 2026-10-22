"""Small synthetic rebuilds; no public corpus, Lean or model is downloaded."""
from collections import Counter
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/data'))
from nt_benchmark import BenchmarkPipeline, BenchmarkVerifier
from nt_benchmark.corpus import build_corpus
from nt_benchmark.identity import check_repositories, record_identity
from nt_benchmark.schedules import build_orders, prerequisite_order
from nt_benchmark.sources import checked_source_files, fetch_sources, registry_digest, source_files
from nt_benchmark.storage import digest, file_sha256, rows, write_rows
from nt_benchmark.verification import SCREEN_TACTICS


class DatasetRebuildTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='formal-rl-dataset-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.statement = '(a b : ℕ) (h : a ∣ b) : a ∣ b'
        self.workbook = [dict(formal_statement='theorem fixture ' + self.statement + ' := by sorry',
                              proof=['exact h', 'exact h', 'sorry'])]
        self.config = dict(version='fixture', seed=7, max_per_topic=4, max_candidates=32,
                           max_reference_chars=4096, near_duplicate_threshold=0.7,
                           dev_percent=20, test_percent=20, heldout_topics=[],
                           lean='v4.10.0-rc1', mathlib='pin', batch=2, timeout=30)

    def registry(self, payload):
        return dict(sources=dict(lean_workbook=dict(repo='fixture/data', revision='a' * 40,
            files=[dict(path='workbook.json', bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())])))

    def raw(self, workbook=None, name='raw'):
        payload = json.dumps(self.workbook if workbook is None else workbook, ensure_ascii=False).encode()
        registry = self.registry(payload)
        with patch('nt_benchmark.sources.urlopen', return_value=io.BytesIO(payload)):
            fetch_sources(registry, ['lean_workbook'], self.root / name, len(payload))
        return self.root / name, registry

    def test_fetch_checks_size_and_hash_and_does_not_overwrite(self):
        raw, registry = self.raw()
        self.assertEqual(len(checked_source_files(raw, registry)), 1)
        with self.assertRaises(FileExistsError):
            fetch_sources(registry, ['lean_workbook'], raw, 10000)
        (raw / 'lean_workbook/workbook.json').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            checked_source_files(raw, registry)

    def test_fetch_refuses_moving_revisions_path_escape_and_oversize_before_writes(self):
        registry = self.registry(b'fixture')
        for change in ('revision', 'path', 'size'):
            modified = copy.deepcopy(registry)
            source = modified['sources']['lean_workbook']
            if change == 'revision':
                source['revision'] = 'main'
            elif change == 'path':
                source['files'][0]['path'] = '../escape'
            else:
                source['files'][0]['bytes'] = 1000000
            output = self.root / change
            with self.subTest(change=change), self.assertRaises(ValueError):
                fetch_sources(modified, ['lean_workbook'], output, 100)
            self.assertFalse(output.exists())

    def test_failed_download_keeps_failure_receipt_and_partial_bytes(self):
        payload = b'wrong bytes'
        registry = self.registry(b'expected')
        output = self.root / 'failed'
        with patch('nt_benchmark.sources.urlopen', return_value=io.BytesIO(payload)), self.assertRaises(ValueError):
            fetch_sources(registry, ['lean_workbook'], output, 100)
        self.assertEqual(json.loads((output / 'manifest.json').read_text())['status'], 'failed')
        self.assertTrue((output / 'lean_workbook/workbook.json.partial').exists())

    def test_corpus_is_order_independent_and_keeps_bindings_and_proof_status(self):
        renamed = dict(formal_statement='theorem other (x y : ℕ) (q : x ∣ y) : x ∣ y := by sorry', proof=['exact q'])
        hashes = []
        for index, workbook in enumerate((self.workbook + [renamed], [renamed] + self.workbook)):
            raw, registry = self.raw(workbook, f'raw{index}')
            output = self.root / f'corpus{index}'
            manifest = build_corpus(raw, registry, output, unchanged_base=True)
            self.assertEqual(manifest['status'], 'candidates_not_verified')
            self.assertEqual(manifest['counts']['statements'], 2)
            self.assertEqual(manifest['counts']['proofs_retained'], 2)
            hashes.append(manifest['corpus_sha256'])
            records = list(rows(output / 'corpus.jsonl'))
            self.assertEqual(len({r['canonical_statement_sha256'] for r in records}), 1)
            self.assertTrue(all(r['reference_status'] == 'not_lean_verified' for r in records))
            for record in records:
                for proof in record['proofs']:
                    self.assertIn(record['statement'], proof['text'])
            with redirect_stdout(io.StringIO()):
                selection = BenchmarkPipeline(self.config).select([output / 'corpus.jsonl'], self.root / f'selection{index}')
            self.assertEqual(selection['selected'], 1)
        self.assertEqual(hashes[0], hashes[1])

    def test_exposure_is_explicit_complete_and_not_pretraining_clearance(self):
        raw, registry = self.raw()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            build_corpus(raw, registry, self.root / 'no_lineage')
        audit = self.root / 'audit.json'
        receipt = dict(parent_checkpoint='fixture-parent', audit_description='fixture only', statements={})
        audit.write_text(json.dumps(receipt))
        output = self.root / 'missing_audit'
        with self.assertRaisesRegex(ValueError, 'omits'):
            build_corpus(raw, registry, output, exposure_path=audit)
        self.assertEqual(json.loads((output / 'manifest.json').read_text())['status'], 'failed')
        from build_nt_pool import key
        receipt['statements'][digest(key(self.statement))] = True
        audit.write_text(json.dumps(receipt))
        output = self.root / 'audited'
        result = build_corpus(raw, registry, output, exposure_path=audit)
        self.assertTrue(next(rows(output / 'corpus.jsonl'))['seen_by_parent_lineage'])
        self.assertEqual(result['pretraining_exposure'], 'unknown')

    def test_selection_binds_the_corpus_manifest_and_rejects_edits(self):
        raw, registry = self.raw()
        output = self.root / 'corpus'
        build_corpus(raw, registry, output, unchanged_base=True)
        with patch('nt_benchmark.pipeline.file_sha256', side_effect=AssertionError('must not read oversized inputs')), \
                self.assertRaisesRegex(ValueError, 'Input bytes'):
            BenchmarkPipeline(self.config).select([output / 'corpus.jsonl'], self.root / 'oversize', 1)
        with redirect_stdout(io.StringIO()):
            manifest = BenchmarkPipeline(self.config).select([output / 'corpus.jsonl'], self.root / 'selection')
        self.assertEqual(manifest['inputs'][0]['manifest_sha256'], file_sha256(output / 'manifest.json'))
        with (output / 'corpus.jsonl').open('a') as stream:
            stream.write('{}\n')
        with self.assertRaisesRegex(ValueError, 'Corpus differs'):
            BenchmarkPipeline(self.config).select([output / 'corpus.jsonl'], self.root / 'bad_selection')

    def test_source_registry_has_full_pins_and_historical_scope(self):
        path = Path(__file__).resolve().parents[1] / 'configs/data/nt_sources_v1.json'
        registry = json.loads(path.read_text())
        entries = source_files(registry, registry['sources'])
        self.assertEqual(len(entries), 25)
        self.assertEqual(len({entry['source'] for entry in entries}), 5)
        self.assertIn('not a reconstruction', registry['purpose'])
        self.assertEqual(registry_digest(registry), registry_digest(copy.deepcopy(registry)))

    def test_parquet_adapters_feed_the_corpus_with_source_identities(self):
        try:
            import pyarrow as arrow
            import pyarrow.parquet as parquet
        except ImportError:
            self.skipTest('Install the data group to check Parquet adapters.')
        statement = 'theorem fixture ' + self.statement + ' := by sorry'
        preamble = 'import Mathlib\nopen BigOperators Real Nat Topology Rat\n'
        proof = 'theorem fixture ' + self.statement + ' := by\n  exact h'
        fixtures = {
            'goedel_workbook': dict(problem_id='fixture', full_proof=preamble + proof),
            'goedel_pset_v1': dict(problem_id='fixture', formal_statement=preamble + proof, informal_statement='fixture'),
            'goedel_v2_sft': dict(messages=[dict(role='user', content='```lean\n' + preamble + statement + '\n```'),
                                          dict(role='assistant', content='```lean\n' + proof + '\n```')]),
            'stp': dict(prompt='```lean4\n' + preamble + proof.split(':= by')[0] + ':= by', target='\n  exact h', tag='fixture'),
        }
        raw, registry = self.raw()
        for source, record in fixtures.items():
            directory = raw / source
            directory.mkdir()
            path = directory / 'part.parquet'
            parquet.write_table(arrow.Table.from_pylist([record]), path)
            registry['sources'][source] = dict(repo='fixture/' + source, revision='b' * 40,
                files=[dict(path=path.name, bytes=path.stat().st_size, sha256=file_sha256(path))])
        (raw / 'manifest.json').write_text(json.dumps(dict(status='downloaded',
            registry_sha256=registry_digest(registry), files=source_files(registry, registry['sources']))))
        output = self.root / 'all_sources'
        result = build_corpus(raw, registry, output, unchanged_base=True)
        record = next(rows(output / 'corpus.jsonl'))
        self.assertEqual(set(record['sources']), set(registry['sources']))
        self.assertEqual(result['counts']['statements'], 1)

    def test_strict_screen_uses_allowed_tactics_and_completes_all_splits(self):
        from probe_start_depth import FORBIDDEN
        self.assertNotIn('exact?', SCREEN_TACTICS)
        self.assertTrue(all(not FORBIDDEN.search(tactic) for tactic in SCREEN_TACTICS))
        package = self.root / 'selection'
        (package / 'private').mkdir(parents=True)
        selector = BenchmarkPipeline(self.config).selector
        candidates = []
        for split in ('train', 'validation', 'test'):
            record = selector.candidate(dict(statement=self.statement, sources=['fixture'], near_syllabus=[],
                seen_by_parent_lineage=False, proofs=[dict(source='fixture', text='theorem x ' + self.statement + ' := by\n  exact h')]))
            record.update(instance_id=split, name=split, task_id=split + ':whole:0', family=split,
                          header='theorem ' + split + ' ' + self.statement, split=split, excluded_reason=None)
            candidates.append(record)
        write_rows(package / 'private/candidates.jsonl', candidates)
        frozen = dict(config=self.config, candidates_sha256=file_sha256(package / 'private/candidates.jsonl'),
                      screen_tactics=SCREEN_TACTICS)
        (package / 'manifest.json').write_text(json.dumps(frozen))
        identity = self.root / 'identity.json'
        identity.write_text('{}')

        class Backend:
            version = 'synthetic; no Lean execution'
            def __init__(self, *args):
                pass
            def check(self, path, items):
                verdict = 'accepted' if path.name == 'reference_batch.lean' else 'rejected'
                return [dict(status=verdict) for _ in items], 0
            def goal_states(self, path, items):
                return ['⊢ fixture' for _ in items]

        output = self.root / 'verified'
        with patch('nt_benchmark.verification.LeanVerifier', Backend), redirect_stdout(io.StringIO()):
            BenchmarkVerifier().verify(package, output, self.root, identity)
        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'exploratory_verified')
        self.assertEqual(manifest['data_sha256']['tasks_train.jsonl'], file_sha256(output / 'data/tasks_train.jsonl'))
        frozen['screen_tactics'] = ['exact?']
        (package / 'manifest.json').write_text(json.dumps(frozen))
        with self.assertRaisesRegex(ValueError, 'unsupported screen'):
            BenchmarkVerifier().verify(package, self.root / 'bad_screen', self.root, identity)

    def test_prerequisites_reject_cycles_unknown_nodes_and_missing_annotations(self):
        for parents in ({'a': ['b'], 'b': ['a']}, {'a': ['unknown'], 'b': []}, {'a': []},
                        {'a': [[]], 'b': []}):
            with self.subTest(parents=parents), self.assertRaises(ValueError):
                prerequisite_order(['a', 'b'], dict(prerequisites=parents), 7)
        order = prerequisite_order(['a', 'b', 'c'], dict(prerequisites={'a': [], 'b': ['a'], 'c': ['b']}), 7)
        self.assertEqual(order, ['a', 'b', 'c'])

    def test_orders_preserve_training_multiplicities_and_source_hash(self):
        package = self.root / 'verified'
        (package / 'data').mkdir(parents=True)
        tasks = [dict(task_id=name, split='train', kind='whole', given='') for name in ('a', 'b', 'c')]
        path = package / 'data/tasks_train.jsonl'
        write_rows(path, tasks)
        (package / 'manifest.json').write_text(json.dumps(dict(status='exploratory_verified',
            data_sha256={'tasks_train.jsonl': file_sha256(path)})))
        annotation = self.root / 'prerequisites.json'
        annotation.write_text(json.dumps(dict(reviewer='fixture', review_status='unreviewed',
            prerequisites={'a': [], 'b': ['a'], 'c': ['b']})))
        output = self.root / 'orders'
        result = build_orders(package, output, 7, 2, annotation)
        left = json.loads((output / 'order_R_runtime.json').read_text())
        right = json.loads((output / 'order_C_AI_runtime.json').read_text())
        self.assertEqual(Counter(left), Counter(right))
        self.assertEqual(right, ['a', 'b', 'c'] * 2)
        self.assertFalse(result['historical_equivalence'])
        path.write_text(json.dumps(dict(tasks[0], split='test')) + '\n')
        with self.assertRaisesRegex(ValueError, 'frozen manifest'):
            build_orders(package, self.root / 'changed_orders', 7, 2)

    def test_identity_checks_every_dependency_and_preserves_the_live_lean_pin(self):
        environment = self.root / 'environment'
        mathlib = environment / 'mathlib'
        mathlib.mkdir(parents=True)
        (mathlib / 'lake-manifest.json').write_text(json.dumps(dict(packages=[dict(name='fixture', type='git', rev='dep-pin')])))
        (mathlib / 'lean-toolchain').write_text('leanprover/lean4:v4.10.0-rc1\n')
        revisions = lambda path: 'pin' if path.name == 'mathlib' else 'dep-pin'
        with patch('nt_benchmark.identity.git_revision', side_effect=revisions):
            self.assertEqual(len(check_repositories(environment, self.config)), 2)
        with patch('nt_benchmark.identity.git_revision', return_value='pin'), self.assertRaisesRegex(ValueError, 'fixture'):
            check_repositories(environment, self.config)
        lean = self.root / 'lean'
        lean.write_bytes(b'fixture binary')
        backend = type('Backend', (), {'version': 'fixture', 'lean': lean})()
        with patch('nt_benchmark.identity.git_revision', side_effect=revisions), \
                patch('nt_benchmark.verification.LeanVerifier', return_value=backend):
            result = record_identity(environment, self.config, self.root / 'identity')
        self.assertEqual(result['status'], 'source_identity_checked')
        self.assertEqual(result['lean_binary_sha256'], file_sha256(lean))


if __name__ == '__main__':
    unittest.main()
