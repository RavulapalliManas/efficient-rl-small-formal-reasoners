"""Audit the EXP-024 corpus for SFT and protect a reference set of evaluation statements (EXP-031).

Three subcommands, run in order:
  prepare   writes corpus.jsonl, reference.jsonl and items.jsonl (one Lean check per item, controls included).
  check     checks the items of one shard (i/n) and appends to results_<i>.jsonl; it skips ids already written.
  finalize  joins the results, applies the overlap checks and the dev split, and writes sft_corpus_v1.jsonl,
            reference_audit.jsonl and summary.json into --dest (default --out).
  export    writes self-contained train, dev and test-pool files with the accepted proof text from a finalize output.
  extend    copies a prepare output, appends more reference statements (FormalMATH, ProverBench, PutnamBench) with their
            items, and copies the existing results, so `check` runs only the new items before `finalize` runs again.

Every check uses the pinned Lean and mathlib with `set_option autoImplicit false` added to the historical header.
Corpus proofs use check_batch strict mode (forbidden constructs, one command, target-name guard, axiom allowlist).
Statements are checked with `sorry`. Reference statements also get the 11 tactics of the EXP-024 screen one at a
time, a vacuity check (`False` from the hypotheses) and a negation check, each with one `first | ...` battery.
Overlap with the reference set: exact normalized text, text with bound names renamed in order, and any shared
13-token window of the normalized text. A flagged corpus statement leaves train and dev. With --ngram-scope goal,
the 13-token windows come from the goal after the top-level colon only, because shared binder blocks such as
`(p : ℕ) (hp : Nat.Prime p)` flagged 32.6 percent of the corpus under the registered whole-statement rule.
Memory: one Lean process per shard, measured peak 3.7 GB RSS in earlier runs. Some tactic checks exceed 12 GiB
(for example `simp_all` on a recurrence), so run each shard under a 12 GiB cap with systemd OOMPolicy=continue:
the kernel then kills only Lean, and the item is retried alone and recorded as `crash` if it fails again.
"""
import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
import random
import re
import shutil
import sys

TACTICS = ["rfl", "decide", "norm_num", "simp", "omega", "linarith", "aesop", "simp_all", "tauto", "exact?", "nlinarith"]
# `first` commits to the first tactic that does not fail, so each alternative must also close the goal.
BATTERY = "first " + " ".join(f"| ({t}; done)" for t in ["decide", "norm_num", "simp_all", "omega", "linarith", "nlinarith", "aesop", "tauto"])
NGRAM = 13
DEV_GROUPS = 500
SEED = 20261115
REFERENCE_POOLS = {
    "prepared_v1": "nt_benchmark_v2_20261016/prepared_v1/data/pool.jsonl",
    "release_v3": "nt_dataset_cleanup_20261017_v1/release_v3/benchmark/pool.jsonl",
    "exp028_selection": "exp028_selection_20261018_v1/verification_v3/data/pool.jsonl",
}
CORPUS = "exp024_corpus_v0/out/corpus_v0.jsonl"
MINIF2F_V1 = "external/minif2f_aimo/train-00000-of-00001.parquet"
MINIF2F_V2 = {"minif2f_v2s": "external/minif2f_v2_20261115/src/datasets/miniF2F_v2s.jsonl",
              "minif2f_v2c": "external/minif2f_v2_20261115/src/datasets/miniF2F_v2c.jsonl"}
EXPANSION = "external/eval_expansion_20261115"  # FormalMATH-All, DeepSeek-ProverBench, PutnamBench; hashes in sources.sha256
PREAMBLE_COMMANDS = re.compile(r"(?m)^\s*(?:noncomputable\s+)?(?:def|abbrev|instance|structure|inductive|axiom|variable|"
                               r"namespace|section|local|scoped|notation|macro|lemma)\b")
