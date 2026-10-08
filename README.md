# Efficient RL for Small Formal Reasoners

This project tests when reinforcement learning (RL) helps a small language model learn number theory and write Lean proofs under a fixed compute budget. The model is `Qwen/Qwen2.5-0.5B-Instruct`.

- What we ran and found: [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)
- Full report of every experiment, with the formal definitions: [docs/tex/project_report.pdf](docs/tex/project_report.pdf)

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.12.
2. Install [elan and Lean](https://lean-lang.org/install/manual/). Lean is separate from the Python environment.
3. Run `uv sync`.
4. Run `uv run pre-commit install`.
5. Run the tests with `uv run python -m unittest discover -s tests`.

Training and inference run on cpu-angad and rented GPUs, not on a laptop. Those environments supply `torch`, `transformers`, `trl`, and `tensorboardX`, which `pyproject.toml` does not list.

## Layout

| Path | Contents |
| --- | --- |
| `configs/` | Frozen run configs: `data/`, `eval/`, `pretrain/`, `midtrain/`, `rl/` |
| `scripts/` | Entry points by stage: `data/`, `train/`, `eval/`, `audit/`, `monitor/` |
| `tests/` | Unit tests |
| `docs/` | `EXPERIMENTS.md` and the TeX report in `docs/tex/` |
| `artifacts/` | Local outputs, outside Git. Most outputs live on cpu-angad and in S3 |

## Scripts

Run each script from the repository root.

| Stage | Script | Purpose |
| --- | --- | --- |
| Data | [`prepare_leandojo.py`](scripts/data/prepare_leandojo.py) | Build LeanDojo theorem subsets and check split overlap |
| Data | [`prepare_grpo_pilot.py`](scripts/data/prepare_grpo_pilot.py) | Build whole-proof prompts; holds the shared Lean checker loader |
| Data | [`prepare_prerequisite_curriculum.py`](scripts/data/prepare_prerequisite_curriculum.py) | Build the authored prerequisite and proof-completion tasks |
| Data | [`prepare_rlp_corpus.py`](scripts/data/prepare_rlp_corpus.py) | Build RLP training text |
| Data | [`mathematical_rlp_data.py`](scripts/data/mathematical_rlp_data.py) | Build the examples for the controlled RLP study |
| Train | [`run_grpo_pilot.py`](scripts/train/run_grpo_pilot.py) | GRPO training for the proof and local-action pilots |
| Train | [`local_operator_training.py`](scripts/train/local_operator_training.py) | Train the local-action pilot, where Lean checks one witness |
| Train | [`run_rlp_pilot.py`](scripts/train/run_rlp_pilot.py) | RLP training |
| Eval | [`probe_qwen.py`](scripts/eval/probe_qwen.py) | Local math probe of the base model |
| Eval | [`evaluate_acquisition.py`](scripts/eval/evaluate_acquisition.py) | Free-response knowledge and proof evaluations |
| Eval | [`grade_mathematical_responses.py`](scripts/eval/grade_mathematical_responses.py) | Prepare blinded grading files and recount grades |
| Eval | [`verify_local_operator.py`](scripts/eval/verify_local_operator.py) | Lean check of a submitted witness |
| Audit | [`audit_proofs.py`](scripts/audit/audit_proofs.py) | Label GRPO proof failures |
| Audit | [`audit_rlp_updates.py`](scripts/audit/audit_rlp_updates.py) | Check RLP weight updates and BF16 rounding |
| Audit | [`audit_primitive_rlp.py`](scripts/audit/audit_primitive_rlp.py) | Audit the primitive RLP run |
| Audit | [`audit_rlp_corpus.py`](scripts/audit/audit_rlp_corpus.py) | Audit the RLP training data |
| Audit | [`classify_proof_failures.py`](scripts/audit/classify_proof_failures.py) | Classify the Lean errors of saved proof attempts |
| Audit | [`curriculum_inventory.py`](scripts/audit/curriculum_inventory.py) | Count curriculum starting points in the LeanDojo training proofs |
| Monitor | [`track_tensorboard.py`](scripts/monitor/track_tensorboard.py) | Export run records to TensorBoard |

## Status

This table is the only status record. Update it when a run finishes.

| Experiment | Config | Status |
| --- | --- | --- |
| LeanDojo data subset | `configs/data/leandojo_*.json` | Done. Lean replay not done |
| Base model probe + GSM8K LoRA | `configs/eval/qwen_05b_probe.json` | Done. No accuracy gain |
| Whole-proof GRPO | `configs/rl/qwen_05b_whole_proof_grpo.json` | Done. No learning signal |
| Prerequisite curriculum GRPO | `configs/rl/qwen_05b_prerequisite_grpo*.json` | Done. Training skipped: no proof solved |
| Assisted proof completion | `configs/rl/qwen_05b_completion_grpo.json` | Done. One task qualified; no held-out success |
| RLP training + continuation | `configs/pretrain/qwen_05b_number_theory_rlp*.json` | Done. No accuracy gain |
| FP32 RLP rerun | `configs/midtrain/qwen_05b_number_theory_rlp_fp32.json` | Done. No accuracy gain |
| Primitive RLP curriculum | `configs/midtrain/qwen_05b_primitive_rlp.json` | Done. Held-out accuracy fell |
| Saved-checkpoint evaluations | `configs/eval/` | Done. No complete-answer gain |
| Local-action pilot | `configs/rl/qwen_05b_local_operator_grpo.json` | Done. Witness accuracy improved |
| Controlled RLP comparison | `configs/midtrain/qwen_05b_controlled_rlp.json` | Ordered arm scored. Shuffled arm not scored |
| Lean-verified evaluation | None | Proposed |
| Interactive Lean RL (reward × curriculum) | None | Not started. Needs Anwesha's Lean environment and Abhinav's optimizer |
