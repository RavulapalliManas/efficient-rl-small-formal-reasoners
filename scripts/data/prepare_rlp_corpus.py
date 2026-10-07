"""Prepare RLP acquisition text on cpu-angad, without evaluation solutions.

Memory: at most five XML files <=2 MiB each; <=1000 blocks x64 KiB <64 MiB.
One writer, 1 GiB service cap, 600-second deadline. No model loads.
Keep the complete licensed source and its license beside extracted records.
"""
import argparse
import hashlib
import json
import math
import random
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET

CHAPTERS = ["sets", "integers", "cyclic", "crypt"]


def text_blocks(xml):
    # PreTeXt entity declarations live in the main book. Retain unresolved
    # entity names as text rather than discarding their mathematical symbols.
    xml = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)([A-Za-z][\w-]*);", r"\1", xml)
    root = ET.fromstring(xml)
    blocks = []
    def visit(node, path):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"exercises", "exercise", "solution", "hint", "answer", "sage", "figure", "references"}:
            return
        if tag in {"theorem", "proposition", "lemma", "corollary", "definition", "example", "p"}:
            text = re.sub(r"\s+", " ", " ".join(node.itertext())).strip()
            if text:
                blocks.append((node.get("{http://www.w3.org/XML/1998/namespace}id", path), text))
            return
        for i, child in enumerate(node):
            if child.tag.rsplit("}", 1)[-1] != "title":
                visit(child, path + "/" + str(i))
    visit(root, "root")
    return blocks


def primitive_questions():
    """Held-out computational applications; never enter acquisition text."""
    rows = []
    pairs = [(21, 55), (35, 64), (42, 99), (65, 91), (77, 143), (96, 152), (121, 187), (169, 247)]
    for a, b in pairs:
        rows.append({"id": f"gcd_{a}_{b}", "family": "gcd", "question": f"Compute gcd({a}, {b}).", "answer": math.gcd(a, b)})
    for a, n in [(3, 17), (5, 23), (7, 29), (9, 31), (11, 37), (13, 41), (17, 43), (19, 47)]:
        rows.append({"id": f"inverse_{a}_{n}", "family": "modular_inverse", "question": f"Find the least nonnegative inverse of {a} modulo {n}.", "answer": pow(a, -1, n)})
    for a, k, n in [(3, 8, 17), (5, 7, 23), (7, 9, 29), (9, 11, 31), (11, 13, 37), (13, 15, 41), (17, 17, 43), (19, 19, 47)]:
        rows.append({"id": f"power_{a}_{k}_{n}", "family": "modular_power", "question": f"Compute the least nonnegative residue of {a}^{k} modulo {n}.", "answer": pow(a, k, n)})
    for n in [18, 24, 28, 30, 36, 40, 45, 60]:
        rows.append({"id": f"totient_{n}", "family": "totient", "question": f"Compute Euler's totient phi({n}).", "answer": sum(math.gcd(k, n) == 1 for k in range(1, n + 1))})
    return rows


def prime_questions(split):
    """Separate integer instances for curriculum validation and held-out use."""
    numbers = {'validation': [101, 102, 103, 105], 'heldout': [131, 133, 137, 143]}[split]
    rows = []
    for i, n in enumerate(numbers):
        factors = [d for d in range(2, n + 1) if n % d == 0 and
                   all(d % k for k in range(2, math.isqrt(d) + 1))]
        prime = len(factors) == 1 and factors[0] == n
        a = n if i == 2 else 2
        tasks = [
            ('primality', f'Is {n} prime? Answer 1 for yes and 0 for no.', int(prime)),
            ('smallest_prime_factor', f'Find the smallest prime factor of {n}.', min(factors)),
            ('distinct_prime_factors', f'How many distinct prime factors does {n} have?', len(factors)),
            ('fermat_applicable', f'Do the hypotheses that p is prime and p does not divide a both hold for a={a}, p={n}? Answer 1 for yes and 0 for no.', int(prime and a % n != 0)),
        ]
        for family, question, answer in tasks:
            rows.append({'id': f'{split}_{family}_{n}', 'n': n, 'family': family,
                         'question': question, 'answer': answer})
    return rows


