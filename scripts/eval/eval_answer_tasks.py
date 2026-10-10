"""Pass rate of frozen models on Lean-checked answer tasks, next to two no-model baselines.

The model sees the informal question and is asked to end with a line `Answer: <numbers>`. Two parse rules are
scored per sample: strict (the last `Answer:` line or `\\boxed{}`) and lenient (`<label>_lenient`: the last numbers
anywhere in the text). The numbers fill the answer slots of the task's Lean statement, and Lean checks it
(`build_answer_tasks.lean_check`). Lean verifies the
answer only; the reasoning text is not checked. Answers above 10**12 are rejected without a Lean call (every
reference answer is below 10**7). Python also recomputes correctness from the reference, and the summary
reports how often Lean and Python agree.

Baselines: the most common training answer of the family (a constant, one try), and uniform random answers below
the largest training answer of the family (--samples tries, like each model).

Memory: one FP32 0.5B model (~2 GiB) + k sequences of KV cache, plus up to --workers Lean processes at the end
(about 2.6 GB RSS each).
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import re
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "data")]
from build_answer_tasks import lean_check  # noqa: E402

INSTRUCTION = ("Answer this number theory question. Reason briefly, then end with one line of the form "
               "'Answer: <number>' (for two numbers: 'Answer: <p>, <q>').\n\n{question}")
CAP = 10**12


def parse(text, arity, lenient=False):
    """Strict: the last `Answer:` line or `\\boxed{}`. Lenient: the last numbers anywhere in the text."""
    marked = re.findall(r"Answer:\s*([^\n]*)|\\boxed\{([^}]*)\}", text)
    source = text if lenient else ("".join(marked[-1]) if marked else "")
    numbers = [int(x) for x in re.findall(r"\d+", source)]
    return numbers[-arity:] if len(numbers) >= arity else None  # last numbers: "gcd(84, 36) = 12" means 12


def main():
    parser = argparse.ArgumentParser()
    for name in ("tasks", "environment", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--split", default="val")
    parser.add_argument("--model", action="append", default=[], help="label=path; repeat for each model")
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=192)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit-per-family", type=int, default=0, help="0 = all tasks of the split")
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    rows = [json.loads(line) for line in args.tasks.open()]
    train = [r for r in rows if r["split"] == "train"]
    tasks, per_family = [], Counter()
    for r in rows:
        if r["split"] == args.split and (not args.limit_per_family or per_family[r["family"]] < args.limit_per_family):
            tasks.append(r)
            per_family[r["family"]] += 1
    rng = random.Random(args.seed)
    constant = {f: list(Counter(tuple(r["answer"]) for r in train if r["family"] == f).most_common(1)[0][0])
                for f in per_family}
    top = {f: max(max(r["answer"]) for r in train if r["family"] == f) for f in per_family}
    candidates = []  # (source, task, sample, text, parsed, generated_tokens, seconds_for_group)
    for t in tasks:
        candidates.append(("baseline_constant", t, 0, "", constant[t["family"]], 0, 0.0))
        for i in range(args.samples):  # same number of tries as each model
            candidates.append(("baseline_random", t, i, "", [rng.randint(0, top[t["family"]]) for _ in t["answer"]], 0, 0.0))
    if args.model:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        torch.set_num_threads(args.threads)
        for spec in args.model:
            label, path = spec.split("=", 1)
            tokenizer = AutoTokenizer.from_pretrained(path)
            model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32).eval()
            torch.manual_seed(args.seed)
            for t in tasks:
                text = tokenizer.apply_chat_template([{"role": "user", "content": INSTRUCTION.format(question=t["question"])}],
                                                     tokenize=False, add_generation_prompt=True)
                inputs = tokenizer(text, return_tensors="pt")
                start = time.monotonic()
                with torch.no_grad():
                    out = model.generate(**inputs, do_sample=True, temperature=args.temperature, top_p=args.top_p, top_k=0,
                                         max_new_tokens=args.max_new_tokens, num_return_sequences=args.samples,
                                         pad_token_id=tokenizer.eos_token_id)
                seconds = time.monotonic() - start
                for i, sequence in enumerate(out):
                    ids = [x for x in sequence[inputs["input_ids"].shape[1]:].tolist() if x != tokenizer.eos_token_id]
                    response = tokenizer.decode(ids, skip_special_tokens=True)
                    candidates.append((label, t, i, response, parse(response, len(t["answer"])), len(ids), seconds))
                    candidates.append((label + "_lenient", t, i, response, parse(response, len(t["answer"]), lenient=True),
                                       len(ids), seconds))
                print(json.dumps({"model": label, "task": t["task_id"], "seconds": round(seconds, 1)}), flush=True)
            del model
    checkable = [i for i, c in enumerate(candidates) if c[4] is not None and max(c[4]) < CAP]
    theorems = [f"theorem c{i} : {candidates[i][1]['statement'].format(*candidates[i][4])} := by {candidates[i][1]['tactic']}"
                for i in checkable]
    chunks = [(k, theorems[k:k + 300]) for k in range(0, len(theorems), 300)]
    with ThreadPoolExecutor(args.workers) as pool:
        flags = [f for part in pool.map(lambda c: lean_check(c[1], args.environment.resolve(),
                                                              args.output.resolve() / "lean" / f"c{c[0]:06d}.lean",
                                                              args.timeout), chunks) for f in part]
    accepted = dict(zip(checkable, flags))
    stats = defaultdict(lambda: {"tasks": set(), "solved": set(), "samples": 0, "accepted": 0, "unparsed": 0,
                                 "lean_python_agree": 0, "checked": 0})
    with (args.output / "samples.jsonl").open("x") as out:
        for i, (source, t, sample, response, parsed, tokens, seconds) in enumerate(candidates):
            ok = accepted.get(i, False)
            correct = parsed == t["answer"]
            out.write(json.dumps({"source": source, "task_id": t["task_id"], "family": t["family"], "level": t["level"],
                                  "sample": sample, "response": response, "parsed": parsed, "lean_accepted": ok,
                                  "lean_checked": i in accepted, "python_correct": correct, "generated_tokens": tokens,
                                  "generation_seconds_for_group": seconds}, ensure_ascii=False) + "\n")
            for key in (source, f"{source}/{t['family']}", f"{source}/level{t['level']}"):
                s = stats[key]
                s["tasks"].add(t["task_id"])
                s["samples"] += 1
                s["accepted"] += ok
                s["unparsed"] += parsed is None
                if ok:
                    s["solved"].add(t["task_id"])
                if i in accepted:
                    s["checked"] += 1
                    s["lean_python_agree"] += ok == correct
    summary = {"split": args.split, "tasks": len(tasks), "samples_per_task": args.samples,
               "by": {k: {**{x: v[x] for x in ("samples", "accepted", "unparsed", "checked", "lean_python_agree")},
                          "tasks": len(v["tasks"]), "solved_any": len(v["solved"])} for k, v in sorted(stats.items())}}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary["by"].items() if "/" not in k}), flush=True)


if __name__ == "__main__":
    main()
