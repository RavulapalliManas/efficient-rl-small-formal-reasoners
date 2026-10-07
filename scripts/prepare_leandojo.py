"""Prepare a project-defined Nat subset of LeanDojo's official random splits.

One archive pass, one theorem at a time, and a disk spool for training traces.
Compact candidate JSON is capped at 64 MiB by the supplied config. Typical peak
estimate: 64 MiB x 4 Python overhead + 32 MiB current record + 1 MiB parser.
This is a working-memory estimate, not an operating-system RSS limit.
No evaluation proof text is written to the exported manifests or audit rows.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import resource
import sys
import tarfile
import tempfile
import time

import ijson


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def declaration_header(code):
    """Cut at a top-level assignment, preserving assignments inside binders."""
    depth = 0
    comment = 0
    quoted = False
    i = 0
    while i < len(code):
        pair = code[i:i + 2]
        if comment:
            if pair == "/-":
                comment += 1
                i += 2
            elif pair == "-/":
                comment -= 1
                i += 2
            else:
                i += 1
            continue
        if quoted:
            if code[i] == "\\":
                i += 2
                continue
            if code[i] == '"':
                quoted = False
        elif pair == "/-":
            comment = 1
            i += 2
            continue
        elif pair == "--":
            newline = code.find("\n", i)
            i = len(code) if newline < 0 else newline + 1
            continue
        elif code[i] == '"':
            quoted = True
        elif code[i] in "([{⦃":
            depth += 1
        elif code[i] in ")]}⦄":
            depth -= 1
        elif pair == ":=" and depth == 0:
            return code[:i].strip()
        i += 1
    return code.strip()


def normalize(header):
    """Textual duplicate heuristic, not Lean alpha-equivalence."""
    text = re.sub(r"@\[[^\]]*\]", "", header)
    text = re.sub(r"\b(?:lemma|theorem)\s+[^\s(]+", "declaration", text)
    names = []
    for match in re.finditer(r"[(\{⦃]\s*([\w' \t]+)\s*:", text):
        names.extend(match[1].split())
    # Section variables often appear without explicit binders in corpus headers.
    names.extend(re.findall(r"\b[a-z](?:[0-9₀-₉]+)?\b", text))
    substitutions = {name: f"v{i}" for i, name in enumerate(dict.fromkeys(names))}
    tokens = re.findall(r"[\w'✝]+|[^\s]", text)
    return " ".join(substitutions.get(token, token) for token in tokens)


def similarity(left, right):
    a, b = set(left.split()), set(right.split())
    return len(a & b) / len(a | b) if a or b else 1.0


def prepare(archive_path, config, output):
    started = time.monotonic()
    with archive_path.open("rb") as source:
        md5 = hashlib.file_digest(source, "md5").hexdigest()
    if md5 != config["archive_md5"]:
        raise ValueError("Archive checksum does not match the pinned source")
    output.mkdir(parents=True, exist_ok=False)
    paths = {path: family for family, members in config["families"].items() for path in members}
    rows = {split: [] for split in ("train", "val", "test")}
    originals = Counter()
    seen_ids = {}
    selected = {}
    compact_bytes = 0
    corpus_seen = False
    metadata = None
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as spool:
        with tarfile.open(archive_path, "r|gz") as archive:
            for member in archive:
                name = member.name.removeprefix("leandojo_benchmark_4/")
                if name in ("random/train.json", "random/val.json", "random/test.json"):
                    split = name.split("/")[1].removesuffix(".json")
                    for original in ijson.items(archive.extractfile(member), "item"):
                        originals[split] += 1
                        if original["file_path"] not in paths:
                            continue
                        if (original["url"], original["commit"]) != (
                            config["source"]["url"], config["source"]["commit"]
                        ):
                            raise ValueError("Selected theorem has a different source revision")
                        key = (original["file_path"], original["full_name"])
                        if key in seen_ids:
                            raise ValueError(f"Theorem locator occurs twice: {key}")
                        seen_ids[key] = split
                        tactics = original["traced_tactics"]
                        compact = {k: original[k] for k in (
                            "url", "commit", "file_path", "full_name", "start", "end"
                        )}
                        compact.update(family=paths[key[0]], official_split=split,
                                       _proof_hash=digest([t["tactic"].strip() for t in tactics]) if tactics else None,
                                       _references=sorted({p["full_name"] for t in tactics
                                                           for p in t["annotated_tactic"][1]}),
                                       _tactic_count=len(tactics))
                        compact_bytes += len(encode(compact).encode())
                        if len(selected) >= config["max_candidates"] or compact_bytes > config["max_candidate_mib"] * 1048576:
                            raise MemoryError("Candidate cap exceeded; no manifest is complete")
                        rows[split].append(compact)
                        selected[key] = compact
                        if split == "train":
                            spool.write(encode(original) + "\n")
                elif name == "corpus.jsonl":
                    corpus_seen = True
                    for line in archive.extractfile(member):
                        file = json.loads(line)
                        if file["path"] not in paths:
                            continue
                        context_digest = digest(file)
                        for premise in file["premises"]:
                            key = (file["path"], premise["full_name"])
                            if key in selected:
                                row = selected[key]
                                row["declaration_header"] = declaration_header(premise["code"])
                                row["imports"] = file["imports"]
                                row["context_digest"] = context_digest
                elif name == "metadata.json":
                    metadata = json.load(archive.extractfile(member))
                elif name.startswith("licenses/") and member.isfile():
                    target = output / "licenses" / Path(name).name
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
        if not corpus_seen or metadata is None or metadata["from_repo"] != config["source"]:
            raise ValueError("Missing corpus or mismatched dataset metadata")
        if sum(len(encode(r).encode()) for records in rows.values() for r in records) > config["max_candidate_mib"] * 1048576:
            raise MemoryError("Enriched candidate cap exceeded; no manifest is complete")
        exclusions = []
        for split in rows:
            valid = []
            for row in rows[split]:
                header = row.get("declaration_header", "")
                tokens = set(re.findall(r"[\w'✝]+|[^\s]", header))
                reason = None
                if not re.search(r"\b(lemma|theorem)\b", header):
                    reason = "missing_declaration"
                elif tokens & set(config["excluded_header_tokens"]):
                    reason = "excluded_type"
                elif re.search(r"\b(by|where|let)\b", header):
                    reason = "ambiguous_header"
                if reason:
                    exclusions.append(dict(split=split, full_name=row["full_name"], reason=reason))
                    continue
                row["_normalized"] = normalize(header)
                row["theorem_id"] = digest({k: row[k] for k in (
                    "url", "commit", "file_path", "full_name", "start", "end",
                    "declaration_header", "imports", "context_digest"
                )})
                valid.append(row)
            rows[split] = valid
        candidates = {split: len(records) for split, records in rows.items()}
        heldout_names = {r["full_name"] for split in ("val", "test") for r in rows[split]}
        removals = []
        overlaps = []
        kept = {"test": rows["test"]}
        threshold = config["near_duplicate_threshold"]
        for split in ("val", "train"):
            targets = rows["test"] + (rows["val"] if split == "train" else [])
            valid = []
            training_seen = set()
            for row in rows[split]:
                reason = None
                for target in targets:
                    score = similarity(row["_normalized"], target["_normalized"])
                    if row["_proof_hash"] and row["_proof_hash"] == target["_proof_hash"]:
                        overlaps.append(dict(kind="proof_text", left=row["theorem_id"], right=target["theorem_id"], left_split=split))
                    if row["_normalized"] == target["_normalized"] or score >= threshold:
                        match = "normalized_exact" if row["_normalized"] == target["_normalized"] else "near_duplicate"
                        overlaps.append(dict(kind="statement", match=match, left=row["theorem_id"], right=target["theorem_id"], left_split=split, similarity=score))
                        reason = "statement_overlap"
                if not reason and split == "train" and heldout_names & set(row["_references"]):
                    reason = "heldout_reference"
                if not reason and split == "train" and row["_normalized"] in training_seen:
                    reason = "training_duplicate"
                if reason:
                    removals.append(dict(split=split, theorem_id=row["theorem_id"], full_name=row["full_name"], reason=reason))
                else:
                    training_seen.add(row["_normalized"])
                    valid.append(row)
            kept[split] = valid
        train_by_name = {row["full_name"]: row for row in kept["train"]}
        spool.seek(0)
        train_trace_count = 0
        train_traced_theorems = 0
        with (output / "train.jsonl").open("w") as target:
            for line in spool:
                original = json.loads(line)
                if original["full_name"] in train_by_name:
                    row = train_by_name[original["full_name"]]
                    public = {k: v for k, v in row.items() if not k.startswith("_")}
                    public["traced_tactics"] = original["traced_tactics"]
                    train_trace_count += len(public["traced_tactics"])
                    train_traced_theorems += bool(public["traced_tactics"])
                    target.write(encode(public) + "\n")
        for split in ("val", "test"):
            with (output / f"{split}.jsonl").open("w") as target:
                for row in kept[split]:
                    target.write(encode({k: v for k, v in row.items() if not k.startswith("_")}) + "\n")
    (output / "overlap_audit.jsonl").write_text("".join(encode(r) + "\n" for r in overlaps))
    (output / "removed.jsonl").write_text("".join(encode(r) + "\n" for r in removals + exclusions))
    manifest_hashes = {}
    for split in kept:
        with (output / f"{split}.jsonl").open("rb") as file:
            manifest_hashes[split] = hashlib.file_digest(file, "sha256").hexdigest()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = dict(official_counts=dict(originals), selected_before_header_filter=dict(Counter(seen_ids.values())),
                  candidates=candidates, retained={s: len(r) for s, r in kept.items()},
                  removed={s: dict(Counter(r["reason"] for r in removals + exclusions if r["split"] == s)) for s in rows},
                  families={s: dict(Counter(r["family"] for r in kept[s])) for s in kept},
                  train_traced_theorems=train_traced_theorems, train_tactic_records=train_trace_count,
                  overlap_pairs=dict(Counter(r.get("match", r["kind"]) for r in overlaps)),
                  within_split_normalized_duplicates={s: sum(c - 1 for c in Counter(r["_normalized"] for r in records).values()) for s, records in kept.items()},
                  archive_md5=md5, manifest_sha256=manifest_hashes, config=config,
                  source_metadata=metadata, script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  elapsed_seconds=time.monotonic() - started,
                  invocation=sys.argv, ijson_version=ijson.__version__,
                  peak_rss_bytes=rss if sys.platform == "darwin" else rss * 1024,
                  limitations=["No Lean replay or model probe was run.",
                               "Headers omit section variable declarations; resolve source locators in Lean.",
                               "Text normalization and token-set Jaccard are heuristics, not semantic equivalence.",
                               "Proof-text equality can reflect common tactics; it alone does not remove a theorem.",
                               "Official random splits share source files and theorem families.",
                               "Unknown pretraining exposure and unannotated premise references remain unaudited.",
                               "The raw archive contains evaluation proofs and is audit-only."])
    (output / "audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.archive, json.loads(args.config.read_text()), args.output)
    print(json.dumps({k: report[k] for k in ("official_counts", "candidates", "retained", "removed", "train_tactic_records", "elapsed_seconds", "peak_rss_bytes")}, indent=2))


if __name__ == "__main__":
    main()
