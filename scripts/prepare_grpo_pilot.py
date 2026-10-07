"""Prepare gold-free whole-proof prompts on cpu-angad.

Memory bound: 3000 headers x 64 KiB < 192 MiB, plus one Lean worker
limited to 2 GiB. Run under a 3 GiB service cap and a 1800-second timeout.
This filters standalone declarations; it does not reconstruct missing context.
"""
import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import re

FAMILIES = ["logic", "sets", "functions", "relations", "arithmetic",
            "integer_arithmetic", "recurrences", "divisibility", "gcd",
            "modular_arithmetic", "congruences", "primes", "primality",
            "factorization", "binomial", "divisors", "quadratic_residues", "diophantine"]


def ordered_ids(questions, arm, steps, seed):
    """Each pass has identical membership; only its order changes."""
    rank = {family: i for i, family in enumerate(FAMILIES)}
    base = sorted(questions, key=lambda q: (q.get("prerequisite_rank", rank.get(q["family"], len(rank))), q["id"]))
    rng = random.Random(seed)
    result = []
    while len(result) < steps:
        current = [q["id"] for q in base]
        if arm == "random":
            rng.shuffle(current)
        result.extend(current)
    if steps % len(base):
        raise ValueError("Update count must cover complete passes in both orders")
    return result


def load_checker(path, environment, output):
    spec = importlib.util.spec_from_file_location("pilot_checker", path.resolve())
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.ROOT = output.resolve()
    checker.ENV = environment.resolve()
    checker.LEAN = checker.ENV / "lean-4.10.0-rc1-linux/bin"
    return checker


def module_name(path):
    for prefix in (".lake/packages/lean4/src/lean/", ".lake/packages/batteries/"):
        path = path.removeprefix(prefix)
    if path.startswith(".lake/") or not path.endswith(".lean"):
        raise ValueError("Unsupported import path: " + path)
    return path[:-5].replace("/", ".")


def verifier_failed(record):
    if record["status"] == "forbidden_construct":
        return False
    output = record.get("output", "").lower()
    return (record["status"] != "returned" or record.get("exit_code") not in (0, 1)
            or any(message in output for message in (
                "permission denied", "unknown module prefix", "object file", "failed to import",
                "out of memory", "cannot allocate memory", "no such file", "cloning again")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--checker", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    checker = load_checker(args.checker, args.environment, args.output)
    buckets = defaultdict(list)
    source_count = 0
    source_digest = hashlib.sha256()
    with args.train.open("rb") as source:
        for line in source:
            source_digest.update(line)
            row = json.loads(line)
            source_count += 1
            if source_count > 3000 or len(line) > 1024 * 1024:
                raise ValueError("Preparation input exceeds its memory bound")
            if row["official_split"] != "train":
                raise ValueError("Non-training row in training input")
            # Gold tactic traces are discarded before any prompt or selection.
            header_row = {k: row[k] for k in (
                "theorem_id", "family", "full_name", "declaration_header", "imports")}
            if len(json.dumps(header_row).encode()) > 64 * 1024:
                raise ValueError("Header exceeds the preparation memory bound")
            buckets[row["family"]].append(header_row)
    for bucket in buckets.values():
        bucket.sort(key=lambda q: (len(q["declaration_header"]), q["theorem_id"]))
    families = sorted(buckets, key=lambda f: FAMILIES.index(f) if f in FAMILIES else len(FAMILIES))
    candidates = [bucket[i] for i in range(max(map(len, buckets.values())))
                  for family in families if i < len(bucket := buckets[family])]
    accepted = []
    compiled = 0
    with (args.output / "preparation.jsonl").open("x") as records:
        for row in candidates:
            header = row["declaration_header"]
            match = re.search(r"\b(?:lemma|theorem)\s+([^\s({:]+)", header)
            if match is None or not row["full_name"].endswith(match[1]):
                continue
            namespace = row["full_name"][:-len(match[1])].rstrip(".")
            imports = list(dict.fromkeys(module_name(p) for p in row["imports"]))
            roots = [args.environment / "mathlib/.lake/build/lib",
                     args.environment / "lean-4.10.0-rc1-linux/lib/lean"]
            roots.extend((args.environment / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
            unavailable = [p for p in imports if not any((root / (p.replace(".", "/") + ".olean")).exists() for root in roots)]
            if unavailable:
                records.write(json.dumps({"id": row["theorem_id"], "selection_status": "unavailable_imports", "imports": unavailable}) + "\n")
                continue
            if compiled == 120:
                break
            compiled += 1
            prefix = "".join("import " + p + "\n" for p in imports) + "import Lean.Elab.Tactic\nset_option autoImplicit false\n"
            if namespace:
                prefix += "namespace " + namespace + "\n"
            q = {"id": row["theorem_id"], "family": row["family"], "name": row["full_name"],
                 "header": header, "prefix": prefix, "closing": ["end " + namespace] if namespace else []}
            result = checker.lean_call(checker.source_for(q, "  trace_state\n  sorry"), q["id"])
            valid = result["exit_code"] == 0 and "TARGET_ABSENT" in result["output"] and "error:" not in result["output"]
            records.write(json.dumps({"id": q["id"], "valid": valid, **result}) + "\n")
            records.flush()
            print(json.dumps({"id": q["id"], "family": q["family"], "valid": valid}), flush=True)
            if verifier_failed(result):
                raise RuntimeError("Verifier infrastructure failure during preparation")
            if valid:
                q["initial_state"] = "\n".join(line for line in result["output"].split("TARGET_ABSENT", 1)[-1].splitlines()
                                              if ".lean:" not in line).strip()
                accepted.append(q)
            if len(accepted) == 16:
                break
    if len(accepted) < 8:
        raise RuntimeError("Insufficient standalone training contexts; no GPU launch")
    # Complete passes avoid a membership confound from truncating different orders.
    accepted = accepted[:16 if len(accepted) >= 16 else 8]
    validation = [q for q in checker.rows(args.validation) if q["group"] == "project_validation"][:8]
    train_ids = {q["id"] for q in accepted}
    if train_ids & {q["id"] for q in validation}:
        raise ValueError("Training and validation IDs overlap")
    for name, questions in (("train", accepted), ("validation", validation)):
        (args.output / (name + ".jsonl")).write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions))
    checker.save(args.output / "manifest.json", {
        "source_train_rows": source_count, "train_questions": len(accepted), "validation_questions": len(validation),
        "selection": "family-interleaved, shorter headers first, imports available, compile-valid and target absent; no model scores",
        "curriculum": "source-family heuristic; prerequisites and optimality unvalidated",
        "source_sha256": source_digest.hexdigest(),
        "lean": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5",
        "overlap_audit": "inherits frozen corpus exact and near-duplicate audit; no new theorem statements"})


if __name__ == "__main__":
    main()
