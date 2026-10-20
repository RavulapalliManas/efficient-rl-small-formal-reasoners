"""Batch-verifier process outcomes, without Lean or model execution."""
import importlib.util
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class ExternalProofsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'scripts/data/verify_external_proofs.py'
        spec = importlib.util.spec_from_file_location('external_proofs', path)
        cls.verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.verifier)

    def check(self, returncode=0, axioms=None, error=False, failure=None, *, strict=False, axiom_name=None, texts=None):
        batch = [dict(text=text) for text in (texts or ['theorem a : True := by\n  trivial',
                                                        'theorem b : True := by\n  exact missing'])]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'batch.lean'

            def run(command, **kwargs):
                if failure is not None:
                    raise failure
                lines = path.read_text().splitlines()
                messages = [dict(severity='information', pos=dict(line=lines.index(f'#print axioms check_{index}') + 1),
                                 data=(f'{axiom_name or f"check_{index}"} depends on axioms: [{", ".join(names)}]' if names else
                                       f'{axiom_name or f"check_{index}"} does not depend on any axioms'))
                            for index, names in (axioms or {}).items()]
                if error:
                    messages.append(dict(severity='error', pos=dict(line=lines.index('  exact missing') + 1),
                                         data='unknown identifier missing'))
                return subprocess.CompletedProcess(command, returncode,
                    stdout='\n'.join(json.dumps(message) for message in messages), stderr='fixture stderr')

            with patch.object(self.verifier.subprocess, 'run', side_effect=run):
                return self.verifier.check_batch(Path('/unused/lean'), {}, path, batch, 1, strict=strict)[0]

    def test_crashed_process_cannot_supply_negative_screens_or_partial_acceptance(self):
        for returncode in (-9, 2):
            with self.subTest(returncode=returncode):
                results = self.check(returncode=returncode, axioms={0: []})
                self.assertEqual([row['status'] for row in results], ['crash', 'crash'])
                self.assertTrue(all(row['returncode'] == returncode for row in results))
                self.assertTrue(all(row['stderr'] == 'fixture stderr' for row in results))

    def test_missing_per_item_evidence_is_incomplete_even_after_a_normal_exit(self):
        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                results = self.check(returncode=returncode, axioms={0: []})
                self.assertEqual([row['status'] for row in results], ['accepted', 'incomplete'])

    def test_diagnostic_exit_one_preserves_genuine_per_item_outcomes(self):
        results = self.check(returncode=1, axioms={0: []}, error=True)
        self.assertEqual([row['status'] for row in results], ['accepted', 'rejected'])
        self.assertEqual(results[1]['first_error'], 'unknown identifier missing')

    def test_axiom_results_keep_the_existing_acceptance_rules(self):
        results = self.check(axioms={0: ['propext', 'Classical.choice'], 1: ['sorryAx']})
        self.assertEqual([row['status'] for row in results], ['accepted', 'sorry_only'])
        results = self.check(axioms={0: [], 1: ['unapproved_axiom']})
        self.assertEqual([row['status'] for row in results], ['accepted', 'rejected'])

    def test_process_launch_failure_is_a_crash_with_its_diagnostic(self):
        results = self.check(failure=FileNotFoundError('Lean binary missing'))
        self.assertEqual([row['status'] for row in results], ['crash', 'crash'])
        self.assertTrue(all('Lean binary missing' in row['stderr'] for row in results))

    def test_batch_timeout_keeps_its_existing_status(self):
        results = self.check(failure=subprocess.TimeoutExpired('lean', 1))
        self.assertEqual(results, [dict(status='batch_timeout'), dict(status='batch_timeout')])

    def test_strict_mode_rejects_forbidden_proof_text_before_lean(self):
        result = self.check(strict=True, axioms={0: []}, texts=['theorem a : True := by\n  trivial',
                                                                  'theorem b : True := by\n  sorry'])
        self.assertIn('one_command_check', Path(self.verifier.__file__).read_text())
        self.assertEqual(result[1]['status'], 'forbidden_construct')

    def test_strict_mode_requires_axiom_message_to_name_the_target(self):
        results = self.check(strict=True, axioms={0: [], 1: []}, axiom_name='Shadow.check_0')
        self.assertEqual(results[0]['status'], 'incomplete')

    def test_strict_mode_checks_each_candidate_as_one_command(self):
        batch = [dict(text='theorem a : True := by\n  trivial\nnamespace Injected\nend Injected')]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'batch.lean'
            def run(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, stdout='', stderr='')
            with patch.object(self.verifier.subprocess, 'run', side_effect=run):
                self.verifier.check_batch(Path('/unused/lean'), {}, path, batch, 1, strict=True)
            self.assertIn('EXTRA_COMMAND', path.read_text())
            self.assertIn('TARGET_ABSENT', path.read_text())


if __name__ == '__main__':
    unittest.main()
