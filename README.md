# Efficient RL for Small Formal Reasoners

This project studies reinforcement learning (RL) for number-theory proofs with `Qwen/Qwen2.5-0.5B-Instruct`.
The model proposes proof steps. Lean 4 checks them, and a complete accepted proof earns the proof reward.

<a id="read-this-first"></a>

Read the [research report](docs/tex/project_report.pdf) for methods, results and limitations.
Use the [script guide](scripts/README.md) for reproduction and code navigation, and the [config guide](configs/README.md) to select settings.

## Setup

Install Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from the repository root:

```bash
uv sync --locked
uv run python scripts/reproduce.py overview
uv run python -m unittest tests.test_reproduction tests.test_rl_components
```

These checks need no GPU, model weights or Lean installation. They do not reproduce a research result.
For training dependencies, the external Lean runtime and integration tests, see [Runtime and development](scripts/README.md#runtime-and-development).

## Reproduce the main study

The 18–19 October study compares the unchanged model (B), supervised fine-tuning (S), shuffled RL (R) and prerequisite-first RL (C).
Follow the [reproduction procedure](scripts/README.md#main-study-reproduction) for frozen inputs, file checks and command preparation.
Original data, checkpoints and the Linux Lean runtime remain external; some original builders are private.

## Recreate datasets

The [public rebuild](scripts/README.md#dataset-recreation) fetches pinned upstream sources, assembles candidates, verifies proofs and exports a new dataset.
It produces a separate exploratory release; it does not reconstruct the historical 506-theorem training release or its action examples.

```bash
uv sync --locked --group data
uv run python scripts/data/rebuild_nt_dataset.py sources --source lean_workbook
```

`sources` lists revisions, file sizes and checksums without downloading data.
See the [data specification](docs/EXPERIMENTS.md#data-handoff-for-abhinav-20-october-2026-ist) for historical schemas and provenance.

## Status

Experiment execution stopped on 20 October 2026. EXP-029 and EXP-030 did not run.
The recorded results do not demonstrate better proof performance on unseen theorems after RL.

- B, S, R and C each prove 0 of 8 DEV theorems. This small set cannot resolve modest differences.
- On the separate selection set, S proves 1 of 163 assessed theorems, R proves 1 of 164, and filtered RL proves 2 of 164. No arm meets the promotion rule. One S theorem is censored by a runtime defect; it is not counted as a failed proof.
- Prerequisite-first RL accepts 255 of 4,048 TRAIN attempts, against 164 for shuffled RL. These are training results from one run per arm, not evidence of transfer to unseen theorems.

The [report](docs/tex/project_report.pdf) explains the comparisons and their limits.
The tables below are the execution status record; the [experiment register](docs/EXPERIMENTS.md#experiment-register) links protocols and evidence.

<details>
<summary>Experiment results and execution status (32 records)</summary>

| ID | Experiment | Config or entry point | Status |
| --- | --- | --- | --- |
| [EXP-030](docs/EXPERIMENTS.md#exp-030) | Do per-action Lean-error penalties improve proof completion? | `scripts/train/run_tactic_rl.py` | Not run. All experiments stopped on 20 October; the proposed reward comparison remains unmeasured |
| [EXP-029](docs/EXPERIMENTS.md#exp-029) | Compare RL arms with S at 8 attempts on the screened selection set and 62 filtered TRAIN theorems | `scripts/train/run_tactic_rl.py` | Not run. Neither planned stage launched before experiments stopped |
| [EXP-028](docs/EXPERIMENTS.md#exp-028) | Low-acceptance remedies: step search, automation fallback, lemma retrieval, search-mined SFT, pass-rate-filtered RL | `scripts/train/run_tactic_rl.py` options; `scripts/data/build_search_sft.py`, `add_premises_sft.py`, `build_premise_corpus.py` | No remedy arm is promoted at 2 attempts per theorem: S (E0) and search (E2) each prove 1 of 163 assessed theorems; lemma retrieval (E3), R (E7), the SFT seed control (E5) and filtered RL (E6) prove 1, 1, 0 and 2 of 164, respectively. E0 and E2 censor one theorem after an automation timeout. E4 did not run: the S_mined SFT stage failed on a data-builder defect, now fixed. RL_filtered raises acceptance on its 62 TRAIN theorems from 0.31 to 0.60 (p = 0.0001), without measurable transfer. A separate Kimina-Prover-Distill-0.6B probe proves 47 and 40 of 164 within 8 attempts in two draws (56 in either); its format and token budget differ from S |
| [EXP-027](docs/EXPERIMENTS.md#exp-027) | Curriculum order: benchmark performance and generalization | [Protocol and execution guide](docs/EXPERIMENTS.md#exp-027); [common data and execution plan](docs/EXPERIMENTS.md#common-data-and-curriculum-execution-plan-18-october-2026-ist); `scripts/train/run_tactic_rl.py --task-order` | C accepts 255 of 4,048 TRAIN attempts against R's 164 (per-theorem p = 0.0002), with the gain inside the first epoch. Both prove 0 of 8 DEV theorems; C has no selection-set evaluation. Annotations have no human confirmation or measured prerequisite necessity |
| [EXP-026](docs/EXPERIMENTS.md#exp-026) | Generalization, Abhinav: unchanged Qwen, SFT, and SFT + RLVR | [Revised protocol](docs/EXPERIMENTS.md#generalization-experiment-revised-framing-16-october-2026); [current data and model contract](docs/EXPERIMENTS.md#current-data-and-model-contract-closure-18-october-2026-ist) | Fresh S trained; B, S and R each prove 0 of 8 DEV theorems. R accepts 164 of 4,048 TRAIN attempts with no detected learning (first against second visit, p = 0.93) and proves 1 of 164 selection-set theorems in EXP-028 (E7), no gain over S. RL shifts R toward short, malformed actions (DEV format errors 37 to 113 of 192) |

| ID | Experiment | Config or entry point | Status |
| --- | --- | --- | --- |
| [EXP-025](docs/EXPERIMENTS.md#exp-025) | Broader benchmark and data pipeline | [Builder](scripts/data/build_nt_benchmark.py), [expanded config](configs/data/nt_benchmark_v2.json); [checked prerequisite release](docs/EXPERIMENTS.md#checked-prerequisite-release-outcome-17-october-2026); [full-corpus mapping](docs/EXPERIMENTS.md#full-corpus-mapping-outcome-17-october-2026) | `validated_v2/` supersedes the initial common release for new consumers. This release retains 1,880 TRAIN SFT examples and 125 separate SFT validation examples. Public pretraining exposure and complete semantic alias independence remain unresolved |
| [EXP-024](docs/EXPERIMENTS.md#exp-024) | Undergraduate number-theory SFT: new data against equal-token old data | `scripts/train/run_sft.py`, `scripts/eval/curriculum_metrics.py` | Protocol approved 15 October. Mathlib identity check, syllabus, corpus v0 (40,732 theorems) and single-tactic screen done; no training or evaluation yet |
| [EXP-023](docs/EXPERIMENTS.md#exp-023) | Broader number theory, reward and curriculum comparisons, PPO and REINFORCE | [Data config](configs/data/nt_expanded_tactic_v1.json), `scripts/train/run_tactic_rl.py`, `scripts/train/run_tactic_ppo.py` | Final curriculum run done 13 October. RL gains on new instances of trained families (`id_val`), no gain on held-out families; the ordered curriculum shows no advantage, and the failed token gate invalidates the efficiency claim |
| [EXP-022](docs/EXPERIMENTS.md#exp-022) | PPO versus GRPO with the same Lean reward | `scripts/train/lean_ppo.py`, `configs/rl/qwen_05b_lean_{ppo,grpo}_comparison.json` | Adapter and paired configs prepared; EXP-023 corrected two scoring defects in the shared adapter. Lean PPO training has not run |
| [EXP-021](docs/EXPERIMENTS.md#exp-021) | Monday recovery: verified distillation before Lean RL | `configs/midtrain/qwen_05b_monday_recovery.json` | Done. The raw DEV recount finds no accepted proof in any arm, including the shared start |
| [EXP-020](docs/EXPERIMENTS.md#exp-020) | Alternating SFT with RLP and Lean GRPO | `configs/midtrain/qwen_05b_interleaved_training_v3.json` | Done. Non-trivial validation statements proved at 8 attempts: initial 0/392, common SFT 13/392, SFT/GRPO 6/392, SFT/RLP 15/392 |
| [EXP-019](docs/EXPERIMENTS.md#exp-019) | Interactive Lean RL (fixed tasks before curriculum) | `scripts/train/run_tactic_rl.py`, [run config](configs/rl/qwen_05b_tactic_mixed_followup.json) | Done. Fixed-mixture v2 DEV: 26/48 accepted attempts against the parent's 18/48, single seed, no accepted composition attempt. See the [completion record](docs/EXPERIMENTS.md#fixed-mixture-midterm-completion) |
| [EXP-018](docs/EXPERIMENTS.md#exp-018) | Interleaved number-theory SFT | `configs/midtrain/qwen_05b_nt_interleaved_sft_v1.json` | Ready. Not run (needs GPU approval) |
| [EXP-017](docs/EXPERIMENTS.md#exp-017) | Midsem RL data (splits, SFT data, RL tasks) | `scripts/data/build_nt_splits.py`, `scripts/data/build_syntax_sft.py` | Done. Splits v3 and paired `nt_sft_v8` data: 5,812 train SFT examples and 245 SFT validation examples. No training in this build |
| [EXP-016](docs/EXPERIMENTS.md#exp-016) | Number-theory statement pool | `scripts/data/build_nt_pool.py` | Done. Pool v2: 4,306 Lean-checked statements (train 3,450, validation 420, test 436) |
| [EXP-015](docs/EXPERIMENTS.md#exp-015) | Lean-verified evaluation | `scripts/eval/lean_eval_statements.py`, `scripts/eval/probe_nt_pool.py` | Done for the SFT model. 0/200 Lean Workbook number-theory statements; one standard tactic proves 8/200 |
| [EXP-014](docs/EXPERIMENTS.md#exp-014) | Lean SFT on mathlib proofs | `configs/midtrain/qwen_05b_lean_sft_v1.json` | Done. Validation 2/43 theorems at 8 attempts; 0/17 number-theory theorems |
| [EXP-013](docs/EXPERIMENTS.md#exp-013) | Controlled RLP comparison | `configs/midtrain/qwen_05b_controlled_rlp.json` | Done. Both arms 0/18 fully correct; final core score 1.056 shuffled vs 0.500 ordered (sign test p = 0.18) |
| [EXP-012](docs/EXPERIMENTS.md#exp-012) | RLP corpus audit | `configs/data/mathematical_rlp_review_v1.json`, `scripts/audit/audit_rlp_corpus.py` | Done. All 612 numeric answers correct. 160/448 training rules lack domain or zero conditions |
| [EXP-011](docs/EXPERIMENTS.md#exp-011) | Local-action pilot | `configs/rl/qwen_05b_local_operator_grpo.json` | Done. Witness accuracy improved |
| [EXP-010](docs/EXPERIMENTS.md#exp-010) | Saved-checkpoint evaluations | `configs/eval/` | Done. No complete-answer gain |
| [EXP-009](docs/EXPERIMENTS.md#exp-009) | Primitive RLP curriculum | `configs/midtrain/qwen_05b_primitive_rlp.json` | Done. Held-out accuracy fell |
| [EXP-008](docs/EXPERIMENTS.md#exp-008) | FP32 RLP rerun | `configs/midtrain/qwen_05b_number_theory_rlp_fp32.json` | Done. No accuracy gain |
| [EXP-007](docs/EXPERIMENTS.md#exp-007) | RLP training + continuation | `configs/pretrain/qwen_05b_number_theory_rlp*.json` | Done. No accuracy gain |
| [EXP-006](docs/EXPERIMENTS.md#exp-006) | Assisted proof completion | `configs/rl/qwen_05b_completion_grpo.json` | Done. One theorem qualified; no accepted held-out attempt |
| [EXP-005](docs/EXPERIMENTS.md#exp-005) | Prerequisite curriculum GRPO | `configs/rl/qwen_05b_prerequisite_grpo*.json` | Done. Training skipped: no accepted attempt |
| [EXP-004](docs/EXPERIMENTS.md#exp-004) | Whole-proof GRPO | `configs/rl/qwen_05b_whole_proof_grpo.json` | Done. No learning signal |
| [EXP-003](docs/EXPERIMENTS.md#exp-003) | Instruct model proof check | `configs/eval/qwen_05b_probe.json`, `scripts/eval/probe_qwen.py` | Done. Lean accepted 0/63 local attempts. A GPU sampling run at four temperatures shows 0/273 in its exported records |
| [EXP-002](docs/EXPERIMENTS.md#exp-002) | Base model probe + GSM8K LoRA | `configs/eval/qwen_05b_probe.json` | Done. No accuracy gain |
| [EXP-001](docs/EXPERIMENTS.md#exp-001) | LeanDojo data subset | `configs/data/leandojo_*.json` | Done. Lean replay done: 5,921 of 8,273 training proofs accepted; task bank v2 keeps 2,150 theorems outside evaluation and dev files |
| [AUD-002](docs/EXPERIMENTS.md#aud-002) | Lean reward and engineering audit | `scripts/eval/check_checker_attacks.py`, `scripts/eval/bench_lean_verifier.py`, `scripts/eval/check_scoring_paths.py` | Scorer adoption appears in the decision log. Attack controls do not establish an exhaustive security guarantee |
| [AUD-001](docs/EXPERIMENTS.md#aud-001) | Proof failures and curriculum inventory | `scripts/audit/classify_proof_failures.py`, `scripts/audit/curriculum_inventory.py` | This is a post hoc diagnostic, not a registered curriculum-effect test |

</details>

## Repository layout

| Path | Contents |
| --- | --- |
| [scripts/](scripts/README.md) | Dataset builders, training, evaluation and shared components |
| [configs/](configs/README.md) | Versioned settings and checkpoint download index |
| [tests/](tests/) | Local tests and a separate Lean integration group |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | Protocols, evidence and dated decisions |
| [docs/tex/](docs/tex/) | Report source, PDFs and figures |
| [docs/REWARD_HACKING.md](docs/REWARD_HACKING.md) | Historical verifier audit and exploit evidence |
| [patches/](patches/) | Lean REPL patch for aborted-tactic errors |

<a id="not-in-git"></a>

Data, proof traces, checkpoints and run logs live outside Git. Follow the [archive retrieval procedure](docs/EXPERIMENTS.md#6-step-by-step-fetch-and-verify-the-data).
Recorded archive copies expire between 11 and 19 November 2026 unless moved to permanent storage.

<a id="checkpoints"></a>

Published weights belong to earlier experiments; use the [checkpoint guide](scripts/README.md#checkpoints) to distinguish them from main-study models.

<a id="terms"></a>
<a id="curriculum"></a>
<a id="modify-the-pipeline"></a>
<a id="implementation"></a>
<a id="layout"></a>
<a id="documentation"></a>
<a id="scripts"></a>

For terminology, data roles and implementation details, use the [script guide](scripts/README.md#terms) and [experiment catalogue](docs/EXPERIMENTS.md).
