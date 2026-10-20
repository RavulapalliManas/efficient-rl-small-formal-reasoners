"""Pure helpers shared by the held-out screens: pool loading, statement parsing, similarity, bands and strata.

No Lean and no model. automation_chain_screen.py, false_hypothesis_sweep.py, train_similarity_strata.py and
stratify_heldout.py import these functions, so every screen reads the pool and maps Lean statuses the same way.

Memory: helpers hold one pool (<= a few thousand rows of <= 4 KiB) and character 4-gram sets of the TRAIN
statements (about 600 statements x <= 4,096 grams x ~100 bytes, < 250 MiB worst case, < 20 MiB measured shape).
"""
from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from build_nt_pool import key  # noqa: E402
from probe_nt_pool import append, read  # noqa: E402,F401
from profile_external_proofs import statement, tokens  # noqa: E402

NUMERAL = re.compile(r"(?<![\w.'])\d+(?![\w.'])")  # same pattern as nt_benchmark/selection.py
OPEN, CLOSE = "([{⦃", ")]}⦄"
# A binder whose type fully matches this is data. Every other binder, and every instance binder, is a hypothesis.
_ATOM = r"(?:ℕ|ℤ|ℚ|ℝ|ℂ|Nat|Int|Rat|Real|Prop|Type\*?|(?:Fin|ZMod|Finset|Set|List|Multiset|Polynomial)\s+(?:\([^()]*\)|\S+))"
DATA_TYPE = re.compile(rf"{_ATOM}(?:\s*→\s*{_ATOM})*")
BANDS = ((0.7, ">=0.7"), (0.5, "0.5-0.7"), (0.3, "0.3-0.5"), (0.0, "<0.3"))
STRATA = ("vacuous", "automation", "near_train", "other")


def lean_status(raw):
    """verify_group status -> accepted, timeout or rejected (crash and filter rejections count as rejected)."""
    return raw if raw in ("accepted", "timeout") else "rejected"


def header_of(row):
    """Theorem header (no `:= by ...`) of a pool row, an RL task, or an SFT/verifier row holding a whole proof."""
    if "header" in row:
        return row["header"].strip()
    if "formal_statement" in row:
        return re.sub(r":=\s*(by\s*)?sorry\s*$", "", row["formal_statement"].strip()).rstrip()
    text = proof_text(row)
    found = re.search(r"theorem\s+\S+", text)
    if found is None:
        raise ValueError("row has no theorem header")
    return re.split(r":=\s*by\b", text[found.start():], maxsplit=1)[0].strip()


def proof_text(row):
    """Whole-proof text of a verifier row (`text`) or of an SFT row (last assistant message, fences removed)."""
    if "text" in row:
        return row["text"]
    content = [m["content"] for m in row.get("messages", []) if m["role"] == "assistant"][-1]
    return re.sub(r"^```lean4?\s*\n|\n?```\s*$", "", content.strip())


def name_of(row):
    return row.get("name", row.get("theorem"))


def load_pool(path, split=None, tasks=None):
    """Pool rows of `split` (all when None) in name order, each with `topic` from a tasks file when given."""
    topics = {r["name"]: r.get("topic", "unknown") for r in read(tasks)} if tasks else {}
    rows = [dict(r, topic=r.get("topic", topics.get(r["name"], "unknown"))) for r in read(path)]
    if len({r["name"] for r in rows}) != len(rows):
        raise ValueError("duplicate names in pool")
    return sorted((r for r in rows if split is None or r["split"] == split), key=lambda r: r["name"])


def pending_candidates(names, texts, arm, existing):
    """Candidate rows (name order, then text order) not already in `existing` (rows with arm, name, sample)."""
    done = {(c["arm"], c["name"], c["sample"]) for c in existing}
    return [{"arm": arm, "name": n, "sample": i, "continuation": "  " + t.replace("\n", "\n  "), "generated_tokens": 0}
            for n in names for i, t in enumerate(texts) if (arm, n, i) not in done]


def attempts_by_name(checked, arm):
    """name -> {sample: mapped status} for checked rows of `arm`."""
    out = {}
    for c in checked:
        if c["arm"] == arm:
            out.setdefault(c["name"], {})[c["sample"]] = lean_status(c["status"])
    return out


def top_level_index(text, separator, start=0):
    """Index of the first `separator` at or after `start` outside every bracket pair, or -1."""
    depth = 0
    for i in range(len(text)):
        if text[i] in OPEN:
            depth += 1
        elif text[i] in CLOSE:
            depth -= 1
        elif depth == 0 and i >= start and text.startswith(separator, i):
            return i
    return -1


def top_level_split(text, separator):
    """Split at `separator` outside every bracket pair."""
    parts, start = [], 0
    while (i := top_level_index(text, separator, start)) >= 0:
        parts.append(text[start:i])
        start = i + len(separator)
    return [p.strip() for p in parts + [text[start:]]]


