"""Prepare blinded review packets and recount independent mathematical grades.

No automatic correctness judge. Reviews are supplied by independent reviewers.
Bounded development data: <=1000 cases x64 KiB <64 MiB; use a 2 GiB CPU cap.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def prepare(source, rubric, output, calibration=False):
    if calibration:
        cases = json.loads(source.read_text())['cases']
    else:
        cases = [{'case_id':r['id'],'grader_input':{key:r[key] for key in ('prompt','reference','response')}} for r in rows(source)]
    if len(cases)>1000:
        raise ValueError('Review packet exceeds bound')
    output.mkdir(parents=True,exist_ok=False)
    mapping = {}
    for reviewer, seed in (('a',713),('b',917)):
        order = list(cases)
        random.Random(seed).shuffle(order)
        mapping[reviewer] = {}
        with (output/f'blind_{reviewer}.jsonl').open('x') as out:
            for i,case in enumerate(order):
                rid = hashlib.sha256(f'{seed}:{i}:{case["case_id"]}'.encode()).hexdigest()[:16]
                mapping[reviewer][rid] = case['case_id']
                out.write(json.dumps({'review_id':rid,**case['grader_input']})+'\n')
    (output/'private_map.json').write_text(json.dumps(mapping,indent=2)+'\n')
    (output/'rubric.json').write_bytes(rubric.read_bytes())
    (output/'manifest.json').write_text(json.dumps({'n':len(cases),'source':str(source),
        'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'rubric_sha256':hashlib.sha256(rubric.read_bytes()).hexdigest(),
        'gold_visible':False,'checkpoint_visible':False},indent=2)+'\n')
    return {'n':len(cases),'gold_visible':False,'checkpoint_visible':False}


def validate_review(review, required):
    if set(review)!=set(required):
        raise ValueError('Review schema keys differ: '+review.get('review_id','missing id'))
    for name in ('fact_correct','conditions_correct','operations_correct','conclusion_correct','full_correct','ancillary_error'):
        if review[name] is not None and type(review[name]) is not bool:
            raise ValueError('Non-boolean correctness dimension')
    if review['core_score'] is not None and (type(review['core_score']) is not int or not 0<=review['core_score']<=3):
        raise ValueError('Invalid core score')
    if review['full_correct'] is True and (review['core_score']!=3 or review['ancillary_error'] is not False):
        raise ValueError('Inconsistent full correctness')
    if bool(review['unknown_dimensions']) != review['uncertain']:
        raise ValueError('Uncertainty field disagrees')


def recount(packet, reviews_a, reviews_b, calibration, output):
    mapping = json.loads((packet/'private_map.json').read_text())
    rubric = json.loads((packet/'rubric.json').read_text())
    required = rubric['output_schema']['required']
    aligned = {}
    for reviewer,path in (('a',reviews_a),('b',reviews_b)):
        data = rows(path)
        if len({r['review_id'] for r in data})!=len(data) or {r['review_id'] for r in data}!=set(mapping[reviewer]):
            raise ValueError('Missing, duplicate, or unexpected review IDs')
        for r in data:
            validate_review(r,required)
        aligned[reviewer] = {mapping[reviewer][r['review_id']]:r for r in data}
    comparison_keys = ['fact_correct','conditions_correct','operations_correct','conclusion_correct',
                       'core_score','full_correct','ancillary_error','first_error_step','error_category','uncertain']
    disagreements = [{ 'case_id':key, 'fields':[f for f in comparison_keys if aligned['a'][key][f]!=aligned['b'][key][f]]}
                     for key in aligned['a']]
    disagreements = [d for d in disagreements if d['fields']]
    report = {'n':len(aligned['a']),'disagreements':disagreements,
              'adjudication_status':'required' if disagreements else 'no disagreement',
              'limitations':'Independent AI reviews may share errors. No human validation. Calibration does not establish model competence.'}
    if calibration:
        cases = {c['case_id']:c for c in json.loads(calibration.read_text())['cases']}
        report['calibration'] = {}
        for reviewer,grades in aligned.items():
            counts = defaultdict(Counter)
            for cid,grade in grades.items():
                case,expected = cases[cid],cases[cid]['gold']['review']
                for stratum in ('all',case['representation']):
                    counter = counts[stratum]
                    counter['n'] += 1
                    counter['valid'] += expected['full_correct'] is True
                    counter['invalid'] += expected['full_correct'] is False
                    counter['false_accept'] += grade['full_correct'] is True and expected['full_correct'] is False
                    counter['false_reject'] += grade['full_correct'] is False and expected['full_correct'] is True
                    counter['unknown'] += grade['full_correct'] is None
                    counter['first_error_mismatch'] += grade['first_error_step'] != expected['first_error_step']
            report['calibration'][reviewer] = {k:dict(v) for k,v in counts.items()}
    output.mkdir(parents=True,exist_ok=False)
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    (output/'aligned_reviews.json').write_text(json.dumps(aligned,indent=2)+'\n')
    return report


def paired_comparison(tasks, before, after):
    """Use paired concept resampling; store 10000 floats, not bootstrap matrices."""
    if set(before) != set(tasks) or set(after) != set(tasks):
        raise ValueError('Baseline and current summary IDs differ')
    if any(value is not None and type(value) is not bool for value in before.values()):
        raise ValueError('Baseline full_correct must be boolean or null')

    def compare(selected):
        per_item = [{'case_id': cid, 'before': before[cid], 'after': after[cid]}
                    for cid in sorted(selected)]
        groups = defaultdict(list)
        for cid in selected:
            groups[tasks[cid]['family']].append(cid)
        differences = {family: sum(int(after[cid] is True) - int(before[cid] is True)
                                  for cid in cids) / len(cids)
                       for family, cids in groups.items()}
        concepts = sorted(differences)
        n_concepts = len(concepts)
        interval = None
        if concepts:
            rng = random.Random(20261006)
            # Sampling differences uses identical sampled concept keys for both policies.
            draws = sorted(sum(differences[key] for key in rng.choices(concepts, k=n_concepts))
                           / n_concepts for _ in range(10000))
            interval = []
            for probability in (0.025, 0.975):
                index = (len(draws) - 1) * probability
                lower = int(index)
                interval.append(draws[lower] + (index - lower) * (draws[lower + 1] - draws[lower]))
        transitions = Counter(('unknown' if row['before'] is None else str(row['before']).lower())
                              + '_to_' + ('unknown' if row['after'] is None else str(row['after']).lower())
                              for row in per_item)
        return {'n': len(selected), 'per_item': per_item, 'transitions': dict(sorted(transitions.items())),
                'gained': sum(before[cid] is False and after[cid] is True for cid in selected),
                'lost': sum(before[cid] is True and after[cid] is False for cid in selected),
                'unchanged': sum(before[cid] == after[cid] for cid in selected),
                'changed_with_unknown': sum(before[cid] != after[cid] and
                                            (before[cid] is None or after[cid] is None) for cid in selected),
                'macro_difference': sum(differences.values()) / n_concepts if n_concepts else None,
                'by_concept_difference': differences,
                'bootstrap': {'method': 'paired concept-cluster percentile; linear percentile interpolation',
                              'draws': 10000, 'seed': 20261006, 'n_concept_clusters': n_concepts,
                              'interval_95': interval},
                'interpretation': 'Unknowns remain unestablished successes in the conservative endpoint, not false judgments. The interval describes these authored concept clusters, not training-seed variability.'}

    return {'all': compare(list(tasks)),
            'primary': compare([cid for cid, task in tasks.items()
                                if task['category'] == 'acquisition' and task['condition'] == 'independent_use']),
            'composition': compare([cid for cid, task in tasks.items() if task['category'] == 'composition'])}


def summarize(config, responses, reviews, output, adjudication=None, baseline_summary=None):
    """Count full correctness without treating an unknown judgment as false.

    Each input is capped at 64 MiB and tasks at 1000. Peak budget: five
    64 MiB inputs x fourfold decoded expansion <1.3 GiB, under a 2 GiB cap.
    Aligned review keys are case IDs; review_id remains an opaque packet ID.
    """
    for path in (config, responses, reviews, adjudication, baseline_summary):
        if path is not None and path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Summary input exceeds 64 MiB bound')
    tasks = json.loads(config.read_text())['tasks']
    raw = rows(responses)
    aligned = json.loads(reviews.read_text())
    task_by_id = {task['id']: task for task in tasks}
    response_by_id = {row['id']: row for row in raw}
    ids = set(task_by_id)
    if not 0 < len(tasks) <= 1000 or len(ids) != len(tasks):
        raise ValueError('Missing, duplicate, or excessive config task IDs')
    if len(response_by_id) != len(raw) or set(response_by_id) != ids:
        raise ValueError('Missing, duplicate, or unexpected response IDs')
    if set(aligned) != {'a', 'b'}:
        raise ValueError('Expected aligned reviewers a and b')
    for name, grades in aligned.items():
        if set(grades) != ids:
            raise ValueError('Missing or unexpected aligned review IDs: '+name)
        packet_ids = [grade['review_id'] for grade in grades.values()]
        if any(not isinstance(rid, str) or not rid for rid in packet_ids) or len(set(packet_ids)) != len(ids):
            raise ValueError('Missing or duplicate opaque review IDs: '+name)
        if any(grade['full_correct'] is not None and type(grade['full_correct']) is not bool
               for grade in grades.values()):
            raise ValueError('full_correct must be boolean or null')
    for cid, task in task_by_id.items():
        row = response_by_id[cid]
        for field in ('family', 'category', 'condition', 'prompt', 'reference'):
            if row[field] != task[field]:
                raise ValueError('Response differs from frozen task: '+cid+' '+field)
        if type(row['capped']) is not bool:
            raise ValueError('Missing or invalid capped value: '+cid)

    values = {name: {cid: grade['full_correct'] for cid, grade in grades.items()}
              for name, grades in aligned.items()}
    consensus = {cid: values['a'][cid] if values['a'][cid] == values['b'][cid] else None
                 for cid in ids}
    disagreements = sorted(cid for cid in ids if values['a'][cid] != values['b'][cid])
    overrides = []
    seen = set()
    if adjudication is not None:
        for entry in json.loads(adjudication.read_text())['entries']:
            cid, field = entry['case_id'], entry['field']
            if cid not in ids or (cid, field) in seen:
                raise ValueError('Unexpected or duplicate adjudication field')
            seen.add((cid, field))
            if field != 'full_correct':
                continue
            value = entry['resolved_value']
            evidence = entry.get('response_evidence', '')
            if value is not None and type(value) is not bool:
                raise ValueError('Adjudicated full_correct must be boolean or null')
            if (not entry.get('rationale', '').strip() or not evidence.strip()
                    or evidence not in response_by_id[cid]['response']):
                raise ValueError('Adjudication requires rationale and exact response evidence')
            consensus[cid] = value
            overrides.append(entry)

    def counts(selected, grades):
        n = len(selected)
        correct = sum(grades[cid] is True for cid in selected)
        return {'n': n, 'true': correct,
                'false': sum(grades[cid] is False for cid in selected),
                'unknown': sum(grades[cid] is None for cid in selected),
                'full_correct_fraction': correct / n if n else None,
                'capped': sum(response_by_id[cid]['capped'] for cid in selected)}

    def grouped(selected, grades, field):
        groups = defaultdict(list)
        for cid in selected:
            groups[task_by_id[cid][field]].append(cid)
        return {key: counts(group, grades) for key, group in sorted(groups.items())}

    def endpoint(selected, grades):
        result = counts(selected, grades)
        concepts = grouped(selected, grades, 'family')
        result.update(by_concept=concepts, n_concepts=len(concepts),
                      macro_full_correct=(sum(r['full_correct_fraction'] for r in concepts.values())
                                          / len(concepts) if concepts else None))
        return result

    def summarize_values(grades):
        primary = [cid for cid, task in task_by_id.items()
                   if task['category'] == 'acquisition' and task['condition'] == 'independent_use']
        composition = [cid for cid, task in task_by_id.items() if task['category'] == 'composition']
        conditions = sorted({task['condition'] for task in tasks})
        return {'all': counts(ids, grades),
                'by_condition': grouped(ids, grades, 'condition'),
                'by_concept': grouped(ids, grades, 'family'),
                'by_category': grouped(ids, grades, 'category'),
                'by_condition_concept': {
                    condition: grouped([cid for cid in ids if task_by_id[cid]['condition'] == condition],
                                       grades, 'family') for condition in conditions},
                'primary': endpoint(primary, grades), 'composition': endpoint(composition, grades)}

    frozen_tasks = {cid: {field: task[field] for field in
                         ('family', 'category', 'condition', 'prompt', 'reference')}
                    for cid, task in task_by_id.items()}
    report = {'n': len(ids), 'frozen_tasks': frozen_tasks,
              'inputs': {'config': str(config), 'responses': str(responses),
                                      'reviews': str(reviews), 'adjudication': str(adjudication) if adjudication else None},
              'reviewers': {name: summarize_values(grades) for name, grades in values.items()},
              'consensus': summarize_values(consensus),
              'consensus_full_correct': dict(sorted(consensus.items())),
              'full_correct_disagreements': disagreements, 'applied_adjudications': overrides,
              'primary_definition': 'Mean over concept families of true full_correct / all acquisition independent_use responses. Unknown judgments remain in each denominator.',
              'limitations': 'Unknown is not a false mathematical judgment. Consensus requires agreement or recorded adjudication. These counts do not establish grader reliability or population competence.'}
    if baseline_summary is not None:
        baseline = json.loads(baseline_summary.read_text())
        if baseline['frozen_tasks'] != frozen_tasks:
            raise ValueError('Baseline and current frozen tasks differ')
        report['inputs']['baseline_summary'] = str(baseline_summary)
        report['paired'] = paired_comparison(frozen_tasks, baseline['consensus_full_correct'], consensus)
    output.mkdir(parents=True, exist_ok=False)
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    prepare_parser=sub.add_parser('prepare')
    for name in ('source','rubric','output'):
        prepare_parser.add_argument('--'+name,type=Path,required=True)
    prepare_parser.add_argument('--calibration',action='store_true')
    recount_parser=sub.add_parser('recount')
    for name in ('packet','reviews-a','reviews-b','output'):
        recount_parser.add_argument('--'+name,type=Path,required=True)
    recount_parser.add_argument('--calibration',type=Path)
    summary_parser=sub.add_parser('summarize')
    for name in ('config','responses','reviews','output'):
        summary_parser.add_argument('--'+name,type=Path,required=True)
    summary_parser.add_argument('--adjudication',type=Path)
    summary_parser.add_argument('--baseline-summary',type=Path)
    args=p.parse_args()
    if args.command=='prepare':
        result=prepare(args.source,args.rubric,args.output,args.calibration)
    elif args.command=='recount':
        result=recount(args.packet,args.reviews_a,args.reviews_b,args.calibration,args.output)
    else:
        result=summarize(args.config,args.responses,args.reviews,args.output,args.adjudication,args.baseline_summary)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
