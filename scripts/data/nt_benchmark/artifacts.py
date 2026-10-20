"""Proof profiling and split-safe exports consumed by SFT and RL runners."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from build_proof_tasks import top_level_steps
from probe_nt_pool import INSTRUCTION
from probe_start_depth import lean_code
from verify_external_proofs import HEADER
from .storage import digest, file_sha256, rows, write_rows


def reference_profile(body):
    code = '\n'.join(line for line in lean_code(body).splitlines() if line.strip())
    blocks, _ = top_level_steps(code)
    return dict(reference_lines=len(code.splitlines()), reference_blocks=len(blocks))



class BenchmarkExporter:
    """Keep held-out reference proofs out of public tasks and training messages."""

    def describe(self, package, output):
        """Write evaluation questions and private answers without changing membership.

        Peak: <=256 rows x 256 KiB x 3 copies <192 MiB. Use a 256 MiB cap.
        This exports saved verification evidence; it does not replay Lean or
        certify the intended mathematics. Unknown semantic reviews stay unknown.
        """
        manifest = json.loads((package / 'manifest.json').read_text())
        if manifest['status'] != 'exploratory_verified':
            raise ValueError('A verified source package is required')
        data = package / 'data'
        references = {}
        for row in rows(data / 'private/references.jsonl'):
            if row['task_id'] in references or len(references) >= 256:
                raise ValueError('Duplicate reference or reference cap exceeded')
            references[row['task_id']] = row
        questions, answers, seen = {}, [], set()
        for split, label in [('validation', 'dev'), ('test', 'test')]:
            questions[label] = []
            for task in rows(data / f'tasks_{label}.jsonl'):
                if task['split'] != split or task['task_id'] in seen:
                    raise ValueError('Evaluation split mismatch or duplicate task')
                if task['kind'] != 'whole' or task['given']:
                    raise ValueError('Evaluation questions must start without a supplied proof')
                seen.add(task['task_id'])
                reference = references.get(task['task_id'], {})
                if (any(reference.get(key) != task[key] for key in ('name', 'header', 'state', 'split'))
                        or reference.get('reference_status') != 'accepted'
                        or reference.get('screen_status') != 'not_solved'):
                    raise ValueError('Question differs from its accepted reference')
                public = {key: task[key] for key in ('task_id', 'name', 'header', 'state', 'split')}
                public.update(expected_response='lean_tactic_block', semantic_review='unreviewed',
                              source_template_group=task.get('family'),
                              question='Prove the exact theorem below under its stated hypotheses.')
                questions[label].append(public)
                answers.append(dict(task_id=task['task_id'], split=split,
                                    header=reference['header'], reference_body=reference['reference_body'],
                                    reference_status='accepted_in_source_package'))
        contract = dict(
            schema_version='nt_evaluation_spec_v1', imports_and_options=HEADER,
            lean=manifest['config']['lean'], mathlib=manifest['config']['mathlib'],
            response='One fenced lean block containing the next tactic block; comments are optional.',
            expected_response='lean_tactic_block', theorem_redeclaration_allowed=False,
            assessment_runner='scripts/train/run_tactic_rl.py --assessment-only',
            assessment_limits=None,
            limits_status='Bind action, token, attempt and time limits before model sampling.',
            success='The accumulated proof proves the unchanged theorem and passes complete replay.',
            reference_exact_match_required=False,
            allowed_axioms=['propext', 'Classical.choice', 'Quot.sound'],
            forbidden=['sorry or admitted dependencies', 'custom axioms', 'changed theorem or hypotheses'],
            partial_steps_are_complete_proofs=False,
            explanation='Optional comments must describe the formal step. Readability is not Lean-certified.',
            private_answer_policy='References and reviewer notes never enter policy prompts or training.',
            semantic_review='Required before treating questions as a benchmark of the intended mathematics.')
        source_paths = ['manifest.json', 'data/tasks_dev.jsonl', 'data/tasks_test.jsonl',
                        'data/private/references.jsonl']
        result = dict(schema_version=contract['schema_version'], status='semantic_review_required',
                      questions={key: len(value) for key, value in questions.items()},
                      source_sha256={name: file_sha256(package / name) for name in source_paths},
                      reference_validation='Reuses saved source verdicts; no fresh Lean replay.',
                      source_module_sha256=file_sha256(Path(__file__)),
                      limitations=['Semantic families, assumption consistency and intended meaning need review.',
                                   'Assessment budgets and model-lineage exposure checks remain unresolved.'])
        output.mkdir(parents=True, exist_ok=False)
        (output / 'private').mkdir()
        for label, values in questions.items():
            write_rows(output / f'questions_{label}.jsonl', values)
        write_rows(output / 'private/expected_answers.jsonl', answers)
        for name, value in [('answer_contract.json', contract), ('manifest.json', result)]:
            (output / name).write_text(json.dumps(value, indent=2) + '\n')
        return result

    def export(self, verified, output):
        """Only freshly checked references with a complete negative screen can enter."""
        for row in verified:
            if (row.get('reference_status') != 'accepted' or row.get('screen_status') != 'not_solved'
                    or not row.get('state') or row.get('excluded_reason')):
                raise ValueError('Incomplete or failed verification cannot be exported')
        output.mkdir(parents=True, exist_ok=False)
        (output / 'sft').mkdir()
        (output / 'private').mkdir()
        pool, tasks, sft = [], defaultdict(list), defaultdict(list)
        for row in verified:
            row.update(reference_profile(row['reference_body']))
            public = {field: row[field] for field in ('task_id', 'name', 'header', 'state', 'family', 'topic',
                                                     'reference_lines', 'reference_blocks', 'annotation_status')}
            public.update(kind='whole', given='', lines_remaining=None, split=row['split'])
            tasks[row['split']].append(public)
            pool.append(dict(name=row['name'], formal_statement=row['header'], split=row['split']))
            if row['split'] == 'train':
                loss_split = 'val' if int(digest(row['family']), 16) % 10 == 0 else 'train'
                sft[loss_split].append(dict(theorem=row['name'], family=row['family'], format='proof',
                    messages=[dict(role='user', content=INSTRUCTION.format(state=row['state'])),
                              dict(role='assistant', content='```lean\n' + row['header'] + ' := by\n'
                                   + row['reference_body'] + '\n```')]))
        write_rows(output / 'pool.jsonl', pool)
        for split in ('train', 'validation', 'test'):
            label = 'dev' if split == 'validation' else split
            write_rows(output / f'tasks_{label}.jsonl', tasks[split])
        for split in ('train', 'val'):
            write_rows(output / f'sft/sft_{split}.jsonl', sft[split])
        write_rows(output / 'private/references.jsonl', verified)
        (output / 'excluded_from_training.txt').write_text(''.join(r['name'] + '\n' for r in verified if r['split'] != 'train'))
        return dict(by_split={split: len(tasks[split]) for split in ('train', 'validation', 'test')},
                    by_topic=dict(Counter(row['topic'] for row in verified)),
                    sft={split: len(sft[split]) for split in ('train', 'val')})