def primitive_structure_blocks():
    """Project-authored acquisition text; no evaluation solutions or SFT loss."""
    blocks = []
    for a, b in [(26, 7), (39, 8), (46, 9), (57, 10), (68, 11), (79, 12), (86, 13), (95, 14)]:
        q, r = divmod(a, b)
        blocks.append((0, 'division_gcd', f'Euclidean division writes a = b*q + r with b positive and 0 <= r < b. For a={a} and b={b}, the quotient is {q} and the remainder is {r}: {a} = {b}*{q} + {r}. A common divisor of a and b divides r=a-b*q. Conversely a common divisor of b and r divides a=b*q+r. Therefore gcd({a},{b})=gcd({b},{r})={math.gcd(a,b)}. This invariant allows repeated division until the remainder is zero.'))
    structures = [
        ('divisibility_witness', 'For integers a and b, a divides b means there exists an integer k such that b=a*k. A proof must supply k and verify this equation. If b=a*k and c=b*l, substitution gives c=a*(k*l). The witness k*l proves that a divides c. The witness can depend on the given hypotheses.'),
        ('quantifiers', 'To prove a statement for every natural number n, fix an arbitrary n and reason without imposing extra assumptions. To prove an implication P implies Q, assume P and derive Q. To prove that an object exists, construct a witness and verify its required property. Checking selected numerical examples does not prove a universal statement.'),
        ('induction', 'Let S(n)=1+2+...+n, with S(0)=0. Prove 2*S(n)=n*(n+1) by induction. At n=0 both sides are zero. Assume 2*S(k)=k*(k+1). Since S(k+1)=S(k)+(k+1), twice the new sum equals k*(k+1)+2*(k+1)=(k+1)*(k+2). This proves the successor case and completes induction.'),
        ('contradiction', 'A proof by contradiction assumes the negation of the desired conclusion and derives an impossibility. Suppose all primes form a finite list p_1,...,p_k. Let N=p_1*...*p_k+1. Since N>1, it has a prime divisor q. No listed prime divides N, because each leaves remainder one. Thus q is absent from the allegedly complete list. N itself need not be prime.'),
    ]
    blocks.extend((1, name, text) for name, text in structures)
    primes = [
        ('prime_definition', 'An integer p is prime when p>1 and its positive divisors are exactly 1 and p. The integer 1 is not prime. If n>1 is composite, write n=a*b with a>1 and b>1. At least one factor is at most the square root of n. Thus trial division up to that bound can decide primality. A divisor strictly between 1 and n is a certificate of compositeness.'),
        ('euclid_lemma', 'Euclid\'s lemma states that if a prime p divides a*b, then p divides a or p divides b. If p does not divide a, primality gives gcd(p,a)=1. Bezout\'s identity supplies integers u,v with u*p+v*a=1. Multiplying by b gives u*p*b+v*a*b=b. Both terms on the left are divisible by p, so p divides b. The primality hypothesis is necessary: 6 divides 2*3 but divides neither factor.'),
        ('factorization', 'Every integer n>1 is a product of primes. Existence follows by strong induction: a prime is already a product of one prime; a composite is a product of smaller positive integers greater than one, each covered by the induction hypothesis. For uniqueness, Euclid\'s lemma matches a prime in one factorization to a prime in the other. Cancel that common factor and repeat. Order does not affect uniqueness.'),
        ('fermat_conditions', 'Fermat\'s little theorem states that if p is prime and p does not divide a, then a^(p-1) is congruent to 1 modulo p. The equivalent form a^p is congruent to a modulo p holds for all integers a when p is prime. Check the prime and nondivisibility hypotheses before using the first form. A single base satisfying the congruence does not certify primality. Modular inverse existence instead requires gcd(a,m)=1 for a modulus m>1.'),
    ]
    blocks.extend((2, name, text) for name, text in primes)
    return blocks


