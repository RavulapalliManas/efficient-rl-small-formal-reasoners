"""Bounded, authored records and matched information views for mathematical RLP.

At most 2,000 rows x 16 KiB <32 MiB of serialized text; use a 2 GiB CPU cap.
No model generation, theorem search, Lean, or optimizer. Reference steps are
mathematically reviewed templates; arithmetic certificates have exact checks.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random

SOURCE = 'https://twjudson.github.io/aata-files/aata-html/integers-section-division-algorithm.html'
MODULAR = 'https://twjudson.github.io/aata-files/aata-html/groups-section-mod-n-sym.html'
INSTRUCTION = 'Give a complete answer with the mathematical steps and a clear conclusion. Check the conditions you use. Do not write code.'
FACTS = {
    'divisibility': ('For integers d,x, d divides x iff x=d*u for some integer u. This includes d=0: zero divides only zero.',
                     'For integers d,x, divisibility means x=d*u with u in ___.',
                     'd divides x iff d=x*u for an integer u.'),
    'cancellation': ('For integers a,x,y, if a is nonzero and a*x=a*y then x=y. Without a nonzero assumption the implication can fail.',
                     'Integer cancellation from a*x=a*y requires a to be ___.',
                     'From a*x=a*y one can always conclude x=y, including a=0.'),
    'division': ('For every integer a and positive integer b, unique integers q,r satisfy a=b*q+r and 0<=r<b.',
                 'For integer a and b>0, a=b*q+r with integer q,r and remainder bounds ___.',
                 'Euclidean division requires 0<r<=b.'),
    'bezout': ('For integers a,b not both zero, gcd(a,b) is the positive gcd and equals a*u+b*v for some integers u,v. Set gcd(0,0)=0 separately.',
               'For integer a,b not both zero, gcd(a,b)=a*u+b*v for u,v in ___.',
               'Bezout coefficients must be positive integers.'),
    'inverse': ('For integers a,m with m>1, an inverse of a modulo m exists iff gcd(a,m)=1. If it exists, its unique normalized representative x satisfies 0<=x<m.',
                'For m>1, an inverse of a modulo m exists exactly when gcd(a,m)=___.',
                'Every nonzero integer a has an inverse modulo every m>1.'),
    'gcd': ('For integers a,b,q, the common divisors of a,b equal those of b,a-q*b. Hence gcd(a,b)=gcd(b,a-q*b), with nonnegative gcd and gcd(0,0)=0.',
            'Compare the common divisors of a,b with those of b,a-q*b; show both ___ of divisibility.',
            'Subtracting any integer from one operand always preserves the gcd.'),
    'congruence': ('For integers x,y and m>1, x is congruent to y modulo m iff x-y=m*k for an integer k.',
                   'For m>1, congruence x=y modulo m means x-y=m*k with k in ___.',
                   'Congruence x=y modulo m means x-y=k/m for an integer k.'),
    'modular_cancellation': ('For integers a,m with m>1, multiplication by a can be cancelled from every congruence modulo m iff gcd(a,m)=1.',
                             'For m>1, cancellation of a from all congruences modulo m requires gcd(a,m)=___.',
                             'A nonzero integer can always be cancelled modulo m>1.')
}
TEMPLATES = ('linear', 'chain', 'cancellation', 'division', 'bezout', 'inverse', 'gcd', 'congruence', 'modular_cancellation')
CONCEPT = dict(linear='divisibility', chain='divisibility', cancellation='cancellation',
               division='division', bezout='bezout', inverse='inverse', gcd='gcd', congruence='congruence',
               modular_cancellation='modular_cancellation', composition='divisibility')


def bezout(a, b):
    old_r, r, old_s, s, old_t, t = abs(a), abs(b), 1, 0, 0, 1
    while r:
        q = old_r // r
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
        old_t, t = t, old_t - q * t
    return old_r, old_s * (-1 if a < 0 else 1), old_t * (-1 if b < 0 else 1)


def make_record(kind, index, split):
    c = index + 2
    p = {'c': c}
    if kind == 'linear':
        assumptions = ['d,x,y are integers', 'd divides x', 'd divides y']
        goal = f'Prove that d divides {c}*x-({c+1})*y. Include the case d=0.'
        steps = ['Choose integers u,v with x=d*u and y=d*v.',
                 f'{c}*x-({c+1})*y=d*({c}*u-({c+1})*v).',
                 f'The integer {c}*u-({c+1})*v is a witness. No division by d is needed, so d=0 is included.']
        method = 'Unpack the hypotheses, substitute into the requested expression, then construct an integer witness.'
    elif kind == 'chain':
        assumptions = ['a,b,z are integers', 'a divides b', 'b divides z']
        goal = f'Prove that a divides {c}*z. Include a=0.'
        steps = ['Choose integers u,v such that b=a*u and z=b*v.',
                 f'{c}*z={c}*(a*u)*v=a*({c}*u*v).',
                 f'The integer {c}*u*v is a witness; no nonzero hypothesis is needed.']
        method = 'Unpack both divisibility assumptions, substitute one equality into the other, and construct a witness.'
    elif kind == 'cancellation':
        assumptions = ['a,x,y are integers', 'a*x=a*y']
        goal = f'A student concludes {c}*x={c}*y without assuming a is nonzero. Is this valid? Give a counterexample if invalid and state a sufficient additional condition.'
        steps = [f'Take a=0,x=0,y=1. Then a*x=a*y=0 but {c}*x=0 and {c}*y={c}.',
                 'The conclusion is not valid in general.',
                 f'If a is nonzero, a*(x-y)=0 implies x=y in the integers, hence {c}*x={c}*y.']
        method = 'Check zero cases first, then state the condition under which cancellation is valid.'
    elif kind == 'division':
        a, b = (0 if index % 4 == 0 else (-1 if index % 2 else 1) * (7*c+3)), c+3
        q, r = divmod(a, b)
        p.update(a=a, b=b, q=q, r=r)
        assumptions = [f'a={a} and b={b} are integers']
        goal = 'Find the Euclidean quotient and remainder. Verify the equation and remainder bounds.'
        steps = [f'The divisor {b} is positive.', f'q={q}, r={r}; {a}={b}*({q})+{r}.',
                 f'0<={r}<{b}, so these are the Euclidean quotient and remainder.']
        method = 'Use the integer division equation, then check the divisor sign and both remainder bounds.'
    elif kind == 'bezout':
        a, b = (0 if index % 4 == 0 else (-1 if index % 2 else 1)*6*c), 9*(c+1)
        g, u, v = bezout(a, b)
        p.update(a=a, b=b, g=g, u=u, v=v)
        assumptions = [f'a={a}, b={b} are integers']
        goal = 'Find gcd(a,b) and integer Bezout coefficients. Justify that the claimed gcd is correct.'
        steps = [f'Let g={g}, u={u}, v={v}. Then {a}*({u})+{b}*({v})={g}.',
                 f'{g}>0 and {g} divides both {a} and {b}.',
                 f'Every common divisor divides the linear combination {g}. Thus gcd({a},{b})={g}.']
        method = 'Give coefficients, verify the identity, and show both common-divisor conditions for the gcd.'
    elif kind == 'inverse':
        m = 2*c+5 if index % 2 else 6*c+6
        a = (-1 if index % 3 == 1 else 1)*(c+1) if index % 2 else (0 if index % 4 == 0 else 2*c+2)
        while index % 2 and math.gcd(a,m) != 1:
            a += 1 if a > 0 else -1
        g, u, v = bezout(a, m)
        exists = g == 1
        x = u % m if exists else None
        p.update(a=a, m=m, g=g, u=u, v=v, exists=exists, x=x)
        assumptions = [f'a={a}, m={m} are integers']
        goal = 'Does a have an inverse modulo m? If yes, give the least nonnegative inverse and verify it. If no, prove nonexistence.'
        if exists:
            steps = [f'{m}>1 and {a}*({u})+{m}*({v})=1, so gcd({a},{m})=1.',
                     f'x={x}, 0<={x}<{m}, and {a}*{x}-1={m}*{(a*x-1)//m}.',
                     f'Therefore {x} is the least nonnegative inverse.']
        else:
            steps = [f'{g}>1 divides both {a} and {m}.',
                     f'If {a}*x were congruent to 1 modulo {m}, {g} would divide both {a}*x and {a}*x-1.',
                     f'Then {g} would divide 1, impossible. No inverse exists.']
        method = 'Check coprimality; verify a normalized inverse or derive a divisibility contradiction.'
    elif kind == 'gcd':
        assumptions = ['a,b are integers']
        goal = f'Prove gcd(a,b)=gcd(b,a-{c}*b), using nonnegative gcd and gcd(0,0)=0.'
        steps = [f'If d divides a and b, then d divides a-{c}*b.',
                 f'If d divides b and a-{c}*b, then d divides (a-{c}*b)+{c}*b=a.',
                 'The two pairs have the same common divisors, so their nonnegative gcds agree, including the all-zero case.']
        method = 'Show that every common divisor of either pair divides the other pair; handle the zero convention.'
    elif kind == 'modular_cancellation':
        m, a = 2*(c+1), (2*c+1 if index % 2 else 2*c)
        g,u,v = bezout(a,m)
        p.update(a=a,m=m,g=g,u=u,v=v)
        assumptions = [f'a={a} and m={m} are integers']
        goal = 'Can a be cancelled from every congruence modulo m? Prove this or give a counterexample.'
        if g == 1:
            steps = [f'{a}*({u})+{m}*({v})=1.',
                     f'If {a}*x is congruent to {a}*y modulo {m}, then {m} divides {a}*(x-y).',
                     f'Multiplying the identity by x-y shows {m} divides x-y. Thus cancellation is valid.']
        else:
            steps = [f'Take x=0,y={m//g}. Then {a}*y={a*m//g} is divisible by {m}, so a*x is congruent to a*y.',
                     f'But 0<{m//g}<{m}, so x is not congruent to y modulo {m}.',
                     'Therefore cancellation is not valid for every congruence.']
        method = 'Check coprimality. Use a Bezout identity for valid cancellation, or exhibit two distinct residue classes with the same product.'
    elif kind == 'composition':
        assumptions = ['a,b,x,y are integers', 'a divides b', 'b divides x', 'b divides y']
        goal = f'Prove a divides {c}*x-({c+1})*y, including a=0.'
        steps = ['Choose integers u,v,w with b=a*u, x=b*v and y=b*w.',
                 'Substitution gives x=a*u*v and y=a*u*w.',
                 f'{c}*x-({c+1})*y=a*(u*({c}*v-({c+1})*w)).',
                 f'The integer u*({c}*v-({c+1})*w) is a witness, without division by a.']
        method = 'Compose the divisibility witnesses, then combine the resulting equalities.'
    else:
        assumptions = ['x,y,u,v,m are integers', 'm>1', 'x is congruent to y modulo m', 'u is congruent to v modulo m']
        goal = f'Prove {c}*x+u is congruent to {c}*y+v modulo m.'
        steps = ['Choose integers k,l with x-y=m*k and u-v=m*l.',
                 f'({c}*x+u)-({c}*y+v)={c}*(x-y)+(u-v)=m*({c}*k+l).',
                 'The difference is divisible by m, proving the congruence.']
        method = 'Write both differences as modulus multiples, combine them, and use the congruence definition.'
    concept = CONCEPT[kind]
    step_operations = {
        'linear':['UNPACK','DISTRIBUTE','WITNESS'], 'chain':['UNPACK','SUBSTITUTE','WITNESS'],
        'cancellation':['COUNTEREXAMPLE','REJECT_INFERENCE','CANCEL'],
        'division':['CHECK_CONDITION','VERIFY_EQUALITY','VERIFY_BOUND'],
        'bezout':['VERIFY_EQUALITY','DIVISIBILITY','GCD_CHARACTERIZATION'],
        'inverse':['BEZOUT','VERIFY_EQUALITY','CONCLUDE'] if p.get('exists') else ['DIVISIBILITY','ASSUME_NEGATION','CONTRADICTION'],
        'gcd':['DIVISIBILITY','LINEAR_COMBINATION','GCD_CHARACTERIZATION'],
        'congruence':['UNPACK','DISTRIBUTE','WITNESS'],
        'modular_cancellation':['BEZOUT','UNPACK','LINEAR_COMBINATION'] if p.get('g')==1 else ['COUNTEREXAMPLE','VERIFY_BOUND','REJECT_INFERENCE'],
        'composition':['UNPACK','SUBSTITUTE','DISTRIBUTE','WITNESS']
    }
    record_id = f'{kind}_{index}'
    return {'id': record_id, 'family_id': record_id, 'template_family': kind, 'split': split,
            'concept_ids': [concept], 'operator_ids': list(dict.fromkeys(step_operations[kind])), 'assumptions': assumptions,
            'goal': goal, 'parameters': p, 'method': method, 'representation': 'numeric' if kind in ('division','bezout','inverse') else 'symbolic',
            'reference_steps': [{'step': i+1, 'claim': text, 'operation': step_operations[kind][i],
                                 'premises': ['assumptions'] + list(range(1, i+1)),
                                 'scope': 'current problem', 'justification': 'integer arithmetic and the declared knowledge'}
                                for i, text in enumerate(steps)],
            'dependencies': [[i, i+1] for i in range(1, len(steps))],
            'source': MODULAR if concept in ('inverse','congruence','modular_cancellation') else SOURCE,
            'review_status': 'unreviewed'}


def validate_record(record):
    p, kind = record['parameters'], record['template_family']
    if p['c'] <= 0 or not record['reference_steps'] or not record['assumptions']:
        return False
    if kind == 'division':
        return p['b'] > 0 and p['a'] == p['b']*p['q']+p['r'] and 0 <= p['r'] < p['b']
    if kind == 'bezout':
        return p['g'] == math.gcd(p['a'], p['b']) and p['a']*p['u']+p['b']*p['v'] == p['g']
    if kind == 'inverse':
        if p['m'] <= 1 or p['exists'] != (math.gcd(p['a'],p['m']) == 1):
            return False
        return (0 <= p['x'] < p['m'] and (p['a']*p['x']-1) % p['m'] == 0) if p['exists'] else p['x'] is None
    if kind == 'modular_cancellation':
        return p['m']>1 and p['g']==math.gcd(p['a'],p['m']) and p['a']*p['u']+p['m']*p['v']==p['g']
    # Universal reference arguments require mathematical review, not finite testing.
    return kind in TEMPLATES + ('composition',)


def render(record, view, repeat=0, phase=None, recognition_rotation=None):
    concept = record['concept_ids'][0]
    fact, partial, false = FACTS[concept]
    problem = 'Assumptions: ' + '; '.join(record['assumptions']) + '.\nProblem: ' + record['goal']
    target = '\n'.join(f'{s["step"]}. {s["claim"]}' for s in record['reference_steps'])
    knowledge, structure = 'absent', 'absent'
    if view == 'recall':
        topic = {'divisibility':'integer divisibility a | b',
                 'cancellation':'cancellation from the integer equality a*x=a*y',
                 'division':'Euclidean division of integers',
                 'bezout':'the Bezout identity for integers',
                 'inverse':'multiplicative inverses of an integer a modulo an integer m',
                 'gcd':'the gcd of two integers under a Euclidean subtraction step',
                 'congruence':'congruence of integers modulo an integer m',
                 'modular_cancellation':'cancellation of a from a*x congruent to a*y modulo an integer m'}[concept]
        problem = f'State the mathematical rule for {topic}. Include its domain and required conditions.'
        target = fact
    elif view == 'full':
        knowledge, structure = 'full', 'sequence'
        problem += '\nKnowledge: ' + fact + '\nMethod: ' + record['method']
    elif view == 'partial':
        knowledge, structure = 'partial', 'partial_sequence'
        problem += '\nKnowledge: ' + partial + '\nComplete the missing condition before solving.\nMethod: Check conditions, then justify each operation.'
        target = fact + '\n' + target
    elif view == 'recognition':
        knowledge, structure = 'recognition', 'absent'
        rotation = record['parameters']['c'] % 2 if recognition_rotation is None else recognition_rotation
        choices = [fact, false] if rotation else [false, fact]
        problem += '\nWhich rule is valid? Explain your choice, then solve.\nA. ' + choices[0] + '\nB. ' + choices[1]
        target = 'The valid rule is: ' + fact + '\n' + target
    problem += '\n' + INSTRUCTION
    stage = ('full','partial','recognition','recall','independent').index(view)
    return {'id': f'{record["id"]}:{view}:{repeat}', 'example_id': record['id'],
            'family_id': record['family_id'], 'template_family': record['template_family'],
            'concept_ids': record['concept_ids'], 'operator_ids': record['operator_ids'],
            'representation': record['representation'], 'view_id': view, 'repeat_index': repeat,
            'curriculum_stage': stage if phase is None else phase, 'replay': bool(repeat),
            'knowledge_mask': knowledge, 'structure_mask': structure,
            'public': {'prompt': problem}, 'private': {'target_text': target,
                'reference_steps': record['reference_steps'], 'source': record['source']}}


def build_study():
    records = [make_record(kind, index, split) for split, indices in
               [('train', range(8)), ('validation', range(10,12)), ('heldout', range(20,23))]
               for kind in TEMPLATES for index in indices]
    records += [make_record('composition', index, 'heldout') for index in (30,31)]
    assert all(validate_record(r) for r in records)
    train = [r for r in records if r['split'] == 'train']
    ordered = []
    for phase, view in enumerate(('full','partial','recognition','recall','independent')):
        for i, record in enumerate(train):
            ordered.append(render(record, view))
            if phase and (i+1) % 4 == 0:
                ordered.append(render(record, 'full', repeat=phase, phase=phase))
    shuffled = list(ordered)
    random.Random(20261006).shuffle(shuffled)
    return {'records': records, 'ordered': ordered, 'shuffled': shuffled}


def export_study(output, review_path):
    """Require source review before admission; keep final references outside arms."""
    study = build_study()
    review = json.loads(Path(review_path).read_text())
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if review['source_sha256'] != source_hash or set(review['accepted_templates']) != set(TEMPLATES + ('composition',)):
        raise ValueError('Source review must admit this exact generator and every template')
    output.mkdir(parents=True, exist_ok=False)
    def write(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    def public(row):
        return {k:v for k,v in row.items() if k != 'private'}
    def private(row):
        return {'id': row['id'], **row['private']}
    for arm in ('ordered','shuffled'):
        folder = output / arm
        write(folder/'train.jsonl', [public(r) for r in study[arm]])
        write(folder/'private/train_references.jsonl', [private(r) for r in study[arm]])
        validation = [render(r,'independent') for r in study['records'] if r['split'] == 'validation']
        write(folder/'validation.jsonl', [public(r) for r in validation])
        write(folder/'private/validation_references.jsonl', [private(r) for r in validation])
        (folder/'manifest.json').write_text(json.dumps({'schema':'mathematical_rlp_v1', 'arm':arm,
            'source_sha256':source_hash, 'review':review, 'presentations':len(study[arm]),
            'heldout_policy':'Final references excluded from training directories'}, indent=2)+'\n')
    interlude = [r for kind in ('linear','chain','cancellation','division') for r in
                 [r for r in study['ordered'] if r['view_id']=='full' and not r['replay'] and r['template_family']==kind][:4]]
    assert all(not any(word in r['public']['prompt'].lower() for word in ('inverse','coprime','modulo')) for r in interlude)
    write(output/'retention/train.jsonl', [public(r) for r in interlude])
    write(output/'retention/private/train_references.jsonl', [private(r) for r in interlude])
    for split in ('train','validation','heldout'):
        records = [dict(r, review_status='accepted_by_mathematical_review') for r in study['records'] if r['split']==split]
        write(output/'private'/f'{split}_records.jsonl', records)
    for split in ('validation','heldout'):
        rows = [render(r,view) for r in study['records'] if r['split']==split
                for view in ('independent','full')]
        # Recall repeats are one item per concept, never one independent observation per rendering.
        first = {}
        for r in study['records']:
            if r['split']==split:
                first.setdefault(r['concept_ids'][0], r)
        rows += [render(r,view,recognition_rotation=i%2) for i,r in enumerate(first.values()) for view in ('recall','recognition')]
        tasks = [{'id':r['id'], 'family':r['concept_ids'][0], 'family_id':r['family_id'],
                  'category':'composition' if r['template_family']=='composition' else 'acquisition',
                  'condition':{'full':'supplied_use','independent':'independent_use','recall':'recall','recognition':'recognition'}[r['view_id']],
                  'prompt':r['public']['prompt'], 'reference':r['private']['target_text'],
                  'rubric':'Use mathematical_grading_v1; accept valid alternate methods; a correct conclusion with invalid reasoning is not fully correct.',
                  'source':r['private']['source']} for r in rows]
        write(output/'evaluation'/f'{split}_public.jsonl', [{k:v for k,v in t.items() if k not in ('reference','rubric')} for t in tasks])
        write(output/'evaluation'/f'{split}_references.jsonl', [{'id':t['id'],'reference':t['reference'],'rubric':t['rubric']} for t in tasks])
        # Evaluator reads references for logging only; task_messages forwards prompt alone.
        config = {'seed':20261006,'cpu_threads':8,'memory_limit_gib':8,'batch_size':4,
                  'max_wall_seconds_per_model':10800,'max_prompt_tokens':2048,'max_new_tokens':1024,
                  'length_views':[256,512,1024],'tasks':tasks,
                  'checkpoint':'early RLP checkpoint-118','scope':'Exploratory instance transfer; shared proof templates. No broad undergraduate claim.'}
        (output/'evaluation'/f'{split}_config.json').write_text(json.dumps(config,indent=2)+'\n')
    manifest = {'schema':'mathematical_rlp_v1','seed':20261006,'source_sha256':source_hash,
        'counts':dict(Counter(r['split'] for r in study['records'])),
        'presentations':{a:len(study[a]) for a in ('ordered','shuffled')},
        'exposure':{a:dict(Counter((r['template_family']+':'+r['view_id']) for r in study[a])) for a in ('ordered','shuffled')},
        'same_multiset':Counter(r['id'] for r in study['ordered'])==Counter(r['id'] for r in study['shuffled']),
        'split_unit':'Canonical mathematical instance; all support views and replay descendants remain together.',
        'template_overlap':'Shared proof templates across splits are intentional. This is instance transfer, not unseen proof-family transfer.',
        'limitations':['Small authored corpus; no human validation or Lean certification.',
                       'Recall repeats exposed facts and measures access, not unseen-concept acquisition.',
                       'Heldout generated now; excluded from training and calibration. Do not tune against heldout results.'],
        'review':review}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