CONTROLS = [  # (id, job, text, expected status)
    ("ctrl_proof_good", "proof", "theorem c (n : ℕ) (h : 1 < n) : 0 < n := by omega", "accepted"),
    ("ctrl_proof_wrong", "proof", "theorem c (n : ℕ) : n < 0 := by omega", "rejected"),
    ("ctrl_proof_unbound", "proof", "theorem c (h : 1 < n) : 0 < n := by omega", "rejected"),
    ("ctrl_proof_sorry", "proof", "theorem c (n : ℕ) (h : 1 < n) : 0 < n := by sorry", "forbidden_construct"),
    ("ctrl_proof_native", "proof", "theorem c : 2 + 2 = 4 := by native_decide", "forbidden_construct"),
    ("ctrl_stmt_clean", "stmt", "theorem c (n : ℕ) (h : 1 < n) : 0 < n := by sorry", "sorry_only"),
    ("ctrl_stmt_unbound", "stmt", "theorem c (h : 1 < n) : 0 < n := by sorry", "rejected"),
    ("ctrl_tac_easy", "tac:norm_num", "theorem c : (2 : ℕ) + 2 = 4 := by norm_num", "accepted"),
    ("ctrl_tac_hard", "tac:nlinarith", "theorem c (n : ℕ) : ∑ i in Finset.range n, (2 * i + 1) = n ^ 2 := by nlinarith", "rejected"),
    ("ctrl_vacuous_yes", "vacuous", f"theorem c (n : ℕ) (h1 : n < 2) (h2 : 3 < n) : False := by {BATTERY}", "accepted"),
    ("ctrl_vacuous_no", "vacuous", f"theorem c (n : ℕ) (h : 1 < n) : False := by {BATTERY}", "rejected"),
    ("ctrl_negation_yes", "negation", f"theorem c (n : ℕ) : ¬ (n + 1 = n) := by {BATTERY}", "accepted"),
    ("ctrl_negation_no", "negation", f"theorem c (n : ℕ) (h : 1 < n) : ¬ (0 < n) := by {BATTERY}", "rejected"),
]
EXTEND_CONTROLS = [  # the `open ... in` prefix and a solo item with a helper definition
    ("ctrl_open_in", "stmt", "open Finset in\ntheorem c : (range 3).card = 3 := by sorry", "sorry_only"),
    ("ctrl_open_scoped", "stmt", "theorem c : (range 3).card = 3 := by sorry", "rejected"),
    ("ctrl_open_chain", "stmt", "open Real Set in\nopen scoped BigOperators in\ntheorem c : (∑ i in Finset.range 3, i) = 3 := by sorry",
     "sorry_only"),
    ("ctrl_solo_def", "stmt", "def f (n : ℕ) : ℕ := n + 1\ntheorem c : f 1 = 2 := by sorry", "sorry_only"),
]


def strip_comments(text):
    text = re.sub(r"/-.*?-/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", " ", text)


def bare_statement(text):
    """The statement without any file header, `theorem name`, comments and a trailing `:= by sorry` or `:= by`.
    AI-MO miniF2F rows carry the import header; 16 miniF2F-v2c rows define an answer `abbrev` first, which is dropped,
    so those statements do not elaborate alone."""
    text = strip_comments(text)
    match = re.search(r"(?:^|\n)\s*(?:theorem|lemma)\s+[^\s:(\[{⦃]+", text)  # `name:` keeps its colon
    text = text[match.end():] if match else text
    return re.sub(r":=\s*(by)?\s*(sorry)?\s*$", "", text.strip()).strip()


def own_line(proof):
    """Strict mode inserts its guard after the first `:= by`. A tactic left on that line would then sit outside the
    block, so it moves to its own indented line. Only the first `:= by` is touched."""
    match = re.search(r":=\s*by\b", proof)
    if match and re.match(r"[ \t]+[^\s-]", proof[match.end():]):
        return proof[:match.end()] + "\n  " + proof[match.end():].lstrip(" \t")
    return proof


