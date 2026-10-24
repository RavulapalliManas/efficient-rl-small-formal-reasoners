import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import threading
from unittest.mock import patch


class ExpansionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'scripts/data/prepare_nt_tactic_expansion.py'
        cls.module = None
        if path.exists():
            spec = importlib.util.spec_from_file_location('nt_expansion', path)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.assertIsNotNone(self.module, 'The broader training selector is missing')
        self.config = dict(train_names=['p'], dev_names=['q'], max_reference_blocks=12,
                           max_reference_chars=4096, max_prefix_blocks=3)
        self.pool = [dict(name='p', split='train', formal_statement='theorem p (a : Nat) : a = a'),
                     dict(name='q', split='train', formal_statement='theorem q (b : Bool) : b = b')]
        self.tasks = []
        for row, state in zip(self.pool, ['a : Nat ⊢ a = a', 'b : Bool ⊢ b = b']):
            for kind, given in [('whole', ''), ('prefix_top', '  skip\n')]:
                self.tasks.append(dict(task_id=row['name'] + ':' + kind, name=row['name'],
                    kind=kind, header=row['formal_statement'],
                    state=state if not given else state.replace('= a', '= a + 0').replace('= b', '= !b'), given=given,
                    lines_remaining=1 if given else None))
        self.proofs = [dict(name=n, source='lean_workbook', status='accepted', selected=True,
                            body='  skip\n  exact rfl') for n in ['p', 'q']]
        self.eval_states = [dict(name='old', state='z : Int ⊢ z < 0')]
        self.eval_pool = [dict(name='old', formal_statement='theorem old (z : Int) : z < 0')]

    def select(self, **changes):
        args = dict(tasks=self.tasks, pool=self.pool, proofs=self.proofs, excluded=set(),
                    eval_states=self.eval_states, eval_pool=self.eval_pool, config=self.config)
        args.update(changes)
        return self.module.select(**args)

    def test_available_cuts_keep_parent_role_without_four_depths(self):
        rows, rejected = self.select()
        self.assertEqual([(r['name'], r['role'], r['kind']) for r in rows],
                         [('p', 'train', 'whole'), ('p', 'train', 'prefix_top'),
                          ('q', 'dev', 'whole')])
        self.assertTrue(any(r['reason'] == 'dev_prefix_not_exported' for r in rejected))

    def test_explicit_vacuous_exclusion_cannot_reenter_training(self):
        with self.assertRaisesRegex(ValueError, 'excluded TRAIN'):
            self.select(config=dict(self.config, excluded_train_names=['p']))

    def test_structural_primers_are_train_only_and_disclose_supplied_answers(self):
        rows, _ = self.select(config=dict(self.config, structural_primers=True))
        primers = [r for r in rows if r.get('answer_supplied')]
        self.assertEqual(len(primers), 1)
        primer = primers[0]
        self.assertEqual(primer['name'], 'p')
        self.assertEqual(primer['curriculum_stage'], 'structure')
        self.assertIn('have curriculum_fact : a = a := by', primer['given'])
        self.assertEqual(primer['reference_rest'], '  exact curriculum_fact')
        self.assertEqual(primer['covered_operations'], ['exact'])
        self.assertTrue(primer['requires_state_reconstruction'])

    def test_unknown_or_missing_accepted_reference_fails(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.select(proofs=self.proofs[:1])
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.select(config=dict(self.config, train_names=['unknown']))

    def test_old_evaluation_name_and_header_are_excluded(self):
        with self.assertRaisesRegex(ValueError, 'excluded'):
            self.select(excluded={'p'})
        rows, rejected = self.select(eval_pool=self.eval_pool + [dict(name='renamed',
            formal_statement='theorem renamed (a : Nat) : a = a')])
        self.assertFalse(any(r['name'] == 'p' for r in rows))
        self.assertTrue(any(r['reason'] == 'near_duplicate_eval' for r in rejected))

    def test_state_duplicate_is_excluded(self):
        rows, rejected = self.select(eval_states=self.eval_states + [dict(name='renamed',
            state=self.tasks[0]['state'])])
        self.assertFalse(any(r['name'] == 'p' for r in rows))
        self.assertTrue(any(r['reason'] == 'near_duplicate_eval' for r in rejected))

    def test_header_in_protected_state_rows_is_excluded(self):
        states = self.eval_states + [dict(name='renamed', state='other goal',
            header='theorem renamed (a : Nat) : a = a')]
        rows, rejected = self.select(eval_states=states)
        self.assertFalse(any(r['name'] == 'p' for r in rows))
        self.assertTrue(any(r['reason'] == 'near_duplicate_eval' for r in rejected))

    def test_reference_does_not_enter_public_policy_row(self):
        rows, _ = self.select()
        for row in rows:
            public = self.module.policy_row(row)
            self.assertNotIn('reference_rest', public)
            self.assertNotIn('exact rfl', repr(public))

    def test_duplicate_prefix_state_preserves_whole_and_records_rejection(self):
        tasks = [dict(t) for t in self.tasks]
        tasks[1]['state'] = tasks[0]['state']
        rows, rejected = self.select(tasks=tasks)
        self.assertEqual([r['task_id'] for r in rows if r['role'] == 'train'], ['p:whole'])
        duplicate = next(r for r in rejected if r['task_id'] == 'p:prefix_top')
        self.assertEqual(duplicate['reason'], 'duplicate_train_state')
        self.assertEqual(duplicate['duplicate_of'], 'p:whole')

    def test_duplicate_whole_train_states_fail_without_dropping_a_root(self):
        pool = self.pool + [dict(name='r', split='train', formal_statement='theorem r (c : Int) : c < 0')]
        tasks = [dict(t) for t in self.tasks]
        tasks[2]['state'] = tasks[0]['state']
        tasks.append(dict(task_id='r:whole', name='r', kind='whole', header=pool[-1]['formal_statement'],
                          given='', state='c : Int ⊢ c < 0', lines_remaining=None))
        proofs = self.proofs + [dict(self.proofs[0], name='r')]
        with self.assertRaisesRegex(ValueError, 'whole TRAIN state'):
            self.select(tasks=tasks, pool=pool, proofs=proofs,
                        config=dict(self.config, train_names=['p', 'q'], dev_names=['r']))

    def test_duplicate_roles_and_non_reference_prefix_fail(self):
        with self.assertRaisesRegex(ValueError, 'disjoint'):
            self.select(config=dict(self.config, dev_names=['p']))
        tasks = [dict(t) for t in self.tasks]
        tasks[1]['given'] = '  intros\n'
        with self.assertRaisesRegex(ValueError, 'prefix'):
            self.select(tasks=tasks)

    def test_reserved_conjunction_template_remains_excluded(self):
        pool = [dict(r) for r in self.pool]
        pool[0]['formal_statement'] = 'theorem p (a : Nat) : a ∣ a ∧ a ∣ a'
        tasks = [dict(t, header=pool[0]['formal_statement']) if t['name'] == 'p' else t
                 for t in self.tasks]
        rows, rejected = self.select(tasks=tasks, pool=pool)
        self.assertFalse(any(r['name'] == 'p' for r in rows))
        self.assertTrue(any(r['reason'] == 'reserved_template' for r in rejected))

    def test_public_outputs_use_replayed_state_and_keep_dev_references_private(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = {'tasks': self.tasks, 'pool': self.pool, 'proofs': self.proofs,
                        'eval-states': self.eval_states, 'eval-pool': self.eval_pool}
            for name, rows in fixtures.items():
                (root / name).write_text(''.join(json.dumps(r) + '\n' for r in rows))
            (root / 'exclude-names').write_text('old\n')
            (root / 'config').write_text(json.dumps(self.config))
            (root / 'environment/mathlib').mkdir(parents=True)
            (root / 'environment/mathlib/lean-toolchain').write_text('leanprover/lean4:v4.10.0-rc1')
            (root / 'mathlib-identity').write_text(json.dumps(dict(
                mathlib_revision='29dcec074de168ac2bf835a77ef68bbe069194c5', mismatches=[], source='fixture')))
            names = list(fixtures) + ['exclude-names', 'config', 'environment', 'mathlib-identity',
                                     'checker', 'repl', 'tokenizer', 'output']
            argv = ['prepare'] + [v for n in names for v in ['--' + n, str(root / n)]]
            class Tokenizer:
                def apply_chat_template(self, *args, **kwargs):
                    return 'prompt'
                def __call__(self, *args, **kwargs):
                    return {'input_ids': [1]}
            transformers = SimpleNamespace(AutoTokenizer=SimpleNamespace(
                from_pretrained=lambda *a, **kw: Tokenizer()))
            from rl_components.environment import anonymous_header
            states = {(anonymous_header(t['header'])[1], t['given']): t['state'] for t in self.tasks}
            ctx = SimpleNamespace(pool_repls=set(), local=threading.local(), setup_timeout=120,
                                  timeout=20, checker=SimpleNamespace(guard=lambda name: ''))
            active = set()
            class Repl:
                def __init__(self, *_):
                    if active:
                        raise AssertionError('Previous Lean process is still active')
                    active.add(self)
                    self.proc = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO())
                def send(self, request, timeout):
                    if 'env' not in request:
                        return {'env': 1}, 0., 0.
                    command = request['cmd']
                    header, body = command.split(' := by\n', 1)
                    given = body.removesuffix('  sorry\n')
                    return {'env': 2, 'sorries': [{'proofState': 3,
                        'goal': states[header, given].replace(' ', '  ')}]}, 0., 0.
                def close(self):
                    active.discard(self)
            def verify(context, unit, refs):
                self.assertFalse(active, 'Episode must close before reference replay')
                pooled = Repl()
                context.pool_repls.add(pooled)
                context.local.repl = pooled
                return [{'status': 'accepted'}]
            from rl_components import environment as rl
            with patch('sys.argv', argv), patch.dict('sys.modules', transformers=transformers), \
                    patch.object(self.module, 'load_checker'), \
                    patch.object(self.module, 'repl_context', return_value=ctx), \
                    patch.object(rl, 'Repl', Repl), \
                    patch.object(self.module, 'verify_group', side_effect=verify):
                self.module.main()
            self.assertFalse(active)
            self.assertFalse(ctx.local.__dict__)
            train = [json.loads(l) for l in (root / 'output/train_tasks.jsonl').read_text().splitlines()]
            self.assertEqual(train[0]['state'], self.tasks[0]['state'].replace(' ', '  '))
            for name in ['train_tasks.jsonl', 'dev_tasks.jsonl', 'train_pool.jsonl', 'dev_pool.jsonl']:
                self.assertNotIn('exact rfl', (root / 'output' / name).read_text())
            references = [json.loads(l) for l in (root / 'output/private/references.jsonl').read_text().splitlines()]
            self.assertEqual({r['name'] for r in references}, {'p'})


if __name__ == '__main__':
    unittest.main()
