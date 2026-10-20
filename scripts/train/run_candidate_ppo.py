"""PPO-Clip updates for complete Lean candidate-selection episodes.

Uses the shared LM categorical actor, frozen menus and observation-only critic.
This is a categorical PPO implementation, not an extension of TRL's token
rollouts. No candidate screening or generation occurs during optimizer epochs.
"""
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candidate_policy import discounted_returns, masked_distribution, validate_episode  # noqa: E402


def clipped_surrogate(new_log_prob, old_log_prob, advantage, clip_ratio):
    """Scalar reference for the independently checked tensor objective."""
    ratio = math.exp(new_log_prob - old_log_prob)
    return min(ratio * advantage, max(1 - clip_ratio, min(1 + clip_ratio, ratio)) * advantage)


def validate_candidate_config(config, algorithm):
    """Reject missing pins and invalid bounds before loading a model or Lean."""
    if algorithm not in ('ppo', 'reinforce', 'frozen_lm', 'uniform'):
        raise ValueError('Unknown candidate algorithm')
    integers = ('max_episodes', 'episodes_per_update', 'max_candidates', 'horizon', 'max_lean_calls',
                'max_scoring_tokens', 'max_prompt_tokens', 'max_action_tokens')
    if algorithm == 'ppo':
        integers += ('num_ppo_epochs',)
    for key in integers:
        if type(config.get(key)) is not int or config[key] < 1:
            raise ValueError('Positive integer required: ' + key)
    positive = ('max_wall_seconds', 'lean_timeout_seconds', 'lean_setup_timeout_seconds')
    if algorithm in ('ppo', 'reinforce'):
        positive += ('learning_rate',)
    for key in positive:
        value = config.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Positive finite number required: ' + key)
    if type(config.get('seed')) is not int or config['seed'] < 0:
        raise ValueError('Nonnegative integer seed required')
    if config['episodes_per_update'] < 2 or config['max_episodes'] % config['episodes_per_update']:
        raise ValueError('Use complete groups of at least two episodes')
    for key in ('lean', 'mathlib', 'experiment_id', 'run_id'):
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError('Required config value: ' + key)
    if config.get('interaction_contract') != 'candidate_selection_v1' or config.get('split') != 'train':
        raise ValueError('Candidate driver requires the registered TRAIN candidate-selection contract')
    for key, default, lower, upper in (
            ('gamma', 1., 0., 1.), ('clip_ratio', .2, 0., 1.),
            ('value_coef', .5, 0., math.inf), ('entropy_coef', 0., 0., math.inf),
            ('max_grad_norm', 1., 0., math.inf), ('target_kl', .03, 0., math.inf)):
        value = config.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError('Invalid optimizer bound: ' + key)
        if key in ('clip_ratio', 'max_grad_norm', 'target_kl') and value <= 0 or key == 'clip_ratio' and value >= 1:
            raise ValueError('Invalid optimizer bound: ' + key)


