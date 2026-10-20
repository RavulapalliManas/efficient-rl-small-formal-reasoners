"""SFT examples from Lean-checked external proofs of number-theory pool statements (train split only).

Inputs: the pool (build_nt_pool.py), proof check results (verify_external_proofs.py --mode proofs), and the
initial goal of each statement (trace_state; probe_nt_pool.py states.jsonl). A proof enters when Lean accepted it,
its problem is in the pool's train split under the same name, its statement equals the pool statement up to
whitespace, and its body has no FORBIDDEN construct and no non-indented line. Comments stay in the body: Goedel
proofs interleave informal reasoning with tactics. Format matches build_sft_data.py "proof" examples (the SFT
proof instruction with the goal state; the answer is the code block). 5% of train problems (by name hash) form an
eval-loss split; validation and test statements are never read.
--strip-comments removes block and line comments from every kept body (the interleaving ablation: the same proofs
and examples, with the informal reasoning removed); has_comments still describes the original body.
--problems keeps only the problems named in a file (one name per line; for example sft_problems.txt of build_nt_splits.py).
--augmentations adds the accepted tactic-augmentation proofs through the same filters.
--excluded-problems applies forward-only exclusions without changing the frozen split.
--syntax appends a completed syntax build. Its theorem-level loss split and examples
stay identical in the interleaved and comments-removed arms.
`coverage --help` stages existing SFT messages as a separate, non-admitted candidate pack.

Memory: pool, states, and ~11k proof rows (<300 MiB).
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval")]
from probe_nt_pool import INSTRUCTION, header  # noqa: E402
from probe_start_depth import FORBIDDEN  # noqa: E402


def body_of(text, stmt):
    """Proof body after the statement's `:= by`, or None when the proof's statement differs from stmt."""
    found = re.search(r"theorem\s+\S+", text)
    if found is None:
        return None
    parts = re.split(r":=\s*by\b", text[found.start():], maxsplit=1)
    squash = lambda s: re.sub(r"\s+", " ", s).strip()
    if len(parts) < 2 or squash(re.sub(r"^theorem\s+\S+", "", parts[0])) != squash(re.sub(r"^theorem\s+\S+", "", stmt)):
        return None
    lines = parts[1].split("\n")
    body = (["  " + lines[0].strip()] if lines[0].strip() else []) + lines[1:]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body) or None


def strip_comments(body):
    """The body without /- -/ blocks and -- comments; lines left empty are dropped."""
    body = re.sub(r"/-.*?-/", "", body, flags=re.S)
    lines = [re.sub(r"--.*", "", line).rstrip() for line in body.split("\n")]
    return "\n".join(line for line in lines if line.strip())


def proof_rows(paths, augmentations, pool):
    for path in paths:
        yield from map(json.loads, path.open())
    for path in augmentations:
        for row in map(json.loads, path.open()):
            if row["name"] not in pool:
                continue
            yield {"name": row["name"], "status": "accepted", "source": "tactic_augmentation",
                   "text": header(pool[row["name"]]) + " := by\n" + row["text"]}


METHODS = {
    "assumptions": r"intro|intros|assumption",
    "types_and_casts": r"norm_cast|exact_mod_cast|push_cast|change",
    "witnesses": r"use|exists|refine\s+⟨|rcases|obtain",
    "rewriting": r"rw|rwa|simp_rw",
    "lemma_application": r"apply|exact|refine|simpa",
    "cases": r"cases|rcases|by_cases|interval_cases|fin_cases",
    "contradiction": r"by_contra|contradiction|exfalso|false_or_by_contra",
    "induction": r"induction|induction'|strong_induction_on",
}
CONSTRUCTIONS = {
    "divisibility": r"∣|dvd",
    "gcd_or_coprimality": r"gcd|Coprime|IsCoprime",
    "congruence_or_remainder": r"MOD|ModEq|≡|%",
    "powers": r"\^|pow",
    "prime_or_factorization": r"Prime|factorization|primeFactors",
    "finite_residue_ring": r"ZMod",
    "multiplicative_order": r"orderOf|IsPrimitiveRoot",
    "quadratic_residue": r"IsSquare|legendreSym|jacobiSym",
    "arithmetic_function": r"totient|ArithmeticFunction|divisors",
    "factorial": r"factorial|!",
    "existential_construction": r"∃",
    "polynomial_equation": r"\^.*=|=.*\^",
    "digits_or_bases": r"digits|ofDigits",
}


def coverage_methods(body):
    """Syntactic method signals, never evidence of mathematical understanding."""
    code = strip_comments(body)
    labels = [name for name, pattern in METHODS.items()
              if re.search(r"(?:^|\n|;|=>)\s*(?:" + pattern + r")(?:\b|\s)", code)]
    if len(labels) > 1:
        labels.append("combined_proof")
    return labels


def coverage_main():
    """Stage existing SFT messages without Lean, inference, or new proof synthesis.

    Memory bound: <=512 retained rows x 64 KiB x 8 Python overhead = 256 MiB;
    <=50,000 pool headers/names x 4.5 KiB x 4 = 879 MiB; <=256 evaluation headers
    x 64 KiB x 4 = 64 MiB; interpreter and scratch <256 MiB. Total <1.5 GiB.
    Inputs stream, and only the best representative per coverage cell is retained.
    Managed hard cap: 2 GiB, one CPU, 600 seconds. Outputs are candidate-only.
    """
    from build_nt_pool import key
    from profile_external_proofs import tokens
    from nt_benchmark.selection import NUMERAL, TOPICS

    parser = argparse.ArgumentParser(description=coverage_main.__doc__)
    for option in ("candidate-input", "baseline", "source-pool", "evaluation-questions"):
        parser.add_argument("--" + option, type=Path, action="append", required=True)
    parser.add_argument("--exclude-names", type=Path, action="append", default=[])
    parser.add_argument("--source-review", type=Path, help="Source-only review JSONL: theorem, disposition, reason")
    parser.add_argument("--syllabus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[2:])

    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def rows(path):
        with path.open("rb") as stream:
            for number, line in enumerate(stream, 1):
                if len(line) > 65536:
                    raise ValueError(f"Row exceeds 64 KiB: {path.name}:{number}")
                yield number, json.loads(line)

    def statement(text):
        text = re.split(r":=\s*by\b", text, maxsplit=1)[0]
        return re.sub(r"^\s*(?:theorem|lemma)\s+\S+\s*", "", text).strip()

    def identity(text):
        return NUMERAL.sub("NUM", key(statement(text)))

    inputs = list(dict.fromkeys(args.candidate_input + args.baseline + args.source_pool
                               + args.evaluation_questions + args.exclude_names + [args.syllabus]
                               + ([args.source_review] if args.source_review else [])))
    receipts = {str(path): [p for name in ("manifest.json", "run.json", "checks.jsonl")
                           if (p := path.parent / name).is_file()] for path in args.candidate_input}
    inputs = list(dict.fromkeys(inputs + [p for group in receipts.values() for p in group]))
    identities = {str(path): digest(path) for path in inputs}
    pools, protected, reserved_names = {}, [], set()
    pool_rows = 0
    for path in args.source_pool:
        for _, record in rows(path):
            pool_rows += 1
            if (pool_rows > 50000 or len(record["formal_statement"].encode()) > 4096
                    or len(record["name"].encode()) > 512):
                raise ValueError("Pool exceeds the stated memory bound")
            pools[record["name"]] = {field: record[field] for field in ("name", "formal_statement", "split")}
            if record["split"] != "train":
                reserved_names.add(record["name"])
    for path in args.exclude_names:
        reserved_names.update(path.read_text().split())
    for path in args.evaluation_questions:
        for _, record in rows(path):
            reserved_names.add(record["name"])
            protected.append((record["name"], identity(record["header"]), tokens(key(statement(record["header"])))))
    if len(protected) > 256:
        raise ValueError("Evaluation headers exceed the stated memory bound")
    # Reserve historical non-TRAIN constructions as exact numeric templates too.
    old_reserved = {identity(row["formal_statement"]) for name, row in pools.items() if name in reserved_names}
    syllabus = json.loads(args.syllabus.read_text())
    families = [(f["id"], f["split"], tokens(key(f["lean_statement"]))) for f in syllabus["families"]]
    counts, baseline, available = Counter(), Counter(), Counter()
    reviews = {r["theorem"]: r for _, r in rows(args.source_review)} if args.source_review else {}
    representatives = {}
    args.output.mkdir(parents=True, exist_ok=False)

    def labels(record):
        answer = record["messages"][1]["content"]
        if record["format"] == "proof":
            code = answer.removeprefix("```lean\n").removesuffix("\n```")
            head = re.split(r":=\s*by\b", code, maxsplit=1)[0]
            body = body_of(code, head)
        elif record["format"] == "v21_observation":
            head, body = record["header"], answer.removeprefix("```lean\n").removesuffix("\n```")
        elif record["format"] == "completion":
            prompt = record["messages"][0]["content"]
            head = prompt.split("Proof so far:\n", 1)[-1].split(":= by", 1)[0]
            body = answer.removeprefix("```lean\n").removesuffix("\n```")
        else:
            return None
        if not body or not re.match(r"\s*(?:theorem|lemma)\s+", head):
            return None
        methods = coverage_methods(body)
        topics = [name for name, pattern in TOPICS if re.search(pattern, statement(head))] or ["Prerequisite logic"]
        constructions = [name for name, pattern in CONSTRUCTIONS.items() if re.search(pattern, statement(head))]
        cells = {(record["format"], topic, method) for topic in topics for method in methods}
        return head, body, methods, topics, constructions, cells

    for path in args.baseline:
        for _, record in rows(path):
            result = labels(record)
            baseline.update(result[-1] if result else [])
            counts["baseline_rows"] += 1
    with (args.output / "exclusions.jsonl").open("x") as exclusions:
        for path in args.candidate_input:
            for line, record in rows(path):
                counts["source_rows"] += 1
                name = record["theorem"]
                review = reviews.get(name)
                result = labels(record)
                reason, matches = None, []
                if result is None:
                    reason = "unsupported_or_unparsed_format"
                else:
                    head, body, methods, topics, constructions, cells = result
                    current = tokens(key(statement(head)))
                    template = identity(head)
                    for reserved, prior_template, prior in protected:
                        if template == prior_template or len(current & prior) / max(1, len(current | prior)) >= .7:
                            matches.append(reserved)
                    near_families = [fid for fid, split, prior in families if split != "A_training_family"
                                     and len(current & prior) / max(1, len(current | prior)) >= .7]
                    if review and review["disposition"] == "exclude":
                        reason = "source_semantic_review_exclusion"
                        matches = [review["reason"]]
                    elif name in reserved_names or template in old_reserved:
                        reason = "historical_evaluation_or_exclusion_identity"
                    elif matches:
                        reason = "evaluation_lexical_relative"
                    elif near_families:
                        reason = "historical_heldout_syllabus_relative"
                        matches = near_families
                    elif FORBIDDEN.search(strip_comments(body)):
                        reason = "forbidden_target"
                    elif any(re.search(r"(?<![\w.])" + re.escape(n) + r"(?![\w.])", body)
                             for n, _, _ in protected):
                        reason = "evaluation_premise_name"
                    elif not cells:
                        reason = "no_requested_method_signal"
                locator = dict(input=str(path), input_sha256=identities[str(path)], line=line,
                               saved_receipts={str(p): identities[str(p)] for p in receipts[str(path)]})
                if reason:
                    exclusions.write(json.dumps(dict(theorem=name, reason=reason, matches=matches,
                                                      provenance=locator), ensure_ascii=False) + "\n")
                    counts["excluded:" + reason] += 1
                    continue
                available.update(cells)
                counts["eligible_rows"] += 1
                record = dict(record)
                held = int(hashlib.sha256(template.encode()).hexdigest(), 16) % 100 < 5
                source_pool = pools.get(name)
                record.update(readiness="NOT_TRAINING_READY", provenance=locator,
                              loss_split="validation_within_source_train" if held else "train",
                              topic_labels=topics, construction_markers=constructions, method_signals=methods,
                              construction_review="unreviewed; markers are not semantic families",
                              historical_topic_compatibility="broader_coverage_proposal_requires_new_split"
                              if set(topics) & {"Quadratic residues", "Arithmetic functions"}
                              else "not_semantically_certified",
                              source_review=review or dict(disposition="unreviewed"),
                              source_train_eligibility="recorded_pool_train" if source_pool and source_pool["split"] == "train"
                              else "unresolved; saved SFT source membership is not admission",
                              proof_evidence="saved SFT target; source acceptance not freshly rebound",
                              original_informal_meaning="not rebound from saved SFT messages",
                              semantic_review="pending", exposure_review="pending_actual_consumed_lineage",
                              strict_replay="not_run", tokenizer_length_review="pending")
                rank = hashlib.sha256((template + "\n" + record["format"] + "\n" + body).encode()).hexdigest()
                for cell in cells:
                    previous = representatives.get(cell)
                    if previous is None or rank < previous[0]:
                        representatives[cell] = (rank, record)
                if len(representatives) > 512:
                    raise ValueError("Coverage cells exceed the stated retained-row bound")
    chosen = {rank: record for rank, record in representatives.values()}
    selected = Counter()
    for protocol in ("proof", "v21_observation", "completion"):
        with (args.output / f"candidates_{protocol}.jsonl").open("x") as stream:
            for rank, record in sorted(chosen.items()):
                if record["format"] == protocol:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    selected.update(labels(record)[-1])
                    counts["selected:" + protocol] += 1
    counts["selected"] = len(chosen)
    counts["selected_diagnostic_only"] = sum(record["source_review"]["disposition"] == "diagnostic_only"
                                              for record in chosen.values())
    counts["selected_broader_topic_proposals"] = sum(record["historical_topic_compatibility"]
        == "broader_coverage_proposal_requires_new_split" for record in chosen.values())
    with (args.output / "representative_examples.jsonl").open("x") as examples:
        shown = set()
        for _, record in sorted(chosen.items(), key=lambda pair:
                               (pair[1]["source_review"]["disposition"] == "diagnostic_only", pair[0])):
            cells = {(record["format"], method) for method in record["method_signals"]}
            fresh = cells - shown
            if fresh:
                examples.write(json.dumps(dict(record, example_for=sorted(fresh)), ensure_ascii=False) + "\n")
                shown.update(fresh)
    topics = [name for name, _ in TOPICS] + ["Prerequisite logic"]
    coverage = [dict(protocol=protocol, topic=topic, method=method,
                     baseline_rows=baseline[cell], available_rows=available[cell], selected_rows=selected[cell],
                     status="candidate_signal" if selected[cell] else "uncovered")
                for protocol in ("proof", "v21_observation", "completion") for topic in topics
                for method in [*METHODS, "combined_proof"] for cell in [(protocol, topic, method)]]
    (args.output / "coverage.json").write_text(json.dumps(dict(
        interpretation="Syntactic signals, not semantic validation or learned capability", cells=coverage,
        historical_topic_holdouts="Historical split files unchanged. Quadratic-residue and arithmetic-function rows "
                                 "are broader-coverage proposals incompatible with the historical topic holdouts.",
        construction_markers=dict(Counter(marker for record in chosen.values()
                                         for marker in record["construction_markers"]))), indent=2) + "\n")
    manifest = dict(status="NOT_TRAINING_READY", counts=dict(counts), inputs=identities,
                    source_sha256=digest(Path(__file__)), command=sys.argv,
                    strict_replay_performed=False, evaluation_proofs_read=False,
                    protocol="Saved messages unchanged; separate files per existing consumer format",
                    source_scope="Saved SFT sources only. The broader EXP-024 corpus is not projected directly: "
                                 "this build has no bound train admission and initial-state mapping for its additional rows.",
                    selection="One deterministic representative per protocol/topic/method cell; no repeated-example fill",
                    loss_partition="Numeric canonical statement hash within candidate source TRAIN; never benchmark DEV/TEST",
                    limits=dict(memory_bytes=2 * 2**30, cpu=1, seconds=600, row_bytes=65536, retained_cells=512),
                    blockers=["mathematical meaning and non-vacuity review", "semantic construction independence",
                              "actual producer and consumed lineage binding", "strict reference replay",
                              "measured tokenizer lengths", "historical acceptance evidence binding",
                              "file-context completion is not a standalone interactive example"])
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(dict(status=manifest["status"], counts=dict(counts))), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, action="append", required=True)
    parser.add_argument("--augmentations", type=Path, action="append", default=[],
                        help="Accepted proofs.jsonl from build_nt_tactic_aug.py")
    parser.add_argument("--syntax", type=Path, help="Completed build_syntax_sft.py output directory")
    parser.add_argument("--excluded-problems", type=Path, help="Additional forward-only SFT exclusions")
    parser.add_argument("--states", type=Path, required=True, help="states.jsonl with name and state")
    parser.add_argument("--max-chars", type=int, default=4000)
    parser.add_argument("--strip-comments", action="store_true", help="remove comments from every kept body")
    parser.add_argument("--problems", type=Path, help="file of problem names to keep, one per line")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pool = {r["name"]: r for r in map(json.loads, args.pool.open()) if r["split"] == "train"}
    keep = set(args.problems.read_text().split()) if args.problems else None
    excluded = set(args.excluded_problems.read_text().split()) if args.excluded_problems else set()
    states = {r["name"]: r["state"] for r in map(json.loads, args.states.open()) if r["state"]}
    counts, seen = Counter(), set()
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / "train.jsonl").open("x") as train, (args.output / "val.jsonl").open("x") as val:
        for r in proof_rows(args.proofs, args.augmentations, pool):
            counts["proof_rows"] += 1
            if r["status"] != "accepted":
                counts["skipped_not_accepted"] += 1
                continue
            row = pool.get(r["name"])
            if row is None:
                counts["skipped_not_train_pool_name"] += 1
                continue
            if keep is not None and r["name"] not in keep:
                counts["skipped_not_in_problems"] += 1
                continue
            if r["name"] in excluded:
                counts["skipped_additional_exclusion"] += 1
                continue
            if r["name"] not in states:
                counts["skipped_no_state"] += 1
                continue
            stmt = header(row)
            body = body_of(r["text"], stmt)
            if body is None:
                counts["skipped_statement_differs"] += 1
            elif FORBIDDEN.search(body) or any(l.strip() and not l[0].isspace() for l in body.split("\n")):
                counts["skipped_forbidden_or_top_level"] += 1
            elif len(body) > args.max_chars:
                counts["skipped_too_long"] += 1
            elif (r["name"], body) in seen:
                counts["skipped_duplicate_body"] += 1
            else:
                seen.add((r["name"], body))
                source = r["source"].split("_proof")[0]
                comments = "/-" in body or bool(re.search(r"^\s*--", body, re.M))
                if args.strip_comments:
                    body = strip_comments(body)
                held = int(hashlib.sha256(r["name"].encode()).hexdigest(), 16) % 100 < 5
                (val if held else train).write(json.dumps({
                    "format": "proof", "theorem": r["name"], "source": source, "has_comments": comments,
                    "messages": [{"role": "user", "content": INSTRUCTION.format(state=states[r["name"]])},
                                 {"role": "assistant", "content": "```lean\n" + stmt + " := by\n" + body + "\n```"}]},
                    ensure_ascii=False) + "\n")
                counts["kept_" + ("val" if held else "train")] += 1
                counts[f"kept_{source}_{'with' if comments else 'without'}_comments"] += 1
        if args.syntax:
            if not (args.syntax / "manifest.json").is_file():
                raise RuntimeError("Syntax build has no completion manifest")
            for split, out in (("train", train), ("val", val)):
                for r in map(json.loads, (args.syntax / f"sft_{split}.jsonl").open()):
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    counts[f"kept_{split}"] += 1
                    counts[f"kept_syntax_{split}"] += 1
    counts["problems"] = len({n for n, _ in seen})
    (args.output / "manifest.json").write_text(json.dumps({"counts": dict(counts), "max_chars": args.max_chars,
                                                           "pool": args.pool.name, "strip_comments": args.strip_comments,
                                                           "problems_file": args.problems and args.problems.name,
                                                           "excluded_problems_file": args.excluded_problems and args.excluded_problems.name,
                                                           "augmentations": [p.parent.name + "/" + p.name for p in args.augmentations],
                                                           "syntax": args.syntax and args.syntax.name},
                                                          indent=2) + "\n")
    print(json.dumps(dict(counts), indent=1), flush=True)


if __name__ == "__main__":
    coverage_main() if len(sys.argv) > 1 and sys.argv[1] == "coverage" else main()
