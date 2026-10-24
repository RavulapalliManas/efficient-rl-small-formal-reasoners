"""Import extra public Lean 4 number-theory statements and proofs, filter them like nt_pool_v2, and Lean-check them.

Sources (downloaded at pinned Hugging Face revisions and read as data only; licenses and versions are in
artifacts/notes/2026-10-10_extra_data_report.md):
  goedel_pset_v1  Goedel-LM/Goedel-Pset-v1 @79fc4fbe (MIT): formal_statement, informal_statement. Some rows carry a
                  proof instead of `sorry`; those proofs are proof candidates.
  goedel_v2_sft   Goedel-LM/SFT_dataset_v2 @da71b450 (Apache-2.0), shards 0, 6, ..., 48 of 54: the statement is the
                  first code block of the first user message; the proof is the last code block of the last message.
  stp             kfdong/STP_Lean_0320 @8759699c (MIT): the prompt ends with the statement and `:= by`; the target is
                  the proof body.

Stages. Each writes a new directory (exist_ok=False), except that check-proofs --resume appends to its own output.
  extract           Keeps code with the pool preamble only (imports Mathlib/Aesop, `open BigOperators Real Nat Topology
                    Rat`) and exactly one theorem and no other declaration. The statement text without comments must
                    pass the filters of build_nt_pool.py: NUMBER_THEORY, not REAL (ℝ ℂ √ π Real. Complex.), not closed.
                    Statements equal up to binder renaming merge (key of build_nt_pool.py). Each key is compared with
                    nt_pool_v2 (all splits, including renamed Lean Workbook duplicates) and the miniF2F test statements:
                    same key or token Jaccard >= 0.7. Near a nt_pool_v2 validation or test statement, or near miniF2F:
                    forbidden_reason, and its proofs are never checked. Near only nt_pool_v2 train: a duplicate (not a
                    new statement; its proofs stay candidates and carry the pool names).
  check-statements  Statement + `:= by sorry` with check_batch of verify_external_proofs.py; sorry_only = elaborates.
  select-proofs     Candidates whose statement elaborates and is not forbidden; at most 2 distinct bodies per statement
                    per source (sha256 order). Rejected without Lean: FORBIDDEN of probe_start_depth.py, EXTRA (below),
                    a non-indented line. The rest is sampled in sha256 order to at most --budget Lean checks, split
                    evenly over sources (an unused share passes to the other sources).
  check-proofs      verify_group of bench_lean_verifier.py under --scorer-root (train_code_v9 from 2026-10-10): the RL scorer (REPL path, pool HEADER, 20 s per proof,
                    axioms within propext, Classical.choice, Quot.sound, one-command parse check). Each proof is checked
                    against its own statement text, so the hypothesis names in the body match.
  finalize          statements.jsonl, proofs.jsonl (accepted only), summary.json in a new directory.

EXTRA (2026-10-10, after the trainer agent found scorer holes: an axiom message forged with `trace`, namespace
shadowing, trailing commands): a proof body that contains `trace`, `run_` + word, `logInfo`, `#`, or a line whose first
word is a command keyword (namespace, end, section, theorem, lemma, def, open, ...) or `@[` is rejected without Lean.

Memory: extract streams each parquet file in batches of 1,000 rows and keeps only NT candidates (measured peak in the
report). check-statements: one lean process per shard (3.7 GB peak measured for mathlib). check-proofs: --workers REPL
processes, 3.72 GB RSS each, of which 3.42 GB are shared file pages; 8 workers need about 6 GB; run under 16 GiB.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from build_nt_pool import REAL, key  # noqa: E402
from profile_external_proofs import NUMBER_THEORY, closed, statement, tokens  # noqa: E402

THRESHOLD = 0.7
PREAMBLE = re.compile(r"import (?:Mathlib|Aesop)|set_option maxHeartbeats \d+|open BigOperators Real Nat Topology Rat")
DECLARATION = re.compile(r"^[ \t]*(?:@\[|(?:theorem|lemma|def|abbrev|instance|example|noncomputable|structure|inductive|"
                         r"class|axiom|opaque|namespace|section|variable|macro|syntax|elab|notation|universe)\b)", re.M)
COMMAND_WORDS = ("namespace end section theorem lemma def example abbrev instance structure class inductive axiom axioms "
                 "open set_option attribute variable universe macro macro_rules syntax elab elab_rules notation infix "
                 "infixl infixr prefix postfix noncomputable private protected local scoped mutual deriving import "
                 "export initialize builtin_initialize declare_syntax_cat opaque unsafe partial").split()
EXTRA = re.compile(r"trace|\brun_\w+|logInfo|#|^[ \t]*(?:@\[|(?:" + "|".join(COMMAND_WORDS) + r")\b)", re.M)
STATEMENT_BAD = re.compile(r"#|trace|\brun_\w+|\bsorry\b|\badmit\b|native_decide|\baxiom\b")
PREFIX = {"goedel_pset_v1": "xgpset", "goedel_v2_sft": "xgv2", "stp": "xstp"}
OPEN, CLOSE = "([{⟨⦃", ")]}⟩⦄"
SCAN = re.compile(r"--|/-|:=|[(\[{⟨⦃)\]}⟩⦄]|(?<![\w.'])(?:let|have|letI|haveI)(?![\w'])")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def strip_comments(text):
    return re.sub(r"--[^\n]*", "", re.sub(r"/-.*?-/", " ", text, flags=re.S))


def split_declaration(code):
    """(preamble, header, rest) for the first line-initial theorem/lemma: the header runs to the first `:=` outside
    brackets and comments that does not belong to a `let` or `have` of the statement; rest follows that `:=`. None if
    there is no such declaration."""
    match = re.search(r"^(?:theorem|lemma)\s", code, re.M)
    if not match:
        return None
    depth, pending, i = 0, 0, match.start()
    while True:
        found = SCAN.search(code, i)
        if not found:
            return None
        token = found.group(0)
        if token in ("--", "/-"):
            end = code.find("\n" if token == "--" else "-/", found.end())
            if end < 0:
                return None
            i = end + (0 if token == "--" else 2)
            continue
        if token in ("let", "have", "letI", "haveI"):
            pending += depth == 0
        elif token == ":=" and depth == 0 and pending:
            pending -= 1
        elif token == ":=" and depth == 0:
            return code[:match.start()], code[match.start():found.start()].rstrip(), code[found.end():]
        else:
            depth += 1 if token in OPEN else -1 if token in CLOSE else 0
        i = found.end()


def clean_statement(header):
    """(original name, statement text after the name without comments) of a `theorem name ...` header."""
    match = re.match(r"(?:theorem|lemma)\s+(\S+)", header)
    text = strip_comments(header[match.end():])
    lines = [line.rstrip() for line in text.split("\n")]
    return match.group(1), "\n".join(line for line in lines if line.strip()).strip()


def preamble_ok(preamble, require_open=True):
    """Only pool preamble lines; the pool `open` line is required for statement code. Proof code may omit it (the
    body is checked under the pool HEADER either way)."""
    lines = [line.strip() for line in strip_comments(preamble).split("\n") if line.strip()]
    return all(PREAMBLE.fullmatch(line) for line in lines) and (
        not require_open or "open BigOperators Real Nat Topology Rat" in lines)


def body_after_by(rest):
    """The tactic body after `:= by`, every line indented, or None if the proof is not `by ...`."""
    match = re.match(r"\s*by\b", rest)
    if not match:
        return None
    first, _, remaining = rest[match.end():].partition("\n")
    lines = (["  " + first.strip()] if first.strip() else []) + remaining.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(line.rstrip() for line in lines).rstrip()


def has_comments(body):
    """The rule of body() in profile_external_proofs.py: a block comment or a line that starts with `--`."""
    return "/-" in body or bool(re.search(r"^\s*--", body, re.M))


def parse_code(code, counts, tag, require_open=True):
    """(original name, clean statement, rest after `:=`) of code with the pool preamble and one theorem, or None.
    Code with no NUMBER_THEORY match anywhere is dropped first: its statement, a substring, cannot match either."""
    if not NUMBER_THEORY.search(code):
        counts[tag + "dropped_no_number_theory_content_anywhere"] += 1
        return None
    parts = split_declaration(code)
    if parts is None:
        counts[tag + "no_theorem"] += 1
        return None
    preamble, header, rest = parts
    if not preamble_ok(preamble, require_open):
        counts[tag + "nonstandard_preamble"] += 1
        return None
    if len(DECLARATION.findall(strip_comments(code))) != 1:
        counts[tag + "other_declarations"] += 1
        return None
    name, stmt = clean_statement(header)
    return name, stmt, rest


def rows_of(path, columns):
    import pyarrow.parquet as pq
    for batch in pq.ParquetFile(path).iter_batches(batch_size=1000, columns=columns):
        yield from batch.to_pylist()


def source_records(source, paths, counts):
    """(origin id, original name, clean statement, natural language or None, proof body or None) per usable row."""
    tag = source + ":"
    for path in paths:
        if source == "goedel_pset_v1":
            for row in rows_of(path, ["problem_id", "formal_statement", "informal_statement"]):
                counts[tag + "rows"] += 1
                parsed = parse_code(row["formal_statement"], counts, tag)
                if parsed:
                    name, stmt, rest = parsed
                    placeholder = strip_comments(rest).split() in (["sorry"], ["by", "sorry"])
                    proof = None if placeholder else body_after_by(rest)
                    if proof is None and not placeholder:
                        counts[tag + "proof_not_by_block"] += 1
                    yield row["problem_id"], name, stmt, row["informal_statement"], proof
        elif source == "goedel_v2_sft":
            for k, row in enumerate(rows_of(path, ["messages"])):
                counts[tag + "rows"] += 1
                messages = row["messages"]
                first = re.findall(r"```lean4?\n(.*?)```", messages[0]["content"], re.S)
                last = re.findall(r"```lean4?\n(.*?)```", messages[-1]["content"], re.S) if messages[-1]["role"] == "assistant" else []
                if not first or not last:
                    counts[tag + "missing_code_block"] += 1
                    continue
                parsed = parse_code(first[0], counts, tag + "statement_")
                if not parsed or not re.fullmatch(r"\s*by\s+sorry\s*", parsed[2]):
                    counts[tag + "statement_not_sorry"] += bool(parsed)
                    continue
                proof_parsed = parse_code(last[-1], counts, tag + "proof_", require_open=False)
                body = body_after_by(proof_parsed[2]) if proof_parsed else None
                if proof_parsed and body is None:
                    counts[tag + "proof_not_by_block"] += 1
                yield f"{Path(path).name}:{k}", parsed[0], parsed[1], None, body
        elif source == "stp":
            for k, row in enumerate(rows_of(path, ["prompt", "target", "tag"])):
                counts[tag + "rows"] += 1
                counts[tag + "tag_" + row["tag"]] += 1
                code = row["prompt"].split("```lean4\n", 1)[-1]
                parsed = parse_code(code, counts, tag)
                if not parsed:
                    continue
                if not re.fullmatch(r"\s*by\s*", parsed[2]):
                    counts[tag + "prompt_not_ending_by"] += 1
                    continue
                body = body_after_by("by" + row["target"])
                yield f"{Path(path).name}:{k}:{row['tag']}", parsed[0], parsed[1], None, body


class NearIndex:
    """Exact token-Jaccard search against a small reference set. If J(Q, R) >= t then |Q ∩ R| >= ceil(t |Q|), so R
    holds one of any |Q| - ceil(t |Q|) + 1 tokens of Q; the probe takes the rarest ones, then computes J exactly."""

    def __init__(self, refs):
        self.refs = refs  # [(token set, label)]
        self.postings = defaultdict(list)
        for i, (t, _) in enumerate(refs):
            for token in t:
                self.postings[token].append(i)

    def near(self, q):
        if not q:
            return []
        order = sorted(q, key=lambda x: (len(self.postings.get(x, ())), x))
        probe = len(q) - (7 * len(q) + 9) // 10 + 1  # ceil(0.7 |Q|) in integers; THRESHOLD is 0.7
        seen, hits = set(), []
        for token in order[:probe]:
            for i in self.postings.get(token, ()):
                if i in seen:
                    continue
                seen.add(i)
                t = self.refs[i][0]
                if len(q & t) / len(q | t) >= THRESHOLD:
                    hits.append(self.refs[i][1])
        return hits


def references(pool_path, workbook_path, minif2f_path, counts):
    import pyarrow.parquet as pq
    with open(pool_path) as input_stream:
        pool = [json.loads(line) for line in input_stream]
    by_key = {r["key"]: (r["split"], r["name"]) for r in pool}
    duplicate_split = {d: r["split"] for r in pool for d in r["duplicates"]}
    refs = [(tokens(statement(r["formal_statement"])), ("pool_" + r["split"], r["name"])) for r in pool]
    with open(workbook_path) as _input_stream:
        for r in json.load(_input_stream):
            found = re.search(r"theorem\s+(\S+)", r["formal_statement"])
            if found and found[1] in duplicate_split:
                refs.append((tokens(statement(r["formal_statement"])), ("pool_" + duplicate_split[found[1]], found[1])))
                by_key.setdefault(key(statement(r["formal_statement"])), (duplicate_split[found[1]], found[1]))
    minif2f_keys = {}
    for row in pq.read_table(minif2f_path).to_pylist():
        parts = split_declaration(row["formal_statement"])
        name, stmt = clean_statement(parts[1])
        refs.append((tokens(stmt), ("minif2f", row["name"])))
        minif2f_keys[key(stmt)] = row["name"]
    counts["reference_pool_rows"] = len(pool)
    counts["reference_pool_duplicate_statements"] = len(refs) - len(pool) - len(minif2f_keys)
    counts["reference_minif2f_statements"] = len(minif2f_keys)
    return NearIndex(refs), by_key, minif2f_keys


def extract(args):
    counts = Counter()
    index, pool_keys, minif2f_keys = references(args.pool, args.workbook, args.minif2f, counts)
    statements, proofs, names = {}, {}, {}
    for source, paths in (("goedel_pset_v1", args.goedel_pset), ("goedel_v2_sft", args.goedel_v2), ("stp", args.stp)):
        tag = source + ":"
        for origin, original, stmt, natural, body in source_records(source, paths, counts):
            counts[tag + "parsed"] += 1
            if not stmt or STATEMENT_BAD.search(stmt):
                counts[tag + "statement_bad_construct"] += 1
                continue
            if not NUMBER_THEORY.search(stmt):
                counts[tag + "dropped_no_number_theory_content"] += 1
                continue
            if REAL.search(stmt):
                counts[tag + "dropped_real_or_complex"] += 1
                continue
            if closed(stmt):
                counts[tag + "dropped_closed_numeric"] += 1
                continue
            counts[tag + "number_theory_rows"] += 1
            k = key(stmt)
            if k not in statements:
                base = PREFIX[source] + "_" + re.sub(r"[^A-Za-z0-9_]", "_", origin.split("-")[-1] if source == "goedel_pset_v1" else original)
                name = base if base not in names else base + "_" + sha(k)[:8]
                names[name] = k
                reasons = []
                if k in pool_keys and pool_keys[k][0] != "train":
                    reasons.append(f"same_key_pool_{pool_keys[k][0]}:{pool_keys[k][1]}")
                if k in minif2f_keys:
                    reasons.append(f"same_key_minif2f:{minif2f_keys[k]}")
                hits = index.near(tokens(stmt))
                reasons += sorted({f"near_{label}:{n}" for label, n in hits if label != "pool_train"})
                train_hits = sorted({n for label, n in hits if label == "pool_train"} |
                                    ({pool_keys[k][1]} if k in pool_keys and pool_keys[k][0] == "train" else set()))
                statements[k] = {"name": name, "source": source, "formal_statement": f"theorem {name} {stmt} := by sorry",
                                 "natural_language_statement": natural, "key": k, "origin": origin,
                                 "forbidden_reason": ";".join(reasons) or None, "pool_train_duplicate_of": train_hits,
                                 "duplicates": [], "sources": [source]}
            else:
                counts[tag + "merged_renamed_duplicate"] += 1
                row = statements[k]
                row["duplicates"].append(f"{source}:{origin}")
                if source not in row["sources"]:
                    row["sources"].append(source)
                if row["natural_language_statement"] is None and natural:
                    row["natural_language_statement"] = natural
            if body:
                proof_key = sha(k + "\n" + stmt + "\n" + body)
                if proof_key in proofs:
                    counts[tag + "proof_exact_duplicate"] += 1
                else:
                    proofs[proof_key] = {"id": proof_key, "key": k, "source": source, "origin": origin,
                                         "own_statement": stmt, "body": body, "has_comments": has_comments(body)}
                    counts[tag + "proof_candidates"] += 1
                    counts[tag + "proof_candidates_with_comments"] += proofs[proof_key]["has_comments"]
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / "statements.jsonl").open("x") as out:
        for row in statements.values():
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (args.output / "proof_candidates.jsonl").open("x") as out:
        for row in proofs.values():
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    rows = list(statements.values())
    summary = {"counts": dict(sorted(counts.items())), "unique_statements": len(rows),
               "forbidden": sum(bool(r["forbidden_reason"]) for r in rows),
               "pool_train_duplicates_not_forbidden": sum(bool(r["pool_train_duplicate_of"]) and not r["forbidden_reason"] for r in rows),
               "new_not_forbidden": sum(not r["pool_train_duplicate_of"] and not r["forbidden_reason"] for r in rows),
               "first_source": dict(Counter(r["source"] for r in rows)), "proof_candidates": len(proofs)}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1), flush=True)


def check_statements(args):
    from verify_external_proofs import check_batch
    with open(args.extract / "statements.jsonl") as input_stream:
        rows = [json.loads(line) for line in input_stream]
    with open(args.extract / "proof_candidates.jsonl") as input_stream:
        with_proofs = {json.loads(line)["key"] for line in input_stream}
    # New statements always; pool-train duplicates only when a proof candidate needs them.
    todo = [r for r in rows if not r["pool_train_duplicate_of"] or r["forbidden_reason"] or r["key"] in with_proofs]
    index, count = map(int, args.shard.split("/"))
    todo = todo[index::count]
    # Reuse a verdict for the same statement text (theorem name masked) from earlier runs: elaboration depends only on
    # the text, the pin, and the header.
    known = {}
    for old_extract, old_checks in zip(args.reuse_extract or [], args.reuse_checks or []):
        with open(old_extract / "statements.jsonl") as input_stream:
            text = {r["name"]: r["formal_statement"] for r in map(json.loads, input_stream)}
        for path in old_checks.split(","):
            with open(Path(path) / "results.jsonl") as input_stream:
                for r in map(json.loads, input_stream):
                    known[re.sub(r"^theorem\s+\S+", "theorem _", text[r["name"]])] = r
    env_root = args.environment.resolve()
    lean = env_root / "lean-4.10.0-rc1-linux/bin/lean"
    libs = [env_root / "mathlib/.lake/build/lib"] + sorted((env_root / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    args.output.mkdir(parents=True, exist_ok=False)
    counts = Counter()
    with (args.output / "results.jsonl").open("x") as out:
        fresh = []
        for r in todo:
            old = known.get(re.sub(r"^theorem\s+\S+", "theorem _", r["formal_statement"]))
            if old is None:
                fresh.append(r)
            else:
                out.write(json.dumps(dict(old, name=r["name"], reused=True), ensure_ascii=False) + "\n")
                counts[old["status"]] += 1
                counts["reused"] += 1
        todo = fresh
        for b in range(0, len(todo), args.batch):
            batch = [{"name": r["name"], "source": r["source"], "text": r["formal_statement"]} for r in todo[b:b + args.batch]]
            results, seconds = check_batch(lean, env, args.output / "batch.lean", batch, args.timeout)
            for item, result in zip(batch, results):
                out.write(json.dumps({"name": item["name"], **result}, ensure_ascii=False) + "\n")
                counts[result["status"]] += 1
            out.flush()
            print(json.dumps({"done": b + len(batch), "of": len(todo), "batch_seconds": round(seconds, 1)}), flush=True)
    summary = {"items": len(todo), "shard": args.shard, "counts": dict(counts), "lean": "v4.10.0-rc1",
               "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def elaborated(paths):
    status = {}
    for path in paths:
        with open(path / "results.jsonl") as input_stream:
            for line in input_stream:
                r = json.loads(line)
                status[r["name"]] = r["status"]
    return status


def use_scorer(root, output):
    """Import the scorer modules from `root` (a scripts/ directory, for example train_code_v9/scripts) and record
    their paths and sha256 in output/run.json."""
    sys.path[:0] = [str(root.resolve() / d) for d in ("eval", "data", "audit")]
    import bench_lean_verifier
    import probe_start_depth
    record = {m.__name__: {"path": m.__file__, "sha256": file_sha(m.__file__)} for m in (bench_lean_verifier, probe_start_depth)}
    (output / "run.json").write_text(json.dumps({"scorer": record, "lean": "v4.10.0-rc1",
                                                "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}, indent=2) + "\n")
    return bench_lean_verifier, probe_start_depth


def select_proofs(args):
    args.output.mkdir(parents=True, exist_ok=False)
    FORBIDDEN = use_scorer(args.scorer_root, args.output)[1].FORBIDDEN
    statements = {}
    with open(args.extract / "statements.jsonl") as input_stream:
        for line in input_stream:
            r = json.loads(line)
            statements[r["key"]] = r
    status = elaborated(args.statement_checks)
    counts, by_source = Counter(), defaultdict(list)
    per_statement = defaultdict(list)
    with open(args.extract / "proof_candidates.jsonl") as _input_stream:
        for line in _input_stream:
            p = json.loads(line)
            s = statements[p["key"]]
            tag = p["source"] + ":"
            counts[tag + "candidates"] += 1
            if s["forbidden_reason"]:
                counts[tag + "statement_forbidden"] += 1
            elif status.get(s["name"]) != "sorry_only":
                counts[tag + "statement_does_not_elaborate"] += 1
            else:
                per_statement[(p["source"], p["key"])].append(p)
    for (source, k), group in per_statement.items():
        group.sort(key=lambda p: p["id"])
        counts[source + ":over_2_per_statement"] += max(len(group) - 2, 0)
        for p in group[:2]:
            tag = source + ":"
            body = p["body"]
            if EXTRA.search(body):
                counts[tag + "matches_extra_pattern_any"] += 1
            if FORBIDDEN.search(body):
                counts[tag + "rejected_forbidden_construct"] += 1
            elif EXTRA.search(body):
                counts[tag + "rejected_extra_pattern"] += 1
                for word in sorted({m.group(0).strip() for m in EXTRA.finditer(body)}):
                    counts[tag + "extra_pattern:" + word] += 1
            elif any(line.strip() and not line[0].isspace() for line in body.split("\n")):
                counts[tag + "rejected_top_level_line"] += 1
            else:
                by_source[source].append(p)
    budget, sources = args.budget, sorted(by_source, key=lambda s: len(by_source[s]))
    selected = []
    for i, source in enumerate(sources):
        share = budget // (len(sources) - i)
        group = sorted(by_source[source], key=lambda p: p["id"])
        take = group[:share]
        counts[source + ":eligible_for_lean"] = len(group)
        counts[source + ":sampled_out"] = len(group) - len(take)
        counts[source + ":selected"] = len(take)
        budget -= len(take)
        selected += take
    with (args.output / "selection.jsonl").open("x") as out:
        for p in selected:
            s = statements[p["key"]]
            out.write(json.dumps(dict(p, name=s["name"], header=f"theorem {s['name']} {p['own_statement']}"), ensure_ascii=False) + "\n")
    summary = {"counts": dict(sorted(counts.items())), "selected": len(selected), "budget": args.budget}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1), flush=True)


def check_proofs(args):
    with open(args.selection / "selection.jsonl") as input_stream:
        rows = [json.loads(line) for line in input_stream]
    args.output.mkdir(parents=True, exist_ok=args.resume)
    scorer = use_scorer(args.scorer_root, args.output)[0]
    repl_context, verify_group = scorer.repl_context, scorer.verify_group
    from prepare_grpo_pilot import load_checker
    done = set()
    path = args.output / "checked.jsonl"
    if path.exists():
        text = path.read_text()
        if text and not text.endswith("\n"):  # a run stopped mid-line: drop the partial record
            text = text[:text.rfind("\n") + 1]
            path.write_text(text)
        done = {json.loads(line)["id"] for line in text.splitlines()}
    todo = sorted((r for r in rows if r["id"] not in done), key=lambda r: r["id"])  # sources interleaved
    env_root = args.environment.resolve()
    checker = load_checker(args.checker, env_root, args.output)
    ctx = repl_context(checker, env_root, args.repl, args.output, args.timeout, args.setup_timeout, args.restart_after)

    def run(r):
        unit = {"task_id": "extra:" + r["id"][:12], "kind": "statement", "name": r["name"], "header": r["header"],
                "given_steps": []}
        return verify_group(ctx, unit, [r["body"]])[0]

    try:
        with ThreadPoolExecutor(args.workers) as executor, (args.output / "checked.jsonl").open("a") as out:
            for k, (r, result) in enumerate(zip(todo, executor.map(run, todo))):
                out.write(json.dumps({"id": r["id"], "source": r["source"], **result}, ensure_ascii=False) + "\n")
                if k % 200 == 0:
                    out.flush()
                    print(json.dumps({"checked": k + 1, "of": len(todo)}), flush=True)
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()


def file_sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finalize(args):
    with open(args.extract / "statements.jsonl") as input_stream:
        statements = [json.loads(line) for line in input_stream]
    status = elaborated(args.statement_checks)
    with open(args.selection / "selection.jsonl") as input_stream:
        selection = {r["id"]: r for r in map(json.loads, input_stream)}
    checked = {}
    for path in args.proof_checks:
        with open(path / "checked.jsonl") as _input_stream:
            for line in _input_stream:
                r = json.loads(line)
                checked[r["id"]] = r
    counts = Counter()
    for s in statements:
        tag = s["source"] + ":"
        st = status.get(s["name"], "not_checked")
        counts[tag + "statement_lean_" + st] += 1
    args.output.mkdir(parents=True, exist_ok=False)
    keep = [s for s in statements if not s["pool_train_duplicate_of"] and status.get(s["name"]) == "sorry_only"]
    for s in statements:
        if s["pool_train_duplicate_of"] and not s["forbidden_reason"]:
            counts[s["source"] + ":dropped_duplicate_of_pool_train"] += 1
    with (args.output / "statements.jsonl").open("x") as out:
        for s in keep:
            row = {k: s[k] for k in ("name", "source", "formal_statement", "natural_language_statement", "key",
                                     "forbidden_reason", "duplicates", "sources", "origin")}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            counts[s["source"] + ":statements_written_" + ("forbidden" if s["forbidden_reason"] else "usable")] += 1
    by_key = {s["key"]: s for s in statements}
    with (args.output / "proofs.jsonl").open("x") as out:
        for pid, r in checked.items():
            p = selection[pid]
            tag = p["source"] + ":"
            counts[tag + "proof_lean_calls"] += r.get("lean_calls", 0)
            counts[tag + "proof_status_" + r["status"]] += 1
            if r["status"] != "accepted":
                continue
            s = by_key[p["key"]]
            text = f"{p['header']} := by\n{p['body']}"
            out.write(json.dumps({"name": p["name"], "source": p["source"], "text": text, "has_comments": p["has_comments"],
                                  "key": p["key"], "formal_statement": p["header"] + " := by sorry", "origin": p["origin"],
                                  "statement_in_statements_jsonl": not s["pool_train_duplicate_of"],
                                  "pool_train_duplicate_of": s["pool_train_duplicate_of"],
                                  "lean_seconds": round(r["lean_seconds"], 3)}, ensure_ascii=False) + "\n")
            counts[tag + "proofs_accepted_" + ("with_comments" if p["has_comments"] else "without_comments")] += 1
    sources = sorted({s["source"] for s in statements})
    rates = {}
    for source in sources:
        n = sum(1 for r in checked.values() if r["source"] == source)
        a = sum(1 for r in checked.values() if r["source"] == source and r["status"] == "accepted")
        rates[source] = {"checked": n, "accepted": a, "rate": round(a / n, 4) if n else None}
    summary = {"counts": dict(sorted(counts.items())), "proof_acceptance": rates,
               "inputs": {name: json.loads((path / "summary.json").read_text()) for name, path in
                          (("extract", args.extract), ("selection", args.selection))},
               "lean": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5",
               "sha256": {f: file_sha(args.output / f) for f in ("statements.jsonl", "proofs.jsonl")}}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "inputs"}, indent=1, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    p = sub.add_parser("extract")
    p.add_argument("--goedel-pset", type=Path, nargs="+", required=True)
    p.add_argument("--goedel-v2", type=Path, nargs="+", required=True)
    p.add_argument("--stp", type=Path, nargs="+", required=True)
    p.add_argument("--pool", type=Path, required=True, help="nt_pool_v2/pool.jsonl")
    p.add_argument("--workbook", type=Path, required=True, help="lean_workbook.json, for renamed pool duplicates")
    p.add_argument("--minif2f", type=Path, required=True, help="miniF2F test parquet (AI-MO/minif2f_test)")
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("check-statements")
    for name in ("extract", "environment", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--shard", default="0/1")
    p.add_argument("--reuse-extract", type=Path, action="append", help="earlier extract directory")
    p.add_argument("--reuse-checks", action="append", help="comma-separated check-statements directories of that extract")
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--timeout", type=int, default=1800)
    p = sub.add_parser("select-proofs")
    p.add_argument("--extract", type=Path, required=True)
    p.add_argument("--statement-checks", type=Path, nargs="+", required=True)
    p.add_argument("--budget", type=int, default=60000)
    p.add_argument("--scorer-root", type=Path, required=True, help="scripts/ directory of the shared scorer (train_code_v9)")
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("check-proofs")
    for name in ("selection", "environment", "checker", "repl", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--scorer-root", type=Path, required=True, help="scripts/ directory of the shared scorer (train_code_v9)")
    p.add_argument("--timeout", type=float, default=20)
    p.add_argument("--setup-timeout", type=float, default=120)
    p.add_argument("--restart-after", type=int, default=400)
    p.add_argument("--resume", action="store_true")
    p = sub.add_parser("finalize")
    p.add_argument("--extract", type=Path, required=True)
    p.add_argument("--statement-checks", type=Path, nargs="+", required=True)
    p.add_argument("--selection", type=Path, required=True)
    p.add_argument("--proof-checks", type=Path, nargs="+", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    {"extract": extract, "check-statements": check_statements, "select-proofs": select_proofs,
     "check-proofs": check_proofs, "finalize": finalize}[args.stage](args)


if __name__ == "__main__":
    main()