def update_ppo(policy, optimizer, episodes, *, gamma=1., clip_ratio=.2, value_coef=.5,
               entropy_coef=0., epochs=4, max_grad_norm=1., target_kl=.03):
    """Fixed reward-to-go targets minus old values; one streamed batch per epoch.

    No advantage normalization: a singleton rollout must retain its signal.
    Clipping bounds the surrogate, not policy divergence. A positive target_kl
    stops further epochs after the sampled approximate KL exceeds that bound.
    """
    import torch
    from candidate_policy import WorkCapReached
    if (not episodes or not 0 < clip_ratio < 1 or epochs < 1 or value_coef < 0
            or entropy_coef < 0 or max_grad_norm <= 0 or target_kl <= 0):
        raise ValueError('Invalid PPO batch or settings')
    rows = []
    for episode in episodes:
        validate_episode(episode)
        rows.extend(zip(episode.transitions, discounted_returns(episode, gamma)))
    if not rows:
        return dict(updates=0, transitions=0, episodes=len(episodes), status='no_actions')
    parameters = list(policy.parameters())
    metrics = dict(updates=0, transitions=len(rows), episodes=len(episodes), status='complete')
    optimizer.zero_grad(set_to_none=True)
    try:
        for epoch in range(epochs):
            total_policy, total_value, total_kl = 0., 0., 0.
            for step, target in rows:
                logits, value = policy.evaluate(step.support)
                dist = masked_distribution(logits, step.support.mask)
                action = torch.tensor(step.decision.action_index, device=logits.device)
                log_prob = dist.log_prob(action)
                if epoch == 0 and not math.isclose(log_prob.item(), step.decision.old_log_prob,
                                                  rel_tol=1e-5, abs_tol=1e-5):
                    raise ValueError('Old log-probability differs on the frozen support')
                advantage = target - step.decision.old_value
                ratio = (log_prob - step.decision.old_log_prob).exp()
                actor = -torch.minimum(ratio * advantage,
                    ratio.clamp(1 - clip_ratio, 1 + clip_ratio) * advantage)
                value_loss = .5 * (value - target).square()
                loss = actor + value_coef * value_loss - entropy_coef * dist.entropy()
                if not torch.isfinite(loss):
                    raise FloatingPointError('Non-finite PPO loss')
                (loss / len(rows)).backward()
                delta = log_prob.detach() - step.decision.old_log_prob
                total_kl += (delta.exp() - 1 - delta).item() / len(rows)
                total_policy += actor.item() / len(rows)
                total_value += value_loss.item() / len(rows)
            if total_kl > target_kl:
                optimizer.zero_grad(set_to_none=True)
                metrics.update(status='target_kl', approximate_kl=total_kl)
                break
            torch.nn.utils.clip_grad_norm_(parameters, max_grad_norm, error_if_nonfinite=True)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            metrics.update(updates=metrics['updates'] + 1, policy_loss=total_policy,
                           value_loss=total_value, approximate_kl=total_kl)
    except WorkCapReached as error:
        optimizer.zero_grad(set_to_none=True)
        metrics.update(status='stopped', stop_reason=str(error))
    except BaseException:
        optimizer.zero_grad(set_to_none=True)
        raise
    return metrics


