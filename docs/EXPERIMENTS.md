# Experiments

This is the experiment catalogue, protocol record, and decision log for this repository.
[README status](../README.md#status) owns current execution status.
The [TeX report](tex/project_report.tex) owns scientific narrative and interpretation.
The [reward inventory](REWARD_HACKING.md) owns exploit details.

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

The records below index documented evidence locations. This catalogue update does not recount results or verify remote artifact availability.
Historical locators retain the report's relative paths. Confirm their machine and durable storage from the original manifest before reuse.
Historical summaries remain below. Their presence does not certify their numbers.
Historical execution owners, seed-level run IDs, and exact commands are not fully indexed.
Local note paths under `artifacts/notes/` are outside Git and are unavailable in a clean public checkout.

## Record contract

Before a new run, complete its protocol in the relevant experiment entry.
Keep resolved execution details in the existing run directory, outside Git.
For historical runs, preserve the original record and list missing fields here.

| Record | Required content | Location |
| --- | --- | --- |
| Question | Research question, hypothesis or exploratory aim, inference boundary | Experiment entry here |
| Responsibility | Scientific owner, execution owner, reviewer; distinguish assigned work from authorship | Experiment entry here |
| Comparison | Arms, starting checkpoint, data exposure, seeds, baseline, controlled variables | Protocol here; resolved settings in run artifacts |
| Evaluation | Primary metric, numerator and denominator, unit of analysis, split, checkpoint selection rule, uncertainty method | Protocol here; per-item outcomes in run artifacts |
| Limits | Token, Lean-call, wall-time, memory, and GPU caps; approval reference for paid work | Protocol and resolved run record |
| Identity | Experiment ID, unique run ID, parent run or checkpoint, UTC timestamps, command, working directory | Existing run record |
| Versions | Git revision and dirty diff; model revision; config and data digests; Lean, mathlib, REPL, and scorer versions | Resolved run record and manifests |
| Inputs | Frozen split membership, exact and near-duplicate audit, excluded items, proof exposure | Data manifests and audit output |
| Outputs | Raw completions, Lean verdicts, error categories, checkpoints, logs, exit code, stop reason | Run directory |
| Recount | Script, command, source file and row or item IDs, exclusions, counted denominator, derived output | Run artifacts; concise evidence link here |
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
6. Append protocol changes and corrections to the decision log. Preserve the original protocol and result.

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
| Experiment catalogue, protocols, evidence index, next actions, decisions | This file |
| Scientific explanation, formal definitions, figures, conclusions | `docs/tex/project_report.tex` |
| Exploit mechanism, fix, residual risk | `docs/REWARD_HACKING.md`, linked to the audit entry here |
| Raw evidence, resolved settings, manifests, recounts, logs, costs | Existing run directories and private operations ledgers |
| Agent workflow | `AGENTS.md`; `CLAUDE.md` links to it |
| Dated notes and scratch handoff | Historical sources; no continuing project status |

Reuse these owners. A new document requires a distinct audience and durable purpose that no existing source serves.
Generated run reports stay with their run and appear in its evidence index.
Do not turn generated reports into another project narrative or status record.
Keep infrastructure identifiers in `.private/infrastructure.md`.
Public records use cpu-angad with paths relative to `formal_rl/`, or the existing redacted S3 prefix.
Preserve historical files and unique evidence. Consolidation does not authorize deletion.

## Experiment register

The original experiment numbers remain stable. Later records extend the catalogue.
Kinds distinguish research experiments, data builds, evaluations, and audits.
Current execution status appears in the README, not in this register.

| ID | Kind | Question or purpose | Record |
| --- | --- | --- | --- |
| EXP-001 | Data build | Can we prepare an isolated LeanDojo theorem subset? | [Record](#exp-001) |
| EXP-002 | Baseline | Does GSM8K LoRA change informal math accuracy? | [Record](#exp-002) |
| EXP-003 | Baseline | Can the unchanged Instruct model generate accepted Lean proofs? | [Record](#exp-003) |
| EXP-004 | Experiment | Does family order change whole-proof GRPO learning? | [Record](#exp-004) |
| EXP-005 | Experiment | Do prerequisite tasks produce an initial Lean success signal? | [Record](#exp-005) |
| EXP-006 | Experiment | Does a proof prefix make curriculum comparison feasible? | [Record](#exp-006) |
| EXP-007 | Experiment | Does RLP training improve independently generated answers? | [Record](#exp-007) |
| EXP-008 | Audit and rerun | Are RLP updates lost through optimizer precision? | [Record](#exp-008) |
| EXP-009 | Experiment | Does primitive-first RLP improve held-out answers? | [Record](#exp-009) |
| EXP-010 | Evaluation | Which capabilities change across saved RLP checkpoints? | [Record](#exp-010) |
| EXP-011 | Experiment | Can a Lean reward improve local witness generation? | [Record](#exp-011) |
| EXP-012 | Data audit | Are the RLP answers and rule conditions valid? | [Record](#exp-012) |
| EXP-013 | Experiment | Does ordered assistance differ from shuffled assistance? | [Record](#exp-013) |
| EXP-014 | Experiment | Does proof SFT provide a useful starting checkpoint? | [Record](#exp-014) |
| EXP-015 | Evaluation | Does the SFT checkpoint solve target theorems beyond a fixed tactic baseline? | [Record](#exp-015) |
| EXP-016 | Data build | Can external statements support a number-theory evaluation pool? | [Record](#exp-016) |
| EXP-017 | Data build | Can SFT, RL tasks, and development decisions use distinct admitted data? | [Record](#exp-017) |
| EXP-018 | Experiment | Does informal reasoning between Lean steps improve the starting policy? | [Record](#exp-018) |
| EXP-019 | Experiment | Does Lean-reward GRPO improve held-out proof completion at matched cost? | [Record](#exp-019) |
| AUD-001 | Audit | Which proof errors and available starting points inform task selection? | [Record](#aud-001) |
| AUD-002 | Audit | Can scorer exploits or data exposure invalidate the reward? | [Record](#aud-002) |

## Adopted inputs and next actions

This is a protocol checkpoint, not a report of live machine state.
The decision log adopts `nt_pool_v2`, `nt_splits_v3`, and scorer `train_code_v11`.
The interleaved SFT config selects `nt_sft_v5` and its comments-removed counterpart.
The data build note indexes `nt_rl_tasks_v3` for RL proof-prefix and whole-proof tasks.
Verify resolved launch inputs against these sources when a run is approved.

| Priority | Required action | Responsible role | Evidence or acceptance condition |
| --- | --- | --- | --- |
| Before GPU launch | Freeze the protocol and run IDs for EXP-018 and EXP-019; update stale lease inputs | Manas coordinates; execution owner recorded per run | Resolved config and launch command match the adopted inputs; cost approved for that launch |
| Before reusing scores | Re-score older subprocess evaluations with the adopted scorer | Evaluation owner: Manas | New rescore record links raw generations, old scorer, new scorer, and recount |
| Before citing results | Index raw evidence and recount the controlled RLP comparison | Evaluation owner: Manas | EXP-013 links the final paired regrade report and its input rows |
| Before updating the report | Reconcile the reward snapshot and catalogue with source artifacts | Scientific owner: Manas; editor assigned for the change | Report and reward inventory name their scorer version and evidence boundary |
| Before interpreting new data | Index external and in-house data builds mentioned in the scratch handoff | Data owner: Manas | Source manifest, split audit, Lean verdicts, and durable output receipt inspected |

### Unresolved evidence and documentation gaps

- Historical result values are not recounted by this catalogue edit. Keep them as historical summaries until their raw sources are inspected.
- EXP-001's historical table predates the later replay recorded in the README. Do not treat that table as current status.
- EXP-013's historical notes cover the ordered arm. The later paired review appears in the README and phase ledger.
- The phase ledger describes multiple prompt, baseline, and scorer corrections. Earlier results need version-specific review before reuse.
- The report and reward inventory describe an earlier scorer than the last adoption decision. Their open-item lists need source-based reconciliation.
- Historical records do not consistently index commands, owners, timestamps, resolved configs, and durable receipts. Do not invent these fields.
- Reservation ledgers do not establish invoiced cost. A historical budget does not authorize a new launch.

## Guidance sources

- [NeurIPS paper checklist](https://neurips.cc/public/guides/PaperChecklist): reproducible settings, evaluation, uncertainty, and compute reporting.
- [MLflow tracking](https://mlflow.org/docs/latest/tracking/): experiments group runs; runs associate parameters, metrics, and artifacts.
- [The Carpentries project organization](https://carpentries-lab.github.io/good-enough-practices/05-project_organization.html): consistent organization and descriptive file names.

These sources guide the record contract. This project keeps its existing runners and artifact stores.

## Historical setup

- Model: `Qwen/Qwen2.5-0.5B-Instruct`. Most later runs start from `checkpoint-118`, an early checkpoint of the primitive RLP run.
- Lean: `v4.10.0-rc1` with mathlib `29dcec074de168ac2bf835a77ef68bbe069194c5`.
- Budget: a $95 total ceiling for all GPU runs. The run ledgers record reservations, not invoices.
- Team: Manas owns data, curriculum, and evaluation. Anwesha owns the interactive Lean environment. Abhinav owns the policy optimizer.
- Artifacts: run outputs live on cpu-angad under `formal_rl/artifacts/` and in S3 under `capstate/acq/formal_rl/`. Bucket names and machine addresses are in the private note `.private/infrastructure.md`.

Two terms appear throughout:

- GRPO: the RL algorithm we use. For each prompt it samples a group of answers and rewards each answer relative to the others in its group. If every answer in a group gets the same reward, the group teaches nothing.
- RLP: a reward that measures how much the model's reasoning makes a hidden correct answer more likely. It does not check whether the reasoning is correct.

## Historical result summary

This retained summary predates the later replay and paired RLP review. It is not current project status.
The original statement that every count matches raw files is not verified by this edit.
"n/m" means n successes out of m items. See each record for source paths and verification gaps.

| # | Experiment | Date | Result |
| --- | --- | --- | --- |
| 1 | LeanDojo data subset | 10-01 | Nat subset: 706 train, 17 validation, 15 test theorems. Broader pool on cpu-angad: 2,658 / 43 / 38. Lean replay not done |
| 2 | Base model math probe + GSM8K LoRA | 10-01 | GSM8K 9/24 before, 8/24 after LoRA. MATH number theory 3/20 both. No gain |
| 3 | Instruct model proof check | 10-01, 10-03 | Lean accepted 0/63 local attempts. A GPU sampling run at four temperatures shows 0/273 in its exported records |
| 4 | Whole-proof GRPO, random vs. family order | 10-03 | 0/8 validation theorems in every phase. No group had reward variation, so no learning signal |
| 5 | Prerequisite curriculum GRPO (+ syntax retry) | 10-04 | 0/256 sampled proofs accepted. Training skipped |
| 6 | Assisted proof completion | 10-04 | Proof prefixes gave 2/64 accepted. Only one theorem qualified, so the order comparison was impossible. Unassisted held-out 0/16 |
| 7 | RLP number-theory training + continuation | 10-04 | Held-out 11/48 before and after. Validation 7/16 before and after |
| 8 | Precision audit + FP32 rerun | 10-04 | Saved optimizer state was BF16 and lost small updates. FP32 rerun: held-out 11/48, validation 7/16 to 6/16 |
| 9 | Primitive RLP curriculum | 10-05 | Held-out fell from 42/108 to 21/108. Validation peaked at 25/56 at checkpoint 118, from 16/56 at the start |
| 10 | Evaluations of saved checkpoints | 10-05 | Early RLP improved explained recognition (14/32 vs. 7/32) but not complete applications or proofs |
| 11 | Local-action pilot, Lean-verified reward | 10-06 | Validation 0/8 to 6/8; held-out 0/4 to 2/4. Error-free answers: 2/8 and 0/4 |
| 12 | RLP corpus audit | 10-06 | All 612 numeric answers correct. 160/448 training rules lack domain or zero conditions |
| 13 | Controlled RLP, ordered arm | 10-06 | 0/18 fully correct at all five checkpoints. Shuffled arm not yet scored |

## Historical experiment records

<a id="exp-001"></a>

### EXP-001: LeanDojo data subset

- Config: `configs/data/leandojo_*.json`.
- Code: [`scripts/data/prepare_leandojo.py`](../scripts/data/prepare_leandojo.py).
- Evidence locator: `artifacts/leandojo_nat_v1/frozen/; artifacts/leandojo_number_theory_v2/`.
- Historical record: Git commit `8fc8423`, `docs/results/01_data_preparation.md`.

We filtered LeanDojo Benchmark 4 to arithmetic and number-theory files and kept the official random splits. We removed training theorems that overlap validation or test text. Text checks do not prove the splits are independent. Script: `scripts/data/prepare_leandojo.py`. Configs: `configs/data/leandojo_*.json`.

<a id="exp-002"></a>

### EXP-002: Base model math probe

- Config: `configs/eval/qwen_05b_probe.json; configs/midtrain/qwen_05b_gsm8k_lora.yaml`.
- Code: [`scripts/eval/probe_qwen.py`](../scripts/eval/probe_qwen.py).
- Evidence locator: `local artifacts/qwen_05b_probe/comparison.json`.
- Historical record: Git commit `8fc8423`, `docs/results/02_qwen_capability_probe.md`.

We tested the base model on 24 GSM8K and 20 MATH number-theory questions, then trained a small LoRA on GSM8K. Validation loss fell from 0.701403 to 0.514280, but accuracy did not improve. Script: `scripts/eval/probe_qwen.py`.

<a id="exp-003"></a>

### EXP-003: Instruct model proof check

- Config: `configs/eval/qwen_05b_probe.json`.
- Code: [`scripts/eval/probe_qwen.py`](../scripts/eval/probe_qwen.py); [`scripts/audit/classify_proof_failures.py`](../scripts/audit/classify_proof_failures.py).
- Evidence locator: `local artifacts/qwen_05b_instruct_proof/summary.json; cpu-angad tensorboard_pilot_20261003/events/`.
- Historical record: Git commit `8fc8423`, `docs/results/02_qwen_capability_probe.md`.
- Gap: GPU sampling counts came from exported monitoring records; the historical note states that an independent recount was not done.

Each of 21 formal goals got three attempts. Lean accepted none. 23/63 responses hit the token limit. A later classification found that none of the 63 was well-formed Lean 4: 57 had a parse or file-structure error, 3 were empty, and 3 used `sorry` (`scripts/audit/classify_proof_failures.py`). A second run on an L40S GPU sampled the same goals greedily and at temperatures 0.3, 0.6, and 0.9: 0 of 273 attempts were accepted. Those GPU counts come from the exported monitoring records; the independent recount was never done.

<a id="exp-004"></a>

### EXP-004: Whole-proof GRPO

- Config: `configs/rl/qwen_05b_whole_proof_grpo.json`.
- Code: [`scripts/train/run_grpo_pilot.py`](../scripts/train/run_grpo_pilot.py).
- Evidence locator: `artifacts/whole_proof_grpo_20261003/`.
- Historical record: Git commit `8fc8423`, `docs/results/03_grpo_proof_pilots.md`.

Both arms ran 32 optimizer steps on identical theorems. The model never produced an accepted proof, so every group had the same zero reward and the gradients stayed at zero. Script: `scripts/train/run_grpo_pilot.py`.

<a id="exp-005"></a>

### EXP-005: Prerequisite curriculum GRPO

- Config: `configs/rl/qwen_05b_prerequisite_grpo*.json`.
- Code: [`scripts/data/prepare_prerequisite_curriculum.py`](../scripts/data/prepare_prerequisite_curriculum.py); [`scripts/train/run_grpo_pilot.py`](../scripts/train/run_grpo_pilot.py).
- Evidence locator: `artifacts/prerequisite_rl_v1/`.
- Historical record: Git commit `8fc8423`, `docs/results/03_grpo_proof_pilots.md`.

We wrote four stages of tasks: logic, arithmetic, divisibility, induction. Before training, we checked whether the model could solve any of them with lemma statements, lemma names, or no help. It solved none, so training was skipped. A retry with Lean 4 syntax templates also solved none.

<a id="exp-006"></a>

### EXP-006: Assisted proof completion

- Config: `configs/rl/qwen_05b_completion_grpo.json`.
- Code: [`scripts/train/run_grpo_pilot.py`](../scripts/train/run_grpo_pilot.py).
- Evidence locator: `artifacts/completion_rl_v1/`.
- Historical record: Git commit `8fc8423`, `docs/results/03_grpo_proof_pilots.md`.

We gave the model most of a proof and asked it to finish. Filling one missing expression worked 2/64 times. Only one theorem qualified for training, so both curriculum arms saw the same single task. The model never solved a held-out theorem without help.

<a id="exp-007"></a>

### EXP-007: RLP number-theory training

- Config: `configs/pretrain/qwen_05b_number_theory_rlp*.json`.
- Code: [`scripts/train/run_rlp_pilot.py`](../scripts/train/run_rlp_pilot.py).
- Evidence locator: `artifacts/number_theory_rlp_v1/; artifacts/number_theory_rlp_v2/audit_final/`.
- Historical record: Git commit `8fc8423`, `docs/results/04_rlp_acquisition.md`.

We trained with the RLP reward on number-theory text, first in textbook order, then primitives first. Scores did not change. The checkpoint is saved in S3 under `number_theory_rlp_20261004_continuation/`.

<a id="exp-008"></a>

### EXP-008: Precision audit

- Config: `configs/midtrain/qwen_05b_number_theory_rlp_fp32.json`.
- Code: [`scripts/audit/audit_rlp_updates.py`](../scripts/audit/audit_rlp_updates.py); [`scripts/train/run_rlp_pilot.py`](../scripts/train/run_rlp_pilot.py).
- Evidence locator: `artifacts/number_theory_rlp_v2/update_audit/; artifacts/number_theory_rlp_fp32_midtraining/`.
- Historical record: Git commit `8fc8423`, `docs/results/04_rlp_acquisition.md`.

Only 8,557,971 of 494,032,768 weights changed between checkpoints. The optimizer stored its state in BF16, which rounds away small updates. We reran in FP32. Updates then worked, but scores still did not improve. Script: `scripts/audit/audit_rlp_updates.py`.

<a id="exp-009"></a>

### EXP-009: Primitive RLP curriculum

- Config: `configs/midtrain/qwen_05b_primitive_rlp.json`.
- Code: [`scripts/train/run_rlp_pilot.py`](../scripts/train/run_rlp_pilot.py); [`scripts/audit/audit_primitive_rlp.py`](../scripts/audit/audit_primitive_rlp.py).
- Evidence locator: `primitive_midtraining_v1/audit_final/diagnosis/`.
- Historical record: Git commit `8fc8423`, `docs/decisions.md` and `docs/team_workstreams.md`.

We trained on 448 authored examples in dependency order, from witnesses to primes and congruences. Held-out accuracy halved by the end. Training scored a supplied answer prefix, while evaluation asked the model to write the answer itself. That mismatch is a possible cause, but no test isolates it. The RLP reward rose over the same run, so the reward and correctness moved apart. Script: `scripts/audit/audit_primitive_rlp.py`.

<a id="exp-010"></a>

### EXP-010: Evaluations of saved checkpoints

- Config: `configs/eval/acquisition_probe_v1.json; configs/eval/decomposition_probe_v2.json; configs/eval/knowledge_inventory_v1.json`.
- Code: [`scripts/eval/evaluate_acquisition.py`](../scripts/eval/evaluate_acquisition.py).
- Evidence locator: `artifacts/acquisition_eval_v1/; artifacts/decomposition_eval_v2/; artifacts/knowledge_inventory_v1/`.
- Historical record: Git commit `8fc8423`, `docs/results/05_acquisition_evaluations.md`.

We compared the original model, early RLP (`checkpoint-118`), and final RLP on recall, application, proof steps, and full proofs. The original completed 4/24 acquisition tasks; each RLP checkpoint completed 2/24. Final RLP hit the length limit on 26/80 responses. Two AI graders scored every answer; they disagreed on 44/321. Script: `scripts/eval/evaluate_acquisition.py`.

<a id="exp-011"></a>

### EXP-011: Local-action pilot

- Config: `configs/rl/qwen_05b_local_operator_grpo.json`.
- Code: [`scripts/train/local_operator_training.py`](../scripts/train/local_operator_training.py); [`scripts/eval/verify_local_operator.py`](../scripts/eval/verify_local_operator.py).
- Evidence locator: `operator_pilot_v1/gpu_records/out/`.
- Historical record: Git commit `8fc8423`, `docs/results/06_local_action_pilot.md`.

The model had to supply a witness, for example `k*l` for `c = a*(k*l)`. Lean checked the witness, and the reward was 1 or 0. After 64 steps, correct witnesses on validation rose from 0/8 to 6/8. The explanations around them often stayed wrong. Lean checks only the witness. Scripts: `scripts/train/local_operator_training.py`, `scripts/eval/verify_local_operator.py`.

<a id="exp-012"></a>

### EXP-012: RLP corpus audit

- Config: `configs/data/mathematical_rlp_review_v1.json`.
- Code: [`scripts/audit/audit_rlp_corpus.py`](../scripts/audit/audit_rlp_corpus.py).
- Evidence locator: `artifacts/rlp_data_audit_v1/final/`.
- Historical record: Git commit `8fc8423`, `docs/results/07_rlp_corpus_audit.md`.

Every numeric answer in the primitive corpus was correct. 160 of the 448 training rules left out conditions, for example that a modular inverse needs gcd(a, n) = 1. A constant answer solved every contradiction item, so some tasks had shortcuts. Script: `scripts/audit/audit_rlp_corpus.py`.

<a id="exp-013"></a>

### EXP-013: Controlled RLP

- Config: `configs/midtrain/qwen_05b_controlled_rlp.json`.
- Code: [`scripts/data/mathematical_rlp_data.py`](../scripts/data/mathematical_rlp_data.py); [`scripts/train/run_rlp_pilot.py`](../scripts/train/run_rlp_pilot.py); [`scripts/eval/regrade_controlled_rlp.py`](../scripts/eval/regrade_controlled_rlp.py).
- Evidence locator: `artifacts/mathematical_pretraining_v1/; artifacts/controlled_ordered_review_20261006/; artifacts/controlled_shuffled_review_20261009/`.
- Historical record: Git commit `8fc8423`, `docs/results/08_controlled_rlp.md`.
- Later review: `artifacts/notes/2026-10-09_phase0_ledger.md` (local historical note), first Update; recount script above writes `report.json`. Exact final report path is not indexed.

We built 72 training examples, each shown with decreasing help: full, partial, recognition, recall, independent. The ordered arm runs them in that order; the shuffled arm sees the same 432 presentations in random order. The ordered arm ran 432 steps and never produced a fully correct answer. Partial credit rose from 0.89 to 1.33, then fell to 0.72. Scripts: `scripts/data/mathematical_rlp_data.py`, `scripts/train/run_rlp_pilot.py`, `scripts/eval/grade_mathematical_responses.py`.

## Historical interpretation

1. Lean-checked rewards produce no learning signal when the model never succeeds. This blocked every whole-proof experiment.
2. A Lean-checked reward worked once the task was small enough for the model to sometimes succeed.
3. The RLP reward can rise while correctness falls. Correct training answers do not make the model's reasoning correct.
4. None of the curriculum experiments has yet given a fair comparison of ordered against shuffled training.
5. AI graders disagree often enough that correctness scores need a checker, which motivates Lean.

## Historical next steps

These are the earlier proposed steps, retained as history. They are superseded by the adopted inputs and next actions above.
Do not use this list as execution status.

1. Score the shuffled arm of the controlled RLP study.
2. Replay the training proofs in Lean. Measure how often the unchanged model finishes a proof when it starts 1, 2, or 3 steps before the end.
3. Check the unchanged checkpoints with Lean on fixed theorem statements, next to a baseline that uses no model.
4. Then train on Lean 4 proofs, run expert iteration, and compare curricula and rewards with GRPO. Section 11 of the report gives the design.

## Later experiment and audit records

<a id="exp-014"></a>

### EXP-014: Lean SFT on mathlib proofs

- Config: `configs/midtrain/qwen_05b_lean_sft_v1.json`.
- Code: [`scripts/data/build_sft_data.py`](../scripts/data/build_sft_data.py); [`scripts/train/run_sft.py`](../scripts/train/run_sft.py).
- Evidence locator: `cpu-angad sft_checkpoints/v2_final/; run prefix lean_sft_20261009_v2`
- Source record: `artifacts/notes/2026-10-09_phase0_ledger.md` (local historical note).
- Boundary or gap: The config records a failed initial lease and a changed batch size. Keep both launch records. The exact archived run.json location needs indexing.

<a id="exp-015"></a>

### EXP-015: Lean evaluation and tactic search

- Config: No single frozen evaluation config indexed.
- Code: [`scripts/eval/lean_eval_statements.py`](../scripts/eval/lean_eval_statements.py); [`scripts/eval/probe_nt_pool.py`](../scripts/eval/probe_nt_pool.py); [`scripts/eval/search_tactics.py`](../scripts/eval/search_tactics.py); [`scripts/eval/single_tactic_baseline.py`](../scripts/eval/single_tactic_baseline.py).
- Evidence locator: `cpu-angad val_eval_v4/; search_v3_sftfinal/; search_v3_fixed/; nt_probe/train200_v3_merged/summary.json`
- Source record: `artifacts/notes/2026-10-09_phase0_ledger.md` (local historical note).
- Boundary or gap: These evaluation populations and scorers differ. Keep separate run IDs. Re-score older subprocess verdicts before reuse; do not pool them with current REPL verdicts.

<a id="exp-016"></a>

### EXP-016: Number-theory statement pool

- Config: No standalone frozen build config indexed.
- Code: [`scripts/data/build_nt_pool.py`](../scripts/data/build_nt_pool.py); [`scripts/data/build_nt_states.py`](../scripts/data/build_nt_states.py); [`scripts/data/verify_external_proofs.py`](../scripts/data/verify_external_proofs.py).
- Evidence locator: `cpu-angad nt_pool_v2/`
- Source record: `artifacts/notes/2026-10-09_phase0_ledger.md` (local historical note).
- Boundary or gap: Pool admission and proof validity do not establish that the formal statement matches the source problem. Pin the source manifest and admission command.

<a id="exp-017"></a>

### EXP-017: Midsem splits and training data

- Config: Input versions appear in the decision log and interleaved SFT config.
- Code: [`scripts/data/build_nt_splits.py`](../scripts/data/build_nt_splits.py); [`scripts/data/build_nt_sft_data.py`](../scripts/data/build_nt_sft_data.py); [`scripts/data/build_nt_hole_tasks.py`](../scripts/data/build_nt_hole_tasks.py); [`scripts/audit/check_split_leakage.py`](../scripts/audit/check_split_leakage.py).
- Evidence locator: `cpu-angad nt_splits_v3/summary.json; nt_splits_v3_audit/leakage_check.json; nt_sft_v5/; nt_sft_v5_nocomments/; nt_rl_tasks_v3/tasks.jsonl`
- Source record: `artifacts/notes/2026-10-10_data_build_report.md` (local historical note).
- Boundary or gap: Use the final outputs section of the source note. Earlier rebuilds remain historical. Similarity filtering does not establish semantic independence.

<a id="exp-018"></a>

### EXP-018: Interleaved number-theory SFT

- Config: `configs/midtrain/qwen_05b_nt_interleaved_sft_v1.json`.
- Code: [`scripts/train/run_sft.py`](../scripts/train/run_sft.py).
- Evidence locator: `Planned inputs: cpu-angad nt_sft_v5/ and nt_sft_v5_nocomments/. Output run IDs not assigned.`
- Source record: `artifacts/notes/2026-10-10_midsem_plan.md` (local historical note).
- Boundary or gap: Compare comments with comments removed using the same examples and starting checkpoint. Choose the arm on DEV. The dated plan predates the final splits; use the adopted inputs above.

<a id="exp-019"></a>

### EXP-019: Interactive Lean RL

- Config: `configs/rl/qwen_05b_lean_grpo_smoke.json` is a smoke config, not a frozen GPU protocol.
- Code: [`scripts/train/run_lean_grpo.py`](../scripts/train/run_lean_grpo.py); [`scripts/eval/pass_rate_map.py`](../scripts/eval/pass_rate_map.py); [`scripts/data/build_rft_data.py`](../scripts/data/build_rft_data.py).
- Evidence locator: `Planned inputs: cpu-angad nt_splits_v3/ and nt_rl_tasks_v3/. GPU output run IDs not assigned.`
- Source record: `artifacts/notes/2026-10-10_midsem_plan.md` (local historical note).
- Registered comparison: SFT start, real Lean reward, shuffled reward, and rejection-sampling fine-tuning (RFT), with matched generated-token budgets.
- Primary endpoint: fully completed proofs on the non-trivial validation population defined by the adopted split. Report the full population separately.
- Selection: choose the starting checkpoint and task mix on DEV. Keep held-out test and miniF2F outside adaptive decisions.
- Missing launch fields: frozen GPU configs, exact sampling count, seed list, run IDs, output paths, execution owner, reviewer, and launch approval.
- Boundary: CPU smoke evidence does not establish GPU training or a research result. Do not reuse a smoke config as the study protocol.

<a id="aud-001"></a>

### AUD-001: Proof failures and curriculum inventory

- Config: Historical post hoc analysis; no single frozen config indexed.
- Code: [`scripts/audit/classify_proof_failures.py`](../scripts/audit/classify_proof_failures.py); [`scripts/audit/curriculum_inventory.py`](../scripts/audit/curriculum_inventory.py).
- Evidence locator: `Source paths are in the report section labelled sec:posthoc.`
- Source record: [`project_report.tex`](../docs/tex/project_report.tex).
- Boundary or gap: This is a post hoc diagnostic, not a registered curriculum-effect test.

<a id="aud-002"></a>

### AUD-002: Lean reward and engineering audit

- Config: Scorer adoption appears in the decision log.
- Code: [`scripts/eval/check_checker_attacks.py`](../scripts/eval/check_checker_attacks.py); [`scripts/eval/bench_lean_verifier.py`](../scripts/eval/bench_lean_verifier.py); [`scripts/eval/check_scoring_paths.py`](../scripts/eval/check_scoring_paths.py).
- Evidence locator: `cpu-angad trainer_checker_v1/; trainer_checker_v1/attacks_v11/summary.json; eng_audit_v1/`
- Source record: `artifacts/notes/2026-10-10_trainer_checker_report.md` (local historical note).
- Additional sources: `artifacts/notes/2026-10-10_engineering_audit.md` (local historical note) and [`REWARD_HACKING.md`](REWARD_HACKING.md).
- Boundary or gap: The reward inventory and report describe an earlier scorer snapshot. Use separate scorer-specific audit records. Attack controls do not establish an exhaustive security guarantee.

## Decision log

Add one dated line per decision. Do not edit old lines.

- 2026-09-24: Set up the repository. Use Lean 4 only.
- 2026-10-01: Freeze the Nat subset as a pipeline test. Probe the base model locally. Fix the Instruct model for RL.
- 2026-10-01: Curate the broader data pool on cpu-angad and archive it privately in Drive.
- 2026-10-03: Run a $10 frozen-model diagnostic on a Spot L40S, then a separate whole-proof GRPO pilot.
- 2026-10-04: Raise the budget ceiling to $64. Test prerequisite tasks with decreasing help, then assisted completion.
- 2026-10-04: Move to number-theory learning with RLP before proof RL. Rerun in FP32 after the precision audit.
- 2026-10-05: Run the primitive curriculum. Investigate its decline on CPU. Select `checkpoint-118` as the training start.
- 2026-10-05: Evaluate saved checkpoints with blinded AI grading and partial credit.
- 2026-10-06: Run the local-action pilot with a Lean-checked witness reward.
- 2026-10-06: Defer Lean, audit the RLP corpus, and prepare the controlled ordered-vs-shuffled comparison. Raise the ceiling to $95.
- 2026-10-06: Propose a Lean-verified evaluation as the next study.
- 2026-10-07: Reorganize scripts by stage, remove machine identifiers from the public repo, and merge the docs into this file.
- 2026-10-09: Expand the TeX report to cover all 13 experiments, add the GPU sampling run to Experiment 3, and add two post hoc audit scripts.
- 2026-10-09: Manas approved: extend the development set with LeanDojo random/test under the same selection rule; remove task-bank units that share a file with a development theorem from RL training; build the GRPO trainer with the Lean REPL reward in this repo (the optimizer is listed under Abhinav); GPU use within the 145 USD ceiling, each launch confirmed with its cost.
- 2026-10-10: Manas set the post-training direction: interleaved informal reasoning and Lean in one model (no separate translator in the RL loop), longer outputs accepted, and more Lean-verified samples through offline steps (SFT, distillation, expert iteration, proof-prefix tasks) and online steps (GRPO with Lean reward), after the engineering audit passes.
- 2026-10-10: Adopt nt_pool_v2 (4,306 Lean Workbook number-theory statements; drops 86 statements that use sqrt, pi, Real.*, or Complex.*) and nt_sft_v2; probe, pass-rate map, and GRPO trainer now share one REPL scorer (verify_group), after the engineering audit found a batch-file error-attribution bug and an implicit repetition penalty of 1.1 from the checkpoint generation config.
- 2026-10-10: Adopt nt_splits_v3 for the midsem RL study: exclude train statements with token Jaccard >= 0.85 to a validation or test statement and >= 0.7 to any miniF2F test statement, plus statements whose hypotheses prove False; hold out 25% of proved problems from SFT as the source of RL proof-prefix tasks; a 300-statement DEV slice for all decisions; primary metric on the 392 validation statements no single tactic proves, reported by nearest-train-similarity band.
- 2026-10-10: Adopt scorer v11 (train_code_v11) as the reward and evaluation check for every arm: axiom report read only at the target's #print axioms line, one-command parse check, FORBIDDEN extended with metaprogram commands and observe, and FORBIDDEN applied only outside Lean comments, so informal reasoning in comments cannot cause a rejection (12 of 1,153 nt_sft_v5 rows had been falsely rejected for the word observe). The old subprocess check in probe_start_depth.py is not used for new results; earlier results it scored are re-scored before reuse. The shuffled-reward arm replaces the Bernoulli random-reward arm. Lean timeout: 20 s for RL rewards and pass-rate maps, 60 s for final validation and test scoring.
- 2026-10-10: Establish stable experiment IDs and a single catalogue in this file. Keep current status in README, scientific narrative in the TeX report, and raw evidence in run artifacts. Preserve dated notes as history; create no new per-session Markdown records. Historical results remain un-recounted by this documentation change.
