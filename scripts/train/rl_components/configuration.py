"""Configuration validation and training/evaluation mode restrictions."""
from .actions import parse_action


REQUIRED_KEYS = ('run_id', 'seed', 'lean', 'mathlib', 'group_size', 'max_groups', 'max_episode_steps',
                 'max_prompt_tokens', 'max_action_tokens', 'exploration_bonus', 'learning_rate', 'cpu_threads',
                 'lean_timeout_seconds', 'lean_setup_timeout_seconds', 'max_wall_seconds', 'max_generated_tokens',
                 'max_lean_calls', 'curriculum_max_lines_remaining')
V2_KEYS = ('discount', 'max_chunk_tactics', 'backtrack_owner', 'harness_backtrack_after')


def validate_config(config, *, forbidden):
    """Validate run settings using the supplied forbidden-code check.

    The runner supplies its existing scorer rule. This keeps configuration
    checks independent of the Lean runtime and the evaluation scripts.
    """
    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise ValueError('Config misses keys: ' + ', '.join(missing))
    version = config.get('environment_version', 1)
    if version == 1 and any(key in config for key in V2_KEYS):
        raise ValueError('Environment v1 config contains v2-only keys')
    if version == 2:
        if 'discount' not in config:
            raise ValueError('Environment v2 requires an explicit discount')
        for key in ('max_chunk_tactics', 'harness_backtrack_after'):
            value = config.get(key, 1)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(key + ' must be an integer of at least 1')
        if config.get('backtrack_owner', 'harness') not in ('harness', 'policy'):
            raise ValueError('backtrack_owner must be harness or policy')
    every = config.get('checkpoint_every', 0)
    if not isinstance(every, int) or every < 0:
        raise ValueError('checkpoint_every must be a non-negative integer')
    if not isinstance(config.get('gradient_checkpointing', False), bool):
        raise ValueError('gradient_checkpointing must be true or false')
    keep = config.get('checkpoint_keep', [])
    if not isinstance(keep, list) or any(isinstance(g, bool) or not isinstance(g, int) or g < 1 for g in keep):
        raise ValueError('checkpoint_keep must be a list of positive group counts')
    if config.get('task_sampler', 'fixed') not in ('fixed', 'band'):
        raise ValueError('task_sampler must be fixed or band')
    cap = config.get('band_cap_ratio', 1.5)
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap < 1:
        raise ValueError('band_cap_ratio must be a number of at least 1')
    candidates = config.get('step_candidates', 1)
    if isinstance(candidates, bool) or not isinstance(candidates, int) or candidates < 1:
        raise ValueError('step_candidates must be an integer of at least 1')
    automation = config.get('automation_fallback', [])
    if not isinstance(automation, list) or any(
            not isinstance(t, str) or forbidden(t) or parse_action(t, 1)['kind'] != 'tactics' for t in automation):
        raise ValueError('automation_fallback must be a list of single allowed tactics')
    if (candidates > 1 or automation) and version != 2:
        raise ValueError('Step search and automation fallback require environment v2')
    spec = config.get('task_filter')
    if spec is not None:
        if not isinstance(spec, dict) or set(spec) != {'pass_rates_from', 'min_exclusive', 'max_inclusive'}:
            raise ValueError('task_filter needs exactly pass_rates_from, min_exclusive and max_inclusive')
        low, high = spec['min_exclusive'], spec['max_inclusive']
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (low, high))
                or not 0 <= low < high <= 1):
            raise ValueError('task_filter needs 0 <= min_exclusive < max_inclusive <= 1')
    rollout = config.get('rollout', 'sequential')
    if rollout not in ('sequential', 'lockstep') or (rollout == 'lockstep' and version != 2):
        raise ValueError('rollout must be sequential, or lockstep with environment v2')
    workers = config.get('lean_workers', config['group_size'])
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError('lean_workers must be an integer of at least 1')
    backend = config.get('generation_backend', 'hf')
    if backend not in ('hf', 'vllm') or (backend == 'vllm' and rollout != 'lockstep'):
        raise ValueError('generation_backend must be hf, or vllm with lockstep rollout')
    fraction = config.get('vllm_memory_fraction', 0.4)
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0.05 <= fraction <= 0.9:
        raise ValueError('vllm_memory_fraction must lie in [0.05, 0.9]')
    retrieval = config.get('premise_retrieval')
    if retrieval is not None:
        if (version != 2 or not isinstance(retrieval, dict) or set(retrieval) != {'corpus', 'k'}
                or isinstance(retrieval['k'], bool) or not isinstance(retrieval['k'], int) or not 1 <= retrieval['k'] <= 16):
            raise ValueError('premise_retrieval needs environment v2, a corpus path and 1 <= k <= 16')


def check_mode_settings(config, evaluating):
    """Keep search, automation and task filtering in the mode where their measurement stays valid."""
    if not evaluating and (config.get('step_candidates', 1) > 1 or config.get('automation_fallback')):
        raise ValueError('Step search and automation fallback are evaluation and data-collection settings; '
                         'a selected or automation-completed action is not an on-policy REINFORCE sample')
    if evaluating and config.get('task_filter'):
        raise ValueError('task_filter selects training tasks; evaluation populations stay fixed')
    if not evaluating and config.get('generation_backend', 'hf') == 'vllm':
        raise ValueError('vLLM sampling is evaluation-only until a weight-sync path is validated on a GPU; '
                         'training samples must come from the live policy')


def validate_environment_settings(config):
    """Check proof rewards, repair limits, episode bounds, and the discount."""
    reward_mode = config.get('reward_mode', 'goal_change_v1')
    max_repair_attempts = config.get('max_repair_attempts', 0)
    if max_repair_attempts not in (0, 1):
        raise ValueError('Error repair allows zero or one retry per trajectory')
    if reward_mode not in ('goal_change_v1', 'error_feedback_v1'):
        raise ValueError('Unknown tactic reward mode')
    if reward_mode == 'error_feedback_v1' and config['exploration_bonus'] != 0:
        raise ValueError('Local verifier feedback requires zero goal-text exploration bonus')
    if config['group_size'] < 2 or not 0 <= config['exploration_bonus'] < 1 or config['max_episode_steps'] < 1:
        raise ValueError('Invalid group, episode, or shaping bound')
    version, gamma = config.get('environment_version', 1), config.get('discount', 1.0)
    if version not in (1, 2) or not 0 < gamma <= 1:  # validate_config already rejects a v1 discount.
        raise ValueError('Unknown environment version or invalid discount')
    if version == 2 and (config['exploration_bonus'] or max_repair_attempts):
        raise ValueError('Environment v2 has no goal-text bonus and no repair attempt')
