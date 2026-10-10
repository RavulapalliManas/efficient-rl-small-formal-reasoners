"""GRPO on Lean tasks with a Lean-verified binary reward, and cost records per optimizer step.

Reward (Lean-verified): 1 if the whole proof (the task's given steps, then the sampled steps) is accepted, else 0.
Lean scoring is verify_group in bench_lean_verifier.py, the same function the pass-rate maps (probe_nt_pool.py,
pass_rate_map.py) use: no FORBIDDEN match in the sampled text (rejected without a Lean call), then no error,
TARGET_ABSENT, and axioms within ALLOWED_AXIOMS, one leanprover-community/repl command per completion after the
prefix (repl-env mode; agreed with the subprocess check on 160 of 160 labels, rlvr_verifier/bench_v1). No partial
credit; nothing is heuristic. Control arm only: with shuffle_rewards true, the Lean rewards are permuted within each
group of num_generations completions (seeded by the config seed), so each group keeps its reward multiset, the reward
rate, and the share of mixed groups, while the link between a completion and its reward breaks. That reward is NOT
Lean-verified for the completion it is given; rollouts.jsonl keeps the true verdict ("status") next to it.

Tasks, from either or both sources:
- Task-bank units (task_bank_v2), optionally limited to --units, in file context. The run stops if a used unit lies
  in an evaluation file or in a file listed in --exclude-files (the development files).
- Number-theory pool statements (build_nt_pool.py), split train only, optionally limited to --pool-names, under the
  pool header of verify_external_proofs.py, with the trace_state goal of build_nt_states.py. The run stops if a
  name in --pool-names is not a train statement of the pool. As in probe_nt_pool.py, an empty continuation or a
  line that is not indented is rejected without Lean.
- RL tasks (--tasks, nt_rl_tasks tasks.jsonl: task_id, name, kind, header, given, state, ...), with --pool for the
  split check. The run stops if a task's name is not a train statement of the pool, if its header is not the pool
  statement's header, or if its given text has a FORBIDDEN match. Lean checks the pool header + header + " := by\n" +
  given + the completion; the prefill is "```lean\n" + header + " := by\n" + given (lean_unit and prompt of
  probe_nt_pool.py, which the pass-rate map also uses).
The run also stops if a pool statement or task name is listed in --exclude-names (for example the DEV slice).
Prompts match the probes that measured the pass rates: pool statements and RL tasks use the SFT proof instruction
(probe_nt_pool.py, lean_eval_statements.py); bank continuations after given steps use the instruction of
probe_start_depth.py. Tasks whose
prompt exceeds max_prompt_tokens are dropped and counted, because TRL would truncate the prefilled proof.
Sampling: temperature, top_p, top_k, and repetition_penalty come from the config, for the HF and the vLLM path alike.
With use_vllm, TRL runs vLLM colocated on the training GPU and generation stops at the closing ``` (as in the
probe); without it, HF generation runs to EOS and the reward cuts the text at the fence.

Records in --output: run.json, rollouts.jsonl (one line per completion), steps.jsonl (per optimizer step, cumulative
since the start: generated tokens, Lean calls, Lean wall and CPU seconds, forbidden, timed-out, and capped samples,
groups with 0, some, or all accepted; plus wall seconds, GPU seconds = wall x GPUs, and TRL's logs). The run stops
after the first optimizer step at which max_wall_seconds, max_generated_tokens, or max_lean_calls is reached.

Memory: one 0.5B policy in FP32 (~2 GiB) + AdamW state (~4 GiB) + activations; a reference model only if beta > 0;
with vLLM, vllm_gpu_memory_utilization of the GPU for weights and KV cache.
Lean: up to verifier_workers REPL processes at a time. A bank REPL serves one unit for one step, then closes; it
peaked at about 1.9 GiB RSS (bench_v1). A pool REPL holds all of mathlib (3.7 GB peak for one lean process,
probe_nt_pool.py) and lives per worker thread until repl_restart_after commands. The REPL has no -M cap: run under a
cgroup cap of at least verifier_workers x 4 GiB plus the policy.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import sys
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from bench_lean_verifier import repl_context, verify_group  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import INSTRUCTION as PROOF_INSTRUCTION, header, lean_unit, prompt  # noqa: E402
from probe_start_depth import FORBIDDEN, INSTRUCTION as CONTINUE_INSTRUCTION  # noqa: E402
from replay_training_proofs import load, proof_body  # noqa: E402


def train_statements(pool, names=None):
    """Pool rows of split train, or only `names`; raises if a name is not a train statement of the pool."""
    if names is None:
        return [r for r in pool if r["split"] == "train"]
    split = {r["name"]: r["split"] for r in pool}
    outside = sorted(n for n in names if split.get(n) != "train")
    if outside:  # validation and test statements stay out of training, prompts, and adaptive sampling
        raise RuntimeError(f"{len(outside)} names are not train statements of the pool, for example {outside[:3]}")
    return [r for r in pool if r["name"] in names]


def task_units(tasks, pool):
    """verify_group units (with goal state) of RL tasks; raises if a name is not a train statement of the pool, a header
    is not the pool statement's header, a given text has a FORBIDDEN match, or a task_id repeats."""
    rows = {r["name"]: r for r in train_statements(pool, {t["name"] for t in tasks})}
    bad = sorted(t["task_id"] for t in tasks if t["header"] != header(rows[t["name"]]) or FORBIDDEN.search(t["given"]))
    if bad:
        raise RuntimeError(f"{len(bad)} tasks differ from the pool statement or have a FORBIDDEN given text, for example {bad[:3]}")
    if len({t["task_id"] for t in tasks}) != len(tasks):
        raise RuntimeError("task_id values repeat")
    return [dict(lean_unit(t), state=t["state"]) for t in tasks]


