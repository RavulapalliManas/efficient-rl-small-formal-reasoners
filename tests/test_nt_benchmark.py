import hashlib
import importlib.util
import inspect
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(__file__).resolve().parents[1] / 'scripts/data/build_nt_benchmark.py'

    def module(self):
        self.assertTrue(self.path.exists(), 'The maintained benchmark builder is missing')
        spec = importlib.util.spec_from_file_location('nt_benchmark', self.path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def config(self):
        return dict(version='fixture_v1', seed=7, max_per_topic=4, max_candidates=32,
                    max_reference_chars=4096, near_duplicate_threshold=0.7,
                    dev_percent=20, test_percent=20,
                    heldout_topics=['Arithmetic functions', 'Quadratic residues'],
                    lean='v4.10.0-rc1', mathlib='pin', batch=2, timeout=30)

    def row(self, statement='(a b : ℕ) (h : a ∣ b) : a ∣ b', body='  exact h', seen=False):
        return dict(statement=statement, sources=['fixture'], seen_by_parent_lineage=seen,
                    near_syllabus=[], proofs=[dict(source='fixture', text=f'theorem original {statement} := by\n{body}')])

    def candidate(self, module, row):
        return module.candidate(row, self.config())

    def test_selector_configuration_is_independent_between_instances(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'BenchmarkSelector'), 'Expose the configurable selector object')
        ordinary = m.BenchmarkSelector(self.config())
        restricted = m.BenchmarkSelector(dict(self.config(), max_reference_chars=1))
        self.assertEqual(ordinary.candidate(self.row()), m.candidate(self.row(), self.config()))
        with self.assertRaisesRegex(ValueError, 'reference proof'):
            restricted.candidate(self.row())
        self.assertEqual(ordinary.candidate(self.row())['reference_body'], '  exact h')

    def test_prior_package_blocks_exact_numeric_and_near_duplicates(self):
        m = self.module()
        prior = self.row('(a : ℕ) : a % 3 = a % 3', '  rfl')
        self.assertIn('protected', inspect.signature(m.BenchmarkSelector).parameters)
        selector = m.BenchmarkSelector(self.config(), protected=[dict(statement=prior['statement'])])
        for statement in (prior['statement'], '(z : ℕ) : z % 7 = z % 7',
                          '(a : ℕ) : a % 3 = a % 3 + 0'):
            with self.subTest(statement=statement), self.assertRaisesRegex(ValueError, 'prior benchmark'):
                selector.candidate(self.row(statement, '  omega'))
        self.assertEqual(selector.candidate(self.row())['topic'], 'Divisibility')

    def test_statement_lists_and_source_ids_exclude_protected_training_sources(self):
        m = self.module()
        protected, relative = '(a : ℕ) : a % 3 = a % 3', '(z : ℕ) : z % 7 = z % 7'
        source, keep = '(n : ℕ) (h : 2 ∣ n) : 2 ∣ n * n', '(a b : ℕ) (h : a ∣ b) : a ∣ b'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / 'corpus.jsonl'
            corpus.write_text(''.join(json.dumps(self.row(s, '  omega')) + '\n' for s in (protected, relative, source, keep)))
            statements = root / 'protected.jsonl'
            statements.write_text(json.dumps(dict(statement=protected)) + '\n')
            ids = root / 'ids.txt'
            ids.write_text(hashlib.sha256(source.encode()).hexdigest() + '\n')
            with redirect_stdout(io.StringIO()):
                manifest = m.BenchmarkPipeline(self.config(), protected_statements=[statements],
                                               excluded_ids=[ids]).select([corpus], root / 'out', 1 << 20)
            chosen = [r['statement'] for r in m.rows(root / 'out/private/candidates.jsonl')]
        self.assertEqual(chosen, [keep])
        self.assertEqual(manifest['counts']['excluded:protected source identity'], 1)
        self.assertEqual(manifest['counts']['excluded:matches prior benchmark'], 2)
        self.assertEqual([sorted(p) for p in manifest['protected_inputs']],
                         [['rows', 'sha256', 'statements'], ['excluded_ids', 'sha256']])

    def test_protected_statement_cap_is_configurable_and_bounded(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            statements = Path(tmp) / 'protected.jsonl'
            statements.write_text(''.join(json.dumps(dict(statement=f'(a : ℕ) : a % {i} = a % {i}')) + '\n'
                                          for i in range(300)))
            with self.assertRaisesRegex(ValueError, 'exclusion cap'):
                m.BenchmarkPipeline(self.config(), protected_statements=[statements])
            m.BenchmarkPipeline(dict(self.config(), max_protected=300), protected_statements=[statements])
        for cap in (0, 4097, True):
            with self.assertRaises(ValueError):
                m.BenchmarkPipeline(dict(self.config(), max_protected=cap))

    def test_expansion_limit_is_bounded_and_does_not_change_v1_config(self):
        m = self.module()
        m.BenchmarkPipeline(dict(self.config(), max_candidates=256, max_per_topic=24))
        for changes in (dict(max_candidates=257), dict(max_per_topic=25)):
            with self.assertRaises(ValueError):
                m.BenchmarkPipeline(dict(self.config(), **changes))

    def test_pipeline_uses_supplied_selector_without_changing_artifact_contract(self):
        m = self.module()
        self.assertTrue(hasattr(m, 'BenchmarkPipeline'), 'Expose the composable pipeline object')
        class OneCandidate:
            def select(inner, paths, max_input_bytes):
                return [self.candidate(m, self.row())], {'fixture': 1}

            def partition(inner, candidates):
                return m.partition(candidates, self.config())

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'input.jsonl'
            source.write_text('{}\n')
            output = Path(tmp) / 'selection'
            pipeline = m.BenchmarkPipeline(self.config(), selector=OneCandidate())
            with redirect_stdout(io.StringIO()):
                pipeline.select([source], output, 1024)
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['counts'], {'fixture': 1})
            self.assertEqual(len(list(m.rows(output / 'private/candidates.jsonl'))), 1)
            self.assertIn('selection.py', manifest['source_sha256'])

    def test_verifier_preserves_incomplete_screens_and_refuses_exports(self):
        m = self.module()
        import nt_benchmark.verification as verification
        from nt_benchmark.storage import file_sha256

        class IncompleteBackend:
            version = 'fixture, no Lean execution'

            def __init__(self, *args):
                pass

            def check(self, path, items):
                status = 'accepted' if path.name == 'reference_batch.lean' else 'incomplete'
                return [dict(status=status) for _ in items], 0

            def goal_states(self, path, items):
                return ['⊢ fixture' for _ in items]

        with tempfile.TemporaryDirectory() as tmp:
            package, output = Path(tmp) / 'package', Path(tmp) / 'verification'
            (package / 'private').mkdir(parents=True)
            candidate = self.candidate(m, self.row())
            candidate.update(split='train', excluded_reason=None)
            candidates = package / 'private/candidates.jsonl'
            m.write_rows(candidates, [candidate])
            (package / 'manifest.json').write_text(json.dumps(dict(
                config=self.config(), candidates_sha256=file_sha256(candidates))))
            identity = Path(tmp) / 'identity.json'
            identity.write_text('{}')
            with patch.object(verification, 'LeanVerifier', IncompleteBackend), \
                    redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'empty split'):
                m.BenchmarkVerifier().verify(package, output, Path(tmp), identity)
            self.assertFalse((output / 'data').exists())
            outcomes = list(m.rows(output / 'private/verification.jsonl'))
            self.assertEqual(outcomes[0]['status'], 'screen_incomplete')
            self.assertEqual(json.loads((output / 'manifest.json').read_text())['status'], 'incomplete')

    def test_live_mathlib_revision_must_match_registered_pin(self):
        self.module()
        from nt_benchmark import verification
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment = root / 'environment'
            mathlib = environment / 'mathlib'
            mathlib.mkdir(parents=True)
            (mathlib / '.git').mkdir()
            (mathlib / 'lean-toolchain').write_text('leanprover/lean4:v4.10.0-rc1\n')
            identity = root / 'identity.json'
            identity.write_text(json.dumps(dict(clean=True, toolchain='leanprover/lean4:v4.10.0-rc1',
                repositories=[dict(name='mathlib', revision='pin')])) )
            def git_result(command, **kwargs):
                if command[1] == '--version':
                    return subprocess.CompletedProcess(command, 0, stdout='Lean version 4.10.0-rc1\n', stderr='')
                return subprocess.CompletedProcess(command, 0, stdout='different-revision\n', stderr='')
            with patch.object(verification.subprocess, 'run', side_effect=git_result), \
                    self.assertRaisesRegex(ValueError, 'Live mathlib revision'):
                verification.LeanVerifier(dict(mathlib='pin', lean='v4.10.0-rc1', timeout=30),
                                          environment, identity)

    def test_source_snapshot_without_git_metadata_cannot_claim_live_identity(self):
        self.module()
        from nt_benchmark import verification
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment = root / 'environment'
            mathlib = environment / 'mathlib'
            mathlib.mkdir(parents=True)
            (mathlib / 'lean-toolchain').write_text('leanprover/lean4:v4.10.0-rc1\n')
            identity = root / 'identity.json'
            identity.write_text(json.dumps(dict(clean=True, toolchain='leanprover/lean4:v4.10.0-rc1',
                repositories=[dict(name='mathlib', revision='pin')])) )
            def lean_version(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, stdout='Lean version 4.10.0-rc1\n', stderr='')
            with patch.object(verification.subprocess, 'run', side_effect=lean_version), \
                    self.assertRaisesRegex(ValueError, 'Cannot verify live mathlib identity'):
                verification.LeanVerifier(dict(mathlib='pin', lean='v4.10.0-rc1', timeout=30),
                                          environment, identity)

    def test_registered_snapshot_hashes_must_match_live_sources(self):
        self.module()
        from nt_benchmark import verification
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment = root / 'environment'
            mathlib = environment / 'mathlib'
            mathlib.mkdir(parents=True)
            source = mathlib / 'Fixture.lean'
            source.write_text('def fixture := 1\n')
            (mathlib / 'lean-toolchain').write_text('leanprover/lean4:v4.10.0-rc1\n')
            (mathlib / 'lake-manifest.json').write_text('{"packages": []}\n')
            blobs = {}
            for path in mathlib.iterdir():
                data = path.read_bytes()
                blobs[path.name] = hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
            identity = root / 'identity.json'
            identity.write_text(json.dumps(dict(clean=True, toolchain='leanprover/lean4:v4.10.0-rc1',
                repositories=[dict(name='mathlib', revision='pin', source_blobs=blobs)])))
            version = verification.subprocess.CompletedProcess([], 0, stdout='Lean version 4.10.0-rc1\n')
            with patch.object(verification.subprocess, 'run', return_value=version):
                backend = verification.LeanVerifier(dict(mathlib='pin', lean='v4.10.0-rc1', timeout=30),
                                                    environment, identity)
                self.assertEqual(backend.mathlib_identity_source, 'live_source_blob_hashes')
                for change in ('modified', 'missing', 'extra'):
                    with self.subTest(change=change):
                        source.write_text('def fixture := 2\n' if change == 'modified' else 'def fixture := 1\n')
                        if change == 'missing':
                            source.unlink()
                        extra = mathlib / 'Extra.lean'
                        if change == 'extra':
                            extra.write_text('def extra := 1\n')
                        with self.assertRaisesRegex(ValueError, 'Live source snapshot differs'):
                            verification.LeanVerifier(dict(mathlib='pin', lean='v4.10.0-rc1', timeout=30),
                                                      environment, identity)

    def test_renaming_and_numeric_variants_share_template_group(self):
        m = self.module()
        a = self.candidate(m, self.row('(a : ℕ) : a % 3 = a % 3', '  rfl'))
        b = self.candidate(m, self.row('(z : ℕ) : z % 7 = z % 7', '  rfl'))
        self.assertEqual(a['template_key'], b['template_key'])
        rows, _ = m.partition([a, b], self.config())
        self.assertEqual(rows[0]['split'], rows[1]['split'])
        self.assertEqual(rows[0]['family'], rows[1]['family'])

    def test_transitive_near_duplicate_component_stays_in_one_split(self):
        m = self.module()
        rows = [dict(instance_id=x, template_key=x, statement=s, topic='Divisibility',
                     seen_by_parent_lineage=False, reserved=False)
                for x, s in [('a', 'α β γ δ'), ('b', 'α β γ δ ε'), ('c', 'β γ δ ε')]]
        config = dict(self.config(), near_duplicate_threshold=0.7)
        rows, audit = m.partition(rows, config)
        self.assertEqual(len({r['family'] for r in rows}), 1)
        self.assertEqual(len({r['split'] for r in rows}), 1)
        self.assertEqual(len(audit), 2)

    def test_seen_relative_of_heldout_topic_cannot_enter_evaluation(self):
        m = self.module()
        a = self.candidate(m, self.row('(p : ℕ) : Nat.totient p = Nat.totient p', '  rfl'))
        b = dict(a, instance_id='seen', seen_by_parent_lineage=True)
        rows, _ = m.partition([a, b], self.config())
        self.assertTrue(all(r['excluded_reason'] == 'known_parent_exposure' for r in rows))

    def test_selection_is_order_independent_and_caps_topics(self):
        m = self.module()
        rows = [self.row(f'(a : ℕ) : a % {i} = a % {i}', '  rfl') for i in range(2, 10)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'corpus.jsonl'
            path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            first, _ = m.select([path], self.config(), 1 << 20)
            path.write_text(''.join(json.dumps(r) + '\n' for r in reversed(rows)))
            second, _ = m.select([path], self.config(), 1 << 20)
        self.assertEqual([r['instance_id'] for r in first], [r['instance_id'] for r in second])
        self.assertEqual(len(first), self.config()['max_per_topic'])

    def test_alpha_duplicates_merge_exposure_before_partition(self):
        m = self.module()
        inputs = [self.row('(p : ℕ) : Nat.totient p = Nat.totient p', '  rfl'),
                  self.row('(q : ℕ) : Nat.totient q = Nat.totient q', '  rfl', seen=True)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'corpus.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in inputs))
            chosen, counts = m.select([path], self.config(), 1 << 20)
        self.assertEqual(len(chosen), 1)
        self.assertTrue(chosen[0]['seen_by_parent_lineage'])
        self.assertEqual(counts['merged_canonical_duplicate'], 1)
        assigned, _ = m.partition(chosen, self.config())
        self.assertEqual(assigned[0]['excluded_reason'], 'known_parent_exposure')

    def test_filtered_reference_still_taints_a_duplicate_with_parent_exposure(self):
        m = self.module()
        inputs = [self.row('(p : ℕ) : Nat.totient p = Nat.totient p', '  rfl'),
                  self.row('(q : ℕ) : Nat.totient q = Nat.totient q', '  exact?', seen=True)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'corpus.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in inputs))
            chosen, _ = m.select([path], self.config(), 1 << 20)
        self.assertTrue(chosen[0]['seen_by_parent_lineage'])

    def test_filtered_numeric_relative_taints_the_retained_template(self):
        m = self.module()
        inputs = [self.row('(p : ℕ) : Nat.totient (p + 3) = Nat.totient (p + 3)', '  rfl'),
                  self.row('(q : ℕ) : Nat.totient (q + 7) = Nat.totient (q + 7)', '  exact?', seen=True)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'corpus.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in inputs))
            chosen, _ = m.select([path], self.config(), 1 << 20)
        assigned, _ = m.partition(chosen, self.config())
        self.assertEqual(assigned[0]['excluded_reason'], 'known_parent_exposure')

    def test_bad_proofs_and_abstract_groups_are_rejected(self):
        m = self.module()
        for row in [self.row(body='  sorry'), self.row(body='  exact?'),
                    self.row(body='  exact h\naxiom injected : False'),
                    self.row('{G : Type*} [Group G] (a : G) : orderOf a ∣ orderOf a', '  exact dvd_refl _')]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.candidate(m, row)

    def test_leading_comments_do_not_change_reference_step_counts(self):
        m = self.module()
        body = '  /- leading explanation -/\n  have h := Nat.dvd_refl a\n  exact h'
        row = self.candidate(m, self.row('(a : ℕ) : a ∣ a', body))
        self.assertEqual(row['reference_lines'], 2)
        self.assertEqual(row['reference_blocks'], 2)

    def test_outputs_do_not_leak_references_or_relabel_evaluation(self):
        m = self.module()
        rows = []
        for split in ['train', 'validation', 'test']:
            r = self.candidate(m, self.row())
            r.update(instance_id=split, name='nt_' + split, task_id=split + ':whole:0',
                     header='theorem nt_' + split + ' ' + r['statement'], split=split,
                     family='template_' + split, state='a b : ℕ\nh : a ∣ b\n⊢ a ∣ b',
                     reference_status='accepted', screen_status='not_solved',
                     reference_body='  exact PRIVATE_' + split)
            rows.append(r)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'data'
            m.export(rows, out, self.config())
            pool = [json.loads(line) for line in (out / 'pool.jsonl').read_text().splitlines()]
            self.assertEqual({r['split'] for r in pool}, {'train', 'validation', 'test'})
            self.assertTrue((out / 'sft/sft_train.jsonl').is_file(), 'run_sft.py requires sft_train.jsonl')
            self.assertTrue((out / 'sft/sft_val.jsonl').is_file(), 'run_sft.py requires sft_val.jsonl')
            sft = (out / 'sft/sft_train.jsonl').read_text() + (out / 'sft/sft_val.jsonl').read_text()
            self.assertIn('PRIVATE_train', sft)
            self.assertNotIn('PRIVATE_validation', sft)
            self.assertNotIn('PRIVATE_test', sft)
            for path in [out / 'pool.jsonl', *out.glob('tasks_*.jsonl')]:
                self.assertNotIn('PRIVATE_', path.read_text())
            self.assertEqual(set((out / 'excluded_from_training.txt').read_text().split()),
                             {'nt_validation', 'nt_test'})

    def test_evaluation_description_preserves_questions_and_separates_answers(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            package, output = Path(tmp) / 'package', Path(tmp) / 'review'
            (package / 'data/private').mkdir(parents=True)
            (package / 'manifest.json').write_text(json.dumps(dict(
                status='exploratory_verified', config=self.config())))
            references = []
            for split, label in [('validation', 'dev'), ('test', 'test')]:
                task = dict(task_id=label + ':whole:0', name=label, split=split,
                            header=f'theorem {label} (a : ℕ) : a = a',
                            state='a : ℕ\n⊢ a = a', kind='whole', given='', family=label)
                m.write_rows(package / f'data/tasks_{label}.jsonl', [task])
                references.append(dict(task, reference_body='  rfl -- PRIVATE_' + label,
                                       reference_status='accepted', screen_status='not_solved'))
            m.write_rows(package / 'data/private/references.jsonl', references)
            result = m.BenchmarkExporter().describe(package, output)
            self.assertEqual(result['questions'], {'dev': 1, 'test': 1})
            self.assertEqual(result['status'], 'semantic_review_required')
            for label in ('dev', 'test'):
                raw = (output / f'questions_{label}.jsonl').read_text()
                self.assertNotIn('PRIVATE_', raw)
                row = json.loads(raw)
                self.assertEqual(row['header'], f'theorem {label} (a : ℕ) : a = a')
                self.assertEqual(row['semantic_review'], 'unreviewed')
                self.assertEqual(row['expected_response'], 'lean_tactic_block')
            answers = list(m.rows(output / 'private/expected_answers.jsonl'))
            self.assertEqual({row['task_id'] for row in answers},
                             {'dev:whole:0', 'test:whole:0'})
            self.assertIn('PRIVATE_dev', answers[0]['reference_body'])
            contract = json.loads((output / 'answer_contract.json').read_text())
            self.assertFalse(contract['reference_exact_match_required'])
            self.assertIsNone(contract['assessment_limits'])

    def test_evaluation_description_rejects_reference_or_split_mismatch(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / 'package'
            (package / 'data/private').mkdir(parents=True)
            (package / 'manifest.json').write_text(json.dumps(dict(
                status='exploratory_verified', config=self.config())))
            task = dict(task_id='dev:whole:0', name='dev', split='validation',
                        header='theorem dev : True', state='⊢ True', kind='whole', given='')
            m.write_rows(package / 'data/tasks_dev.jsonl', [task])
            m.write_rows(package / 'data/tasks_test.jsonl', [])
            reference = dict(task, header='theorem changed : True', reference_body='  trivial',
                             reference_status='accepted', screen_status='not_solved')
            path = package / 'data/private/references.jsonl'
            m.write_rows(path, [reference])
            output = Path(tmp) / 'bad_reference'
            with self.assertRaisesRegex(ValueError, 'reference'):
                m.BenchmarkExporter().describe(package, output)
            self.assertFalse(output.exists())
            reference['header'] = task['header']
            path.write_text(json.dumps(reference) + '\n')
            tasks = package / 'data/tasks_dev.jsonl'
            tasks.write_text(json.dumps(dict(task, split='train')) + '\n')
            with self.assertRaisesRegex(ValueError, 'split'):
                m.BenchmarkExporter().describe(package, Path(tmp) / 'bad_split')

    def test_incomplete_or_failed_verification_cannot_export(self):
        m = self.module()
        base = self.candidate(m, self.row())
        base.update(split='train', family='template_a', state='⊢ True')
        with tempfile.TemporaryDirectory() as tmp:
            for index, changes in enumerate([{}, dict(reference_status='rejected', screen_status='not_solved'),
                                             dict(reference_status='accepted', screen_status='incomplete')]):
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'verification'):
                    m.export([dict(base, **changes)], Path(tmp) / str(index), self.config())


if __name__ == '__main__':
    unittest.main()
