"""Thin TRL GRPO wrapper for the separate whole-proof pilot, one L40S.

Memory bound: 0.5B parameters x (2-byte weights + 2-byte gradients +
16-byte optimizer reserve) < 10 GB; one training microbatch's logits
<= 2304 x 151936 x 4 bytes < 1.5 GB. CUDA allocator cap: 24 GiB.
Four rollout KV caches <= 4 x 24 x 2 x 2 x 64 x 2304 x 2 bytes.
Host service cap: 16 GiB. One Lean process at a time, <=2 GiB.
No interactive tactics, custom optimizer, or supervised loss. Completion mode
supplies training-proof prefixes as prompt tokens, outside the policy loss.
"""
import argparse
from collections import Counter
import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import signal
import shutil
import time

from prepare_grpo_pilot import load_checker, ordered_ids, verifier_failed


def assistance_at(config, index, size):
    levels = config.get("assistance_passes")
    return levels[index // size] if levels else "none"


def prompt(q, assistance="none"):
    support = ""
    if assistance == "full":
        support = ("\nDefinitions:\n" + q.get("definitions", "") + "\nRelevant lemma statements:\n"
                   + "\n".join(q.get("support_statements", [])) + "\nGuidance:\n" + q.get("guidance", ""))
    elif assistance == "names":
        support = "\nRelevant lemma names:\n" + "\n".join(q.get("support_names", []))
    elif assistance != "none":
        raise ValueError("Unknown assistance level: " + assistance)
    return [
        {"role": "system", "content": "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."},
        {"role": "user", "content": (
            "Complete this Lean 4 proof. Return only a fenced Lean code block containing the tactic body, "
            "without the leading by. Do not use sorry, admit, axioms, native_decide, or change the statement.\n"
            f"Namespace: {q['name'].rsplit('.', 1)[0] if '.' in q['name'] else '(root)'}\n"
            f"Declaration:\n{q['header']} := by\nInitial goal:\n{q['initial_state']}" + support)},
    ]


def completion_prompt(q, level="body", assistance="full", variant="prefilled"):
    if level != "body" and q.get("split", "train") != "train":
        raise ValueError("Evaluation proofs cannot supply completion assistance")
    messages = prompt(q, assistance)
    if variant == "existing":
        if level != "body":
            raise ValueError("The existing prompt has no supplied proof prefix")
        return messages
    prefix = "" if level == "body" else q["completion_prefixes"][level]
    messages.append({"role": "assistant", "content": "```lean\n" + prefix})
    return messages


def next_completion_stage(solved_by_arm, current, threshold):
    if set(solved_by_arm) != {"random", "curriculum"}:
        raise ValueError("Both validation results are required")
    return min(current + 1, 2) if min(solved_by_arm.values()) >= threshold else current


def main():
    parser = argparse.ArgumentParser()
    for name in ("config", "data", "checker", "environment", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config.get("operator_study"):
        from local_operator_training import run
        return run(args, config)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    started = time.monotonic()
    counts = dict(generated_tokens=0, physical_lean_calls=0, rollout_attempts=0)
    run = {"config": config, "training": True, "counts": counts, "arms": {}, "status": "starting"}
    def save_run():
        run["wall_seconds"] = time.monotonic() - started
        temporary = args.output / "run.json.tmp"
        temporary.write_text(json.dumps(run, indent=2) + "\n")
        temporary.replace(args.output / "run.json")
    def stop(signum, frame):
        raise TimeoutError("Registered pilot wall cap reached")
    signal.signal(signal.SIGALRM, stop)
    signal.alarm(config["max_wall_seconds"])
    checker = load_checker(args.checker, args.environment, args.output)
    physical_call = checker.lean_call
    def bounded_call(source, stem):
        if counts["physical_lean_calls"] >= config["max_physical_lean_calls"]:
            raise RuntimeError("Registered physical Lean-call cap reached")
        counts["physical_lean_calls"] += 1
        result = physical_call(source, stem)
        if verifier_failed(result):
            save_run()
            raise RuntimeError("Verifier infrastructure failure: " + stem)
        return result
    checker.lean_call = bounded_call
    def reserve_generation(n):
        if counts["generated_tokens"] + n * config["max_completion_tokens"] > config["max_generated_tokens"]:
            raise RuntimeError("Remaining token budget cannot cover a complete generation group")
    def record_attempt(q, text, token_ids, phase, step, out, assistance="none", supplied_prefix="", prompt_tokens=None):
        counts["generated_tokens"] += len(token_ids)
        counts["rollout_attempts"] += 1
        stem = "attempt_" + str(counts["rollout_attempts"])
        # The opening fence belongs to the supplied prompt, not the action.
        candidate = text[len("```lean\n"):].split("```", 1)[0] if supplied_prefix else text
        result = checker.check_candidate(q, candidate, stem)
        if verifier_failed(result):
            raise RuntimeError("Verifier infrastructure failure: " + stem)
        row = {"id": q["id"], "phase": phase, "step": step, "response": text, "assistance": assistance,
               "completion_ids": token_ids, "generated_tokens": len(token_ids), "supplied_prefix": supplied_prefix,
               "prompt_tokens": prompt_tokens,
               "reward_component": "terminal_proof", "reward_source": "Lean-verified",
               "reward": int(result["accepted"]), "verification": result, "counts": dict(counts)}
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()
        save_run()
        return row["reward"]
    try:
        questions = checker.rows(args.data / "train.jsonl")
        validation = checker.rows(args.data / "validation.jsonl")
        levels = config.get("assistance_passes")
        readiness_levels = config.get("readiness_assistance", ["full"])
        if levels and config["max_steps_per_arm"] != len(questions) * len(levels):
            raise ValueError("Assistance schedule must cover complete theorem passes")
        lookup = {q["id"]: q for q in questions}
        orders = {arm: ordered_ids(questions, arm, config["max_steps_per_arm"], config["seed"]) for arm in config["arms"]}
        if Counter(orders["random"]) != Counter(orders["curriculum"]):
            raise RuntimeError("The arms have different theorem occurrences")
        run["data_manifest"] = json.loads((args.data / "manifest.json").read_text())
        run["data_sha256"] = {name: hashlib.sha256((args.data / name).read_bytes()).hexdigest()
                              for name in ("train.jsonl", "validation.jsonl")}
        if config.get("completion_study"):
            if any("completion_prefixes" in q for q in validation):
                raise ValueError("Evaluation contains supplied solution steps")
            groups = (4 * len(questions) + len(validation)
                      + 2 * config["completion_rounds"] * len(questions)
                      + 2 * config["completion_rounds"] * sum(q["split"] == "validation" for q in validation)
                      + 2 * sum(q["split"] == "heldout" for q in validation))
        else:
            groups = (len(config["arms"]) * config["max_steps_per_arm"] + (len(config["arms"]) + 1) * len(validation)
                      + (len(questions) * len(readiness_levels) if config.get("readiness_probe") else 0))
        run["maximum_generated_tokens"] = groups * config["num_generations"] * config["max_completion_tokens"]
        run["maximum_physical_lean_calls"] = groups * config["num_generations"] + len(questions) + len(validation) + 2
        if run["maximum_generated_tokens"] > config["max_generated_tokens"] or run["maximum_physical_lean_calls"] > config["max_physical_lean_calls"]:
            raise RuntimeError("Registered caps cannot cover the pilot")
        run["lean"] = "v4.10.0-rc1"
        run["mathlib"] = "29dcec074de168ac2bf835a77ef68bbe069194c5"
        run["planned_device"] = "one L40S, BF16, full parameters"
        (args.output / "orders.json").write_text(json.dumps(orders, indent=2) + "\n")
        if args.dry_run:
            run["training"] = False
            run["status"] = "dry_run_only"
            print(json.dumps(run, indent=2), flush=True)
            return
        import torch
        from datasets import Dataset
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback, set_seed
        from trl import GRPOConfig, GRPOTrainer
        if importlib.metadata.version("trl") != config["trl_version"]:
            raise RuntimeError("TRL version differs from registration")
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError("This runner requires one CUDA GPU")
        torch.cuda.set_per_process_memory_fraction(config["cuda_memory_gib"] * 1024**3 / torch.cuda.get_device_properties(0).total_memory)
        run["versions"] = {p: importlib.metadata.version(p) for p in ("torch", "transformers", "trl", "datasets", "accelerate")}
        tokenizer = AutoTokenizer.from_pretrained(config["checkpoint"], revision=config["revision"])
        tokenizer.padding_side = "left"
        for q in questions + validation:
            for level in (levels or ["none"]) if q in questions else ["none"]:
                tokens = tokenizer.apply_chat_template(prompt(q, level), tokenize=True, add_generation_prompt=True)
                if len(tokens) > config["max_prompt_tokens"]:
                    raise RuntimeError("Prompt exceeds registered cap: " + q["id"])
            initial = checker.lean_call(checker.source_for(q, "  trace_state\n  sorry"), "initial_" + q["id"])
            if initial["exit_code"] != 0 or "TARGET_ABSENT" not in initial["output"]:
                raise RuntimeError("Invalid original theorem context: " + q["id"])
        control = {"id": "grpo_control", "name": "grpo_control", "prefix": "import Mathlib.Algebra.Order.Ring.Nat\n",
                   "header": "theorem grpo_control (n : Nat) : n + 0 = n", "closing": []}
        good = checker.check_candidate(control, "rfl", "control_correct")
        bad = checker.check_candidate(dict(control, header="theorem grpo_control : (0 : Nat) = 1"), "rfl", "control_incorrect")
        checker.save(args.output / "controls.json", {"correct": good, "incorrect": bad})
        if not good["accepted"] or bad["accepted"]:
            raise RuntimeError("Checker controls failed")
        def load_model(path=None):
            set_seed(config["seed"])
            return AutoModelForCausalLM.from_pretrained(path or config["checkpoint"], revision=None if path else config["revision"],
                torch_dtype=torch.bfloat16, attn_implementation="sdpa").to("cuda")
        def evaluate(model, phase, pool=None, assistance="none", completion_level=None, variant="prefilled", details=False):
            model.eval()
            model.config.use_cache = True
            with (args.output / (phase + ".jsonl")).open("x") as out:
                mixed = 0
                by_id = {}
                for q in validation if pool is None else pool:
                    rewards = []
                    messages = prompt(q, assistance) if completion_level is None else completion_prompt(q, completion_level, assistance, variant)
                    prefilled = messages[-1]["role"] == "assistant"
                    supplied = messages[-1]["content"] if prefilled else ""
                    inputs = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=not prefilled,
                        continue_final_message=prefilled,
                        return_tensors="pt", return_dict=True).to("cuda")
                    if inputs["input_ids"].shape[1] > config["max_prompt_tokens"]:
                        raise RuntimeError("Completion prompt exceeds registered cap")
                    for attempt in range(config["num_generations"]):
                        reserve_generation(1)
                        set_seed(config["seed"] + attempt)
                        torch.cuda.synchronize()
                        began = time.monotonic()
                        with torch.inference_mode():
                            generated = model.generate(**inputs, max_new_tokens=config["max_completion_tokens"],
                                do_sample=True, temperature=config["temperature"], top_p=config["top_p"],
                                top_k=config["top_k"],
                                pad_token_id=tokenizer.pad_token_id)
                        torch.cuda.synchronize()
                        ids = generated[0, inputs["input_ids"].shape[1]:].tolist()
                        generation_seconds = time.monotonic() - began
                        rewards.append(record_attempt(q, supplied + tokenizer.decode(ids, skip_special_tokens=True), ids,
                                                      phase, attempt, out, assistance, supplied, inputs["input_ids"].shape[1]))
                        print(json.dumps({"phase": phase, "id": q["id"], "generation_wall_seconds": generation_seconds}), flush=True)
                    mixed += int(len(set(rewards)) > 1)
                    by_id[q["id"]] = rewards
                return {"mixed_groups": mixed, "by_id": by_id,
                        "solved": sum(any(r) for r in by_id.values()), "theorems": len(by_id)} if details else mixed

        if config.get("completion_study"):
            model = load_model()
            probes = {}
            for label, variant, level in [("existing", "existing", "body"), ("prefilled", "prefilled", "body"),
                                          ("expression", "prefilled", "expression"), ("tactic", "prefilled", "tactic")]:
                probes[label] = evaluate(model, "probe_" + label, questions, "full", level, variant, True)
            run["probes"] = probes
            evaluate(model, "baseline", details=True)
            eligible = {}
            for q in questions:
                for index, label in enumerate(["expression", "tactic", "prefilled"]):
                    if len(set(probes[label]["by_id"][q["id"]])) > 1:
                        eligible[q["id"]] = index
                        break
            run["eligible_start_levels"] = eligible
            run["ordering_identifiable"] = len(eligible) > 1
            del model
            gc.collect()
            torch.cuda.empty_cache()
            if not eligible:
                run["status"] = "no_informative_training_groups"
                return
            selected = [q for q in questions if q["id"] in eligible]
            cv = [q for q in validation if q["split"] == "validation"]
            heldout = [q for q in validation if q["split"] == "heldout"]
            stage = 0
            run["completion_rounds"] = []
            for round_index in range(config["completion_rounds"]):
                round_record = {"round": round_index, "shared_stage": stage, "validation": {}, "orders": {}}
                for arm in config["arms"]:
                    arm_dir = args.output / arm
                    arm_dir.mkdir(exist_ok=True)
                    checkpoint = arm_dir / "policy"
                    model = load_model(str(checkpoint) if checkpoint.exists() else None)
                    optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"], weight_decay=0.0)
                    if (arm_dir / "optimizer.pt").exists():
                        optimizer.load_state_dict(torch.load(arm_dir / "optimizer.pt", map_location="cpu", weights_only=True))
                    order = ordered_ids(selected, arm, len(selected), config["seed"] + round_index)
                    round_record["orders"][arm] = order
                    if arm == config["arms"][0]:
                        run["completion_rounds"].append(round_record)
                    save_run()
                    with (arm_dir / "rollouts.jsonl").open("a") as rollout_file, (arm_dir / "trainer.jsonl").open("a") as logs_file:
                        informative = [0]
                        def terminal_completion(prompts, completions, completion_ids, theorem_id, completion_level, trainer_state, **kwargs):
                            if len(set(theorem_id)) != 1 or len(set(completion_level)) != 1 or len(completions) != config["num_generations"]:
                                raise RuntimeError("Completion reward group mixes tasks or assistance")
                            level = completion_level[0]
                            expected = ["expression", "tactic", "body"][min(eligible[theorem_id[0]] + stage, 2)]
                            if level != expected:
                                raise RuntimeError("Completion assistance differs from shared schedule")
                            q = lookup[theorem_id[0]]
                            supplied = completion_prompt(q, level)[-1]["content"]
                            responses = [c[0]["content"] for c in completions]
                            if any(not c.startswith(supplied) for c in responses):
                                raise RuntimeError("TRL did not reconstruct the supplied assistant prefix")
                            prompt_tokens = len(tokenizer.apply_chat_template(completion_prompt(q, level), tokenize=True,
                                                                             continue_final_message=True))
                            rewards = [record_attempt(q, c, ids, arm, round_index * len(selected) + trainer_state.global_step,
                                                      rollout_file, level, supplied, prompt_tokens)
                                       for c, ids in zip(responses, completion_ids, strict=True)]
                            informative[0] += int(len(set(rewards)) > 1)
                            return rewards
                        class CompletionRecords(TrainerCallback):
                            def on_step_begin(self, args, state, control, **kwargs):
                                reserve_generation(config["num_generations"])
                            def on_log(self, args, state, control, logs=None, **kwargs):
                                row = {"step": round_index * len(selected) + state.global_step, "round": round_index,
                                       "shared_stage": stage, "wall_seconds": time.monotonic() - started,
                                       "informative_groups": informative[0], **counts, **(logs or {})}
                                logs_file.write(json.dumps(row) + "\n")
                                logs_file.flush()
                                print(json.dumps({"arm": arm, **row}), flush=True)
                        settings = GRPOConfig(output_dir=str(arm_dir), max_steps=len(selected),
                            per_device_train_batch_size=1, gradient_accumulation_steps=config["num_generations"],
                            generation_batch_size=config["num_generations"], num_generations=config["num_generations"],
                            num_iterations=1, shuffle_dataset=False, seed=config["seed"] + round_index, data_seed=config["seed"] + round_index,
                            learning_rate=config["learning_rate"], lr_scheduler_type="constant", weight_decay=0.0, bf16=True,
                            gradient_checkpointing=True, max_prompt_length=config["max_prompt_tokens"],
                            max_completion_length=config["max_completion_tokens"], temperature=config["temperature"],
                            top_p=config["top_p"], top_k=config["top_k"], beta=config["beta"], loss_type=config["loss_type"], scale_rewards="group",
                            use_vllm=False, logging_steps=1, save_strategy="no", report_to="tensorboard", remove_unused_columns=False)
                        dataset = Dataset.from_list([{"prompt": completion_prompt(lookup[qid], ["expression", "tactic", "body"][min(eligible[qid] + stage, 2)]),
                                                      "theorem_id": qid, "completion_level": ["expression", "tactic", "body"][min(eligible[qid] + stage, 2)]}
                                                     for qid in order])
                        trainer = GRPOTrainer(model=model, args=settings, train_dataset=dataset, processing_class=tokenizer,
                                              reward_funcs=terminal_completion, callbacks=[CompletionRecords()], optimizers=(optimizer, None))
                        began = time.monotonic()
                        trainer.train()
                        trainer.save_model(str(checkpoint))
                        torch.save(optimizer.state_dict(), arm_dir / "optimizer.pt")
                        checker.save(arm_dir / "checkpoint_manifest.json", {"round": round_index, "shared_stage": stage,
                            "config": config, "selected_ids": list(eligible), "start_levels": eligible,
                            "lean": run["lean"], "mathlib": run["mathlib"], "data_sha256": run["data_sha256"],
                            "loss_scope": "generated continuation tokens only; supplied assistant prefix is prompt context"})
                        previous = run["arms"].get(arm, {})
                        run["arms"][arm] = {"optimizer_steps": previous.get("optimizer_steps", 0) + trainer.state.global_step,
                                            "informative_groups": previous.get("informative_groups", 0) + informative[0],
                                            "training_wall_seconds": previous.get("training_wall_seconds", 0) + time.monotonic() - began,
                                            "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated()}
                        del trainer, optimizer, dataset
                    round_record["validation"][arm] = evaluate(model, arm + "_cv_" + str(round_index), cv, details=True)
                    if round_index == config["completion_rounds"] - 1:
                        evaluate(model, arm + "_heldout", heldout, details=True)
                    del model
                    gc.collect()
                    torch.cuda.empty_cache()
                    save_run()
                stage = next_completion_stage({arm: result["solved"] for arm, result in round_record["validation"].items()},
                                              stage, config["fade_min_solved_validation"])
                round_record["next_shared_stage"] = stage
                save_run()
            run["status"] = "complete"
            return
        for arm in config["arms"]:
            model = load_model()
            if arm == config["arms"][0]:
                if config.get("readiness_probe"):
                    mixed = {level: evaluate(model, "readiness_" + level, questions, level) for level in readiness_levels}
                    run["readiness_mixed_groups"] = mixed
                    if not any(mixed.values()):
                        run["status"] = "no_informative_training_groups"
                        return
                evaluate(model, "baseline")
            arm_dir = args.output / arm
            arm_dir.mkdir()
            rollout_file = (arm_dir / "rollouts.jsonl").open("x")
            logs_file = (arm_dir / "trainer.jsonl").open("x")
            informative_groups = 0
            def terminal_proof(prompts, completions, completion_ids, theorem_id, trainer_state, assistance_level=None, **kwargs):
                nonlocal informative_groups
                if len(set(theorem_id)) != 1 or len(completions) != config["num_generations"]:
                    raise RuntimeError("Reward group does not describe one theorem")
                level = (assistance_level or ["none"])[0]
                if assistance_level and len(set(assistance_level)) != 1:
                    raise RuntimeError("One reward group has different assistance levels")
                expected = assistance_at(config, trainer_state.global_step, len(questions))
                if level != expected:
                    raise RuntimeError("Observed assistance differs from the registered pass")
                rewards = [record_attempt(lookup[qid], c[0]["content"], ids, arm, trainer_state.global_step, rollout_file, level)
                           for qid, c, ids in zip(theorem_id, completions, completion_ids, strict=True)]
                informative_groups += int(len(set(rewards)) > 1)
                return rewards
            class Records(TrainerCallback):
                def on_step_begin(self, args, state, control, **kwargs):
                    reserve_generation(config["num_generations"])
                def on_log(self, args, state, control, logs=None, **kwargs):
                    record = {"step": state.global_step, "wall_seconds": time.monotonic() - started,
                              "disk_free_bytes": shutil.disk_usage(arm_dir).free,
                              "generated_tokens": counts["generated_tokens"], "physical_lean_calls": counts["physical_lean_calls"],
                              "informative_groups": informative_groups, **(logs or {})}
                    logs_file.write(json.dumps(record) + "\n")
                    logs_file.flush()
                    print(json.dumps({"arm": arm, **record}), flush=True)
            settings = GRPOConfig(output_dir=str(arm_dir), max_steps=config["max_steps_per_arm"],
                per_device_train_batch_size=1, gradient_accumulation_steps=config["num_generations"],
                generation_batch_size=config["num_generations"], num_generations=config["num_generations"],
                num_iterations=1, shuffle_dataset=False, seed=config["seed"], data_seed=config["seed"],
                learning_rate=config["learning_rate"], weight_decay=0.0, bf16=True,
                gradient_checkpointing=True, max_prompt_length=config["max_prompt_tokens"],
                max_completion_length=config["max_completion_tokens"], temperature=config["temperature"],
                top_p=config["top_p"], top_k=config["top_k"], beta=config["beta"], loss_type=config["loss_type"], scale_rewards="group",
                use_vllm=False, logging_steps=1, save_steps=config["save_steps"], save_total_limit=config["save_total_limit"],
                report_to="tensorboard", remove_unused_columns=False)
            dataset = Dataset.from_list([{"prompt": prompt(lookup[qid], assistance_at(config, i, len(questions))),
                                          "theorem_id": qid, "assistance_level": assistance_at(config, i, len(questions))}
                                         for i, qid in enumerate(orders[arm])])
            trainer = GRPOTrainer(model=model, args=settings, train_dataset=dataset,
                processing_class=tokenizer, reward_funcs=terminal_proof, callbacks=[Records()])
            trainer.train()
            run["arms"][arm] = {"optimizer_steps": trainer.state.global_step, "informative_groups": informative_groups,
                                "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated()}
            trainer.save_state()
            rollout_file.close()
            logs_file.close()
            evaluate(model, arm + "_after")
            save_run()
            del trainer, model, dataset
            gc.collect()
            torch.cuda.empty_cache()
        run["status"] = "complete"
    except BaseException as error:
        run["status"] = "failed"
        run["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        save_run()


if __name__ == "__main__":
    main()
