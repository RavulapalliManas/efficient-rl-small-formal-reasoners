"""Deterministic selection and duplicate-aware split assignment."""
from collections import Counter, defaultdict
import re

from build_nt_pool import key
from build_nt_sft_data import body_of
from probe_start_depth import FORBIDDEN
from profile_external_proofs import tokens
from .artifacts import reference_profile
from .storage import digest, rows

NUMERAL = re.compile(r'(?<![\w.\'])\d+(?![\w.\'])')

TOPICS = [
    ('Quadratic residues', r'legendreSym|jacobiSym|IsSquare|quadratic'),
    ('Arithmetic functions', r'totient|ArithmeticFunction|sigma|moebius|divisors'),
    ('Order and primitive roots', r'orderOf|IsPrimitiveRoot|primitiveRoots'),
    ('Fermat, Euler, Wilson', r'factorial|pow_card|\^\s*\(?\s*\w+\s*-\s*1\s*\)?.*(?:MOD|%|≡)'),
    ('Digits and bases', r'digits|ofDigits'),
    ('Diophantine equations', r'(?:¬\s*∃|∃\s*\(?\w+\s+\w+).*(?:\^|\*).*='),
    ('Bezout and coprimality', r'Coprime|IsCoprime|gcd_eq_gcd_ab|gcd[^=]*=\s*1\b'),
    ('Congruences', r'ZMOD|\[MOD|≡|ZMod|%'),
    ('Primes and factorization', r'Prime|primeFactors|factorization|minFac'),
    ('Divisibility', r'∣|gcd|lcm|Even|Odd'),
]