def excluded_names(units, excluded):
    """Statement names of the units that appear in `excluded`."""
    return sorted({u["name"] for u in units if u.get("name") in excluded})


def shuffle_within_groups(rewards, size, rng):
    """Shuffled-reward control: the rewards permuted within each consecutive group of `size` (TRL's GRPO groups)."""
    if len(rewards) % size:
        raise ValueError(f"{len(rewards)} rewards do not split into groups of {size}")
    shuffled = []
    for start in range(0, len(rewards), size):
        group = list(rewards[start:start + size])
        rng.shuffle(group)
        shuffled += group
    return shuffled


def prompt_text(tokenizer, unit):
    """Pool statements and RL tasks: prompt() of probe_nt_pool.py. Bank units: the probe_start_depth.py instruction."""
    if unit["kind"] == "statement":
        return prompt(tokenizer, unit, unit["state"])
    given = [{"tactic": step[2:]} for step in unit["given_steps"]]
    instruction = CONTINUE_INSTRUCTION if given else PROOF_INSTRUCTION
    return (tokenizer.apply_chat_template([{"role": "user", "content": instruction.format(state=unit["state"])}],
                                          tokenize=False, add_generation_prompt=True)
            + "```lean\n" + unit["header"] + " := by\n" + (proof_body(given) + "\n" if given else ""))


def over_budget(counts, wall_seconds, config):
    """True once the run has used its wall-time, generated-token, or Lean-call budget."""
    return (wall_seconds >= config["max_wall_seconds"] or counts["generated_tokens"] >= config["max_generated_tokens"]
            or counts["lean_calls"] >= config["max_lean_calls"])


def score(ctx, by_id, task_ids, completions, executor):
    """Completion texts cut at the closing fence, and their Lean verdicts in completion order (one group per task)."""
    groups = {}
    for i, tid in enumerate(task_ids):
        groups.setdefault(tid, []).append(i)
    texts = [c.split("```", 1)[0] for c in completions]
    results = [None] * len(completions)
    verdicts = executor.map(lambda tid: verify_group(ctx, by_id[tid], [texts[i] for i in groups[tid]]), groups)
    for tid, group in zip(groups, verdicts):
        for i, result in zip(groups[tid], group):
            results[i] = result
    return texts, groups, results


