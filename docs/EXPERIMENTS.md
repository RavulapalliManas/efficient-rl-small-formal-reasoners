# Experiments

Use the summaries below to find an experiment, then follow its protocol or results link.
The [README](../README.md#status) gives current findings; the [report](tex/project_report.pdf) explains methods and interpretation.

- [Execution summaries](#execution-record): outcomes of all 32 experiments and audits.
- [Experiment register](#experiment-register): questions, protocols, results and report sections.
- [Recording rules](#record-contract): requirements for a new run.
- [Experiment history](EXPERIMENT_HISTORY.md): original protocols, amendments, failures and dated decisions.
- [Main-study data handoff](EXPERIMENT_HISTORY.md#start-here-one-page-for-abhinav): archived data, weights and run commands.
- [Verifier audit](REWARD_HACKING.md): historical scorer exploits and controls.

## Naming and terminology

Study titles identify the intervention or comparison and its task. The register states the motivation separately.
These summaries describe the recorded protocols; they do not add hypotheses or change which studies ran.
Use the same study title in summaries and record headings. Dates, owners and delivery deadlines belong in the record.
Keep experiment IDs, config keys, run IDs, theorem IDs and artifact filenames stable.
Historical amendments retain their original wording; the titles above them use the current names.

- Name a figure by its measured quantity and population. State an interpretation in the caption or discussion, with its uncertainty.
- Name both sides of a comparison. An umbrella study with several comparisons must identify its individual protocols.
- Distinguish an experiment from a data build, readiness assessment or post-hoc analysis. Prepared arms are not completed comparisons.
- Use **accepted attempts** for complete proofs accepted by Lean and **theorems proved** for distinct theorems with an accepted attempt.
- Report the attempt budget with theorem counts. A valid tactic step is partial progress, not a proof.
- Use **selection set** for EXP-028. It is exploratory and distinct from the reserved TEST set.
- Use **RLVR** for reinforcement learning with verifiable rewards. It describes the reward setting, not the optimizer.
- Use **GRPO** for group relative policy optimization and **PPO** for proximal policy optimization.
- Name the interactive learner **REINFORCE with a leave-one-out baseline**. **RLOO** denotes the REINFORCE leave-one-out estimator; group sampling alone does not make it GRPO.
- Use **RLP** for the target-likelihood reward defined in the report. It does not verify reasoning correctness.

The [script glossary](../scripts/README.md#terms) defines model names, actions, attempts and metrics.
Algorithm names follow the [GRPO documentation](https://huggingface.co/docs/trl/v0.24.0/en/grpo_trainer) and the [REINFORCE leave-one-out reference](https://arxiv.org/abs/2402.14740).

## Execution record

Experiment execution stopped on 20 October 2026. This table preserves the recorded outcomes, including stopped and unrun work.
The [README](../README.md#status) summarizes the current findings; the [register](#experiment-register) links protocols and evidence.

<details>
<summary>Show all 32 experiment and audit records</summary>

| ID | Study title | Config or entry point | Status |
| --- | --- | --- | --- |
| [EXP-030](EXPERIMENT_HISTORY.md#exp-030) | Terminal proof reward versus per-action Lean-error penalties | `scripts/train/run_tactic_rl.py` | Not run. All experiments stopped on 20 October; the proposed reward comparison remains unmeasured |
| [EXP-029](EXPERIMENT_HISTORY.md#exp-029) | Eight-attempt evaluation of SFT and RL checkpoints | `scripts/train/run_tactic_rl.py` | Not run. Neither planned stage launched before experiments stopped |
| [EXP-028](EXPERIMENT_HISTORY.md#exp-028) | Exploratory evaluation of search, retrieval, SFT and filtered RL | `scripts/train/run_tactic_rl.py` options; `scripts/data/build_search_sft.py`, `add_premises_sft.py`, `build_premise_corpus.py` | No remedy arm is promoted at 2 attempts per theorem: S (E0) and search (E2) each prove 1 of 163 assessed theorems; lemma retrieval (E3), R (E7), the SFT seed control (E5) and filtered RL (E6) prove 1, 1, 0 and 2 of 164, respectively. E0 and E2 censor one theorem after an automation timeout. E4 did not run: the S_mined SFT stage failed on a data-builder defect, now fixed. RL_filtered raises acceptance on its 62 TRAIN theorems from 0.31 to 0.60 (p = 0.0001), without measurable transfer. A separate Kimina-Prover-Distill-0.6B probe proves 47 and 40 of 164 within 8 attempts in two draws (56 in either); its format and token budget differ from S |
| [EXP-027](EXPERIMENT_HISTORY.md#exp-027) | Prerequisite-ordered versus shuffled RL | [Protocol and execution guide](EXPERIMENT_HISTORY.md#exp-027); [common data and execution plan](EXPERIMENT_HISTORY.md#common-data-and-curriculum-execution-plan-18-october-2026-ist); `scripts/train/run_tactic_rl.py --task-order` | C accepts 255 of 4,048 TRAIN attempts against R's 164 (per-theorem p = 0.0002), with the gain inside the first epoch. Both prove 0 of 8 DEV theorems; C has no selection-set evaluation. Annotations have no human confirmation or measured prerequisite necessity |
| [EXP-026](EXPERIMENT_HISTORY.md#exp-026) | Unchanged model, SFT and shuffled RL proof evaluation | [Revised protocol](EXPERIMENT_HISTORY.md#generalization-experiment-revised-framing-16-october-2026); [current data and model contract](EXPERIMENT_HISTORY.md#current-data-and-model-contract-closure-18-october-2026-ist) | Fresh S trained; B, S and R each prove 0 of 8 DEV theorems. R accepts 164 of 4,048 TRAIN attempts with no detected learning (first against second visit, p = 0.93) and proves 1 of 164 selection-set theorems in EXP-028 (E7), no gain over S. RL shifts R toward short, malformed actions (DEV format errors 37 to 113 of 192) |
| [EXP-025](EXPERIMENT_HISTORY.md#exp-025) | Number-theory benchmark construction and proof-data verification | [Builder](../scripts/data/build_nt_benchmark.py), [expanded config](../configs/data/nt_benchmark_v2.json); [checked prerequisite release](EXPERIMENT_HISTORY.md#checked-prerequisite-release-outcome-17-october-2026); [full-corpus mapping](EXPERIMENT_HISTORY.md#full-corpus-mapping-outcome-17-october-2026) | `validated_v2/` supersedes the initial common release for new consumers. This release retains 1,880 TRAIN SFT examples and 125 separate SFT validation examples. Public pretraining exposure and complete semantic alias independence remain unresolved |
| [EXP-024](EXPERIMENT_HISTORY.md#exp-024) | Undergraduate number-theory SFT versus equal-token reference SFT | `scripts/train/run_sft.py`, `scripts/eval/curriculum_metrics.py` | Protocol approved 15 October. Mathlib identity check, syllabus, corpus v0 (40,732 theorems) and single-tactic screen done; no training or evaluation yet |
| [EXP-023](EXPERIMENT_HISTORY.md#exp-023) | Number-theory RL reward, algorithm and curriculum comparisons | [Data config](../configs/data/nt_expanded_tactic_v1.json), `scripts/train/run_tactic_rl.py`, `scripts/train/run_tactic_ppo.py` | Final curriculum run done 13 October. RL gains on new instances of trained families (`id_val`), no gain on held-out families; the ordered curriculum shows no advantage, and the failed token gate invalidates the efficiency claim |
| [EXP-022](EXPERIMENT_HISTORY.md#exp-022) | PPO versus GRPO with matched Lean proof rewards | `scripts/train/lean_ppo.py`, `configs/rl/qwen_05b_lean_{ppo,grpo}_comparison.json` | Adapter and paired configs prepared; EXP-023 corrected two scoring defects in the shared adapter. Lean PPO training has not run |
| [EXP-021](EXPERIMENT_HISTORY.md#exp-021) | Rejection-sampling fine-tuning before Lean-reward GRPO | `configs/midtrain/qwen_05b_monday_recovery.json` | Done. The raw DEV recount finds no accepted proof in any arm, including the shared start |
| [EXP-020](EXPERIMENT_HISTORY.md#exp-020) | Alternating SFT with RLP or Lean-reward GRPO | `configs/midtrain/qwen_05b_interleaved_training_v3.json` | Done. Non-trivial validation statements proved at 8 attempts: initial 0/392, common SFT 13/392, SFT/GRPO 6/392, SFT/RLP 15/392 |
| [EXP-019](EXPERIMENT_HISTORY.md#exp-019) | Interactive Lean RL with terminal and heuristic feedback rewards | `scripts/train/run_tactic_rl.py`, [run config](../configs/rl/qwen_05b_tactic_mixed_followup.json) | Done. Fixed-mixture v2 DEV: 26/48 accepted attempts against the parent's 18/48, single seed, no accepted composition attempt. See the [completion record](EXPERIMENT_HISTORY.md#fixed-mixture-midterm-completion) |
| [EXP-018](EXPERIMENT_HISTORY.md#exp-018) | Interleaved informal reasoning and Lean-tactic SFT | `configs/midtrain/qwen_05b_nt_interleaved_sft_v1.json` | Ready. Not run (needs GPU approval) |
| [EXP-017](EXPERIMENT_HISTORY.md#exp-017) | Theorem splits, proof-action SFT data and RL task construction | `scripts/data/build_nt_splits.py`, `scripts/data/build_syntax_sft.py` | Done. Splits v3 and paired `nt_sft_v8` data: 5,812 train SFT examples and 245 SFT validation examples. No training in this build |
| [EXP-016](EXPERIMENT_HISTORY.md#exp-016) | Lean-checked number-theory statement-pool construction | `scripts/data/build_nt_pool.py` | Done. Pool v2: 4,306 Lean-checked statements (train 3,450, validation 420, test 436) |
| [EXP-015](EXPERIMENT_HISTORY.md#exp-015) | Lean Workbook proof evaluation with a fixed-tactic baseline | `scripts/eval/lean_eval_statements.py`, `scripts/eval/probe_nt_pool.py` | Done for the SFT model. 0/200 Lean Workbook number-theory statements; one standard tactic proves 8/200 |
| [EXP-014](EXPERIMENT_HISTORY.md#exp-014) | Supervised fine-tuning on mathlib proofs | `configs/midtrain/qwen_05b_lean_sft_v1.json` | Done. Validation 2/43 theorems at 8 attempts; 0/17 number-theory theorems |
| [EXP-013](EXPERIMENT_HISTORY.md#exp-013) | Ordered versus shuffled mathematical-span RLP | `configs/midtrain/qwen_05b_controlled_rlp.json` | Done. Both arms 0/18 fully correct; final core score 1.056 shuffled vs 0.500 ordered (sign test p = 0.18) |
| [EXP-012](EXPERIMENT_HISTORY.md#exp-012) | RLP corpus correctness and assumption audit | `configs/data/mathematical_rlp_review_v1.json`, `scripts/audit/audit_rlp_corpus.py` | Done. All 612 numeric answers correct. 160/448 training rules lack domain or zero conditions |
| [EXP-011](EXPERIMENT_HISTORY.md#exp-011) | Lean-verified witness generation with GRPO | `configs/rl/qwen_05b_local_operator_grpo.json` | Done. Witness accuracy improved |
| [EXP-010](EXPERIMENT_HISTORY.md#exp-010) | Mathematical capability evaluation across RLP checkpoints | `configs/eval/` | Done. No complete-answer gain |
| [EXP-009](EXPERIMENT_HISTORY.md#exp-009) | Primitive-first number-theory RLP curriculum | `configs/midtrain/qwen_05b_primitive_rlp.json` | Done. Held-out accuracy fell |
| [EXP-008](EXPERIMENT_HISTORY.md#exp-008) | RLP optimizer-precision audit and FP32 rerun | `configs/midtrain/qwen_05b_number_theory_rlp_fp32.json` | Done. No accuracy gain |
| [EXP-007](EXPERIMENT_HISTORY.md#exp-007) | Number-theory RLP training and continuation | `configs/pretrain/qwen_05b_number_theory_rlp*.json` | Done. No accuracy gain |
| [EXP-006](EXPERIMENT_HISTORY.md#exp-006) | Proof-prefix assistance for GRPO training | `configs/rl/qwen_05b_completion_grpo.json` | Done. One theorem qualified; no accepted held-out attempt |
| [EXP-005](EXPERIMENT_HISTORY.md#exp-005) | Prerequisite-curriculum GRPO readiness assessment | `configs/rl/qwen_05b_prerequisite_grpo*.json` | Done. Training skipped: no accepted attempt |
| [EXP-004](EXPERIMENT_HISTORY.md#exp-004) | Ordered versus shuffled whole-proof GRPO | `configs/rl/qwen_05b_whole_proof_grpo.json` | Done. No learning signal |
| [EXP-003](EXPERIMENT_HISTORY.md#exp-003) | Lean proof-generation evaluation of the unchanged Instruct model | `configs/eval/qwen_05b_probe.json`, `scripts/eval/probe_qwen.py` | Done. Lean accepted 0/63 local attempts. A GPU sampling run at four temperatures shows 0/273 in its exported records |
| [EXP-002](EXPERIMENT_HISTORY.md#exp-002) | GSM8K supervised fine-tuning and informal mathematics evaluation | `configs/eval/qwen_05b_probe.json` | Done. No accuracy gain |
| [EXP-001](EXPERIMENT_HISTORY.md#exp-001) | LeanDojo number-theory dataset preparation | `configs/data/leandojo_*.json` | Done. Lean replay done: 5,921 of 8,273 training proofs accepted; task bank v2 keeps 2,150 theorems outside evaluation and dev files |
| [AUD-002](EXPERIMENT_HISTORY.md#aud-002) | Lean reward-verifier and data-exposure audit | `scripts/eval/check_checker_attacks.py`, `scripts/eval/bench_lean_verifier.py`, `scripts/eval/check_scoring_paths.py` | Scorer adoption appears in the decision log. Attack controls do not establish an exhaustive security guarantee |
| [AUD-001](EXPERIMENT_HISTORY.md#aud-001) | Post-hoc proof-failure and curriculum analysis | `scripts/audit/classify_proof_failures.py`, `scripts/audit/curriculum_inventory.py` | This is a post hoc diagnostic, not a registered curriculum-effect test |

</details>

## Experiment register

Experiment IDs remain stable. Protocol and results links point to the dated history.
Use the README for current execution status.

| ID | Kind | Motivation | Record | Results | Report |
| --- | --- | --- | --- | --- | --- |
| EXP-001 | Data build | Prepare arithmetic and number-theory proofs with split-overlap exclusions. | [Record](EXPERIMENT_HISTORY.md#exp-001) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.1](tex/project_report.pdf#page=40) |
| EXP-002 | Baseline | Assess whether GSM8K LoRA fine-tuning changes informal mathematics accuracy. | [Record](EXPERIMENT_HISTORY.md#exp-002) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.1](tex/project_report.pdf#page=40) |
| EXP-003 | Baseline | Measure complete-proof acceptance before project fine-tuning. | [Record](EXPERIMENT_HISTORY.md#exp-003) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.1](tex/project_report.pdf#page=40) |
| EXP-004 | Experiment | Compare task orders for whole-proof GRPO from the unchanged model. | [Record](EXPERIMENT_HISTORY.md#exp-004) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.2](tex/project_report.pdf#page=40) |
| EXP-005 | Readiness assessment | Check for accepted proofs before launching prerequisite-curriculum training. | [Record](EXPERIMENT_HISTORY.md#exp-005) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.2](tex/project_report.pdf#page=40) |
| EXP-006 | Experiment | Test whether supplied proof prefixes make proof-completion training feasible. | [Record](EXPERIMENT_HISTORY.md#exp-006) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.2](tex/project_report.pdf#page=40) |
| EXP-007 | Experiment | Assess whether target-likelihood rewards improve independently generated answers. | [Record](EXPERIMENT_HISTORY.md#exp-007) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-008 | Audit and rerun | Check whether optimizer precision prevents parameter updates. | [Record](EXPERIMENT_HISTORY.md#exp-008) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-009 | Experiment | Evaluate held-out answers after RLP on dependency-ordered mathematical primitives. | [Record](EXPERIMENT_HISTORY.md#exp-009) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-010 | Evaluation | Compare recall, application and proof responses across saved checkpoints. | [Record](EXPERIMENT_HISTORY.md#exp-010) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-011 | Experiment | Test learning on local witness expressions checked by Lean. | [Record](EXPERIMENT_HISTORY.md#exp-011) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.4](tex/project_report.pdf#page=42) |
| EXP-012 | Data audit | Check numeric targets, rule hypotheses and answer shortcuts in the RLP corpus. | [Record](EXPERIMENT_HISTORY.md#exp-012) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-013 | Experiment | Compare curriculum order with matched mathematical-span training examples. | [Record](EXPERIMENT_HISTORY.md#exp-013) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-014 | Experiment | Assess proof SFT as an initialization for subsequent Lean RL. | [Record](EXPERIMENT_HISTORY.md#exp-014) | In record | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-015 | Evaluation | Compare SFT proof generation with a model-free tactic baseline. | [Record](EXPERIMENT_HISTORY.md#exp-015) | In record | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-016 | Data build | Prepare a statement pool for number-theory training and evaluation. | [Record](EXPERIMENT_HISTORY.md#exp-016) | In record | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-017 | Data build | Construct separate theorem splits and compatible supervised and RL examples. | [Record](EXPERIMENT_HISTORY.md#exp-017) | In record | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-018 | Experiment | Test supervised examples that alternate informal reasoning and formal tactics. | [Record](EXPERIMENT_HISTORY.md#exp-018) | Not run | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-019 | Experiment | Evaluate interactive proof learning, including terminal-only and heuristic-feedback REINFORCE pilots. | [Record](EXPERIMENT_HISTORY.md#exp-019) | [Native parity](EXPERIMENT_HISTORY.md#native-parity-midterm-completion); [fixed mixture](EXPERIMENT_HISTORY.md#fixed-mixture-midterm-completion) | [Appendix B.5](tex/project_report.pdf#page=42); [Appendix B.6](tex/project_report.pdf#page=42) |
| EXP-020 | Exploratory pilot | Compare two exploratory training pipelines from a shared SFT stage; data and objectives also differ. | [Record](EXPERIMENT_HISTORY.md#exp-020) | [Validation recount](EXPERIMENT_HISTORY.md#exp-020-validation-recount-11-october-2026) | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-021 | Experiment | Evaluate verified, model-generated training proofs as supervised data before an additional RL stage. | [Record](EXPERIMENT_HISTORY.md#exp-021) | [Decision log](EXPERIMENT_HISTORY.md#decision-log), 11 October | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-022 | Experiment | Compare a learned value baseline with group-relative optimization under matched protocol settings. | [Record](EXPERIMENT_HISTORY.md#exp-022) | Not run | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-023 | Experiment program | Extend the task bank and compare reward, algorithm and ordering candidates in separate protocols. | [Record](EXPERIMENT_HISTORY.md#exp-023) | [Final run](EXPERIMENT_HISTORY.md#handoff-final-curriculum-run-results-14-october); [held-out pass@k](EXPERIMENT_HISTORY.md#exp-023-held-out-passk-15-october-2026); [recount](EXPERIMENT_HISTORY.md#exp-023-saved-question-recount-outcome-15-october-2026) | [Appendix B.7](tex/project_report.pdf#page=43); [Appendix E.1](tex/project_report.pdf#page=49) |
| EXP-024 | Experiment | Compare supervised data sources at a matched token budget. | [Record](EXPERIMENT_HISTORY.md#exp-024) | [Corpus v0](EXPERIMENT_HISTORY.md#exp-024-corpus-v0-outcome-15-october-2026); [screen](EXPERIMENT_HISTORY.md#exp-024-screen-part-2-outcome-15-october-2026); no training | Not in the report |
| AUD-001 | Audit | Describe proof failures and available task structure without testing a curriculum effect. | [Record](EXPERIMENT_HISTORY.md#aud-001) | In record | Section removed in commit `c612b21` |
| AUD-002 | Audit | Identify scorer exploits and evaluation exposure that can invalidate proof-reward interpretation. | [Record](EXPERIMENT_HISTORY.md#aud-002) | In record; [reward inventory](REWARD_HACKING.md) | [Appendix C](tex/project_report.pdf#page=44); [Appendix D](tex/project_report.pdf#page=44) |
| EXP-025 | Data build | Build traceable training and evaluation inputs with proof verification and overlap checks. | [Record](EXPERIMENT_HISTORY.md#exp-025) | [Expanded dataset](EXPERIMENT_HISTORY.md#exp-025-expanded-dataset-outcome-16-october-2026); [filtered release](EXPERIMENT_HISTORY.md#filtered-release-outcome-17-october-2026); [prerequisite release](EXPERIMENT_HISTORY.md#checked-prerequisite-release-outcome-17-october-2026); [full-corpus mapping](EXPERIMENT_HISTORY.md#full-corpus-mapping-outcome-17-october-2026) | [Appendix F](tex/project_report.pdf#page=50); [Appendix H](tex/project_report.pdf#page=58) |
| EXP-026 | Experiment | Measure changes in proof completion across the main training stages. | [Revised protocol](EXPERIMENT_HISTORY.md#generalization-experiment-revised-framing-16-october-2026) | [R and E3](EXPERIMENT_HISTORY.md#r-result-and-e3-result-19-october-2026-ist); [dev_R](EXPERIMENT_HISTORY.md#dev_r-result-rl-shifts-s-toward-repeated-short-actions-19-october-2026-ist); [E7](EXPERIMENT_HISTORY.md#e7-result-r-on-the-selection-set-19-october-2026-ist) | [Appendix F](tex/project_report.pdf#page=50); [§4](tex/project_report.pdf#page=9) |
| EXP-027 | Experiment | Compare theorem order with the same SFT initialization and training multiset. | [Protocol](EXPERIMENT_HISTORY.md#exp-027) | [C](EXPERIMENT_HISTORY.md#c-result-curriculum-order-raises-train-acceptance-and-narrows-the-policy-19-october-2026-ist); [dev_C](EXPERIMENT_HISTORY.md#dev_c-result-and-main-lease-end-19-october-2026-ist) | [Appendix F](tex/project_report.pdf#page=50); [§4.4](tex/project_report.pdf#page=14) |
| EXP-028 | Exploratory screen | Screen interventions for complete-proof gains on the separate selection set. | [Protocol](EXPERIMENT_HISTORY.md#exp-028) | [E0](EXPERIMENT_HISTORY.md#e0-result-18-october-2026-ist); [E2](EXPERIMENT_HISTORY.md#e2-result-and-lemma-data-18-october-2026-ist); [E5 and E3](EXPERIMENT_HISTORY.md#e5-result-and-the-e3-comparison-19-october-2026-ist); [E6](EXPERIMENT_HISTORY.md#e6-result-filtered-rl-on-the-selection-set-19-october-2026-ist); [RL_filtered](EXPERIMENT_HISTORY.md#rl_filtered-result-learning-on-the-62-filtered-train-tasks-19-october-2026-ist); [Kimina probe](EXPERIMENT_HISTORY.md#kimina-capability-probe-result-19-october-2026-ist); [why-analysis](EXPERIMENT_HISTORY.md#analysis-why-rl-raises-train-acceptance-without-a-held-out-gain-20-october-2026-ist) | [§4](tex/project_report.pdf#page=9); [§4.6](tex/project_report.pdf#page=16) |
| EXP-029 | Unrun experiment | Test whether a larger attempt budget changes the SFT–RL comparison; this study did not run. | [Protocol](EXPERIMENT_HISTORY.md#exp-029) | Not run; [stop](EXPERIMENT_HISTORY.md#exp-029-stop) | [Appendix G](tex/project_report.pdf#page=56) |
| EXP-030 | Unrun exploratory pilot | Compare complete-proof acceptance under terminal-only and error-penalized rewards; this study did not run. | [Protocol](EXPERIMENT_HISTORY.md#exp-030) | Not run; [stop](EXPERIMENT_HISTORY.md#exp-030-stop) | [Appendix G](tex/project_report.pdf#page=56) |

The Results column links outcome entries. The Report column links sections and pages in the current PDF.


<details>
<summary>Rules for recording and updating experiments</summary>

## Catalogue rules

An experiment asks a research question. A run is one execution, including an arm, seed, retry, or rescore.
Data builds, baselines, and audits also need records. They do not establish a training gain.

- Keep the existing experiment numbering. `EXP-001` refers to the original Experiment 1.
- Assign a new experiment ID for a new question. Never renumber records when the report order changes.
- Keep comparisons under one experiment. Give each execution a unique run ID and output directory.
- Keep historical output names. Add their experiment association here instead of renaming artifacts.
- Record each failed, stopped, skipped, and null run. Separate these outcomes from measured success or failure.
- Mark missing fields as `not recorded` and unchecked paths as `not inspected`. Never reconstruct them from memory.
- Keep dates inside entries. Do not create a new Markdown file for each session, plan, audit, or handoff.

Historical records retain their original evidence paths. Some paths and run metadata remain private or incomplete.
Use the original manifests to locate artifacts; this index does not verify their availability.
Paths under `artifacts/notes/` are outside Git.

## Record contract

Before a new run, add its protocol to the relevant entry in the [history](EXPERIMENT_HISTORY.md).
Keep resolved execution details in the existing run directory, outside Git.
For historical runs, preserve the original record and list missing fields here.

| Record | Required content | Location |
| --- | --- | --- |
| Question | Research question, hypothesis or exploratory aim, inference boundary | Experiment entry in the history |
| Responsibility | Scientific owner, execution owner, reviewer; distinguish assigned work from authorship | Experiment entry in the history |
| Comparison | Arms, starting checkpoint, data exposure, seeds, baseline, controlled variables | Protocol in the history; resolved settings in run artifacts |
| Evaluation | Primary metric, numerator and denominator, unit of analysis, split, checkpoint selection rule, uncertainty method | Protocol in the history; per-item outcomes in run artifacts |
| Limits | Token, Lean-call, wall-time, memory, and GPU caps; approval reference for paid work | Protocol and resolved run record |
| Identity | Experiment ID, unique run ID, parent run or checkpoint, UTC timestamps, command, working directory | Existing run record |
| Versions | Git revision and dirty diff; model revision; config and data digests; Lean, mathlib, REPL, and scorer versions | Resolved run record and manifests |
| Inputs | Frozen split membership, exact and near-duplicate audit, excluded items, proof exposure | Data manifests and audit output |
| Outputs | Raw completions, Lean verdicts, error categories, checkpoints, logs, exit code, stop reason | Run directory |
| Recount | Script, command, source file and row or item IDs, exclusions, counted denominator, derived output | Run artifacts; link from the history |
| Resources | Generated tokens, physical Lean calls, cache hits, wall and GPU time, peak memory | Existing run logs |
| Cost | Reservation, measured usage, invoice if available, and durable artifact receipt | Private run ledger and run artifacts |

Use the runners' existing files. `run_grpo_pilot.py` and `run_rlp_pilot.py` write `run.json`.
`run_lean_grpo.py` writes `run.json`, `steps.jsonl`, and `rollouts.jsonl`.
`run_sft.py` writes `run.json` after training, so it cannot document a failed launch by itself.
Keep that launch's command, log, exit code, and reservation in the existing operations ledger.
These requirements are documentation rules. The current runners do not automatically populate every field.

### Protocol and result updates

1. Register the question, owner, comparison, metric, evidence path, and limits before launch.
2. Freeze the config, splits, model, and verifier identity in the run record.
3. Record the run ID and output location under its experiment. Keep one writer per output.
4. Recount outcomes from raw records before updating a result or the README status.
5. Record the interpretation and limitation together. Put the scientific explanation in the TeX report.
6. Append protocol changes and corrections to the [decision log](EXPERIMENT_HISTORY.md#decision-log). Preserve the original protocol and result.

Choose checkpoints and task mixes on the development split only.
Keep training, development, validation, and held-out test membership explicit.
For paired comparisons, match the starting checkpoint, data, sampling settings, scorer, and resource budget.
Record deviations and failed attempts before interpreting the comparison.
Label each reward component as Lean-verified or heuristic. A completed subgoal is not a complete proof.
Keep timeouts, crashes, truncations, and scorer errors distinguishable from rejected proofs in raw records.
If an endpoint counts them as failures, state that rule and retain their categories.
A corrected scorer needs a new rescore record linked to the original generation run.
Compare arms only under the same declared scoring protocol.

### Documentation ownership

| Content | Maintained source |
| --- | --- |
| Current execution status and navigation | `README.md` |
| Experiment summaries and evidence index | This file |
| Original protocols, amendments, outcomes and dated decisions | [Experiment history](EXPERIMENT_HISTORY.md) |
| Scientific explanation, formal definitions, figures, conclusions | `docs/tex/project_report.tex` |
| Exploit mechanism, fix, residual risk | `docs/REWARD_HACKING.md`, linked to the audit entry here |
| Raw evidence, resolved settings, manifests, recounts, logs, costs | Existing run directories and private operations ledgers |
| Historical notes | Git history and run artifacts; no continuing project status |

Reuse these owners. A new document requires a distinct audience and durable purpose that no existing source serves.
Generated run reports stay with their run and appear in its evidence index.
Do not turn generated reports into another project narrative or status record.
Keep infrastructure identifiers in `.private/infrastructure.md`.
Public records use cpu-angad with paths relative to `formal_rl/`, or the existing redacted S3 prefix.
Preserve historical files and unique evidence. Consolidation does not authorize deletion.

</details>

## Historical reading guide

<a id="how-to-read-this-file"></a>
The [history reading guide](EXPERIMENT_HISTORY.md#reading-the-history) explains the older names and where amendments were recorded.
