"""Shared categorical policy over a frozen menu of Lean-admitted tactics.

The actor scores tactic tokens with the causal LM, including EOS, then normalizes
across the menu. The critic reads observation tokens only. Both losses update the
LM backbone; the scalar value head is also retained by the REINFORCE comparison.
No probe result, reference continuation or future observation enters either input.
"""
from dataclasses import dataclass, replace
import math
import time

INTERRUPTED = frozenset(('crash', 'replay_crash', 'timeout', 'replay_timeout', 'wall_cap',
                         'lean_call_cap', 'scoring_token_cap', 'terminated', 'interrupted'))


@dataclass(frozen=True)
class Candidate:
    id: int
    tactic: str


@dataclass(frozen=True)
class CandidateSupport:
    observation: str
    actions: tuple[Candidate, ...]
    mask: tuple[bool, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'actions', tuple(self.actions))
        mask = tuple(self.mask) if self.mask else (True,) * len(self.actions)
        object.__setattr__(self, 'mask', mask)
        if (not self.actions or len(mask) != len(self.actions) or not any(mask)
                or any(type(m) is not bool for m in mask)):
            raise ValueError('Candidate support has an empty or invalid mask')
        ids = [a.id for a in self.actions]
        if len(set(ids)) != len(ids) or any(type(i) is not int for i in ids):
            raise ValueError('Candidate IDs must be distinct integers')
        if not self.observation or any(not a.tactic.strip() for a in self.actions):
            raise ValueError('Observation and candidate text must be nonempty')

    @classmethod
    def from_menu(cls, menu):
        if menu['status'] != 'ready':
            raise ValueError('A non-ready menu cannot define policy support')
        return cls(menu['observation'], tuple(Candidate(a['id'], a['tactic']) for a in menu['actions']))


@dataclass(frozen=True)
class Decision:
    action_index: int
    old_log_prob: float
    old_value: float


@dataclass(frozen=True)
class Transition:
    support: CandidateSupport
    decision: Decision
    reward: float
    terminated: bool
    truncated: bool
    next_value: float = 0.0
    censored: bool = False


@dataclass(frozen=True)
class Episode:
    transitions: tuple[Transition, ...]
    status: str
    completed: bool

    def __post_init__(self):
        object.__setattr__(self, 'transitions', tuple(self.transitions))


def validate_episode(episode):
    if not episode.completed or episode.status in INTERRUPTED:
        raise ValueError('Incomplete or censored episode cannot enter an update')
    if not episode.transitions:
        if episode.status != 'empty':
            raise ValueError('A zero-action episode must record an empty menu')
        return
    for index, step in enumerate(episode.transitions):
        d = step.decision
        if (step.censored or step.terminated and step.truncated
                or not 0 <= d.action_index < len(step.support.actions)
                or not step.support.mask[d.action_index]):
            raise ValueError('Censored transition or invalid action/boundary')
        if not all(math.isfinite(v) for v in (step.reward, step.next_value, d.old_log_prob, d.old_value)):
            raise ValueError('Non-finite rollout value')
        if d.old_log_prob > 1e-6 or step.reward not in (0., 1.):
            raise ValueError('Invalid policy log-probability or nonbinary reward')
        if step.reward and not step.terminated:
            raise ValueError('Positive reward requires terminal proof acceptance')
        if step.next_value and not step.truncated:
            raise ValueError('Bootstrap is allowed only for a genuine truncation')
        if index < len(episode.transitions) - 1 and (step.terminated or step.truncated):
            raise ValueError('Episode continues after a boundary')
    last = episode.transitions[-1]
    if not (last.terminated or last.truncated):
        raise ValueError('Episode has no final boundary')


def discounted_returns(episode, gamma=1.0):
    validate_episode(episode)
    if not 0 <= gamma <= 1:
        raise ValueError('Discount must be between zero and one')
    if not episode.transitions:
        return []
    last = episode.transitions[-1]
    value = last.next_value if last.truncated else 0.0
    returns = []
    for step in reversed(episode.transitions):
        value = step.reward + gamma * value
        returns.append(value)
    return list(reversed(returns))


