# Efficient RL for Small Formal Reasoners

This project tests when reinforcement learning (RL) helps a small language model learn number theory and write Lean proofs under a fixed compute budget. The model is `Qwen/Qwen2.5-0.5B-Instruct`.

## Read this first

| Purpose | Location |
| --- | --- |
| Current project status | [Status](#status) |
| Every experiment, with its record, results and report section | [Experiment register](docs/EXPERIMENTS.md#experiment-register) |
| What each script does, the shared code, and where to start | [scripts/README.md](scripts/README.md) |
| The experiment that each config belongs to | [configs/README.md](configs/README.md) |
| What lives outside Git: data, weights, the Lean checker and private run configs | [Not in Git](#not-in-git) |
| Main report source. Edit this for project document requests | [project_report.tex](docs/tex/project_report.tex) |
| Compiled report | [project_report.pdf](docs/tex/project_report.pdf) |
| Every dataset of the number-theory study: what it is, how it was made, its frozen copy, and the fetch-and-verify steps | [Data handoff for Abhinav](docs/EXPERIMENTS.md#data-handoff-for-abhinav-20-october-2026-ist) |
| Saved model checkpoints | [Downloads](#checkpoints) |
| Curriculum data and builders | [Curriculum](#curriculum) |
| Experiment catalogue, protocols, evidence, ownership, and decisions | [EXPERIMENTS.md](docs/EXPERIMENTS.md) |
| Generalization: Abhinav; curriculum: Manas | [Generalization](docs/EXPERIMENTS.md#exp-026); [curriculum](docs/EXPERIMENTS.md#exp-027); [Abhinav's handoff](docs/EXPERIMENTS.md#abhinavs-task-handoff-and-checklist); [report](docs/tex/project_report.pdf) |
| Evaluation questions, expected answers and semantic issues | [Evaluation review pack](docs/EXPERIMENTS.md#exp-025-evaluation-specification-and-semantic-review-17-october-2026) |
| Description of every DEV and TEST question | [Report appendix A](docs/tex/project_report.pdf#page=37); [source and verification](docs/EXPERIMENTS.md#full-evaluation-question-descriptions-17-october-2026) |
| Prerequisite curriculum, proof strategy and reward discussion | [Handoff of 14 October](docs/EXPERIMENTS.md#handoff-prerequisites-proof-strategy-and-reward-14-october) |
| Final curriculum run results and evidence (EXP-023) | [Results handoff of 14 October](docs/EXPERIMENTS.md#handoff-final-curriculum-run-results-14-october) |
| Two-page setup brief with the Lean loop and ordered next steps | [TeX source](docs/tex/training_setup.tex), [PDF](docs/tex/training_setup.pdf) |
| Reward exploit inventory | [REWARD_HACKING.md](docs/REWARD_HACKING.md) |
| Agent rules and document ownership | [AGENTS.md](AGENTS.md) |

## Layout

| Path | Contents |
| --- | --- |
| [`configs/`](configs/) | Frozen run settings by stage: `data/`, `eval/`, `pretrain/`, `midtrain/`, `rl/`. [configs/README.md](configs/README.md) maps each config to its experiment |
| [`scripts/`](scripts/) | Commands and shared code by stage: `data/`, `train/`, `eval/`, `audit/`, `monitor/`, `report/`. [scripts/README.md](scripts/README.md) describes every script |
| [`tests/`](tests/) | Unit tests. The Lean integration tests need `TACTIC_RL_ROOT`; see [Setup](#setup) |
| [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md) | Experiment catalogue: register, protocols, results, decision log and data handoff |
| [`docs/tex/`](docs/tex/) | The report (`project_report`), the two-page setup brief (`training_setup`) and their figures |
| [`docs/REWARD_HACKING.md`](docs/REWARD_HACKING.md) | Ways the Lean reward paid without a proof |
| [`docs/archive/`](docs/archive/) | A historical handoff snapshot. Not maintained |
| [`patches/`](patches/) | A patch that makes the Lean REPL report the errors of aborted tactics |
| `artifacts/` | Ignored by Git. Local historical notes only; data and results live in remote storage |
| [`AGENTS.md`](AGENTS.md) | Rules for agents. `CLAUDE.md` links to it |

## Not in Git

- The archived copies expire between 11 and 19 November 2026 unless they are moved to permanent storage.
- Datasets, run outputs and logs are in remote storage. The [data handoff](docs/EXPERIMENTS.md#data-handoff-for-abhinav-20-october-2026-ist) lists the archived copies of the number-theory study and the steps to fetch and verify them.
- Published checkpoints are GitHub release assets; see [Checkpoints](#checkpoints). [Section 4.9 of the data handoff](docs/EXPERIMENTS.md#49-model-weights) lists the archived weights of the 18 and 19 October runs.
- The Lean checker module `checker.py`, the Lean REPL binary and the Lean environment are inputs that scripts take by path (`--checker`, `--repl`, `--environment`). [Section 4.10 of the data handoff](docs/EXPERIMENTS.md#410-the-lean-runtime-environment-repl-checker-and-identity) lists their archived copies.
- The run configs of the 18 and 19 October leases, and the recount scripts of 19 and 20 October, are in private launch records under `.private/ops/`.

## Status

This table is the only current execution status record. Update it from run evidence when status changes.
The catalogue holds protocols and evidence links; the report holds scientific interpretation.
Historical result values below are retained. This catalogue update does not recount them.
See [evidence gaps and next actions](docs/EXPERIMENTS.md#adopted-inputs-and-next-actions) before reusing a score or launching a run.

20 October 2026: Manas stops all experiments and asks for analysis only. Lease6 is not launched, and EXP-029 and EXP-030 do not run. See the [EXP-029 stop](docs/EXPERIMENTS.md#exp-029-stop) and [EXP-030 stop](docs/EXPERIMENTS.md#exp-030-stop) entries.

The next delivery targets number theory proof learning before Wednesday, 21 October.
Complete proofs from the initial goal are the main endpoint; supplied-prefix completions remain a separate diagnostic.
The [delivery amendment](docs/EXPERIMENTS.md#number-theory-rl-delivery-amendment-18-october-2026-ist) records ownership, dependencies and limits.
The [pipeline repair record](docs/EXPERIMENTS.md#number-theory-pipeline-repairs-18-october-2026-ist) records the checked engineering changes and their limits.

Rows run from the newest experiment to the oldest. The [register](docs/EXPERIMENTS.md#experiment-register) links the results and report section of each one.

| ID | Experiment | Config or entry point | Status |
| --- | --- | --- | --- |
| [EXP-030](docs/EXPERIMENTS.md#exp-030) | Do per-action Lean-error penalties make REINFORCE close more proofs than the terminal proof reward alone? | `scripts/train/run_tactic_rl.py` | Not run. EXP-030 does not run, because Manas stops all experiments (EXP-029 stop entry). The question of EXP-030 stays open |
| [EXP-029](docs/EXPERIMENTS.md#exp-029) | Do the RL arms differ from S at 8 attempts on the screened 164-theorem selection set and on the 62 RL_filtered tasks? | `scripts/train/run_tactic_rl.py` | Not run. Neither lease5 nor lease6 is launched, and no EXP-029 stage runs. The questions of EXP-029 stay open |
| [EXP-028](docs/EXPERIMENTS.md#exp-028) | Low-acceptance remedies: step search, automation fallback, lemma retrieval, search-mined SFT, pass-rate-filtered RL | `scripts/train/run_tactic_rl.py` options; `scripts/data/build_search_sft.py`, `add_premises_sft.py`, `build_premise_corpus.py` | No remedy arm is promoted on the 164-theorem held-out set: S solves 1 (E0); search (E2), lemma retrieval (E3), the SFT seed control (E5), R (E7) and pass-rate-filtered RL (E6) solve 0 to 2. E4 did not run: the S_mined SFT stage failed on a data-builder defect, now fixed. RL_filtered raises acceptance on its 62 TRAIN tasks from 0.31 to 0.60 (p = 0.0001), without measurable transfer. Kimina-Prover-Distill-0.6B solves 47 and 40 of 164 within 8 samples in two draws (56 in either) |
| [EXP-027](docs/EXPERIMENTS.md#exp-027) | Curriculum, Manas: benchmark performance and generalization | [Protocol and execution guide](docs/EXPERIMENTS.md#exp-027); [common data and execution plan](docs/EXPERIMENTS.md#common-data-and-curriculum-execution-plan-18-october-2026-ist); [report plan](docs/tex/project_report.pdf); `scripts/train/run_tactic_rl.py --task-order` | C (prerequisite-first) accepts 255 of 4,048 TRAIN attempts against R's 164 on the same tasks (per-task p = 0.0002), with the gain inside the first pass. C solves 0 of 8 DEV roots; the held-out R/C contrast is not yet measured. Annotations have no human confirmation or measured prerequisite necessity |
| [EXP-026](docs/EXPERIMENTS.md#exp-026) | Generalization, Abhinav: unchanged Qwen, SFT, and SFT + RLVR | [Revised protocol](docs/EXPERIMENTS.md#generalization-experiment-revised-framing-16-october-2026); [current data and model contract](docs/EXPERIMENTS.md#current-data-and-model-contract-closure-18-october-2026-ist) | Fresh S trained; B, S and R each solve 0 of 8 DEV roots. R accepts 164 of 4,048 TRAIN attempts with no detected learning (first against second visit, p = 0.93) and solves 1 of 164 held-out theorems in EXP-028 (E7), no gain over S. RL shifts R toward short, malformed actions (DEV format errors 37 to 113 of 192) |
| [EXP-025](docs/EXPERIMENTS.md#exp-025) | Broader benchmark and data pipeline | [Builder](scripts/data/build_nt_benchmark.py), [expanded config](configs/data/nt_benchmark_v2.json); [checked prerequisite release](docs/EXPERIMENTS.md#checked-prerequisite-release-outcome-17-october-2026); [full-corpus mapping](docs/EXPERIMENTS.md#full-corpus-mapping-outcome-17-october-2026) | `validated_v2/` supersedes the initial common projection for new consumers. The filtered release retains 1,880 TRAIN actions and 125 separate loss-validation actions. Public pretraining exposure and complete semantic alias independence remain unresolved |
| [EXP-024](docs/EXPERIMENTS.md#exp-024) | Undergraduate number-theory SFT: new data against equal-token old data | `scripts/train/run_sft.py`, `scripts/eval/curriculum_metrics.py` | Protocol approved 15 October. Mathlib identity check, syllabus, corpus v0 (40,732 problems) and single-tactic screen done; no training or evaluation yet |
| [EXP-023](docs/EXPERIMENTS.md#exp-023) | Broader number theory, reward and curriculum comparisons, PPO and REINFORCE | [Data config](configs/data/nt_expanded_tactic_v1.json), `scripts/train/run_tactic_rl.py`, `scripts/train/run_tactic_ppo.py` | Final curriculum run done 13 October. RL gains on new instances of trained families (`id_val`), no gain on held-out families; the ordered curriculum shows no advantage, and the failed token gate invalidates the efficiency claim |
| [EXP-022](docs/EXPERIMENTS.md#exp-022) | PPO versus GRPO with the same Lean reward | `scripts/train/lean_ppo.py`, `configs/rl/qwen_05b_lean_{ppo,grpo}_comparison.json` | Adapter and paired configs prepared; EXP-023 corrected two scoring defects in the shared adapter. Lean PPO training has not run |
| [EXP-021](docs/EXPERIMENTS.md#exp-021) | Monday recovery: verified distillation before Lean RL | `configs/midtrain/qwen_05b_monday_recovery.json` | Done. The raw DEV recount finds no accepted proof in any arm, including the shared start |
| [EXP-020](docs/EXPERIMENTS.md#exp-020) | Alternating SFT with RLP and Lean GRPO | `configs/midtrain/qwen_05b_interleaved_training_v3.json` | Done. Non-trivial validation statements solved at 8 samples: initial 0/392, common SFT 13/392, SFT/GRPO 6/392, SFT/RLP 15/392 |
| [EXP-019](docs/EXPERIMENTS.md#exp-019) | Interactive Lean RL (fixed tasks before curriculum) | `scripts/train/run_tactic_rl.py`, [run config](configs/rl/qwen_05b_tactic_mixed_followup.json) | Done. Fixed-mixture v2 DEV: 26/48 accepted attempts against the parent's 18/48, single seed, no composition success. See the [completion record](docs/EXPERIMENTS.md#fixed-mixture-midterm-completion) |
| [EXP-018](docs/EXPERIMENTS.md#exp-018) | Interleaved number-theory SFT | `configs/midtrain/qwen_05b_nt_interleaved_sft_v1.json` | Ready. Not run (needs GPU approval) |
| [EXP-017](docs/EXPERIMENTS.md#exp-017) | Midsem RL data (splits, SFT data, RL tasks) | `scripts/data/build_nt_splits.py`, `scripts/data/build_syntax_sft.py` | Done. Splits v3 and paired `nt_sft_v8` data: 5,812 train rows and 245 loss-evaluation rows. No training in this build |
| [EXP-016](docs/EXPERIMENTS.md#exp-016) | Number-theory statement pool | `scripts/data/build_nt_pool.py` | Done. Pool v2: 4,306 Lean-checked statements (train 3,450, validation 420, test 436) |
| [EXP-015](docs/EXPERIMENTS.md#exp-015) | Lean-verified evaluation | `scripts/eval/lean_eval_statements.py`, `scripts/eval/probe_nt_pool.py` | Done for the SFT model. 0/200 Lean Workbook number-theory statements; one standard tactic solves 8/200 |
| [EXP-014](docs/EXPERIMENTS.md#exp-014) | Lean SFT on mathlib proofs | `configs/midtrain/qwen_05b_lean_sft_v1.json` | Done. Validation 2/43 theorems at 8 samples; 0/17 number-theory theorems |
| [EXP-013](docs/EXPERIMENTS.md#exp-013) | Controlled RLP comparison | `configs/midtrain/qwen_05b_controlled_rlp.json` | Done. Both arms 0/18 fully correct; final core score 1.056 shuffled vs 0.500 ordered (sign test p = 0.18) |
| [EXP-012](docs/EXPERIMENTS.md#exp-012) | RLP corpus audit | `configs/data/mathematical_rlp_review_v1.json`, `scripts/audit/audit_rlp_corpus.py` | Done. All 612 numeric answers correct. 160/448 training rules lack domain or zero conditions |
| [EXP-011](docs/EXPERIMENTS.md#exp-011) | Local-action pilot | `configs/rl/qwen_05b_local_operator_grpo.json` | Done. Witness accuracy improved |
| [EXP-010](docs/EXPERIMENTS.md#exp-010) | Saved-checkpoint evaluations | `configs/eval/` | Done. No complete-answer gain |
| [EXP-009](docs/EXPERIMENTS.md#exp-009) | Primitive RLP curriculum | `configs/midtrain/qwen_05b_primitive_rlp.json` | Done. Held-out accuracy fell |
| [EXP-008](docs/EXPERIMENTS.md#exp-008) | FP32 RLP rerun | `configs/midtrain/qwen_05b_number_theory_rlp_fp32.json` | Done. No accuracy gain |
| [EXP-007](docs/EXPERIMENTS.md#exp-007) | RLP training + continuation | `configs/pretrain/qwen_05b_number_theory_rlp*.json` | Done. No accuracy gain |
| [EXP-006](docs/EXPERIMENTS.md#exp-006) | Assisted proof completion | `configs/rl/qwen_05b_completion_grpo.json` | Done. One task qualified; no held-out success |
| [EXP-005](docs/EXPERIMENTS.md#exp-005) | Prerequisite curriculum GRPO | `configs/rl/qwen_05b_prerequisite_grpo*.json` | Done. Training skipped: no proof solved |
| [EXP-004](docs/EXPERIMENTS.md#exp-004) | Whole-proof GRPO | `configs/rl/qwen_05b_whole_proof_grpo.json` | Done. No learning signal |
| [EXP-003](docs/EXPERIMENTS.md#exp-003) | Instruct model proof check | `configs/eval/qwen_05b_probe.json`, `scripts/eval/probe_qwen.py` | Done. Lean accepted 0/63 local attempts. A GPU sampling run at four temperatures shows 0/273 in its exported records |
| [EXP-002](docs/EXPERIMENTS.md#exp-002) | Base model probe + GSM8K LoRA | `configs/eval/qwen_05b_probe.json` | Done. No accuracy gain |
| [EXP-001](docs/EXPERIMENTS.md#exp-001) | LeanDojo data subset | `configs/data/leandojo_*.json` | Done. Lean replay done: 5,921 of 8,273 training proofs accepted; task bank v2 keeps 2,150 theorems outside evaluation and dev files |
| [AUD-002](docs/EXPERIMENTS.md#aud-002) | Lean reward and engineering audit | `scripts/eval/check_checker_attacks.py`, `scripts/eval/bench_lean_verifier.py`, `scripts/eval/check_scoring_paths.py` | Scorer adoption appears in the decision log. Attack controls do not establish an exhaustive security guarantee |
| [AUD-001](docs/EXPERIMENTS.md#aud-001) | Proof failures and curriculum inventory | `scripts/audit/classify_proof_failures.py`, `scripts/audit/curriculum_inventory.py` | This is a post hoc diagnostic, not a registered curriculum-effect test |

The full status cells of EXP-024 to EXP-028, and two older status paragraphs, are kept word for word in [README status text before condensing](docs/EXPERIMENTS.md#readme-status-text-before-condensing-20-october-2026).

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.12.
2. Install [elan and Lean](https://lean-lang.org/install/manual/). Lean is separate from the Python environment.
3. Run `uv sync`.
4. Run `uv run pre-commit install`.
5. Run the tests with `uv run python -m unittest discover -s tests`.

Training and inference run on rented AWS GPUs, not on a laptop. Those environments supply `torch`, `transformers`, `trl`, and `tensorboardX`, which `pyproject.toml` does not list.
cpu-angad holds historical working copies only; new jobs do not run there.

On a machine without `torch` and `datasets`, 8 tests fail with `ModuleNotFoundError`.
The Lean integration tests in `tests/test_tactic_rl_integration.py` run only when `TACTIC_RL_ROOT` and `TACTIC_RL_OUTPUT` are set. `TACTIC_RL_ROOT` must hold `lean_env_full/`, `proof_replay_v1/code/checker.py` and the REPL build under `rlvr_verifier/`.

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

## Scripts

Run each script from the repository root.
[scripts/README.md](scripts/README.md) lists every script with its purpose, the experiments that name it and the scripts that import it.
