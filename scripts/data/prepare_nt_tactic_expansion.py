"""Prepare frozen TRAIN-source roots and available prefixes, with separate DEV roots.

Peak: <=128 MiB serialized inputs x 6 <768 MiB, tokenizer <1 GiB,
one Lean worker <=4 GiB. Use an 8 GiB managed cap. No policy or optimizer.
Reference length is a proxy. Replay readiness is not model competence.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import textwrap
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_tactic_curriculum import (policy_row, read_rows, top_level_steps,
    task_units, tokens, statement, load_checker, mathlib_identity, repl_context,
    verify_group, reserved_template, STEP)  # noqa: E402
from run_tactic_rl import LeanEpisode  # noqa: E402


def select(tasks, pool, proofs, excluded, eval_states, eval_pool, config):
    train, dev = config['train_names'], config['dev_names']
    if set(train) & set(config.get('excluded_train_names', [])):
        raise ValueError('Explicitly excluded TRAIN parent cannot be requested')
    requested = train + dev
    if not train or not dev or len(requested) > 32 or len(set(requested)) != len(requested):
        raise ValueError('Require nonempty disjoint roles with at most 32 distinct parents')
    if len({r['name'] for r in pool}) != len(pool):
        raise ValueError('Duplicate source pool names')
    task_units(tasks, pool)
    reserved = excluded | {r['name'] for r in eval_pool} | {r.get('name') for r in eval_states}
    if set(requested) & reserved:
        raise ValueError('Requested parent is excluded')
    references = [r for r in proofs if r['name'] in requested and
                  r['status'] == 'accepted' and r['selected']]
    if len({r['name'] for r in references}) != len(references):
        raise ValueError('Duplicate accepted selected references')
    refs = {r['name']: r for r in references}
    if set(requested) - refs.keys() or any(not r.get('source') for r in references):
        raise ValueError('Requested parent or accepted source reference is missing')
    roots = {n: [t for t in tasks if t['name'] == n and t['kind'] == 'whole'] for n in requested}
    if any(len(v) != 1 or v[0]['given'] for v in roots.values()):
        raise ValueError('Requested parent original root is missing or duplicated')
    old_headers = [tokens(statement(r['formal_statement'])) for r in eval_pool]
    old_headers += [tokens(statement(r['header'])) for r in eval_states if r.get('header')]
    old_states = [tokens(r['state']) for r in eval_states if r.get('state')]
    if not old_headers or not old_states:
        raise ValueError('Protected evaluation headers and states are missing')
    dev_headers = [tokens(statement(roots[n][0]['header'])) for n in dev]
    dev_states = [tokens(roots[n][0]['state']) for n in dev]
    admitted, rejected = [], []
    for name in requested:
        role = 'train' if name in train else 'dev'
        ref = refs[name]
        steps, _ = top_level_steps(ref['body'])
        candidates = sorted((t for t in tasks if t['name'] == name),
                            key=lambda t: (t['kind'] != 'whole', t['task_id']))
        for task in candidates:
            reason, rest = None, ref['body']
            if reserved_template(task['header']):
                reason = 'reserved_template'
            elif task['kind'] not in ('whole', 'prefix_top'):
                reason = 'unsupported_start_kind'
            elif role == 'dev' and task['kind'] != 'whole':
                reason = 'dev_prefix_not_exported'
            elif len(steps) > config.get('max_reference_blocks', 12) or len(ref['body']) > config.get('max_reference_chars', 4096):
                reason = 'reference_cap'
            elif task['kind'] == 'prefix_top':
                cuts = [k for k in range(1, len(steps)) if '\n'.join(steps[:k]) + '\n' == task['given']]
                if len(cuts) != 1:
                    raise ValueError('Task prefix differs from selected parent reference')
                rest = '\n'.join(steps[cuts[0]:])
                if len(steps) - cuts[0] > config.get('max_prefix_blocks', 3):
                    reason = 'prefix_cap'
            hs, ss = tokens(statement(task['header'])), tokens(task['state'])
            headers = old_headers + (dev_headers if role == 'train' else [])
            states = old_states + (dev_states if role == 'train' else [])
            if not reason and (any(len(hs & x) / max(len(hs | x), 1) >= .85 for x in headers) or
                               any(len(ss & x) / max(len(ss | x), 1) >= .7 for x in states)):
                reason = 'near_duplicate_eval'
            if reason:
                rejected.append(dict(task_id=task['task_id'], name=name, role=role, reason=reason))
            else:
                admitted.append(dict(task, role=role, reference_rest=rest))
    seen = {}
    for task in admitted:
        if task['role'] == 'train' and task['kind'] == 'whole':
            state = ' '.join(task['state'].split())
            if state in seen:
                raise ValueError('Duplicate whole TRAIN state')
            seen[state] = task['task_id']
    unique = []
    for task in admitted:
        state = ' '.join(task['state'].split())
        if task['role'] == 'train' and task['kind'] != 'whole' and state in seen:
            rejected.append(dict(task_id=task['task_id'], name=task['name'], role='train',
                                 reason='duplicate_train_state', duplicate_of=seen[state]))
        else:
            unique.append(task)
            if task['role'] == 'train':
                seen[state] = task['task_id']
    if config.get('structural_primers'):
        for task in unique:
            if task['role'] != 'train':
                continue
            if task['kind'] == 'whole':
                stage, operations = 'whole', []
            else:
                application = config.get('application_task_ids', [])
                stage = 'application' if task['task_id'] in application else 'composition'
                operations = []
            stages = ('structure', 'application', 'composition', 'whole')
            task.update(curriculum_stage=stage, answer_supplied=False, covered_operations=operations,
                        prerequisites=list(stages[:stages.index(stage)]),
                        prerequisite_kind='declared_introduction_order_not_mastery')
        primers = []
        for task in unique:
            if task['role'] != 'train' or task['kind'] != 'whole':
                continue
            goal = task['state'].rsplit('⊢', 1)[-1].strip()
            if not goal or 'curriculum_fact' in task['state']:
                raise ValueError('Structural primer target is missing or its local name collides')
            body = '  exact curriculum_fact'
            operations = ['exact']
            if goal.startswith('∀'):
                body = '  intro curriculum_argument\n  exact curriculum_fact curriculum_argument'
                operations = ['intro', 'exact']
            elif goal.startswith('∃'):
                body = ('  obtain ⟨curriculum_witness, curriculum_rest⟩ := curriculum_fact\n'
                        '  refine ⟨curriculum_witness, ?_⟩\n  exact curriculum_rest')
                operations = ['obtain', 'refine', 'exact']
            elif '→' in goal:
                body = '  intro curriculum_argument\n  exact curriculum_fact curriculum_argument'
                operations = ['intro', 'exact']
            given = '  have curriculum_fact : ' + goal + ' := by\n' + textwrap.indent(task['reference_rest'], '  ') + '\n'
            primers.append(dict(task, task_id=task['name'] + ':structure:0', kind='prefix_top', given=given,
                                lines_remaining=len(body.splitlines()), reference_rest=body,
                                curriculum_stage='structure', answer_supplied=True,
                                covered_operations=operations, prerequisites=[], requires_state_reconstruction=True))
        unique.extend(primers)
    return unique, rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('tasks', 'pool', 'proofs', 'exclude-names', 'eval-states', 'eval-pool',
                 'checker', 'environment', 'repl', 'mathlib-identity', 'tokenizer', 'config', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    inputs = [getattr(args, n) for n in ('tasks', 'pool', 'proofs', 'exclude_names', 'eval_states', 'eval_pool', 'config')]
    if sum(p.stat().st_size for p in inputs) > 128 * 1024**2:
        raise ValueError('Source bytes exceed memory bound')
    config = json.loads(args.config.read_text())
    pin = '29dcec074de168ac2bf835a77ef68bbe069194c5'
    if config.get('mathlib', pin) != pin:
        raise ValueError('Config differs from pinned Mathlib')
    identity = mathlib_identity(args.mathlib_identity, pin)
    lean = (args.environment / 'mathlib/lean-toolchain').read_text().strip()
    if lean != 'leanprover/lean4:v4.10.0-rc1' or config.get('lean', lean) != lean:
        raise ValueError('Config differs from pinned Lean')
    args.output.mkdir(parents=True, exist_ok=False)
    private = args.output / 'private'
    private.mkdir()
    manifest = dict(status='preparing', config=config, lean=lean, mathlib=identity,
                    attempted=0, rejected=[], unprocessed=[],
                    input_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    ctx, accepted = None, []
    try:
        pool = read_rows(args.pool)
        chosen, rejected = select(read_rows(args.tasks), pool, read_rows(args.proofs),
            set(args.exclude_names.read_text().split()), read_rows(args.eval_states), read_rows(args.eval_pool), config)
        manifest.update(rejected=rejected, unprocessed=[t['task_id'] for t in chosen])
        if set(config['train_names'] + config['dev_names']) - {t['name'] for t in chosen if t['kind'] == 'whole'}:
            raise ValueError('Requested whole root rejected by frozen exclusions or caps')
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
        checker = load_checker(args.checker, args.environment, private)
        ctx = repl_context(checker, args.environment, args.repl, private, 20, 120, 64)
        with (private / 'verification.jsonl').open('x') as stream:
            for task in chosen:
                counts = dict(lean_calls=0, lean_seconds=0., lean_cpu_seconds=0.)
                episode = LeanEpisode(ctx, task, private, counts)
                try:
                    state = episode.reset()
                finally:
                    episode.close()
                reason, wall = None, counts['lean_seconds']
                unit = dict(task_id=task['task_id'], name=task['name'], kind='statement',
                            header=task['header'], given=task['given'], given_steps=[])
                try:
                    result = verify_group(ctx, unit, [task['reference_rest']])[0]
                finally:
                    for repl in list(ctx.pool_repls):
                        repl.close()
                        ctx.pool_repls.discard(repl)
                    ctx.local.__dict__.clear()
                text = tokenizer.apply_chat_template([{'role': 'user', 'content': STEP.format(state=state or '')}],
                                                      tokenize=False, add_generation_prompt=True)
                length = len(tokenizer(text, add_special_tokens=False)['input_ids'])
                reconstruct = task.get('requires_state_reconstruction', False)
                if reconstruct and state:
                    protected = read_rows(args.eval_states)
                    protected += [t for t in chosen if t['role'] == 'dev']
                    ss = tokens(state)
                    if any(len(ss & tokens(t['state'])) / max(len(ss | tokens(t['state'])), 1) >= .7
                           for t in protected if t.get('state')):
                        reason = 'near_duplicate_eval'
                record = dict(task_id=task['task_id'], role=task['role'], state_reason=reason,
                    expected_state=None if reconstruct else task['state'], reconstructed_state=state,
                    state_origin='fresh_constructed_primer' if reconstruct else 'reconstructed_existing_start',
                    state_constructed=bool(state) if reconstruct else None,
                    prior_state_matches=None if reconstruct else ' '.join((state or '').split()) == ' '.join(task['state'].split()),
                    state_matches=None if reconstruct else
                        ' '.join((state or '').split()) == ' '.join(task['state'].split()),
                    prompt_tokens=length, replay=result, state_seconds=wall)
                stream.write(json.dumps(record) + '\n'); stream.flush()
                manifest['attempted'] += 1
                manifest['unprocessed'].remove(task['task_id'])
                state_valid = record['state_constructed'] if reconstruct else record['state_matches']
                if reason or not state_valid or result['status'] != 'accepted' or length > config.get('max_prompt_tokens', 1024):
                    manifest['rejected'].append(dict(task_id=task['task_id'], reason='replay_or_state_or_prompt_failure'))
                    raise RuntimeError('Frozen selected start failed verification: ' + task['task_id'])
                accepted.append(dict(task, state=state))
        for role in ('train', 'dev'):
            public = [dict(policy_row(t), **{k: t[k] for k in
                      ('curriculum_stage', 'answer_supplied', 'covered_operations', 'prerequisites', 'prerequisite_kind') if k in t})
                      for t in accepted if t['role'] == role]
            (args.output / (role + '_tasks.jsonl')).write_text(''.join(json.dumps(t) + '\n' for t in public))
            names = set(config[role + '_names'])
            (args.output / (role + '_pool.jsonl')).write_text(''.join(json.dumps({k:r[k] for k in ('name', 'split', 'formal_statement')}) + '\n' for r in pool if r['name'] in names))
        (private / 'references.jsonl').write_text(''.join(json.dumps(t) + '\n' for t in accepted if t['role'] == 'train'))
        manifest.update(status='prepared', admitted=len(accepted), by_role=dict(Counter(t['role'] for t in accepted)))
    except Exception as error:
        manifest.update(status='failed', error=str(error))
        raise
    finally:
        if ctx:
            for repl in list(ctx.pool_repls):
                repl.close()
        (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
