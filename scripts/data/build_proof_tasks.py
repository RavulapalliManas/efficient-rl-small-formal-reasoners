"""Build Lean-checked proof tasks from the original mathlib proof text, with cut points.

For every training theorem with a tactic proof (linear or nested), this script
1. rebuilds the file context (source file up to the declaration; evaluation proofs become sorry),
2. replays the original tactic block verbatim with the pinned Lean,
3. splits an accepted block into top-level steps, and for each cut k checks the first k steps
   followed by `trace_state` and `all_goals sorry`; Lean's JSON messages give the goal state.
Training proofs only. Term-mode proofs are counted and skipped.

Memory: training rows <200 MiB, plus one Lean process capped at 2 GiB (-M2048). A declaration whose
context exceeds that cap is recorded as memory_limit and skipped; the RL checker has the same cap.
Run each shard under a 3 GiB service cap.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit")]
from replay_training_proofs import ALLOWED_AXIOMS, load, question  # noqa: E402

GUARD = ('  run_tac do\n    if (← Lean.getEnv).contains ({name}.toName) then\n'
         '      throwError "TARGET_PRESENT"\n    else\n      Lean.logInfo "TARGET_ABSENT"\n')
CONTINUATION = ("<;>", ")", "]", "}", "|", ".", ",", "_")


def tactic_block(row, source):
    """The original text after `:= by`, with the first tactic moved to its own line."""
    lines = source.split("\n")
    text = "\n".join(lines[row["start"][0] - 1:row["end"][0]])
    header = row["declaration_header"].split("\n")
    keyword = next(i for i, line in enumerate(header) if re.search(r"\b(?:lemma|theorem)\b", line))
    statement = "\n".join(header[keyword:])
    if not text.startswith(statement):
        return None
    match = re.match(r"\s*:=\s*by\b", text[len(statement):])
    if match is None:
        return None
    rest = text[len(statement) + match.end():].split("\n")
    body = (["  " + rest[0].strip()] if rest[0].strip() else []) + rest[1:]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body) if body else None


def top_level_steps(body):
    """Split a tactic block at lines with the block's own indentation."""
    lines = body.split("\n")
    indent = len(lines[0]) - len(lines[0].lstrip())
    steps = []
    for line in lines:
        stripped = line.strip()
        at_top = stripped and len(line) - len(line.lstrip()) == indent
        if steps and (not at_top or stripped.startswith(CONTINUATION) or stripped.startswith("--")):
            steps[-1].append(line)
        else:
            steps.append([line])
    return ["\n".join(s) for s in steps], " " * indent


def lean_json(lean, env, path, source, timeout):
    path.write_text(source)
    start = time.monotonic()
    try:
        result = subprocess.run([str(lean), "--json", "-j1", "-M2048", str(path)], env=env,
                                capture_output=True, text=True, timeout=timeout)
        messages = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        status = "returned"
        if "excessive memory consumption" in result.stderr:
            return {"status": "memory_limit", "messages": [], "wall_seconds": time.monotonic() - start}
        if result.returncode not in (0, 1) or (result.stderr.strip() and not messages):
            raise RuntimeError("Lean infrastructure failure: " + result.stderr[:300])
    except subprocess.TimeoutExpired:
        messages, status = [], "timeout"
    return {"status": status, "messages": messages, "wall_seconds": time.monotonic() - start}


def build_source(q, body, tail):
    text = q["prefix"] + q["header"] + " := by\n" + GUARD.format(name=json.dumps(q["name"])) + body + "\n" + tail
    return text + "\n".join(q["closing"]) + "\n#print axioms " + q["name"] + "\n"


def judge(result):
    msgs = result["messages"]
    errors = [m["data"] for m in msgs if m["severity"] == "error"]
    infos = [m["data"] for m in msgs if m["severity"] == "information"]
    axioms = None
    for data in infos:
        if "does not depend on any axioms" in data:
            axioms = []
        elif match := re.search(r"depends on axioms:\s*\[([^\]]*)\]", data, re.S):
            axioms = [x.strip() for x in match.group(1).split(",")]
    if result["status"] in ("timeout", "memory_limit"):
        status = result["status"]
    elif any("TARGET_PRESENT" in e for e in errors):
        status = "target_present"
    elif errors:
        status = "rejected"
    elif "TARGET_ABSENT" not in infos:
        status = "rejected"
    elif axioms is not None and "sorryAx" in axioms:
        status = "depends_on_sorry"
    elif axioms is not None and set(axioms) <= ALLOWED_AXIOMS:
        status = "accepted"
    else:
        status = "rejected"
    return status, errors, infos, axioms


