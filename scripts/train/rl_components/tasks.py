"""Task admission and curriculum scheduling, independent of Lean processes."""
import random


class PoolTaskAdmission:
    """Check task metadata against the original pool and declared split.

    The caller supplies statement parsing, unit conversion, and forbidden-code
    detection. No row is relabelled or copied into another split.
    """

    def __init__(self, rows, *, header, unit_factory, forbidden):
        self.rows = rows
        self.header = header
        self.unit_factory = unit_factory
        self.forbidden = forbidden

    def train_statements(self, names=None):
        if names is None:
            return [row for row in self.rows if row['split'] == 'train']
        split = {row['name']: row['split'] for row in self.rows}
        outside = sorted(name for name in names if split.get(name) != 'train')
        if outside:
            raise RuntimeError(f'{len(outside)} names are not train statements of the pool, for example {outside[:3]}')
        return [row for row in self.rows if row['name'] in names]

    def units(self, tasks, split='train'):
        if split not in ('train', 'validation', 'test'):
            raise ValueError('Unknown task split')
        names = {task['name'] for task in tasks}
        if split == 'train':
            selected = self.train_statements(names)
        else:
            selected = [row for row in self.rows if row['name'] in names and row['split'] == split]
            if names != {row['name'] for row in selected}:
                raise RuntimeError('Assessment names are not statements of the declared ' + split + ' split')
        rows = {row['name']: row for row in selected}
        bad = sorted(task['task_id'] for task in tasks
                     if task['header'] != self.header(rows[task['name']]) or self.forbidden.search(task['given']))
        if bad:
            raise RuntimeError(f'{len(bad)} tasks differ from the pool statement or have a FORBIDDEN given text, for example {bad[:3]}')
        if len({task['task_id'] for task in tasks}) != len(tasks):
            raise RuntimeError('task_id values repeat')
        return [dict(self.unit_factory(task), state=task['state']) for task in tasks]


class TaskCurriculum:
    """Compose task validation with exclusions and the existing prefix cutoff.

    Validate every supplied task before filtering can hide a split leak.
    The validator has the existing task_units(tasks, pool, split=...) interface.
    """

    def __init__(self, pool, excluded, max_remaining, *, validate_units):
        self.pool = pool
        self.excluded = excluded
        self.max_remaining = max_remaining
        self.validate_units = validate_units

    def _within_cutoff(self, tasks):
        return [task for task in tasks if task['kind'] == 'whole' or
                (task['kind'] == 'prefix_top' and task['lines_remaining'] <= self.max_remaining)]

    def training(self, tasks):
        self.validate_units(tasks, self.pool)
        if any(task['name'] in self.excluded for task in tasks):
            raise ValueError('Training input contains excluded names')
        return self._within_cutoff(tasks)

    def assessment(self, tasks, split='train'):
        if split == 'train':
            return self.training(tasks)
        self.validate_units(tasks, self.pool, split=split)
        if not {task['name'] for task in tasks} <= self.excluded:
            raise ValueError('Assessment names must be excluded from training')
        return self._within_cutoff(tasks)


class TaskSchedule:
    """Reuse an admitted task bank for balanced sampling or a frozen schedule."""

    def __init__(self, tasks):
        self.tasks = tasks

    def balanced(self, seed):
        rng = random.Random(seed)
        kinds = sorted({task['kind'] for task in self.tasks})
        banks = {kind: sorted((task for task in self.tasks if task['kind'] == kind), key=lambda task: task['task_id'])
                 for kind in kinds}
        positions = dict.fromkeys(kinds, 0)
        for bank in banks.values():
            rng.shuffle(bank)
        while True:
            for kind in kinds:
                position = positions[kind]
                yield banks[kind][position]
                positions[kind] += 1
                if positions[kind] == len(banks[kind]):
                    rng.shuffle(banks[kind])
                    positions[kind] = 0

    def frozen(self, order, max_groups):
        by_id = {task['task_id']: task for task in self.tasks}
        if len(by_id) != len(self.tasks):
            raise ValueError('Task bank contains duplicate task IDs')
        if not isinstance(order, list) or len(order) != max_groups or not order:
            raise ValueError('Task schedule must contain exactly max_groups entries')
        if any(not isinstance(task_id, str) or task_id not in by_id for task_id in order):
            raise ValueError('Task schedule contains an unknown task ID')
        return iter([by_id[task_id] for task_id in order])
