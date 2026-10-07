# Team workstreams

Each workstream owns its paths and its acceptance checks. Agree on the trajectory and run-record schemas before parallel implementation. Merge through the interfaces in [the research plan](research_plan.md).

## A. Representation and training

Own `src/formal_rl/models/`, `data/`, `training/`, and `configs/model/`, `data/`, `pretrain/`, and `midtrain/`. Select the public or custom model, construct one shared checkpoint trajectory, prepare licensed formal traces, and implement supervised fine-tuning and resume. Deliver checkpoint manifests with tokenizer, data, token count, and configuration. A readiness probe must load each checkpoint through one shared interface.

## B. Lean and reward

Own `src/formal_rl/lean/`, `rewards/`, and `configs/reward/`. Pin Lean and mathlib, implement reset and step, cache by theorem context, tactic sequence, and Lean revision, and record verifier statuses. Deliver terminal reward and prefix diagnostics with verified versus heuristic provenance. Tiny Lean examples must pass in the marked integration group before RL uses the interface.

## C. RL, curriculum, and evaluation

Own `src/formal_rl/rl/`, `curricula/`, `eval/`, and their config directories. Implement the first small policy update, uniform and later adaptive sampling, the matched expert-iteration baseline, and cost accounting. Deliver a held-out solve-rate curve against Lean calls with raw event rows. The trainer must accept samplers and rewards through the agreed interfaces.

Coordinate changes to `README.md`, `docs/experimental_protocol.md`, `docs/metrics.md`, and `docs/decisions.md` through one reviewer. The README status table has one writer per merge. Put artifacts in run-specific paths to avoid concurrent writes.

## Midterm handoff, 2026-09-29

This section records the team's discussion before experiments. The course deadline was about three weeks away at this discussion. The working target is a model near 500M parameters and Lean 4 proofs about natural numbers and elementary number theory. The dated data decision below supplies the first concrete manifests.

### Manas: data, curriculum, RL, and evaluation

