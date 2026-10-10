"""Number-theory theorems of the mathlib Archive at the pin, from files compiled at the pin.

Files: Archive/Imo/*.lean, Archive/Wiedijk100Theorems/*.lean, Archive/ZagierTwoSquares.lean. Each file is compiled with
lean on LEAN_PATH (no lake; `lean --json`, as check_batch of verify_external_proofs.py), from a copy with one
`#print axioms <full name>` line appended per theorem or lemma that declarations() of build_nt_knowledge.py finds. The
file builds when Lean returns and reports no error on the file's own lines; the appended lines only print. A theorem is
verified when its file builds and the axioms on its line (axiom_report of bench_lean_verifier.py: one message, naming
it) are within ALLOWED_AXIOMS. Time is the wall time of the one compile, appended lines included.
Number-theory content: the statement matches NUMBER_THEORY of profile_external_proofs.py (a text heuristic).
miniF2F overlap, per file (one file is one problem): the file name ImoYYYYQN matches a miniF2F name imo_YYYY_pN (an
optional _k suffix allowed), or a theorem statement of the file has token Jaccard >= --minif2f-jaccard to a miniF2F
statement. Every theorem of an overlapping file is dropped. Other leakage: check_leakage.py per theorem.
Kept: verified, number-theory content, not wrapped in an `... in` line, a proof start found, no FORBIDDEN construct of
probe_start_depth.py, no overlap, no leakage hit. A kept proof can use earlier lemmas of its file.
Output (a new directory): lean/ (compiled copies), files.jsonl (build status, seconds), theorems.jsonl (every theorem
with its flags), items.jsonl (kept: file, theorem, statement, proof, minif2f_overlap), summary.json.

Memory: one lean process per worker. `import Mathlib` peaked at 3.7 GB RSS, mostly shared olean pages
(probe_nt_pool.py). Budget 2 GiB private per process + 3.4 GB shared: 8 workers about 20 GiB; run under a 24 GiB cap.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval", "audit")]
from bench_lean_verifier import axiom_report  # noqa: E402
from build_nt_knowledge import declarations  # noqa: E402
from check_leakage import Leakage, statement_of  # noqa: E402
from profile_external_proofs import NUMBER_THEORY  # noqa: E402
from replay_training_proofs import ALLOWED_AXIOMS  # noqa: E402

PATTERNS = ("Archive/Imo/*.lean", "Archive/Wiedijk100Theorems/*.lean", "Archive/ZagierTwoSquares.lean")
IMO_FILE = re.compile(r"Imo(\d{4})Q(\d+)")
IMO_MINIF2F = re.compile(r"imo_(\d{4})_p(\d+)(?:_\d+)?")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compile_file(lean, env, mathlib, output, rel, timeout):
    """Build status, seconds, and the declarations of one Archive file with their axioms."""
    source = (mathlib / rel).read_text()
    decls = declarations(source)
    own = len(source.rstrip("\n").split("\n"))
    path = output / "lean" / rel.replace("/", "__")
    path.write_text(source.rstrip("\n") + "\n" + "".join(f"#print axioms {d['full_name']}\n" for d in decls))
    start = time.monotonic()
    try:
        result = subprocess.run([str(lean), "--json", str(path)], env=env, capture_output=True, text=True, timeout=timeout)
        messages = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        status = "returned" if result.returncode == 0 or (result.returncode == 1 and messages) else "crash"
    except subprocess.TimeoutExpired:
        messages, status = [], "timeout"
    seconds = time.monotonic() - start
    errors = [m["data"] for m in messages if m["severity"] == "error" and m["pos"]["line"] <= own]
    builds = status == "returned" and not errors
    info = {"messages": [dict(m, severity="info") for m in messages if m["severity"] == "information"]}
    for k, d in enumerate(decls):
        d["axioms"] = axiom_report(info, own + 1 + k, d["full_name"]) if builds else None
        d["verified"] = d["axioms"] is not None and set(d["axioms"]) <= ALLOWED_AXIOMS
    return {"file": rel, "lines": own, "status": status, "builds": builds, "errors": len(errors),
            "first_error": errors[0][:300] if errors else None, "seconds": round(seconds, 2), "theorems": len(decls)}, decls


def main():
    parser = argparse.ArgumentParser()
    for name in ("environment", "pool", "splits", "minif2f", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True, help="LeanDojo validation or test zip")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=1800, help="seconds per file")
    parser.add_argument("--jaccard", type=float, default=0.85)
    parser.add_argument("--minif2f-jaccard", type=float, default=0.7)
    parser.add_argument("--limit-files", type=int, help="first files only (smoke test)")
    args = parser.parse_args()
    started = time.monotonic()
    environment = args.environment.resolve()
    mathlib = environment / "mathlib"
    lean = environment / "lean-4.10.0-rc1-linux/bin/lean"
    libs = [mathlib / ".lake/build/lib"] + sorted((mathlib / ".lake/packages").glob("*/.lake/build/lib"))
    env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    files = sorted(str(p.relative_to(mathlib)) for pattern in PATTERNS for p in mathlib.glob(pattern))[:args.limit_files]
    leak = Leakage(args.pool, args.splits, args.eval, args.minif2f, args.jaccard, args.minif2f_jaccard)
    minif2f_imo = {(m[1], m[2]): r["name"] for r in map(json.loads, args.minif2f.open()) if (m := IMO_MINIF2F.fullmatch(r["name"]))}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    with ThreadPoolExecutor(args.workers) as threads:
        compiled = list(threads.map(lambda f: compile_file(lean, env, mathlib, args.output, f, args.timeout), files))

    counts, drops, theorems, items, overlaps, nt_by_file = Counter(), Counter(), [], [], {}, {}
    with (args.output / "files.jsonl").open("x") as out:
        for record, decls in compiled:
            imo = IMO_FILE.fullmatch(Path(record["file"]).stem)
            reasons = [f"name:{minif2f_imo[imo[1], imo[2]]}"] if imo and (imo[1], imo[2]) in minif2f_imo else []
            for d in decls:
                d["hits"] = leak.hits(statement_of(d["statement"]))
                reasons += [f"jaccard:{d['full_name']}~{h['name']}:{h['jaccard']}" for h in d["hits"] if h["target"] == "minif2f"]
            if reasons:
                overlaps[record["file"]] = reasons
            record["minif2f_overlap"] = reasons
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            for d in decls:
                nt = bool(NUMBER_THEORY.search(d["statement"]))
                other = [h for h in d["hits"] if h["target"] != "minif2f"]
                reason = ("not_verified" if not d["verified"] else "not_number_theory" if not nt
                          else "wrapped_in_command" if d["wrapped"] else "no_proof_start" if d["kind"] is None
                          else "forbidden_construct" if d["forbidden"] else "minif2f_overlap" if reasons
                          else "leakage_hit" if other else None)
                counts["theorems"] += 1
                counts["number_theory"] += nt
                counts["dropped_" + reason if reason else "kept"] += 1
                for h in other:
                    drops[h["target"]] += 1
                if nt:
                    nt_by_file.setdefault(record["file"], []).append(d["full_name"])
                theorems.append({"file": record["file"], "theorem": d["full_name"], "start": d["start"], "end": d["end"],
                                 "kind": d["kind"], "number_theory": nt, "axioms": d["axioms"], "verified": d["verified"],
                                 "minif2f_overlap": bool(reasons), "leakage_hits": d["hits"], "dropped": reason})
                if reason is None:
                    items.append({"file": record["file"], "theorem": d["full_name"], "statement": d["statement"],
                                  "proof": d["proof"], "kind": d["kind"], "docstring": d["docstring"],
                                  "start": d["start"], "end": d["end"], "minif2f_overlap": False})
    for name, rows in (("theorems.jsonl", theorems), ("items.jsonl", items)):
        with (args.output / name).open("x") as out:
            out.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    records = [r for r, _ in compiled]
    summary = {
        "files": len(files), "files_build": sum(r["builds"] for r in records),
        "files_not_building": {r["file"]: r["status"] + ": " + str(r["first_error"]) for r in records if not r["builds"]},
        "compile_seconds_total": round(sum(r["seconds"] for r in records), 1),
        "compile_seconds_max": max((r["seconds"], r["file"]) for r in records),
        "counts": dict(counts), "leakage_drops_by_target": dict(drops), "minif2f_overlap_files": overlaps,
        "number_theory_theorems_by_file": nt_by_file, "items": len(items),
        "items_by_file": dict(Counter(i["file"] for i in items)), "leakage_targets": leak.counts(),
        "wall_seconds": round(time.monotonic() - started, 1), "workers": args.workers, "timeout": args.timeout,
        "inputs": {f"{p.parent.name}/{p.name}": sha256(p) for p in (args.pool, args.minif2f, *args.eval)},
        "code": {Path(f).name: sha256(f) for f in (__file__, sys.modules["build_nt_knowledge"].__file__)},
        "lean_version": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}
    with (args.output / "summary.json").open("x") as out:
        out.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=1, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
