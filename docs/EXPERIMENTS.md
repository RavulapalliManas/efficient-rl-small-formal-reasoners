# Experiments

Use the summaries below to find an experiment, then follow its protocol or results link.
The [README](../README.md#status) gives current findings; the [report](tex/project_report.pdf) explains methods and interpretation.

- [Execution summaries](#execution-record): outcomes of all 32 experiments and audits.
- [Experiment register](#experiment-register): questions, protocols, results and report sections.
- [Recording rules](#record-contract): requirements for a new run.
- [Experiment history](EXPERIMENT_HISTORY.md): original protocols, amendments, failures and dated decisions.
- [Main-study data handoff](EXPERIMENT_HISTORY.md#start-here-one-page-for-abhinav): archived data, weights and run commands.
- [Verifier audit](REWARD_HACKING.md): historical scorer exploits and controls.

## Execution record

Experiment execution stopped on 20 October 2026. This table preserves the recorded outcomes, including stopped and unrun work.
The [README](../README.md#status) summarizes the current findings; the [register](#experiment-register) links protocols and evidence.

<details>
<summary>Show all 32 experiment and audit records</summary>

| ID | Experiment | Config or entry point | Status |
| --- | --- | --- | --- |
| [EXP-030](EXPERIMENT_HISTORY.md#exp-030) | Do per-action Lean-error penalties improve proof completion? | `scripts/train/run_tactic_rl.py` | Not run. All experiments stopped on 20 October; the proposed reward comparison remains unmeasured |
| [EXP-029](EXPERIMENT_HISTORY.md#exp-029) | Compare RL arms with S at 8 attempts on the screened selection set and 62 filtered TRAIN theorems | `scripts/train/run_tactic_rl.py` | Not run. Neither planned stage launched before experiments stopped |
| [EXP-028](EXPERIMENT_HISTORY.md#exp-028) | Low-acceptance remedies: step search, automation fallback, lemma retrieval, search-mined SFT, pass-rate-filtered RL | `scripts/train/run_tactic_rl.py` options; `scripts/data/build_search_sft.py`, `add_premises_sft.py`, `build_premise_corpus.py` | No remedy arm is promoted at 2 attempts per theorem: S (E0) and search (E2) each prove 1 of 163 assessed theorems; lemma retrieval (E3), R (E7), the SFT seed control (E5) and filtered RL (E6) prove 1, 1, 0 and 2 of 164, respectively. E0 and E2 censor one theorem after an automation timeout. E4 did not run: the S_mined SFT stage failed on a data-builder defect, now fixed. RL_filtered raises acceptance on its 62 TRAIN theorems from 0.31 to 0.60 (p = 0.0001), without measurable transfer. A separate Kimina-Prover-Distill-0.6B probe proves 47 and 40 of 164 within 8 attempts in two draws (56 in either); its format and token budget differ from S |
| [EXP-027](EXPERIMENT_HISTORY.md#exp-027) | Curriculum order: benchmark performance and generalization | [Protocol and execution guide](EXPERIMENT_HISTORY.md#exp-027); [common data and execution plan](EXPERIMENT_HISTORY.md#common-data-and-curriculum-execution-plan-18-october-2026-ist); `scripts/train/run_tactic_rl.py --task-order` | C accepts 255 of 4,048 TRAIN attempts against R's 164 (per-theorem p = 0.0002), with the gain inside the first epoch. Both prove 0 of 8 DEV theorems; C has no selection-set evaluation. Annotations have no human confirmation or measured prerequisite necessity |
| [EXP-026](EXPERIMENT_HISTORY.md#exp-026) | Generalization, Abhinav: unchanged Qwen, SFT, and SFT + RLVR | [Revised protocol](EXPERIMENT_HISTORY.md#generalization-experiment-revised-framing-16-october-2026); [current data and model contract](EXPERIMENT_HISTORY.md#current-data-and-model-contract-closure-18-october-2026-ist) | Fresh S trained; B, S and R each prove 0 of 8 DEV theorems. R accepts 164 of 4,048 TRAIN attempts with no detected learning (first against second visit, p = 0.93) and proves 1 of 164 selection-set theorems in EXP-028 (E7), no gain over S. RL shifts R toward short, malformed actions (DEV format errors 37 to 113 of 192) |
| [EXP-025](EXPERIMENT_HISTORY.md#exp-025) | Broader benchmark and data pipeline | [Builder](../scripts/data/build_nt_benchmark.py), [expanded config](../configs/data/nt_benchmark_v2.json); [checked prerequisite release](EXPERIMENT_HISTORY.md#checked-prerequisite-release-outcome-17-october-2026); [full-corpus mapping](EXPERIMENT_HISTORY.md#full-corpus-mapping-outcome-17-october-2026) | `validated_v2/` supersedes the initial common release for new consumers. This release retains 1,880 TRAIN SFT examples and 125 separate SFT validation examples. Public pretraining exposure and complete semantic alias independence remain unresolved |
| [EXP-024](EXPERIMENT_HISTORY.md#exp-024) | Undergraduate number-theory SFT: new data against equal-token old data | `scripts/train/run_sft.py`, `scripts/eval/curriculum_metrics.py` | Protocol approved 15 October. Mathlib identity check, syllabus, corpus v0 (40,732 theorems) and single-tactic screen done; no training or evaluation yet |
| [EXP-023](EXPERIMENT_HISTORY.md#exp-023) | Broader number theory, reward and curriculum comparisons, PPO and REINFORCE | [Data config](../configs/data/nt_expanded_tactic_v1.json), `scripts/train/run_tactic_rl.py`, `scripts/train/run_tactic_ppo.py` | Final curriculum run done 13 October. RL gains on new instances of trained families (`id_val`), no gain on held-out families; the ordered curriculum shows no advantage, and the failed token gate invalidates the efficiency claim |
| [EXP-022](EXPERIMENT_HISTORY.md#exp-022) | PPO versus GRPO with the same Lean reward | `scripts/train/lean_ppo.py`, `configs/rl/qwen_05b_lean_{ppo,grpo}_comparison.json` | Adapter and paired configs prepared; EXP-023 corrected two scoring defects in the shared adapter. Lean PPO training has not run |
| [EXP-021](EXPERIMENT_HISTORY.md#exp-021) | Monday recovery: verified distillation before Lean RL | `configs/midtrain/qwen_05b_monday_recovery.json` | Done. The raw DEV recount finds no accepted proof in any arm, including the shared start |
| [EXP-020](EXPERIMENT_HISTORY.md#exp-020) | Alternating SFT with RLP and Lean GRPO | `configs/midtrain/qwen_05b_interleaved_training_v3.json` | Done. Non-trivial validation statements proved at 8 attempts: initial 0/392, common SFT 13/392, SFT/GRPO 6/392, SFT/RLP 15/392 |
| [EXP-019](EXPERIMENT_HISTORY.md#exp-019) | Interactive Lean RL (fixed tasks before curriculum) | `scripts/train/run_tactic_rl.py`, [run config](../configs/rl/qwen_05b_tactic_mixed_followup.json) | Done. Fixed-mixture v2 DEV: 26/48 accepted attempts against the parent's 18/48, single seed, no accepted composition attempt. See the [completion record](EXPERIMENT_HISTORY.md#fixed-mixture-midterm-completion) |
| [EXP-018](EXPERIMENT_HISTORY.md#exp-018) | Interleaved number-theory SFT | `configs/midtrain/qwen_05b_nt_interleaved_sft_v1.json` | Ready. Not run (needs GPU approval) |
| [EXP-017](EXPERIMENT_HISTORY.md#exp-017) | Midsem RL data (splits, SFT data, RL tasks) | `scripts/data/build_nt_splits.py`, `scripts/data/build_syntax_sft.py` | Done. Splits v3 and paired `nt_sft_v8` data: 5,812 train SFT examples and 245 SFT validation examples. No training in this build |
| [EXP-016](EXPERIMENT_HISTORY.md#exp-016) | Number-theory statement pool | `scripts/data/build_nt_pool.py` | Done. Pool v2: 4,306 Lean-checked statements (train 3,450, validation 420, test 436) |
| [EXP-015](EXPERIMENT_HISTORY.md#exp-015) | Lean-verified evaluation | `scripts/eval/lean_eval_statements.py`, `scripts/eval/probe_nt_pool.py` | Done for the SFT model. 0/200 Lean Workbook number-theory statements; one standard tactic proves 8/200 |
| [EXP-014](EXPERIMENT_HISTORY.md#exp-014) | Lean SFT on mathlib proofs | `configs/midtrain/qwen_05b_lean_sft_v1.json` | Done. Validation 2/43 theorems at 8 attempts; 0/17 number-theory theorems |
| [EXP-013](EXPERIMENT_HISTORY.md#exp-013) | Controlled RLP comparison | `configs/midtrain/qwen_05b_controlled_rlp.json` | Done. Both arms 0/18 fully correct; final core score 1.056 shuffled vs 0.500 ordered (sign test p = 0.18) |
| [EXP-012](EXPERIMENT_HISTORY.md#exp-012) | RLP corpus audit | `configs/data/mathematical_rlp_review_v1.json`, `scripts/audit/audit_rlp_corpus.py` | Done. All 612 numeric answers correct. 160/448 training rules lack domain or zero conditions |
| [EXP-011](EXPERIMENT_HISTORY.md#exp-011) | Local-action pilot | `configs/rl/qwen_05b_local_operator_grpo.json` | Done. Witness accuracy improved |
| [EXP-010](EXPERIMENT_HISTORY.md#exp-010) | Saved-checkpoint evaluations | `configs/eval/` | Done. No complete-answer gain |
| [EXP-009](EXPERIMENT_HISTORY.md#exp-009) | Primitive RLP curriculum | `configs/midtrain/qwen_05b_primitive_rlp.json` | Done. Held-out accuracy fell |
| [EXP-008](EXPERIMENT_HISTORY.md#exp-008) | FP32 RLP rerun | `configs/midtrain/qwen_05b_number_theory_rlp_fp32.json` | Done. No accuracy gain |
| [EXP-007](EXPERIMENT_HISTORY.md#exp-007) | RLP training + continuation | `configs/pretrain/qwen_05b_number_theory_rlp*.json` | Done. No accuracy gain |
| [EXP-006](EXPERIMENT_HISTORY.md#exp-006) | Assisted proof completion | `configs/rl/qwen_05b_completion_grpo.json` | Done. One theorem qualified; no accepted held-out attempt |
| [EXP-005](EXPERIMENT_HISTORY.md#exp-005) | Prerequisite curriculum GRPO | `configs/rl/qwen_05b_prerequisite_grpo*.json` | Done. Training skipped: no accepted attempt |
| [EXP-004](EXPERIMENT_HISTORY.md#exp-004) | Whole-proof GRPO | `configs/rl/qwen_05b_whole_proof_grpo.json` | Done. No learning signal |
| [EXP-003](EXPERIMENT_HISTORY.md#exp-003) | Instruct model proof check | `configs/eval/qwen_05b_probe.json`, `scripts/eval/probe_qwen.py` | Done. Lean accepted 0/63 local attempts. A GPU sampling run at four temperatures shows 0/273 in its exported records |
| [EXP-002](EXPERIMENT_HISTORY.md#exp-002) | Base model probe + GSM8K LoRA | `configs/eval/qwen_05b_probe.json` | Done. No accuracy gain |
| [EXP-001](EXPERIMENT_HISTORY.md#exp-001) | LeanDojo data subset | `configs/data/leandojo_*.json` | Done. Lean replay done: 5,921 of 8,273 training proofs accepted; task bank v2 keeps 2,150 theorems outside evaluation and dev files |
| [AUD-002](EXPERIMENT_HISTORY.md#aud-002) | Lean reward and engineering audit | `scripts/eval/check_checker_attacks.py`, `scripts/eval/bench_lean_verifier.py`, `scripts/eval/check_scoring_paths.py` | Scorer adoption appears in the decision log. Attack controls do not establish an exhaustive security guarantee |
| [AUD-001](EXPERIMENT_HISTORY.md#aud-001) | Proof failures and curriculum inventory | `scripts/audit/classify_proof_failures.py`, `scripts/audit/curriculum_inventory.py` | This is a post hoc diagnostic, not a registered curriculum-effect test |

</details>

## Experiment register

Experiment IDs remain stable. Protocol and results links point to the dated history.
Use the README for current execution status.

| ID | Kind | Question or purpose | Record | Results | Report |
| --- | --- | --- | --- | --- | --- |
| EXP-001 | Data build | Can we prepare an isolated LeanDojo theorem subset? | [Record](EXPERIMENT_HISTORY.md#exp-001) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.1](tex/project_report.pdf#page=40) |
| EXP-002 | Baseline | Does GSM8K LoRA change informal math accuracy? | [Record](EXPERIMENT_HISTORY.md#exp-002) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.1](tex/project_report.pdf#page=40) |
| EXP-003 | Baseline | Can the unchanged Instruct model generate accepted Lean proofs? | [Record](EXPERIMENT_HISTORY.md#exp-003) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.1](tex/project_report.pdf#page=40) |
| EXP-004 | Experiment | Does family order change whole-proof GRPO learning? | [Record](EXPERIMENT_HISTORY.md#exp-004) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.2](tex/project_report.pdf#page=40) |
| EXP-005 | Experiment | Do prerequisite theorems produce initial accepted attempts in Lean? | [Record](EXPERIMENT_HISTORY.md#exp-005) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.2](tex/project_report.pdf#page=40) |
| EXP-006 | Experiment | Does a proof prefix make curriculum comparison feasible? | [Record](EXPERIMENT_HISTORY.md#exp-006) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.2](tex/project_report.pdf#page=40) |
| EXP-007 | Experiment | Does RLP training improve independently generated answers? | [Record](EXPERIMENT_HISTORY.md#exp-007) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-008 | Audit and rerun | Are RLP updates lost through optimizer precision? | [Record](EXPERIMENT_HISTORY.md#exp-008) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-009 | Experiment | Does primitive-first RLP improve held-out answers? | [Record](EXPERIMENT_HISTORY.md#exp-009) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-010 | Evaluation | Which capabilities change across saved RLP checkpoints? | [Record](EXPERIMENT_HISTORY.md#exp-010) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-011 | Experiment | Can a Lean reward improve local witness generation? | [Record](EXPERIMENT_HISTORY.md#exp-011) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.4](tex/project_report.pdf#page=42) |
| EXP-012 | Data audit | Are the RLP answers and rule conditions valid? | [Record](EXPERIMENT_HISTORY.md#exp-012) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-013 | Experiment | Does ordered assistance differ from shuffled assistance? | [Record](EXPERIMENT_HISTORY.md#exp-013) | [Summary](EXPERIMENT_HISTORY.md#historical-result-summary) | [Appendix B.3](tex/project_report.pdf#page=41) |
| EXP-014 | Experiment | Does proof SFT provide a useful starting checkpoint? | [Record](EXPERIMENT_HISTORY.md#exp-014) | In record | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-015 | Evaluation | Does the SFT checkpoint prove target theorems beyond a fixed tactic baseline? | [Record](EXPERIMENT_HISTORY.md#exp-015) | In record | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-016 | Data build | Can external statements support a number-theory evaluation pool? | [Record](EXPERIMENT_HISTORY.md#exp-016) | In record | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-017 | Data build | Can SFT, RL tasks, and development decisions use distinct admitted data? | [Record](EXPERIMENT_HISTORY.md#exp-017) | In record | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-018 | Experiment | Does informal reasoning between Lean tactics improve the starting policy? | [Record](EXPERIMENT_HISTORY.md#exp-018) | Not run | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-019 | Experiment | Does Lean-reward GRPO improve held-out proof completion at matched cost? | [Record](EXPERIMENT_HISTORY.md#exp-019) | [Native parity](EXPERIMENT_HISTORY.md#native-parity-midterm-completion); [fixed mixture](EXPERIMENT_HISTORY.md#fixed-mixture-midterm-completion) | [Appendix B.5](tex/project_report.pdf#page=42); [Appendix B.6](tex/project_report.pdf#page=42) |
| EXP-020 | Exploratory pilot | Can alternating supervised proof practice with either hidden-target RLP or Lean-verified GRPO improve full proof completion? | [Record](EXPERIMENT_HISTORY.md#exp-020) | [Validation recount](EXPERIMENT_HISTORY.md#exp-020-validation-recount-11-october-2026) | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-021 | Experiment | Test verified proof distillation before another Lean RL stage | [Record](EXPERIMENT_HISTORY.md#exp-021) | [Decision log](EXPERIMENT_HISTORY.md#decision-log), 11 October | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-022 | Experiment | Does a learned value function help where within-prompt binary rewards rarely vary? | [Record](EXPERIMENT_HISTORY.md#exp-022) | Not run | [Appendix B.5](tex/project_report.pdf#page=42) |
| EXP-023 | Experiment | Broader complete proofs, PPO and REINFORCE, an error-aware reward, and curriculum experiments | [Record](EXPERIMENT_HISTORY.md#exp-023) | [Final run](EXPERIMENT_HISTORY.md#handoff-final-curriculum-run-results-14-october); [held-out pass@k](EXPERIMENT_HISTORY.md#exp-023-held-out-passk-15-october-2026); [recount](EXPERIMENT_HISTORY.md#exp-023-saved-question-recount-outcome-15-october-2026) | [Appendix B.7](tex/project_report.pdf#page=43); [Appendix E.1](tex/project_report.pdf#page=49) |
| EXP-024 | Experiment | Does SFT on Lean-verified undergraduate number-theory data make the model prove more theorems than the same amount of SFT on the old data? | [Record](EXPERIMENT_HISTORY.md#exp-024) | [Corpus v0](EXPERIMENT_HISTORY.md#exp-024-corpus-v0-outcome-15-october-2026); [screen](EXPERIMENT_HISTORY.md#exp-024-screen-part-2-outcome-15-october-2026); no training | Not in the report |
| AUD-001 | Audit | Which proof errors and available starting points inform task selection? | [Record](EXPERIMENT_HISTORY.md#aud-001) | In record | Section removed in commit `c612b21` |
| AUD-002 | Audit | Can scorer exploits or data exposure invalidate the reward? | [Record](EXPERIMENT_HISTORY.md#aud-002) | In record; [reward inventory](REWARD_HACKING.md) | [Appendix C](tex/project_report.pdf#page=44); [Appendix D](tex/project_report.pdf#page=44) |
| EXP-025 | Data build | Can the broader corpus provide reproducible benchmark and training inputs? | [Record](EXPERIMENT_HISTORY.md#exp-025) | [Expanded dataset](EXPERIMENT_HISTORY.md#exp-025-expanded-dataset-outcome-16-october-2026); [filtered release](EXPERIMENT_HISTORY.md#filtered-release-outcome-17-october-2026); [prerequisite release](EXPERIMENT_HISTORY.md#checked-prerequisite-release-outcome-17-october-2026); [full-corpus mapping](EXPERIMENT_HISTORY.md#full-corpus-mapping-outcome-17-october-2026) | [Appendix F](tex/project_report.pdf#page=50); [Appendix H](tex/project_report.pdf#page=58) |
| EXP-026 | Prospective experiment | How do unchanged Qwen, SFT and SFT + RLVR differ in generalization? | [Revised protocol](EXPERIMENT_HISTORY.md#generalization-experiment-revised-framing-16-october-2026) | [R and E3](EXPERIMENT_HISTORY.md#r-result-and-e3-result-19-october-2026-ist); [dev_R](EXPERIMENT_HISTORY.md#dev_r-result-rl-shifts-s-toward-repeated-short-actions-19-october-2026-ist); [E7](EXPERIMENT_HISTORY.md#e7-result-r-on-the-selection-set-19-october-2026-ist) | [Appendix F](tex/project_report.pdf#page=50); [§4](tex/project_report.pdf#page=8) |
| EXP-027 | Prospective experiment | Does curriculum improve familiar benchmark performance, generalization, or both? | [Protocol](EXPERIMENT_HISTORY.md#exp-027) | [C](EXPERIMENT_HISTORY.md#c-result-curriculum-order-raises-train-acceptance-and-narrows-the-policy-19-october-2026-ist); [dev_C](EXPERIMENT_HISTORY.md#dev_c-result-and-main-lease-end-19-october-2026-ist) | [Appendix F](tex/project_report.pdf#page=50); [§4.4](tex/project_report.pdf#page=15) |
| EXP-028 | Prospective screen | Do step search, automation fallback, lemma retrieval, search-mined SFT and pass-rate-filtered RL raise held-out acceptance over S? | [Protocol](EXPERIMENT_HISTORY.md#exp-028) | [E0](EXPERIMENT_HISTORY.md#e0-result-18-october-2026-ist); [E2](EXPERIMENT_HISTORY.md#e2-result-and-lemma-data-18-october-2026-ist); [E5 and E3](EXPERIMENT_HISTORY.md#e5-result-and-the-e3-comparison-19-october-2026-ist); [E6](EXPERIMENT_HISTORY.md#e6-result-filtered-rl-on-the-selection-set-19-october-2026-ist); [RL_filtered](EXPERIMENT_HISTORY.md#rl_filtered-result-learning-on-the-62-filtered-train-tasks-19-october-2026-ist); [Kimina probe](EXPERIMENT_HISTORY.md#kimina-capability-probe-result-19-october-2026-ist); [why-analysis](EXPERIMENT_HISTORY.md#analysis-why-rl-raises-train-acceptance-without-a-held-out-gain-20-october-2026-ist) | [§4](tex/project_report.pdf#page=8); [§4.6](tex/project_report.pdf#page=16) |
| EXP-029 | Prospective experiment | Do the RL arms differ from S at 8 attempts on the screened 164-theorem selection set and on the 62 RL_filtered theorems? | [Protocol](EXPERIMENT_HISTORY.md#exp-029) | Not run; [stop](EXPERIMENT_HISTORY.md#exp-029-stop) | [Appendix G](tex/project_report.pdf#page=56) |
| EXP-030 | Exploratory pilot | Do per-action Lean-error penalties make REINFORCE close more proofs than the terminal proof reward alone? | [Protocol](EXPERIMENT_HISTORY.md#exp-030) | Not run; [stop](EXPERIMENT_HISTORY.md#exp-030-stop) | [Appendix G](tex/project_report.pdf#page=56) |

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
