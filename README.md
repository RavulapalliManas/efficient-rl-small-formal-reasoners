# Efficient RL for Small Formal Reasoners

This project tests when reinforcement learning (RL) helps a small language model learn number theory and write Lean proofs under a fixed compute budget. The model is `Qwen/Qwen2.5-0.5B-Instruct`.

## Read this first

| Purpose | Location |
| --- | --- |
| Current project status | [Status](#status) |
| Saved model checkpoints | [Downloads](#checkpoints) |
| Curriculum data and builders | [Curriculum](#curriculum) |
| Experiment catalogue, protocols, evidence, ownership, and decisions | [EXPERIMENTS.md](docs/EXPERIMENTS.md) |
| Generalization: Abhinav; curriculum: Manas | [Generalization](docs/EXPERIMENTS.md#exp-026); [curriculum](docs/EXPERIMENTS.md#exp-027); [Abhinav's handoff](docs/EXPERIMENTS.md#abhinavs-task-handoff-and-checklist); [report](docs/tex/project_report.pdf) |
| Every dataset of the number-theory study: what it is, how it was made, its frozen copy, and the fetch-and-verify steps | [Data handoff for Abhinav](docs/EXPERIMENTS.md#data-handoff-for-abhinav-20-october-2026-ist) |
| Evaluation questions, expected answers and semantic issues | [Evaluation review pack](docs/EXPERIMENTS.md#exp-025-evaluation-specification-and-semantic-review-17-october-2026) |
| Description of every DEV and TEST question | [Report appendix A](docs/tex/project_report.pdf#page=24); [source and verification](docs/EXPERIMENTS.md#full-evaluation-question-descriptions-17-october-2026) |
| Prerequisite curriculum, proof strategy and reward discussion | [Current handoff](docs/EXPERIMENTS.md#handoff-prerequisites-proof-strategy-and-reward-14-october) |
| Final curriculum run results and evidence | [Results handoff](docs/EXPERIMENTS.md#handoff-final-curriculum-run-results-14-october) |
| Main report source. Edit this for project document requests | [project_report.tex](docs/tex/project_report.tex) |
| Compiled report | [project_report.pdf](docs/tex/project_report.pdf) |
| Two-page setup brief with the Lean loop and ordered next steps | [TeX source](docs/tex/training_setup.tex), [PDF](docs/tex/training_setup.pdf) |
| Reward exploit inventory | [REWARD_HACKING.md](docs/REWARD_HACKING.md) |
| Agent rules and document ownership | [AGENTS.md](AGENTS.md) |

## Curriculum

Download the prepared data from [Lean curriculum v8](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean-curriculum-v8).
The bundle includes both SFT comment variants, syntax examples, source manifests, reference verdicts, and the corrected readiness audit.

The [split builder](scripts/data/build_nt_splits.py) and [syntax builder](scripts/data/build_syntax_sft.py) prepare these inputs.
The [SFT config](configs/midtrain/qwen_05b_nt_interleaved_sft_v1.json) records the adopted data versions.
The [experiment record](docs/EXPERIMENTS.md#exp-017) describes their provenance and limitations.

The [high-school preparation config](configs/data/highschool_prerequisites_v1.json) adds train-only GSM8K and MATH inputs.
The ordered pilot starts with arithmetic, then balances MATH subjects within each competition difficulty level.
The [RLP config](configs/midtrain/qwen_05b_highschool_rlp_preparation.json) keeps worked solutions private and leaves training unauthorized.
The data build prepares a separate frozen diagnostic. Data coverage does not establish the model's knowledge.

The [short-proof selector](scripts/data/prepare_short_proof_subset.py) and [selection config](configs/data/leandojo_short_proofs.json) prepare an original LeanDojo training subset.
The selected tasks retain the source context and exclude official evaluation and reserved DEV files.
Whole-theorem starts and verified reference-prefix starts share binary complete-proof rewards.
Hidden continuations remain private; live Lean goals supply the policy observations.
Reference length is a difficulty proxy. Model-based difficulty calibration remains pending.

The [benchmark builder](scripts/data/build_nt_benchmark.py) freezes a bounded selection from the existing broader corpus.
Its `select` stage records inputs and duplicate groups; `verify` replays references and screens fixed tactics before export.
The package separates TRAIN, DEV and TEST tasks and keeps evaluation proofs outside SFT examples.
Use the [expanded dataset config](configs/data/nt_benchmark_v2.json) and [EXP-025 record](docs/EXPERIMENTS.md#exp-025-expanded-dataset-outcome-16-october-2026) for current inputs and evidence.
The prepared package is cpu-angad `formal_rl/nt_benchmark_v2_20261016/prepared_v1/`.
Its `data/` directory contains the pool, TRAIN/DEV/TEST tasks and SFT inputs.
The [v1 config](configs/data/nt_benchmark_v1.json) remains available for the earlier pilot.
Topic and duplicate groups use text rules. Mathematical family review and model difficulty calibration remain pending.

The main study (EXP-026 and EXP-027) trains on the later `validated_v2` release (506 TRAIN roots, 1,880 SFT action rows), not on `prepared_v1`.
It evaluates on the fixed DEV 8 of `release_v3`; the TEST 21 of `release_v3` stays untouched.
The [data handoff](docs/EXPERIMENTS.md#data-handoff-for-abhinav-20-october-2026-ist) describes every dataset, how it was made, its frozen copy and the steps to fetch and verify it.
A fresh clone cannot rebuild these datasets: several builders are private, and at `main` the benchmark `verify` stage admits no candidate because the screen marks `exact?` forbidden.
The archived copies expire between 11 and 19 November 2026 unless they are moved to permanent storage.

## Modify the pipeline

The existing commands remain the entrypoints. Reusable objects own the boundaries below.
Change the relevant component without copying a training runner or benchmark builder.

| Aspect | Component |
| --- | --- |
| Candidate selection and duplicate-aware splits | [`BenchmarkSelector`](scripts/data/nt_benchmark/selection.py) |
| Data exports and pinned Lean verification | [`BenchmarkExporter`](scripts/data/nt_benchmark/artifacts.py), [`LeanVerifier` and `BenchmarkVerifier`](scripts/data/nt_benchmark/verification.py) |
| Benchmark orchestration | [`BenchmarkPipeline`](scripts/data/nt_benchmark/pipeline.py), with replaceable selector and verifier objects |
| Task admission, curriculum cutoff and sampling order | [`PoolTaskAdmission`, `TaskCurriculum` and `TaskSchedule`](scripts/train/rl_components/tasks.py) |
| REINFORCE objective and optimizer update | [`LeaveOneOutObjective` and `PolicyGradientUpdater`](scripts/train/rl_components/objectives.py) |
| SFT message conversion | [`SFTDataset`](scripts/train/rl_components/sft.py) |
| Retained proof reconstruction and fresh replay export | [`ProofTrajectory`](scripts/eval/proof_export/trajectory.py), [`ProofExporter`](scripts/eval/proof_export/exporter.py) |

For example, import `BenchmarkPipeline` from `scripts.data.nt_benchmark` in a repository checkout.
Pass `selector=your_selector` to replace selection while retaining artifact orchestration.
Its selector must provide `select(paths, max_input_bytes)` and `partition(candidates)`.
Verification reads the frozen package configuration, rather than selecting a new population.
The training updater accepts separate advantage and token-log-probability callables.

The existing `LeanEpisode` class still owns proof interaction.
TRL still owns the whole-proof GRPO optimizer.
Configuration files retain model, sampling, reward and budget settings.
Source receipts include the extracted benchmark and training modules.
Changes to a component require a new recorded experiment when they change scientific behavior.
This organization does not establish faster execution or RL improvement.

## Checkpoints

Saved model weights and tokenizer files are available through [GitHub Releases](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases).
The [download index](configs/checkpoints.json) records source files, asset sizes, and GitHub digests.

| Checkpoint | Download |
| --- | --- |
| SFT final | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean_sft_20261009_v2-final) |
| Primitive RLP step 118 | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/primitive_rlp_20261005-checkpoint-118) |
| Primitive RLP final | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/primitive_rlp_20261005-final) |
| Controlled RLP ordered final | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/controlled_rlp_20261006-ordered-final) |
| Controlled RLP shuffled final | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/controlled_rlp_20261006-shuffled-final) |
| SFT step 300 | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean_sft_20261009_v2-checkpoint-300) |
| SFT step 1500 | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean_sft_20261009_v2-checkpoint-1500) |
| SFT step 4526 | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean_sft_20261009_v2-checkpoint-4526) |

Download all assets for a release into one directory:

```bash
gh release download lean_sft_20261009_v2-final --repo RavulapalliManas/efficient-rl-small-formal-reasoners --dir sft-final
```

If a release contains split files, use the reassembly command in its release notes.
Auxiliary metadata with private infrastructure identifiers uses redacted `.public.json` files.
Weights and optimizer state retain their original contents.

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.12.
2. Install [elan and Lean](https://lean-lang.org/install/manual/). Lean is separate from the Python environment.
3. Run `uv sync`.
4. Run `uv run pre-commit install`.
5. Run the tests with `uv run python -m unittest discover -s tests`.

Training and inference run on rented AWS GPUs, not on a laptop. Those environments supply `torch`, `transformers`, `trl`, and `tensorboardX`, which `pyproject.toml` does not list.
cpu-angad holds historical working copies only; new jobs do not run there.

## Layout

| Path | Contents |
| --- | --- |
| `configs/` | Frozen run configs: `data/`, `eval/`, `pretrain/`, `midtrain/`, `rl/` |
| `scripts/` | Entry points by stage: `data/`, `train/`, `eval/`, `audit/`, `monitor/` |
| `tests/` | Unit tests |
| `docs/` | `EXPERIMENTS.md` and the TeX report in `docs/tex/` |
| `artifacts/` | Ignored historical notes and local exceptions. New data, checkpoints, and results live on remote compute and durable storage |

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
| Eval | [`export_tactic_proofs.py`](scripts/eval/export_tactic_proofs.py) | Export accepted original proof paths, preserve indentation, and require complete replay |
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
| Report | [`make_report_figures.py`](scripts/report/make_report_figures.py) | Draw the report's data figures from recorded counts |

## Status

This table is the only current execution status record. Update it from run evidence when status changes.
The catalogue holds protocols and evidence links; the report holds scientific interpretation.
Historical result values below are retained. This catalogue update does not recount them.
See [evidence gaps and next actions](docs/EXPERIMENTS.md#adopted-inputs-and-next-actions) before reusing a score or launching a run.

EXP-023 final curriculum run completed on 13 October (GPU run v2 plus a CPU evaluation of `id_val` and `external_clean`).
On new questions of the trained problem types (`id_val`), RL raised accepted attempts from 148/320 to 208-242/320 (family test p ≤ 0.0011).
On held-out problem types, no arm differs from the warmstart W (Holm-adjusted p = 1); S1-S2 rise and S3-S4 fall.
The ordered curriculum shows no advantage over shuffling, and the efficiency claim is invalid because the token gate failed.
See the [current handoff](docs/EXPERIMENTS.md#handoff-final-curriculum-run-results-14-october) for evidence and next steps.

EXP-019 midterm runs (native parity and fixed mixture) completed by 12 October.
The fixed-mixture RL final passes 26/48 DEV attempts against the SFT parent's 18/48 on three theorems.
These small panels do not establish held-out generalization.
See the [native parity record](docs/EXPERIMENTS.md#native-parity-midterm-completion) and the [fixed-mixture record](docs/EXPERIMENTS.md#fixed-mixture-midterm-completion).

The next delivery targets number theory proof learning before Wednesday, 21 October.
Complete proofs from the initial goal are the main endpoint; supplied-prefix completions remain a separate diagnostic.
The [delivery amendment](docs/EXPERIMENTS.md#number-theory-rl-delivery-amendment-18-october-2026-ist) records ownership, dependencies and limits.
The [pipeline repair record](docs/EXPERIMENTS.md#number-theory-pipeline-repairs-18-october-2026-ist) records the checked engineering changes and their limits.

| ID | Experiment | Config or entry point | Status |
| --- | --- | --- | --- |
| [EXP-026](docs/EXPERIMENTS.md#exp-026) | Generalization, Abhinav: unchanged Qwen, SFT, and SFT + RLVR | [Revised protocol](docs/EXPERIMENTS.md#generalization-experiment-revised-framing-16-october-2026); [current data and model contract](docs/EXPERIMENTS.md#current-data-and-model-contract-closure-18-october-2026-ist) | The clean study uses fresh SFT from pinned B because historical S trained on examples that invoke a reserved DEV target lemma. The [on-demand L40S main lease](docs/EXPERIMENTS.md#spot-first-amendment-and-launch-18-october-2026-ist) ran from 16:09 IST on 18 October to 07:12 IST on 19 October and terminated (estimated compute USD 33.77 of USD 102.31 reserved). Fresh S trained; B, S and R each solve 0 of 8 DEV roots. R accepts 164 of 4,048 TRAIN attempts with no detected learning (first against second visit, p = 0.93) and solves 1 of 164 held-out theorems in EXP-028 (E7), no gain over S. RL shifts R toward short, malformed actions (DEV format errors 37 to 113 of 192) |
| [EXP-027](docs/EXPERIMENTS.md#exp-027) | Curriculum, Manas: benchmark performance and generalization | [Protocol and execution guide](docs/EXPERIMENTS.md#exp-027); [common data and execution plan](docs/EXPERIMENTS.md#common-data-and-curriculum-execution-plan-18-october-2026-ist); [report plan](docs/tex/project_report.pdf); `scripts/train/run_tactic_rl.py --task-order` | Full AI source review covers 536 canonical roots and 548 TRAIN proof variants; the primary projection retains 506 roots, and both order files contain 1,012 groups with equal task multiplicities. Annotations have no human confirmation or measured prerequisite necessity. C (prerequisite-first) accepts 255 of 4,048 TRAIN attempts against R's 164 on the same tasks (per-task p = 0.0002), with the gain inside the first pass. C narrows onto `omega`, `simp` and `simp_all` (mean action length 10.4 tokens in the last quarter). C solves 0 of 8 DEV roots; the held-out R/C contrast is not yet measured |
| [EXP-028](docs/EXPERIMENTS.md#exp-028) | Low-acceptance remedies: step search, automation fallback, lemma retrieval, search-mined SFT, pass-rate-filtered RL | `scripts/train/run_tactic_rl.py` options; `scripts/data/build_search_sft.py`, `add_premises_sft.py`, `build_premise_corpus.py` | Registered 18 October; four leases ran on 18 and 19 October and all terminated (estimated compute USD 52.28 of USD 165.32 reserved). No remedy arm is promoted on the 164-theorem held-out set: S solves 1 (E0); search (E2), lemma retrieval (E3), the SFT seed control (E5), R (E7) and pass-rate-filtered RL (E6) solve 0 to 2. E4 did not run: the S_mined SFT stage failed on a data-builder defect, now fixed. RL_filtered raises acceptance on its 62 TRAIN tasks from 0.31 to 0.60 (p = 0.0001), without measurable transfer. Kimina-Prover-Distill-0.6B solves 47 and 40 of 164 within 8 samples in two draws (56 in either). vLLM lockstep rollout is 4.06 times faster than sequential (timing only; the backend check has not yet passed) |
| [EXP-025](docs/EXPERIMENTS.md#exp-025) | Broader benchmark and data pipeline | [Builder](scripts/data/build_nt_benchmark.py), [expanded config](configs/data/nt_benchmark_v2.json); [checked prerequisite release](docs/EXPERIMENTS.md#checked-prerequisite-release-outcome-17-october-2026); [full-corpus mapping](docs/EXPERIMENTS.md#full-corpus-mapping-outcome-17-october-2026) | `validated_v2/` supersedes the initial common projection for new consumers. Fresh strict replay accepts all 580 source proofs, and every canonical source root reconstructs under v2. The filtered release retains 1,880 TRAIN actions and 125 separate loss-validation actions. All retained rows pass the actual consumer checks without truncation. Every source record has a retained or quarantine disposition; no records remain pending. Source certificates and frozen evaluation reservations remain bound. Public pretraining exposure and complete semantic alias independence remain unresolved |
| [EXP-024](docs/EXPERIMENTS.md#exp-024) | Undergraduate number-theory SFT: new data against equal-token old data | `scripts/train/run_sft.py`, `scripts/eval/curriculum_metrics.py` | Protocol approved 15 October. Mathlib identity check, syllabus, corpus v0 (40,732 problems) and single-tactic screen done; no training or evaluation yet. A separate [recovery data revision](docs/EXPERIMENTS.md#recovery-data-preparation-18-october-2026-ist) passes CPU verification and is archived for a future run. It adds format and Lean-error recovery observations, preserves the recorded prerequisite categories and split assignments, and quarantines one theorem whose source action hits the live cycle guard. The active EXP-026 pilot stays fixed; no learning gain is measured for the new dataset |
| [EXP-023](docs/EXPERIMENTS.md#exp-023) | Broader number theory, reward and curriculum comparisons, PPO and REINFORCE | [Data config](configs/data/nt_expanded_tactic_v1.json), `scripts/train/run_tactic_rl.py`, `scripts/train/run_tactic_ppo.py` | Final curriculum run done 13 October. RL gains on new instances of trained families (`id_val`), no gain on held-out families; the ordered curriculum shows no advantage, and the failed token gate invalidates the efficiency claim |
| [EXP-001](docs/EXPERIMENTS.md#exp-001) | LeanDojo data subset | `configs/data/leandojo_*.json` | Done. Lean replay done: 5,921 of 8,273 training proofs accepted; task bank v2 keeps 2,150 theorems outside evaluation and dev files |
| [EXP-002](docs/EXPERIMENTS.md#exp-002) | Base model probe + GSM8K LoRA | `configs/eval/qwen_05b_probe.json` | Done. No accuracy gain |
| [EXP-004](docs/EXPERIMENTS.md#exp-004) | Whole-proof GRPO | `configs/rl/qwen_05b_whole_proof_grpo.json` | Done. No learning signal |
| [EXP-005](docs/EXPERIMENTS.md#exp-005) | Prerequisite curriculum GRPO | `configs/rl/qwen_05b_prerequisite_grpo*.json` | Done. Training skipped: no proof solved |
| [EXP-006](docs/EXPERIMENTS.md#exp-006) | Assisted proof completion | `configs/rl/qwen_05b_completion_grpo.json` | Done. One task qualified; no held-out success |
| [EXP-007](docs/EXPERIMENTS.md#exp-007) | RLP training + continuation | `configs/pretrain/qwen_05b_number_theory_rlp*.json` | Done. No accuracy gain |
| [EXP-008](docs/EXPERIMENTS.md#exp-008) | FP32 RLP rerun | `configs/midtrain/qwen_05b_number_theory_rlp_fp32.json` | Done. No accuracy gain |
| [EXP-009](docs/EXPERIMENTS.md#exp-009) | Primitive RLP curriculum | `configs/midtrain/qwen_05b_primitive_rlp.json` | Done. Held-out accuracy fell |
| [EXP-010](docs/EXPERIMENTS.md#exp-010) | Saved-checkpoint evaluations | `configs/eval/` | Done. No complete-answer gain |
| [EXP-011](docs/EXPERIMENTS.md#exp-011) | Local-action pilot | `configs/rl/qwen_05b_local_operator_grpo.json` | Done. Witness accuracy improved |
| [EXP-013](docs/EXPERIMENTS.md#exp-013) | Controlled RLP comparison | `configs/midtrain/qwen_05b_controlled_rlp.json` | Done. Both arms 0/18 fully correct; final core score 1.056 shuffled vs 0.500 ordered (sign test p = 0.18) |
| [EXP-014](docs/EXPERIMENTS.md#exp-014) | Lean SFT on mathlib proofs | `configs/midtrain/qwen_05b_lean_sft_v1.json` | Done. Validation 2/43 theorems at 8 samples; 0/17 number-theory theorems |
| [EXP-015](docs/EXPERIMENTS.md#exp-015) | Lean-verified evaluation | `scripts/eval/lean_eval_statements.py`, `scripts/eval/probe_nt_pool.py` | Done for the SFT model. 0/200 Lean Workbook number-theory statements; one standard tactic solves 8/200 |
| [EXP-016](docs/EXPERIMENTS.md#exp-016) | Number-theory statement pool | `scripts/data/build_nt_pool.py` | Done. Pool v2: 4,306 Lean-checked statements (train 3,450, validation 420, test 436) |
| [EXP-017](docs/EXPERIMENTS.md#exp-017) | Midsem RL data (splits, SFT data, RL tasks) | `scripts/data/build_nt_splits.py`, `scripts/data/build_syntax_sft.py` | Done. Splits v3 and paired `nt_sft_v8` data: 5,812 train rows and 245 loss-evaluation rows. No training in this build |
| [EXP-018](docs/EXPERIMENTS.md#exp-018) | Interleaved number-theory SFT | `configs/midtrain/qwen_05b_nt_interleaved_sft_v1.json` | Ready. Not run (needs GPU approval) |
| [EXP-021](docs/EXPERIMENTS.md#exp-021) | Monday recovery: verified distillation before Lean RL | `configs/midtrain/qwen_05b_monday_recovery.json` | Done. The raw DEV recount finds no accepted proof in any arm, including the shared start |
| [EXP-022](docs/EXPERIMENTS.md#exp-022) | PPO versus GRPO with the same Lean reward | `scripts/train/lean_ppo.py`, `configs/rl/qwen_05b_lean_{ppo,grpo}_comparison.json` | Adapter and paired configs prepared; EXP-023 corrected two scoring defects in the shared adapter. Lean PPO training has not run |
| [EXP-019](docs/EXPERIMENTS.md#exp-019) | Interactive Lean RL (fixed tasks before curriculum) | `scripts/train/run_tactic_rl.py`, [run config](configs/rl/qwen_05b_tactic_mixed_followup.json) | Done. Fixed-mixture v2 DEV: 26/48 accepted attempts against the parent's 18/48, single seed, no composition success. See the [completion record](docs/EXPERIMENTS.md#fixed-mixture-midterm-completion) |
| [EXP-020](docs/EXPERIMENTS.md#exp-020) | Alternating SFT with RLP and Lean GRPO | `configs/midtrain/qwen_05b_interleaved_training_v3.json` | Done. Non-trivial validation statements solved at 8 samples: initial 0/392, common SFT 13/392, SFT/GRPO 6/392, SFT/RLP 15/392 |