def masked_distribution(logits, mask):
    import torch
    if logits.ndim != 1 or len(mask) != logits.numel() or not any(mask):
        raise ValueError('Categorical support has an empty or invalid mask')
    valid = torch.tensor(mask, dtype=torch.bool, device=logits.device)
    if not torch.isfinite(logits[valid]).all():
        raise ValueError('Non-finite candidate score')
    return torch.distributions.Categorical(logits=logits.masked_fill(~valid, -torch.inf))


def sample_decision(policy, support):
    import torch
    with torch.no_grad():
        logits, value = policy.evaluate(support)
        distribution = masked_distribution(logits, support.mask)
        action = distribution.sample()
        return Decision(int(action.item()), float(distribution.log_prob(action).item()), float(value.item()))


class WorkCapReached(RuntimeError):
    pass


class ScoringBudget:
    """Count all teacher-forced forward tokens, including PPO epochs and critic calls.

    The fixed caller-supplied catalogue generates no proposal tokens. Physical
    Lean calls remain in the episode's shared counters, outside this object.
    """
    def __init__(self, max_scoring_tokens, max_wall_seconds, counts=None):
        if max_scoring_tokens <= 0 or max_wall_seconds <= 0:
            raise ValueError('Positive scoring and wall caps are required')
        self.max_scoring_tokens, self.max_wall_seconds = max_scoring_tokens, max_wall_seconds
        self.counts = counts if counts is not None else {}
        self.counts.setdefault('scoring_tokens', 0)
        self.counts.setdefault('scoring_forward_calls', 0)
        self.counts.setdefault('proposal_tokens', 0)
        self.started = time.monotonic()

    def check(self):
        if time.monotonic() - self.started >= self.max_wall_seconds:
            raise WorkCapReached('wall_cap')

    def reserve(self, tokens):
        self.check()
        if tokens < 1 or self.counts['scoring_tokens'] + tokens > self.max_scoring_tokens:
            raise WorkCapReached('scoring_token_cap')
        self.counts['scoring_tokens'] += tokens
        self.counts['scoring_forward_calls'] += 1


def prompt_ids(tokenizer, observation, maximum):
    text = tokenizer.apply_chat_template([dict(role='user', content=observation)],
                                          tokenize=False, add_generation_prompt=True)
    ids = tokenizer(text, add_special_tokens=False)['input_ids']
    if not ids or len(ids) > maximum:
        raise ValueError('Observation exceeds the registered prompt token cap')
    return ids


def action_ids(tokenizer, text, maximum):
    ids = tokenizer(text, add_special_tokens=False)['input_ids']
    eos = tokenizer.eos_token_id
    if eos is not None and (not ids or ids[-1] != eos):
        ids = ids + [eos]
    if not ids or len(ids) > maximum:
        raise ValueError('Candidate exceeds the registered action token cap')
    return ids


class UniformCandidatePolicy:
    """Equal scores over admitted actions, with no LM or scoring forwards.

    The zero value is a recorded placeholder, not a learned return estimate.
    The existing masked distribution performs both sampling and normalization.
    """
    def __init__(self, tokenizer, *, max_prompt_tokens, max_action_tokens, budget):
        self.tokenizer, self.budget = tokenizer, budget
        self.max_prompt_tokens, self.max_action_tokens = max_prompt_tokens, max_action_tokens

    def evaluate(self, support):
        import torch
        self.budget.check()
        prompt_ids(self.tokenizer, support.observation, self.max_prompt_tokens)
        for candidate in support.actions:
            action_ids(self.tokenizer, candidate.tactic, self.max_action_tokens)
        return torch.zeros(len(support.actions)), torch.tensor(0.)


