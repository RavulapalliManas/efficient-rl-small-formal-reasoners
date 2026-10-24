"""On-policy tactic RL with Lean transitions and a leave-one-out baseline.

This command runs training, DEV evaluation, and frozen-task assessment.
Training samples independent attempts on a theorem and updates the policy
with discounted reward-to-go and a leave-one-out baseline.

A complete proof earns reward 1 only after replay through verify_group.
Goal-text bonuses and diagnostic error penalties are heuristic rewards.
Environment version 2 keeps valid tactic transitions, records failed actions,
and applies the configured backtracking rules. A timeout ends the attempt;
a verifier crash censors the group.

Reusable code is organized under rl_components:
- environment and observations: proof interaction and shared prompts.
- actions: tactic parsing and diagnostic formatting.
- generation: sampling backends and token records.
- configuration: run validation and mode restrictions.
- tasks and objectives: task schedules and policy updates.

LeanEpisode owns proof interaction. main() owns the command, run records,
resource limits, sampling loop, and checkpoint export.
"""
import argparse
from collections import Counter
from contextlib import nullcontext
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ('train', 'data', 'eval', 'audit')]
from bench_lean_verifier import Repl, VerifierSetupError, first_error, repl_context, repl_output, split_prefix, verify_group  # noqa: E402
from classify_proof_failures import classify as classify_failure  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import header, lean_unit  # noqa: E402
from probe_start_depth import FORBIDDEN, FORBIDDEN_CODE  # noqa: E402
from replay_training_proofs import load, proof_body, question  # noqa: E402
from run_lean_grpo import task_units  # noqa: E402
from search_tactics import STEP  # noqa: E402
from verify_external_proofs import HEADER  # noqa: E402
from rl_components import LeaveOneOutObjective, PolicyGradientUpdater, TaskCurriculum, TaskSchedule, component_fingerprints  # noqa: E402
from rl_components.actions import (  # noqa: E402
    BACKTRACK, BACKTRACK_V2, CHAR_LITERAL, CONTINUATION, DECLARATION, FENCE,
    INSTRUCTION_V2, RAW_STRING, action_positions, cap_message, lex_lines, parse_action,
)
from rl_components.configuration import (  # noqa: E402
    REQUIRED_KEYS, V2_KEYS, check_mode_settings, validate_config as validate_run_config,
    validate_environment_settings,
)
from rl_components.generation import (  # noqa: E402
    HFGenerator, VLLMGenerator, generation_record, pad_left, step_seed,
    stop_trimmed, vllm_action_ids,
)
# Compatibility exports for existing command callers; new code imports the owners.
from rl_components.environment import (  # noqa: E402
    BudgetReached, COUNTS_LOCK, LeanEpisode, anonymous_header, close_repl,
    finish, forbidden, local_feedback, state_key, transition, v2_feedback,
)
from rl_components.observations import render_observation  # noqa: E402
from rl_components.objectives import action_logprob  # noqa: E402
from rl_components.proofs import tactic_block  # noqa: E402


def bound_state(result, prompt_tokens, maximum):
    if not result['done'] and prompt_tokens > maximum:
        return dict(result, status='prompt_cap', transition_status=result['status'], done=True)
    return result


def can_export(run, updates):
    return bool(updates and run['status'] in ('completed', 'capped') and run.get('phase') != 'update')


def mathlib_identity(path, expected):
    identity = json.loads(path.read_text())
    if identity['mathlib_revision'] != expected or identity['mismatches'] or not identity['source']:
        raise ValueError('Mathlib source receipt does not establish the registered pin')
    return identity


def advantages(rewards, gamma=1.0):
    """Each baseline excludes its trajectory, including its earlier rewards.

    Step t gets sum_{k>=t} gamma^k r_k minus the other trajectories' mean discounted return from the
    start, so both terms are measured from step 0. This is the exact gradient of the discounted
    objective. Groups with no rewards give zero advantage. gamma = 1 reproduces the old values."""
    return LeaveOneOutObjective(gamma).advantages(rewards)


def training_tasks(tasks, pool, excluded, max_remaining):
    return TaskCurriculum(pool, excluded, max_remaining, validate_units=task_units).training(tasks)


def assessment_tasks(tasks, pool, excluded, max_remaining, split='train'):
    if split == 'train':
        return training_tasks(tasks, pool, excluded, max_remaining)
    return TaskCurriculum(pool, excluded, max_remaining, validate_units=task_units).assessment(tasks, split)


def bank_tasks(bank, rows, excluded_files, eval_spans):
    """Admit original-context training units; keep reference suffixes outside policy records."""
    by_id = {row['theorem_id']: row for row in rows}
    if len(by_id) != len(rows) or len({u['task_id'] for u in bank}) != len(bank):
        raise ValueError('Duplicate bank task or training theorem IDs')
    keys = ('task_id', 'theorem_id', 'full_name', 'file_path', 'header', 'k', 'steps_total',
            'steps_remaining', 'state', 'state_source', 'given_steps', 'topic')
    tasks = []
    for unit in bank:
        row = by_id.get(unit['theorem_id'])
        if row is None or row.get('official_split') != 'train':
            raise ValueError('Bank theorem is not an official training row')
        if unit['file_path'] in excluded_files or unit['file_path'] in eval_spans:
            raise ValueError('Bank theorem lies in an excluded or evaluation file')
        if (unit['header'] != row['declaration_header'] or unit['file_path'] != row['file_path']
                or unit['full_name'] != row['full_name']):
            raise ValueError('Bank theorem metadata differs from the training row')
        if (unit['k'] != len(unit['given_steps']) or not 0 <= unit['k'] < unit['steps_total']
                or unit['steps_remaining'] != unit['steps_total'] - unit['k']
                or any(not step.startswith('  ') or FORBIDDEN.search(step) for step in unit['given_steps'])):
            raise ValueError('Invalid bank prefix or remaining-step metadata')
        tasks.append(dict({key: unit[key] for key in keys if key in unit}, kind='bank', name=row['full_name']))
    return tasks