def main():
    parser = argparse.ArgumentParser()
    for name in ("train", "environment", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--shard", default="0/1")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--resume", action="store_true", help="Append to an existing output and skip theorems already done")
    args = parser.parse_args()
    index, count = map(int, args.shard.split("/"))
    environment = args.environment.resolve()
    lean = environment / "lean-4.10.0-rc1-linux/bin/lean"
    libs = [environment / "mathlib/.lake/build/lib"] + sorted((environment / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    eval_spans = {}
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as input_stream:
                for line in input_stream:
                    r = json.loads(line)
                    eval_spans.setdefault(r["file_path"], []).append((r["start"][0], r["end"][0], r["declaration_header"]))
    rows = sorted((r for r in load(args.train) if r["traced_tactics"]), key=lambda r: r["theorem_id"])[index::count][:args.limit]
    done = set()
    if args.resume and (args.output / "tasks.jsonl").exists():
        with (args.output / "tasks.jsonl").open() as input_stream:
            done = {json.loads(line)["theorem_id"] for line in input_stream}
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "lean").mkdir()
    sources, statuses, cut_ok, cut_total = {}, Counter(), 0, 0
    with (args.output / "tasks.jsonl").open("a" if done else "x") as out:
        for row in rows:
            if row["theorem_id"] in done:
                continue
            record = {"theorem_id": row["theorem_id"], "full_name": row["full_name"], "family": row["family"],
                      "file_path": row["file_path"], "traced_tactics": len(row["traced_tactics"])}
            q, skip = question(row, environment, sources, "file", eval_spans)
            body = tactic_block(row, sources[environment / "mathlib" / row["file_path"]]) if q else None
            if q is None:
                record["status"] = skip
            elif body is None:
                record["status"] = "no_tactic_block"
            else:
                stem = args.output / "lean" / row["theorem_id"]
                result = lean_json(lean, env, stem.with_suffix(".lean"), build_source(q, body, ""), args.timeout)
                status, errors, _, axioms = judge(result)
                steps, indent = top_level_steps(body)
                record.update(status=status, axioms=axioms, first_error=(errors or [None])[0],
                              wall_seconds=result["wall_seconds"], steps=steps, cuts=[])
                if status == "accepted":
                    for k in range(1, len(steps)):
                        tail = indent + "trace_state\n" + indent + "all_goals sorry\n"
                        cut_source = build_source(q, "\n".join(steps[:k]), tail)
                        cut = lean_json(lean, env, Path(f"{stem}_cut{k}.lean"), cut_source, args.timeout)
                        state_line = cut_source.split("\n").index(indent + "trace_state") + 1
                        states = [m["data"] for m in cut["messages"]
                                  if m["severity"] == "information" and m["pos"]["line"] == state_line]
                        ok = cut["status"] == "returned" and not any(m["severity"] == "error" for m in cut["messages"]) and len(states) == 1
                        record["cuts"].append({"k": k, "ok": ok, "state": states[0] if ok else None,
                                               "wall_seconds": cut["wall_seconds"]})
                        cut_ok, cut_total = cut_ok + ok, cut_total + 1
            statuses[record["status"]] += 1
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            print(json.dumps({"id": row["theorem_id"][:12], "status": record["status"],
                              "steps": len(record.get("steps", [])), "cuts_ok": sum(c["ok"] for c in record.get("cuts", []))}), flush=True)
    summary = {"tactic_proofs_in_shard": len(rows), "shard": args.shard, "statuses": dict(statuses),
               "cut_points_checked": cut_total, "cut_points_ok": cut_ok, "timeout_seconds": args.timeout,
               "lean": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
