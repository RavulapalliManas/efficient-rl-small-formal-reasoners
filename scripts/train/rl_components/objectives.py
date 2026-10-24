"""The existing leave-one-out estimator and streamed policy-gradient update."""


def proof_rewards(verdicts):
    """Binary complete-proof rewards, refusing a batch with a censored verifier."""
    if any(v.get('censored') or v['status'] in ('crash', 'replay_crash') for v in verdicts):
        raise RuntimeError('Censored verifier outcome; no PPO update')
    return [float(v['status'] == 'accepted') for v in verdicts]


class LeaveOneOutObjective:
    """Discounted reward-to-go with each trajectory excluded from its baseline.

    Both rewards and baselines are discounted from step zero. gamma=1 retains
    the original undiscounted estimator. Equal terminal-only returns have
    zero advantage. Intermediate rewards can give later steps different
    advantages even when the total returns are equal.
    """

    def __init__(self, gamma=1.0):
        self.gamma = gamma

    def advantages(self, rewards):
        if len(rewards) < 2:
            raise ValueError('Leave-one-out requires at least two trajectories')
        discounted_returns = [
            sum(reward * self.gamma ** step for step, reward in enumerate(trajectory))
            for trajectory in rewards
        ]
        all_advantages = []
        for trajectory_index, trajectory in enumerate(rewards):
            other_returns = discounted_returns[:trajectory_index] + discounted_returns[trajectory_index + 1:]
            baseline = sum(other_returns) / (len(rewards) - 1)
            reward_to_go = 0.0
            reversed_advantages = []
            for step in range(len(trajectory) - 1, -1, -1):
                # Discount both the return and baseline from the start of the attempt.
                reward_to_go += trajectory[step] * self.gamma ** step
                reversed_advantages.append(reward_to_go - baseline)
            all_advantages.append(list(reversed(reversed_advantages)))
        return all_advantages


class PolicyGradientUpdater:
    """Compose an objective with token log probabilities and an optimizer.

    Backpropagate one action at a time; no graph spans a Lean call. The caller
    enables checkpointing only when train and eval modes compute the same policy.
    """

    def __init__(self, model, optimizer, torch, autocast, *, advantages, action_logprob):
        self.model = model
        self.optimizer = optimizer
        self.torch = torch
        self.autocast = autocast
        self.advantages = advantages
        self.action_logprob = action_logprob

    def update(self, trajectories, checkpointing=False):
        trajectory_rewards = [[step['reward'] for step in path] for path in trajectories]
        values = self.advantages(trajectory_rewards)
        self.optimizer.zero_grad(set_to_none=True)
        loss_value, informative = 0.0, False
        if checkpointing:
            self.model.train()
        try:
            for path, trajectory_advantages in zip(trajectories, values):
                for step, advantage in zip(path, trajectory_advantages):
                    if not advantage:
                        continue
                    informative = True
                    with self.autocast():
                        log_probability = self.action_logprob(
                            self.model, step['prompt_ids'], step['action_ids'], self.torch)
                        loss = -advantage * log_probability / len(trajectories)
                    if not self.torch.isfinite(loss):
                        raise RuntimeError('Non-finite policy loss')
                    loss.backward()
                    loss_value += float(loss.detach())
        finally:
            self.model.eval()
        norm = 0.0
        if informative:
            norm = float(self.torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0, error_if_nonfinite=True))
            self.optimizer.step()
        return dict(loss=loss_value, gradient_norm=norm, optimizer_updated=informative, advantages=values)


def action_logprob(model, prompt_ids, action_ids, torch):
    """Log probability of the sampled tokens, including EOS, without retokenizing.

    Only the last len(action_ids)+1 positions reach the LM head (logits_to_keep), so the logits
    tensor is (A+1, vocab) instead of (P+A, vocab). The values equal the full-logits path.
    """
    ids = torch.tensor(prompt_ids + action_ids, device=next(model.parameters()).device)[None]
    mask = torch.ones_like(ids)
    targets = torch.tensor(action_ids, device=ids.device)
    try:
        # Kept positions are P-1 .. P+A-1; the last one predicts nothing in the action.
        logits = model(input_ids=ids, attention_mask=mask, use_cache=False,
                       logits_to_keep=len(action_ids) + 1).logits[0, :-1]
    except TypeError:  # An older transformers forward has no logits_to_keep; only the call may fall back.
        logits = model(input_ids=ids, attention_mask=mask, use_cache=False).logits[0, len(prompt_ids) - 1:-1]
    predicted = logits.float().log_softmax(-1)
    return predicted.gather(1, targets[:, None]).sum()
