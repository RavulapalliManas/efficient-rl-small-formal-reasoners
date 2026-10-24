"""Best-first tactic search with a frozen model on curriculum-validation theorems (Lean REPL tactic mode).

Each theorem is rebuilt with file context; every other evaluation declaration keeps a sorry proof. The REPL
elaborates the file prefix once, then `header := by guard; sorry` gives the root proof state. The model proposes
tactics for the current goals in the SFT step format ("Lean 4 goal state: ... Write the next tactic."), and each
proposal runs in REPL tactic mode. A node's priority is the summed log-probability of the tactics on its path.
A path that closes every goal is re-checked as one whole proof with the subprocess checker (check in
probe_start_depth.py: FORBIDDEN, errors, TARGET_ABSENT, axioms); only re-checked proofs count as solved.
Tactic mode cannot run tactics that need an auxiliary declaration (rl-39 benchmark), so such proposals fail.

Budget per theorem: --expansions node expansions, --candidates samples each, --seconds wall time.
Each record logs tactic calls (repl_calls), whole-proof re-checks, generated tokens (EOS excluded), wall seconds,
model seconds, and Lean seconds (REPL tactic wall and CPU, setup wall, re-check wall).
--tactic-list replaces the model with FIXED (no-model baseline of the same shape): every expansion tries the same
16 tactics, all children have equal priority (breadth-first), and a node is not retried. `decide` is left out because a
stuck tactic stops the theorem, and it solved 0/43 in the single-tactic baseline.
Memory: one FP32 0.5B model (~2 GiB) + candidates x 64-token KV cache, and one REPL process (<2 GiB RSS in
the rl-39 benchmark), restarted for every theorem. Run under an 8 GiB service cap with 6 CPU threads.
"""
import argparse
from collections import defaultdict
import heapq
import itertools
import json
import os
from pathlib import Path
import sys
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from prepare_grpo_pilot import load_checker  # noqa: E402
from replay_training_proofs import direct_lean_call, proof_body, question  # noqa: E402
from probe_start_depth import FORBIDDEN, check  # noqa: E402
from bench_lean_verifier import Repl, repl_output, split_prefix  # noqa: E402

STEP = "Lean 4 goal state:\n\n{state}\n\nWrite the next tactic."
FIXED = ["rfl", "norm_num", "simp", "omega", "linarith", "aesop", "simp_all", "tauto", "ring", "intros", "constructor",
         "ext", "norm_cast", "push_cast", "split_ifs", "contradiction"]


def export(path):
    with zipfile.ZipFile(path) as archive:
        with archive.open(archive.namelist()[0]) as input_stream:
            return [json.loads(line) for line in input_stream]


def propose(model, tokenizer, torch, goals, k, max_new_tokens):
    """Sample k tactics; return unique (tactic, summed log-probability) pairs and the generated token count."""
    text = tokenizer.apply_chat_template([{"role": "user", "content": STEP.format(state=goals)}], tokenize=False,
                                         add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, do_sample=True, temperature=1.0, top_p=0.95, top_k=0, max_new_tokens=max_new_tokens,
                             num_return_sequences=k, pad_token_id=tokenizer.eos_token_id,
                             output_scores=True, return_dict_in_generate=True)
    scores = model.compute_transition_scores(out.sequences, out.scores, normalize_logits=True)
    best, tokens = {}, 0
    for seq, logp in zip(out.sequences, scores):
        ids = seq[inputs["input_ids"].shape[1]:].tolist()
        n = len(ids)
        while n and ids[n - 1] in (tokenizer.eos_token_id, tokenizer.pad_token_id):
            n -= 1
        tokens += n
        tactic = tokenizer.decode(ids[:n], skip_special_tokens=True).strip()
        if tactic:
            best[tactic] = max(best.get(tactic, float("-inf")), float(logp[:max(n, 1)].sum()))
    return sorted(best.items(), key=lambda kv: -kv[1]), tokens


