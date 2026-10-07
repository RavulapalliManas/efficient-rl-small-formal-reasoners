"""Focused acquisition scoring checks; no model inference."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/eval'))


class AcquisitionChecks(unittest.TestCase):
    def test_free_response_prompt_excludes_review_metadata(self):
        import evaluate_acquisition as ev
        self.assertTrue(hasattr(ev, 'task_messages'), 'Free-response prompt builder missing')
        task = {'id': 'a', 'family': 'f', 'category': 'semantics', 'condition': 'closed',
                'prompt': 'Explain whether the step follows.', 'rubric': 'PRIVATE RUBRIC',
                'reference': 'PRIVATE ANSWER', 'source': 'PRIVATE SOURCE'}
        self.assertEqual(ev.task_messages(task),
                         [{'role': 'user', 'content': task['prompt']}])
        self.assertEqual(ev.summarize([{'kind': 'semantics', 'accepted': None}]),
                         {'semantics': {'n': 1, 'status':
                          'Requires blinded semantic review against frozen rubric'}})

    def test_completion_stops_at_any_eos_without_counting_batch_padding(self):
        import evaluate_acquisition as ev
        self.assertTrue(hasattr(ev, 'trim_completion'), 'EOS-aware trimming missing')
        self.assertEqual(ev.trim_completion([11, 12, 98, 0, 0], {98, 99}, 0, 5),
                         ([11, 12, 98], False))
        self.assertEqual(ev.trim_completion([11, 12, 99], {98, 99}, 0, 3),
                         ([11, 12, 99], False))
        self.assertEqual(ev.trim_completion([11, 12, 13], {98, 99}, 0, 3),
                         ([11, 12, 13], True))
        self.assertEqual(ev.trim_completion([11, 99, 99], {99}, 99, 3),
                         ([11, 99], False))

    def test_scoring_contract(self):
        self.assertIsNotNone(importlib.util.find_spec('evaluate_acquisition'),
                             'Evaluation-only runner missing')
        import evaluate_acquisition as ev
        for text in ('17', ' 17. ', r'\boxed{17}', '$17$'):
            self.assertEqual(ev.parse_integer(text), 17)
        for text in ('17 or 18', 'The answer is 17', '17.5', '', 'print(17)'):
            self.assertIsNone(ev.parse_integer(text))
        self.assertEqual(ev.parse_integer('-9'), -9)
        item = {'id': 'example', 'recognition': 'Which?',
                'choices': ['wrong', 'right', 'wrong2', 'wrong3'], 'correct': 1}
        for rotation in (0, 1):
            prompt, target = ev.recognition_prompt(item, rotation)
            self.assertIn(f'{target}. right', prompt)
            self.assertNotIn('correct', prompt)
        rows = [{'kind': 'recognition', 'accepted': True, 'parsed': True},
                {'kind': 'recognition', 'accepted': False, 'parsed': True},
                {'kind': 'application', 'accepted': False, 'parsed': False},
                {'kind': 'recall', 'accepted': None, 'parsed': None}]
        summary = ev.summarize(rows)
        self.assertEqual(summary['recognition']['correct'], 1)
        self.assertEqual(summary['recognition']['n'], 2)
        self.assertEqual(summary['application']['unparsed'], 1)
        self.assertNotIn('correct', summary['recall'])

    def test_frozen_application_gold(self):
        import evaluate_acquisition as ev
        cfg = json.loads((ROOT / 'configs/eval/acquisition_probe_v1.json').read_text())
        expected = [13, 3, 12, 1, 1, 1, 19, 0, 51, 0, 1, 3]
        self.assertEqual([ev.application_answer(q) for q in cfg['items']], expected)
        for q in cfg['items']:
            self.assertEqual(len(q['choices']), 4)
            self.assertIn(q['correct'], range(4))


if __name__ == '__main__':
    unittest.main()
