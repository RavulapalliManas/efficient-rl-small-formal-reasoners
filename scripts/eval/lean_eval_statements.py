"""Lean-checked whole-proof evaluation of frozen models on curriculum-validation theorems.

Validation exports carry no proofs. Each theorem is rebuilt from its source file up to the
declaration (file context), with every other evaluation declaration blanked. Lean prints the
initial goal; the prompt shows the statement and that goal. Two baselines need no model:
a fixed tactic list, and the empty proof. Training proofs and test theorems are not used.

Memory: one FP32 0.5B model (~2 GiB) + k x 1,280-token KV cache, plus one Lean process
capped at 2 GiB. Run under an 8 GiB service cap with 8 CPU threads.
"""
import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sys
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from prepare_grpo_pilot import load_checker  # noqa: E402
from replay_training_proofs import direct_lean_call, question  # noqa: E402
from build_proof_tasks import lean_json  # noqa: E402
from probe_start_depth import check  # noqa: E402

INSTRUCTION = ("Prove this Lean 4 theorem. The environment is Lean 4 with mathlib. The goal state is:\n\n{state}\n\n"
               "Write the tactic proof, indented by two spaces, then close the code block.")


def export(path):
    with zipfile.ZipFile(path) as archive:
        with archive.open(archive.namelist()[0]) as input_stream:
            return [json.loads(line) for line in input_stream]


def main():
    parser = argparse.ArgumentParser()
    for name in ("validation", "test", "checker", "environment", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model", action="append", default=[], help="label=path; repeat for each model")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20261009)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    checker = load_checker(args.checker, args.environment, args.output)
    checker.lean_call = direct_lean_call(checker, args.environment.resolve(), args.timeout)
    env_root = args.environment.resolve()
    libs = [env_root / "mathlib/.lake/build/lib"] + sorted((env_root / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    lean_env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    validation, test = export(args.validation), export(args.test)
    spans = defaultdict(list)
    for r in validation + test:
        spans[r["file_path"]].append((r["start"][0], r["end"][0], r["declaration_header"]))
    sources, tasks, counts = {}, [], Counter()
    with (args.output / "tasks.jsonl").open("x") as out:
        for row in validation:
            others = {p: [s for s in v if p != row["file_path"] or s[0] != row["start"][0]] for p, v in spans.items()}
            q, skip = question(row, args.environment.resolve(), sources, "file", others)
            record = {"theorem_id": row["theorem_id"], "full_name": row["full_name"], "family": row["family"], "status": skip}
            if q:
                # Read the goal from Lean's JSON messages at the trace_state line. Splitting the text output at
                # "TARGET_ABSENT" breaks where the unusedTactic linter echoes the guard code into a warning.
                source = checker.source_for(q, "  trace_state\n  sorry")
                state_line = source.split("\n").index("  trace_state") + 1
                probe = lean_json(checker.LEAN / "lean", lean_env, args.output / "lean" / (q["id"] + "_state.lean"), source, args.timeout)
                errors = [m["data"] for m in probe["messages"] if m["severity"] == "error"]
                infos = [m for m in probe["messages"] if m["severity"] == "information"]
                states = [m["data"] for m in infos if m["pos"]["line"] == state_line]
                ok = probe["status"] == "returned" and not errors and any(m["data"] == "TARGET_ABSENT" for m in infos) and len(states) == 1
                record["status"] = "ready" if ok else ("target_present" if any("TARGET_PRESENT" in e for e in errors) else "context_error")
                if ok:
                    state = states[0].strip()
                    # The no-model baseline is scripts/eval/single_tactic_baseline.py (one tactic per attempt);
                    # a single `first | ...` line fails to parse where a tactic is not imported.
                    empty = check(checker, q, [], "  skip", q["id"] + "_empty")
                    record.update(state=state, empty_proof=empty["status"])
                    tasks.append((row, q, state))
            counts[record["status"]] += 1
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
    with (args.output / "tasks.jsonl").open() as input_stream:
        task_rows = [json.loads(line) for line in input_stream]
    summary = {"validation_theorems": len(validation), "task_status": dict(counts),
               "empty_proof_accepted": sum(r.get("empty_proof") == "accepted" for r in task_rows), "models": {}}
    if args.model:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        torch.set_num_threads(args.threads)
        with (args.output / "samples.jsonl").open("x") as out:
            for spec in args.model:
                label, path = spec.split("=", 1)
                tokenizer = AutoTokenizer.from_pretrained(path)
                model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32).eval()
                torch.manual_seed(args.seed)
                solved, accepted, total = 0, 0, 0
                for row, q, state in tasks:
                    messages = [{"role": "user", "content": INSTRUCTION.format(state=state)}]
                    text = (tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                            + "```lean\n" + row["declaration_header"] + " := by\n")
                    # The prefill ends at a newline: pre-typed spaces split tokens unlike training and add an extra space.
                    inputs = tokenizer(text, return_tensors="pt")
                    start = time.monotonic()
                    with torch.no_grad():
                        generated = model.generate(**inputs, do_sample=True, temperature=args.temperature, top_p=args.top_p,
                                                   top_k=0, max_new_tokens=args.max_new_tokens,
                                                   num_return_sequences=args.samples, pad_token_id=tokenizer.eos_token_id)
                    seconds, hits = time.monotonic() - start, 0
                    for i, sequence in enumerate(generated):
                        ids = sequence[inputs["input_ids"].shape[1]:].tolist()
                        while ids and ids[-1] == tokenizer.eos_token_id:
                            ids.pop()
                        continuation = tokenizer.decode(ids, skip_special_tokens=True).split("```", 1)[0]
                        result = check(checker, q, [], continuation, f"{label}_{row['theorem_id'][:16]}_s{i}")
                        hits += result["status"] == "accepted"
                        out.write(json.dumps({"model": label, "theorem_id": row["theorem_id"], "sample": i,
                                              "generated_tokens": len(ids), "capped": len(ids) >= args.max_new_tokens,
                                              "generation_seconds_for_group": seconds, "continuation": continuation,
                                              **result}, ensure_ascii=False) + "\n")
                        out.flush()
                    solved += hits > 0
                    accepted += hits
                    total += len(generated)
                    print(json.dumps({"model": label, "id": row["theorem_id"][:12], "accepted": hits}), flush=True)
                summary["models"][label] = {"theorems": len(tasks), f"solved_at_{args.samples}": solved,
                                            "accepted_samples": accepted, "samples": total}
                del model
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
