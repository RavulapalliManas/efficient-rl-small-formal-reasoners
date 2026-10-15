"""Arm C task sampler: Thompson sampling toward families that give mixed groups.

A group of G trajectories gives a learning signal only when it is mixed, that is when
0 < k < G trajectories get reward 1. For a family with success rate p, the probability
of a mixed group is v(p) = 1 - p^G - (1 - p)^G. The sampler keeps a Beta(a, b) posterior
over p for each family, draws p from it, and picks the family with the largest v(p).

Pre-registered specification:
- Prior a = b = 1. Evidence weight w = 0.5: a group with k successes adds w*k to a and
  w*(G - k) to b. The weight below 1 discounts the evidence because the G trajectories
  of one group share a task and are not independent.
- Cap: a family is eligible only if its share of groups so far is below
  cap_ratio * (1 / number_of_families). With zero groups so far, all families are eligible.
  If cap_ratio == 1 and every share equals the uniform share, the families with the
  fewest groups are eligible instead (the eligible set is never empty).
- Within the chosen family, the sampler picks the least-visited task; the seeded RNG
  breaks ties. Family ties in v are broken by family name.

Censored groups: the caller MUST NOT call observe() for a censored group. A censored
group gives no outcome, so it updates no posterior and no visit count. The next call to
next_task() then re-draws from the same posterior.

Determinism: the sampler owns random.Random(seed). The same seed, tasks, and observe()
sequence give the same task sequence. Memory: O(number of tasks), no arrays.

The caller passes training tasks only. As a defensive check, task_id values that start
with 'ho' or 'iv_' (held-out or invalid-test identifiers) raise ValueError.
"""
import random

FORBIDDEN_PREFIXES = ('ho_', 'ho2_', 'iv_')  # Evaluation-set name prefixes; never training tasks.


class BandSampler:
    def __init__(self, tasks, group_size=8, seed=0, cap_ratio=1.5, prior=(1.0, 1.0), weight=0.5):
        if group_size < 2:
            raise ValueError('group_size must be at least 2 for a group to be mixed')
        if cap_ratio < 1:
            raise ValueError('cap_ratio must be at least 1')
        if not tasks:
            raise ValueError('tasks is empty')
        self.G, self.cap_ratio, self.weight = group_size, cap_ratio, weight
        self.rng = random.Random(seed)
        self.tasks = {}
        for task in tasks:
            task_id = task['task_id']
            if task_id.startswith(FORBIDDEN_PREFIXES):
                raise ValueError('Held-out-like task ID in training tasks: ' + task_id)
            if task_id in self.tasks:
                raise ValueError('Duplicate task ID: ' + task_id)
            self.tasks[task_id] = task
        self.by_family = {}
        for task_id, task in sorted(self.tasks.items()):
            self.by_family.setdefault(task['family'], []).append(task_id)
        self.posterior = {f: [float(prior[0]), float(prior[1])] for f in self.by_family}
        self.family_visits = dict.fromkeys(self.by_family, 0)
        self.task_visits = dict.fromkeys(self.tasks, 0)
        self.groups = 0

    def _eligible(self):
        n = len(self.by_family)
        if self.groups == 0:
            return sorted(self.by_family)
        limit = self.cap_ratio * self.groups / n  # visits < limit  <=>  share < cap_ratio / n
        eligible = [f for f in self.by_family if self.family_visits[f] < limit]
        if not eligible:
            low = min(self.family_visits.values())
            eligible = [f for f in self.by_family if self.family_visits[f] == low]
        return sorted(eligible)

    def next_task(self):
        """Return the next training task dict. Does not change any state except the RNG."""
        best = None
        for family in self._eligible():
            a, b = self.posterior[family]
            p = self.rng.betavariate(a, b)
            mixed = 1 - p ** self.G - (1 - p) ** self.G
            if best is None or mixed > best[0]:  # families iterate in name order: ties keep the first
                best = (mixed, family)
        ids = self.by_family[best[1]]
        least = min(self.task_visits[i] for i in ids)
        return self.tasks[self.rng.choice([i for i in ids if self.task_visits[i] == least])]

    def observe(self, task, k):
        """Record a completed, uncensored group in which k of G trajectories got reward 1."""
        if isinstance(k, bool) or not isinstance(k, int) or not 0 <= k <= self.G:
            raise ValueError(f'k must be an integer in 0..{self.G}, got {k!r}')
        family = self.tasks[task['task_id']]['family']
        self.posterior[family][0] += self.weight * k
        self.posterior[family][1] += self.weight * (self.G - k)
        self.family_visits[family] += 1
        self.task_visits[task['task_id']] += 1
        self.groups += 1

    def state(self):
        """JSON-serialisable snapshot for steps.jsonl."""
        return dict(groups=self.groups,
                    posterior={f: list(ab) for f, ab in sorted(self.posterior.items())},
                    family_visits=dict(sorted(self.family_visits.items())),
                    shares={f: (v / self.groups if self.groups else 0.0) for f, v in sorted(self.family_visits.items())},
                    task_visits=dict(sorted(self.task_visits.items())))


def simulate(tasks, true_p_by_task, steps, seed, policy='band', group_size=8, **kwargs):
    """Count mixed groups (0 < k < G) over `steps` groups with k ~ Binomial(G, p_task).

    policy 'band' uses BandSampler (extra kwargs go to it); 'round_robin' cycles the
    tasks in task_id order. Both use the same environment RNG stream seeded by `seed`.
    """
    env = random.Random(f'env|{seed}')
    ordered = sorted(tasks, key=lambda t: t['task_id'])
    sampler = BandSampler(tasks, group_size, seed, **kwargs) if policy == 'band' else None
    mixed = 0
    for step in range(steps):
        task = sampler.next_task() if sampler else ordered[step % len(ordered)]
        p = true_p_by_task[task['task_id']]
        k = sum(env.random() < p for _ in range(group_size))
        mixed += 0 < k < group_size
        if sampler:
            sampler.observe(task, k)
    return mixed