def run_training(policy, optimizer, episode_factory, tasks, candidates, config, output, *,
                 update_fn=update_ppo, algorithm='ppo', counts=None, metadata=None):
    """Collect repeated episodes from one root before either matched update.

    Stream raw screening and selected outcomes before optimization. A failed or
    interrupted group is retained but never enters an update. No eval split is
    read by this function. The caller owns task admission and process hard caps.
    """
    from dataclasses import asdict
    import json
    import time
    from candidate_policy import WorkCapReached, collect_episode
    group_size, maximum = config['episodes_per_update'], config['max_episodes']
    if not tasks or group_size < 2 or maximum < 1 or maximum % group_size:
        raise ValueError('Use nonempty tasks and complete groups of at least two episodes')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = dict(metadata or {}, status='running', algorithm=algorithm, config=config,
                  episodes=0, accepted=0, optimizer_updates=0, counts=counts if counts is not None else {})
    started = time.monotonic()
    pending = []
    try:
        with (output / 'rollouts.jsonl').open('x') as raw, (output / 'episodes.jsonl').open('x') as saved, \
                (output / 'steps.jsonl').open('x') as metrics:
            for index in range(maximum):
                task = tasks[(index // group_size) % len(tasks)]
                task_id = task.get('task_id') if isinstance(task, dict) else str(task)
                def record(event):
                    raw.write(json.dumps(dict(episode=index, task_id=task_id, **event)) + '\n')
                    raw.flush()
                env = episode_factory(task)
                try:
                    episode = collect_episode(env, policy, candidates, max_candidates=config['max_candidates'],
                        max_lean_calls=config['max_lean_calls'], max_steps=config['horizon'], record=record)
                except BaseException as error:
                    record(dict(kind='exception', error=type(error).__name__ + ': ' + str(error)))
                    raise
                finally:
                    env.close()
                saved.write(json.dumps(dict(episode=index, task_id=task_id, **asdict(episode))) + '\n')
                saved.flush()
                report['episodes'] += 1
                report['accepted'] += episode.status == 'accepted'
                if not episode.completed:
                    report.update(status='stopped', stop_reason=episode.status, discarded_group_episodes=len(pending) + 1)
                    break
                pending.append(episode)
                if len(pending) < group_size:
                    continue
                options = dict(gamma=config.get('gamma', 1.), max_grad_norm=config.get('max_grad_norm', 1.))
                if algorithm == 'ppo':
                    options.update(clip_ratio=config.get('clip_ratio', .2), value_coef=config.get('value_coef', .5),
                        entropy_coef=config.get('entropy_coef', 0.), epochs=config['num_ppo_epochs'],
                        target_kl=config.get('target_kl', .03))
                if algorithm in ('uniform', 'frozen_lm'):
                    for episode in pending:
                        validate_episode(episode)
                    result = dict(status='no_update_baseline', updates=0, episodes=len(pending),
                                  transitions=sum(len(episode.transitions) for episode in pending))
                else:
                    result = update_fn(policy, optimizer, pending, **options)
                metrics.write(json.dumps(dict(episode=index, task_id=task_id, **result)) + '\n')
                metrics.flush()
                report['optimizer_updates'] += result['updates']
                pending = []
                if result.get('status') == 'stopped':
                    report.update(status='stopped', stop_reason=result['stop_reason'])
                    break
            else:
                report.update(status='complete', stop_reason='requested_episodes')
    except WorkCapReached as error:
        report.update(status='stopped', stop_reason=str(error), discarded_group_episodes=len(pending))
    except BaseException as error:
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        report['training_wall_seconds'] = time.monotonic() - started
        (output / 'run.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(*, update_fn=update_ppo, algorithm='ppo'):
    """Explicit local-model TRAIN-only CLI; requires an approved remote runner.

    Python wall checks and SIGALRM interrupt action boundaries and blocking Lean
    calls. The established managed runner must enforce the process memory/GPU
    caps and hard termination deadline, including setup and checkpoint saving.
    """
    import argparse
    import hashlib
    import json
    import signal
    import subprocess
    import time
    parser = argparse.ArgumentParser(description=algorithm.upper() + ' over frozen Lean candidate menus.')
    for name in ('config', 'pool', 'tasks', 'candidates', 'exclude-names', 'checker',
                 'environment', 'mathlib-identity', 'repl', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True,
                        help='Local immutable model/tokenizer directory; uniform loads tokenizer files only')
    parser.add_argument('--baseline', choices=('frozen_lm', 'uniform'), help='Collect without optimizer updates')
    parser.add_argument('--device', choices=('cpu', 'cuda'), required=True)
    args = parser.parse_args()
    algorithm = args.baseline or algorithm
    config = json.loads(args.config.read_text())
    validate_candidate_config(config, algorithm)
    if args.model is None or not args.model.is_dir():
        raise ValueError('A local immutable model/tokenizer directory is required; no downloads')
    from candidate_policy import ScoringBudget, TorchCandidatePolicy, UniformCandidatePolicy, WorkCapReached
    from run_tactic_rl import LeanEpisode, mathlib_identity, training_tasks
    from prepare_grpo_pilot import load_checker
    from bench_lean_verifier import repl_context
    candidates = json.loads(args.candidates.read_text())
    if (not isinstance(candidates, list) or not 0 < len(candidates) <= config['max_candidates']
            or any(not isinstance(text, str) or not text.strip() for text in candidates)
            or len(set(candidates)) != len(candidates)):
        raise ValueError('Use a bounded nonempty catalogue of distinct tactic strings')
    tasks = [json.loads(line) for line in args.tasks.read_text().splitlines() if line.strip()]
    pool = [json.loads(line) for line in args.pool.read_text().splitlines() if line.strip()]
    excluded = set(args.exclude_names.read_text().split())
    tasks = training_tasks(tasks, pool, excluded, config.get('curriculum_max_lines_remaining', 1))
    if not tasks:
        raise ValueError('Empty training curriculum after the existing admission gate')
    identity = mathlib_identity(args.mathlib_identity, config['mathlib'])
    args.output.mkdir(parents=True, exist_ok=False)
    counts = dict(lean_calls=0, lean_seconds=0., lean_cpu_seconds=0., generated_tokens=0)
    budget = ScoringBudget(config['max_scoring_tokens'], config['max_wall_seconds'], counts)
    report = dict(status='starting', algorithm=algorithm, config=config, counts=counts,
                  experiment_id=config['experiment_id'], run_id=config['run_id'])
    ctx, policy, optimizer = None, None, None
    started = time.monotonic()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGALRM, signal.SIGTERM)}
    def interrupted(signum, _frame):
        raise WorkCapReached('wall_cap' if signum == signal.SIGALRM else 'terminated')
    signal.signal(signal.SIGALRM, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.alarm(math.ceil(config['max_wall_seconds']))
    try:
        checker = load_checker(args.checker, args.environment, args.output)
        version = subprocess.run([str(checker.LEAN / 'lean'), '--version'],
                                 check=True, capture_output=True, text=True).stdout.strip()
        pin = (args.environment / 'mathlib/lean-toolchain').read_text().strip()
        if pin != config['lean'] or '4.10.0-rc1' not in version:
            raise ValueError('Lean/mathlib pair differs from registered config')
        ctx = repl_context(checker, args.environment.resolve(), args.repl, args.output,
                           config['lean_timeout_seconds'], config['lean_setup_timeout_seconds'], 32)
        import torch
        from transformers import AutoTokenizer
        torch.manual_seed(config['seed'])
        tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
        if algorithm == 'uniform':
            policy = UniformCandidatePolicy(tokenizer, max_prompt_tokens=config['max_prompt_tokens'],
                max_action_tokens=config['max_action_tokens'], budget=budget)
        else:
            from transformers import AutoModelForCausalLM
            model = AutoModelForCausalLM.from_pretrained(args.model, local_files_only=True,
                                                        torch_dtype=torch.float32).to(args.device)
            policy = TorchCandidatePolicy(model, tokenizer, max_prompt_tokens=config['max_prompt_tokens'],
                max_action_tokens=config['max_action_tokens'], budget=budget)
            if algorithm == 'frozen_lm':
                for parameter in policy.parameters():
                    parameter.requires_grad_(False)
            else:
                optimizer = torch.optim.AdamW(policy.parameters(), lr=config['learning_rate'])
        inputs = {name: dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                  for name, path in vars(args).items() if isinstance(path, Path) and path.is_file()}
        sources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (
            Path(__file__), Path(__file__).with_name('candidate_policy.py'),
            Path(__file__).with_name('run_tactic_rl.py'), Path(__file__).with_name('run_candidate_reinforce.py'))
            if path.exists()}
        report.update(lean=pin, lean_binary_version=version, mathlib_source_identity=identity,
                      model_source=None if algorithm == 'uniform' else str(args.model),
                      tokenizer_source=str(args.model),
                      input_receipts=inputs, source_sha256=sources,
                      candidate_source='fixed caller-supplied TRAIN catalogue', proposal_generated_tokens=0,
                      planned_root_order=[tasks[(i // config['episodes_per_update']) % len(tasks)]['task_id']
                                          for i in range(config['max_episodes'])])
        def episode_factory(task):
            return LeanEpisode(ctx, task, args.output, counts, version=2, horizon=config['horizon'],
                               max_tactics=1, max_repair_attempts=0, backtrack_owner='harness')
        report = run_training(policy, optimizer, episode_factory, tasks, candidates, config, args.output,
                              update_fn=update_fn, algorithm=algorithm, counts=counts, metadata=report)
    except WorkCapReached as error:
        report.update(status='stopped', stop_reason=str(error))
    except BaseException as error:
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        try:
            if ctx is not None:
                for repl in list(ctx.pool_repls):
                    repl.close()
            if policy is not None and report['status'] == 'complete' and algorithm in ('ppo', 'reinforce'):
                import torch
                policy.model.save_pretrained(args.output / 'final')
                policy.tokenizer.save_pretrained(args.output / 'final')
                torch.save(policy.value_head.state_dict(), args.output / 'critic.pt')
                torch.save(optimizer.state_dict(), args.output / 'optimizer.pt')
        except BaseException as error:
            report.update(status='failed', error='cleanup/export: ' + type(error).__name__ + ': ' + str(error))
            raise
        finally:
            signal.alarm(0)
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            report.update(wall_seconds=time.monotonic() - started, counts=counts)
            (args.output / 'run.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    return report


if __name__ == '__main__':
    main()
