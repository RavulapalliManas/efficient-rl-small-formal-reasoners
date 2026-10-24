"""Near-duplicate checks of new training items against held-out statements, with the thresholds of nt_splits_v3.

Targets, each compared by token Jaccard (tokens() of profile_external_proofs.py on the statement without keyword, name,
and proof, as in build_nt_splits.py):
- at >= --jaccard (0.85): validation and test statements of the pool, the LeanDojo curriculum_validation and
  heldout_evaluation declarations, and the statements of heldout_proof_problems.txt and dev.txt of the splits;
- at >= --minif2f-jaccard (0.7): the miniF2F statements.
The heldout and dev targets are stricter than build_nt_splits.py, which compares train statements only with validation,
test, and miniF2F. `held_out_names` holds every name an item must not have (pool validation and test, heldout, dev,
excluded_from_training, LeanDojo evaluation full names).

Memory: about 1,700 target token sets (<50 MiB).
"""
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parent)]
from profile_external_proofs import statement, tokens  # noqa: E402


def statement_of(text):
    """The statement of a header or declaration: no `:= sorry`, no attributes, keyword, or name."""
    text = re.sub(r":=\s*(by\s*)?sorry\s*$", "", text.strip())
    return re.split(r"\b(?:theorem|lemma)\s+\S+", text, maxsplit=1)[-1].strip()


def jaccard(a, b):
    return len(a & b) / max(len(a | b), 1)


class Leakage:
    def __init__(self, pool, splits, evals, minif2f, threshold=0.85, minif2f_threshold=0.7):
        with Path(pool).open() as input_stream:
            rows = {r["name"]: r for r in map(json.loads, input_stream)}
        names = {f: set((Path(splits) / f"{f}.txt").read_text().split())
                 for f in ("heldout_proof_problems", "dev", "excluded_from_training", "sft_problems")}
        self.sft_problems = names["sft_problems"]
        self.targets = []  # (label, name, tokens, threshold)
        for name, r in sorted(rows.items()):
            label = r["split"] if r["split"] != "train" else "heldout" if name in names["heldout_proof_problems"] \
                else "dev" if name in names["dev"] else None
            if label:
                self.targets.append((label, name, tokens(statement(r["formal_statement"])), threshold))
        self.eval_names = set()
        for path in evals:
            with zipfile.ZipFile(path) as archive:
                with archive.open(archive.namelist()[0]) as input_stream:
                    for line in input_stream:
                        r = json.loads(line)
                        self.eval_names.add(r["full_name"])
                        self.targets.append(("leandojo_" + Path(path).stem, r["full_name"],
                                             tokens(statement_of(r["declaration_header"])), threshold))
        with Path(minif2f).open() as input_stream:
            for r in map(json.loads, input_stream):
                self.targets.append(("minif2f", r["name"], tokens(statement(r["formal_statement"])), minif2f_threshold))
        self.held_out_names = ({n for n, r in rows.items() if r["split"] != "train"} | names["heldout_proof_problems"]
                               | names["dev"] | names["excluded_from_training"] | self.eval_names)

    def hits(self, stmt):
        """Every target at or above its threshold for the statement text `stmt` (no keyword or name)."""
        mine, out = tokens(stmt), []
        for label, name, t, limit in self.targets:
            j = jaccard(mine, t)
            if j >= limit:
                out.append({"target": label, "name": name, "jaccard": round(j, 3)})
        return out

    def counts(self):
        out = {}
        for label, *_ in self.targets:
            out[label] = out.get(label, 0) + 1
        return out
