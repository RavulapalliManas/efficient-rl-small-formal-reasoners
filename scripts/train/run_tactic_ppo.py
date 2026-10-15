"""Pinned library PPO for one tactic block, with the REINFORCE Lean episode contract.

No interaction after the first action. Policy/critic/reference weights use
3 x 0.5e9 x 4 bytes; gradients and Adam moments add 12 GB. Reserve 12 GB
activations and one Lean process x 4 GiB. Use the established 56 GiB host cap.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_tactic_rl import LeanEpisode, STEP, mathlib_identity, scheduled_tasks  # noqa: E402
from run_lean_grpo import task_units  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from bench_lean_verifier import repl_context  # noqa: E402


def checked_state(env, task):
    state = env.reset()
    if state != task['state']:
        raise ValueError('Verified root state differs from frozen task: ' + task['task_id'])
    return state


def prompt_rows(tasks, tokenizer, maximum):
    rows, queries = [], {}
    for task in tasks:
        text = tokenizer.apply_chat_template([{'role': 'user', 'content': STEP.format(state=task['state'])}],
                                             tokenize=False, add_generation_prompt=True)
        tokens = tuple(tokenizer(text, add_special_tokens=False)['input_ids'])
        if len(tokens) > maximum:
            raise ValueError('Registered root prompt exceeds token cap')
        if queries.setdefault(tokens, task['task_id']) != task['task_id']:
            raise ValueError('ambiguous tokenized root query')
        rows.append(dict(prompt=text, task_id=task['task_id'], prompt_tokens=len(tokens)))
    return rows


def ordered_prompt_rows(tasks, rows, maximum, schedule=None):
    if schedule is None:
        return [rows[i % len(rows)] for i in range(maximum)]
    by_id = {row['task_id']: row for row in rows}
    return [by_id[task['task_id']] for task in scheduled_tasks(tasks, schedule, maximum)]


def one_action_score(ctx, by_id, task_ids, completions, executor):
    verdicts = []
    for task_id, text in zip(task_ids, completions):
        counts = dict(lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0)
        env = LeanEpisode(ctx, by_id[task_id], ctx.output, counts,
                          reward_mode='goal_change_v1', max_repair_attempts=0)
        try:
            checked_state(env, by_id[task_id])
            result = env.step(text, 0.0)
            verdicts.append(dict(counts, status='accepted' if result['accepted'] else 'rejected',
                                 episode_result=result))
        finally:
            env.close()
    return completions, {}, verdicts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'model', 'pool', 'tasks', 'exclude-names', 'checker', 'environment',
                 'mathlib-identity', 'repl', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--task-order', type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    expected = dict(interaction_contract='one_action', temperature=1.0, beta=0.0, verifier_workers=1,
                    prompts_per_step=1, top_p=1.0, top_k=0, repetition_penalty=1.0,
                    use_vllm=False, shuffle_rewards=False)
    if any(config.get(k) != v for k, v in expected.items()) or any(
            config.get(k, 0) != 0 for k in ('exploration_bonus', 'max_repair_attempts')):
        raise ValueError('One-action PPO requires unwarped sampling, binary reward and no repair')
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    with args.pool.open() as stream:
        pool = [json.loads(line) for line in stream]
    with args.tasks.open() as stream:
        tasks = [json.loads(line) for line in stream]
    task_units(tasks, pool)
    excluded = set(args.exclude_names.read_text().split())
    if not tasks or any(t['name'] in excluded or t['kind'] not in ('whole', 'prefix_top') for t in tasks):
        raise ValueError('Training input is empty, excluded, or outside the registered task kinds')
    identity = mathlib_identity(args.mathlib_identity, config['mathlib'])
    checker = load_checker(args.checker, args.environment, args.output)
    version = subprocess.run([str(checker.LEAN / 'lean'), '--version'], check=True, capture_output=True, text=True).stdout.strip()
    pin = (args.environment / 'mathlib/lean-toolchain').read_text().strip()
    if pin != config['lean'] or '4.10.0-rc1' not in version:
        raise ValueError('Lean/mathlib pair differs from config')
    ctx = repl_context(checker, args.environment.resolve(), args.repl, args.output,
                       config['lean_timeout_seconds'], config['lean_setup_timeout_seconds'], 32)
    try:
        from datasets import Dataset
        from transformers import AutoTokenizer
        from lean_ppo import run_ppo
        tokenizer = AutoTokenizer.from_pretrained(args.model)
        rows = prompt_rows(tasks, tokenizer, config['max_prompt_tokens'])
        prep = dict(lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0)
        for task in tasks:
            env = LeanEpisode(ctx, task, args.output, prep, max_repair_attempts=0)
            try:
                checked_state(env, task)
                if prep['lean_calls'] >= config['max_lean_calls'] or time.monotonic() - started >= config['max_wall_seconds']:
                    raise ValueError('Preparation exhausted the registered resource cap')
            finally:
                env.close()
        schedule = json.loads(args.task_order.read_text()) if args.task_order else None
        order = ordered_prompt_rows(tasks, rows, config['max_steps'], schedule)
        run = dict(status='running', config=config, interaction_contract='one_action', preparation_counts=prep,
                   preparation_wall_seconds=time.monotonic() - started, lean=pin, lean_binary_version=version,
                   mathlib_source_identity=identity, model_source=str(args.model),
                   prompt_counts={r['task_id']: r['prompt_tokens'] for r in rows},
                   planned_task_order=[r['task_id'] for r in order],
                   inputs={k: dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                           for k, p in vars(args).items() if p is not None and k not in ('model', 'environment', 'repl', 'output')})
        (args.output / 'run.json').write_text(json.dumps(run, indent=2) + '\n')
        remaining = dict(config, max_lean_calls=config['max_lean_calls'] - prep['lean_calls'],
                         max_wall_seconds=config['max_wall_seconds'] - (time.monotonic() - started))
        run_ppo(args, remaining, ctx, {t['task_id']: t for t in tasks}, Dataset.from_list(order),
                tokenizer, run, score_fn=one_action_score)
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()


if __name__ == '__main__':
    main()
