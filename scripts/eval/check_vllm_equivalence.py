"""vLLM-versus-HF equivalence test for probe_nt_pool.py (GPU; first step of the GPU lease; exit 1 on failure).

1. Prompt tokens: vLLM's prompt token ids equal the HF tokenizer's ids for every statement.
2. Greedy decoding (temperature 0, the same repetition penalty, stop at ```): HF FP32 generate on the GPU and vLLM
   give the same continuation for at least --min-greedy-match of --greedy statements.
3. Sampled outputs: a CPU probe run (--cpu-dir) and a vLLM probe run (--vllm-dir) of the same statements and
   settings. Per statement (paired): mean generated tokens, capped fraction, and the mean per-token log-probability
   of the samples under the HF model at temperature 1. Each two-sided paired t-test must give p >= --alpha.
Lean acceptance is compared afterwards on cpu-angad; sftfinal accepted 0 of 1,600 CPU samples, so acceptance alone
has no power to detect a broken generation path.

Memory (GPU): vLLM at --gpu-memory-utilization plus one FP32 0.5B HF model (~2 GiB) and one sequence of activations.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval")]
from probe_nt_pool import INSTRUCTION, header, read  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    for name in ("pool", "model", "cpu_dir", "vllm_dir", "output"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--greedy", type=int, default=64)
    parser.add_argument("--min-greedy-match", type=float, default=0.8)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--repetition-penalty", type=float, required=True)
    parser.add_argument("--alpha", type=float, default=0.01)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.5)
    args = parser.parse_args()
    import torch
    from scipy.stats import ttest_rel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from vllm import LLM, SamplingParams
    args.output.mkdir(parents=True, exist_ok=False)
    with args.pool.open() as input_stream:
        pool = {r["name"]: r for r in map(json.loads, input_stream)}
    goal = {r["name"]: r["state"] for r in read(args.cpu_dir / "states.jsonl") if r["state"]}
    names = sorted(goal)
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    prompts = {n: tokenizer.apply_chat_template([{"role": "user", "content": INSTRUCTION.format(state=goal[n])}],
                                                tokenize=False, add_generation_prompt=True) + "```lean\n" + header(pool[n]) + " := by\n"
               for n in names}
    failures, report = [], {"statements": len(names)}

    llm = LLM(model=str(args.model), dtype="float32", seed=0, gpu_memory_utilization=args.gpu_memory_utilization)
    greedy_names = names[:args.greedy]
    params = SamplingParams(temperature=0.0, repetition_penalty=args.repetition_penalty, max_tokens=args.max_new_tokens, stop=["```"])
    outputs = llm.generate([prompts[n] for n in names], SamplingParams(max_tokens=1))
    same_ids = sum(list(o.prompt_token_ids) == tokenizer(prompts[n])["input_ids"] for n, o in zip(names, outputs))
    report["prompt_ids_equal"] = f"{same_ids}/{len(names)}"
    if same_ids != len(names):
        failures.append("prompt token ids differ")
    vllm_greedy = [o.outputs[0].text.split("```", 1)[0].rstrip() for o in llm.generate([prompts[n] for n in greedy_names], params)]
    del llm
    torch.cuda.empty_cache()

    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32).cuda().eval()
    hf_greedy = []
    for n in greedy_names:
        inputs = tokenizer(prompts[n], return_tensors="pt").to("cuda")
        with torch.no_grad():
            ids = model.generate(**inputs, do_sample=False, repetition_penalty=args.repetition_penalty, max_new_tokens=args.max_new_tokens,
                                 pad_token_id=tokenizer.eos_token_id, stop_strings=["```"], tokenizer=tokenizer)[0, inputs["input_ids"].shape[1]:]
        hf_greedy.append(tokenizer.decode(ids, skip_special_tokens=True).split("```", 1)[0].rstrip())
    match = sum(a == b for a, b in zip(hf_greedy, vllm_greedy))
    report["greedy_equal"] = f"{match}/{len(greedy_names)}"
    if match < args.min_greedy_match * len(greedy_names):
        failures.append(f"greedy continuations equal on {match} of {len(greedy_names)}")

    def logprob(name, text):
        prompt = tokenizer(prompts[name])["input_ids"]
        completion = tokenizer(text, add_special_tokens=False)["input_ids"]
        if not completion:
            return None
        ids = torch.tensor([prompt + completion], device="cuda")
        with torch.no_grad():
            logits = model(ids).logits[0, len(prompt) - 1:-1].float()
        return torch.log_softmax(logits, -1).gather(1, ids[0, len(prompt):, None]).mean().item()

    per = {}
    for label, d in (("cpu", args.cpu_dir), ("vllm", args.vllm_dir)):
        groups = defaultdict(list)
        for c in read(d / "candidates.jsonl"):
            if c["arm"] != "single_tactic" and c["name"] in goal:
                groups[c["name"]].append(c)
        stats = {}
        for n in names:
            g = groups[n]
            lps = [x for x in (logprob(n, c["continuation"]) for c in g) if x is not None]
            stats[n] = {"tokens": sum(c["generated_tokens"] for c in g) / len(g), "capped": sum(bool(c["capped"]) for c in g) / len(g),
                        "logprob": sum(lps) / len(lps) if lps else 0.0, "samples": len(g)}
        per[label] = stats
    for key in ("tokens", "capped", "logprob"):
        a = [per["cpu"][n][key] for n in names]
        b = [per["vllm"][n][key] for n in names]
        p = float(ttest_rel(a, b).pvalue) if a != b else 1.0
        report[key] = {"cpu_mean": sum(a) / len(a), "vllm_mean": sum(b) / len(b), "paired_t_p": p}
        if not p >= args.alpha:
            failures.append(f"{key}: paired t-test p = {p:.3g}")
    report["samples"] = {k: sum(v[n]["samples"] for n in names) for k, v in per.items()}
    report["failures"] = failures
    # first_diff: the first character where the two greedy texts differ (a late split points to numerics, an early one to a bug)
    first = lambda a, b: next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b))) if a != b else None
    (args.output / "greedy.jsonl").write_text("".join(json.dumps({"name": n, "first_diff": first(a, b), "hf": a, "vllm": b},
                                                                 ensure_ascii=False) + "\n"
                                                      for n, a, b in zip(greedy_names, hf_greedy, vllm_greedy)))
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
