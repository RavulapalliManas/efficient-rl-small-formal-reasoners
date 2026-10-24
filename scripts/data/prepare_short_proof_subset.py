"""Select short original LeanDojo training proofs without exposing hidden suffixes.

Preparation only. Reference length is a difficulty proxy, not model success.
Peak: <=256 MiB serialized inputs x 6 Python overhead <1.5 GiB; use a 2 GiB
managed cap. No model or Lean process. A separate native-context replay admits
the selected package for use; selection alone does not establish readiness.
"""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path
import zipfile


PUBLIC = ('task_id', 'theorem_id', 'full_name', 'file_path', 'header', 'k',
          'steps_total', 'steps_remaining', 'state', 'state_source', 'given_steps', 'topic')
CONTEXT = ('theorem_id', 'official_split', 'full_name', 'file_path', 'declaration_header',
           'family', 'url', 'commit', 'imports', 'start', 'end', 'context_digest')


def read_rows(path):
    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            if len(archive.namelist()) != 1:
                raise ValueError('Expected a single JSONL export in zip')
            with archive.open(archive.namelist()[0]) as input_stream:
                return [json.loads(line) for line in input_stream]
    with path.open() as input_stream:
        return [json.loads(line) for line in input_stream]


def select(bank, rows, excluded_files, config):
    if any(r['official_split'] != 'train' for r in rows):
        raise ValueError('Input rows must belong to official train')
    if any((r['url'], r['commit']) != (config['source']['url'], config['source']['commit']) for r in rows):
        raise ValueError('Source pin differs from subset config')
    by_id = {r['theorem_id']: r for r in rows}
    units, excluded = defaultdict(list), Counter()
    for unit in bank:
        if unit['theorem_id'] in by_id:
            units[unit['theorem_id']].append(unit)
    eligible, strata = {}, defaultdict(list)
    rank = lambda tid: hashlib.sha256(f"{config['seed']}/{tid}".encode()).hexdigest()
    for tid, candidates in units.items():
        row = by_id[tid]
        if row['file_path'] in excluded_files:
            excluded['evaluation_or_dev_file'] += 1
            continue
        if row['family'] not in config['families']:
            excluded['other_family'] += 1
            continue
        if any(u['full_name'] != row['full_name'] or u['file_path'] != row['file_path']
               or u['header'] != row['declaration_header'] for u in candidates):
            raise ValueError('Bank identity differs from official source row')
        roots = [u for u in candidates if u['k'] == 0]
        if len(roots) != 1:
            raise ValueError('Each eligible theorem must have one original start')
        root = roots[0]
        length = root['steps_total']
        if not 1 <= length <= config['max_reference_blocks'] or len('\n'.join(root['reference_rest'])) > config['max_reference_chars']:
            excluded['long_reference'] += 1
            continue
        chosen = [u for u in candidates if u['k'] == 0 or u['steps_remaining'] in config['suffix_blocks']]
        for u in chosen:
            if (u['steps_total'] != length or u['steps_remaining'] != length - u['k']
                    or u['given_steps'] + u['reference_rest'] != root['reference_rest']
                    or not u['state'].strip() or u['state'].strip() == 'no goals'):
                raise ValueError('Invalid prefix or remaining-proof metadata')
        eligible[tid] = sorted(chosen, key=lambda u: u['k'])
        strata[row['family'], 'short' if length <= 2 else 'medium'].append(tid)
    queues = [deque(sorted(strata[k], key=rank)) for k in sorted(strata)]
    selected = []
    while any(queues) and len(selected) < config['max_theorems']:
        for queue in queues:
            if queue and len(selected) < config['max_theorems']:
                selected.append(queue.popleft())
    private = [u for tid in selected for u in eligible[tid]]
    public = [{k: u[k] for k in PUBLIC} for u in private]
    context = [{k: by_id[tid][k] for k in CONTEXT if k in by_id[tid]} for tid in selected]
    if len({u['task_id'] for u in public}) != len(public):
        raise ValueError('Duplicate task IDs')
    report = dict(status='selected_pending_native_replay', eligible_theorems=len(eligible),
                  selected_theorems=len(selected), selected_units=len(public),
                  whole_theorem_units=sum(u['k'] == 0 for u in public), excluded=dict(excluded),
                  theorem_families=dict(Counter(by_id[tid]['family'] for tid in selected)),
                  reference_blocks=dict(Counter(eligible[tid][0]['steps_total'] for tid in selected)),
                  limitations=['Length bands are proxies, not measured policy difficulty.',
                               'Original LeanDojo split and source context are retained.',
                               'Prior model exposure and semantic family independence remain unresolved.',
                               'No inference, RL update, or new Lean replay occurs in selection.'])
    return public, context, private, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bank', 'train', 'exclude-files', 'config', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--eval', type=Path, action='append', required=True)
    args = parser.parse_args()
    inputs = [args.bank, args.train, args.exclude_files, args.config, *args.eval]
    if sum(p.stat().st_size for p in inputs) > 256 * 1024**2:
        raise ValueError('Input bytes exceed preparation memory bound')
    config = json.loads(args.config.read_text())
    if not 1 <= config['max_theorems'] <= 64:
        raise ValueError('This preparation supports at most 64 parent theorems')
    excluded = set(args.exclude_files.read_text().split())
    for path in args.eval:
        excluded.update(r['file_path'] for r in read_rows(path))
    public, context, private, report = select(read_rows(args.bank), read_rows(args.train), excluded, config)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'private').mkdir()
    for name, values in [('bank.jsonl', public), ('context_rows.jsonl', context), ('private/references.jsonl', private)]:
        (args.output / name).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in values))
    (args.output / 'excluded_files.txt').write_text('\n'.join(sorted(excluded)) + '\n')
    report.update(config=config, input_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    if not public:
        raise RuntimeError('No eligible short training proofs')


if __name__ == '__main__':
    main()