def split_statement(statement):
    """(binders, goal): the first `:` outside brackets separates them, because every binder is bracketed."""
    depth = 0
    for i, ch in enumerate(statement):
        if ch in "([{⟨⦃":
            depth += 1
        elif ch in ")]}⟩⦄":
            depth -= 1
        elif ch == ":" and depth == 0 and statement[i + 1:i + 2] != "=":
            return statement[:i].strip(), statement[i + 1:].strip()
    raise ValueError("no top-level colon")


def tokens(statement):
    """`∑ x in s` (older mathlib notation) and `∑ x ∈ s` are the same statement, so `in` becomes `∈`."""
    return ["∈" if t == "in" else t for t in re.findall(r"[^\s()\[\]{}⟨⟩,:]+|[()\[\]{}⟨⟩,:]", statement)]


def bound_names(statement):
    names = set()
    try:
        binders, goal = split_statement(statement)
    except ValueError:  # no top-level colon: a goal with no binders, or a malformed source row
        binders, goal = "", statement
    for group in re.findall(r"[(\[{⦃]([^()\[\]{}⦃⦄]*?):", binders):
        names.update(group.split())
    for group in re.findall(r"(?:∀|∃!?|λ|fun|∑|∏)\s+([^,:=∈]+?)\s*(?:[:,∈]|=>|\bin\b)", goal):
        names.update(n.strip("()") for n in group.split())
    return {n for n in names if re.fullmatch(r"[^\W\d][\w'₀-₉]*", n)}


def goal_text(statement):
    try:
        return split_statement(statement)[1]
    except ValueError:
        return statement


def keys(statement):
    """(exact key, renamed key, set of 13-token windows) of a bare statement."""
    toks = tokens(statement)
    names, order = bound_names(statement), {}
    renamed = []
    for t in toks:
        if t in names:
            order.setdefault(t, f"v{len(order)}")
            renamed.append(order[t])
        else:
            renamed.append(t)
    grams = {" ".join(toks[i:i + NGRAM]) for i in range(len(toks) - NGRAM + 1)}
    return " ".join(toks), " ".join(renamed), grams


def preamble(text):
    """(prefix, solo) for a reference file. `open` lines become `open ... in` prefixes, so they cannot leak into the
    next statement of a batch. Any other command before the theorem (a helper `def`, `abbrev`, `variable`, ...) is kept
    verbatim, and the item is checked alone (solo) so its names cannot collide with another item's."""
    text = strip_comments(text)
    match = re.search(r"(?:^|\n)\s*(?:theorem|lemma)\s", text)
    head = re.sub(r"(?m)^\s*import\s.*$", "", text[:match.start()] if match else text)
    if PREAMBLE_COMMANDS.search(head):
        return head.strip() + "\n", True
    # one `open ... in` per line: `open A` and `open scoped B` cannot merge into `open A scoped B`
    lines = [re.sub(r"\s+in$", "", line) for line in re.findall(r"(?m)^\s*open\s+([^\n]*?)\s*$", head)]
    return "".join(f"open {line} in\n" for line in lines), False


def expansion_rows(root):
    """(source, name, split, text, protect_only) of the added reference sets. PutnamBench problems whose answer is a
    `..._solution := sorry` placeholder cannot be checked without the answer, so they only protect training data."""
    base = root / EXPANSION
    for r in json.load(open(base / "formalmath" / "FormalMATH_all.json")):
        yield "formalmath", r["theorem_names"], "test", r["autoformalization"], False
    for r in map(json.loads, open(base / "proverbench" / "proverbench.jsonl")):
        yield "proverbench", r["name"], r["area"], r["header"] + "\n" + r["formal_statement"], False
    for path in sorted((base / "putnambench" / "lean4" / "src").glob("*.lean")):
        text = path.read_text()
        yield "putnambench", path.stem, "test", text, "_solution" in text


