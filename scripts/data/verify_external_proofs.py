"""Check Lean Workbook statements and proofs (Lean Workbook and Goedel-Prover releases) at our pinned Lean.

Sources: Lean Workbook (Apache-2.0, built on Lean v4.8.0-rc1) and Goedel-LM/Lean-workbook-proofs (MIT).
Each file holds up to --batch declarations after one `import Mathlib`, so mathlib loads once per file.
A declaration passes when no error falls inside its lines and `#print axioms` lists only propext,
Classical.choice, and Quot.sound. `set_option maxHeartbeats 0` becomes 400000. Inside each declaration's own
span come a SENTINEL command and its `#print axioms`: a declaration cut off after `by` or `·` makes Lean report the
parse error at the next token, which is then the sentinel, not the next declaration (before 2026-10-10 it fell on
the next declaration and rejected it, and all `#print axioms` lines stood at the end of the file). An unterminated
block comment still swallows the rest of the file, so every later declaration loses its `#print axioms` output and
is rejected. Model-proof scoring uses the REPL path instead (verify_group in bench_lean_verifier.py), where each
proof is its own command.
Modes: proofs (Goedel full proofs, Lean Workbook proofs) and statements (statement + sorry; elaboration only).

Memory: one Lean process loads all of mathlib (measured peak 3.7 GB RSS); run each shard under a 6 GiB cap.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import time

HEADER = "import Mathlib\nimport Aesop\nset_option maxHeartbeats 400000\nopen BigOperators Real Nat Topology Rat\n"
ALLOWED = {"propext", "Classical.choice", "Quot.sound"}
SENTINEL = "example : True := True.intro"
NUMBER_THEORY = re.compile(r"Nat\.Prime|∣|% |Nat\.gcd|Int\.gcd|ZMod|\[MOD|\[ZMOD|Int\.emod|Nat\.digits|Nat\.totient|Nat\.factorization")


def items(workbook, goedel, mode, number_theory_only):
    rows = json.load(open(workbook))
    by_name = {}
    for r in rows:
        match = re.search(r"theorem\s+(\S+)", r["formal_statement"])
        if match and (not number_theory_only or "number_theory" in (r.get("tags") or []) or NUMBER_THEORY.search(r["formal_statement"])):
            by_name[match[1]] = r
    out = []
    statement = {name: re.sub(r":=\s*(by\s*)?sorry\s*$", "", r["formal_statement"].rstrip()).rstrip() for name, r in by_name.items()}
    if mode == "statements":
        for name in by_name:
            out.append({"name": name, "source": "lean_workbook", "text": statement[name] + " := by sorry"})
        return out, by_name
    import pyarrow.parquet as pq
    for row in pq.read_table(goedel).to_pylist():
        if row["problem_id"] in by_name:
            text = row["full_proof"].split("open BigOperators Real Nat Topology Rat", 1)[-1]
            out.append({"name": row["problem_id"], "source": "goedel", "text": text.replace("set_option maxHeartbeats 0", "")})
    for name, r in by_name.items():
        for i, proof in enumerate((r.get("proof") or [])[:2]):  # at most two Lean Workbook proofs per problem
            body = proof if "theorem" in proof else statement[name] + " := by\n" + "\n".join("  " + line for line in proof.split("\n"))
            out.append({"name": name, "source": f"lean_workbook_proof{i}", "text": body})
    return out, by_name


def check_batch(lean, env, path, batch, timeout):
    lines, spans, axiom_line = HEADER.split("\n"), [], {}
    for k, item in enumerate(batch):
        # Give every declaration a unique name so duplicates of one problem can share a file.
        text = re.sub(r"theorem\s+(\S+)", f"theorem check_{k}", item["text"], count=1)
        start = len(lines) + 1
        lines.extend(text.split("\n") + [SENTINEL, f"#print axioms check_{k}"])
        spans.append((start, len(lines)))
        axiom_line[len(lines)] = k
    path.write_text("\n".join(lines) + "\n")
    start_time = time.monotonic()
    try:
        result = subprocess.run([str(lean), "--json", str(path)], env=env, capture_output=True, text=True, timeout=timeout)
        messages = [json.loads(l) for l in result.stdout.splitlines() if l.startswith("{")]
    except subprocess.TimeoutExpired:
        return [{"status": "batch_timeout"} for _ in batch], time.monotonic() - start_time
    status = []
    for k, (a, b) in enumerate(spans):
        errors = [m["data"] for m in messages if m["severity"] == "error" and a <= m["pos"]["line"] <= b]
        axioms = None
        for m in messages:
            if m["severity"] == "information" and axiom_line.get(m["pos"]["line"]) == k:
                found = re.search(r"depends on axioms:\s*\[([^\]]*)\]", m["data"], re.S)
                axioms = [x.strip() for x in found.group(1).split(",")] if found else ([] if "does not depend" in m["data"] else None)
        ok = not errors and axioms is not None and set(axioms) <= ALLOWED
        status.append({"status": "accepted" if ok else ("sorry_only" if not errors and axioms and set(axioms) - ALLOWED == {"sorryAx"} else "rejected"),
                       "first_error": errors[0][:300] if errors else None, "axioms": axioms})
    return status, time.monotonic() - start_time


def main():
    parser = argparse.ArgumentParser()
    for name in ("workbook", "goedel", "environment", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--mode", choices=("proofs", "statements"), default="proofs")
    parser.add_argument("--all-topics", action="store_true", help="Do not restrict to number theory")
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--shard", default="0/1")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    index, count = map(int, args.shard.split("/"))
    env_root = args.environment.resolve()
    lean = env_root / "lean-4.10.0-rc1-linux/bin/lean"
    libs = [env_root / "mathlib/.lake/build/lib"] + sorted((env_root / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    work, by_name = items(args.workbook, args.goedel, args.mode, not args.all_topics)
    work = work[index::count]
    args.output.mkdir(parents=True, exist_ok=False)
    counts = Counter()
    with (args.output / "results.jsonl").open("x") as out:
        for b in range(0, len(work), args.batch):
            batch = work[b:b + args.batch]
            results, seconds = check_batch(lean, env, args.output / "batch.lean", batch, args.timeout)
            for item, result in zip(batch, results):
                r = by_name[item["name"]]
                record = {"name": item["name"], "source": item["source"], "tags": r.get("tags"), "answer": r.get("answer"),
                          "natural_language_statement": r["natural_language_statement"], "formal_statement": r["formal_statement"],
                          "text": item["text"], **result}
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                counts[(item["source"].split("_proof")[0], result["status"])] += 1
            out.flush()
            print(json.dumps({"done": b + len(batch), "of": len(work), "batch_seconds": round(seconds, 1)}), flush=True)
    summary = {"mode": args.mode, "items": len(work), "shard": args.shard,
               "counts": {f"{k[0]}:{k[1]}": v for k, v in sorted(counts.items())},
               "lean": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
