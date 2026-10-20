"""Candidate staging checks. No Lean, tokenizer or model runs."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/data'))


def write(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))


def example(name, header, body):
    return dict(theorem=name, format='proof', messages=[
        dict(role='user', content='saved goal state'),
        dict(role='assistant', content=f'```lean\n{header} := by\n{body}\n```')])


class CoverageTest(unittest.TestCase):
    def test_stage_preserves_messages_reserves_eval_and_keeps_train_loss_partition(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            headers = {
                'safe': 'theorem safe (a b : ℕ) (h : a ∣ b) : a ∣ b + b',
                'relative': 'theorem relative (n : ℕ) : n % 17 = n % 17',
                'held': 'theorem held (p : ℕ) : Nat.totient p = p - 1',
                'old_eval': 'theorem old_eval (a : ℕ) : a ∣ a',
                'bad': 'theorem bad (n : ℕ) (h : n < n) : n ∣ n',
            }
            rows = [example(n, h, '  intro h\n  rw [Nat.dvd_add_iff_left h]\n  exact h')
                    for n, h in headers.items()]
            write(d / 'source.jsonl', rows)
            write(d / 'pool.jsonl', [dict(name=n, formal_statement=h, split='test' if n == 'old_eval' else 'train')
                                     for n, h in headers.items()])
            write(d / 'eval.jsonl', [dict(name='reserved', header='theorem reserved (k : ℕ) : k % 19 = k % 19')])
            write(d / 'review.jsonl', [dict(theorem='bad', disposition='exclude',
                                          reason='inconsistent assumption n < n')])
            (d / 'syllabus.json').write_text(json.dumps(dict(families=[dict(id='t7', split='C_heldout_topic',
                lean_statement='(p : ℕ) : Nat.totient p = p - 1')], topics=[])))
            command = [sys.executable, str(ROOT / 'scripts/data/build_nt_sft_data.py'), 'coverage',
                       '--candidate-input', str(d / 'source.jsonl'), '--baseline', str(d / 'source.jsonl'),
                       '--source-pool', str(d / 'pool.jsonl'), '--evaluation-questions', str(d / 'eval.jsonl'),
                       '--source-review', str(d / 'review.jsonl'),
                       '--syllabus', str(d / 'syllabus.json'), '--output', str(d / 'out')]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            kept = [json.loads(line) for line in (d / 'out/candidates_proof.jsonl').read_text().splitlines()]
            self.assertEqual({r['theorem'] for r in kept}, {'safe'})
            self.assertEqual(kept[0]['messages'], rows[0]['messages'])
            self.assertEqual(kept[0]['readiness'], 'NOT_TRAINING_READY')
            self.assertIn(kept[0]['loss_split'], {'train', 'validation_within_source_train'})
            self.assertEqual(kept[0]['provenance']['line'], 1)
            omitted = [json.loads(line) for line in (d / 'out/exclusions.jsonl').read_text().splitlines()]
            self.assertEqual({r['theorem'] for r in omitted}, {'relative', 'held', 'old_eval', 'bad'})
            manifest = json.loads((d / 'out/manifest.json').read_text())
            self.assertEqual(manifest['status'], 'NOT_TRAINING_READY')
            self.assertEqual(manifest['counts']['selected'], 1)
            self.assertFalse(manifest['strict_replay_performed'])
            self.assertTrue((d / 'out/coverage.json').is_file())
            samples = [json.loads(line) for line in (d / 'out/representative_examples.jsonl').read_text().splitlines()]
            self.assertEqual({r['theorem'] for r in samples}, {'safe'})
            self.assertEqual(samples[0]['messages'], rows[0]['messages'])

    def test_method_labels_do_not_treat_terms_or_comments_as_tactics(self):
        import build_nt_sft_data as builder
        labels = builder.coverage_methods('  -- induction and by_contra\n  exact Nat.dvd_refl n')
        self.assertEqual(labels, ['lemma_application'])
        self.assertEqual(builder.coverage_methods('  induction n with\n  | zero => simp\n  | succ n ih => rw [ih]'),
                         ['rewriting', 'induction', 'combined_proof'])


if __name__ == '__main__':
    unittest.main()