def search(ctx, q, model, tokenizer, torch, args, log):
    start, calls, rechecks, tokens = time.monotonic(), 0, 0, 0
    seconds = {"model_seconds": 0.0, "lean_seconds": 0.0, "lean_cpu_seconds": 0.0, "setup_seconds": 0.0, "recheck_seconds": 0.0}
    repl = Repl(ctx, log)
    record = {"solved": False, "expansions": 0, "repl_calls": 0}
    try:
        head, tail = split_prefix(q["prefix"])
        response, wall, _ = repl.send({"cmd": head}, ctx.setup_timeout)
        seconds["setup_seconds"] += wall
        code, output = repl_output(response)
        if code:
            record["setup_error"] = output[:300]
            return record
        env = response["env"]
        root = tail + q["header"] + " := by\n" + ctx.checker.guard(q["name"]) + "  sorry\n"
        response, wall, _ = repl.send({"cmd": root, "env": env}, ctx.timeout)
        seconds["setup_seconds"] += wall
        sorries = response.get("sorries", [])
        if len(sorries) != 1:
            record["setup_error"] = "root: " + repl_output(response)[1][:300]
            return record
        counter = itertools.count()
        frontier = [(0.0, next(counter), sorries[0]["proofState"], sorries[0]["goal"], [], 0)]
        seen = {sorries[0]["goal"]}
        while frontier and record["expansions"] < args.expansions and time.monotonic() - start < args.seconds:
            neg, _, state, goals, path, tries = heapq.heappop(frontier)
            record["expansions"] += 1
            children = 0
            if model is None:
                proposals = [(t, 0.0) for t in FIXED]
            else:
                t0 = time.monotonic()
                proposals, n = propose(model, tokenizer, torch, goals, args.candidates, args.max_new_tokens)
                tokens += n
                seconds["model_seconds"] += time.monotonic() - t0
            for tactic, logp in proposals:
                if FORBIDDEN.search(tactic) or time.monotonic() - start >= args.seconds:
                    continue
                try:
                    response, wall, cpu = repl.send({"tactic": tactic, "proofState": state}, ctx.timeout)
                    seconds["lean_seconds"] += wall
                    seconds["lean_cpu_seconds"] += cpu or 0.0
                except TimeoutError:
                    repl.close()  # a stuck tactic leaves the REPL busy; stop this theorem
                    record["stopped"] = "tactic_timeout"
                    return record
                calls += 1
                code, _ = repl_output(response)
                if code or response.get("sorries") or "proofState" not in response:
                    continue
                new_goals = response.get("goals", [])
                if not new_goals:
                    proof = path + [tactic]
                    body = proof_body([{"tactic": t} for t in proof])
                    t0 = time.monotonic()
                    verified = check(ctx.checker, q, [], body, q["id"] + f"_found{calls}")
                    seconds["recheck_seconds"] += time.monotonic() - t0
                    rechecks += 1
                    record.setdefault("found", []).append({"proof": proof, "recheck": verified["status"]})
                    if verified["status"] == "accepted":
                        record.update(solved=True, proof=proof)
                        return record
                    continue
                text = "\n\n".join(new_goals)
                if text in seen or len(path) + 1 >= args.max_depth:
                    continue
                seen.add(text)
                children += 1
                heapq.heappush(frontier, (neg - logp, next(counter), response["proofState"], text, path + [tactic], 0))
            if children == 0 and tries + 1 < args.retries and model is not None:
                # Every proposal failed: sample this node again later, behind nodes that did produce children.
                heapq.heappush(frontier, (neg + 1.0, next(counter), state, goals, path, tries + 1))
        return record
    except (TimeoutError, EOFError) as exc:
        record["stopped"] = type(exc).__name__
        return record
    finally:
        # repl_calls counts tactic calls only; the two setup commands (file prefix, root goal) are not included.
        record.update(repl_calls=calls, rechecks=rechecks, generated_tokens=tokens, seconds=time.monotonic() - start, **seconds)
        if repl.proc.poll() is None:
            repl.close()


def main():
    parser = argparse.ArgumentParser()
    for name in ("validation", "test", "checker", "environment", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--tactic-list", action="store_true", help="Propose FIXED instead of model samples")
    parser.add_argument("--label", default="model")
    parser.add_argument("--expansions", type=int, default=24)
    parser.add_argument("--candidates", type=int, default=8)
    parser.add_argument("--max-depth", type=int, default=10)
    parser.add_argument("--retries", type=int, default=3, help="Times a node may be sampled when all its proposals fail")
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20261010)
    args = parser.parse_args()
    if (args.model is None) != args.tactic_list:
        parser.error("give exactly one of --model and --tactic-list")
    environment = args.environment.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    for sub in ("lean", "repl"):
        (args.output / sub).mkdir()
    checker = load_checker(args.checker, args.environment, args.output)
    checker.lean_call = direct_lean_call(checker, environment, args.timeout)
    libs = [environment / "mathlib/.lake/build/lib"] + sorted((environment / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    lean_env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    ctx = argparse.Namespace(output=args.output.resolve(), timeout=args.timeout, setup_timeout=300, repl=args.repl.resolve(),
                             checker=checker, lean_env=lean_env,
                             repl_env=dict(lean_env, LEAN_SYSROOT=str(checker.LEAN.parent), PATH=str(checker.LEAN) + os.pathsep + os.environ["PATH"]))
    torch = tokenizer = model = None
    if args.model:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        torch.set_num_threads(args.threads)
        torch.manual_seed(args.seed)
        tokenizer = AutoTokenizer.from_pretrained(args.model)
        model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32).eval()
    validation, test = export(args.validation), export(args.test)
    spans = defaultdict(list)
    for r in validation + test:
        spans[r["file_path"]].append((r["start"][0], r["end"][0], r["declaration_header"]))
    sources, solved = {}, 0
    totals = dict.fromkeys(["repl_calls", "rechecks", "generated_tokens", "seconds", "model_seconds", "lean_seconds",
                            "lean_cpu_seconds", "setup_seconds", "recheck_seconds"], 0)
    with (args.output / "search.jsonl").open("x") as out:
        for row in validation:
            others = {p: [s for s in v if p != row["file_path"] or s[0] != row["start"][0]] for p, v in spans.items()}
            q, skip = question(row, environment, sources, "file", others)
            record = {"theorem_id": row["theorem_id"], "full_name": row["full_name"], "model": args.label}
            record.update(search(ctx, q, model, tokenizer, torch, args, args.output / "repl" / f"{row['theorem_id'][:16]}.log")
                          if q else {"solved": False, "setup_error": skip})
            solved += record["solved"]
            for key in totals:
                totals[key] += record.get(key, 0)
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            print(json.dumps({"id": row["theorem_id"][:12], "solved": record["solved"], "expansions": record.get("expansions"),
                              "calls": record.get("repl_calls"), "tokens": record.get("generated_tokens")}), flush=True)
    summary = {"model": args.label, "theorems": len(validation), "solved": solved, "expansions": args.expansions,
               "retries": args.retries,
               "candidates": args.candidates, "seconds_per_theorem": args.seconds, "max_depth": args.max_depth,
               "seed": args.seed, "fixed_tactics": FIXED if args.tactic_list else None, "totals": totals}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
