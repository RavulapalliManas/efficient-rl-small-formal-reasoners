"""Audit immutable structured RLP exports on CPU, without model inference.

Memory bound: 1,000 records x 64 KiB < 64 MiB, plus indices < 64 MiB.
Run under a 2 GiB service cap. One process owns the exclusive output directory.
Arithmetic admission covers the numeric task only, not its assistance or a proof.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import re


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def oracle(row):
    """Recompute answers without the preparer's answer or predicate."""
    p, family = row['parameters'], row['family']
    a, b = p['a'], p['b']
    if a <= 0 or b <= 0:
        raise ValueError('This auditor covers the saved positive-operand corpus only')
    if family == 'witness':
        q, r = divmod(b, a)
        if r:
            raise ValueError('No integer witness')
        return q
    if family == 'substitution':
        return a * p['k'] + p['d']
    if family == 'counterexample':
        if a % b == 0:
            raise ValueError('No positive counterexample')
        return a
    if family in ('quotient', 'remainder'):
        return divmod(a, b)[0 if family == 'quotient' else 1]
    if family == 'gcd':
        return max(d for d in range(1, min(a, b) + 1) if a % d == b % d == 0)
    if family in ('bezout', 'inverse'):
        return next(x for x in range(b) if (a * x) % b == 1)
    if family == 'prime_condition':
        return int(a > 1 and not any(a % d == 0 for d in range(2, a)))
    if family == 'prime_factor':
        return next(d for d in range(2, a + 1) if a % d == 0)
    if family == 'congruence':
        return int(a % p['m'] == b % p['m'])
    if family == 'induction':
        return a * (b + 1)
    if family == 'contradiction':
        return divmod(a * b + 1, a)[1]
    if family == 'cancellation':
        # Cancellation means multiplication induces an injective map on residues.
        return int(len({a * x % b for x in range(b)}) == b)
    if family == 'witness_sum':
        return p['k'] + p['l']
    if family == 'witness_chain':
        return p['k'] * p['l']
    raise ValueError('Unknown family: ' + family)


def identity(row):
    """Task-relevant operands; Bezout and inverse ask for the same residue."""
    p, family = row['parameters'], row['family']
    if family in ('bezout', 'inverse'):
        return ('inverse', p['a'], p['b'])
    if family in ('prime_condition', 'prime_factor'):
        return (family, p['a'])
    if family == 'substitution':
        return (family, p['a'], p['k'], p['d'])
    if family == 'congruence':
        return (family, p['a'], p['b'], p['m'])
    if family.startswith('witness_'):
        return (family, p['a'], p['k'], p['l'])
    return (family, p['a'], p['b'])


