# Scripts

This folder holds the project's commands and the code they share. Run each script from the repository root, for example:

```bash
uv run python scripts/data/build_nt_benchmark.py --help
```

Scripts that train or sample a model need `torch`, `transformers` and `trl`, which `pyproject.toml` does not list.
Scripts that call Lean need the pinned Linux Lean environment. See [Setup](../README.md#setup).
[configs/README.md](../configs/README.md) maps each config to its experiment.

## Start here

The number-theory runs of 18 and 19 October (EXP-026 to EXP-028) used these steps:

1. Data: fetch the frozen datasets with the steps in the [data handoff](../docs/EXPERIMENTS.md#data-handoff-for-abhinav-20-october-2026-ist). A fresh clone cannot rebuild them, because several builders are private.
2. SFT: [`train/run_sft.py`](train/run_sft.py) runs supervised fine-tuning on Lean 4 proofs.
3. RL: [`train/run_tactic_rl.py`](train/run_tactic_rl.py) trains one action at a time against Lean. It defines `LeanEpisode`, the proof environment. Its `--task-order` option sets the curriculum order.
4. Evaluation: `train/run_tactic_rl.py` also evaluates. `--evaluation-names` selects the DEV theorems, and `--assessment-only` assesses TRAIN theorems without updates. [Section 7 of the data handoff](../docs/EXPERIMENTS.md#7-step-by-step-run-with-this-data-as-the-main-run-did) gives the exact commands.
5. Report figures: [`report/make_report_figures.py`](report/make_report_figures.py).

[`data/build_nt_benchmark.py`](data/build_nt_benchmark.py) built the EXP-025 benchmark releases that the later releases start from.
The private launchers call `scripts/train/run_tactic_rl.py` and `scripts/train/run_sft.py` by path.

## How scripts import each other

The scripts are not an installed package.
Most scripts add sibling folders such as `scripts/data` and `scripts/eval` to `sys.path` and import other scripts by file name.
Some command scripts therefore also hold code that many other scripts import.
Before you move or rename a script, update every script and test that imports it.

The Lean checker module `checker.py` is not in Git.
Scripts that check proofs take its path with `--checker` and load it with `load_checker`.
[Section 4.10 of the data handoff](../docs/EXPERIMENTS.md#410-the-lean-runtime-environment-repl-checker-and-identity) lists its archived copy, with the REPL binary and the Lean environment.

| Shared file | What other scripts import from it | Importing scripts |
| --- | --- | ---: |
| [`data/prepare_grpo_pilot.py`](data/prepare_grpo_pilot.py) | `load_checker`, which loads the Lean checker module named by `--checker` | 34 |
| [`eval/bench_lean_verifier.py`](eval/bench_lean_verifier.py) | `repl_context` and `verify_group`, the shared Lean REPL scoring path | 26 |
| [`eval/probe_nt_pool.py`](eval/probe_nt_pool.py) | `check_candidates`, `lean_unit`, `header` and the proof prompt `INSTRUCTION` | 22 |
| [`eval/probe_start_depth.py`](eval/probe_start_depth.py) | `FORBIDDEN`, the matcher for forbidden code outside comments | 17 |
| [`data/replay_training_proofs.py`](data/replay_training_proofs.py) | `question`, `proof_body`, `direct_lean_call` and `ALLOWED_AXIOMS` | 15 |
| [`train/run_tactic_rl.py`](train/run_tactic_rl.py) | `LeanEpisode`, the Lean proof environment that takes one action at a time, and `render_observation` | 13 |
| [`audit/profile_external_proofs.py`](audit/profile_external_proofs.py) | `tokens`, `statement` and the `NUMBER_THEORY` pattern | 13 |
| [`data/verify_external_proofs.py`](data/verify_external_proofs.py) | `HEADER`, the Lean file header, and `check_batch` | 11 |
| [`train/rl_components/`](train/rl_components/) | task admission and scheduling, the leave-one-out objective and SFT examples | 10 |
| [`data/build_nt_sft_data.py`](data/build_nt_sft_data.py) | `body_of` and `strip_comments` | 10 |
| [`data/build_proof_tasks.py`](data/build_proof_tasks.py) | `top_level_steps` and the target guard `GUARD` | 7 |

## Every script

Purpose is the first line of the script's docstring. Where that line uses an older name for a thing in the names table on the start page in docs/EXPERIMENTS.md, Purpose uses the current name.
Experiments lists the IDs of the catalogue entries and README status rows that name the script. A catalogue line counts for the ID in its entry heading, or for the one ID on the line when the heading names none.
A dash means that no record names an experiment for the script.
Imported by counts the other scripts that import it.

### data/

Build datasets, splits, SFT examples and RL tasks.

| Script | Purpose | Experiments | Imported by |
| --- | --- | --- | ---: |
| [`data/add_premises_sft.py`](data/add_premises_sft.py) | Add the retrieved-lemma section to existing v2 SFT examples. | EXP-028 | 0 |
| [`data/build_answer_tasks.py`](data/build_answer_tasks.py) | Build Lean-checked number-theory answer tasks (formal Q&A) and check every reference answer in Lean. | EXP-024 | 1 |
| [`data/build_dev_set.py`](data/build_dev_set.py) | Build a development set of LeanDojo Benchmark 4 random/val (+ test) theorems for comparing Lean 4 proof models. | – | 0 |
| [`data/build_nt_archive.py`](data/build_nt_archive.py) | Number-theory theorems of the mathlib Archive at the pin, from files compiled at the pin. | – | 1 |
| [`data/build_nt_benchmark.py`](data/build_nt_benchmark.py) | Build a versioned, exploratory number-theory benchmark from the existing corpus. | EXP-025 | 0 |
| [`data/build_nt_hole_tasks.py`](data/build_nt_hole_tasks.py) | RL hole tasks from Lean-checked proofs of the held-out theorems, plus one whole-statement task per RL pool statement. | EXP-017 | 3 |
| [`data/build_nt_knowledge.py`](data/build_nt_knowledge.py) | Number-theory knowledge items from mathlib at the pin: doc comment (informal) + Lean statement + proof as written. | – | 2 |
| [`data/build_nt_pool.py`](data/build_nt_pool.py) | Build the number-theory statement pool for RL from Lean-checked Lean Workbook statements. | EXP-016 | 4 |
| [`data/build_nt_sft_data.py`](data/build_nt_sft_data.py) | SFT examples from Lean-checked external proofs of number-theory pool statements (train split only). | EXP-017 | 10 |
| [`data/build_nt_splits.py`](data/build_nt_splits.py) | Name lists of the midsem plan (artifacts/notes/2026-10-10_midsem_plan.md): what trains, what is held out, what is scored. | EXP-017 | 0 |
| [`data/build_nt_states.py`](data/build_nt_states.py) | Initial goal states (Lean trace_state) for one split of the number-theory statement pool. | EXP-016 | 0 |
| [`data/build_nt_tactic_aug.py`](data/build_nt_tactic_aug.py) | Tactic-level augmentation: new Lean-checked proofs from the accepted SFT proofs of sft_problems (train split only). | – | 0 |
| [`data/build_premise_corpus.py`](data/build_premise_corpus.py) | Dump Mathlib theorem names and statements from the pinned Lean environment as a premise corpus. | EXP-028 | 0 |
| [`data/build_proof_tasks.py`](data/build_proof_tasks.py) | Build Lean-checked proof tasks from the original mathlib proof text, with cut points. | – | 7 |
| [`data/build_rft_data.py`](data/build_rft_data.py) | SFT examples for the rejection-sampling fine-tuning (RFT) baseline from Lean-accepted map attempts (train split only). | EXP-019 | 0 |
| [`data/build_search_sft.py`](data/build_search_sft.py) | Mine SFT examples from a step-search assessment of TRAIN theorems (expert iteration and error repair). | EXP-028 | 0 |
| [`data/build_sft_data.py`](data/build_sft_data.py) | Build Lean 4 SFT data from LeanDojo Benchmark 4 training proofs at the pinned mathlib commit. | EXP-014 | 0 |
| [`data/build_syntax_sft.py`](data/build_syntax_sft.py) | Syntax SFT from existing replay-accepted bank proofs, at the project's Lean pin. | EXP-017 | 0 |
| [`data/build_task_bank.py`](data/build_task_bank.py) | Merge Lean-checked proof tasks into one task bank with curriculum metadata. | – | 1 |
| [`data/build_v21_sft.py`](data/build_v21_sft.py) | SFT examples in the environment version 2.1 format, from existing Lean-checked number-theory proofs. | – | 0 |
| [`data/export_task_pool.py`](data/export_task_pool.py) | Export a larger training-theorem pool from LeanDojo Benchmark 4 for curriculum experiments. | – | 0 |
| [`data/import_extra_nt.py`](data/import_extra_nt.py) | Import extra public Lean 4 number-theory statements and proofs, filter them like nt_pool_v2, and Lean-check them. | – | 0 |
| [`data/mathematical_rlp_data.py`](data/mathematical_rlp_data.py) | Bounded, authored records and matched information views for mathematical RLP. | EXP-013 | 1 |
| [`data/merge_sft_rows.py`](data/merge_sft_rows.py) | Assemble an SFT data directory from base SFT examples plus optional extra TRAIN SFT examples, within the SFT token limits. | – | 0 |
| [`data/prepare_grpo_pilot.py`](data/prepare_grpo_pilot.py) | Prepare gold-free whole-proof prompts on cpu-angad. | – | 34 |
| [`data/prepare_leandojo.py`](data/prepare_leandojo.py) | Prepare a project-defined Nat subset of LeanDojo's official random splits. | EXP-001 | 3 |
| [`data/prepare_nt_tactic_expansion.py`](data/prepare_nt_tactic_expansion.py) | Prepare frozen TRAIN-source theorems and available prefixes, with separate DEV theorems. | – | 0 |
| [`data/prepare_prerequisite_curriculum.py`](data/prepare_prerequisite_curriculum.py) | Prepare a small authored curriculum with the existing pinned checker. | EXP-005 | 0 |
| [`data/prepare_rlp_corpus.py`](data/prepare_rlp_corpus.py) | Prepare RLP acquisition text on cpu-angad, without evaluation solutions. | – | 0 |
| [`data/prepare_short_proof_subset.py`](data/prepare_short_proof_subset.py) | Select short original LeanDojo training proofs without exposing hidden suffixes. | – | 0 |
| [`data/prepare_tactic_curriculum.py`](data/prepare_tactic_curriculum.py) | Freeze existing training proof starts and paired starting-state schedules. | – | 1 |
| [`data/replay_training_proofs.py`](data/replay_training_proofs.py) | Replay linear LeanDojo training proofs with the pinned Lean checker on cpu-angad. | – | 15 |
| [`data/retrace_v21_states.py`](data/retrace_v21_states.py) | Re-derive each v2.1 SFT prompt's goal through the RL environment and keep only SFT examples whose target RL can run. | – | 0 |
| [`data/verify_external_proofs.py`](data/verify_external_proofs.py) | Check Lean Workbook statements and proofs (Lean Workbook and Goedel-Prover releases) at our pinned Lean. | EXP-016, EXP-024, EXP-025, EXP-029 | 11 |

### data/nt_benchmark/

Package used by `data/build_nt_benchmark.py` (EXP-025).

| Script | Purpose | Experiments | Imported by |
| --- | --- | --- | ---: |
| [`data/nt_benchmark/__init__.py`](data/nt_benchmark/__init__.py) | Number-theory benchmark components. Legacy script commands remain supported. | EXP-025 | 2 |
| [`data/nt_benchmark/artifacts.py`](data/nt_benchmark/artifacts.py) | Proof profiling and split-safe exports consumed by SFT and RL runners. | – | 3 |
| [`data/nt_benchmark/pipeline.py`](data/nt_benchmark/pipeline.py) | Compose replaceable selection, verification, and export components. | – | 1 |
| [`data/nt_benchmark/selection.py`](data/nt_benchmark/selection.py) | Deterministic selection and duplicate-aware split assignment. | – | 3 |
| [`data/nt_benchmark/storage.py`](data/nt_benchmark/storage.py) | Bounded JSONL storage and source receipts for benchmark builds. | – | 5 |
| [`data/nt_benchmark/verification.py`](data/nt_benchmark/verification.py) | Pinned Lean replay, goal extraction, and complete baseline screening. | – | 2 |

### train/

Training entry points and the Lean environment.

| Script | Purpose | Experiments | Imported by |
| --- | --- | --- | ---: |
| [`train/band_sampler.py`](train/band_sampler.py) | Arm C task sampler: Thompson sampling toward families that give mixed groups. | – | 1 |
| [`train/candidate_policy.py`](train/candidate_policy.py) | Shared categorical policy over a frozen menu of Lean-admitted tactics. | – | 2 |
| [`train/lean_ppo.py`](train/lean_ppo.py) | Pinned TRL PPO with the existing binary Lean scorer, not a learned reward model. | EXP-022 | 2 |
| [`train/local_operator_training.py`](train/local_operator_training.py) | Local-action mode for run_grpo_pilot, using the same pinned TRL optimizer. | EXP-011 | 1 |
| [`train/run_candidate_ppo.py`](train/run_candidate_ppo.py) | PPO-Clip updates for complete Lean candidate-selection attempts. | – | 1 |
| [`train/run_candidate_reinforce.py`](train/run_candidate_reinforce.py) | Monte Carlo REINFORCE over the shared frozen candidate policy. | – | 0 |
| [`train/run_grpo_pilot.py`](train/run_grpo_pilot.py) | Thin TRL GRPO wrapper for the separate whole-proof pilot, one L40S. | EXP-004, EXP-005, EXP-006 | 0 |
| [`train/run_interleaved_training.py`](train/run_interleaved_training.py) | Alternate existing SFT, hidden-target RLP, and Lean GRPO runners. | EXP-020 | 1 |
| [`train/run_lean_grpo.py`](train/run_lean_grpo.py) | GRPO on Lean tasks with a Lean-verified binary reward, and cost records per optimizer step. | EXP-019 | 5 |
| [`train/run_rlp_pilot.py`](train/run_rlp_pilot.py) | RLP acquisition through TRL, one L40S, no supervised proof objective. | EXP-007, EXP-008, EXP-009, EXP-013, EXP-021 | 1 |
| [`train/run_sft.py`](train/run_sft.py) | Supervised fine-tuning on Lean 4 proofs with TRL 0.24 (prompt-completion, loss on the completion only). | EXP-014, EXP-018, EXP-023, EXP-024, EXP-025, EXP-027 | 0 |
| [`train/run_tactic_ppo.py`](train/run_tactic_ppo.py) | Pinned library PPO for one tactic block, with the REINFORCE Lean attempt contract. | EXP-023 | 0 |
| [`train/run_tactic_rl.py`](train/run_tactic_rl.py) | On-policy tactic RL with Lean transitions and a leave-one-out baseline. | EXP-019, EXP-020, EXP-021, EXP-023, EXP-024, EXP-026, EXP-027, EXP-028, EXP-029, EXP-030 | 13 |

### train/rl_components/

Package of training components that do not import model libraries.

| Script | Purpose | Experiments | Imported by |
| --- | --- | --- | ---: |
| [`train/rl_components/__init__.py`](train/rl_components/__init__.py) | Reusable training components. Importing this package does not load model libraries. | EXP-025 | 10 |
| [`train/rl_components/objectives.py`](train/rl_components/objectives.py) | The existing leave-one-out estimator and streamed policy-gradient update. | – | 3 |
| [`train/rl_components/premises.py`](train/rl_components/premises.py) | BM25 premise retrieval over library lemmas, independent of Lean processes. | – | 2 |
| [`train/rl_components/proofs.py`](train/rl_components/proofs.py) | Lean action serialization without verifier or trainer imports. | – | 2 |
| [`train/rl_components/sft.py`](train/rl_components/sft.py) | SFT message conversion without importing datasets, Torch, or TRL. | – | 4 |
| [`train/rl_components/tasks.py`](train/rl_components/tasks.py) | Task admission and curriculum scheduling, independent of Lean processes. | EXP-026 | 1 |

### eval/

Evaluations, baselines, probes and scoring checks.

| Script | Purpose | Experiments | Imported by |
| --- | --- | --- | ---: |
| [`eval/bench_lean_verifier.py`](eval/bench_lean_verifier.py) | Compare three Lean verifier modes for a GRPO reward on the same proof attempts. No model runs. | AUD-002 | 26 |
| [`eval/check_checker_attacks.py`](eval/check_checker_attacks.py) | Adversarial continuations against the shared Lean scorer (verify_group): every attack must be rejected. | AUD-002, EXP-023 | 0 |
| [`eval/check_inhouse_controls.py`](eval/check_inhouse_controls.py) | Lean integration controls for build_nt_tactic_aug.py, build_nt_knowledge.py, and build_nt_archive.py (not unittest). | – | 0 |
| [`eval/check_rollout_backends.py`](eval/check_rollout_backends.py) | GPU check that batched HF and vLLM sampling match single-prompt HF sampling of the tactic policy. | EXP-029 | 0 |
| [`eval/check_scoring_paths.py`](eval/check_scoring_paths.py) | Integration test: score the same candidates through the probe, map, and trainer paths and require equal verdicts. | AUD-002 | 0 |
| [`eval/check_task_scoring.py`](eval/check_task_scoring.py) | Integration test: RL tasks score the same in the pass-rate map (probe_nt_pool.py) and in the GRPO reward. | – | 0 |
| [`eval/check_vllm_equivalence.py`](eval/check_vllm_equivalence.py) | vLLM-versus-HF equivalence test for probe_nt_pool.py (GPU; first step of the GPU lease; exit 1 on failure). | – | 0 |
| [`eval/curriculum_metrics.py`](eval/curriculum_metrics.py) | Curriculum metrics from run_tactic_rl.py output directories (assessment or training). | EXP-023, EXP-024 | 0 |
| [`eval/demo_episode.py`](eval/demo_episode.py) | Screen or demonstrate specified theorems in Lean environment version 2.1. | – | 0 |
| [`eval/elaborate_statements.py`](eval/elaborate_statements.py) | Elaborate theorem statements under the pinned Lean and mathlib, with `sorry` as the proof. No proof is checked. | EXP-029 | 0 |
| [`eval/eval_answer_tasks.py`](eval/eval_answer_tasks.py) | Pass rate of frozen models on Lean-checked answer tasks, next to two no-model baselines. | – | 0 |
| [`eval/evaluate_acquisition.py`](eval/evaluate_acquisition.py) | Evaluation only: recognition, recall, and elementary number-theory application. | EXP-010 | 0 |
| [`eval/export_tactic_proofs.py`](eval/export_tactic_proofs.py) | Export complete model proof paths and replay with the existing strict scorer. | EXP-023, EXP-026, EXP-027 | 0 |
| [`eval/false_hypothesis_sweep.py`](eval/false_hypothesis_sweep.py) | False-hypothesis sweep: which statements of the number-theory pool have hypotheses that prove False. | EXP-029 | 0 |
| [`eval/grade_mathematical_responses.py`](eval/grade_mathematical_responses.py) | Prepare blinded review packets and recount independent mathematical grades. | EXP-013 | 0 |
| [`eval/lean_eval_statements.py`](eval/lean_eval_statements.py) | Lean-checked whole-proof evaluation of frozen models on curriculum-validation theorems. | EXP-015 | 2 |
| [`eval/nearest_proof_baseline.py`](eval/nearest_proof_baseline.py) | Nearest-proof baseline: how many held-out statements a copied train proof proves, with no learning. | – | 0 |
| [`eval/pass_rate_map.py`](eval/pass_rate_map.py) | Pass-rate map of one checkpoint over the task bank: a group of attempts per unit, by steps remaining. | EXP-019 | 0 |
| [`eval/probe_nt_pool.py`](eval/probe_nt_pool.py) | Pass rates of frozen models and a single-tactic baseline on sampled statements of the number-theory pool. | EXP-015 | 22 |
| [`eval/probe_qwen.py`](eval/probe_qwen.py) | Bounded, unquantized MLX math probe. Lean statements remain unverified. | EXP-002, EXP-003 | 0 |
| [`eval/probe_start_depth.py`](eval/probe_start_depth.py) | Measure how often a frozen model finishes a training proof from a later starting point. | – | 17 |
| [`eval/probe_tactic_burdens.py`](eval/probe_tactic_burdens.py) | Frozen prompt/premise diagnostic. No optimizer or generated-code execution. | – | 0 |
| [`eval/regrade_controlled_rlp.py`](eval/regrade_controlled_rlp.py) | Blinded regrade of both arms of the controlled RLP study (Experiment 13); no model calls. | EXP-013 | 0 |
| [`eval/rescore_accepted.py`](eval/rescore_accepted.py) | Re-score earlier accepted proofs with the current shared scorer and scan them for the checker holes of 2026-10-10. | – | 0 |
| [`eval/search_tactics.py`](eval/search_tactics.py) | Best-first tactic search with a frozen model on curriculum-validation theorems (Lean REPL tactic mode). | EXP-015 | 1 |
| [`eval/single_tactic_baseline.py`](eval/single_tactic_baseline.py) | No-model baseline: each fixed tactic alone as the whole proof of each curriculum-validation theorem. | EXP-015, EXP-024 | 2 |
| [`eval/verify_local_operator.py`](eval/verify_local_operator.py) | Verify supplied-knowledge witness expressions with the existing Lean checker. | EXP-011 | 1 |

### eval/proof_export/

Package used by `eval/export_tactic_proofs.py`.

| Script | Purpose | Experiments | Imported by |
| --- | --- | --- | ---: |
| [`eval/proof_export/__init__.py`](eval/proof_export/__init__.py) | Reusable proof replay and attempt reconstruction components. | EXP-025 | 1 |
| [`eval/proof_export/exporter.py`](eval/proof_export/exporter.py) | Re-admit saved tasks, replay complete proofs, and serialize export artifacts. | – | 1 |
| [`eval/proof_export/trajectory.py`](eval/proof_export/trajectory.py) | Reconstruct the exact retained proof from a complete attempt. | – | 2 |

### audit/

Read-only audits, leakage checks and held-out screens.

| Script | Purpose | Experiments | Imported by |
| --- | --- | --- | ---: |
| [`audit/audit_primitive_rlp.py`](audit/audit_primitive_rlp.py) | Audit saved primitive RLP records on CPU; no model inference. | EXP-009 | 0 |
| [`audit/audit_proofs.py`](audit/audit_proofs.py) | Stream existing GRPO failures on cpu-angad; no model or Lean execution. | – | 0 |
| [`audit/audit_rlp_corpus.py`](audit/audit_rlp_corpus.py) | Audit immutable structured RLP exports on CPU, without model inference. | EXP-012 | 0 |
| [`audit/audit_rlp_updates.py`](audit/audit_rlp_updates.py) | Compare saved RLP weights and isolate BF16 update rounding on CPU. | EXP-008 | 0 |
| [`audit/automation_chain_screen.py`](audit/automation_chain_screen.py) | Automation-chain screen: which held-out statements a short, statement-independent automation chain proves. | EXP-029 | 0 |
| [`audit/check_leakage.py`](audit/check_leakage.py) | Near-duplicate checks of new training items against held-out statements, with the thresholds of nt_splits_v3. | EXP-017 | 4 |
| [`audit/check_split_leakage.py`](audit/check_split_leakage.py) | Check the training side of a split for leakage into validation, test, and miniF2F. | EXP-017 | 0 |
| [`audit/classify_proof_failures.py`](audit/classify_proof_failures.py) | Classify the Lean errors in a saved whole-proof diagnostic; no model or Lean execution. | AUD-001, EXP-003, EXP-023 | 1 |
| [`audit/curriculum_inventory.py`](audit/curriculum_inventory.py) | Count start points for a reverse proof-state curriculum in a LeanDojo export; read-only. | AUD-001 | 1 |
| [`audit/heldout_screens.py`](audit/heldout_screens.py) | Pure helpers shared by the held-out screens: pool loading, statement parsing, similarity, bands and strata. | – | 5 |
| [`audit/number_theory_priors.py`](audit/number_theory_priors.py) | Rank the prior knowledge that mathlib number-theory proofs use, from LeanDojo premise annotations. | – | 1 |
| [`audit/profile_external_proofs.py`](audit/profile_external_proofs.py) | Profile the Lean-checked external proofs (verify_external_proofs.py output) before any use in training. | EXP-024 | 13 |
| [`audit/stratify_heldout.py`](audit/stratify_heldout.py) | Join the three held-out screens into one stratum per statement, and count proved theorems per stratum and arm. | EXP-029 | 0 |
| [`audit/train_similarity_strata.py`](audit/train_similarity_strata.py) | TRAIN-similarity screen: how close each held-out statement is to a TRAIN statement, with an optional copy baseline. | EXP-029 | 0 |

### monitor/

Run monitoring.

| Script | Purpose | Experiments | Imported by |
| --- | --- | --- | ---: |
| [`monitor/track_tensorboard.py`](monitor/track_tensorboard.py) | Display the frozen-policy pilot's S3 records in TensorBoard. | – | 0 |

### report/

Figures for the report.

| Script | Purpose | Experiments | Imported by |
| --- | --- | --- | ---: |
| [`report/make_report_figures.py`](report/make_report_figures.py) | Draw the data figures of docs/tex/project_report.tex from recorded counts. | – | 0 |