def main():
    parser = argparse.ArgumentParser()
    for name in ("config", "checker", "environment", "repl", "output"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True, help="evaluation exports; repeat")
    parser.add_argument("--model", type=Path, required=True, help="starting checkpoint")
    parser.add_argument("--bank", type=Path, help="task bank; needs --train and --exclude-files")
    parser.add_argument("--train", type=Path, action="append", default=[], help="rows for question(); repeat")
    parser.add_argument("--exclude-files", type=Path, help="development files, one mathlib path per line")
    parser.add_argument("--units", type=Path, help="optional file of bank task_ids, one per line")
    parser.add_argument("--pool", type=Path, help="number-theory statement pool (build_nt_pool.py); split train only")
    parser.add_argument("--pool-states", type=Path, help="states.jsonl of build_nt_states.py for the pool")
    parser.add_argument("--pool-names", type=Path, help="optional file of pool names, one per line")
    parser.add_argument("--tasks", type=Path, help="RL tasks (nt_rl_tasks tasks.jsonl); needs --pool for the split check")
    parser.add_argument("--exclude-names", type=Path, help="optional file of statement names that must not be used")
    args = parser.parse_args()
    if (args.bank and not (args.train and args.exclude_files) or (args.pool_states or args.tasks) and not args.pool
            or not (args.bank or args.pool_states or args.tasks)):
        parser.error("give --bank with --train and --exclude-files, and/or --pool with --pool-states and/or --tasks")
    config = json.loads(args.config.read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)
    ctx = repl_context(checker, environment, args.repl, args.output, config["lean_timeout_seconds"],
                       config["lean_setup_timeout_seconds"], config["repl_restart_after"])
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            for line in archive.open(archive.namelist()[0]):
                r = json.loads(line)
                ctx.eval_spans.setdefault(r["file_path"], []).append((r["start"][0], r["end"][0], r["declaration_header"]))
    units, sources_info = [], {}
    if args.bank:
        excluded = {line.strip() for line in args.exclude_files.open() if line.strip()}
        bank = [dict(json.loads(line), kind="bank") for line in args.bank.open()]
        if args.units:
            wanted = {line.strip() for line in args.units.open() if line.strip()}
            bank = [u for u in bank if u["task_id"] in wanted]
        leaks = [u["task_id"] for u in bank if u["file_path"] in excluded or u["file_path"] in ctx.eval_spans]
        if leaks:
            raise RuntimeError(f"{len(leaks)} units lie in evaluation or excluded files, for example {leaks[:3]}")
        theorems = {u["theorem_id"] for u in bank}
        ctx.rows = {r["theorem_id"]: r for path in args.train for r in load(path) if r["theorem_id"] in theorems}
        units += bank
        sources_info.update(bank=str(args.bank), bank_sha256=hashlib.sha256(args.bank.read_bytes()).hexdigest(),
                            bank_units=len(bank), excluded_files=len(excluded))
    pool = [json.loads(line) for line in args.pool.open()] if args.pool else []
    if args.pool:
        sources_info.update(pool=str(args.pool), pool_sha256=hashlib.sha256(args.pool.read_bytes()).hexdigest())
    if args.pool_states:
        states = {r["name"]: r["state"] for r in map(json.loads, args.pool_states.open())}
        names = {line.strip() for line in args.pool_names.open() if line.strip()} if args.pool_names else None
        statements = train_statements(pool, names)
        kept = [r for r in statements if states.get(r["name"])]
        units += [dict(lean_unit(r), state=states[r["name"]]) for r in kept]
        sources_info.update(pool_statements=len(kept), pool_without_state=len(statements) - len(kept))
    if args.tasks:
        tasks = [json.loads(line) for line in args.tasks.open()]
        units += task_units(tasks, pool)
        sources_info.update(tasks=str(args.tasks), tasks_sha256=hashlib.sha256(args.tasks.read_bytes()).hexdigest(),
                            task_kinds={k: sum(t.get("kind") == k for t in tasks) for k in sorted({t.get("kind") for t in tasks})})
    if args.exclude_names:
        excluded = {line.strip() for line in args.exclude_names.open() if line.strip()}
        hits = excluded_names(units, excluded)
        if hits:
            raise RuntimeError(f"{len(hits)} names are in --exclude-names, for example {hits[:3]}")
        sources_info.update(exclude_names=str(args.exclude_names), excluded_names=len(excluded))
    if len({u["task_id"] for u in units}) != len(units):
        raise RuntimeError("task_id values repeat across task sources")

    import torch
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback
    from trl import GRPOConfig, GRPOTrainer
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    rows, dropped = [], 0
    for u in units:
        text = prompt_text(tokenizer, u)  # as in the probes that measured pass rates
        if len(tokenizer(text, add_special_tokens=False)["input_ids"]) > config["max_prompt_tokens"]:
            dropped += 1
            continue
        rows.append({"prompt": text, "task_id": u["task_id"]})
    random.Random(config["seed"]).shuffle(rows)
    needed = config["max_steps"] * config["prompts_per_step"]
    dataset = Dataset.from_list([rows[i % len(rows)] for i in range(needed)])
    by_id = {u["task_id"]: u for u in units}
    run = {"config": config, "model": str(args.model), **sources_info, "units": len(units),
           "units_with_prompts": len(rows), "dropped_long_prompts": dropped,
           "lean": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5", "repl": str(ctx.repl),
           "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu", "status": "running"}
    (args.output / "run.json").write_text(json.dumps(run, indent=2) + "\n")

    counts = {"generated_tokens": 0, "lean_calls": 0, "lean_seconds": 0.0, "lean_cpu_seconds": 0.0, "samples": 0,
              "accepted": 0, "forbidden": 0, "timeouts": 0, "capped": 0, "groups_zero": 0, "groups_mixed": 0, "groups_all": 0}
    rollouts = (args.output / "rollouts.jsonl").open("x")
    executor = ThreadPoolExecutor(config["verifier_workers"])
    gpus = torch.cuda.device_count() if torch.cuda.is_available() else 0
    # Shuffled-reward control (critique of 2026-10-10, section 5): Lean still checks every completion, so the run spends
    # the same Lean calls and the record keeps the true verdicts; only the reward each completion receives is permuted.
    shuffle = config["shuffle_rewards"]
    reward_rng = random.Random(config["seed"])
    started = time.monotonic()

    def lean_reward(prompts, completions, completion_ids, task_id, trainer_state, **kwargs):
        texts, groups, verdicts = score(ctx, by_id, task_id, completions, executor)
        rewards = [float(v["status"] == "accepted") for v in verdicts]
        if shuffle:
            size = config["num_generations"]
            if any(len(set(task_id[i:i + size])) != 1 for i in range(0, len(task_id), size)):
                raise RuntimeError("completions do not come in consecutive groups of num_generations per task")
            rewards = shuffle_within_groups(rewards, size, reward_rng)
        for tid, indices in groups.items():
            hits = 0
            for i in indices:
                result = verdicts[i]
                ids = list(completion_ids[i])
                tokens = len(ids) - (1 if ids and ids[-1] == tokenizer.eos_token_id else 0)
                accepted = result["status"] == "accepted"
                hits += accepted
                for key in ("lean_calls", "lean_seconds", "lean_cpu_seconds"):
                    counts[key] += result[key]
                counts["generated_tokens"] += tokens
                counts["capped"] += tokens >= config["max_completion_tokens"]
                counts["samples"] += 1
                counts["forbidden"] += result["status"] == "forbidden_construct"
                counts["timeouts"] += result["status"] in ("timeout", "crash")
                rollouts.write(json.dumps({"step": trainer_state.global_step, "task_id": tid, "text": texts[i],
                                           "generated_tokens": tokens, "reward": rewards[i], **result}, ensure_ascii=False) + "\n")
            counts["accepted"] += hits
            counts["groups_zero" if hits == 0 else "groups_all" if hits == len(indices) else "groups_mixed"] += 1
        rollouts.flush()
        return rewards

    class Records(TrainerCallback):
        def __init__(self):
            self.file = (args.output / "steps.jsonl").open("x")

        def on_log(self, _args, state, control, logs=None, **kwargs):
            wall = time.monotonic() - started
            record = {"step": state.global_step, "wall_seconds": wall, "gpu_seconds": wall * gpus, **counts, **(logs or {})}
            self.file.write(json.dumps(record) + "\n")
            self.file.flush()
            print(json.dumps(record), flush=True)
            if over_budget(counts, wall, config):  # checked after each optimizer step: a run overshoots by at most one step
                control.should_training_stop = True

    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32)
    # transformers replaces GRPOConfig sampling values that equal its global defaults (for example repetition_penalty
    # 1.0 or top_p 1.0) with the checkpoint's generation_config (1.1, 0.8); vLLM does not. Set them in both places.
    model.generation_config.update(temperature=config["temperature"], top_p=config["top_p"], top_k=config["top_k"],
                                   repetition_penalty=config["repetition_penalty"])
    vllm = {"vllm_mode": "colocate", "vllm_gpu_memory_utilization": config["vllm_gpu_memory_utilization"],
            "generation_kwargs": {"stop": ["```"]}} if config["use_vllm"] else {}  # vLLM stops at the closing fence
    settings = GRPOConfig(
        output_dir=str(args.output), max_steps=config["max_steps"], num_generations=config["num_generations"],
        per_device_train_batch_size=config["num_generations"], gradient_accumulation_steps=config["prompts_per_step"],
        num_iterations=1, shuffle_dataset=False, seed=config["seed"], data_seed=config["seed"],
        learning_rate=config["learning_rate"], lr_scheduler_type="constant", weight_decay=0.0, bf16=config["bf16"],
        gradient_checkpointing=config["gradient_checkpointing"], max_prompt_length=config["max_prompt_tokens"],
        max_completion_length=config["max_completion_tokens"], temperature=config["temperature"], top_p=config["top_p"],
        top_k=config["top_k"], repetition_penalty=config["repetition_penalty"], beta=config["beta"],
        loss_type=config["loss_type"], scale_rewards="group", use_vllm=config["use_vllm"], **vllm,
        logging_steps=1, save_steps=config["save_steps"], save_total_limit=config["save_total_limit"], report_to="none",
        remove_unused_columns=False)
    trainer = GRPOTrainer(model=model, args=settings, train_dataset=dataset, processing_class=tokenizer,
                          reward_funcs=lean_reward, callbacks=[Records()])
    try:
        trainer.train()
        trainer.save_model(str(args.output / "final"))
        run.update(status="complete", optimizer_steps=trainer.state.global_step)
    except BaseException as error:
        run.update(status="failed", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        wall = time.monotonic() - started
        run.update(wall_seconds=wall, gpu_seconds=wall * gpus, counts=counts)
        (args.output / "run.json").write_text(json.dumps(run, indent=2) + "\n")
        rollouts.close()
        executor.shutdown()
        for repl in list(ctx.pool_repls):
            repl.close()


if __name__ == "__main__":
    main()
