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
    main()
