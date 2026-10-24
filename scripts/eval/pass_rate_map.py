"""Pass-rate map of one checkpoint over the task bank: a group of samples per unit, by steps remaining.

Units (optionally only --topic topics) are sampled per bucket with a fixed seed, in --buckets order, then sharded:
`whole` (k = 0, the whole proof) and `d1`, `d2`, `d3` (k >= 1 given steps, then 1, 2, or 3 steps remaining). Each unit's Lean file is rebuilt in file context, as when the
bank was verified (evaluation proofs become sorry). Prompts and scoring match run_lean_grpo.py: the SFT proof
instruction for `whole`, the probe_start_depth.py instruction after given steps; generation stops at the closing
``` with explicit sampling settings (--repetition-penalty included); verify_group in bench_lean_verifier.py checks
each group in one REPL with the unit's file prefix (--timeout seconds per candidate, as lean_timeout_seconds).
(Runs before 2026-10-10 used the continuation instruction for `whole`, no stop string, the checkpoint's
repetition penalty, and one `lean -M2048` subprocess per sample with a 120 s timeout.)
A group is "mixed" when 0 < accepted < samples. Only mixed groups give GRPO a nonzero advantage.

Memory: one FP32 0.5B model (~2 GiB) + k x 1,280-token KV cache, plus one REPL process per group (bank REPLs
peaked at 1.9 GiB RSS in bench_v1; the REPL has no -M cap). Run under a 6 GiB cap.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import sys
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from bench_lean_verifier import repl_context, verify_group  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import INSTRUCTION as PROOF_INSTRUCTION  # noqa: E402
from probe_start_depth import INSTRUCTION as CONTINUE_INSTRUCTION  # noqa: E402
from replay_training_proofs import load, proof_body, question  # noqa: E402


def bucket(unit):
    if unit["k"] == 0:
        return "whole"
    return f"d{unit['steps_remaining']}" if unit["steps_remaining"] <= 3 else None


def main():
    parser = argparse.ArgumentParser()
    for name in ("bank", "checker", "environment", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--train", type=Path, action="append", required=True, help="rows for question(); repeat")
    parser.add_argument("--eval", type=Path, action="append", required=True, help="evaluation exports; repeat")
    parser.add_argument("--model", required=True, help="label=path")
    parser.add_argument("--per-bucket", type=int, default=200)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--repetition-penalty", type=float, default=1.1,
                        help="1.1 is what generation inherited from the checkpoints before this flag existed")
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=20, help="seconds per candidate (lean_timeout_seconds)")
    parser.add_argument("--setup-timeout", type=float, default=120, help="seconds for the REPL file-prefix command")
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--shard", default="0/1")
    parser.add_argument("--buckets", default="whole,d1,d2,d3", help="buckets to sample, in this order")
    parser.add_argument("--topic", action="append", help="keep only units of these bank topics; repeat")
    args = parser.parse_args()
    index, count = map(int, args.shard.split("/"))
    args.output.mkdir(parents=True, exist_ok=False)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)
    ctx = repl_context(checker, environment, args.repl, args.output, args.timeout, args.setup_timeout, 10**9)
    rows = {r["theorem_id"]: r for path in args.train for r in load(path)}
    eval_spans = defaultdict(list)
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as input_stream:
                for line in input_stream:
                    r = json.loads(line)
                    eval_spans[r["file_path"]].append((r["start"][0], r["end"][0], r["declaration_header"]))
    ctx.rows, ctx.eval_spans = rows, eval_spans
    with args.bank.open() as input_stream:
        units = [dict(u, kind="bank") for u in map(json.loads, input_stream) if not args.topic or u["topic"] in args.topic]
    rng, sampled = random.Random(args.seed), []
    for name in args.buckets.split(","):
        pool = sorted((u for u in units if bucket(u) == name), key=lambda u: u["task_id"])
        rng.shuffle(pool)
        sampled.extend(pool[:args.per_bucket])
    shard = sampled[index::count]

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    torch.set_num_threads(args.threads)
    label, path = args.model.split("=", 1)
    tokenizer = AutoTokenizer.from_pretrained(path)
    model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32).eval()
    torch.manual_seed(args.seed + index)
    sources, stats, totals, started = ctx.sources, defaultdict(Counter), Counter(), time.monotonic()
    with (args.output / "samples.jsonl").open("x") as out:
        for unit in shard:
            name = bucket(unit)
            q, skip = question(rows[unit["theorem_id"]], environment, sources, "file", eval_spans)
            if q is None:
                stats[name]["skipped_" + skip] += 1
                continue
            given = [{"tactic": step[2:]} for step in unit["given_steps"]]  # bank steps carry a 2-space indent
            instruction = CONTINUE_INSTRUCTION if given else PROOF_INSTRUCTION  # as in run_lean_grpo.py
            text = (tokenizer.apply_chat_template([{"role": "user", "content": instruction.format(state=unit["state"])}],
                                                  tokenize=False, add_generation_prompt=True)
                    + "```lean\n" + q["header"] + " := by\n" + (proof_body(given) + "\n" if given else ""))
            inputs = tokenizer(text, return_tensors="pt")
            start = time.monotonic()
            with torch.no_grad():
                generated = model.generate(**inputs, do_sample=True, temperature=args.temperature, top_p=args.top_p,
                                           top_k=0, repetition_penalty=args.repetition_penalty,
                                           max_new_tokens=args.max_new_tokens, num_return_sequences=args.samples,
                                           pad_token_id=tokenizer.eos_token_id, stop_strings=["```"], tokenizer=tokenizer)
            seconds = time.monotonic() - start
            continuations, lengths = [], []
            for sequence in generated:
                ids = sequence[inputs["input_ids"].shape[1]:].tolist()
                while ids and ids[-1] == tokenizer.eos_token_id:
                    ids.pop()
                continuations.append(tokenizer.decode(ids, skip_special_tokens=True).split("```", 1)[0])
                lengths.append(len(ids))
            results = verify_group(ctx, unit, continuations)
            hits = sum(r["status"] == "accepted" for r in results)
            totals.update(generated_tokens=sum(lengths), generation_seconds=seconds, lean_calls=sum(r["lean_calls"] for r in results),
                          lean_seconds=sum(r["lean_seconds"] for r in results), lean_cpu_seconds=sum(r["lean_cpu_seconds"] for r in results))
            for i, result in enumerate(results):
                out.write(json.dumps({"model": label, "task_id": unit["task_id"], "full_name": unit["full_name"],
                                      "topic": unit["topic"], "bucket": name, "k": unit["k"],
                                      "steps_remaining": unit["steps_remaining"], "sample": i,
                                      "generated_tokens": lengths[i], "capped": lengths[i] >= args.max_new_tokens,
                                      "generation_seconds_for_group": seconds, "continuation": continuations[i],
                                      **result}, ensure_ascii=False) + "\n")
            out.flush()
            for key in (name, f"{name}/{unit['topic']}"):
                s = stats[key]
                s["groups"] += 1
                s["samples"] += len(results)
                s["accepted"] += hits
                s["groups_zero" if hits == 0 else "groups_all" if hits == len(results) else "groups_mixed"] += 1
            print(json.dumps({"task": unit["task_id"][:20], "bucket": name, "accepted": hits,
                              "seconds": round(seconds, 1)}), flush=True)
    summary = {"model": args.model, "shard": args.shard, "per_bucket": args.per_bucket, "samples": args.samples,
               "units_in_shard": len(shard), "repetition_penalty": args.repetition_penalty, "lean_timeout": args.timeout,
               "wall_seconds": time.monotonic() - started, **totals, "by": {k: dict(v) for k, v in sorted(stats.items())}}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary["by"].items() if "/" not in k}), flush=True)


if __name__ == "__main__":
    main()
