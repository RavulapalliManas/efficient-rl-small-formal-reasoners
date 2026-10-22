"""Recreate paired task orders from one frozen TRAIN bank and explicit annotations."""
from collections import Counter
import heapq
import json
import random

from .sources import external_output
from .storage import digest, file_sha256, rows


def prerequisite_order(task_ids, annotation, seed):
    """Topological order with deterministic tie breaks; reject missing or cyclic annotations."""
    prerequisites = annotation['prerequisites']
    if not isinstance(prerequisites, dict) or set(prerequisites) != set(task_ids):
        raise ValueError('Prerequisite annotations must cover exactly the TRAIN task IDs.')
    children = {task_id: [] for task_id in task_ids}
    remaining = {}
    for task_id, parents in prerequisites.items():
        if (not isinstance(parents, list) or any(not isinstance(parent, str) for parent in parents)
                or len(set(parents)) != len(parents) or not set(parents) <= set(task_ids)):
            raise ValueError('Prerequisites contain duplicate or unknown task IDs.')
        remaining[task_id] = len(parents)
        for parent in parents:
            children[parent].append(task_id)
    rank = lambda task_id: (digest(f'{seed}/{task_id}'), task_id)
    ready = [rank(task_id) for task_id in task_ids if not remaining[task_id]]
    heapq.heapify(ready)
    order = []
    while ready:
        _, task_id = heapq.heappop(ready)
        order.append(task_id)
        for child in children[task_id]:
            remaining[child] -= 1
            if not remaining[child]:
                heapq.heappush(ready, rank(child))
    if len(order) != len(task_ids):
        raise ValueError('Prerequisite annotations contain a cycle.')
    return order


def build_orders(package, output, seed, epochs, annotation_path=None):
    if not 1 <= epochs <= 10:
        raise ValueError('Use between 1 and 10 complete passes.')
    manifest = json.loads((package / 'manifest.json').read_text())
    if manifest['status'] != 'exploratory_verified':
        raise ValueError('A complete verified benchmark package is required.')
    path = package / 'data/tasks_train.jsonl'
    if manifest.get('data_sha256', {}).get('tasks_train.jsonl') != file_sha256(path):
        raise ValueError('TRAIN task bank differs from its frozen manifest, or its hash is missing.')
    tasks = list(rows(path))
    if not tasks or any(row['split'] != 'train' or row['kind'] != 'whole' or row['given'] for row in tasks):
        raise ValueError('Schedules require whole-theorem TRAIN tasks with empty supplied proofs.')
    task_ids = sorted(row['task_id'] for row in tasks)
    if len(task_ids) != len(set(task_ids)):
        raise ValueError('TRAIN task IDs repeat.')
    rng, shuffled = random.Random(seed), []
    for _ in range(epochs):
        epoch = task_ids.copy()
        rng.shuffle(epoch)
        shuffled.extend(epoch)
    orders = {'order_R_runtime.json': shuffled}
    annotation = None
    if annotation_path:
        annotation = json.loads(annotation_path.read_text())
        if not annotation.get('reviewer') or not annotation.get('review_status'):
            raise ValueError('Record the annotation reviewer and review_status.')
        orders['order_C_AI_runtime.json'] = prerequisite_order(task_ids, annotation, seed) * epochs
    expected = Counter({task_id: epochs for task_id in task_ids})
    if any(Counter(order) != expected for order in orders.values()):
        raise ValueError('Orders do not preserve equal task multiplicities.')
    output = external_output(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, order in orders.items():
        (output / name).write_text(json.dumps(order, indent=2) + '\n')
    result = dict(status='orders_prepared_not_run', historical_equivalence=False, seed=seed, epochs=epochs,
                  tasks=len(tasks), groups=len(shuffled), train_tasks_sha256=file_sha256(path),
                  source_manifest_sha256=file_sha256(package / 'manifest.json'),
                  order_sha256={name: file_sha256(output / name) for name in orders},
                  annotation=None if annotation is None else dict(sha256=file_sha256(annotation_path),
                      reviewer=annotation['reviewer'], review_status=annotation['review_status']),
                  limitation='Annotations are supplied claims; mathematical prerequisite necessity is not verified.')
    (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
