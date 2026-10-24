"""Pass rates of frozen models and a single-tactic baseline on sampled statements of the number-theory pool.

Statements come from build_nt_pool.py (one split, sampled with a fixed seed; --shard i/n takes every n-th).
Lean prints each initial goal (`trace_state` in batch files after `import Mathlib`); the prompt and prefill
match lean_eval_statements.py. Each model writes --samples whole proofs per statement; generation stops at the
closing ``` (as before, the continuation is cut there). The baseline tries each tactic in TACTICS alone.
Sampling settings are explicit, including --repetition-penalty: the HF path would otherwise inherit 1.1 from the
checkpoint's generation_config, and vLLM would use 1.0.
Candidates are scored by verify_group in bench_lean_verifier.py, the function run_lean_grpo.py uses for its reward:
FORBIDDEN, empty, or non-indented candidates are rejected without Lean; every other candidate is one REPL command
after the pool header (no error, axioms within propext, Classical.choice, Quot.sound), so a truncated candidate
cannot change the verdict of another. --check-workers threads each keep one warm REPL.
(Runs before 2026-10-10 used batch files of 50 declarations, where a candidate that ends in `by`, a dangling `·`,
or an open block comment made the next declaration fail: engineering audit, 2026-10-10.)

Every stage appends to its own file, so --resume continues a stopped run: states.jsonl, candidates.jsonl
(with tokens and group generation seconds), checked.jsonl (with Lean calls and seconds per candidate),
runs.jsonl (wall seconds, device, and settings per invocation; GPU seconds = wall of --vllm invocations).
--merge DIR ... writes statements.jsonl and summary.json for several shard directories.
--tasks scores RL tasks (nt_rl_tasks tasks.jsonl: task_id, name, header, given, state) instead of pool statements,
every --shard i/n task, keyed by task_id: the prompt ends with the prefill "```lean\n" + header + " := by\n" + given,
and Lean checks the pool header + header + " := by\n" + given + the continuation. lean_unit and prompt are the
functions run_lean_grpo.py uses, so the map and RL score a task identically.
--vllm generates on a GPU with vLLM (same prompt, sampling settings, and stop string; float32 like the CPU
path), --vllm-chunk statements per call so a stopped run resumes; generation_seconds is a call's time divided by
its statements. --no-check stops after generation (for a GPU machine); a later --resume run on the CPU
machine checks the candidates.

Memory: one FP32 0.5B model (~2 GiB) + samples x (prompt + max-new-tokens) KV cache, plus one Lean process that
loads all of mathlib (3.7 GB peak measured) per check worker (and one for states). Run each shard under a cap of
8 GiB, or 4.5 GiB x (--check-workers + 1) when that is larger.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval")]
from verify_external_proofs import HEADER  # noqa: E402
from bench_lean_verifier import repl_context, verify_group  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402

# Same text as INSTRUCTION in lean_eval_statements.py and PROOF in build_sft_data.py.
INSTRUCTION = ("Prove this Lean 4 theorem. The environment is Lean 4 with mathlib. The goal state is:\n\n{state}\n\n"
               "Write the tactic proof, indented by two spaces, then close the code block.")
TACTICS = ["rfl", "decide", "norm_num", "simp", "omega", "linarith", "aesop", "simp_all", "tauto"]
SETTINGS = ("count", "seed", "samples", "max_new_tokens", "temperature", "top_p", "repetition_penalty", "vllm_dtype",
            "lean_timeout", "check_workers", "split", "prompt_format")
# Kimina-Prover-Distill prompt from its model card (revision 332e8a5259d1bdfda19d7c7f339f30804813cd3a).
KIMINA_SYSTEM = "You are an expert in mathematics and Lean 4."
KIMINA_USER = ("Think about and solve the following problem step by step in Lean 4.\n# Problem:{problem}\n"
               "# Formal statement:\n```lean4\n{formal}\n```\n")


def header(row):
    return re.sub(r":=\s*(by\s*)?sorry\s*$", "", row["formal_statement"].strip()).rstrip()


def row_key(row):
    """Candidates and states are keyed by task_id for an RL task and by name for a pool statement."""
    return row.get("task_id", row["name"])


def lean_unit(row):
    """verify_group unit of a pool statement, or of an RL task (has "given"): Lean checks the pool header + header +
    " := by\n" + given + the continuation."""
    if "given" in row:
        return {"task_id": row["task_id"], "kind": "statement", "name": row["name"], "header": row["header"],
                "given": row["given"], "given_steps": []}
    return {"task_id": "pool:" + row["name"], "kind": "statement", "name": row["name"], "header": header(row), "given_steps": []}


def prompt(tokenizer, unit, state):
    """The SFT proof instruction with the goal state, then the prefill "```lean\n" + header + " := by\n" + the unit's
    given text (none for a whole statement). The prefill ends at a newline: pre-typed spaces split tokens unlike
    training and add an extra space."""
    messages = [{"role": "user", "content": INSTRUCTION.format(state=state)}]
    return (tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            + "```lean\n" + unit["header"] + " := by\n" + unit.get("given", ""))


def kimina_prompt(tokenizer, unit):
    """The card's system and user messages; the formal statement follows the checking header and ends at
    ':= by'. These theorems have no informal problem text, so the problem field stays empty."""
    formal = HEADER + "\n" + unit["header"] + " := by"
    messages = [{"role": "system", "content": KIMINA_SYSTEM},
                {"role": "user", "content": KIMINA_USER.format(problem="", formal=formal)}]
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def kimina_continuation(text, header):
    """Proof body for `header` from the last closed Lean fence whose theorem matches it, else "".

    The model restates the theorem after its reasoning. A changed statement, a missing or unclosed fence
    (a capped output) gives "", which the checker rejects. Only the body is checked, under our header."""
    from build_nt_sft_data import body_of  # build_nt_sft_data imports this module, so import here.
    for block in reversed(re.findall(r"```lean4?[ \t]*\n(.*?)```", text, re.S)):
        body = body_of(re.sub(r"(^|\n)(\s*)lemma\s", r"\1\2theorem ", block), header)
        if body:
            return body
    return ""


def read(path):
    if not path.exists():
        return []
    with path.open() as source:
        return [json.loads(line) for line in source]


def append(path, rows):
    with path.open("a") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


def states(lean, env, path, rows, timeout):
    """Initial goal per row from trace_state, or None where the file errs inside the declaration."""
    lines, marks = HEADER.split("\n"), []
    for k, row in enumerate(rows):
        text = re.sub(r"theorem\s+(\S+)", f"theorem state_{k}", header(row), count=1) + " := by"
        start = len(lines) + 1
        lines.extend(text.split("\n") + ["  trace_state", "  sorry"])
        marks.append((start, len(lines) - 1, len(lines)))
    path.write_text("\n".join(lines) + "\n")
    result = subprocess.run([str(lean), "--json", str(path)], env=env, capture_output=True, text=True, timeout=timeout)
    messages = [json.loads(l) for l in result.stdout.splitlines() if l.startswith("{")]
    out = []
    for start, trace, end in marks:
        errors = [m for m in messages if m["severity"] == "error" and start <= m["pos"]["line"] <= end]
        found = [m["data"] for m in messages if m["severity"] == "information" and m["pos"]["line"] == trace]
        out.append(found[0].strip() if not errors and len(found) == 1 else None)
    return out


def generate_vllm(args, label, path, pending, goal, out):
    from vllm import LLM, SamplingParams
    llm = LLM(model=path, dtype=args.vllm_dtype, seed=args.seed)
    tokenizer = llm.get_tokenizer()
    # No per-request seed: vLLM gives sample i of every request seed + i, which would share draws across statements.
    kimina = args.prompt_format == "kimina"
    params = SamplingParams(n=args.samples, temperature=args.temperature, top_p=args.top_p, top_k=0,
                            repetition_penalty=args.repetition_penalty, max_tokens=args.max_new_tokens,
                            stop=None if kimina else ["```"])
    for c in range(0, len(pending), args.vllm_chunk):
        chunk = pending[c:c + args.vllm_chunk]
        prompts = [kimina_prompt(tokenizer, lean_unit(r)) if kimina else prompt(tokenizer, lean_unit(r), goal[row_key(r)])
                   for r in chunk]
        start = time.monotonic()
        outputs = llm.generate(prompts, params)
        seconds = (time.monotonic() - start) / len(chunk)
        for row, result in zip(chunk, outputs):
            group = []
            for i, o in enumerate(result.outputs):
                continuation = (kimina_continuation(o.text, lean_unit(row)["header"]) if kimina
                                else o.text.split("```", 1)[0].rstrip())
                record = {"arm": label, "name": row_key(row), "sample": i, "continuation": continuation,
                          "generated_tokens": len(o.token_ids), "capped": o.finish_reason == "length",
                          "generation_seconds": seconds}
                if kimina:  # Keep the end of the raw output for audit; the full reasoning is not stored.
                    record.update(extracted=bool(continuation), output_tail=o.text[-3000:])
                group.append(record)
            append(out / "candidates.jsonl", group)
        print(json.dumps({"arm": label, "generated": c + len(chunk), "of": len(pending),
                          "seconds_per_statement": round(seconds, 3)}), flush=True)
    del llm


def summarize(dirs, output):
    goal = {r["name"]: r["state"] for d in dirs for r in read(d / "states.jsonl")}
    candidates = [c for d in dirs for c in read(d / "candidates.jsonl")]
    checked = {(c["arm"], c["name"], c["sample"]): c for d in dirs for c in read(d / "checked.jsonl")}
    batches = [b for d in dirs for b in read(d / "batches.jsonl")]  # Lean batch files of runs before 2026-10-10
    runs = [r for d in dirs for r in read(d / "runs.jsonl")]
    status = {k: c["status"] for k, c in checked.items()}
    groups = defaultdict(Counter)
    for c in candidates:
        groups[c["arm"], c["name"]]["n"] += 1
        groups[c["arm"], c["name"]]["accepted"] += status.get((c["arm"], c["name"], c["sample"])) == "accepted"
    arms = sorted({c["arm"] for c in candidates})
    ready = sorted(n for n, s in goal.items() if s)
    with (output / "statements.jsonl").open("w") as out:
        for name in ready:
            row = {"name": name, "state": goal[name]}
            row.update({a: groups[a, name]["accepted"] for a in arms if (a, name) in groups})
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {"statements": len(goal), "ready": len(ready), "no_state": len(goal) - len(ready),
               "candidates": len(candidates), "checked_or_rejected": len(checked),
               "lean_batch_files": len(batches), "lean_calls": sum(c.get("lean_calls", 0) for c in checked.values()),
               "lean_check_seconds": sum(b["seconds"] for b in batches) + sum(c.get("lean_seconds", 0.0) for c in checked.values()),
               "lean_cpu_seconds": sum(c.get("lean_cpu_seconds", 0.0) for c in checked.values()),
               "invocations": len(runs), "wall_seconds": sum(r["wall_seconds"] for r in runs),
               "gpu_seconds": sum(r["wall_seconds"] for r in runs if r["vllm"]), "arms": {}}
    for a in arms:
        per = [g for (arm, _), g in groups.items() if arm == a]
        rows = [c for c in candidates if c["arm"] == a]
        summary["arms"][a] = {"statements": len(per), "solved": sum(g["accepted"] > 0 for g in per),
                              "accepted": sum(g["accepted"] for g in per), "candidates": len(rows),
                              "mixed_groups (0 < accepted < n)": sum(0 < g["accepted"] < g["n"] for g in per),
                              "generated_tokens": sum(c["generated_tokens"] for c in rows),
                              "generation_seconds": sum(c.get("generation_seconds", 0.0) for c in rows if c["sample"] == 0),
                              "capped": sum(bool(c.get("capped")) for c in rows),
                              "statuses": dict(Counter(status.get((a, c["name"], c["sample"]), "unchecked") for c in rows))}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--merge", type=Path, nargs="+", help="shard directories to summarize into --output")
    parser.add_argument("--pool", type=Path)
    parser.add_argument("--tasks", type=Path, help="RL tasks (nt_rl_tasks tasks.jsonl) to score instead of pool statements")
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--model", action="append", default=[], help="label=path; repeat for each model")
    parser.add_argument("--split", default="train")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--shard", default="0/1")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--repetition-penalty", type=float, default=1.1,
                        help="1.1 is what the HF path inherited from the checkpoints before this flag existed")
    parser.add_argument("--batch", type=int, default=50, help="statements per Lean file for the initial states")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds per Lean states file")
    parser.add_argument("--checker", type=Path, help="checker module (source_for, guard), as for run_lean_grpo.py")
    parser.add_argument("--repl", type=Path, help="leanprover-community/repl binary, as for run_lean_grpo.py")
    parser.add_argument("--lean-timeout", type=float, default=20, help="seconds per candidate (lean_timeout_seconds)")
    parser.add_argument("--setup-timeout", type=float, default=120, help="seconds for the REPL header command")
    parser.add_argument("--repl-restart-after", type=int, default=400)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--vllm", action="store_true", help="generate with vLLM on a GPU")
    parser.add_argument("--vllm-dtype", default="float32")
    parser.add_argument("--vllm-chunk", type=int, default=500, help="statements per vLLM call")
    parser.add_argument("--no-check", action="store_true", help="stop after generation")
    parser.add_argument("--check-workers", type=int, default=1, help="Lean REPL threads")
    parser.add_argument("--prompt-format", choices=("sft", "kimina"), default="sft",
                        help="kimina: the Kimina-Prover card prompt, no stop string, proof taken from the last Lean fence")
    args = parser.parse_args()
    if args.prompt_format == "kimina" and not args.vllm:
        parser.error("--prompt-format kimina needs --vllm")
    if not args.merge and not args.no_check and not (args.checker and args.repl):
        parser.error("checking needs --checker and --repl (or give --no-check)")
    if not args.merge and not (args.pool or args.tasks):
        parser.error("give --pool or --tasks")
    started = time.monotonic()
    if args.merge:
        args.output.mkdir(parents=True, exist_ok=False)
        summarize(args.merge, args.output)
        return
    index, count = map(int, args.shard.split("/"))
    env_root = args.environment.resolve()
    lean = env_root / "lean-4.10.0-rc1-linux/bin/lean"
    libs = [env_root / "mathlib/.lake/build/lib"] + sorted((env_root / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    if args.tasks:
        pool = [json.loads(l) for l in args.tasks.open()]
        rows = pool[index::count]
    else:
        pool = [json.loads(l) for l in args.pool.open()]
        rows = random.Random(args.seed).sample([r for r in pool if r["split"] == args.split], args.count)[index::count]
    out = args.output
    out.mkdir(parents=True, exist_ok=args.resume)
    goal = {r["name"]: r["state"] for r in read(out / "states.jsonl")}
    todo = [r for r in rows if row_key(r) not in goal]
    if args.tasks:  # a task carries its goal state
        append(out / "states.jsonl", [{"name": row_key(r), "state": r["state"]} for r in todo])
        goal.update((row_key(r), r["state"]) for r in todo)
        todo = []
    for b in range(0, len(todo), args.batch):
        batch = todo[b:b + args.batch]
        found = states(lean, env, out / "states.lean", batch, args.timeout)
        append(out / "states.jsonl", [{"name": r["name"], "state": s} for r, s in zip(batch, found)])
        goal.update((r["name"], s) for r, s in zip(batch, found))
    ready = [r for r in rows if goal[row_key(r)]]
    done = {(c["arm"], c["name"]) for c in read(out / "candidates.jsonl")}
    append(out / "candidates.jsonl", [{"arm": "single_tactic", "name": row_key(r), "sample": i, "continuation": "  " + t,
                                       "generated_tokens": 0} for r in ready if ("single_tactic", row_key(r)) not in done
                                      for i, t in enumerate(TACTICS)])
    for spec in args.model:
        label, path = spec.split("=", 1)
        pending = [r for r in ready if (label, row_key(r)) not in done]
        if not pending:
            continue
        if args.vllm:
            generate_vllm(args, label, path, pending, goal, out)
            continue
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        torch.set_num_threads(args.threads)
        tokenizer = AutoTokenizer.from_pretrained(path)
        model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32).eval()
        torch.manual_seed(args.seed + index)
        for k, row in enumerate(pending):
            text = prompt(tokenizer, lean_unit(row), goal[row_key(row)])
            inputs = tokenizer(text, return_tensors="pt")
            start = time.monotonic()
            with torch.no_grad():
                generated = model.generate(**inputs, do_sample=True, temperature=args.temperature, top_p=args.top_p,
                                           top_k=0, repetition_penalty=args.repetition_penalty,
                                           max_new_tokens=args.max_new_tokens, num_return_sequences=args.samples,
                                           pad_token_id=tokenizer.eos_token_id, stop_strings=["```"], tokenizer=tokenizer)
            seconds, group = time.monotonic() - start, []
            for i, sequence in enumerate(generated):
                ids = sequence[inputs["input_ids"].shape[1]:].tolist()
                while ids and ids[-1] == tokenizer.eos_token_id:
                    ids.pop()
                continuation = tokenizer.decode(ids, skip_special_tokens=True).split("```", 1)[0].rstrip()
                group.append({"arm": label, "name": row_key(row), "sample": i, "continuation": continuation,
                              "generated_tokens": len(ids), "capped": len(ids) >= args.max_new_tokens,
                              "generation_seconds": seconds})
            append(out / "candidates.jsonl", group)
            print(json.dumps({"arm": label, "generated": k + 1, "of": len(pending), "seconds": round(seconds, 1)}), flush=True)
        del model
    if not args.no_check:
        checker = load_checker(args.checker, env_root, out)
        ctx = repl_context(checker, env_root, args.repl, out, args.lean_timeout, args.setup_timeout, args.repl_restart_after)
        check_candidates(ctx, {row_key(r): r for r in pool}, out, args.check_workers)
    device = "cpu"
    if args.vllm:
        import torch
        device = torch.cuda.get_device_name(0)
    append(out / "runs.jsonl", [{"wall_seconds": time.monotonic() - started, "vllm": args.vllm, "device": device,
                                 "models": args.model, "shard": args.shard, "no_check": args.no_check,
                                 "pool": str(args.pool), "tasks": str(args.tasks), "settings": {k: getattr(args, k) for k in SETTINGS}}])
    if not args.no_check:
        summarize([out], out)


def check_candidates(ctx, rows, out, workers):
    """Score every unchecked candidate in out/candidates.jsonl with verify_group, one group per call. `rows` maps a
    candidate's name to its pool row or RL task (lean_unit)."""
    checked = {(c["arm"], c["name"], c["sample"]) for c in read(out / "checked.jsonl")}
    groups = defaultdict(list)
    for c in read(out / "candidates.jsonl"):
        if (c["arm"], c["name"], c["sample"]) not in checked:
            groups[c["arm"], c["name"]].append(c)

    def run(key):
        return verify_group(ctx, lean_unit(rows[key[1]]), [c["continuation"] for c in groups[key]])

    done_count, total = 0, sum(map(len, groups.values()))
    try:
        with ThreadPoolExecutor(workers) as pool:
            # Results come back in group order and only this thread writes the output.
            for key, results in zip(groups, pool.map(run, list(groups))):
                append(out / "checked.jsonl", [dict(c, **r) for c, r in zip(groups[key], results)])
                done_count += len(results)
                print(json.dumps({"checked": done_count, "of": total, "lean_seconds": round(sum(r["lean_seconds"] for r in results), 2)}), flush=True)
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()


if __name__ == "__main__":
    main()
