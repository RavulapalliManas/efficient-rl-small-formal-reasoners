"""Supervised fine-tuning on Lean 4 proofs with TRL 0.24 (prompt-completion, loss on the completion only).

Input: sft_train.jsonl and sft_val.jsonl from scripts/data/build_sft_data.py.
Memory (GPU): FP32 master weights 2 GiB + AdamW 4 GiB + gradients 2 GiB + activations for
batch x max_length tokens in BF16; batch 32 x 1024 fits a 48 GiB L40S. Host: <8 GiB for 290k rows.
Saves full checkpoints at the configured milestone steps and at the end.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import torch
from datasets import Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback
from trl import SFTConfig, SFTTrainer

# The managed launcher uses runpy, which does not add this script's directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rl_components import SFTDataset, component_fingerprints  # noqa: E402
from rl_components.sft import sft_input_receipt  # noqa: E402


def rows(path, limit=None):
    return Dataset.from_list(SFTDataset.from_jsonl(path, limit).records)


class Milestones(TrainerCallback):
    def __init__(self, steps):
        self.steps = set(steps)

    def on_step_end(self, args, state, control, **kwargs):
        if state.global_step in self.steps:
            control.should_save = True
        return control


def main():
    parser = argparse.ArgumentParser()
    for name in ("config", "data", "model", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Use only the first N training rows (smoke tests)")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    source_components = component_fingerprints()
    train_source = SFTDataset.from_jsonl(args.data / "sft_train.jsonl", args.limit)
    val_source = SFTDataset.from_jsonl(args.data / "sft_val.jsonl", config.get("eval_rows"))
    train, val = (Dataset.from_list(data.records) for data in (train_source, val_source))
    input_receipt = sft_input_receipt(args.config, args.model, train_source, val_source)
    input_receipt['component_sha256'] = source_components
    input_receipt['run_sft_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "input_receipt.json").write_text(json.dumps(input_receipt, indent=2) + "\n")
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    # Refuse rather than truncate: the loss sits on the completion at the end of each row, so truncation cuts the target.
    lengths = [n for data in (train, val) for n in SFTDataset(data).validate_token_budget(
        tokenizer, config["max_length"], config.get("max_action_tokens"))]
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32)
    settings = SFTConfig(
        output_dir=str(args.output), seed=config["seed"], learning_rate=config["learning_rate"],
        lr_scheduler_type="cosine", warmup_ratio=config["warmup_ratio"], num_train_epochs=config["epochs"],
        per_device_train_batch_size=config["batch_size"], gradient_accumulation_steps=config["grad_accum"],
        per_device_eval_batch_size=config["batch_size"], max_length=config["max_length"],
        bf16=torch.cuda.is_available(), gradient_checkpointing=config.get("gradient_checkpointing", False), logging_steps=config["logging_steps"],
        eval_strategy="steps", eval_steps=config["eval_steps"], save_strategy=config.get("save_strategy", "steps"), save_steps=10**9,
        report_to=["tensorboard"], logging_dir=str(args.output / "tb"), dataloader_num_workers=2,
        max_steps=config.get("max_steps", -1))
    trainer = SFTTrainer(model=model, args=settings, train_dataset=train, eval_dataset=val,
                         processing_class=tokenizer, callbacks=[Milestones(config["milestone_steps"])])
    start = time.monotonic()
    result = trainer.train()
    trainer.save_model(str(args.output / "final"))
    tokenizer.save_pretrained(str(args.output / "final"))
    record = {"train_rows": len(train), "val_rows": len(val), "config": config, "max_row_tokens": max(lengths),
              "component_sha256": source_components,
              "input_receipt": input_receipt,
              "global_steps": trainer.state.global_step, "train_seconds": time.monotonic() - start,
              "train_loss": result.training_loss, "log_history": trainer.state.log_history,
              "cuda": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
              "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None}
    (args.output / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({k: record[k] for k in ("train_rows", "global_steps", "train_seconds", "train_loss")}), flush=True)


if __name__ == "__main__":
    main()