class BenchmarkSelector:
    """Own the selection policy; never inspect Lean outcomes when assigning splits."""

    def __init__(self, config, protected=(), excluded_ids=()):
        self.config = dict(config)
        self.excluded_ids = frozenset(excluded_ids)  # SHA-256 of raw statements that must never be selected.
        canonical = [key(row['statement'].strip()) for row in protected]
        self.protected_templates = {NUMERAL.sub('NUM', statement) for statement in canonical}
        self.protected_tokens = [tokens(statement) for statement in canonical]

    def candidate(self, row):
        config = self.config
        statement = row['statement'].strip()
        if not isinstance(row['seen_by_parent_lineage'], bool):
            raise ValueError('parent exposure must be an explicit boolean')
        if (len(statement) > 4096 or ':=' in statement or '\n' in statement
                or re.search(r'[ℝℂ√π]|\b(?:Real|Complex)\.', statement)
                or ('ZMod' not in statement and re.search(r'\[\s*\w*(?:Group|Monoid)\b', statement))):
            raise ValueError('outside supported number-theory statements')
        canonical = key(statement)
        labels = [label for label, pattern in TOPICS if re.search(pattern, canonical)]
        if not labels:
            raise ValueError('no number-theory topic')
        if self.protected_tokens:
            template = NUMERAL.sub('NUM', canonical)
            current_tokens = tokens(canonical)
            if (template in self.protected_templates or any(
                    len(current_tokens & prior) / max(1, len(current_tokens | prior))
                    >= config['near_duplicate_threshold'] for prior in self.protected_tokens)):
                raise ValueError('matches prior benchmark')
        reference = []
        for proof in row['proofs']:
            body = body_of(proof['text'], 'theorem original ' + statement)
            if (body and len(body) <= config['max_reference_chars'] and not FORBIDDEN.search(body)
                    and all(not line.strip() or line[0].isspace() for line in body.splitlines())):
                reference.append((digest(body), body, proof['source']))
        if not reference:
            raise ValueError('no supported reference proof')
        _, body, source = min(reference)
        instance = digest(canonical)
        name = 'ntb_' + instance[:24]
        return dict(instance_id=instance, name=name, task_id=name + ':whole:0', statement=statement,
                    header='theorem ' + name + ' ' + statement, topic=labels[0], topic_labels=labels,
                    template_key=NUMERAL.sub('NUM', canonical),
                    seen_by_parent_lineage=row['seen_by_parent_lineage'],
                    reserved=any(match['split'] != 'A_training_family' for match in row['near_syllabus']),
                    source_labels=row['sources'], reference_source=source, reference_body=body,
                    **reference_profile(body), strategies=None, intermediate_subproofs=None,
                    dependency_depth=None, empirical_difficulty=None, annotation_status='unreviewed')


    def select(self, paths, max_input_bytes):
        config = self.config
        if sum(path.stat().st_size for path in paths) > max_input_bytes:
            raise ValueError('Input bytes exceed the configured cap')
        selected, seen, templates, counts = defaultdict(list), {}, {}, Counter()
        for source_index, path in enumerate(paths):
            for line, row in enumerate(rows(path), 1):
                counts['rows_read'] += 1
                if counts['rows_read'] > 100000:
                    raise ValueError('Input row cap exceeded')
                if not isinstance(row['seen_by_parent_lineage'], bool):
                    raise ValueError('parent exposure must be an explicit boolean')
                canonical = key(row['statement'].strip())
                identity = digest(canonical)
                template_id = digest(NUMERAL.sub('NUM', canonical))
                reserved = any(match['split'] != 'A_training_family' for match in row['near_syllabus'])
                exposure = templates.setdefault(template_id, [False, False])
                exposure[0] |= row['seen_by_parent_lineage']
                exposure[1] |= reserved
                if identity in seen:
                    counts['merged_canonical_duplicate'] += 1
                    seen[identity][0] |= row['seen_by_parent_lineage']
                    seen[identity][1] |= reserved
                    seen[identity][2] += 1
                else:
                    seen[identity] = [row['seen_by_parent_lineage'], reserved, 1]
                if digest(row['statement']) in self.excluded_ids:
                    counts['excluded:protected source identity'] += 1
                    continue
                try:
                    item = self.candidate(row)
                except ValueError as error:
                    counts['excluded:' + str(error)] += 1
                    continue
                item['source_row'] = dict(input=source_index, line=line)
                rank = digest(f"{config['version']}/{config['seed']}/{item['instance_id']}")
                bucket = selected[item['topic']]
                existing = next((old for _, old in bucket if old['instance_id'] == identity), None)
                if existing:
                    bucket[:] = [(rank, old) for rank, old in bucket if old['instance_id'] != identity]
                    item = min((existing, item), key=lambda row: (row['statement'], digest(row['reference_body'])))
                bucket.append((rank, item))
                bucket.sort(key=lambda pair: pair[0])
                del bucket[config['max_per_topic']:]
        chosen = sorted((item for bucket in selected.values() for _, item in bucket), key=lambda r: r['instance_id'])
        if not chosen or len(chosen) > config['max_candidates']:
            raise ValueError('Empty selection or candidate cap exceeded')
        for item in chosen:
            exposure, reserved, copies = seen[item['instance_id']]
            family_exposure, family_reserved = templates[digest(item['template_key'])]
            item.update(seen_by_parent_lineage=exposure or family_exposure,
                        reserved=reserved or family_reserved, canonical_copies=copies)
        return chosen, dict(counts)


    def partition(self, candidates):
        config = self.config
        """Join exact templates and transitive lexical matches before assigning splits."""
        parent = list(range(len(candidates)))

        def root(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        token_sets = [tokens(key(row['statement'])) for row in candidates]
        audit = []
        for i, left in enumerate(candidates):
            for j in range(i):
                score = len(token_sets[i] & token_sets[j]) / max(1, len(token_sets[i] | token_sets[j]))
                same = left['template_key'] == candidates[j]['template_key']
                if same or score >= config['near_duplicate_threshold']:
                    parent[root(i)] = root(j)
                    audit.append(dict(left=left['instance_id'], right=candidates[j]['instance_id'],
                                      same_template=same, jaccard=score))
        groups = defaultdict(list)
        for index, row in enumerate(candidates):
            groups[root(index)].append(row)
        for group in groups.values():
            family = 'template_' + digest(min(r['template_key'] for r in group))[:24]
            bucket = int(digest(f"{config['version']}/{config['seed']}/{family}"), 16) % 100
            split = ('test' if bucket < config['test_percent'] else
                     'validation' if bucket < config['test_percent'] + config['dev_percent'] else 'train')
            if any(r['topic'] in config['heldout_topics'] or r.get('reserved', False) for r in group):
                split = 'test'
            exposed = any(r['seen_by_parent_lineage'] for r in group)
            for row in group:
                row.update(family=family, split=split,
                           excluded_reason='known_parent_exposure' if split != 'train' and exposed else None)
        return candidates, audit