PRIMITIVES = {
    'witness': (0, [], 'a divides b means b=a*k for an integer k. Construct k and check the equation. For example, 4 divides 12 with witness 3.'),
    'substitution': (0, [], 'Equal quantities can replace one another. If b=a*k and c=b+d, substitution gives c=a*k+d.'),
    'counterexample': (0, ['witness'], 'One counterexample disproves a universal statement. To refute that every multiple of a is a multiple of b, construct a positive multiple of a that is not divisible by b.'),
    'quotient': (0, [], 'For positive b, Euclidean division gives a=b*q+r with 0<=r<b. For example, 17=5*3+2.'),
    'remainder': (1, ['quotient'], 'The remainder is a-b*q after choosing the Euclidean quotient. It must be nonnegative and smaller than b.'),
    'gcd': (1, ['remainder', 'witness'], 'The common divisors of a,b equal those of b,a-b*q. Repeat Euclidean division until a remainder is zero; the last nonzero remainder is the gcd.'),
    'bezout': (1, ['gcd', 'substitution'], 'For coprime a,b, Bezout coefficients satisfy a*x+b*y=1. Choose the unique x with 0<=x<b; then y=(1-a*x)/b is an integer.'),
    'inverse': (2, ['bezout'], 'An inverse of a modulo m exists exactly when gcd(a,m)=1. The normalized inverse x satisfies 0<=x<m and a*x=1 modulo m.'),
    'prime_condition': (2, ['witness'], 'Euclid\'s lemma says: if p is prime and p divides a*b, then p divides a or b. The primality condition matters: 6 divides 2*3 but divides neither factor.'),
    'prime_factor': (2, ['witness', 'prime_condition'], 'The smallest divisor greater than one of an integer greater than one is prime. Test divisibility in increasing order; if no divisor up to the square root exists, the integer is prime.'),
    'congruence': (2, ['witness', 'remainder'], 'a and b are congruent modulo m precisely when m divides a-b. The difference may be negative.'),
    'induction': (3, ['substitution'], 'Induction requires a base case and a successor argument. For S(n)=c*(1+2+...+n), S(n+1)=S(n)+c*(n+1). Knowing the added term alone is not a complete induction proof.'),
    'contradiction': (3, ['prime_condition', 'remainder'], 'For a finite list of primes, let N be their product plus one. Every listed prime leaves remainder one in N. A prime divisor of N is therefore absent from the list. N itself need not be prime.'),
    'cancellation': (3, ['gcd', 'congruence'], 'Multiplication by a can be cancelled from every congruence modulo m exactly when gcd(a,m)=1. For example, cancelling 2 modulo 6 is invalid.'),
}


def verify_primitive(row, answer):
    """Independent arithmetic predicates; no credit for formatting or prose."""
    if not isinstance(answer, int):
        return False
    p, kind = row['parameters'], row['family']
    a, b = p['a'], p['b']
    if kind == 'witness': return a * answer == b
    if kind == 'substitution': return answer == a * p['k'] + p['d']
    if kind == 'counterexample': return 0 < answer <= a and answer % a == 0 and answer % b != 0
    if kind == 'quotient': return 0 <= a - b * answer < b
    if kind == 'remainder': return 0 <= answer < b and (a - answer) % b == 0
    if kind == 'gcd': return answer == math.gcd(a, b)
    if kind in ('bezout', 'inverse'): return 0 <= answer < b and (a * answer - 1) % b == 0
    if kind == 'prime_condition':
        prime = a >= 2 and all(a % d for d in range(2, math.isqrt(a) + 1))
        return answer == int(prime)
    if kind == 'prime_factor': return 1 < answer <= a and a % answer == 0 and all(a % d for d in range(2, answer))
    if kind == 'congruence': return answer == int((a - b) % p['m'] == 0)
    if kind == 'induction': return answer == sum(a * j for j in range(1, b + 2)) - sum(a * j for j in range(1, b + 1))
    if kind == 'contradiction': return answer == (a * b + 1) % a
    if kind == 'cancellation': return answer == int(math.gcd(a, b) == 1)
    if kind == 'witness_sum': return a * answer == a * p['k'] + a * p['l']
    if kind == 'witness_chain': return a * answer == a * p['k'] * p['l']
    raise ValueError(kind)


