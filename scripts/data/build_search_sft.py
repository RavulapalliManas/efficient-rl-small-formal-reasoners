"""Mine SFT rows from a step-search assessment of TRAIN tasks (expert iteration and error repair).

Usage: build_search_sft.py RUN_DIR OUTPUT_DIR

RUN_DIR is a completed or capped training-state assessment of run_tactic_rl.py with step_candidates > 1.
Only trajectories that end in a Lean-accepted policy proof contribute; automation records never do.
For each step whose kept action applied fully, it writes:
- proof_step: the observation before the step, then the kept action;
- repair: the observation after a rejected candidate (it shows Lean's error), then the kept action,
  which applies from the same proof state.
Rows use the SFT chat format (first message prompt, the rest completion) with format v21_observation.
Identical rows are written once.
Peak memory: one trajectory at a time plus the set of row hashes.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

APPLIED = ('changed', 'accepted')


def rows_from_trajectory(steps, source):
    if not any(step.get('accepted') for step in steps):
        return
    for step in steps:
        if step['status'] not in APPLIED:
            continue
        # Observations come from the shared render_observation, the template of v21_observation SFT rows.
        base = dict(format='v21_observation', source_run=source, task_id=step['task_id'], group=step['group'],
                    sample=step['sample'], step=step['step'], chosen=step['chosen'])
        yield dict(base, kind='proof_step', messages=[dict(role='user', content=step['state']),
                                                     dict(role='assistant', content=step['tactic'])])
        for probe in step['candidates']:
            if 'observation_after' in probe:
                yield dict(base, kind='repair', failed_candidate=probe['index'], failed_status=probe['status'],
                           messages=[dict(role='user', content=probe['observation_after']),
                                     dict(role='assistant', content=step['tactic'])])


def build(run_dir, output_dir):
    run_dir, output_dir = Path(run_dir), Path(output_dir)
    run = json.loads((run_dir / 'run.json').read_text())
    if run.get('mode') != 'training_state_assessment' or run.get('status') not in ('completed', 'capped'):
        raise ValueError('Mine only a completed or capped training-state assessment')
    if run.get('config', {}).get('step_candidates', 1) < 2:
        raise ValueError('The source run did not log step-search candidates')
    trajectories = defaultdict(list)
    with (run_dir / 'rollouts.jsonl').open() as stream:
        for line in stream:
            record = json.loads(line)
            if record.get('automation'):
                continue
            if record['task_id'].startswith('dev:'):
                raise ValueError('A DEV task reached a training-data source')
            trajectories[(record['group'], record['sample'])].append(record)
    output_dir.mkdir(parents=True, exist_ok=False)
    seen, counts = set(), defaultdict(int)
    with (output_dir / 'sft_rows.jsonl').open('x') as out:
        for key in sorted(trajectories):
            steps = sorted(trajectories[key], key=lambda r: r['step'])
            for row in rows_from_trajectory(steps, str(run_dir)):
                digest = hashlib.sha256(json.dumps(row['messages'], ensure_ascii=False).encode()).hexdigest()
                if digest in seen:
                    counts['duplicate_' + row['kind']] += 1
                    continue
                seen.add(digest)
                counts[row['kind']] += 1
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
    receipt = dict(source_run=str(run_dir), source_run_id=run.get('run_id'),
                   rollouts_sha256=hashlib.sha256((run_dir / 'rollouts.jsonl').read_bytes()).hexdigest(),
                   trajectories=len(trajectories), counts=dict(counts))
    (output_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(build(sys.argv[1], sys.argv[2])))
