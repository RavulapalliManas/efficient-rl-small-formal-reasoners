"""BM25 premise retrieval over library lemmas, independent of Lean processes.

The corpus holds library declarations only, one JSON object per line with `name` and `statement`.
Retrieval reads the current goals and returns the top-k lemmas as `name : statement` lines for the
observation. Tokens present in more than half the corpus are skipped; they carry almost no BM25
weight and dominate query time. Memory: the postings, under 2 GB for about 200,000 Mathlib theorems.
"""
from collections import Counter, defaultdict
import json
import math
import re

PIECE = re.compile(r'[A-Za-z]+|\d+|[^\sA-Za-z\d]')
WORD = re.compile(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+')
PUNCTUATION = set('()[]{},:._;`\'"')


def tokens(text):
    """Split identifiers on dots, underscores and camel case; keep mathematical symbols."""
    out = []
    for piece in PIECE.findall(text):
        if piece[0].isalpha():
            out += [word.lower() for word in WORD.findall(piece)]
        elif piece not in PUNCTUATION:
            out.append(piece)
    return out


class PremiseIndex:
    def __init__(self, records, k=8, k1=1.2, b=0.75):
        if not records or isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 16:
            raise ValueError('A premise index needs records and 1 <= k <= 16')
        names = [record['name'] for record in records]
        if len(set(names)) != len(names):
            raise ValueError('Premise names must be unique')
        self.records, self.k, self.k1, self.b = records, k, k1, b
        postings, self.lengths = defaultdict(list), []
        for doc, record in enumerate(records):
            counts = Counter(tokens(record['name'] + ' ' + record['statement']))
            self.lengths.append(sum(counts.values()) or 1)
            for token, frequency in counts.items():
                postings[token].append((doc, frequency))
        n = len(records)
        self.average = sum(self.lengths) / n
        self.postings = {t: p for t, p in postings.items() if len(p) <= n / 2}
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}
        self.cache = {}

    def search(self, text):
        """Document indices of the top-k lemmas; ties go to the earlier corpus line."""
        if text not in self.cache:
            scores = defaultdict(float)
            for token in set(tokens(text)):
                for doc, frequency in self.postings.get(token, ()):
                    scale = 1 - self.b + self.b * self.lengths[doc] / self.average
                    scores[doc] += self.idf[token] * frequency * (self.k1 + 1) / (frequency + self.k1 * scale)
            self.cache[text] = sorted(scores, key=lambda doc: (-scores[doc], doc))[:self.k]
        return self.cache[text]

    def __call__(self, goals):
        return [(self.records[doc]['name'] + ' : ' + self.records[doc]['statement'])[:200]
                for doc in self.search('\n'.join(goals))]


def load_index(path, k, protected_names=()):
    """Read a library corpus; refuse any entry named like a project theorem."""
    with open(path) as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    clash = sorted({record['name'] for record in records} & set(protected_names))
    if clash:
        raise ValueError(f'The premise corpus contains project theorem names, for example {clash[:3]}')
    return PremiseIndex(records, k)
