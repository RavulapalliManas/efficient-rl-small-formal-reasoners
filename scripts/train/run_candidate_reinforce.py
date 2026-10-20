"""Monte Carlo REINFORCE over the shared frozen candidate policy.

The leave-one-out baseline includes complete empty-menu failures. No critic
bootstrap or value loss enters this comparison. A batch contains repeated
rollouts from the same TRAIN root, collected before any optimizer update.
"""
import math

from candidate_policy import masked_distribution, validate_episode
from rl_components.objectives import LeaveOneOutObjective


def update_reinforce(policy, optimizer, episodes, *, gamma=1.0, max_grad_norm=1.0):
    import torch

    episodes = tuple(episodes)
    if not 0 <= gamma <= 1 or max_grad_norm <= 0:
        raise ValueError('Invalid discount or gradient norm bound')
    for episode in episodes:
        validate_episode(episode)
        if any(step.truncated for step in episode.transitions):
            raise ValueError('Monte Carlo REINFORCE requires complete terminal returns')
    advantages = LeaveOneOutObjective(gamma).advantages(
        [[step.reward for step in episode.transitions] for episode in episodes])
    # Check the complete batch before accumulating gradients or changing weights.
    with torch.no_grad():
        for episode in episodes:
            for step in episode.transitions:
                logits, _ = policy.evaluate(step.support)
                distribution = masked_distribution(logits, step.support.mask)
                action = torch.tensor(step.decision.action_index, device=logits.device)
                current = float(distribution.log_prob(action))
                if not math.isclose(current, step.decision.old_log_prob, rel_tol=1e-5, abs_tol=1e-6):
                    raise ValueError('REINFORCE requires on-policy rollout probabilities')
    optimizer.zero_grad(set_to_none=True)
    loss_value, informative = 0.0, False
    try:
        for episode, values in zip(episodes, advantages):
            for step, advantage in zip(episode.transitions, values):
                if not advantage or sum(step.support.mask) == 1:
                    continue
                logits, _ = policy.evaluate(step.support)
                distribution = masked_distribution(logits, step.support.mask)
                action = torch.tensor(step.decision.action_index, device=logits.device)
                loss = -advantage * distribution.log_prob(action) / len(episodes)
                if not torch.isfinite(loss):
                    raise ValueError('Non-finite REINFORCE loss')
                loss.backward()
                loss_value += float(loss.detach())
                informative = True
        norm = float(torch.nn.utils.clip_grad_norm_(
            list(policy.parameters()), max_grad_norm, error_if_nonfinite=True)) if informative else 0.0
        updated = informative and norm > 0
        if updated:
            optimizer.step()
    except Exception:
        optimizer.zero_grad(set_to_none=True)
        raise
    return dict(episodes=len(episodes), transitions=sum(len(e.transitions) for e in episodes),
                loss=loss_value, gradient_norm=norm, optimizer_updated=updated, updates=int(updated))


if __name__ == '__main__':
    from run_candidate_ppo import main
    main(update_fn=update_reinforce, algorithm='reinforce')
