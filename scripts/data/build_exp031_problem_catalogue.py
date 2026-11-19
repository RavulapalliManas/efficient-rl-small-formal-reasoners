"""Build review-only CSV metadata for the frozen EXP-031 problem IDs."""

import argparse
import csv
import gzip
import json
import re
from pathlib import Path

from audit_sft_corpus import MINIF2F_NT, NT_POOLS, strip_comments, tokens
from import_extra_nt import split_declaration

TOPIC_PATTERNS = {
    "Primes": r"\b(?:Nat\.Prime|Int\.Prime|Prime|primeFactors(?:List)?|minFac)\b",
    "GCD and coprimality": r"\b(?:gcd|lcm|Coprime|IsCoprime|IsRelPrime)\b",
    "Congruences": r"\[(?:Z?MOD)\b|\b(?:ModEq|ZMod)\b",
    "Divisibility": r"∣|\b(?:dvd|divisors(?:Antidiagonal)?|padicValNat|padicValInt|totient)\b",
    "Parity": r"\b(?:Even|Odd|bodd|even|odd)\b",
    "Digits": r"\b(?:digits|ofDigits|digitSum)\b",
}
TOPIC_PATTERNS = {name: re.compile(pattern) for name, pattern in TOPIC_PATTERNS.items()}
NAME_TOPICS = {"numbertheory": "Number theory", "algebra": "Algebra", "induction": "Induction"}
NAME = re.compile(r"^(?:theorem|lemma)\s+([^\s:(\[{⦃]+)")
SYNTHETIC_PREFIX = re.compile(r"^(?:xstp|xgv2)_")
HASH_SUFFIX = re.compile(r"(?<=_)_*[0-9a-f]{8}$")
BENCHMARK_NAME = re.compile(r"^(?:aime|amc(?:10|12)?[ab]?|imo|imosl|mathd|putnam)_", re.I)
OPAQUE_NAME = re.compile(r"(?:ntb_[a-f0-9]+|[cr]\d+)$")
ACRONYMS = {"aime": "AIME", "amc": "AMC", "amc10": "AMC 10", "amc12": "AMC 12", "imo": "IMO", "imosl": "IMO Shortlist", "mathd": "MATHD",
            "gcd": "GCD", "lcm": "LCM", "ioqm": "IOQM", "xgpset": "Goedel PSet", "lean": "Lean"}
TEST_SETS = ("general", "number_theory")
SHARED_FIELDS = ["id", "title", "sources", "source_names", "topic_tags", "topic_basis"]
TRAIN_FIELDS = SHARED_FIELDS + ["split", "accepted_proofs", "shortest_proof_tokens", "shortest_proof_lines",
                                "shortest_proof_id"]
TEST_FIELDS = SHARED_FIELDS + ["test_sets", "number_theory_label", "label_conflict", "benchmark_level",
                               "level_conflict", "elaborates", "trivial", "flagged"]