def load(path):
    result = []
    with path.open() as stream:
        for line in stream:
            if len(line.encode()) > 64 * 1024:
                raise ValueError('Record exceeds 64 KiB')
            if line.strip():
                result.append(json.loads(line))
            if len(result) > 1000:
                raise ValueError('Export exceeds 1000 records')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--preparer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--support-review', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.data / 'manifest.json').read_text())
    spec = importlib.util.spec_from_file_location('historical_corpus', args.preparer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert digest(args.preparer) == manifest['code_sha256'], 'Wrong historical preparer'
    filenames = {'train': 'train.jsonl', 'validation': 'validation.jsonl',
                 'heldout': 'heldout_primitives.jsonl'}
    data = {split: load(args.data / name) for split, name in filenames.items()}
    assert load(args.data / 'validation_primitives.jsonl') == data['validation']
    unique = {split: list({r['id']: r for r in rows}.values()) for split, rows in data.items()}
    report = {'input': str(args.data), 'preparer_sha256': digest(args.preparer),
              'auditor_sha256': digest(Path(__file__)), 'source_revision': manifest['revision'],
              'input_sha256': {name: digest(args.data / name) for name in filenames.values()},
              'counts': {}, 'overlap': {}, 'failures': [], 'checker_probes': Counter(),
              'coverage': {}, 'limitations': [
                  'Numeric targets do not certify generated reasoning or universal proofs.',
                  'All previously evaluated splits are development evidence, not untouched future tests.',
                  'Template normalization replaces decimal numerals only; it is not semantic deduplication.',
                  'No tokenizer is loaded; character lengths are not token counts.',
                  'Support statements require separate mathematical admission.']}
    failures = report['failures']
    def check(condition, message, row=None):
        if not condition:
            failures.append({'message': message, 'id': row and row['id']})

    report['source_hashes_match'] = {
        name: digest(args.data / 'source' / Path(name).name) == expected
        for name, expected in manifest['source_sha256'].items()}
    check(all(report['source_hashes_match'].values()), 'Reference snapshot hash mismatch')
    for split, rows in data.items():
        by_id = {}
        for row in rows:
            content = {k: v for k, v in row.items() if k != 'replay'}
            if row['id'] in by_id:
                check(content == by_id[row['id']], 'Conflicting repeated record', row)
            by_id[row['id']] = content

    # Chosen positive and negative controls precede corpus measurements.
    controls = [({'family': 'prime_factor', 'parameters': {'a': 15, 'b': 2}}, 3, 5),
                ({'family': 'cancellation', 'parameters': {'a': 2, 'b': 6}}, 0, 1),
                ({'family': 'inverse', 'parameters': {'a': 3, 'b': 7}}, 5, 4)]
    for row, expected, wrong in controls:
        assert oracle(row) == expected
        assert module.verify_primitive(row, expected)
        assert not module.verify_primitive(row, wrong)
    report['planted_controls'] = {'positive': len(controls), 'negative': len(controls), 'passed': True}
    introduced = set()
    for row in data['train']:
        check(set(row['dependencies']) <= introduced, 'Prerequisite order violation', row)
        introduced.add(row['family'])
    with (args.output / 'numeric_admission.jsonl').open('x') as ledger:
        for split, rows in unique.items():
            for row in rows:
                expected = oracle(row)
                valid = expected == row['answer']
                check(valid, 'Incorrect numeric target', row)
                check(row['target_text'] == str(expected), 'Incorrect target text', row)
                check(row['text'] == row['prefix'] + row['target_text'], 'Text assembly mismatch', row)
                check(row['id'] == hashlib.sha256(row['question'].encode()).hexdigest(), 'ID mismatch', row)
                if row['family'] in manifest['concepts']:
                    check(row['assistance'] == manifest['concepts'][row['family']][2], 'Support mismatch', row)
                candidates = {expected, expected - 1, expected + 1, -1, 0, 1}
                if row['family'] == 'prime_factor':
                    candidates.update(d for d in range(2, row['parameters']['a'] + 1)
                                      if row['parameters']['a'] % d == 0)
                for candidate in candidates:
                    accepted = module.verify_primitive(row, candidate)
                    agrees = accepted == (candidate == expected)
                    report['checker_probes']['total'] += 1
                    report['checker_probes']['mismatches'] += not agrees
                    check(agrees, 'Checker disagrees with independent answer', row)
                ledger.write(json.dumps({'id': row['id'], 'split': split, 'family': row['family'],
                    'source_file': str(args.data / filenames[split]), 'answer': row['answer'],
                    'recomputed_answer': expected,
                    'numeric_status': 'accepted_by_exact_check' if valid else 'rejected',
                    'support_status': 'unreviewed', 'admission_status': 'unreviewed',
                    'scope': 'Concrete numeric task only; await support review before positive training admission'}) + '\n')
            families = Counter(r['family'] for r in rows)
            report['counts'][split] = {'presentations': len(data[split]), 'unique': len(rows),
                'replay': sum(r['replay'] for r in data[split]), 'families': dict(families),
                'presentation_families': dict(Counter(r['family'] for r in data[split])),
                'presentation_stages': dict(Counter(r['curriculum_stage'] for r in data[split]))}
            report['coverage'][split] = {
                'assistance_supplied': sum(bool(r['assistance']) for r in rows),
                'compositions': sum(r['evaluation_kind'] == 'composition' for r in rows),
                'explicit_assumption_fields': sum('assumptions' in r for r in rows),
                'reference_step_fields': sum('reference_steps' in r for r in rows),
                'nonpositive_operands': sum(any(v <= 0 for v in r['parameters'].values()) for r in rows),
                'answer_characters': dict(Counter(len(r['target_text']) for r in rows)),
                'by_family': {family: {'answers': dict(Counter(str(r['answer']) for r in rows if r['family'] == family)),
                    'min_a': min(r['parameters']['a'] for r in rows if r['family'] == family),
                    'max_a': max(r['parameters']['a'] for r in rows if r['family'] == family)} for family in families}}
            check(len(data[split]) == manifest['counts'][split], 'Manifest count mismatch: ' + split)
        for left, right in itertools.combinations(unique, 2):
            pair = {}
            transforms = {'id': lambda r: r['id'], 'question': lambda r: r['question'],
                          'equivalent_numeric_task': identity,
                          'numeric_template': lambda r: re.sub(r'\d+', '#', r['question'])}
            for name, transform in transforms.items():
                shared = {transform(r) for r in unique[left]} & {transform(r) for r in unique[right]}
                pair[name] = {'shared_keys': len(shared), 'examples': sorted(map(str, shared))[:5]}
                if name in ('id', 'question', 'equivalent_numeric_task'):
                    check(not shared, name + ' crosses splits: ' + left + '/' + right)
            report['overlap'][left + '/' + right] = pair
    report['checker_probes'] = dict(report['checker_probes'])
    report['unique_total'] = sum(len(rows) for rows in unique.values())
    # Fit each constant answer on training only, then score the development splits.
    constants = {family: Counter(r['answer'] for r in unique['train'] if r['family'] == family)
                 .most_common(1)[0][0] for family in {r['family'] for r in unique['train']}}
    report['training_majority_baseline'] = {
        split: {family: {'prediction': prediction,
                        'n': sum(r['family'] == family for r in rows),
                        'correct': sum(r['family'] == family and r['answer'] == prediction for r in rows)}
                for family, prediction in sorted(constants.items())}
        for split, rows in unique.items() if split != 'train'}
    if args.support_review:
        review = json.loads(args.support_review.read_text())
        assert set(review['families']) == set(manifest['concepts'])
        report['support_review_sha256'] = digest(args.support_review)
        report['admission'] = {}
        with (args.output / 'admission.jsonl').open('x') as ledger:
            for split, rows in unique.items():
                statuses = Counter()
                for row in rows:
                    support = review['families'].get(row['family'])
                    numeric_valid = oracle(row) == row['answer']
                    status = ('rejected' if not numeric_valid else support['reuse_status']
                              if support else 'accepted_by_exact_check')
                    statuses[status] += 1
                    ledger.write(json.dumps({'id': row['id'], 'split': split, 'family': row['family'],
                        'numeric_status': 'accepted_by_exact_check' if numeric_valid else 'rejected',
                        'admission_status': status, 'support_review': support,
                        'scope': 'Prospective reuse; numeric admission is not proof certification',
                        'source_file': str(args.data / filenames[split])}) + '\n')
                report['admission'][split] = dict(statuses)
    report['numeric_status'] = 'PASS' if not failures else 'FAIL'
    report['training_ready'] = False
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('counts', 'checker_probes', 'planted_controls',
                                                  'numeric_status', 'training_ready', 'failures')}
                     | {'admission': report.get('admission')}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
