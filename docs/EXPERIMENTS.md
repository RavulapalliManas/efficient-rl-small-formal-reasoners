# Experiments

Every experiment we ran, what happened, and where the outputs are. The full report is [`tex/project_report.pdf`](tex/project_report.pdf). The older, longer run records are in Git commit `8fc8423`, under `docs/`.

## Setup

- Model: `Qwen/Qwen2.5-0.5B-Instruct`. Most later runs start from `checkpoint-118`, an early checkpoint of the primitive RLP run.
- Lean: `v4.10.0-rc1` with mathlib `29dcec074de168ac2bf835a77ef68bbe069194c5`.
- Budget: a $95 total ceiling for all GPU runs. The run ledgers record reservations, not invoices.
- Team: Manas owns data, curriculum, and evaluation. Anwesha owns the interactive Lean environment. Abhinav owns the policy optimizer.
- Artifacts: run outputs live on cpu-angad under `formal_rl/artifacts/` and in S3 under `capstate/acq/formal_rl/`. Bucket names and machine addresses are in the private note `.private/infrastructure.md`.

Two terms appear throughout:

- GRPO: the RL algorithm we use. For each prompt it samples a group of answers and rewards each answer relative to the others in its group. If every answer in a group gets the same reward, the group teaches nothing.
- RLP: a reward that measures how much the model's reasoning makes a hidden correct answer more likely. It does not check whether the reasoning is correct.

## Results by experiment

Every count below matches the raw run files. "n/m" means n successes out of m items.

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

## Notes on each experiment

### 1. LeanDojo data subset

We filtered LeanDojo Benchmark 4 to arithmetic and number-theory files and kept the official random splits. We removed training theorems that overlap validation or test text. Text checks do not prove the splits are independent. Script: `scripts/data/prepare_leandojo.py`. Configs: `configs/data/leandojo_*.json`.

### 2. Base model math probe

We tested the base model on 24 GSM8K and 20 MATH number-theory questions, then trained a small LoRA on GSM8K. Validation loss fell from 0.701403 to 0.514280, but accuracy did not improve. Script: `scripts/eval/probe_qwen.py`.

### 3. Instruct model proof check

Each of 21 formal goals got three attempts. Lean accepted none. 23/63 responses hit the token limit. A later classification found that none of the 63 was well-formed Lean 4: 57 had a parse or file-structure error, 3 were empty, and 3 used `sorry` (`scripts/audit/classify_proof_failures.py`). A second run on an L40S GPU sampled the same goals greedily and at temperatures 0.3, 0.6, and 0.9: 0 of 273 attempts were accepted. Those GPU counts come from the exported monitoring records; the independent recount was never done.

### 4. Whole-proof GRPO

Both arms ran 32 optimizer steps on identical theorems. The model never produced an accepted proof, so every group had the same zero reward and the gradients stayed at zero. Script: `scripts/train/run_grpo_pilot.py`.

### 5. Prerequisite curriculum GRPO

We wrote four stages of tasks: logic, arithmetic, divisibility, induction. Before training, we checked whether the model could solve any of them with lemma statements, lemma names, or no help. It solved none, so training was skipped. A retry with Lean 4 syntax templates also solved none.

### 6. Assisted proof completion

We gave the model most of a proof and asked it to finish. Filling one missing expression worked 2/64 times. Only one theorem qualified for training, so both curriculum arms saw the same single task. The model never solved a held-out theorem without help.

### 7. RLP number-theory training

We trained with the RLP reward on number-theory text, first in textbook order, then primitives first. Scores did not change. The checkpoint is saved in S3 under `number_theory_rlp_20261004_continuation/`.

### 8. Precision audit

Only 8,557,971 of 494,032,768 weights changed between checkpoints. The optimizer stored its state in BF16, which rounds away small updates. We reran in FP32. Updates then worked, but scores still did not improve. Script: `scripts/audit/audit_rlp_updates.py`.

### 9. Primitive RLP curriculum

We trained on 448 authored examples in dependency order, from witnesses to primes and congruences. Held-out accuracy halved by the end. Training scored a supplied answer prefix, while evaluation asked the model to write the answer itself. That mismatch is a possible cause, but no test isolates it. The RLP reward rose over the same run, so the reward and correctness moved apart. Script: `scripts/audit/audit_primitive_rlp.py`.

### 10. Evaluations of saved checkpoints

We compared the original model, early RLP (`checkpoint-118`), and final RLP on recall, application, proof steps, and full proofs. The original completed 4/24 acquisition tasks; each RLP checkpoint completed 2/24. Final RLP hit the length limit on 26/80 responses. Two AI graders scored every answer; they disagreed on 44/321. Script: `scripts/eval/evaluate_acquisition.py`.

### 11. Local-action pilot

The model had to supply a witness, for example `k*l` for `c = a*(k*l)`. Lean checked the witness, and the reward was 1 or 0. After 64 steps, correct witnesses on validation rose from 0/8 to 6/8. The explanations around them often stayed wrong. Lean checks only the witness. Scripts: `scripts/train/local_operator_training.py`, `scripts/eval/verify_local_operator.py`.

### 12. RLP corpus audit

Every numeric answer in the primitive corpus was correct. 160 of the 448 training rules left out conditions, for example that a modular inverse needs gcd(a, n) = 1. A constant answer solved every contradiction item, so some tasks had shortcuts. Script: `scripts/audit/audit_rlp_corpus.py`.

### 13. Controlled RLP

We built 72 training examples, each shown with decreasing help: full, partial, recognition, recall, independent. The ordered arm runs them in that order; the shuffled arm sees the same 432 presentations in random order. The ordered arm ran 432 steps and never produced a fully correct answer. Partial credit rose from 0.89 to 1.33, then fell to 0.72. Scripts: `scripts/data/mathematical_rlp_data.py`, `scripts/train/run_rlp_pilot.py`, `scripts/eval/grade_mathematical_responses.py`.

## What we learned

1. Lean-checked rewards produce no learning signal when the model never succeeds. This blocked every whole-proof experiment.
2. A Lean-checked reward worked once the task was small enough for the model to sometimes succeed.
3. The RLP reward can rise while correctness falls. Correct training answers do not make the model's reasoning correct.
4. None of the curriculum experiments has yet given a fair comparison of ordered against shuffled training.
5. AI graders disagree often enough that correctness scores need a checker, which motivates Lean.

## Next steps

These are proposed. None has run.

1. Score the shuffled arm of the controlled RLP study.
2. Replay the training proofs in Lean. Measure how often the unchanged model finishes a proof when it starts 1, 2, or 3 steps before the end.
3. Check the unchanged checkpoints with Lean on fixed theorem statements, next to a baseline that uses no model.
4. Then train on Lean 4 proofs, run expert iteration, and compare curricula and rewards with GRPO. Section 11 of the report gives the design.

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