def reference_items(rid, statement, prefix, solo):
    items = [{"id": f"{rid}_stmt", "job": "stmt", "text": f"{prefix}theorem c {statement} := by sorry"}]
    items += [{"id": f"{rid}_tac_{t}", "job": f"tac:{t}", "text": f"{prefix}theorem c {statement} := by {t}"}
              for t in TACTICS]
    try:
        binders, goal = split_statement(statement)
        items.append({"id": f"{rid}_vacuous", "job": "vacuous", "text": f"{prefix}theorem c {binders} : False := by {BATTERY}"})
        items.append({"id": f"{rid}_negation", "job": "negation",
                      "text": f"{prefix}theorem c {binders} : ¬ ({goal}) := by {BATTERY}"})
    except ValueError:
        pass
    return [dict(i, solo=True) if solo else i for i in items]


def extend(args):
    src, out = args.src, args.out
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy(src / "corpus.jsonl", out / "corpus.jsonl")
    reference = [json.loads(l) for l in open(src / "reference.jsonl")]
    items = [json.loads(l) for l in open(src / "items.jsonl")]  # old items keep their positions, so shards keep theirs
    seen = {r["statement"]: r["rid"] for r in reference}
    counts = Counter()
    for source, name, split, text, protect in expansion_rows(args.root):
        if not re.search(r"(?:^|\n)\s*(?:theorem|lemma)\s", strip_comments(text)):
            counts[source + "_no_theorem"] += 1
            continue
        statement = bare_statement(text)
        new = statement not in seen
        rid = seen.setdefault(statement, f"r{len(seen)}")
        reference.append({"rid": rid, "source": source, "name": name,
                          "split": "protect_only" if protect else split, "statement": statement})
        counts[source + "_rows"] += 1
        if new and not protect:
            prefix, solo = preamble(text)
            items += reference_items(rid, statement, prefix, solo)
            counts[source + "_checked"] += 1
            counts["solo_statements"] += solo
    items += [{"id": f"{i}_ext", "job": j, "expected": e, "text": own_line(t) if j == "proof" else t}
              for i, j, t, e in CONTROLS + EXTEND_CONTROLS]
    for name, data in (("reference", reference), ("items", items)):
        with (out / f"{name}.jsonl").open("x") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in data)
    for path in src.glob("results_*.jsonl"):
        shutil.copy(path, out / path.name)
    print(json.dumps({"reference_rows": len(reference), "reference_unique": len(seen), "items": len(items), **counts}))


