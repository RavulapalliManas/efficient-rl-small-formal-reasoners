# GRPO proof pilots

This record moved verbatim from `README.md` on 2026-10-07. The [README status table](../../README.md#baselines-and-status) remains the canonical status record.

## Separate whole-proof GRPO pilot

[The pilot config](../../configs/rl/qwen_05b_whole_proof_grpo.json) fixes the Instruct checkpoint, TRL version, full-parameter training, terminal reward, and resource caps. [Preparation](../../scripts/data/prepare_grpo_pilot.py) uses training headers and available compiled imports. It excludes gold traces, missing contexts, and targets already present in the environment. CPU artifacts remain under `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/whole_proof_grpo_20261003/`; failed preparation attempts remain separate.

[The runner](../../scripts/train/run_grpo_pilot.py) compares random order with a source-family curriculum. Both arms receive identical theorem occurrences and reload the same initial checkpoint. This curriculum is a prerequisite heuristic, not a validated dependency order or an optimum. The pilot uses terminal Lean acceptance without a learned reward model or intermediate reward. A group without reward variation supplies no comparative correctness signal. Optimizer steps alone do not establish learning.

The pilot evaluates the unchanged policy and each final policy on the same diagnostic validation prompts. This is not a held-out undergraduate benchmark. The registered interactive reward comparison remains in [the research plan](../research_plan.md). Whole-proof generation does not implement Anwesha's interaction or Abhinav's shared optimizer.

The completed pilot uses tmux `formal-rl-whole-proof-grpo` on its L40S. S3 prefix `capstate/acq/formal_rl/whole_proof_grpo_20261003_retry2/` stores its receipt, budget reservation, shipped code, and runtime records. Earlier prefixes preserve failed launches and partial results. The instance terminates before its guard deadline `2026-10-03T17:36:10Z`. No automatic replacement is configured. The current exporter uses tmux `formal-rl-grpo-tensorboard-final` and adds `grpo_final/random` and `grpo_final/curriculum` to the same TensorBoard server. Signed source URLs remain private. Earlier monitoring attempts remain preserved. Cumulative token and Lean-call counters cover the entire worker; use phase-specific rollout records for arm comparisons.

This retry saves one final full checkpoint per arm. A Spot interruption before an arm finishes loses its resumable model state. Raw rollout records still stream to S3. Free-disk measurements now accompany trainer rows; the failed worker did not record them.

The CPU recount is `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/whole_proof_grpo_20261003/audit_retry2/summary.json`. S3 `audit/summary.json` and `audit/recount.py` preserve its results and code. The recount verifies actual theorem order, paired validation attempts, token counts, reward records, and true/false checker controls. It exits 0. The following table reads those raw records, rather than treating optimizer steps as learning.

| Diagnostic validation phase | Solved theorems | Accepted attempts |
| --- | ---: | ---: |
| Unchanged policy | 0 / 8 | 0 / 32 |
| After random-order GRPO | 0 / 8 | 0 / 32 |
| After source-family curriculum GRPO | 0 / 8 | 0 / 32 |

32 optimizer steps complete per arm in `runtime/out/run.json`. 128 training rollouts occur per arm in its `rollouts.jsonl`. No training proof is accepted. No sampled group contains both accepted and rejected proofs. Recorded gradient norms remain zero. Paired validation response texts remain unchanged after each arm. This pilot supplies no evidence of curriculum learning or a model capability ceiling.

39,452 generated tokens cover the completed worker in the recount. Failed and partial runs have additional costs in their separate records. Active GPU computation time is not measured. The training-phase budget ledger contains conservative reservations, not an invoice. Before another curriculum comparison, measure reward variation on an easier training pool.

## Prerequisite curriculum diagnostic, 2026-10-04

[The failure audit](../../scripts/audit/audit_proofs.py) reads earlier proof responses and compiler diagnostics. Its labels can overlap. Unknown symbols can be lemmas, variables, or tactics. An unfinished proof does not establish incorrect mathematical reasoning. Reaching the token cap remains separate from the compiler error.

The audit finds checker diagnostic text inside some earlier training goals. This confounds capability interpretation of that pilot. Its zero-reward outcome remains valid. The new preparation removes this diagnostic text from prompts. CPU artifacts stay under `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/prerequisite_rl_v1/`.

[The authored curriculum](../../scripts/data/prepare_prerequisite_curriculum.py) progresses through equality, natural-number rewrites, divisibility, and induction. It is an elementary diagnostic, not a public undergraduate benchmark. Preparation verifies reference proofs and support declarations under the pinned environment. Reference proofs belong to private verification artifacts and source snapshots. They do not enter model inputs or the GPU package.

[The config](../../configs/rl/qwen_05b_prerequisite_grpo.json) compares random order with prerequisite order. Both arms reload the same policy and receive identical theorem occurrences. Both use complete passes with lemma statements, then lemma names, then no task-specific assistance. Imports and recursive definitions remain identical. This comparison tests ordering under a shared assistance schedule. It does not isolate the effect of assistance or enforce premise unlocking.

The runner first measures training reward variation at each assistance level. It skips optimizer updates if every sampled group has constant reward. Baseline and final evaluation use unassisted diagnostic validation and separately identified held-out compositions. Evaluation proofs never enter the GPU package. Exact and textual near-duplicate checks precede generation. Textual separation does not establish semantic independence.

Spot instance `<ec2-instance-id>` uses tmux `formal-rl-prerequisite`. Its independent cpu-angad guard terminates it by `2026-10-04T12:03:46Z`. S3 prefix `capstate/acq/formal_rl/prerequisite_rl_20261004/` stores its launch receipt, cumulative budget ledger, code, and streamed runtime records. The ledger includes earlier reservations against the total ceiling. Reservations are conservative bounds, not invoices. Setup failures and partial runs remain preserved.

The completed readiness result is in CPU `audit_gpu/summary.json` under the artifact root above. S3 `audit/recount/summary.json` and `audit/recount/recount.py` preserve the independently recounted values and code. The recount validates recorded rewards, actual token IDs, assistance levels, checker controls, and physical Lean calls. It exits 0.

| Initial-policy assistance | Accepted proofs / attempts | Solved training theorems / sampled theorems |
| --- | ---: | ---: |
| Definitions, lemma statements, guidance | 0 / 64 | 0 / 16 |
| Lemma names | 0 / 64 | 0 / 16 |
| No task-specific assistance | 0 / 64 | 0 / 16 |
| Definitions, lemma statements, guidance, generic Lean 4 syntax | 0 / 64 | 0 / 16 |

No sampled group has reward variation. The runner skips optimizer updates. Diagnostic validation and held-out compositions remain unevaluated. No decreasing-assistance training or curriculum comparison occurs. These samples do not establish a model capability ceiling or a need for SFT.

30,027 generated tokens cover the readiness worker in the recount. 218 physical Lean calls cover contexts, checker controls, and candidate verification. The worker returns exit 0. Active GPU computation time remains unmeasured.

The raw failures include whole declarations where tactic bodies were requested, obsolete Lean syntax, unknown symbols, and unfinished goals. Unknown symbols include variable names and syntax words, not just lemmas. The instance reports terminated. Its termination observation remains in S3.

[The syntax-assisted retry](../../configs/rl/qwen_05b_prerequisite_grpo_syntax.json) adds generic Lean 4 tactic forms during full assistance. These forms follow [Lean's tactic reference](https://lean-lang.org/doc/reference/latest/Tactic-Proofs/Tactic-Reference/). They contain placeholders, not task-specific reference proofs. Names-only and unassisted prompts remain unchanged. Readiness samples the changed full-assistance prompts before optimization. The checker, reward, optimizer, theorem occurrences, and unassisted evaluation stay fixed. The original result remains immutable. Any gain from templates is a prompt-conditioning result until an RL comparison runs.

The retry uses Spot instance `<ec2-instance-id>`, tmux `formal-rl-prerequisite-syntax`, and S3 prefix `capstate/acq/formal_rl/prerequisite_rl_20261004_syntax/`. The independent guard deadline is `2026-10-04T12:31:46Z`. CPU export `prepared_syntax/` preserves fresh context and reference verification. The current TensorBoard exporter uses tmux `formal-rl-prerequisite-syntax-events`; its event namespace is `prerequisite_20261004_syntax`.

The syntax retry also returns no accepted proof and skips GRPO updates. Its worker and CPU recount return exit 0. The instance reports terminated. CPU `audit_gpu_syntax/summary.json` and S3 `audit/recount/summary.json` preserve this result. The combined record is CPU `combined_readiness.json`, archived beside the retry recount. It counts repeated training theorems across prompt conditions, not independent benchmark samples.

The combined record counts 256 sampled proofs. None is accepted. Its prompt check confirms unchanged names-only and unassisted prompts between exports. No optimizer arm runs. Unassisted validation and unseen-combination policy evaluation remain unrun. The syntax templates do not establish RL readiness in this sample.

The next experiment must establish verified candidate output before comparing curricula. A constrained tactic baseline would change the action space and needs separate registration. Neither knowledge acquisition through RL nor a need for supervised adaptation follows from these readiness failures.

## Assisted proof completion, 2026-10-04

The user authorizes training-proof prefixes as external assistance. This supersedes the earlier gold-free prompt restriction for this separate study. The [preparer](../../scripts/data/prepare_prerequisite_curriculum.py) supports `--completion-tasks`. It exports missing-expression, missing-tactic, and missing-body forms for training. Evaluation statements contain no solution prefixes. Full references and evaluation proofs remain in CPU verification artifacts. For a one-tactic proof, the missing-tactic and missing-body forms are identical. A missing expression in a `simp` list includes its closing delimiter in the continuation.

The [config](../../configs/rl/qwen_05b_completion_grpo.json) uses the existing pinned model, checker, and TRL. Its frozen-policy probe compares the existing prompt, a prefilled code block, a missing-expression prefix, and a missing-tactic prefix. Each condition uses identical theorem membership, decoding, generation length, and attempt limits. [Transformers supports prefilling](https://huggingface.co/docs/transformers/v4.48.0/en/chat_templating). [TRL 0.24.0](https://github.com/huggingface/trl/blob/v0.24.0/trl/trainer/grpo_trainer.py) reconstructs the supplied assistant prefix for reward evaluation and applies the policy loss to generated continuation tokens. The runner removes the supplied opening fence before checking the reconstructed proof body. It does not repair Lean syntax.

Training membership freezes after the probe. A theorem enters when a prefilled condition produces both accepted and rejected proofs. Its initial assistance uses the first such condition in expression, tactic, then body order. Random and prerequisite order use the same selected tasks and initial assistance. Both arms reload the original policy before their first round. Subsequent rounds preserve each arm's policy and Adam moments. The learning rate remains constant.

Each round gives both arms a complete pass over the selected tasks. Both then receive unassisted curriculum validation. Assistance decreases by one level for the next round only when both arms solve at least the configured number of validation theorems. The schedule stays shared across arms. Held-out evaluation occurs at the original and final policies. Held-out outcomes do not control selection or assistance. If the probe selects no theorem, the runner records the frozen baseline and skips training. A single selected theorem cannot identify an ordering effect.

The comparison matches opportunities and resource ceilings. Realized generated tokens and Lean calls can differ. Results must compare the shared realized resource range and report counts beside solve rates. This single-seed elementary study cannot establish undergraduate capability, an optimal curriculum, or learning without demonstration information.

CPU preparation is under `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/completion_rl_v1/prepared/`. The export contains 16 training theorems. It supplies 48 completion forms across those training theorems. Curriculum validation contains four theorems. Held-out evaluation contains four compositions. CPU replay accepts all 24 reference proofs. Evaluation references remain outside the GPU package. Two textual overlap flags affect diagnostic validation; none affect the revised held-out statements at the registered threshold. This does not establish semantic independence.

The CPU recount reads raw continuation IDs and Lean diagnostics. `resource_summary.json` adds physical calls and trainer gradients. Both artifacts live under `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/completion_rl_v1/`. The following counts come from those artifacts.

| Phase | Accepted / rollouts | Theorems | Generated continuation tokens | Physical Lean calls |
| --- | ---: | ---: | ---: | ---: |
| Frozen existing prompt | 0 / 64 | 16 | 7,336 | 63 |
| Frozen prefilled code block | 0 / 64 | 16 | 7,716 | 64 |
| Frozen missing expression | 2 / 64 | 16 | 3,034 | 64 |
| Frozen missing tactic | 0 / 64 | 16 | 7,539 | 64 |
| Random arm, training | 10 / 12 | 1 | 449 | 12 |
| Prerequisite arm, training | 10 / 12 | 1 | 393 | 12 |
| Unassisted held-out baseline | 0 / 16 | 4 | 2,960 | 16 |
| Unassisted held-out, random final policy | 0 / 16 | 4 | 3,091 | 16 |
| Unassisted held-out, prerequisite final policy | 0 / 16 | 4 | 2,838 | 16 |

One training theorem qualifies: `PrereqRL.train_twice_divisible`. Its supplied proof ends with `simpa [twice] using `. Accepted frozen completions supply `ih` and close the code block. This establishes assisted proof completion, not discovery of that proof strategy.

Both arms execute three optimizer steps. Each arm samples two mixed-reward training groups. Trainer logs record nonzero gradient norms for those groups. The all-success group has zero reward variance and zero gradient norm. Near-zero scalar loss does not establish absent gradients. Training acceptances combine samples across changing policies; they are not a matched final-policy accuracy estimate.

The ordering hypothesis remains untested because both arms have the same single-task order. Every unassisted curriculum-validation round accepts zero of its 16 attempts per arm. Assistance therefore never decreases. Final held-out acceptance remains at the baseline. This pilot does not establish independent learning, a curriculum gain, or a need for SFT.

48,913 generated tokens are counted across the complete worker. The worker records 440 rollout attempts. Its 465 physical Lean calls include initial-context checks and checker controls. Worker and training wall times remain in `audit_retry1/summary.json`. Active GPU time was not instrumented; wall time must not be reported as GPU compute time.

The GPU worker returns exit 0. The corrected CPU recount returns exit 0 without changing acceptance or resource gates. Its initial path-filter failure remains in `audit/` and `recount.log`. The immutable preparation manifest contains inherited gold-free wording in `prompt_variant`; `resource_summary.json` records the correction. The exported training prefixes and `reference_policy` already identify the assistance. Future preparation uses corrected metadata. A periodic S3 upload fails while the live status changes; final synchronized records pass the CPU recount.

Spot instance `<ec2-instance-id>` reports terminated. S3 `capstate/acq/formal_rl/completion_rl_20261004/` stores shipped code, configuration, checkpoints, runtime records, audit, termination observation, and the cumulative reservation ledger. Reservations are conservative bounds, not billed spend. The lease stays within the authorized total ceiling. [TensorBoard](http://localhost:6007/#scalars) serves the recorded curves through the bounded CPU server and SSH tunnel.

The next experiment returns to mathematical prerequisite learning. Prepare component lemmas that require interpreting divisibility, constructing witnesses, combining divisibility facts, and using induction. Extend toward gcd, coprimality, primes, and congruences after the prerequisites. [Mathematics in Lean](https://leanprover-community.github.io/mathematics_in_lean/C05_Elementary_Number_Theory.html) supplies candidate number-theory material; replay candidates under the pinned environment before use.

Require a mathematical choice in each task. Supply definitions and registered lemma statements when needed, without supplying the target proof prefix. The model generates the proof body. If a preliminary task asks for a witness or proof term within a supplied wrapper, record that assistance explicitly. Such a task is a component exercise, not independent generation of the complete proof. Completion prefixes remain a diagnostic for the interface.

Freeze genuine component dependencies and unseen compositions before generation. The proposed comparison uses random versus prerequisite order with identical mathematical tasks, assistance, optimizer, and resource limits. Use a frozen probe to establish usable rewards across distinct tasks. If component tasks still produce no reward variation, report the RL cold-start failure rather than interpreting optimizer steps as mathematical learning. The final criterion is improved verification of unseen compositions without target proof prefixes, measured against the unchanged policy and interaction costs. This mathematical curriculum is proposed; no new data or training follows from this documentation correction.