def evaluation_tasks(pool, names, dev):
    if not names or not names <= dev:
        raise ValueError('Evaluation names must be a nonempty subset of DEV')
    rows = {r['name']: r for r in pool}
    if not names <= rows.keys():
        raise ValueError('Evaluation name missing from pool')
    return [dict(task_id='dev:' + name, name=name, kind='whole', header=header(rows[name]),
                 given='', state='') for name in sorted(names)]


def task_order(tasks, seed):
    """Equal root exposure for whole and assisted tasks, shuffled within each kind."""
    return TaskSchedule(tasks).balanced(seed)


def scheduled_tasks(tasks, order, max_groups):
    """Consume a frozen schedule exactly; repeats specify explicit exposure."""
    return TaskSchedule(tasks).frozen(order, max_groups)


def pass_rate_filter(tasks, spec):
    """Keep training tasks whose measured pass rate lies in (min_exclusive, max_inclusive].

    Rates come from steps.jsonl of a completed or capped training-state assessment, so no DEV or held-out
    outcome selects training tasks. Only complete groups are recorded; a task without a measured rate is dropped.
    """
    source = Path(spec['pass_rates_from'])
    run = json.loads((source / 'run.json').read_text())
    if run.get('mode') != 'training_state_assessment' or run.get('status') not in ('completed', 'capped'):
        raise ValueError('Pass rates must come from a completed or capped training-state assessment run')
    accepted, attempts = Counter(), Counter()
    with (source / 'steps.jsonl').open() as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('status') == 'censored':
                continue
            if row['task_id'].startswith('dev:') or any(v not in (0, 1) for v in row['returns']):
                raise ValueError('Pass rates need training tasks with terminal 0/1 returns')
            accepted[row['task_id']] += sum(row['returns'])
            attempts[row['task_id']] += len(row['returns'])
    kept, dropped = [], Counter()
    for task in tasks:
        n = attempts[task['task_id']]
        rate = accepted[task['task_id']] / n if n else None
        if rate is None:
            dropped['unmeasured'] += 1
        elif rate <= spec['min_exclusive']:
            dropped['at_or_below_min'] += 1
        elif rate > spec['max_inclusive']:
            dropped['above_max'] += 1
        else:
            kept.append(task)
    if not kept:
        raise ValueError('The pass-rate filter keeps no training task')
    return kept, dict(spec, steps_sha256=file_sha256(source / 'steps.jsonl'), kept=len(kept), dropped=dict(dropped))


def filter_schedule(schedule, kept_ids):
    """Drop filtered tasks from a frozen order; equal multiplicities across arms stay equal."""
    filtered = [task_id for task_id in schedule if task_id in kept_ids]
    if not filtered:
        raise ValueError('The pass-rate filter removes every scheduled group')
    return filtered


def execution_mode(dev_evaluation, assessment_only, assessment_split='train'):
    if assessment_split not in ('train', 'validation', 'test'):
        raise ValueError('Unknown assessment split')
    if assessment_split != 'train' and not assessment_only:
        raise ValueError('A non-training split requires assessment-only mode')
    if dev_evaluation and assessment_only:
        raise ValueError('Training-state assessment cannot be combined with DEV evaluation')
    if dev_evaluation:
        return 'DEV_evaluation'
    if assessment_only:
        return 'training_state_assessment' if assessment_split == 'train' else assessment_split + '_assessment'
    return 'training'