def prepare(args):
    root, out = args.root, args.out
    out.mkdir(parents=True, exist_ok=False)
    corpus, reference, items = [], [], []
    for i, line in enumerate(open(root / CORPUS)):
        r = json.loads(line)
        cid = f"c{i}"
        corpus.append({"cid": cid, "statement": r["statement"].strip(), "sources": r["sources"],
                       "proofs": [{"pid": f"{cid}_p{j}", "source": p["source"], "lines": p["lines"]}
                                  for j, p in enumerate(r["proofs"])]})
        items.append({"id": cid, "job": "stmt", "text": "theorem c " + r["statement"].strip() + " := by sorry"})
        for j, p in enumerate(r["proofs"]):
            items.append({"id": f"{cid}_p{j}", "job": "proof", "text": own_line(p["text"])})
    rows = []
    for name, rel in MINIF2F_V2.items():
        rows += [(name, r["name"], r["split"], r["formal_statement"]) for r in map(json.loads, open(root / rel))]
    import pyarrow.parquet as pq
    rows += [("minif2f_v1_aimo", r["name"], "valid", r["formal_statement"])  # the AI-MO file holds miniF2F-valid
             for r in pq.read_table(root / MINIF2F_V1).to_pylist()]
    for name, rel in REFERENCE_POOLS.items():
        rows += [(name, r["name"], r["split"], r["formal_statement"])
                 for r in map(json.loads, open(root / rel)) if r["split"] != "train"]
    seen = {}
    for source, name, split, text in rows:
        statement = bare_statement(text)
        new = statement not in seen
        rid = seen.setdefault(statement, f"r{len(seen)}")
        reference.append({"rid": rid, "source": source, "name": name, "split": split, "statement": statement})
        if not new:
            continue
        items.append({"id": f"{rid}_stmt", "job": "stmt", "text": f"theorem c {statement} := by sorry"})
        for t in TACTICS:
            items.append({"id": f"{rid}_tac_{t}", "job": f"tac:{t}", "text": f"theorem c {statement} := by {t}"})
        try:
            binders, goal = split_statement(statement)
        except ValueError:
            continue
        items.append({"id": f"{rid}_vacuous", "job": "vacuous", "text": f"theorem c {binders} : False := by {BATTERY}"})
        items.append({"id": f"{rid}_negation", "job": "negation", "text": f"theorem c {binders} : ¬ ({goal}) := by {BATTERY}"})
    items += [{"id": i, "job": j, "expected": e,
               "text": own_line(t) if j == "proof" else t}
              for i, j, t, e in CONTROLS]
    for name, data in (("corpus", corpus), ("reference", reference), ("items", items)):
        with (out / f"{name}.jsonl").open("x") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in data)
    print(json.dumps({"corpus": len(corpus), "reference_rows": len(reference), "reference_unique": len(seen),
                      "items": len(items), "jobs": Counter(i["job"].split(":")[0] for i in items)}))