Manas selects the training corpus and public benchmark, then gives their exact revisions and split manifests to the team. He owns theorem selection, learnability measures, adaptive sampling, RL efficiency, and the final comparison. The proposed data source is the [LeanDojo Benchmark 4](https://zenodo.org/records/12740403) Mathlib corpus. Its Lean 4 traces support tactic and proof-state training. A number-theory subset would be a team-defined subset, not the full public benchmark score.

Manas keeps training, curriculum validation, and held-out evaluation separate. He checks statement duplicates, near duplicates, and proof overlap before training. The curriculum uses training and curriculum-validation records only. He compares uniform sampling with adaptive sampling from the same checkpoint, reward, and interaction budget. Acceptance is a frozen theorem manifest, a no-RL baseline, and held-out solve-rate curves with raw token, Lean-call, and GPU-time records.

### Anwesha: Lean integration and reward

Anwesha owns the Lean 4 interaction, pinned Lean and mathlib revisions, verifier statuses, and tactic-prefix diagnostics. Her RL feedback uses Lean verification without a learned process reward model (PRM). A completed proof accepted by Lean supplies the terminal reward. An accepted tactic prefix records valid execution up to its reached state. Subgoal reduction is a heuristic and cannot count as proof.

Anwesha gives the team a reset-and-step interface plus a small verified example before RL uses it. She records failed tactics, timeouts, cache hits, and physical Lean executions separately. Her reward records identify each component as Lean-verified or heuristic. This work follows the [experimental protocol](experimental_protocol.md).

### Abhinav: checkpoint trajectory and RL readiness

Abhinav owns architecture choice, continued pretraining or midtraining, supervised fine-tuning, and bounded architecture sweeps. [Qwen2.5-0.5B base](https://huggingface.co/Qwen/Qwen2.5-0.5B) is a candidate that fits the discussed model scale. It has not been selected or evaluated in this repository. He records the tokenizer, data, token exposure, and configuration for each checkpoint.

Abhinav also owns the RL readiness probe. He runs the same short RL procedure from selected checkpoints on one trajectory, using Manas's fixed training and evaluation manifests and Anwesha's Lean interface. He reports each checkpoint's no-RL solve rate and its change after RL. This gives him an RL component without a separate trainer or benchmark. Acceptance is a checkpoint manifest and matched probe records, including null results.

### Public benchmark choices and success criterion

- [LeanDojo Benchmark 4](https://zenodo.org/records/12740403) is the current primary recommendation. It provides public Lean 4 Mathlib data. Freeze an existing test split before training. A filtered elementary `Nat` or number-theory score needs its own declared manifest and denominator.
- [Natural Number Game](https://github.com/leanprover-community/NNG4) is an introductory Lean 4 source for verifier checks and early probes. Its public level files include solutions, and its `MyNat` environment differs from standard Mathlib arithmetic. Do not treat game results as the main held-out claim without an exposure and environment audit.
- The [Lean 4 miniF2F port](https://github.com/google-deepmind/miniF2F) is a stretch test for formalized competition problems. [ProofNet-Verified](https://github.com/marcusm117/ProofNet-Verified) is another public option, but its undergraduate topics are broader than this number-theory focus.

The primary outcome is change in Lean-verified solve rate on a frozen held-out set after RL. Evaluate the starting and RL checkpoints with the same prompts, decoding, and proof-attempt budget. Report the no-RL rate beside the RL result. Compare RL with verifier-filtered expert iteration under matched generated-token and physical Lean-call budgets. Compare adaptive and uniform sampling with the same RL method. Use held-out solve rate against cumulative Lean calls to show efficiency, as defined in [metrics](metrics.md). Use the curriculum-validation baseline to assess feasibility and set the numerical success criterion. Keep the test manifest fixed during those decisions.

### Next decisions

1. Select and pin the model, Lean and mathlib pair, dataset revision, and benchmark split. Record the decision in [decisions](decisions.md).
2. Inspect the public corpus before filtering. Freeze train, curriculum-validation, and held-out manifests. Record overlap checks.
3. Run the smallest no-RL probe that tests whether the chosen benchmark can measure learning at this model scale.
4. Share one trajectory and run-record schema. Then build the verifier and training interfaces against it.

The [README status table](../README.md#baselines-and-status) remains the source of truth for implementation and results. No benchmark score, training run, RL gain, or numerical target is established by this handoff.

## Data interface, 2026-10-01

Use the frozen manifests and counts in [README data preparation](results/01_data_preparation.md#data-preparation). The [dated decision](decisions.md#2026-10-01-frozen-nat-subset-for-a-local-pipeline-probe) records the source and limitations. This subset supports a pipeline probe; its small test set does not settle the main benchmark.

- Manas owns the filter, overlap audit, immutable manifests, and validation baseline. He keeps the test manifest fixed during curriculum and RL development.
- Abhinav loads `artifacts/leandojo_nat_v1/frozen/train.jsonl`. He uses records with nonempty `traced_tactics` for supervised training. He excludes the raw archive, premise corpus, validation exports, and test exports from training data.
- Anwesha uses theorem locators and the pinned Lean/mathlib pair to reconstruct states. A declaration header is not standalone Lean input. The first trace state can be an intermediate subgoal.
- Curriculum statistics use training and curriculum validation. Test exports contain no proof traces. The held-out set does not drive sampling, architecture selection, reward tuning, or stopping.

## RL-only study ownership, 2026-10-01

This section records the latest scope and supersedes earlier ownership assignments for the new RL-only study. The earlier checkpoint and supervised-training studies remain separate. The user requests experiment planning without compute and asks us to assume both teammate components are unavailable.

### Manas: curriculum and experiment design

Manas owns theorem selection, prerequisite relationships, split audits, curriculum sampling, reward comparisons, and evaluation. He plans the fixed-checkpoint comparison in [the research plan](research_plan.md). He specifies resource accounting and success criteria before training. He does not implement the missing Lean environment or policy optimizer as part of this planning task.

The main comparison uses a fixed Instruct checkpoint and GRPO. It crosses uniform versus curriculum sampling with terminal versus terminal-plus-tactic rewards. Premise unlocking and proof decomposition are later isolated experiments. No additional SFT or continued pretraining enters these main arms.

### Anwesha: required Lean handoff

Anwesha supplies reconstructed initial contexts and a pinned Lean/mathlib pair. Her interface must reset a theorem, execute a tactic, and return the resulting state and status. It must distinguish accepted tactics, errors, timeouts, and completed proofs. Final acceptance must check the original theorem without admissions or access to its existing proof.

The handoff records allowed premises, automation, cache behavior, and physical Lean calls. It identifies verified diagnostics separately from heuristic progress. Manas specifies the experiment's credit assignment using these diagnostics. Anwesha retains ownership of verifier implementation and integration.

### Abhinav: required policy optimizer handoff

Abhinav supplies checkpoint and tokenizer loading, complete interactive rollouts, and GRPO updates. His interface must accept theorem sampling and reward records without changing their meaning. It must mask environment observations from policy loss and group episodes from the same initial theorem context.

The handoff records optimizer settings, trainable parameters, decoding, seeds, generated tokens, and measured GPU time. It includes resume behavior and handling of groups without reward variation. Backend selection and optimizer implementation remain Abhinav's work. PLPO requires a separate later handoff before that comparison becomes runnable.

### Dependency order

First, Manas finishes the study registration and both teammates specify their interfaces. After Lean and checkpoint loading arrive, Manas can measure an interactive unchanged-policy baseline under a separately approved budget. RL experiments also require Abhinav's optimizer and complete resource accounting.

L40S is the future hardware target for Manas's portion. No host, spending cap, allocation, or launch is established here. The [README status table](../README.md#baselines-and-status) remains the sole implementation status record.

## Curriculum and RLP handoff, 2026-10-05

This handoff is for Manas, his collaborators, and the next agent continuing his work. It captures the state after the primitive RLP failure analysis. Use the [README status table](../README.md#baselines-and-status) for subsequent updates. Earlier dated planning sections remain historical.

### Goal and ownership

Teach the small model undergraduate number theory and its prerequisites, then train and evaluate formal proof generation. Mathematical learning must appear in independent applications and unseen compositions. Better formatting or higher prediction reward alone does not satisfy this goal.

Manas owns the data, prerequisite curriculum, learnability measurements, and experiment comparisons. Learning speed means questions, rollouts, and generated training tokens needed to reach a registered outcome. Count evaluation costs separately. Measure Lean calls, GPU time, and wall time when applicable.

Anwesha retains ownership of interactive Lean integration and verifier feedback. Abhinav retains ownership of architecture and policy-optimization work. Manas's separate pilots reuse TRL and the existing checker; they do not establish that either teammate's interactive interface is ready.

The original fixed-checkpoint RL-only comparison remains distinct from the later acquisition study. The user subsequently authorized knowledge acquisition and RLP midtraining. The latest primitive run contains no supervised loss. A separate historical GSM8K supervised diagnostic appears in the README; do not merge its results with RLP.

### Conclusion to carry forward

The latest training run completed and changed model parameters. An early checkpoint improved on curriculum validation. The final checkpoint performed worse on the held-out tasks. Undergraduate competence, improved proof quality, and an advantage from curriculum ordering remain unestablished.

Do not continue training the final checkpoint unchanged. The saved early checkpoint is a candidate for evaluation, not a validated replacement model.

### What earlier experiments established

These are summaries of recorded outcomes, not fresh reruns. Follow the linked sections for their original evidence and limits.

| Experiment | Recorded outcome | Interpretation |
| --- | --- | --- |
| [Instruct proof diagnostic](results/02_qwen_capability_probe.md#instruct-proof-diagnostic) | No formal proof accepted in the sampled diagnostic; informal responses included mathematical errors | These prompts and budgets exposed failures; they did not establish a capability ceiling |
| [Whole-proof GRPO](results/03_grpo_proof_pilots.md#separate-whole-proof-grpo-pilot) | No accepted training or validation proof and no reward variation within sampled groups | Terminal reward did not supply a usable contrast in this pilot |
| [Prerequisite and syntax assistance](results/03_grpo_proof_pilots.md#prerequisite-curriculum-diagnostic-2026-10-04) | Readiness produced no accepted proofs; conditional training arms were skipped | Definitions and generic syntax assistance did not bootstrap this setup |
| [Proof completion](results/03_grpo_proof_pilots.md#assisted-proof-completion-2026-10-04) | Assisted successes and nonzero gradients; no unassisted held-out success; eligible pool collapsed to a single task | Completion was learnable with assistance; the experiment could not identify an ordering effect |
| [Earlier RLP continuation](results/04_rlp_acquisition.md#saved-rlp-checkpoint) | No measured arithmetic accuracy gain | A saved checkpoint did not establish acquisition |
| [Precision correction](results/04_rlp_acquisition.md#precision-correction-for-rlp-midtraining) | FP32 updates worked; no measured held-out accuracy gain | Correcting numerical update loss did not resolve acquisition |
| [Revised primitive curriculum](results/04_rlp_acquisition.md#revised-primitive-curriculum) | Early validation improvement, followed by final deterioration | Investigate reward alignment and checkpoint selection before further training |

### Exact latest setup

The run is `primitive_rlp_20261005`. Its [config](../configs/midtrain/qwen_05b_primitive_rlp.json), [preparer](../scripts/data/prepare_rlp_corpus.py), and [runner](../scripts/train/run_rlp_pilot.py) define the experiment. Archived shipped code is authoritative for the completed run.

| Setting | Value |
| --- | --- |
| Initial checkpoint | `Qwen/Qwen2.5-0.5B-Instruct` |
| Model revision | `7ae557604adf67be50417f59c2c2f167def9a775` |
| Starting state | Original pinned Instruct policy, not the earlier acquisition checkpoint |
| Hardware configuration | On-demand L40S, AWS `g6e.2xlarge`, `eu-north-1` |
| Optimizer implementation | TRL `0.24.0`, GRPO |
| Precision | FP32 parameters and optimizer moments; BF16 policy computation |
| Training seed | `20261005` |
| Sampled responses per group | 8 |
| Response limit | 512 generated tokens |
| Learning rate | `1e-6` |
| Teacher | Exponential moving average of the policy, decay `0.999` |
| Objective | Target-span prediction reward; loss on generated reasoning tokens |
| Unique training contexts | 448, recorded in `run.json:unique_training_contexts` |
| Training presentations and updates | 596, recorded in `run.json:optimizer_steps` |

The training examples are authored tasks with instructional definitions. Judson's AATA supplies the pinned source context. The curriculum covers witnesses, substitution, counterexamples, quotient, remainder, gcd, Bézout coefficients, inverses, prime conditions, prime factors, congruences, and computational primitives for induction, contradiction, and cancellation.

These are integer-answer tasks. The induction and contradiction labels do not certify complete proofs. Shared primitive templates cross splits deliberately. Held-out items contain new inputs and reserved witness compositions. The split does not establish semantic independence. Pretraining exposure remains unknown.

Use `primitive_midtraining_v1/prepared_ordered/` on cpu-angad. Its manifest records the unique examples, repeated presentations, validation membership, and held-out membership. Preserve `prepared/` as the rejected draft: its within-stage shuffle violated dependency order.

### Recounted outcomes

The [CPU audit](../scripts/audit/audit_primitive_rlp.py) reproduces these counts from the saved JSONL records. Its latest handoff-time execution returned exit code 0. `diagnosis.json` stores the outputs; paths appear below.

| Unassisted held-out metric | Starting model | Final model | Source field |
| --- | ---: | ---: | --- |
| Correct boxed integer | 42/108 | 21/108 | `evaluations.*_primitives.accepted` |
| No parseable integer | 17/108 | 69/108 | `evaluations.*_primitives.unparsed` |
| Reached generation limit | 15/108 | 36/108 | `evaluations.*_primitives.capped` |
| Contains a code fence | 0/108 | 27/108 | `evaluations.*_primitives.code` |

| Curriculum-validation checkpoint | Correct answers | Source record |
| --- | ---: | --- |
| Starting model | 16/56 | `validation_initial_primitives.jsonl` |
| First milestone | 25/56 | `validation_tokens_200000_primitives.jsonl` |
| Final model | 13/56 | `validation_final_primitives.jsonl` |

The milestone filename denotes the scheduled token threshold, not the exact realized token count. The corresponding saved checkpoint is `checkpoint-118`. It has not received the matched held-out evaluation needed to establish transfer. These observations come from one training seed and do not identify a curriculum effect.

1,626,779 generated training tokens are counted in `diagnosis.json:training.tokens`. 4,768 training responses are counted in `training.counts.rollouts`. Evaluation tokens are excluded from these training counts. This run uses no Lean reward and provides no accepted-proof measurement.

28 previously correct held-out answers fail after training, according to `paired.lost`. Seven previously failed answers become correct, according to `paired.gained`. The losses include wrong integers and responses without parseable integers. The latter category mixes correct unboxed answers, wrong decimals, unexecuted code, and unfinished reasoning. Preserve the registered score; semantic review must remain a separate diagnostic.

### Failure analysis and uncertainty

The scorer appends `Final answer: \boxed{` after the generated reasoning, then scores the known target span. The training prompt does not require the model to produce that prefix or a correct final answer. Unassisted evaluation requires both. The [recorded analysis](decisions.md#2026-10-05-examine-primitive-rlp-deterioration-on-cpu) documents this mismatch.

17 incorrect boxed training responses receive positive relative advantage, counted in `training.counts.wrong_boxed_positive_advantage`. Positive advantage encourages a sampled response relative to its group. Correct responses usually rank higher in the subset containing both correct and wrong boxed answers. The evidence therefore does not support a claim that the reward consistently prefers wrong mathematics.

The assisted prediction score improves while independent answer accuracy declines. This separates the training proxy from the required outcome. It does not prove that prompt mismatch alone caused deterioration. Reward choice, optimization, assistance, and training duration were not isolated in controlled ablations.

The audit confirms the registered training order. `first_update.json` confirms changed sampled parameters and FP32 optimizer moments. The earlier precision failure therefore does not explain an absence of updates in this run. Group centering cancels the shared teacher baseline; that baseline is not a penalty preserving the initial policy.

Validation callbacks replace some regular optimizer log entries. The audit explicitly records missing loss and gradient values in `missing_optimizer_log_steps`. It checks step coverage against rollouts and callback events. It does not fill in missing measurements.

### Artifacts and resumption

The repository root is `/Users/manasvenkatasairavulapalli/Desktop/Computer Science stuff/Pure CS/RL`.

CPU project root:

```text
/mnt/data/manasvenkatasairavulapalli/formal_rl/
```

Durable run root, in the S3 bucket's `us-east-1` region:

```text
s3://<project-bucket>/capstate/acq/formal_rl/primitive_rlp_20261005/
```

| Artifact | Path relative to its root |
| --- | --- |
| CPU prepared data | `primitive_midtraining_v1/prepared_ordered/` |
| CPU recount and original evaluations | `primitive_midtraining_v1/audit_final/` |
| CPU diagnosis and archived records | `primitive_midtraining_v1/audit_final/diagnosis/` |
| S3 early policy | `runtime/out/policy/checkpoint-118/` |
| S3 final policy | `runtime/out/policy_final/` |
| S3 run configuration and counters | `runtime/out/run.json` |
| S3 sampled training responses | `runtime/out/rollouts.jsonl` |
| S3 training and validation logs | `runtime/out/trainer.jsonl` |
| S3 audit, code, and failure examples | `audit/diagnosis/` |

Read checkpoint manifests before loading. Saved checkpoint files do not certify model quality or complete resume state. Reconcile archived configuration, tokenizer, optimizer state, and teacher state for the selected operation. Avoid mistaking the older README continuation checkpoint for this run's checkpoint.

To reproduce the existing diagnosis on cpu-angad, run:

```sh
cd /mnt/data/manasvenkatasairavulapalli/formal_rl
ulimit -v 2097152
python3 primitive_midtraining_v1/audit_final/diagnosis/audit_primitive_rlp.py \
  --root primitive_midtraining_v1/audit_final/diagnosis \
  --train primitive_midtraining_v1/prepared_ordered/train.jsonl
```

This command reads the archived run and rewrites derived diagnosis files. Keep one writer on that output directory. It performs no model inference. The audit streams training records and uses the explicit memory cap shown above.

The cumulative authorized ceiling is $64, including earlier runs. The latest lease had a six-hour limit. Neither figure is a remaining balance or a bill. Reconcile the existing budget ledger and actual resource state before any paid work. The previous project query found no active Stockholm rental; this handoff does not refresh cloud state or authorize a new launch.

### Proposed next work, in order

1. Compare the original policy with `checkpoint-118` on curriculum validation. Match decoding and attempt budgets across direct-answer and supplied-prefix conditions. Report correctness and formatting separately.
2. Use training and validation records to examine reward alignment. Keep any exact-correctness GRPO condition separate from the RLP objective. Preserve the failed run and its scoring rule.
3. Once the objective produces usable learning, compare random and prerequisite order. Hold tasks, assistance, optimizer, checkpoint, and resource limits fixed. Treat premise unlocking as a separate intervention.
4. Register checkpoint selection and stopping before another training run. Reserve held-out evaluation for the declared comparison. The already-inspected test results must not drive task or reward tuning.
5. After independent mathematical gains appear, evaluate transfer into Lean proof RL. Measure acceptance of the original theorem under pinned Lean and mathlib. Keep this distinct from arithmetic answer accuracy.

These steps are proposals from the failure analysis. They are not implemented experiments or promises of improvement. Public undergraduate benchmark performance remains unmeasured in this acquisition run. Do not present its authored diagnostics as ProofNet, miniF2F, or a college-level proficiency score.

## Midterm reference: RL correctness and acquisition, 2026-10-06

This reference connects the experiments, grading corrections, and next research question for the midterm presentation. It is a dated scientific account. The [README status table](../README.md#baselines-and-status) remains the current execution record.

### Question and capability decomposition

The project asks whether reinforcement learning improves mathematical knowledge and proof construction in a small model under a fixed budget. The eventual target is approximately 500 million parameters, with reliable proofs and undergraduate problem solving. That target remains unestablished. The acquisition study starts from early RLP checkpoint-118, not a fresh Instruct policy.

| Component | Controlled task | What the task measures | Main inference limit |
| --- | --- | --- | --- |
| Knowledge access | Recognition, recall, supplied use, and independent use | Whether a fact is accessible and correctly applied under the declared support | Prompt cues can reveal a fact or method |
| Proof semantics | Execute a local operation with mathematics and context supplied | Validity of the submitted transformation | A correct witness can accompany an incorrect explanation |
| Proof strategy | Choose subgoals and dependencies with facts available | Construction of a useful proof decomposition | A supplied lemma can reveal the strategy |
| Integrated solving | Complete a fresh problem without task-specific support | End-to-end mathematical correctness | Libraries, search, or external repairs can supply missing capability |

For divisibility, recalling the existential definition and composing supplied witnesses are different tasks. A product witness tests execution. Retrieving the definition and constructing the argument without assistance tests independent use. These probes are conditional task scores, not direct measurements of isolated internal faculties. See the [inventory](results/05_acquisition_evaluations.md#knowledge-inventory-and-local-operations) and [decomposition evaluation](results/05_acquisition_evaluations.md#acquisition-proof-semantics-and-proof-strategy).

The curriculum varies information availability, rather than labeling every task as an easy or hard proof. The intended sequence is supplied execution, reduced support, independent use, delayed retention, and transfer. Each transition needs correctness evidence. Success with a definition in the prompt does not establish that the model stores and retrieves it independently.

### Experimental evidence and lessons

These are separate experiments with different tasks and objectives. Their historical results are linked below; they are not matched arms of one comparison.

| Experiment | Recorded observation | Lesson for the next experiment |
| --- | --- | --- |
| [Whole-proof GRPO](results/03_grpo_proof_pilots.md#separate-whole-proof-grpo-pilot) | No accepted sampled proofs and no within-group reward variation | Check whether the initial policy produces informative contrasts before optimizing terminal reward |
| [Assisted proof completion](results/03_grpo_proof_pilots.md#assisted-proof-completion-2026-10-04) | Assisted success did not yield unassisted held-out success; the eligible pool collapsed | Assistance and selection can make a narrow task learnable without establishing transfer |
| [Precision correction](results/04_rlp_acquisition.md#precision-correction-for-rlp-midtraining) | Corrected updates worked without a measured held-out accuracy gain | Changed weights and functioning optimizer moments are operational evidence, not acquisition evidence |
| [Primitive RLP](results/04_rlp_acquisition.md#revised-primitive-curriculum) | Early validation improvement preceded final deterioration | Inspect intermediate checkpoints and preserve negative final results |
| [Corpus audit](results/07_rlp_corpus_audit.md#structured-rlp-corpus-audit) | Numeric targets passed; support and distribution limits remained | Correct targets do not certify generated reasoning or identify the cause of deterioration |
| [Local-action pilot](results/06_local_action_pilot.md#local-action-correctness-pilot) | Verified witnesses improved on supplied tasks; explanations and composition remained weak | The verifier certifies the submitted object within its declared scope |
| [Controlled ordered RLP](results/08_controlled_rlp.md#ordered-arm-results) | Partial core scores changed; no phase produced a fully correct answer | Controlled data and ordering do not establish reward alignment with mathematical correctness |

The local RLP objective scores how generated reasoning changes the likelihood of a private mathematical target span. The [official RLP description](https://github.com/NVlabs/RLP) motivates information gain on observed text. This small Instruct-model adaptation is not a replication of its published pretraining experiments.

A correct target is distinct from a correct generated derivation. Invalid reasoning can still increase target likelihood. A valid alternative proof can also differ from the reference wording. Preserve target likelihood, semantic correctness, and verifier acceptance as separate measurements. No completed experiment isolates the mechanism that caused deterioration.

### Ordered-arm case study

The ordered arm continued from early RLP checkpoint-118 with the existing prediction reward. It completed 432 optimizer steps. It generated 2,794,874 training tokens, counted in the ordered `run.json`. The same 18 independent-use acquisition tasks recur across eight concept families. Recognition, recall, and supplied-use scores are not part of this saved-response review.

| Phase | Fully correct / evaluated | Mean core score, scale 0–3 | At response cap / evaluated |
| --- | ---: | ---: | ---: |
| Initial | 0/18 | 0.89 | 0/18 |
| 500,000-token milestone | 0/18 | 0.89 | 2/18 |
| 1,000,000-token milestone | 0/18 | 1.33 | 6/18 |
| 2,000,000-token milestone | 0/18 | 1.17 | 17/18 |
| Final | 0/18 | 0.72 | 7/18 |

The core score is ordinal partial credit. Its mean is a descriptive secondary result, not a probability of correctness. Full correctness remains the primary endpoint. Unknown and capped responses remain in its denominator. Milestone names denote scheduled thresholds, not exact realized training-token counts.

Four paired items improve in core score between initial and final. Six worsen. Eight remain unchanged. No item becomes fully correct. Congruence supplies a narrow retained signal: its mean core score rises from 0 to 3 across the same two items. Both final answers still contain invalid extra reasoning and fail full correctness.

The response limit is 1,024 generated tokens. The two-million-token phase reaches that limit on 17 of 18 answers. Do not exclude those responses or assume that additional tokens would make them correct. This censoring limits the partial-credit interpretation.

This is one training seed and a small authored validation set. The shuffled arm remains unscored in this review, so no ordering advantage is established. No held-out responses were accessed. Delayed retention and undergraduate benchmark competence remain unmeasured.

The CPU preparation baseline and the ordered runtime initial responses are separate artifacts. This case study uses the runtime initial responses as its paired baseline. Do not pool the two baselines or attribute their difference to training without investigating inference and review conditions.

### Grading reliability and corrections

The first second-review file failed evidence alignment and was excluded. Its raw output and invalid interim recounts remain preserved. Reviewer A's verified copy changes evidence excerpts only. A fresh second review covers the entire packet in disjoint blinded batches.

Blinded adjudication resolves 38 cases with disputed core score or full correctness. Exact-ID, excerpt, schema, and consistency checks pass. These checks establish record alignment; they do not establish the truth of every mathematical judgment. Short known-answer controls do not validate long responses merely because the controls pass.

Reviewer A's exact model identity is unavailable. The replacement reviewer and adjudicator report GPT-6 at runtime. Independence is by review context, not demonstrated independence of model errors. This is AI-assisted review, without human validation or Lean certification. Accept equivalent valid methods, but do not repair a missing central argument. A correct conclusion reached through invalid reasoning fails full correctness.

CPU source artifacts are under `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/controlled_ordered_review_20261006/`. The authoritative review is `ordered_results_v2e/`, with `report.json`, `integrity.json`, and `score_ordered_arm_v2.py`. A fresh numerical recount for this reference is `midterm_reference_recount_20261006/`. The rejected review and earlier invalid summaries must not be used in slides.

### Why the next proposal uses Lean

Lean tactics construct proof terms that the kernel checks against formal propositions. Acceptance concerns the encoded target and its assumptions. It does not certify arbitrary prose or the fidelity of an automatic translation. See the [official Lean reference](https://lean-lang.org/doc/reference/latest/Tactic-Proofs/).

The project already has a [local witness checker](../scripts/eval/verify_local_operator.py) and earlier whole-proof checks. The witness checker inserts a submitted expression into an immutable obligation with a fixed proof skeleton. It certifies the witness, not a model-generated derivation. Anwesha's general interactive verifier remains a separate workstream.

The recommended next step is the [Lean-verified acquisition evaluation proposal](research_plan.md#lean-verified-acquisition-evaluation-proposal-2026-10-06). Formalize the diagnostic targets faithfully and test existing checkpoints before another reward change. Previously inspected items remain development data. Final transfer requires fresh sealed tasks.

Lean-based evaluation can score unchanged RLP outputs through a declared formal-output contract. Rewarding Lean acceptance creates a distinct correctness-reward arm. A hybrid prediction-plus-verification reward is another objective. Do not change the data schedule, reward, and parent checkpoint together and attribute a gain to one component.

### Operational rules for interpretable RL

1. Define the submitted object and success predicate before rollouts. Verification must preserve the original task and assumptions.
2. Inspect within-group reward variation. Preserve failed tasks, discarded groups, and zero-variance groups in the records.
3. Match concept exposure, support views, replay, weights, and numerical settings when testing order. Hold the objective fixed.
4. Use frozen curriculum validation for selection. Keep final proofs outside training, retrieval, reward design, and adaptive sampling.
5. Separate local validity, full correctness, partial progress, extraction failures, token caps, and infrastructure failures.
6. Count generated tokens, verifier calls, wall time, and measured GPU time from run records. A reservation ledger is not a bill.
7. Report standalone generation and bounded Lean-assisted search separately. Disclose automation, available lemmas, candidates, and external repairs.

These rules address observed failures and inference limits. They do not guarantee improvement. Verifier correctness still depends on faithful formal targets and output contracts.

### Presentation sequence and permissible claims

| Slide | Question | Evidence or visual |
| --- | --- | --- |
| Model target | What does reliable solving require? | Standalone and Lean-assisted endpoint definitions |
| Decomposition | What can a failed proof conceal? | Recognition/recall/supplied-use/independent-use grid and a divisibility example |
| Reward | What does the optimizer reward? | Private-target likelihood versus acceptance of the submitted object |
| Controls | What makes curriculum comparisons interpretable? | Same theorem-view multiset, replay, checkpoint, objective, and budgets |
| Results | Did ordered RLP improve? | Phase table with full correctness, partial credit, and token caps |
| Measurement | What happened during grading? | Rejected review, corrected excerpts, fresh review, and adjudication provenance |
| Lean boundary | What can the kernel certify? | Exact formal target → submitted proof → kernel acceptance; prose graded separately |
| Next study | Which uncertainty comes next? | Matched Lean checkpoint baseline before correctness-reward training |

The presentation can claim functioning updates, narrow verified-witness learning, controlled data views, and a corrected negative acquisition result. It cannot claim reliable undergraduate performance, an ordering advantage, or a proven degradation mechanism. Lean evaluation is a proposed next experiment, not a measured remedy.