def lockstep_group(envs, generate, decode, observe, pool, *, depth, candidates, automation, max_prompt_tokens,
                   bonus, seeds, emit, counts, solved, name):
    """Roll out one group with all samples advancing together; each episode owns its Lean REPL.

    Each step makes one generate call for every active sample, then steps their episodes in the worker
    pool. Per-sample rules match the sequential loop: prompt cap, state bound, step search, automation
    fallback, censoring and the PAD-before-EOS refusal. Records go to emit in sample order, from this
    thread only. Returns (trajectories, censored, reason, root_automation, automation_done).
    """
    from concurrent.futures import wait
    size = len(envs)
    trajectories, automation_done, root_automation = [[] for _ in envs], [False] * size, None
    resets = [pool.submit(env.reset) for env in envs]
    wait(resets)
    states = []
    for future in resets:
        error = future.exception()
        if isinstance(error, (TimeoutError, EOFError, OSError, RuntimeError)):
            return trajectories, True, 'setup_failure: ' + str(error)[:200], None, automation_done
        states.append(future.result())
    if automation:
        root_automation = envs[0].try_automation(automation, commit=False)
        if root_automation and root_automation['result'].get('censored'):
            return trajectories, True, 'automation_root_' + root_automation['result']['status'], None, automation_done
        if root_automation and not root_automation['result']['accepted']:
            # A timed-out probe is a failed automation outcome. Its REPL closed, so rebuild the root.
            try:
                states[0] = envs[0].reset()
            except (TimeoutError, EOFError, OSError, RuntimeError) as error:
                return trajectories, True, 'setup_failure: ' + str(error)[:200], root_automation, automation_done
        elif root_automation:
            with COUNTS_LOCK:
                counts['automation_root_solved'] += 1
    active, pending = list(range(size)), [None] * size
    for step in range(depth):
        if not active:
            break
        prompts = []
        for i in active:
            ids = pending[i] or observe(envs[i])
            pending[i] = None
            if len(ids) > max_prompt_tokens:
                return trajectories, True, 'prompt_cap', root_automation, automation_done
            prompts.append(ids)
        batches = generate(prompts, candidates, [seeds(i, step) for i in active])
        texts = {}
        for i, ids, generations in zip(active, prompts, batches):
            for generation in generations:
                with COUNTS_LOCK:
                    counts['generated_tokens'] += generation['generated_tokens']
                    counts['retained_tokens'] += generation['retained_tokens']
                if generation['pad_before_eos']:
                    emit(dict(sample=i, step=step, prompt_ids=ids, status='generation_contract_violation', reward=None,
                              accepted=False, done=True, censored=True, **generation))
                    raise RuntimeError('Sampled PAD before EOS; refuse an unmatched training update')
            texts[i] = [decode(g['action_ids']) for g in generations]
        def apply_action(index):
            if candidates == 1:
                return envs[index].step(texts[index][0], bonus)
            return envs[index].search_step(texts[index])

        futures = {i: pool.submit(apply_action, i) for i in active}
        wait(futures.values())
        finished, censored = [], None
        for i, ids, generations in zip(active, prompts, batches):
            outcome = futures[i].result()  # An unexpected environment error propagates, as in the sequential loop.
            if candidates == 1:
                search, generation, tactic, result = None, generations[0], texts[i][0], outcome
            else:
                search, chosen = outcome, outcome['chosen']
                generation, tactic, result = generations[chosen], texts[i][chosen], outcome['result']
            if not result['done']:
                pending[i] = observe(envs[i])
                result = bound_state(result, len(pending[i]), max_prompt_tokens)
                if result['status'] == 'prompt_cap':
                    with COUNTS_LOCK:
                        counts['prompt_cap_ends'] += 1  # Truncation scored 0, counted separately.
            item = dict(prompt_ids=ids, **generation, tactic=tactic, state=states[i], **result)
            if search:
                item.update(chosen=search['chosen'], candidates=[
                    dict(probe, tactic=texts[i][probe['index']],
                         generated_tokens=generations[probe['index']]['generated_tokens'])
                    for probe in search['probes']])
            trajectories[i].append(item)
            if result['accepted']:
                with COUNTS_LOCK:
                    counts['accepted'] += 1
                if name not in solved:
                    solved.append(name)
            emit(dict(sample=i, step=step, **item))
            if result.get('censored'):
                censored = censored or result['status']
            if result['done']:
                finished.append(i)
            else:
                states[i] = envs[i].prompt_state()
        if censored:
            return trajectories, True, censored, root_automation, automation_done
        active = [i for i in active if i not in finished]
    if automation:
        eligible = [i for i in range(size) if trajectories[i] and not trajectories[i][-1]['done']]
        futures = {i: pool.submit(envs[i].try_automation, automation) for i in eligible}
        wait(futures.values())
        for i in eligible:
            automated = futures[i].result()
            if not automated:
                continue
            emit(dict(sample=i, step=len(trajectories[i]), automation=True, tactic=automated['tactic'], **automated['result']))
            if automated['result'].get('censored'):
                return trajectories, True, 'automation_' + automated['result']['status'], root_automation, automation_done
            if automated['result']['accepted']:
                automation_done[i] = True
                with COUNTS_LOCK:
                    counts['automation_accepted'] += 1
    return trajectories, False, None, root_automation, automation_done


def update(model, optimizer, trajectories, torch, autocast, gamma=1.0, checkpointing=False):
    """Stream one action's backward pass; hold no graph across a Lean call.

    With checkpointing, Hugging Face recomputes activations only in train mode; main() enables it
    only for models without dropout, so train mode computes the same function as eval mode."""
    updater = PolicyGradientUpdater(model, optimizer, torch, autocast,
        advantages=lambda rewards: advantages(rewards, gamma), action_logprob=action_logprob)
    return updater.update(trajectories, checkpointing)


def validate_config(config):
    """Validate settings with the shared parser and the existing scorer rules."""
    return validate_run_config(config, forbidden=forbidden)


def lean_reservation(size, depth, per_action, setup_calls, replay_calls):
    """Worst-case Lean calls of one group: every action uses its full chunk, every trajectory replays."""
    return size * (depth * per_action + setup_calls + replay_calls)


def trajectory_seed(seed, task_id, visit, sample):
    """Independent, reproducible sampling stream per (task visit, sample), also across shards."""
    return int(hashlib.sha256(f'{seed}|{task_id}|{visit}|{sample}'.encode()).hexdigest()[:8], 16)