def check(args):
    sys.path.insert(0, str(args.code))
    import verify_external_proofs as vep
    if args.header == "plain":  # the added sources compile with only their own `open` lines
        vep.HEADER = re.sub(r"(?m)^open .*\n", "", vep.HEADER)
    vep.HEADER = vep.HEADER + "set_option autoImplicit false\n"
    shard, count = map(int, args.shard.split("/"))
    items = [json.loads(l) for k, l in enumerate(open(args.out / "items.jsonl")) if k % count == shard]
    path = args.out / f"results_{shard}.jsonl"
    done = {json.loads(l)["id"] for l in open(path)} if path.exists() else set()
    env_root = args.environment.resolve()
    lean = env_root / "lean-4.10.0-rc1-linux/bin/lean"
    libs = [env_root / "mathlib/.lake/build/lib"] + sorted((env_root / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    work = args.out / "lean" / f"shard_{shard}.lean"
    work.parent.mkdir(exist_ok=True)
    groups = defaultdict(list)
    for item in items:
        if item["id"] not in done:
            groups[item["job"] == "proof"].append(item)
    def batches(todo):
        run = []
        for item in todo:
            if item.get("solo"):
                yield [item]
            else:
                run.append(item)
                if len(run) == args.batch:
                    yield run
                    run = []
        if run:
            yield run

    with path.open("a") as out:
        for strict, todo in groups.items():
            b = 0
            for batch in batches(todo):
                results, seconds = vep.check_batch(lean, env, work, batch, args.timeout, strict=strict)
                if any(r["status"] in ("batch_timeout", "crash") for r in results):  # retry each item alone
                    results = [vep.check_batch(lean, env, work, [item], args.timeout // 3, strict=strict)[0][0]
                               for item in batch]
                for item, result in zip(batch, results):
                    out.write(json.dumps({"id": item["id"], "job": item["job"], **result}, ensure_ascii=False) + "\n")
                out.flush()
                b += len(batch)
                print(json.dumps({"shard": shard, "strict": strict, "done": b, "of": len(todo),
                                  "seconds": round(seconds, 1)}), flush=True)


def finalize(args):
    out = args.out
    dest = args.dest or out
    dest.mkdir(parents=True, exist_ok=True)
    gram_text = goal_text if args.ngram_scope == "goal" else (lambda statement: statement)
    results = {}
    for path in sorted(out.glob("results_*.jsonl")):
        for line in open(path):
            r = json.loads(line)
            results[r["id"]] = r
    items = [json.loads(l) for l in open(out / "items.jsonl")]
    missing = [i["id"] for i in items if i["id"] not in results]
    assert not missing, f"{len(missing)} items have no result, first {missing[:5]}"
    status = {k: r["status"] for k, r in results.items()}
    controls = {i["id"]: {"expected": i["expected"], "got": status[i["id"]], "pass": status[i["id"]] == i["expected"]}
                for i in items if "expected" in i}

    reference = [json.loads(l) for l in open(out / "reference.jsonl")]
    ref_exact, ref_renamed, ref_grams = {}, {}, defaultdict(set)
    ref_rows = {}
    for r in reference:
        ref_rows.setdefault(r["rid"], {"rid": r["rid"], "statement": r["statement"], "members": []})["members"].append(
            {"source": r["source"], "name": r["name"],  # reference.jsonl from earlier runs labels AI-MO "test"
             "split": "valid" if r["source"] == "minif2f_v1_aimo" else r["split"]})
    for rid, r in ref_rows.items():
        exact, renamed, _ = keys(r["statement"])
        grams = keys(gram_text(r["statement"]))[2]
        ref_exact[exact] = rid
        ref_renamed[renamed] = rid
        for g in grams:
            ref_grams[g].add(rid)

    rows, reasons = [], Counter()
    for r in map(json.loads, open(out / "corpus.jsonl")):
        exact, renamed, _ = keys(r["statement"])
        grams = keys(gram_text(r["statement"]))[2]
        overlap = {"exact": ref_exact.get(exact), "renamed": ref_renamed.get(renamed),
                   "ngram13": sorted({rid for g in grams for rid in ref_grams.get(g, ())})[:5]}
        for p in r["proofs"]:
            p["status"] = status[p["pid"]]
        accepted = sum(p["status"] == "accepted" for p in r["proofs"])
        reason = ("statement_fails" if status[r["cid"]] != "sorry_only" else
                  "no_accepted_proof" if not accepted else
                  "overlap_exact" if overlap["exact"] else
                  "overlap_renamed" if overlap["renamed"] else
                  "overlap_ngram13" if overlap["ngram13"] else None)
        reasons[reason or "eligible"] += 1
        rows.append(dict(r, statement_status=status[r["cid"]], accepted_proofs=accepted, overlap=overlap,
                         renamed_key=hashlib.sha256(renamed.encode()).hexdigest()[:16], exclusion=reason))

    groups = sorted({r["renamed_key"] for r in rows if r["exclusion"] is None})
    if args.dev_from:  # keep an earlier dev split fixed; its groups that are no longer eligible drop out
        dev_keys = {json.loads(l)["group"] for l in open(args.dev_from)} & set(groups)
    else:
        dev_keys = set(random.Random(SEED).sample(groups, DEV_GROUPS))
    for r in rows:
        r["split"] = "excluded" if r["exclusion"] else ("dev" if r["renamed_key"] in dev_keys else "train")
    train_keys = {r["renamed_key"] for r in rows if r["split"] == "train"}
    assert not train_keys & dev_keys, "a renamed key is in both train and dev"
    assert not any(r["split"] != "excluded" and (r["overlap"]["exact"] or r["overlap"]["renamed"] or r["overlap"]["ngram13"])
                   for r in rows), "an overlapping statement is in train or dev"

    ref_out = []
    for rid, r in ref_rows.items():
        tac = {t: status.get(f"{rid}_tac_{t}") for t in TACTICS}
        ref_out.append(dict(r, statement_status=status.get(f"{rid}_stmt"), tactics=tac,
                            trivial=any(s == "accepted" for s in tac.values()),
                            vacuous=status.get(f"{rid}_vacuous") == "accepted",
                            negation_proved=status.get(f"{rid}_negation") == "accepted"))

    with (dest / "sft_corpus_v1.jsonl").open("x") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    with (dest / "reference_audit.jsonl").open("x") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in ref_out)
    by_source = defaultdict(Counter)
    for r in ref_out:
        for m in {m["source"] for m in r["members"]}:
            elab = r["statement_status"] == "sorry_only"
            by_source[m].update({"statements": 1, "elaborates": elab, "trivial": elab and r["trivial"],
                                 "vacuous": elab and r["vacuous"], "negation_proved": elab and r["negation_proved"]})
    summary = {
        "controls": controls, "controls_all_pass": all(c["pass"] for c in controls.values()),
        "corpus_statements": len(rows), "exclusion": dict(reasons),
        "splits": dict(Counter(r["split"] for r in rows)),
        "split_proofs": {s: sum(r["accepted_proofs"] for r in rows if r["split"] == s) for s in ("train", "dev")},
        "proof_status": dict(Counter(p["status"] for r in rows for p in r["proofs"])),
        "statement_status": dict(Counter(r["statement_status"] for r in rows)),
        "reference_unique": len(ref_out), "reference_by_source": {k: dict(v) for k, v in by_source.items()},
        "trivial_by_tactic": dict(Counter(t for r in ref_out if r["statement_status"] == "sorry_only"
                                          for t, s in r["tactics"].items() if s == "accepted")),
        "seed": SEED, "dev_groups": len(dev_keys), "dev_from": str(args.dev_from) if args.dev_from else None, "ngram": NGRAM, "ngram_scope": args.ngram_scope, "battery": BATTERY,
        "lean": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5",
        "code_sha256": {n: hashlib.sha256((Path(__file__).resolve().parent / n).read_bytes()).hexdigest()
                        for n in ("audit_sft_corpus.py", "verify_external_proofs.py")},
    }
    (dest / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("controls_all_pass", "exclusion", "splits", "proof_status")}))


