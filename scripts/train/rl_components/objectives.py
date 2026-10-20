"""The existing leave-one-out estimator and streamed policy-gradient update."""


def proof_rewards(verdicts):
    """Binary complete-proof rewards, refusing a batch with a censored verifier."""
    if any(v.get('censored') or v['status'] in ('crash', 'replay_crash') for v in verdicts):
        raise RuntimeError('Censored verifier outcome; no PPO update')
    return [float(v['status'] == 'accepted') for v in verdicts]


class LeaveOneOutObjective:
    """Discounted reward-to-go with each trajectory excluded from its baseline.

    Both rewards and baselines are discounted from step zero. gamma=1 retains
    the original undiscounted estimator. Equal returns have zero advantage.
    """

    def __init__(self, gamma=1.0):
        self.gamma = gamma

    def advantages(self, rewards):
        if len(rewards) < 2:
            raise ValueError('Leave-one-out requires at least two trajectories')
        totals = [sum(reward * self.gamma ** k for k, reward in enumerate(row)) for row in rewards]
        result = []
        for index, row in enumerate(rewards):
            baseline = sum(totals[:index] + totals[index + 1:]) / (len(rewards) - 1)
            future, values = 0.0, []
            for k in range(len(row) - 1, -1, -1):
                future += row[k] * self.gamma ** k
                values.append(future - baseline)
            result.append(list(reversed(values)))
        return result


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
        values = self.advantages([[step['reward'] for step in path] for path in trajectories])
        self.optimizer.zero_grad(set_to_none=True)
        loss_value, informative = 0.0, False
        if checkpointing:
            self.model.train()
        try:
            for path, adv in zip(trajectories, values):
                for step, value in zip(path, adv):
                    if not value:
                        continue
                    informative = True
                    with self.autocast():
                        loss = -value * self.action_logprob(self.model, step['prompt_ids'], step['action_ids'], self.torch) / len(trajectories)
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
