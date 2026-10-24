"""Measure how often a frozen model finishes a training proof from a later starting point.

For each replay-accepted linear proof with n tactics and each depth d, the prompt supplies the
first n-d reference tactics and the current goal; the model writes the remaining tactics.
Lean checks the whole proof with the pinned checker (direct LEAN_PATH call, 20-second timeout).
No training. Training proofs only.

Memory: one FP32 0.5B model (~2 GiB) + k sequences x 1,280 tokens of KV cache (<0.2 GiB),
plus one Lean process capped at 2 GiB. Run under a 6 GiB service cap with 8 CPU threads.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import re
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit")]
from prepare_grpo_pilot import load_checker  # noqa: E402
from replay_training_proofs import ALLOWED_AXIOMS, axioms_of, direct_lean_call, load, proof_body, question  # noqa: E402

# Library-search tactics (exact?, apply?, rw?, hint, observe) prove goals without the model naming a lemma; block them.
# Metaprogram entry points (run_tac, run_conv, by_elab, run_cmd, run_elab, run_meta) run arbitrary code during
# elaboration; a run_tac can register a fake `#print axioms` elaborator (check_checker_attacks.py, 2026-10-10).
FORBIDDEN_CODE = re.compile(r"\b(sorry|admit|axiom|unsafe|native_decide|set_option|run_tac|run_conv|by_elab|run_cmd|run_elab|run_meta|observe)\b"
                            r"|#|\b(exact|apply|rw|rw_search)\?|\bhint\b|\blibrary_search\b")


def lean_code(text):
    """The text without Lean comments (-- line comments and nested /- -/ blocks); string literals are kept, so a
    comment marker inside a string does not start a comment. Lean ignores comments, so words in the informal
    reasoning of an interleaved proof cannot change what Lean checks. Char literals such as '"' are not handled."""
    out, i, depth, in_string = [], 0, 0, False
    while i < len(text):
        two = text[i:i + 2]
        if depth:
            if two == "/-":
                depth, i = depth + 1, i + 2
            elif two == "-/":
                depth, i = depth - 1, i + 2
            else:
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
        elif in_string:
            out.append(text[i:i + 2] if text[i] == "\\" else text[i])
            in_string = text[i] != '"'
            i += 2 if text[i] == "\\" else 1
        elif two == "/-":
            depth, i = 1, i + 2
        elif two == "--":
            while i < len(text) and text[i] != "\n":
                i += 1
        else:
            in_string = text[i] == '"'
            out.append(text[i])
            i += 1
    return "".join(out)


class _CodeOnly:
    """FORBIDDEN.search(text) matches FORBIDDEN_CODE only in the code of text, not in its comments."""

    def search(self, text):
        return FORBIDDEN_CODE.search(lean_code(text))


FORBIDDEN = _CodeOnly()
INSTRUCTION = ("Complete this Lean 4 proof. The environment is Lean 4 with mathlib. "
               "The theorem and its first proof steps are given. The current goal state after those steps is:\n\n{state}\n\n"
               "Write only the remaining tactic lines, indented by two spaces, then close the code block.")


def start_points(replayed, rows, depths, count, seed):
    """Stratified sample: about count/len(depths) points per depth, from proofs longer than d when possible."""
    accepted = {r["theorem_id"] for r in replayed if r["status"] == "accepted"}
    proofs = [r for r in rows if r["theorem_id"] in accepted]
    rng, points = random.Random(seed), []
    for depth in depths:
        pool = [r for r in proofs if len(r["traced_tactics"]) > depth] or [r for r in proofs if len(r["traced_tactics"]) == depth]
        rng.shuffle(pool)
        points.extend((r, depth) for r in pool[:count // len(depths)])
    return points


def prompt_text(tokenizer, row, depth, indent=False):
    tactics = row["traced_tactics"]
    given = tactics[:len(tactics) - depth]
    state = tactics[len(tactics) - depth]["state_before"]
    messages = [{"role": "user", "content": INSTRUCTION.format(state=state)}]
    prefill = "```lean\n" + row["declaration_header"] + " := by\n" + (proof_body(given) + "\n" if given else "")
    # With indent, the prefill opens the next tactic line, so the model cannot close the block at once.
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True) + prefill + ("  " if indent else ""), given


def check(checker, q, given, continuation, stem):
    if FORBIDDEN.search(continuation):
        return {"status": "forbidden_construct"}
    body = (proof_body(given) + "\n" if given else "") + continuation.rstrip()
    result = checker.lean_call(checker.source_for(q, body) + "\n#print axioms " + q["name"] + "\n", stem)
    output, axioms = result["output"], axioms_of(result["output"])
    if result["status"] == "timeout":
        return {"status": "timeout"}
    ok = (result["exit_code"] == 0 and "TARGET_ABSENT" in output and "error:" not in output
          and axioms is not None and set(axioms) <= ALLOWED_AXIOMS)
    return {"status": "accepted" if ok else "rejected",
            "first_error": None if ok else next(iter(re.findall(r"error: ([^\n]*)", output)), None)}


def main():
    parser = argparse.ArgumentParser()
    for name in ("train", "replay", "checker", "environment", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model", action="append", required=True, help="label=path; repeat for each model")
    parser.add_argument("--depths", default="1,2,3")
    parser.add_argument("--points", type=int, default=21)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--prefill-indent", action="store_true", help="End the prefill with two spaces of a new tactic line")
    args = parser.parse_args()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    torch.set_num_threads(args.threads)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    checker = load_checker(args.checker, args.environment, args.output)
    checker.lean_call = direct_lean_call(checker, args.environment.resolve())
    with args.replay.open() as input_stream:
        replayed = [json.loads(line) for line in input_stream]
    rows = load(args.train)
    points = start_points(replayed, rows, [int(d) for d in args.depths.split(",")], args.points, args.seed)
    sources, summary = {}, defaultdict(Counter)
    with (args.output / "probe.jsonl").open("x") as out:
        for spec in args.model:
            label, path = spec.split("=", 1)
            tokenizer = AutoTokenizer.from_pretrained(path)
            model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32).eval()
            torch.manual_seed(args.seed)
            for row, depth in points:
                q, skip = question(row, args.environment.resolve(), sources)
                if q is None:
                    raise RuntimeError("Replay-accepted theorem failed to rebuild: " + skip)
                text, given = prompt_text(tokenizer, row, depth, args.prefill_indent)
                inputs = tokenizer(text, return_tensors="pt")
                start = time.monotonic()
                with torch.no_grad():
                    generated = model.generate(**inputs, do_sample=True, temperature=args.temperature, top_p=args.top_p,
                                               top_k=0, max_new_tokens=args.max_new_tokens,
                                               num_return_sequences=args.samples, pad_token_id=tokenizer.eos_token_id)
                seconds = time.monotonic() - start
                outcomes = []
                for i, sequence in enumerate(generated):
                    ids = sequence[inputs["input_ids"].shape[1]:].tolist()
                    while ids and ids[-1] == tokenizer.eos_token_id:
                        ids.pop()
                    continuation = ("  " if args.prefill_indent else "") + tokenizer.decode(ids, skip_special_tokens=True).split("```", 1)[0]
                    stem = f"{label}_{row['theorem_id'][:16]}_d{depth}_s{i}"
                    result = check(checker, q, given, continuation, stem)
                    record = {"model": label, "theorem_id": row["theorem_id"], "full_name": row["full_name"],
                              "family": row["family"], "tactics": len(row["traced_tactics"]), "depth": depth,
                              "sample": i, "generated_tokens": len(ids), "capped": len(ids) >= args.max_new_tokens,
                              "generation_seconds_for_group": seconds, "continuation": continuation, **result}
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()
                    outcomes.append(result["status"] == "accepted")
                    summary[(label, depth)][result["status"]] += 1
                summary[(label, depth)]["groups"] += 1
                summary[(label, depth)]["mixed_groups"] += 0 < sum(outcomes) < len(outcomes)
                print(json.dumps({"model": label, "depth": depth, "id": row["theorem_id"][:12], "accepted": sum(outcomes)}), flush=True)
            del model
    report = {f"{label}_d{depth}": dict(counts) for (label, depth), counts in sorted(summary.items())}
    (args.output / "summary.json").write_text(json.dumps({"points": len(points), "samples": args.samples,
                                                          "prefill_indent": args.prefill_indent,
                                                          "results": report}, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