def split_binders(stmt):
    """(binders, conclusion) of a statement without the theorem name; each binder is (opener, names, type)."""
    binders, i = [], 0
    while True:
        while i < len(stmt) and stmt[i].isspace():
            i += 1
        if i >= len(stmt) or stmt[i] not in OPEN:
            break
        depth, j = 0, i
        while j < len(stmt):
            depth += stmt[j] in OPEN
            depth -= stmt[j] in CLOSE
            if depth == 0:
                break
            j += 1
        inner = stmt[i + 1:j]
        colon = top_level_index(inner, ":")
        names, kind = (inner[:colon], inner[colon + 1:]) if colon >= 0 else ("", inner)
        binders.append((stmt[i], names.strip(), kind.strip()))
        i = j + 1
    rest = stmt[i:].strip()
    return binders, rest[1:].strip() if rest.startswith(":") else rest


def premises(stmt):
    """Hypothesis binders before the colon, whether the conclusion has an arrow, and whether it ends in False."""
    binders, conclusion = split_binders(stmt)
    named = [f"{n} : {t}".strip(" :") for o, n, t in binders if o == "[" or not DATA_TYPE.fullmatch(t)]
    return {"hypotheses": named, "goal_arrow": "→" in conclusion,
            "conclusion_false": bool(re.search(r"(?:^|→|,)\s*False\s*$", conclusion))}


def vacuity_status(premise, statuses):
    """contradictory when Lean proved False from the premises, consistent only when there is no premise at all."""
    if premise["conclusion_false"]:
        return "undetermined"
    if "accepted" in statuses:
        return "contradictory"
    if not premise["hypotheses"] and not premise["goal_arrow"]:
        return "consistent"
    return "undetermined"


def signature(stmt):
    """Construction signature: binder renaming, numerals and MOD/ZMOD moduli -> N, hypothesis conjuncts sorted."""
    text = re.sub(r"\[(Z?MOD)\s+[^\]]+\]", r"[\1 N]", key(stmt))
    text = NUMERAL.sub("N", text)
    binders, conclusion = split_binders(text)
    parts = []
    for opener, names, kind in binders:
        kind = " ∧ ".join(sorted(top_level_split(kind, "∧")))
        parts.append(opener + (names + " : " if names else "") + kind + CLOSE[OPEN.index(opener)])
    return " ".join(parts) + " : " + conclusion


def template(stmt):
    return NUMERAL.sub("NUM", key(stmt))


def char_grams(stmt, n=4):
    text = re.sub(r"\s+", " ", key(stmt))
    return {text[i:i + n] for i in range(max(len(text) - n + 1, 1))}


def jaccard(a, b):
    return len(a & b) / max(len(a | b), 1)


def features(stmt):
    return {"tokens": tokens(key(stmt)), "grams": char_grams(stmt), "template": template(stmt),
            "signature": signature(stmt)}


def band(similarity):
    return next(label for low, label in BANDS if similarity >= low)


def nearest(target, library):
    """Similarity row of `target` features against `library` (name -> features); ties go to the smaller name."""
    best = {"token_jaccard_max": (-1.0, ""), "char4_jaccard_max": (-1.0, "")}
    ranked = []
    for name, f in sorted(library.items()):
        tok, gram = jaccard(target["tokens"], f["tokens"]), jaccard(target["grams"], f["grams"])
        for field, value in (("token_jaccard_max", tok), ("char4_jaccard_max", gram)):
            if value > best[field][0]:
                best[field] = (value, name)
        ranked.append(((f["signature"] == target["signature"], f["template"] == target["template"], max(tok, gram)), name))
    top = min(ranked, key=lambda x: (tuple(-v for v in x[0]), x[1]))[1] if ranked else None
    similarity = max(best["token_jaccard_max"][0], best["char4_jaccard_max"][0], 0.0)
    return {"token_jaccard_max": round(max(best["token_jaccard_max"][0], 0.0), 4),
            "token_nearest": best["token_jaccard_max"][1] or None,
            "char4_jaccard_max": round(max(best["char4_jaccard_max"][0], 0.0), 4),
            "char4_nearest": best["char4_jaccard_max"][1] or None,
            "template_matches": sorted(n for n, f in library.items() if f["template"] == target["template"]),
            "signature_matches": sorted(n for n, f in library.items() if f["signature"] == target["signature"]),
            "max_similarity": round(similarity, 4), "nearest": top, "band": band(similarity)}


def stratum(vacuous, automation, similarity):
    """Fixed order: vacuous, automation, near_train, other."""
    if vacuous["status"] == "contradictory":
        return "vacuous"
    if automation["accepting_chains"]:
        return "automation"
    if (similarity.get("copy_status") == "accepted" or similarity["signature_matches"]
            or similarity["max_similarity"] >= 0.5):
        return "near_train"
    return "other"


def theorem_of(task_id):
    """Pool name of an RL or evaluation task_id (`dev:<name>`, `<name>:whole:0`, or the name itself)."""
    return re.sub(r":whole:\d+$", "", re.sub(r"^dev:", "", task_id))


def solved_by_arm(steps):
    """(solved names, attempted names, censored names) from steps.jsonl rows: solved when any return equals 1."""
    solved, attempted, censored = set(), set(), set()
    for row in steps:
        name = theorem_of(row["task_id"])
        if row.get("status") == "censored":
            censored.add(name)
            continue
        attempted.add(name)
        if any(v == 1 for v in row.get("returns", [])):
            solved.add(name)
    return solved, attempted, censored - attempted


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def counts(rows, field):
    return dict(sorted(Counter(r[field] for r in rows).items()))
