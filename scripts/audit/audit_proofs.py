"""Stream existing GRPO failures on cpu-angad; no model or Lean execution.

Memory: one JSON row <=1 MiB, 10 retained excerpts per phase <=50 KiB.
Use a 512 MiB service cap. Labels describe compiler evidence, not math knowledge.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


def labels(row, cap, eos):
    result = row["verification"]
    if result["accepted"]:
        return ["accepted"]
    output = result.get("output", "").lower()
    found = []
    if result["status"] == "forbidden_construct":
        found.append("forbidden_construct")
    elif result["status"] != "returned":
        found.append("verifier_infrastructure")
    else:
        if any(x in output for x in ("unexpected token", "expected token", "unexpected end", "expected tactic", "error: expected '")):
            found.append("syntax_error")
        if "unknown identifier" in output or "unknown constant" in output:
            found.append("unknown_symbol")
        if any(x in output for x in ("type mismatch", "application type mismatch", "tactic '")):
            found.append("type_or_tactic_error")
        if "unsolved goals" in output:
            found.append("proof_not_completed")
        if not found:
            found.append("other_verifier_rejection")
    ids = row.get("completion_ids", [])
    if row["generated_tokens"] >= cap and (not ids or ids[-1] not in eos):
        found.append("token_cap_reached")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--max-rows", type=int, default=1000)
    ap.add_argument("--max-tokens", type=int, default=256)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    summary = {"phases": {}, "label_policy": "overlapping compiler diagnostics; unknown symbols may be constants, tactics or local names; failed proofs do not establish incorrect natural-language reasoning"}
    files = ["baseline.jsonl", "random/rollouts.jsonl", "curriculum/rollouts.jsonl",
             "random_after.jsonl", "curriculum_after.jsonl"]
    total = 0
    with (args.output / "classified.jsonl").open("x") as out:
        for name in files:
            counts, digest, retained = Counter(), hashlib.sha256(), Counter()
            examples, n = [], 0
            with (args.source / name).open("rb") as source:
                for line in source:
                    if len(line) > 1024**2 or total >= args.max_rows:
                        raise ValueError("Audit input exceeds its registered bound")
                    digest.update(line)
                    row = json.loads(line)
                    tags = labels(row, args.max_tokens, {151643, 151645})
                    if len(row.get("completion_ids", [])) != row["generated_tokens"]:
                        raise ValueError("Token counter differs from tokenizer IDs")
                    record = {"source": name, "id": row["id"], "step": row["step"], "labels": tags,
                              "unknown_symbols": re.findall(r"unknown (?:identifier|constant) '([^']+)'", row["verification"].get("output", "")),
                              "response": row["response"], "compiler_output": row["verification"].get("output", "")}
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    counts.update(tags)
                    for tag in tags:
                        if retained[tag] < 2:
                            examples.append({"label": tag, "id": row["id"], "step": row["step"],
                                             "response": row["response"][:600], "diagnostic": record["compiler_output"][:1200]})
                            retained[tag] += 1
                    n += 1
                    total += 1
            summary["phases"][name] = {"attempts": n, "labels": dict(counts), "sha256": digest.hexdigest(), "examples": examples}
    contexts = []
    with args.questions.open() as source:
        for line in source:
            q = json.loads(line)
            markers = [x for x in ("TARGET_PRESENT", "TARGET_ABSENT", "run_tac", "Lean.getEnv", "Lean.logInfo") if x in q["initial_state"]]
            contexts.append({"id": q["id"], "internal_markers": markers})
    summary["prompt_contexts"] = contexts
    summary["total_attempts"] = total
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"attempts": total, "phases": {k: {"attempts": v["attempts"], "labels": v["labels"]} for k, v in summary["phases"].items()},
                      "contexts": len(contexts), "contexts_with_internal_markers": sum(bool(q["internal_markers"]) for q in contexts)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