def read_jsonl(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def clean_name(name):
    name = SYNTHETIC_PREFIX.sub("", name)
    return HASH_SUFFIX.sub("", name).rstrip("_")


def source_title(name):
    name = clean_name(name)
    if OPAQUE_NAME.fullmatch(name) or name.isdecimal():
        return ""
    name = re.sub(r"(?<=\d)(?:i|ii)(?=_)", lambda match: f"_{match[0].upper()}", name)
    name = re.sub(r"^amc(10|12)([ab])_", lambda match: f"amc{match[1]}_{match[2].upper()}_", name)
    name = name.replace("numbertheory", "number_theory").replace("_all_", "_")
    words = re.split(r"[_\s-]+", name)
    return " ".join(ACRONYMS.get(word.lower(), word[:1].upper() + word[1:]) for word in words if word)


def metadata(row, names, statements, source_topics=()):
    tags = [tag for tag, pattern in TOPIC_PATTERNS.items() if any(pattern.search(s) for s in statements)]
    named_topics = {topic for key, topic in NAME_TOPICS.items()
                    if any(re.match(fr"^(?:mathd_)?{key}_", clean_name(name)) for name in names)}
    broad_topics = sorted(set(source_topics) | named_topics)
    sources = sorted(row["sources"])
    titles = [(not BENCHMARK_NAME.match(clean_name(name)), source_title(name)) for name in names]
    preferred_title = min((entry for entry in titles if entry[1]), default=(False, ""))[1]
    title = preferred_title or next(iter(broad_topics + tags), "Problem")
    if tags and tags[0].lower() not in title.lower():
        title = f"{title} — {tags[0]}"
    bases = []
    if broad_topics:
        bases.append("source")
    if tags:
        bases.append("statement_keywords")
    return {
        "id": row["id"],
        "title": f"{title} ({row['id']})",
        "sources": " | ".join(sources),
        "source_names": " | ".join(sorted(names)),
        "topic_tags": " | ".join(broad_topics + tags),
        "topic_basis": " | ".join(bases) or "unlabeled",
    }


def training_metadata(row, split):
    measurements = []
    names = set()
    for proof in row["proofs"]:
        declaration = split_declaration(proof["proof"].lstrip())
        if declaration is None:
            continue
        _, header, body = declaration
        if match := NAME.match(header):
            names.add(match[1])
        body = re.sub(r"^\s*by\b", "", strip_comments(body), count=1)
        if length := len(tokens(body)):
            measurements.append((length, sum(bool(line.strip()) for line in body.splitlines()), proof["id"]))
    length, lines, proof_id = min(measurements) if measurements else (None, None, None)
    return metadata(row, names, [row["statement"]]) | {
        "split": split,
        "accepted_proofs": len(row["proofs"]),
        "shortest_proof_tokens": length,
        "shortest_proof_lines": lines,
        "shortest_proof_id": proof_id,
    }


def test_metadata(row, selected):
    memberships = [name for name in TEST_SETS if row["id"] in selected[name]]
    labels = [selected[name][row["id"]] for name in memberships]
    if labels and any(item != labels[0] for item in labels[1:]):
        raise ValueError(f"Test-set metadata disagrees for {row['id']}")
    label = labels[0] if labels else {}
    known_number_theory = label.get("label")
    if not labels and (NT_POOLS.intersection(row["sources"]) or any(
        name.startswith(MINIF2F_NT) for name in row["names"]
    )):
        known_number_theory = True
    source_topics = {member["split"].replace("_", " ").capitalize()
                     for version in row["versions"] for member in version["members"]
                     if member["source"] == "proverbench" and member["split"] != "aime"}
    if known_number_theory is True:
        source_topics.add("Number theory")
    statements = [version["statement"] for version in row["versions"] if version["elaborates"]]
    return metadata(row, row["names"], statements, source_topics) | {
        "test_sets": " | ".join(memberships),
        "number_theory_label": known_number_theory,
        "label_conflict": label.get("label_conflict"),
        "benchmark_level": label.get("level"),
        "level_conflict": label.get("level_conflict"),
        "elaborates": row["elaborates"],
        "trivial": row["trivial"],
        "flagged": row["flagged"],
    }


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits", type=Path, default=Path("data/exp031_splits_v4"))
    parser.add_argument("--testsets", type=Path, default=Path("data/exp031_testsets_v2"))
    parser.add_argument("--dest", type=Path, default=Path("data/exp031_problem_catalogue_v1"))
    args = parser.parse_args()

    selected = {name: {row["id"]: row for row in read_jsonl(args.testsets / f"test_{name}.jsonl")}
                for name in TEST_SETS}
    pool = read_jsonl(args.splits / "test_pool.jsonl")
    if set().union(*selected.values()) - {row["id"] for row in pool}:
        raise ValueError("A test-set ID is missing from the frozen test pool")

    args.dest.mkdir(parents=True, exist_ok=True)
    for split, filename in (("train", "train.jsonl.gz"), ("dev", "dev.jsonl")):
        rows = read_jsonl(args.splits / filename)
        write_csv(args.dest / f"{split}.csv", TRAIN_FIELDS, (training_metadata(row, split) for row in rows))
    write_csv(args.dest / "test_pool.csv", TEST_FIELDS, (test_metadata(row, selected) for row in pool))


if __name__ == "__main__":
    main()
