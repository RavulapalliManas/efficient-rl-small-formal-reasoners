import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/data'))
sys.path.insert(0, str(ROOT / 'scripts/train'))
from build_search_sft import build  # noqa: E402
from rl_components.sft import SFTDataset  # noqa: E402


def step(sample, index, status, tactic, candidates, accepted=False):
    return dict(group=0, sample=sample, step=index, task_id='t', state=f'obs {sample}.{index}', tactic=tactic,
                status=status, accepted=accepted, chosen=0, candidates=candidates)


class BuildSearchSFTTest(unittest.TestCase):
    def write_run(self, directory, records, mode='training_state_assessment', candidates=4):
        run = Path(directory) / 'run'
        run.mkdir()
        (run / 'run.json').write_text(json.dumps(dict(mode=mode, status='completed', run_id='r',
                                                       config=dict(step_candidates=candidates))))
        (run / 'rollouts.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
        return run

    def test_only_accepted_paths_give_proof_steps_and_repairs(self):
        rejected = dict(index=1, status='rejected', observation_after='obs after error')
        records = [
            step(0, 0, 'changed', 'intro h', [dict(index=0, status='changed'), rejected]),
            step(0, 1, 'partial', 'simp\nbad', [dict(index=0, status='partial')]),
            step(0, 2, 'accepted', 'omega', [dict(index=0, status='accepted')], accepted=True),
            step(1, 0, 'changed', 'intro h', [dict(index=0, status='changed'), rejected]),  # Failed path.
            dict(group=0, sample=1, step=1, task_id='t', automation=True, tactic='simp', status='accepted',
                 accepted=True),
        ]
        with TemporaryDirectory() as directory:
            receipt = build(self.write_run(directory, records), Path(directory) / 'out')
            rows = [json.loads(line) for line in (Path(directory) / 'out/sft_rows.jsonl').read_text().splitlines()]
        self.assertEqual(receipt['counts'], {'proof_step': 2, 'repair': 1})
        self.assertEqual([(r['kind'], r['messages'][0]['content'], r['messages'][1]['content']) for r in rows],
                         [('proof_step', 'obs 0.0', 'intro h'), ('repair', 'obs after error', 'intro h'),
                          ('proof_step', 'obs 0.2', 'omega')])
        self.assertEqual(rows[1]['failed_status'], 'rejected')
        self.assertEqual({r['format'] for r in rows}, {'v21_observation'})

    def test_rows_load_in_the_sft_trainer(self):
        # Lease 1 S_mined failed with KeyError 'format' because mined rows lacked the trainer's required field.
        records = [step(0, 0, 'accepted', 'omega', [dict(index=0, status='accepted')], accepted=True)]
        with TemporaryDirectory() as directory:
            build(self.write_run(directory, records), Path(directory) / 'out')
            dataset = SFTDataset.from_jsonl(Path(directory) / 'out/sft_rows.jsonl')
        self.assertEqual(dataset.records, [dict(prompt=[dict(role='user', content='obs 0.0')],
                                                completion=[dict(role='assistant', content='omega')],
                                                format='v21_observation')])

    def test_refuses_runs_that_are_not_training_search_assessments(self):
        for mode, candidates in (('DEV_evaluation', 4), ('training_state_assessment', 1)):  # A capped source is valid.
            with TemporaryDirectory() as directory:
                run = self.write_run(directory, [], mode=mode, candidates=candidates)
                with self.assertRaises(ValueError):
                    build(run, Path(directory) / 'out')
        with TemporaryDirectory() as directory:
            run = self.write_run(directory, [dict(step(0, 0, 'changed', 'x', []), task_id='dev:y')])
            with self.assertRaisesRegex(ValueError, 'DEV'):
                build(run, Path(directory) / 'out')


if __name__ == '__main__':
    unittest.main()
