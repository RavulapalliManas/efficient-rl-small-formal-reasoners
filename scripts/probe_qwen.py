"""Bounded, unquantized MLX math probe. Formal headers remain unverified.

Batch size is one. Weights use about 1 GB; a 4,608-token BF16 KV cache uses
24 layers x 2 tensors x 2 heads x 64 dimensions x 4,608 x 2 bytes.
Prefill chunks contain 256 tokens. MLX's 4 GiB limit is a guideline; observed
active memory is checked during generation. This is not a full benchmark.
"""

import argparse
import hashlib
import importlib.metadata
import json
import random
import re
import time
from decimal import Decimal
from pathlib import Path


def read_rows(path):
    with Path(path).open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_rows(path, rows):
    with Path(path).open("x") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def score_answer(benchmark, gold, response):
    response = response.split("\nQuestion:", 1)[0]
    if benchmark == "gsm8k":
        pattern = r"####\s*([-+]?\d[\d,]*(?:\.\d+)?)"
        answer = re.findall(pattern, response)
        target = re.findall(pattern, gold)
        if not target:
            raise ValueError("GSM8K gold has no final answer")
        return (bool(answer) and Decimal(answer[-1].replace(",", "")) ==
                Decimal(target[-1].replace(",", "")), bool(answer))
    from math_verify import LatexExtractionConfig, parse, verify

    config = [LatexExtractionConfig(boxed_match_priority=0)]
    target = parse(gold, extraction_config=config, fallback_mode="no_fallback")
    if not target:
        raise ValueError("MATH gold could not be parsed")
    if "\\boxed{" not in response:
        return False, False
    answer = parse(response, extraction_config=config, fallback_mode="no_fallback")
    return bool(answer) and verify(target, answer), bool(answer)


def prepare(config):
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download

    output = Path(config["data_path"])
    output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(config["seed"])
    probes, provenance, exemplar_questions = [], [], []
    for benchmark, source in config["datasets"].items():
        splits = {}
        for split in ("train", "test"):
            filename = f'{source["directory"]}/{split}-00000-of-00001.parquet'
            path = hf_hub_download(source["repo"], filename, repo_type="dataset",
                                   revision=source["revision"], local_dir=output / "source" / benchmark)
            splits[split] = pq.read_table(path).to_pylist()
            provenance.append({"benchmark": benchmark, "split": split, "source": source,
                               "filename": filename, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                               "n": len(splits[split])})
        question_key = "question" if benchmark == "gsm8k" else "problem"
        answer_key = "answer" if benchmark == "gsm8k" else "solution"
        indices = list(range(len(splits["test"])))
        rng.shuffle(indices)
        if benchmark == "gsm8k":
            indices = indices[:config["gsm8k_n"]]
        else:
            indices = [i for level in range(1, 6) for i in
                       [j for j in indices if splits["test"][j]["level"] == f"Level {level}"][:config["math_per_level"]]]
        train = splits["train"]
        # All few-shot examples and SFT data come from official training splits.
        exemplars = train[:config["shots"]]
        exemplar_questions.extend(r[question_key] for r in exemplars)
        provenance.append({"purpose": "few_shot", "benchmark": benchmark,
                           "source_ids": list(range(config["shots"]))})
        prefix = "".join(f'Question: {r[question_key]}\nAnswer: {r[answer_key]}\n\n' for r in exemplars)
        for i in indices:
            row = splits["test"][i]
            probes.append({"id": f"{benchmark}:test:{i}", "benchmark": benchmark,
                           "level": row.get("level"), "question": row[question_key],
                           "prompt": prefix + f'Question: {row[question_key]}\nAnswer:', "gold": row[answer_key]})
        if benchmark == "gsm8k":
            remaining = list(range(config["shots"], len(train)))
            rng.shuffle(remaining)
            for split, ids in (("train", remaining[:64]), ("valid", remaining[64:80])):
                write_rows(output / f"{split}.jsonl", [{"text": f'Question: {train[i][question_key]}\nAnswer: {train[i][answer_key]}'} for i in ids])
                provenance.append({"purpose": f"sft_{split}", "source_ids": ids})
    # Inspect exact and lexical near-overlap before using the public probe.
    def words(text):
        return set(re.findall(r"\w+", text.lower()))
    training_texts = [r["text"].split("\nAnswer:")[0].removeprefix("Question: ")
                      for split in ("train", "valid") for r in read_rows(output / f"{split}.jsonl")]
    training_texts.extend(exemplar_questions)
    overlaps = []
    for probe in probes:
        a = words(probe["question"])
        for text in training_texts:
            b = words(text)
            similarity = len(a & b) / len(a | b)
            if similarity >= 0.9:
                overlaps.append({"id": probe["id"], "similarity": similarity})
    if overlaps:
        raise ValueError(f"Public probe overlaps SFT data: {overlaps}")
    write_rows(output / "math_probes.jsonl", probes)
    formal = [{"id": r["theorem_id"], "benchmark": "formal_header", "family": r["family"],
               "full_name": r["full_name"], "prompt": f'-- Lean 4. Complete the proof.\n-- Namespace: {r["full_name"].rsplit(".", 1)[0]}\n{r["declaration_header"]} := by\n  ',
               "context_status": "header_only_not_reconstructed"}
              for r in read_rows(config["formal_validation"])]
    write_rows(output / "formal_headers.jsonl", formal)
    (output / "manifest.json").write_text(json.dumps({"config": config, "sources": provenance,
        "overlap": {"threshold": 0.9, "matches": overlaps, "scope": "Few-shot examples, SFT train and diagnostic validation versus selected public test questions; lexical only"},
        "formal_manifest_sha256": hashlib.sha256(Path(config["formal_validation"]).read_bytes()).hexdigest()}, indent=2))
    print(json.dumps({"math_questions": len(probes), "formal_headers": len(formal), "data_path": str(output)}))