def structured_curriculum():
    """Instructional contexts, hidden mathematical targets, and replay.

    At most 1000 records x8 KiB <8 MiB; output bound 64 MiB.
    Shared templates test new instances; held-out compositions use unseen templates.
    """
    rng = random.Random(20261005)
    result = {'train': [], 'validation': [], 'heldout': []}
    seen, questions_seen = set(), set()
    for family, (stage, dependencies, definition) in PRIMITIVES.items():
        for split, count in [('train', 32), ('validation', 4), ('heldout', 6)]:
            made = 0
            while made < count:
                a, b, k, d = (rng.randint(2, 60) for _ in range(4))
                p = {'a': a, 'b': b, 'k': k, 'd': d}
                if family == 'witness':
                    p['b'] = a * k; answer = k
                    question = f'Find the integer k satisfying {p["b"]}={a}*k.'
                elif family == 'substitution':
                    answer = a * k + d
                    question = f'If b={a}*{k} and c=b+{d}, what is c?'
                elif family == 'counterexample':
                    if a % b == 0: continue
                    answer = a
                    question = f'Refute the claim that every multiple of {a} is divisible by {b}. Return the smallest positive counterexample.'
                elif family == 'quotient':
                    p['a'] = a * b + k; answer = p['a'] // b
                    question = f'Find the Euclidean quotient when {p["a"]} is divided by {b}.'
                elif family == 'remainder':
                    p['a'] = a * b + k; answer = p['a'] % b
                    question = f'Find the Euclidean remainder when {p["a"]} is divided by {b}.'
                elif family == 'gcd':
                    p['a'], p['b'] = a * (1 + k % 5), b * (1 + k % 5)
                    answer = math.gcd(p['a'], p['b'])
                    question = f'Compute gcd({p["a"]},{p["b"]}).'
                elif family in ('bezout', 'inverse'):
                    if math.gcd(a, b) != 1: continue
                    answer = pow(a, -1, b)
                    question = (f'Find x with 0<=x<{b} for which {a}*x+{b}*y=1 has an integer y.' if family == 'bezout'
                                else f'Find the least nonnegative inverse of {a} modulo {b}.')
                elif family == 'prime_condition':
                    a = rng.randint(2, 300); p['a'] = a
                    answer = int(all(a % j for j in range(2, math.isqrt(a) + 1)))
                    if answer != made % 2: continue
                    question = f'Does {a} satisfy the primality hypothesis of Euclid\'s lemma? Give 1 for yes or 0 for no.'
                elif family == 'prime_factor':
                    a = rng.randint(61, 2000); p['a'] = a
                    answer = next(d for d in range(2, a + 1) if a % d == 0)
                    question = f'Find the smallest prime factor of {a}.'
                elif family == 'congruence':
                    p['m'] = d; p['a'] = b + d * k + made % 2; answer = int(made % 2 == 0)
                    question = f'Are {p["a"]} and {b} congruent modulo {d}? Give 1 for yes or 0 for no.'
                elif family == 'induction':
                    answer = a * (b + 1)
                    question = f'Let S(n)={a}*(1+2+...+n). In the successor step at n={b}, what is S({b+1})-S({b})?'
                elif family == 'contradiction':
                    answer = 1
                    question = f'Set N={a}*{b}+1. What remainder does N leave upon division by {a}?'
                elif family == 'cancellation':
                    answer = int(math.gcd(a, b) == 1)
                    if answer != made % 2: continue
                    question = f'Can multiplication by {a} be cancelled for all congruences modulo {b}? Give 1 for yes or 0 for no.'
                # Canonical operands catch paraphrased inverse/Bezout duplicates too.
                identity = ('inverse' if family == 'bezout' else family, p['a'], p['b'], p.get('m'), k if family == 'substitution' else None, d if family == 'substitution' else None)
                if identity in seen or question in questions_seen: continue
                seen.add(identity); questions_seen.add(question)
                prefix = definition + '\nTask: ' + question + '\nThe required integer is '
                row = {'id': hashlib.sha256(question.encode()).hexdigest(), 'family': family,
                       'chapter': family, 'parameters': p, 'question': question, 'answer': answer,
                       'prefix': prefix, 'target_text': str(answer), 'text': prefix + str(answer),
                       'curriculum_stage': stage, 'dependencies': dependencies, 'replay': False,
                       'evaluation_kind': 'new_instance', 'assistance': definition,
                       'source': 'project-authored examples; Judson AATA integers, sets, and cosets chapters'}
                assert verify_primitive(row, answer)
                result[split].append(row); made += 1
    # Unseen compositions are never placed in acquisition text.
    for family in ('witness_sum', 'witness_chain'):
        for i in range(12):
            a, k, l = 7 + i, 3 + i, 5 + 2 * i
            answer = k + l if family == 'witness_sum' else k * l
            question = (f'Given b={a}*{k} and c={a}*{l}, find w such that b+c={a}*w.' if family == 'witness_sum'
                        else f'Given b={a}*{k} and c=b*{l}, find w such that c={a}*w.')
            row = {'id': hashlib.sha256(question.encode()).hexdigest(), 'family': family,
                   'chapter': family, 'parameters': {'a': a, 'b': a*k, 'k': k, 'l': l},
                   'question': question, 'answer': answer, 'prefix': question + '\nAnswer: ',
                   'target_text': str(answer), 'text': question + '\nAnswer: ' + str(answer),
                   'curriculum_stage': 4, 'dependencies': ['witness', 'substitution'],
                   'replay': False, 'evaluation_kind': 'composition', 'assistance': '', 'source': 'project-authored composition'}
            assert verify_primitive(row, answer)
            result['heldout'].append(row)
    ordered, previous = [], []
    for stage in range(4):
        fresh = []
        for family, (family_stage, _, _) in PRIMITIVES.items():
            if family_stage == stage:
                examples = [q for q in result['train'] if q['family'] == family]
                rng.shuffle(examples)
                fresh.extend(examples)
        for i, q in enumerate(fresh):
            ordered.append(q)
            if (i + 1) % 3 == 0:
                ordered.append({**rng.choice(previous or fresh[:i+1]), 'replay': True})
        previous.extend(fresh)
    result['train'] = ordered
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--extend-primes", action="store_true")
    ap.add_argument("--source-revision")
    ap.add_argument("--structured-primitives", action="store_true")
    ap.add_argument("--controlled-mathematics", action="store_true")
    ap.add_argument("--review", type=Path)
    args = ap.parse_args()
    if args.controlled_mathematics:
        if args.review is None:
            ap.error('--controlled-mathematics requires --review')
        from mathematical_rlp_data import export_study
        print(json.dumps(export_study(args.output, args.review), indent=2), flush=True)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.output / "source"
    source.mkdir()
    def get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "formal-rl-acquisition"})
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise ValueError("Source exceeds preparation memory bound")
        return raw
    revision = args.source_revision or json.loads(get("https://api.github.com/repos/twjudson/aata/commits/master"))["sha"]
    chapters = CHAPTERS + (["cosets"] if args.extend_primes or args.structured_primitives else [])
    base = "https://raw.githubusercontent.com/twjudson/aata/" + revision + "/"
    hashes = {}
    for name in ["COPYING", "src/gfdl.xml", *["src/" + c + ".xml" for c in chapters]]:
        raw = get(base + name)
        (source / Path(name).name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    if args.structured_primitives:
        data = structured_curriculum()
        for filename, rows in [('train', data['train']), ('validation', data['validation']),
                               ('validation_primitives', data['validation']), ('heldout_primitives', data['heldout'])]:
            (args.output / (filename + '.jsonl')).write_text(''.join(json.dumps(q) + '\n' for q in rows))
        ids = {split: {q['id'] for q in rows} for split, rows in data.items()}
        assert not (ids['train'] & (ids['validation'] | ids['heldout']) or ids['validation'] & ids['heldout'])
        manifest = {'source': 'https://github.com/twjudson/aata', 'revision': revision,
                    'source_sha256': hashes, 'license': 'Authored examples; reference source GFDL-1.3-or-later retained',
                    'concepts': PRIMITIVES, 'counts': {k: len(v) for k, v in data.items()},
                    'unique_train': len(ids['train']), 'replay_occurrences': sum(q['replay'] for q in data['train']),
                    'overlap': 'Exact question and canonical operand identities separated. Shared primitive templates cross splits deliberately. Composition templates held out.',
                    'limitations': 'Computational instances and condition checks, not complete proofs or a public undergraduate benchmark. Shared templates are near duplicates by design. Contradiction remainder has a constant-answer baseline.',
                    'objective': 'Task-conditioned RLP on hidden mathematical continuations, no supervised target loss.',
                    'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps(manifest, indent=2), flush=True)
        return
    seen = set()
    records = {"train": [], "validation": []}
    for rank, chapter in enumerate(chapters):
        for locator, text in text_blocks((source / (chapter + ".xml")).read_text()):
            if len(text) < 120:
                continue
            if len(text.encode()) > 64 * 1024:
                raise ValueError("Text block exceeds preparation bound")
            digest = hashlib.sha256(text.encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            split = "validation" if int(digest[:2], 16) < 26 else "train"
            records[split].append({"id": digest, "chapter": chapter, "rank": rank,
                                   "locator": locator, "text": text, "curriculum_stage": 3,
                                   "source": base + "src/" + chapter + ".xml"})
    if args.extend_primes:
        for stage, concept, text in primitive_structure_blocks():
            records['train'].append({'id': hashlib.sha256(text.encode()).hexdigest(),
                'chapter': 'authored_' + concept, 'rank': -1, 'curriculum_stage': stage,
                'locator': concept, 'text': text, 'source': 'project-authored primitive and proof-structure text'})
    if not all(records.values()) or sum(map(len, records.values())) > 1000:
        raise ValueError("Corpus is empty or exceeds the record bound")
    for split, pool in records.items():
        (args.output / (split + ".jsonl")).write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in pool))
    questions = primitive_questions()
    if args.extend_primes:
        questions += prime_questions('heldout')
        (args.output / 'validation_primitives.jsonl').write_text(''.join(json.dumps(q) + '\n' for q in prime_questions('validation')))
    (args.output / "heldout_primitives.jsonl").write_text("".join(json.dumps(q) + "\n" for q in questions))
    manifest = {"source": "https://github.com/twjudson/aata", "revision": revision,
                "license": "GFDL-1.3-or-later; full sources, attribution and license retained",
                "source_sha256": hashes, "chapters": chapters,
                "curriculum": "primitive, proof structure, prime applications, then shuffled textbook contexts" if args.extend_primes else "shuffled textbook contexts",
                "authored_training_text": args.extend_primes,
                "counts": {**{s: len(p) for s, p in records.items()}, "heldout_primitives": len(questions)},
                "split": "whole theorem/example/paragraph groups assigned by text hash; exact duplicates removed",
                "limitations": "Related definitions and theorem families cross text splits. Heldout primitives share mathematical operations with the corpus. No public benchmark score or semantic contamination guarantee.",
                "objective": "RLP acquisition text. Exercise and solution sections excluded. Heldout answers never enter training.",
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
