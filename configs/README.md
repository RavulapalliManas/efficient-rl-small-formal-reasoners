# Configs

Each config holds the frozen settings of one data build, evaluation or training run.
Changed settings go into a new versioned file, for example `qwen_05b_interleaved_training_v1.json` to `_v3.json`.
[scripts/README.md](../scripts/README.md) describes the scripts that read these files.

The run configs of the 18 and 19 October leases (EXP-026 to EXP-028) are private launch inputs, not files in this folder.
[Section 7 of the data handoff](../docs/EXPERIMENTS.md#7-step-by-step-run-with-this-data-as-the-main-run-did) lists the settings of the main-run configs.
[`checkpoints.json`](checkpoints.json) is the download index of the published checkpoints. See [Checkpoints](../README.md#checkpoints).

## Every config

Purpose is the first sentence of the config's `purpose` field, or a dash when the config has none. Where that sentence uses an older name for a thing in the names table on the start page in docs/EXPERIMENTS.md, Purpose uses the current name.
Experiment comes from the source that the Source column names, in this order of preference:

1. The config's own `experiment_id` or `experiment` field.
2. The prefix of its `run_id` field, for example `exp019-` for EXP-019.
3. A catalogue entry or a README status row that names the config. A catalogue line counts for the ID in its entry heading, or for the one ID on the line when the heading names none.
4. A pattern in such an entry or row, for example `configs/rl/qwen_05b_prerequisite_grpo*.json`. A pattern counts only for the configs that Git tracked when the pattern was written.

`not recorded` means that no record names an experiment for the config.

### data/

Inputs of dataset builds and selections.

| Config | Purpose | Experiment | Source |
| --- | --- | --- | --- |
| [`data/highschool_prerequisites_v1.json`](data/highschool_prerequisites_v1.json) | – | EXP-019 | declared `experiment_id` |
| [`data/leandojo_nat_v1.json`](data/leandojo_nat_v1.json) | – | EXP-001 | README pattern and catalogue pattern |
| [`data/leandojo_number_theory_v2.json`](data/leandojo_number_theory_v2.json) | – | EXP-001 | README pattern and catalogue pattern |
| [`data/leandojo_short_proofs.json`](data/leandojo_short_proofs.json) | Original LeanDojo TRAIN short proofs for binary-reward interactive RL preparation. | not recorded | not recorded |
| [`data/local_operators_v1.json`](data/local_operators_v1.json) | – | not recorded | not recorded |
| [`data/mathematical_rlp_review_v1.json`](data/mathematical_rlp_review_v1.json) | – | EXP-012 | catalogue and README |
| [`data/nt_benchmark_v1.json`](data/nt_benchmark_v1.json) | Bounded exploratory benchmark build from the existing broader corpus. | EXP-025 | declared `experiment` |
| [`data/nt_benchmark_v2.json`](data/nt_benchmark_v2.json) | Expanded exploratory SFT/RL dataset from the existing broader corpus. | EXP-025 | declared `experiment` |
| [`data/nt_expanded_tactic_v1.json`](data/nt_expanded_tactic_v1.json) | Freeze broader TRAIN-source theorems and separate DEV theorems before model sampling. | EXP-023 | declared `experiment_id` |
| [`data/nt_sft_recovery_v1.json`](data/nt_sft_recovery_v1.json) | Separate recovery-augmented action dataset; no active pilot change or model launch. | not recorded | not recorded |
| [`data/nt_validation_v1.json`](data/nt_validation_v1.json) | Held-out selection set for EXP-028 screening. | EXP-028 | declared `experiment` |
| [`data/undergrad_nt_v1.json`](data/undergrad_nt_v1.json) | – | EXP-024 | declared `experiment` |

### eval/

Settings of evaluations, probes and grading.

| Config | Purpose | Experiment | Source |
| --- | --- | --- | --- |
| [`eval/acquisition_probe_v1.json`](eval/acquisition_probe_v1.json) | – | EXP-010 | catalogue |
| [`eval/decomposition_grading_v2.json`](eval/decomposition_grading_v2.json) | User-requested exploratory regrading of all checkpoints with partial credit and separate ancillary errors. | not recorded | not recorded |
| [`eval/decomposition_notation_check.json`](eval/decomposition_notation_check.json) | – | not recorded | not recorded |
| [`eval/decomposition_probe_v2.json`](eval/decomposition_probe_v2.json) | – | EXP-010 | catalogue |
| [`eval/knowledge_inventory_v1.json`](eval/knowledge_inventory_v1.json) | – | EXP-010 | catalogue |
| [`eval/local_operator_probe_v1.json`](eval/local_operator_probe_v1.json) | – | not recorded | not recorded |
| [`eval/mathematical_grader_calibration_v1.json`](eval/mathematical_grader_calibration_v1.json) | – | not recorded | not recorded |
| [`eval/mathematical_grading_v1.json`](eval/mathematical_grading_v1.json) | Development calibration and reference-informed mathematical admission/evaluation. | not recorded | not recorded |
| [`eval/qwen_05b_curriculum_stage_probe_v1prompt.json`](eval/qwen_05b_curriculum_stage_probe_v1prompt.json) | EXP-023 stage-achievability probe. | EXP-023 | declared `experiment_id` |
| [`eval/qwen_05b_curriculum_stage_probe_v21prompt.json`](eval/qwen_05b_curriculum_stage_probe_v21prompt.json) | EXP-023 stage-achievability probe. | EXP-023 | declared `experiment_id` |
| [`eval/qwen_05b_heldout_parent_v1prompt.json`](eval/qwen_05b_heldout_parent_v1prompt.json) | EXP-023 held-out baseline. | EXP-023 | declared `experiment_id` |
| [`eval/qwen_05b_leandojo_short_calibration.json`](eval/qwen_05b_leandojo_short_calibration.json) | Frozen native LeanDojo calibration. | EXP-019 | `run_id` prefix |
| [`eval/qwen_05b_nt_expanded_calibration.json`](eval/qwen_05b_nt_expanded_calibration.json) | Frozen training-only parent calibration. | EXP-023 | declared `experiment_id` |
| [`eval/qwen_05b_nt_expanded_dev.json`](eval/qwen_05b_nt_expanded_dev.json) | Frozen original broader DEV theorems, with --evaluation-names and no optimizer. | EXP-023 | declared `experiment_id` |
| [`eval/qwen_05b_nt_retention_dev.json`](eval/qwen_05b_nt_retention_dev.json) | Frozen elementary DEV retention panel from the completed mixed RL study. | EXP-023 | declared `experiment_id` |
| [`eval/qwen_05b_prerequisite_sft_dev_v1.json`](eval/qwen_05b_prerequisite_sft_dev_v1.json) | Matched optimizer-disabled pilot evaluation of the fresh parent and final interactive SFT checkpoint on all retained release_v3 DEV statements. | EXP-026 | declared `experiment_id` |
| [`eval/qwen_05b_prerequisite_sft_dev_v2.json`](eval/qwen_05b_prerequisite_sft_dev_v2.json) | Matched optimizer-disabled pilot evaluation of the fresh parent and final interactive SFT checkpoint on all retained release_v3 DEV statements. | EXP-026 | declared `experiment_id` |
| [`eval/qwen_05b_probe.json`](eval/qwen_05b_probe.json) | – | EXP-002, EXP-003 | catalogue and README |
| [`eval/qwen_05b_tactic_burdens.json`](eval/qwen_05b_tactic_burdens.json) | – | EXP-019 | `run_id` prefix |
| [`eval/qwen_05b_tactic_comparison_dev.json`](eval/qwen_05b_tactic_comparison_dev.json) | Matched unassisted DEV evaluation for EXP019 comparison; no optimizer. | EXP-019 | `run_id` prefix |
| [`eval/qwen_05b_tactic_curriculum_signal.json`](eval/qwen_05b_tactic_curriculum_signal.json) | Frozen training-state signal assessment. | EXP-019 | `run_id` prefix |
| [`eval/qwen_05b_tactic_dev.json`](eval/qwen_05b_tactic_dev.json) | Frozen DEV interactive proof evaluation; no optimizer; statements without proof prefixes | EXP-019 | `run_id` prefix |
| [`eval/qwen_05b_tactic_fixed_short_dev.json`](eval/qwen_05b_tactic_fixed_short_dev.json) | Paired fresh evaluation on the three RL-parent-disjoint validation elementary theorems. | EXP-019 | `run_id` prefix |
| [`eval/qwen_05b_tactic_fixed_short_prescreen.json`](eval/qwen_05b_tactic_fixed_short_prescreen.json) | Optimizer-disabled repeated frozen sampling before fixed-task learning. | EXP-019 | `run_id` prefix |

### pretrain/

RLP training on number-theory text.

| Config | Purpose | Experiment | Source |
| --- | --- | --- | --- |
| [`pretrain/qwen_05b_number_theory_rlp.json`](pretrain/qwen_05b_number_theory_rlp.json) | – | EXP-007 | README pattern and catalogue pattern |
| [`pretrain/qwen_05b_number_theory_rlp_continuation.json`](pretrain/qwen_05b_number_theory_rlp_continuation.json) | – | EXP-007 | README pattern and catalogue pattern |

### midtrain/

SFT, RLP and interleaved training runs.

| Config | Purpose | Experiment | Source |
| --- | --- | --- | --- |
| [`midtrain/qwen_05b_controlled_rlp.json`](midtrain/qwen_05b_controlled_rlp.json) | – | EXP-013 | catalogue and README |
| [`midtrain/qwen_05b_gsm8k_lora.yaml`](midtrain/qwen_05b_gsm8k_lora.yaml) | – | EXP-002 | catalogue |
| [`midtrain/qwen_05b_gsm8k_rlp_preparation.json`](midtrain/qwen_05b_gsm8k_rlp_preparation.json) | Basic arithmetic preparation using official GSM8K train only; keep solutions private; no official test access | EXP-019 | `run_id` prefix |
| [`midtrain/qwen_05b_highschool_rlp_preparation.json`](midtrain/qwen_05b_highschool_rlp_preparation.json) | – | EXP-019 | `run_id` prefix |
| [`midtrain/qwen_05b_interleaved_training_v1.json`](midtrain/qwen_05b_interleaved_training_v1.json) | Exploratory alternating SFT/RLP and SFT/Lean-GRPO branches from a common SFT checkpoint; different training data, not an algorithm-only comparison | EXP-020 | declared `experiment_id` |
| [`midtrain/qwen_05b_interleaved_training_v2.json`](midtrain/qwen_05b_interleaved_training_v2.json) | Exploratory alternating SFT/RLP and SFT/Lean-GRPO branches from a common SFT checkpoint; different training data, not an algorithm-only comparison | EXP-020 | declared `experiment_id` |
| [`midtrain/qwen_05b_interleaved_training_v3.json`](midtrain/qwen_05b_interleaved_training_v3.json) | Exploratory alternating SFT/RLP and SFT/Lean-GRPO branches from a common SFT checkpoint; different training data, not an algorithm-only comparison | EXP-020 | declared `experiment_id` |
| [`midtrain/qwen_05b_lean_sft_v1.json`](midtrain/qwen_05b_lean_sft_v1.json) | Lean 4 supervised fine-tuning before expert iteration and GRPO; teaches Lean 4 syntax and tactic use | EXP-014 | catalogue and README |
| [`midtrain/qwen_05b_monday_recovery.json`](midtrain/qwen_05b_monday_recovery.json) | Exploratory Monday recovery: train-only verified proof distillation, supervised control, then KL-regularized Lean GRPO. | EXP-021 | declared `experiment_id` |
| [`midtrain/qwen_05b_nt_expanded_warmstart.json`](midtrain/qwen_05b_nt_expanded_warmstart.json) | Conditional supervised warmstart on accepted TRAIN continuations with exact STEP prompts and raw tactic-block targets. | EXP-023 | declared `experiment_id` |
| [`midtrain/qwen_05b_nt_interleaved_sft_v1.json`](midtrain/qwen_05b_nt_interleaved_sft_v1.json) | Number-theory proof SFT with informal comments and tactic variants. | EXP-018 | catalogue and README |
| [`midtrain/qwen_05b_number_theory_rlp_fp32.json`](midtrain/qwen_05b_number_theory_rlp_fp32.json) | – | EXP-008 | catalogue and README |
| [`midtrain/qwen_05b_prerequisite_interactive_sft_v1.json`](midtrain/qwen_05b_prerequisite_interactive_sft_v1.json) | Initial interactive number-theory SFT pilot on the checked prerequisite action release. | EXP-026 | declared `experiment_id` |
| [`midtrain/qwen_05b_prerequisite_interactive_sft_v2.json`](midtrain/qwen_05b_prerequisite_interactive_sft_v2.json) | Initial interactive number-theory SFT pilot on the checked prerequisite action release. | EXP-026 | declared `experiment_id` |
| [`midtrain/qwen_05b_primitive_rlp.json`](midtrain/qwen_05b_primitive_rlp.json) | – | EXP-009 | catalogue and README |
| [`midtrain/qwen_05b_tactic_continued_sft_comparison.json`](midtrain/qwen_05b_tactic_continued_sft_comparison.json) | Exploratory continued-SFT candidate for EXP-019. | EXP-019 | declared `experiment_id` |
| [`midtrain/qwen_05b_v21_warmstart.json`](midtrain/qwen_05b_v21_warmstart.json) | Format warmstart for environment version 2.1: teach the parent to answer the v2.1 observation with one ```lean fence of up to 4 tactics that ends at the closing fence. | EXP-023 | declared `experiment_id` |

### rl/

RL runs and the candidate-action catalogue.

| Config | Purpose | Experiment | Source |
| --- | --- | --- | --- |
| [`rl/lean_candidate_catalogue_v1.json`](rl/lean_candidate_catalogue_v1.json) | – | not recorded | not recorded |
| [`rl/qwen_05b_candidate_frozen_lm_v1.json`](rl/qwen_05b_candidate_frozen_lm_v1.json) | Prospective no-update baseline for matched candidate-policy comparison. | EXP-026 | declared `experiment_id` |
| [`rl/qwen_05b_candidate_ppo_v1.json`](rl/qwen_05b_candidate_ppo_v1.json) | Prospective matched candidate-policy pilot. | EXP-026 | declared `experiment_id` |
| [`rl/qwen_05b_candidate_reinforce_v1.json`](rl/qwen_05b_candidate_reinforce_v1.json) | Prospective matched candidate-policy pilot. | EXP-026 | declared `experiment_id` |
| [`rl/qwen_05b_candidate_uniform_v1.json`](rl/qwen_05b_candidate_uniform_v1.json) | Prospective no-update baseline for matched candidate-policy comparison. | EXP-026 | declared `experiment_id` |
| [`rl/qwen_05b_completion_grpo.json`](rl/qwen_05b_completion_grpo.json) | – | EXP-006 | catalogue and README |
| [`rl/qwen_05b_lean_grpo_comparison.json`](rl/qwen_05b_lean_grpo_comparison.json) | EXP-022 prospective PPO versus GRPO pilot. | EXP-022 | README pattern and catalogue pattern |
| [`rl/qwen_05b_lean_grpo_smoke.json`](rl/qwen_05b_lean_grpo_smoke.json) | CPU smoke test of scripts/train/run_lean_grpo.py: the pipeline runs and records costs; not a result | EXP-019 | catalogue |
| [`rl/qwen_05b_lean_ppo_comparison.json`](rl/qwen_05b_lean_ppo_comparison.json) | EXP-022 prospective PPO versus GRPO pilot. | EXP-022 | README pattern and catalogue pattern |
| [`rl/qwen_05b_local_operator_grpo.json`](rl/qwen_05b_local_operator_grpo.json) | – | EXP-011 | catalogue and README |
| [`rl/qwen_05b_nt_feedback_ordered.json`](rl/qwen_05b_nt_feedback_ordered.json) | Exploratory interactive reward-by-curriculum comparison. | EXP-023 | declared `experiment_id` |
| [`rl/qwen_05b_nt_feedback_shuffled.json`](rl/qwen_05b_nt_feedback_shuffled.json) | Exploratory interactive reward-by-curriculum comparison. | EXP-023 | declared `experiment_id` |
| [`rl/qwen_05b_nt_one_action_ppo.json`](rl/qwen_05b_nt_one_action_ppo.json) | Matched one-action PPO comparison with REINFORCE. | EXP-023 | declared `experiment_id` |
| [`rl/qwen_05b_nt_one_action_reinforce.json`](rl/qwen_05b_nt_one_action_reinforce.json) | Matched one-action REINFORCE comparison with PPO. | EXP-023 | declared `experiment_id` |
| [`rl/qwen_05b_nt_terminal_ordered.json`](rl/qwen_05b_nt_terminal_ordered.json) | Exploratory interactive reward-by-curriculum comparison. | EXP-023 | declared `experiment_id` |
| [`rl/qwen_05b_nt_terminal_shuffled.json`](rl/qwen_05b_nt_terminal_shuffled.json) | Exploratory interactive reward-by-curriculum comparison. | EXP-023 | declared `experiment_id` |
| [`rl/qwen_05b_prerequisite_grpo.json`](rl/qwen_05b_prerequisite_grpo.json) | – | EXP-005 | README pattern and catalogue pattern |
| [`rl/qwen_05b_prerequisite_grpo_syntax.json`](rl/qwen_05b_prerequisite_grpo_syntax.json) | – | EXP-005 | README pattern and catalogue pattern |
| [`rl/qwen_05b_tactic_curriculum_reverse.json`](rl/qwen_05b_tactic_curriculum_reverse.json) | Preparation candidate. | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_tactic_curriculum_uniform.json`](rl/qwen_05b_tactic_curriculum_uniform.json) | Preparation candidate. | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_tactic_fixed_short.json`](rl/qwen_05b_tactic_fixed_short.json) | Preparation candidate for fixed short-task learning, before curriculum comparisons. | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_tactic_mixed_followup.json`](rl/qwen_05b_tactic_mixed_followup.json) | Exploratory midterm RL follow-up. | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_tactic_native_parity.json`](rl/qwen_05b_tactic_native_parity.json) | Midterm native Lean RL pilot. | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_tactic_rl_feedback.json`](rl/qwen_05b_tactic_rl_feedback.json) | Exploratory local Lean diagnostic feedback; compare with terminal-only RL from the same checkpoint and tasks. | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_tactic_rl_feedback_comparison.json`](rl/qwen_05b_tactic_rl_feedback_comparison.json) | Exploratory Lean-in-the-loop feedback versus terminal reward. | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_tactic_rl_pilot.json`](rl/qwen_05b_tactic_rl_pilot.json) | Exploratory interactive tactic RL from EXP020 common SFT; GPU launch remains unapproved | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_tactic_rl_terminal.json`](rl/qwen_05b_tactic_rl_terminal.json) | Terminal-only interactive RL from EXP020 common SFT; GPU launch remains unapproved | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_tactic_rl_terminal_comparison.json`](rl/qwen_05b_tactic_rl_terminal_comparison.json) | Exploratory Lean-in-the-loop feedback versus terminal reward. | EXP-019 | `run_id` prefix |
| [`rl/qwen_05b_whole_proof_grpo.json`](rl/qwen_05b_whole_proof_grpo.json) | – | EXP-004 | catalogue and README |