def evaluate(config, output, adapter):
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    from mlx.utils import tree_flatten

    output.mkdir(parents=True, exist_ok=False)
    mx.set_memory_limit(config["memory_limit_gib"] * 1024**3)
    mx.set_cache_limit(128 * 1024**2)
    mx.random.seed(config["seed"])
    model, tokenizer = load(config["model_path"], adapter_path=adapter, trust_remote_code=False)
    parameters = tree_flatten(model.parameters())
    manifest = {"config": config, "adapter": adapter, "runtime": "MLX Metal",
                "versions": {p: importlib.metadata.version(p) for p in ("mlx", "mlx-lm", "transformers", "math-verify", "pyarrow")},
                "weight_dtypes": sorted({str(p.dtype) for _, p in parameters}),
                "parameter_bytes": sum(p.nbytes for _, p in parameters),
                "parameter_count": sum(p.size for _, p in parameters),
                "phase": "evaluation",
                "gpu_time_seconds": None, "gpu_time_status": "not separately instrumented",
                "lean_calls": 0, "formal_verifier_status": "unavailable"}
    (output / "run.json").write_text(json.dumps(manifest, indent=2))
    sampler = make_sampler(temp=0)
    rows = read_rows(Path(config["data_path"]) / "math_probes.jsonl") + read_rows(Path(config["data_path"]) / "formal_headers.jsonl")
    with (output / "responses.jsonl").open("x") as handle:
        for question in rows:
            prompt = tokenizer.encode(question["prompt"])
            record = dict(question, prompt_tokens=len(prompt), generated_tokens=0,
                          response="", status="complete", lean_status="not_applicable")
            start = time.perf_counter()
            if len(prompt) > config["max_prompt_tokens"]:
                record["status"] = "prompt_limit"
            else:
                for response in stream_generate(model, tokenizer, prompt, max_tokens=config["max_tokens"],
                                                sampler=sampler, prefill_step_size=256):
                    record["response"] += response.text
                    record["generated_tokens"] = response.generation_tokens
                    record["finish_reason"] = response.finish_reason
                    record["peak_memory_gb"] = response.peak_memory
                    if mx.get_active_memory() > config["memory_limit_gib"] * 1024**3:
                        record["status"] = "memory_limit"
                        break
                    if time.perf_counter() - start > config["max_seconds_per_question"]:
                        record["status"] = "timeout"
                        break
                    if "\nQuestion:" in record["response"]:
                        record["status"] = "next_question_stop"
                        break
            record["wall_seconds"] = time.perf_counter() - start
            if question["benchmark"] == "formal_header":
                record["lean_status"] = "unavailable_context_not_reconstructed"
                record["correct"] = None
            elif record["status"] in ("complete", "next_question_stop"):
                record["correct"], record["answer_extracted"] = score_answer(question["benchmark"], question["gold"], record["response"])
            else:
                record["correct"], record["answer_extracted"] = False, False
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(json.dumps({k: record.get(k) for k in ("id", "status", "correct", "generated_tokens", "wall_seconds")}), flush=True)
            mx.clear_cache()
    summary = summarize(output)
    print(json.dumps(summary, indent=2))


def summarize(output):
    rows = read_rows(output / "responses.jsonl")
    summary = {}
    for benchmark in sorted({r["benchmark"] for r in rows}):
        group = [r for r in rows if r["benchmark"] == benchmark]
        summary[benchmark] = {"n": len(group), "correct": None if benchmark == "formal_header" else sum(r["correct"] for r in group),
                              "generated_tokens": sum(r["generated_tokens"] for r in group),
                              "wall_seconds": sum(r["wall_seconds"] for r in group),
                              "length_capped": sum(r.get("finish_reason") == "length" for r in group),
                              "answer_extracted": sum(r.get("answer_extracted", False) for r in group),
                              "statuses": {s: sum(r["status"] == s for r in group) for s in sorted({r["status"] for r in group})}}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "evaluate", "summarize"))
    parser.add_argument("--config", default="configs/eval/qwen_05b_probe.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--adapter")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if args.mode == "prepare":
        prepare(config)
    elif args.mode == "evaluate":
        if args.output is None:
            parser.error("evaluate requires --output")
        evaluate(config, args.output, args.adapter)
    else:
        if args.output is None:
            parser.error("summarize requires --output")
        print(json.dumps(summarize(args.output), indent=2))
