"""Re-score earlier accepted proofs with the current shared scorer and scan them for the checker holes of 2026-10-10.

Needs the pinned Lean environment and the REPL (Lean test group). Holes (check_checker_attacks.py): a forged axiom
report (trace or logInfo text with "depend"), a metaprogram (run_tac, by_elab, ...), and a second command after the
proof (an indented theorem, namespace, or @[command_elab]). Sources:
- --probe-dir: probe_nt_pool.py runs; every candidate with status "accepted" in checked.jsonl.
- --sft: build_nt_sft_data.py rows (train.jsonl, val.jsonl); every row (all were Lean-accepted).
- --proofs: verify_external_proofs.py results; every accepted proof whose body matches HOLE_TEXT (a superset of the
  text each hole needs). Bodies come from body_of against the pool header (rows whose statement differs are counted).
Every selected body is scored by verify_group under its pool header (pool rows from --pool, later files win), the
scorer of the map and the reward. Output: checked.jsonl (source, name, status) and summary.json (counts per source,
bodies no longer accepted, HOLE_TEXT matches per pattern). Exit code 0; the summary is the result.

Memory: --workers REPLs at once (3.7 GB RSS each measured, about 3.4 GB of it shared mathlib pages) + rows (<500 MiB).
Run under 4.5 GiB x --workers + 2 GiB.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from bench_lean_verifier import repl_context, verify_group  # noqa: E402
from build_nt_sft_data import body_of  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import header, lean_unit, read  # noqa: E402

HOLE_TEXT = re.compile(r"depend|run_tac|run_conv|by_elab|run_cmd|run_elab|run_meta|logInfo|\btrace\b|command_elab"
                       r"|^\s*(theorem|lemma|def|namespace|section|end|@\[|elab|macro|syntax|instance|example|abbrev|attribute)\b", re.M)


def main():
    parser = argparse.ArgumentParser()
    for name in ("checker", "environment", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--pool", type=Path, action="append", required=True, help="pool files; later files win")
    parser.add_argument("--probe-dir", type=Path, action="append", default=[])
    parser.add_argument("--sft", type=Path, action="append", default=[])
    parser.add_argument("--proofs", type=Path, action="append", default=[])
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--lean-timeout", type=float, default=20)
    parser.add_argument("--setup-timeout", type=float, default=120)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = {r["name"]: r for path in args.pool for r in map(json.loads, path.open())}
    items, counts, patterns = [], Counter(), Counter()
    for d in args.probe_dir:
        for c in read(d / "checked.jsonl"):
            if c["status"] == "accepted":
                items.append({"source": str(d.name), "name": c["name"], "body": c["continuation"]})
    for path in args.sft:
        for r in map(json.loads, path.open()):
            code = r["messages"][1]["content"].removeprefix("```lean\n").removesuffix("\n```")
            items.append({"source": "sft:" + path.parent.name + "/" + path.name, "name": r["theorem"],
                          "body": code.split(" := by\n", 1)[1]})
    for path in args.proofs:
        for r in map(json.loads, path.open()):
            if r["status"] != "accepted":
                continue
            counts["external_accepted"] += 1
            stmt = re.sub(r":=\s*(by\s*)?sorry\s*$", "", r["formal_statement"].strip()).rstrip()
            body = body_of(r["text"], stmt)
            text = body if body is not None else r["text"]
            found = {m.group(0).strip() for m in HOLE_TEXT.finditer(text)}
            patterns.update(found)
            if found:
                counts["external_hole_text_matches"] += 1
                if body is None or r["name"] not in rows:
                    counts["external_matches_not_rescorable"] += 1
                else:
                    items.append({"source": "external:" + path.parent.name, "name": r["name"], "body": body,
                                  "hole_text": sorted(found)})
    missing = sorted({i["name"] for i in items if i["name"] not in rows})
    items = [i for i in items if i["name"] in rows]
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)
    ctx = repl_context(checker, environment, args.repl, args.output, args.lean_timeout, args.setup_timeout, 400)
    groups = defaultdict(list)
    for i in items:
        groups[i["name"]].append(i)
    try:
        with ThreadPoolExecutor(args.workers) as pool, (args.output / "checked.jsonl").open("x") as out:
            for name, results in zip(groups, pool.map(lambda n: verify_group(ctx, lean_unit(rows[n]), [i["body"] for i in groups[n]]),
                                                      list(groups))):
                for item, result in zip(groups[name], results):
                    item.update(result)
                    out.write(json.dumps(item, ensure_ascii=False) + "\n")
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()
    by_source = defaultdict(Counter)
    for i in items:
        by_source[i["source"]][i["status"]] += 1
    summary = {"counts": dict(counts), "names_missing_from_pool": missing, "hole_text_patterns": dict(patterns),
               "rescored": len(items), "statuses_by_source": {k: dict(v) for k, v in sorted(by_source.items())},
               "no_longer_accepted": [{k: i.get(k) for k in ("source", "name", "status", "first_error", "hole_text")}
                                      for i in items if i["status"] != "accepted"]}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("counts", "rescored", "statuses_by_source", "hole_text_patterns")}, indent=1), flush=True)


if __name__ == "__main__":
    main()