MINIF2F_SOURCES = {"minif2f_v1_aimo", "minif2f_v2s", "minif2f_v2c"}


def export(args):
    """One row per problem, so no problem appears twice in a file.
    train.jsonl and dev.jsonl: corpus statements with the same renamed key become one row with every distinct accepted
    proof, in the text Lean checked. test_pool.jsonl: reference statements with the same renamed key or the same miniF2F
    problem name become one problem; each version keeps its own statement and audit flags.
    excluded.jsonl: the excluded corpus statements and why."""
    src, dest = args.src, args.dest
    dest.mkdir(parents=True, exist_ok=False)
    text = {i["id"]: i["text"] for i in map(json.loads, open(args.out / "items.jsonl")) if i["job"] == "proof"}
    counts = Counter()
    merged = {"train": {}, "dev": {}}
    with (dest / "excluded.jsonl").open("x") as f:
        for r in map(json.loads, open(src / "sft_corpus_v1.jsonl")):
            if r["split"] == "excluded":
                f.write(json.dumps({"id": r["cid"], "statement": r["statement"], "exclusion": r["exclusion"],
                                    "overlap": r["overlap"]}, ensure_ascii=False) + "\n")
                counts["excluded"] += 1
                continue
            proofs = [{"id": p["pid"], "source": p["source"], "proof": text[p["pid"]]}
                      for p in r["proofs"] if p["status"] == "accepted"]
            assert len(proofs) == r["accepted_proofs"] > 0
            counts[r["split"] + "_statements"] += 1
            row = merged[r["split"]].setdefault(r["renamed_key"], {"id": r["cid"], "statement": r["statement"],
                                                                   "sources": [], "group": r["renamed_key"],
                                                                   "merged_ids": [], "proofs": []})
            row["merged_ids"].append(r["cid"])
            row["sources"] = sorted(set(row["sources"]) | set(r["sources"]))
            seen = {p["proof"] for p in row["proofs"]}
            for proof in proofs:
                if proof["proof"] not in seen:
                    seen.add(proof["proof"])
                    row["proofs"].append(proof)
    for split, rows in merged.items():
        with (dest / f"{split}.jsonl").open("x") as f:
            for row in rows.values():
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        counts[split] = len(rows)
        counts[split + "_proofs"] = sum(len(r["proofs"]) for r in rows.values())

    ref = [json.loads(l) for l in open(src / "reference_audit.jsonl")]
    parent = list(range(len(ref)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    first = {}
    for i, r in enumerate(ref):
        links = [("key", keys(r["statement"])[1])]
        links += [("minif2f", m["name"]) for m in r["members"] if m["source"] in MINIF2F_SOURCES]
        for link in links:
            parent[root(i)] = root(first.setdefault(link, i))
    problems = defaultdict(list)
    for i, r in enumerate(ref):
        problems[root(i)].append(r)
    with (dest / "test_pool.jsonl").open("x") as f:
        for versions in problems.values():
            out = [{"rid": v["rid"], "statement": v["statement"], "members": v["members"],
                    "elaborates": v["statement_status"] == "sorry_only", "trivial": v["trivial"], "tactics": v["tactics"],
                    "vacuous": v["vacuous"], "negation_proved": v["negation_proved"]} for v in versions]
            ok = [v for v in out if v["elaborates"]]
            row = {"id": versions[0]["rid"], "names": sorted({m["name"] for v in versions for m in v["members"]}),
                   "sources": sorted({m["source"] for v in versions for m in v["members"]}),
                   "elaborates": bool(ok), "trivial": any(v["trivial"] for v in ok),
                   "flagged": any(v["vacuous"] or v["negation_proved"] for v in ok), "versions": out}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            counts["test_pool_problems"] += 1
            counts["test_pool_elaborates"] += row["elaborates"]
            counts["test_pool_trivial"] += row["trivial"]
            counts["test_pool_flagged"] += row["flagged"]
        counts["test_pool_versions"] = len(ref)

    summary = json.load(open(src / "summary.json"))
    for split in ("train", "dev"):
        assert counts[split + "_statements"] == summary["splits"][split]
    assert counts["train_proofs"] <= summary["split_proofs"]["train"] and counts["dev_proofs"] <= summary["split_proofs"]["dev"]
    manifest = {"experiment": "EXP-031", "run": "sft_corpus_audit_20261115_v1", "counts": dict(counts),
                "summary": summary,
                "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dest.glob("*.jsonl"))}}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(dict(counts)))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("check")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--code", type=Path, required=True, help="directory holding verify_external_proofs.py")
    c.add_argument("--environment", type=Path, required=True)
    c.add_argument("--shard", required=True, help="i/n")
    c.add_argument("--batch", type=int, default=25)
    c.add_argument("--timeout", type=int, default=1800)
    c.add_argument("--header", choices=("historical", "plain"), default="historical",
                   help="plain drops the historical `open` line; the extension items carry their own")
    f = sub.add_parser("finalize")
    f.add_argument("--out", type=Path, required=True)
    f.add_argument("--dest", type=Path, help="output directory (default --out)")
    f.add_argument("--ngram-scope", choices=("statement", "goal"), default="statement")
    f.add_argument("--dev-from", type=Path, help="dev.jsonl of an earlier export; reuse its groups as dev")
    x = sub.add_parser("extend")
    x.add_argument("--root", type=Path, required=True)
    x.add_argument("--src", type=Path, required=True, help="prepare output with results")
    x.add_argument("--out", type=Path, required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", type=Path, required=True, help="prepare output, for the proof text")
    e.add_argument("--src", type=Path, required=True, help="finalize output")
    e.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args()
    {"prepare": prepare, "check": check, "finalize": finalize, "export": export, "extend": extend}[args.command](args)


if __name__ == "__main__":
    main()