class TorchCandidatePolicy:
    """Full-backbone LM actor and observation-only value head, with dropout off.

    Stream one candidate forward at a time. At most K graphs survive until a
    transition's backward pass. The remote runner must cap K, prompt/action
    tokens, memory and resources; these bounds are not a measured memory claim.
    """
    def __init__(self, model, tokenizer, *, max_prompt_tokens, max_action_tokens, budget, value_head=None):
        import torch
        self.model, self.tokenizer, self.budget = model, tokenizer, budget
        self.max_prompt_tokens, self.max_action_tokens = max_prompt_tokens, max_action_tokens
        parameter = next(model.parameters())
        self.value_head = value_head if value_head is not None else torch.nn.Linear(model.config.hidden_size, 1)
        self.value_head.to(device=parameter.device, dtype=parameter.dtype)
        model.eval()
        self.value_head.eval()

    def parameters(self):
        yield from self.model.parameters()
        yield from self.value_head.parameters()

    def _value_ids(self, ids):
        import torch
        self.budget.reserve(len(ids))
        tokens = torch.tensor([ids], device=next(self.model.parameters()).device)
        hidden = self.model.base_model(input_ids=tokens, attention_mask=torch.ones_like(tokens),
                                       use_cache=False).last_hidden_state[0, -1]
        return self.value_head(hidden).squeeze(-1).float()

    def value(self, observation):
        return self._value_ids(prompt_ids(self.tokenizer, observation, self.max_prompt_tokens))

    def evaluate(self, support):
        import torch
        from run_tactic_rl import action_logprob
        prompt = prompt_ids(self.tokenizer, support.observation, self.max_prompt_tokens)
        # Validate all text before the first forward; no silent truncation changes support.
        actions = [action_ids(self.tokenizer, a.tactic, self.max_action_tokens) for a in support.actions]
        scores = []
        for ids, valid in zip(actions, support.mask):
            if valid:
                self.budget.reserve(len(prompt) + len(ids))
                scores.append(action_logprob(self.model, prompt, ids, torch))
            else:
                scores.append(torch.tensor(0., device=next(self.model.parameters()).device))
        return torch.stack(scores), self._value_ids(prompt)


def collect_episode(env, policy, candidates, *, max_candidates, max_lean_calls, max_steps, record=None):
    """Collect one finite-horizon episode without gradients through Lean.

    max_steps defines task termination, so horizon exhaustion is a failed proof,
    not a bootstrap. External resource interruption remains incomplete. Empty
    menus are complete failed episodes, including when no action was sampled.
    """
    if not 0 < len(candidates) <= max_candidates or max_steps < 1:
        raise ValueError('Invalid candidate or horizon bound')
    steps = []
    emit = record if record is not None else lambda event: None
    try:
        if env.counts['lean_calls'] + 3 > max_lean_calls:
            return Episode((), 'lean_call_cap', False)
        env.reset()
        for _ in range(max_steps):
            if getattr(policy, 'budget', None) is not None:
                policy.budget.check()
            if env.counts['lean_calls'] + 4 * (len(candidates) + 1) > max_lean_calls:
                return Episode(tuple(steps), 'lean_call_cap', False)
            menu = env.candidate_menu(candidates, max_candidates=max_candidates, max_lean_calls=max_lean_calls)
            emit(dict(kind='menu', menu=menu))
            if menu['status'] == 'empty':
                if steps:
                    steps[-1] = replace(steps[-1], terminated=True)
                return Episode(tuple(steps), 'empty', True)
            if menu['status'] != 'ready' or menu.get('censored'):
                return Episode(tuple(steps), menu['status'], False)
            support = CandidateSupport.from_menu(menu)
            decision = sample_decision(policy, support)
            if getattr(policy, 'budget', None) is not None:
                policy.budget.check()
            result = env.select_candidate(menu, support.actions[decision.action_index].id)
            emit(dict(kind='selected', decision=decision.__dict__, result=result))
            censored = bool(result.get('censored')) or result['status'] in INTERRUPTED
            terminated = bool(result.get('done')) or result['status'] != 'changed'
            accepted = bool(result.get('accepted')) and terminated and not censored
            steps.append(Transition(support, decision, float(accepted), terminated, False, censored=censored))
            if censored:
                return Episode(tuple(steps), result['status'], False)
            if terminated:
                return Episode(tuple(steps), result['status'], True)
        steps[-1] = replace(steps[-1], terminated=True)
        return Episode(tuple(steps), 'horizon', True)
    except WorkCapReached as error:
        emit(dict(kind='interruption', error=str(error)))
        return Episode(tuple(steps), str(error), False)
