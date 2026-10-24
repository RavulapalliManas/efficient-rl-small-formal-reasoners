"""SFT rows for the rejection-sampling fine-tuning (RFT) baseline from Lean-accepted map samples (train split only).

Inputs: probe_nt_pool.py run directories (candidates.jsonl with generated_tokens and checked.jsonl with status, both
written by the map), the pool (build_nt_pool.py), and, for a map run with --tasks, the same tasks file. A sample enters
when Lean accepted it (status "accepted", scored by verify_group, the RL reward check), its arm is in --arms (default:
every arm except single_tactic), and its statement is in the pool's train split and not in --exclude-names (for
example the DEV slice). Rows are deduplicated per statement: one row per distinct proof body (given text + sampled
continuation, trailing whitespace removed) of a statement. A candidate of a validation, test, or unknown statement
stops the build: map outputs on held-out statements never become training data.
Format: as build_nt_sft_data.py "proof" rows. The user turn is the SFT proof instruction with the goal state (a task's
state for a task); the assistant turn is "```lean\n" + header + " := by\n" + given + continuation + "\n```", that is the
prefill the map prompted with plus the sampled continuation. 5% of statements (by name hash, as build_nt_sft_data.py)
go to val.jsonl for the eval loss.
Budget: samples are visited round-robin (sample 0 of every unit in file order, then sample 1, ...). With
--max-generated-tokens B, the build stops before the first sample that would take the generated tokens of all visited
samples (accepted or not) past B; only samples before it can become rows. Set B to the GRPO arm's cumulative
generated_tokens (steps.jsonl) to give RFT the same generation budget. manifest.json records the tokens used.

Memory: candidates and checks of the given directories (<1 GiB for 30k samples).
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval")]
from probe_nt_pool import INSTRUCTION, lean_unit, read, row_key  # noqa: E402


def rft_rows(candidates, checked, units, states, split, excluded, arms, max_tokens):
    """(train rows, val rows, counts). units maps a candidate name to its pool row or task; split maps statement names
    to splits; states maps candidate names to goal states."""
    outside = sorted({lean_unit(units[c["name"]])["name"] for c in candidates} - {n for n, s in split.items() if s == "train"})
    if outside:
        raise RuntimeError(f"{len(outside)} statements are not train statements, for example {outside[:3]}")
    status = {(c["arm"], c["name"], c["sample"]): c["status"] for c in checked}
    order = {name: i for i, name in enumerate(dict.fromkeys(c["name"] for c in candidates))}
    visit = sorted((c for c in candidates if arms is None and c["arm"] != "single_tactic" or arms and c["arm"] in arms),
                   key=lambda c: (c["sample"], order[c["name"]], c["arm"]))
    counts, seen, train, val, tokens = Counter(), set(), [], [], 0
    for c in visit:
        if max_tokens is not None and tokens + c["generated_tokens"] > max_tokens:
            counts["stopped_at_budget"] = 1
            break
        tokens += c["generated_tokens"]
        counts["samples_visited"] += 1
        if status.get((c["arm"], c["name"], c["sample"])) != "accepted":
            continue
        counts["accepted_visited"] += 1
        unit = lean_unit(units[c["name"]])
        name = unit["name"]
        if name in excluded:
            counts["skipped_excluded_name"] += 1
            continue
        body = (unit.get("given", "") + c["continuation"]).rstrip()
        if (name, body) in seen:
            counts["skipped_duplicate_body"] += 1
            continue
        seen.add((name, body))
        held = int(hashlib.sha256(name.encode()).hexdigest(), 16) % 100 < 5
        (val if held else train).append({
            "format": "proof", "theorem": name, "source": "rft:" + c["arm"], "task_id": unit["task_id"], "sample": c["sample"],
            "has_comments": "/-" in body or bool(re.search(r"^\s*--", body, re.M)),
            "messages": [{"role": "user", "content": INSTRUCTION.format(state=states[c["name"]])},
                         {"role": "assistant", "content": "```lean\n" + unit["header"] + " := by\n" + body + "\n```"}]})
    counts.update(rows_train=len(train), rows_val=len(val), statements=len({n for n, _ in seen}), generated_tokens_used=tokens)
    return train, val, counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-dir", type=Path, action="append", required=True, help="probe_nt_pool.py output directory; repeat")
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, help="tasks file, when the map ran with --tasks")
    parser.add_argument("--exclude-names", type=Path, help="statement names that must not become rows (for example DEV)")
    parser.add_argument("--arms", nargs="+", help="model arms to use (default: all but single_tactic)")
    parser.add_argument("--max-generated-tokens", type=int, help="generation budget (the GRPO arm's generated_tokens)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.pool.open() as input_stream:
        pool = [json.loads(line) for line in input_stream]
    task_rows = pool
    if args.tasks:
        with args.tasks.open() as input_stream:
            task_rows = [json.loads(line) for line in input_stream]
    units = {row_key(row): row for row in task_rows}
    excluded = set()
    if args.exclude_names:
        with args.exclude_names.open() as input_stream:
            excluded = {line.strip() for line in input_stream if line.strip()}
    candidates = [c for d in args.map_dir for c in read(d / "candidates.jsonl")]
    checked = [c for d in args.map_dir for c in read(d / "checked.jsonl")]
    states = {r["name"]: r["state"] for d in args.map_dir for r in read(d / "states.jsonl")}
    train, val, counts = rft_rows(candidates, checked, units, states, {r["name"]: r["split"] for r in pool}, excluded,
                                  set(args.arms) if args.arms else None, args.max_generated_tokens)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, rows in (("train.jsonl", train), ("val.jsonl", val)):
        with (args.output / name).open("x") as out:
            out.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    (args.output / "manifest.json").write_text(json.dumps({
        "counts": dict(counts), "map_dirs": [str(d) for d in args.map_dir], "pool": str(args.pool),
        "tasks": str(args.tasks), "exclude_names": str(args.exclude_names), "arms": args.arms,
        "max_generated_tokens": args.max_generated_tokens}, indent=2) + "\n")
    print(json.dumps(dict(counts), indent=1), flush=True)


if __name__ == "__main__":
    main()