def file_sha256(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def parse_arguments():
    """Parse the command and reject incompatible data or evaluation modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'model', 'checker', 'environment',
                 'mathlib-identity', 'repl', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--pool', type=Path)
    parser.add_argument('--exclude-names', type=Path)
    parser.add_argument('--tasks', type=Path, help='Training tasks; required unless evaluating DEV')
    parser.add_argument('--bank', type=Path, help='Native file-context bank; requires --train, --eval and --exclude-files')
    parser.add_argument('--train', type=Path, action='append', default=[], help='Official train rows for bank context; repeat')
    parser.add_argument('--eval', type=Path, action='append', default=[], help='Official evaluation exports for file exclusion; repeat')
    parser.add_argument('--exclude-files', type=Path, help='Additional DEV/excluded mathlib files, one per line')
    parser.add_argument('--units', type=Path, help='Optional bank task IDs, one per line')
    parser.add_argument('--evaluation-names', type=Path, help='Frozen DEV subset of --exclude-names; disables all optimizer updates')
    parser.add_argument('--assessment-only', action='store_true', help='Assess frozen reset states without an optimizer or checkpoint export')
    parser.add_argument('--assessment-split', choices=('train', 'validation', 'test'),
                        help='Declared pool split for --assessment-only; defaults to train')
    parser.add_argument('--task-order', type=Path, help='Frozen JSON list of admitted training task IDs, including explicit repeats')
    args = parser.parse_args()
    if args.assessment_split is not None and (not args.assessment_only or args.bank):
        parser.error('assessment-split requires --assessment-only with pool tasks')
    if args.bank:
        if not (args.train and args.eval and args.exclude_files) or any(
                (args.pool, args.tasks, args.exclude_names, args.evaluation_names)):
            parser.error('Give --bank with --train, --eval and --exclude-files, without pool inputs')
    elif (not (args.pool and args.exclude_names) or bool(args.tasks) == bool(args.evaluation_names)
          or args.train or args.eval or args.exclude_files or args.units):
        parser.error('Give --pool, --exclude-names and exclusively --tasks or --evaluation-names')
    if args.evaluation_names and (args.assessment_only or args.task_order):
        parser.error('DEV evaluation cannot use training assessment or task schedules')
    return args


def check_group_budget(config, counts, *, native_bank):
    """Check the worst-case cost of a whole group before sampling it."""
    group_size = config['group_size']
    episode_steps = config['max_episode_steps']
    candidates = config.get('step_candidates', 1)
    token_reservation = group_size * episode_steps * candidates * config['max_action_tokens']
    if counts['generated_tokens'] + token_reservation > config['max_generated_tokens']:
        raise BudgetReached('token_cap')

    setup_calls = 6 if native_bank else 5
    if config.get('environment_version', 1) == 2:
        calls_per_action = config.get('max_chunk_tactics', 4)
        replay_calls = 2
    else:
        calls_per_action = 1
        replay_calls = 0

    policy_calls = lean_reservation(
        group_size, episode_steps * candidates, calls_per_action, setup_calls, replay_calls)
    # Probe each fallback once at the root and once per attempt, including replay.
    automation = config.get('automation_fallback', [])
    automation_calls = (group_size + 1) * len(automation) * (1 + replay_calls)
    if counts['lean_calls'] + policy_calls + automation_calls > config['max_lean_calls']:
        raise BudgetReached('lean_call_cap')


def save_periodic_checkpoint(output, model, tokenizer, optimizer, torch, *, group, counts):
    """Replace the rolling checkpoint after saving its weights and optimizer."""
    staging = output / 'checkpoint.tmp'
    model.save_pretrained(staging)
    tokenizer.save_pretrained(staging)
    torch.save(optimizer.state_dict(), staging / 'optimizer.pt')
    metadata = dict(groups=counts['groups'], group=group, optimizer_updates=counts['optimizer_updates'])
    (staging / 'checkpoint.json').write_text(json.dumps(metadata) + '\n')
    checkpoint = output / 'checkpoint'
    if checkpoint.exists():
        shutil.rmtree(checkpoint)
    staging.rename(checkpoint)


def save_retained_checkpoint(output, model, tokenizer, *, group, counts):
    """Keep a named weights/tokenizer snapshot without optimizer state."""
    checkpoint = output / f"checkpoint_g{counts['groups']}"
    staging = checkpoint.with_name(checkpoint.name + '.tmp')
    model.save_pretrained(staging)
    tokenizer.save_pretrained(staging)
    metadata = dict(groups=counts['groups'], group=group, optimizer_updates=counts['optimizer_updates'])
    (staging / 'checkpoint.json').write_text(json.dumps(metadata) + '\n')
    staging.rename(checkpoint)
    return checkpoint.name


def main():
    args = parse_arguments()
    config = json.loads(args.config.read_text())
    validate_config(config)
    validate_environment_settings(config)
    reward_mode = config.get('reward_mode', 'goal_change_v1')
    max_repair_attempts = config.get('max_repair_attempts', 0)
    version = config.get('environment_version', 1)
    gamma = config.get('discount', 1.0)
    chunk = dict(max_tactics=config.get('max_chunk_tactics', 4), backtrack_owner=config.get('backtrack_owner', 'harness'),
                 backtrack_after=config.get('harness_backtrack_after', 2)) if version == 2 else {}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    started = time.monotonic()
    counts = dict(generated_tokens=0, retained_tokens=0, lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0,
                  trajectories=0, accepted=0, groups=0, optimizer_updates=0, censored_groups=0, prompt_cap_ends=0)
    run = dict(status='starting', config=config, counts=counts, experiment_id=config.get('experiment_id', 'EXP-019'),
               run_id=config['run_id'], environment_version=version, discount=gamma, **chunk,
               action_format='comment_tactic_chunk_v2' if version == 2 else 'raw_or_single_closed_lean_fence_v1',
               reward_mode=reward_mode,
               max_repair_attempts=max_repair_attempts,
               reward_components={'terminal': 'Lean-verified',
                                  'error_penalties' if reward_mode == 'error_feedback_v1' else 'exploration': 'heuristic'})
    candidates, automation = config.get('step_candidates', 1), config.get('automation_fallback', [])
    if candidates > 1 or automation:
        run.update(step_candidates=candidates, automation_fallback=automation)
    if automation:
        counts.update(automation_accepted=0, automation_root_solved=0)
        run['reward_components']['automation'] = 'Lean-verified; automation-completed proofs are counted apart from policy proofs'

    def save():
        run['wall_seconds'] = time.monotonic() - started
        run['gpu_seconds'] = run['wall_seconds'] if run.get('device') == 'cuda' else 0.0
        path = args.output / 'run.json'
        path.with_suffix('.tmp').write_text(json.dumps(run, indent=2) + '\n')
        path.with_suffix('.tmp').replace(path)

    def alarm(*_):
        raise BudgetReached('wall_cap')

    def terminated(*_):
        raise BudgetReached('terminated')

    signal.signal(signal.SIGALRM, alarm)
    signal.signal(signal.SIGTERM, terminated)  # Service stop or Spot interruption still saves run.json.
    signal.alarm(config['max_wall_seconds'])
    ctx = executor = None
    try:
        pool = []
        if args.pool:
            with args.pool.open() as stream:
                pool = [json.loads(line) for line in stream]
        excluded = set(args.exclude_names.read_text().split()) if args.exclude_names else set()
        rows, eval_spans = [], {}
        dev_evaluation = args.evaluation_names is not None
        assessment_split = args.assessment_split or 'train'
        mode = execution_mode(dev_evaluation, args.assessment_only, assessment_split)
        evaluating = mode != 'training'
        check_mode_settings(config, evaluating)
        if args.bank:
            for path in args.eval:
                with zipfile.ZipFile(path) as archive:
                    for line in archive.open(archive.namelist()[0]):
                        row = json.loads(line)
                        eval_spans.setdefault(row['file_path'], []).append(
                            (row['start'][0], row['end'][0], row['declaration_header']))
            rows = [row for path in args.train for row in load(path)]
            tasks = bank_tasks([json.loads(line) for line in args.bank.open()], rows,
                               set(args.exclude_files.read_text().split()), eval_spans)
            if args.units:
                wanted = set(args.units.read_text().split())
                if not wanted <= {task['task_id'] for task in tasks}:
                    raise ValueError('Requested bank task ID is missing')
                tasks = [task for task in tasks if task['task_id'] in wanted]
        elif dev_evaluation:
            tasks = evaluation_tasks(pool, set(args.evaluation_names.read_text().split()), excluded)
        else:
            with args.tasks.open() as stream:
                task_rows = [json.loads(line) for line in stream]
            if args.assessment_only:
                tasks = assessment_tasks(task_rows, pool, excluded, config['curriculum_max_lines_remaining'], assessment_split)
            else:
                tasks = training_tasks(task_rows, pool, excluded, config['curriculum_max_lines_remaining'])
        if not tasks:
            raise ValueError('Empty training curriculum')
        if config.get('task_filter'):
            tasks, run['task_filter'] = pass_rate_filter(tasks, config['task_filter'])
        run['mode'] = mode
        run['assessment_split'] = assessment_split if args.assessment_only else None
        run['population'] = [t['name'] for t in tasks] if evaluating else None
        run['solved_names'] = []
        population_file = 'evaluation_task_ids.json' if evaluating else 'training_task_ids.json'
        (args.output / population_file).write_text(json.dumps([t['task_id'] for t in tasks]) + '\n')
        input_names = ['config', 'checker', 'mathlib_identity']
        if args.bank:
            input_names += ['bank', 'exclude_files'] + (['units'] if args.units else [])
        else:
            input_names += ['pool', 'exclude_names', 'evaluation_names' if dev_evaluation else 'tasks']
        planned_groups = len(tasks) if evaluating else config['max_groups']
        if args.task_order:
            input_names.append('task_order')
            schedule = json.loads(args.task_order.read_text())
            planned, planned_groups = schedule, config['max_groups']
            if config.get('task_filter'):
                if len(schedule) != config['max_groups']:
                    raise ValueError('Task schedule must contain exactly max_groups entries')
                planned = filter_schedule(schedule, {t['task_id'] for t in tasks})
                planned_groups = len(planned)
                run['filtered_task_order'] = planned
            order = scheduled_tasks(tasks, planned, planned_groups)
            run['planned_task_order'] = schedule
            (args.output / 'planned_task_order.json').write_text(json.dumps(schedule) + '\n')
        else:
            order = iter(tasks) if evaluating else task_order(tasks, config['seed'])
        sampler = None
        if config.get('task_sampler', 'fixed') == 'band':
            if evaluating or args.task_order:
                raise ValueError('The band sampler is a training sampler; it excludes evaluation and frozen schedules')
            if any('family' not in t for t in tasks):
                raise ValueError('The band sampler needs a family label on every training task')
            import band_sampler
            sampler = band_sampler.BandSampler(tasks, config['group_size'], config['seed'], config.get('band_cap_ratio', 1.5))
            run['task_sampler'] = 'band'
            input_names.append('task_sampler_module')
            args.task_sampler_module = Path(band_sampler.__file__)
        entry = lambda path: dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())  # noqa: E731
        run['inputs'] = {k: entry(getattr(args, k)) for k in input_names}
        for kind in ('train', 'eval'):
            for index, path in enumerate(getattr(args, kind)):
                run['inputs'][f'{kind}_{index}'] = entry(path)
        premise_index = None
        if config.get('premise_retrieval'):
            from rl_components.premises import load_index
            corpus = Path(config['premise_retrieval']['corpus'])
            premise_index = load_index(corpus, config['premise_retrieval']['k'], {t['name'] for t in tasks})
            run['inputs']['premise_corpus'] = entry(corpus)
        run['model_source'] = str(args.model)
        run['model_sha256'] = file_sha256(args.model / 'model.safetensors')
        run['script_sha256'] = file_sha256(__file__)
        run['component_sha256'] = component_fingerprints()
        run['repl_sha256'] = file_sha256(args.repl)
        mathlib = args.environment / 'mathlib'
        run['lean'] = (mathlib / 'lean-toolchain').read_text().strip()
        run['mathlib_source_identity'] = mathlib_identity(args.mathlib_identity, config['mathlib'])
        run['mathlib'] = run['mathlib_source_identity']['mathlib_revision']
        lean_version = subprocess.run([str(args.environment / 'lean-4.10.0-rc1-linux/bin/lean'), '--version'],
                                 check=True, capture_output=True, text=True)
        run['lean_binary_version'] = lean_version.stdout.strip()
        if run['lean'] != config['lean'] or '4.10.0-rc1' not in run['lean_binary_version']:
            raise RuntimeError('Lean/mathlib pair differs from config')
        checker = load_checker(args.checker, args.environment, args.output)
        ctx = repl_context(checker, args.environment.resolve(), args.repl, args.output,
                           config['lean_timeout_seconds'], config['lean_setup_timeout_seconds'], 32)
        if args.bank:
            theorems = {task['theorem_id'] for task in tasks}
            ctx.rows = {row['theorem_id']: row for row in rows if row['theorem_id'] in theorems}
            ctx.eval_spans = eval_spans
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig, set_seed
        set_seed(config['seed'])
        torch.set_num_threads(config['cpu_threads'])
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        run.update(device=device, versions={p: importlib.metadata.version(p) for p in ('torch', 'transformers')},
                   device_name=torch.cuda.get_device_name(0) if device == 'cuda' else 'cpu')
        model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.float32).to(device)
        model.eval()  # Dropout must match generation during differentiable replay.
        tokenizer = AutoTokenizer.from_pretrained(args.model)
        # A fresh config drops checkpoint sampling warpers (min_p, n-gram bans, ...) that would make the
        # sampled distribution differ from the policy that update() differentiates.
        old = model.generation_config
        model.generation_config = GenerationConfig(eos_token_id=old.eos_token_id, pad_token_id=old.pad_token_id,
                                                   bos_token_id=old.bos_token_id, do_sample=True, temperature=1.0,
                                                   top_p=1.0, top_k=0, repetition_penalty=1.0)
        end_turn = tokenizer.convert_tokens_to_ids('<|im_end|>')
        stops = old.eos_token_id if isinstance(old.eos_token_id, list) else [old.eos_token_id]
        if end_turn is not None and end_turn != tokenizer.unk_token_id and end_turn not in stops:
            raise RuntimeError('The end-of-turn token does not stop generation')
        run['generation_stop_ids'] = stops
        optimizer = None if evaluating else torch.optim.AdamW(model.parameters(), lr=config['learning_rate'], weight_decay=0.0)
        checkpointing = bool(config.get('gradient_checkpointing', False)) and not evaluating
        if checkpointing:
            dropout = {k: v for k, v in model.config.to_dict().items() if 'dropout' in k and v}
            if dropout:
                raise RuntimeError('Gradient checkpointing needs train mode; refuse a model with dropout ' + str(dropout))
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
        run['gradient_checkpointing'] = checkpointing
        autocast = (lambda: torch.autocast('cuda', dtype=torch.bfloat16)) if device == 'cuda' else nullcontext

        def encode(content):
            text = tokenizer.apply_chat_template([{'role': 'user', 'content': content}],
                                                 tokenize=False, add_generation_prompt=True)
            return tokenizer(text, add_special_tokens=False)['input_ids']

        def observe(env):
            if version == 1:
                return encode(STEP.format(state=env.prompt_state()))
            for omit in range(len(env.shown) + 1):  # Hide the oldest policy steps only when needed.
                ids = encode(env.observation(omit))
                if len(ids) <= config['max_prompt_tokens']:
                    break
            return ids

        lockstep = config.get('rollout', 'sequential') == 'lockstep'
        if lockstep:
            from concurrent.futures import ThreadPoolExecutor
            # One thread per concurrently stepped episode; each episode and replay thread owns its Lean REPL.
            executor = ThreadPoolExecutor(max_workers=config.get('lean_workers', config['group_size']))
            if config.get('generation_backend', 'hf') == 'vllm':
                generator = VLLMGenerator(args.model, stops, tokenizer.pad_token_id, config['max_action_tokens'],
                                          config.get('vllm_memory_fraction', 0.4), config['seed'])
            else:
                generator = HFGenerator(model, tokenizer, torch, autocast, device, stops, config['max_action_tokens'], set_seed)
            decode = lambda ids: tokenizer.decode(ids, skip_special_tokens=True)  # noqa: E731
            run.update(rollout='lockstep', generation_backend=config.get('generation_backend', 'hf'),
                       lean_workers=config.get('lean_workers', config['group_size']),
                       sampling_seeds='per (task visit, sample, step) request seed' if config.get('generation_backend') == 'vllm'
                       else 'one stream per batched step, seeded from the active samples')
        run.update(status='running', phase='idle')
        save()
        with (args.output / 'rollouts.jsonl').open('x') as stream, (args.output / 'steps.jsonl').open('x') as steps:
            groups = planned_groups
            visits = {}
            for group in range(groups):
                size, depth = config['group_size'], config['max_episode_steps']
                check_group_budget(config, counts, native_bank=bool(args.bank))
                task = sampler.next_task() if sampler else next(order)
                visit = visits.get(task['task_id'], 0)
                visits[task['task_id']] = visit + 1
                trajectories, censored = [], False
                root_automation, automation_done = None, []
                run['phase'] = 'sampling'
                if not lockstep:
                    env = LeanEpisode(ctx, task, args.output, counts, reward_mode=reward_mode,
                                      max_repair_attempts=max_repair_attempts, version=version, horizon=depth,
                                      premises=premise_index, **chunk)
                    try:
                        for sample in range(size):
                            set_seed(trajectory_seed(config['seed'], task['task_id'], visit, sample))
                            try:
                                state, trajectory = env.reset(), []
                            except (TimeoutError, EOFError, OSError, RuntimeError) as exc:
                                censored = True  # Setup failure is infrastructure or data, never a policy outcome.
                                run['last_censor_reason'] = 'setup_failure: ' + str(exc)[:200]
                                break
                            if automation and sample == 0:
                                # Automation alone at the root is the baseline for automation-completed proofs.
                                root_automation = env.try_automation(automation, commit=False)
                                if root_automation and root_automation['result'].get('censored'):
                                    censored = True  # A crashed REPL is infrastructure.
                                    run['last_censor_reason'] = 'automation_root_' + root_automation['result']['status']
                                    break
                                if root_automation and not root_automation['result']['accepted']:
                                    # A timed-out probe is a failed automation outcome. Its REPL closed, so rebuild the root.
                                    try:
                                        state = env.reset()
                                    except (TimeoutError, EOFError, OSError, RuntimeError) as exc:
                                        censored = True
                                        run['last_censor_reason'] = 'setup_failure: ' + str(exc)[:200]
                                        break
                                elif root_automation:
                                    counts['automation_root_solved'] += 1
                            pending = None
                            for step in range(depth):
                                prompt_ids = pending or observe(env)
                                pending = None
                                if len(prompt_ids) > config['max_prompt_tokens']:
                                    censored = True
                                    run['last_censor_reason'] = 'prompt_cap'
                                    break
                                inputs = torch.tensor(prompt_ids, device=device)[None]
                                with torch.no_grad(), autocast():
                                    rows = model.generate(input_ids=inputs, attention_mask=torch.ones_like(inputs),
                                        do_sample=True, temperature=1.0, top_p=1.0, top_k=0, repetition_penalty=1.0,
                                        max_new_tokens=config['max_action_tokens'], pad_token_id=tokenizer.eos_token_id,
                                        num_return_sequences=candidates)
                                generations = [generation_record(stop_trimmed(row[len(prompt_ids):].tolist(), stops),
                                                                 stops, tokenizer.pad_token_id) for row in rows]
                                for generation in generations:
                                    for key in ('generated_tokens', 'retained_tokens'):
                                        counts[key] += generation[key]
                                    if generation['pad_before_eos']:
                                        stream.write(json.dumps(dict(group=group, sample=sample, step=step,
                                            task_id=task['task_id'], kind=task['kind'], prompt_ids=prompt_ids,
                                            status='generation_contract_violation', reward=None, accepted=False,
                                            done=True, censored=True, **generation)) + '\n')
                                        stream.flush()
                                        save()
                                        raise RuntimeError('Sampled PAD before EOS; refuse an unmatched training update')
                                texts = [tokenizer.decode(g['action_ids'], skip_special_tokens=True) for g in generations]
                                if candidates == 1:
                                    search, generation, tactic = None, generations[0], texts[0]
                                    result = env.step(tactic, config['exploration_bonus'] / depth)
                                else:
                                    search = env.search_step(texts)
                                    generation, tactic = generations[search['chosen']], texts[search['chosen']]
                                    result = search['result']
                                if not result['done']:
                                    pending = observe(env)
                                    result = bound_state(result, len(pending), config['max_prompt_tokens'])
                                    if result['status'] == 'prompt_cap':
                                        counts['prompt_cap_ends'] += 1  # Truncation scored 0, counted separately.
                                item = dict(prompt_ids=prompt_ids, **generation, tactic=tactic, state=state, **result)
                                if search:
                                    item.update(chosen=search['chosen'], candidates=[
                                        dict(probe, tactic=texts[probe['index']],
                                             generated_tokens=generations[probe['index']]['generated_tokens'])
                                        for probe in search['probes']])
                                trajectory.append(item)
                                if result['accepted']:
                                    counts['accepted'] += 1
                                    if task['name'] not in run['solved_names']:
                                        run['solved_names'].append(task['name'])
                                stream.write(json.dumps(dict(group=group, sample=sample, step=step, task_id=task['task_id'],
                                                             kind=task['kind'], **item), ensure_ascii=False) + '\n')
                                stream.flush()
                                save()
                                if result.get('censored'):
                                    censored = True
                                    run['last_censor_reason'] = result['status']
                                if result['done']:
                                    break
                                state = env.prompt_state()
                            automated = None
                            if automation and not censored and trajectory and not trajectory[-1]['done']:
                                automated = env.try_automation(automation)
                                if automated:
                                    stream.write(json.dumps(dict(group=group, sample=sample, step=len(trajectory),
                                        task_id=task['task_id'], kind=task['kind'], automation=True,
                                        tactic=automated['tactic'], **automated['result']), ensure_ascii=False) + '\n')
                                    stream.flush()
                                    if automated['result'].get('censored'):
                                        censored = True
                                        run['last_censor_reason'] = 'automation_' + automated['result']['status']
                                    elif automated['result']['accepted']:
                                        counts['automation_accepted'] += 1
                            trajectories.append(trajectory)
                            automation_done.append(bool(automated and automated['result']['accepted']))
                            if trajectory:
                                counts['trajectories'] += 1
                            if censored:
                                break
                    finally:
                        env.close()
                else:
                    envs = [LeanEpisode(ctx, task, args.output, counts, reward_mode=reward_mode,
                                        max_repair_attempts=max_repair_attempts, version=version, horizon=depth,
                                        premises=premise_index, repl_log=f'episodes_s{i}.log', **chunk) for i in range(size)]

                    def emit(record):
                        stream.write(json.dumps(dict(group=group, task_id=task['task_id'], kind=task['kind'], **record),
                                                ensure_ascii=False) + '\n')
                        stream.flush()
                        save()
                    try:
                        trajectories, censored, reason, root_automation, automation_done = lockstep_group(
                            envs, generator, decode, observe, executor, depth=depth, candidates=candidates,
                            automation=automation, max_prompt_tokens=config['max_prompt_tokens'],
                            bonus=config['exploration_bonus'] / depth,
                            seeds=lambda i, step: step_seed(config['seed'], task['task_id'], visit, i, step),
                            emit=emit, counts=counts, solved=run['solved_names'], name=task['name'])
                    finally:
                        for episode in envs:
                            episode.close()
                    if censored:
                        run['last_censor_reason'] = reason
                    counts['trajectories'] += sum(1 for path in trajectories if path)
                if censored:
                    counts['censored_groups'] += 1
                    steps.write(json.dumps(dict(group=group, status='censored', reason=run['last_censor_reason'])) + '\n')
                    steps.flush()
                    continue  # Never optimize a partially sampled group.
                if evaluating:
                    result = dict(loss=None, gradient_norm=None, optimizer_updated=False)
                else:
                    run['phase'] = 'update'
                    result = update(model, optimizer, trajectories, torch, autocast, gamma, checkpointing)
                counts['groups'] += 1
                counts['optimizer_updates'] += result['optimizer_updated']
                if sampler:  # Lean-verified successes only; censored groups never reach this line.
                    sampler.observe(task, sum(any(s['accepted'] for s in path) for path in trajectories))
                    result = dict(result, sampler=sampler.state())
                run['phase'] = 'idle'
                # Policy returns exclude automation; automation outcomes stay in their own fields.
                extra = dict(automation_accepted=automation_done,
                             automation_root=root_automation['tactic'] if root_automation and root_automation['result']['accepted'] else None,
                             automation_root_status=root_automation['result']['status'] if root_automation else None) if automation else {}
                steps.write(json.dumps(dict(group=group, task_id=task['task_id'], kind=task['kind'],
                    returns=[sum(s['reward'] for s in path) for path in trajectories],
                    discounted_returns=[sum(s['reward'] * gamma ** k for k, s in enumerate(path)) for path in trajectories],
                    n_steps=[len(path) for path in trajectories],
                    # Penalties can make a proved episode's return zero or negative; count proofs from this field.
                    accepted=[any(s['accepted'] for s in path) for path in trajectories], **result, **extra)) + '\n')
                steps.flush()
                every = config.get('checkpoint_every', 0)
                if every and not evaluating and counts['groups'] % every == 0:
                    run['phase'] = 'checkpoint'
                    save_periodic_checkpoint(args.output, model, tokenizer, optimizer, torch,
                                             group=group, counts=counts)
                    run.update(phase='idle', last_checkpoint_groups=counts['groups'])
                if not evaluating and counts['groups'] in config.get('checkpoint_keep', []):
                    # Kept snapshots (for example the midpoint) are never deleted; weights and tokenizer only.
                    run['phase'] = 'checkpoint'
                    name = save_retained_checkpoint(args.output, model, tokenizer, group=group, counts=counts)
                    run.setdefault('kept_checkpoints', []).append(name)
                    run['phase'] = 'idle'
                save()
        run['status'] = 'completed'
    except BudgetReached as exc:
        run.update(status='capped', stop_reason=str(exc),
                   partial_group_optimized='unknown' if run.get('phase') == 'update' else False)
    except BaseException as exc:
        run.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        signal.alarm(0)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if ctx is not None:
            for repl in ctx.pool_repls:
                close_repl(repl)
        save()
    if can_export(run, counts['optimizer_updates']):
        model.save_pretrained(args.output / 'final')
        tokenizer.save_pretrained(args.output / 'final')
        torch.save(optimizer.state_dict(), args.output / 'optimizer.pt')
        run['checkpoint'] = 'final'
        save()
    print(json.dumps(run))


if __name__ == '__main__':
    main()
