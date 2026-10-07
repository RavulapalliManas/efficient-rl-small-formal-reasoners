"""Planted summary outcomes, unknown handling, and input alignment."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/grade_mathematical_responses.py'


class MathematicalGradingChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        specifications = [
            ('a0', 'A', 'acquisition', 'independent_use', True, True),
            ('a1', 'A', 'acquisition', 'independent_use', False, False),
            ('b0', 'B', 'acquisition', 'independent_use', True, False),
            ('b1', 'B', 'acquisition', 'independent_use', None, None),
            ('s0', 'B', 'acquisition', 'supplied_use', True, True),
            ('c0', 'C', 'composition', 'independent_use', True, True),
            ('r0', 'B', 'acquisition', 'recall', None, None),
        ]
        self.tasks, self.responses = [], []
        self.reviews = {'a': {}, 'b': {}}
        for cid, family, category, condition, a, b in specifications:
            task = dict(id=cid, family=family, category=category, condition=condition,
                        prompt='prompt '+cid, reference='reference '+cid)
            self.tasks.append(task)
            self.responses.append(dict(task, response='response '+cid, capped=cid == 'b0'))
            for reviewer, value in [('a', a), ('b', b)]:
                self.reviews[reviewer][cid] = dict(review_id=reviewer+'-'+cid, full_correct=value)

    def run_summary(self, responses=None, reviews=None, tasks=None, adjudication=None, baseline=None):
        (self.root/'config.json').write_text(json.dumps({'tasks': self.tasks if tasks is None else tasks}))
        (self.root/'responses.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in
            (self.responses if responses is None else responses)))
        (self.root/'reviews.json').write_text(json.dumps(self.reviews if reviews is None else reviews))
        command = [sys.executable, str(SCRIPT), 'summarize', '--config', str(self.root/'config.json'),
                   '--responses', str(self.root/'responses.jsonl'), '--reviews', str(self.root/'reviews.json'),
                   '--output', str(self.root/'summary')]
        if adjudication is not None:
            (self.root/'adjudication.json').write_text(json.dumps(adjudication))
            command += ['--adjudication', str(self.root/'adjudication.json')]
        if baseline is not None:
            command += ['--baseline-summary', str(baseline)]
        return subprocess.run(command, capture_output=True, text=True)

    def test_identical_pair_has_zero_delta_and_interval(self):
        first = self.run_summary()
        self.assertEqual(first.returncode, 0, first.stderr)
        baseline = self.root/'baseline'
        (self.root/'summary').rename(baseline)
        second = self.run_summary(baseline=baseline/'report.json')
        self.assertEqual(second.returncode, 0, second.stderr)
        report = json.loads((self.root/'summary/report.json').read_text())
        paired = report['paired']['primary']
        self.assertEqual(paired['macro_difference'], 0)
        self.assertEqual(paired['bootstrap']['interval_95'], [0, 0])
        self.assertEqual(paired['unchanged'], 4)

    def test_all_false_to_all_true_has_unit_delta_and_interval(self):
        before, after = deepcopy(self.reviews), deepcopy(self.reviews)
        for reviewer in before:
            for cid in before[reviewer]:
                before[reviewer][cid]['full_correct'] = False
                after[reviewer][cid]['full_correct'] = True
        first = self.run_summary(reviews=before)
        self.assertEqual(first.returncode, 0, first.stderr)
        baseline = self.root/'baseline'
        (self.root/'summary').rename(baseline)
        second = self.run_summary(reviews=after, baseline=baseline/'report.json')
        self.assertEqual(second.returncode, 0, second.stderr)
        report = json.loads((self.root/'summary/report.json').read_text())
        self.assertEqual(report['paired']['primary']['macro_difference'], 1)
        self.assertEqual(report['paired']['primary']['bootstrap']['interval_95'], [1, 1])
        self.assertEqual(report['paired']['primary']['gained'], 4)

    def test_pair_rejects_changed_tasks(self):
        first = self.run_summary()
        self.assertEqual(first.returncode, 0, first.stderr)
        baseline = self.root/'baseline'
        (self.root/'summary').rename(baseline)
        self.tasks[0]['prompt'] = self.responses[0]['prompt'] = 'different task'
        self.assertNotEqual(self.run_summary(baseline=baseline/'report.json').returncode, 0)

    def test_known_macro_and_unknowns(self):
        result = self.run_summary()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root/'summary/report.json').read_text())
        primary = report['consensus']['primary']
        self.assertEqual(primary['n'], 4)
        self.assertEqual(primary['unknown'], 2)
        self.assertEqual(primary['macro_full_correct'], 0.25)
        self.assertEqual(primary['by_concept']['A']['full_correct_fraction'], 0.5)
        self.assertEqual(primary['by_concept']['B']['false'], 0)
        self.assertEqual(primary['by_concept']['B']['unknown'], 2)
        self.assertEqual(primary['capped'], 1)
        self.assertEqual(report['consensus']['composition']['macro_full_correct'], 1)
        self.assertEqual(report['reviewers']['a']['primary']['macro_full_correct'], 0.5)
        self.assertEqual(report['reviewers']['b']['primary']['macro_full_correct'], 0.25)
        self.assertIsNone(report['consensus_full_correct']['b0'])

    def test_evidenced_adjudication_changes_consensus_only(self):
        adjudication = {'entries': [dict(case_id='b0', field='full_correct', resolved_value=True,
                                        rationale='Checked the response.', response_evidence='response b0')]}
        result = self.run_summary(adjudication=adjudication)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root/'summary/report.json').read_text())
        self.assertEqual(report['consensus']['primary']['macro_full_correct'], 0.5)
        self.assertEqual(report['reviewers']['b']['primary']['macro_full_correct'], 0.25)
        self.assertEqual(report['consensus']['primary']['unknown'], 1)

    def test_rejects_missing_duplicate_and_wrong_ids(self):
        damaged = deepcopy(self.reviews)
        damaged['b']['wrong'] = damaged['b'].pop('a0')
        duplicate = deepcopy(self.reviews)
        duplicate['a']['a1']['review_id'] = duplicate['a']['a0']['review_id']
        for kwargs in [{'responses': self.responses[:-1]},
                       {'responses': self.responses+[self.responses[0]]},
                       {'tasks': self.tasks+[self.tasks[0]]},
                       {'reviews': damaged}, {'reviews': duplicate}]:
            with self.subTest(kwargs=kwargs):
                self.assertNotEqual(self.run_summary(**kwargs).returncode, 0)
                self.assertFalse((self.root/'summary').exists())

    def test_rejects_changed_task_and_missing_truncation(self):
        for field in ['prompt', 'family', 'reference', 'capped']:
            damaged = deepcopy(self.responses)
            if field == 'capped':
                del damaged[0][field]
            else:
                damaged[0][field] = 'changed'
            with self.subTest(field=field):
                self.assertNotEqual(self.run_summary(responses=damaged).returncode, 0)

    def test_rejects_unsupported_adjudication(self):
        for evidence in ['', 'text absent from response']:
            entry = dict(case_id='b0', field='full_correct', resolved_value=True,
                         rationale='A rationale.', response_evidence=evidence)
            self.assertNotEqual(self.run_summary(adjudication={'entries': [entry]}).returncode, 0)


if __name__ == '__main__':
    unittest.main()
