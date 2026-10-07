# Research plan

## Goal and scope

This is a hypothesis-testing project. Manas studies curriculum, proof decomposition, exploration, and reward attribution for a fixed small formal reasoner. The planned initial policy is `Qwen/Qwen2.5-0.5B-Instruct`. No additional supervised fine-tuning (SFT) or continued pretraining occurs in the main comparison. An L40S is the future hardware target for this workstream. No compute is authorized by this plan.

The main question is: can prerequisite-aware curriculum and Lean feedback improve formal proof learning with fewer questions, rollouts, and generated tokens? Start with number theory and its prerequisites. Measure broader undergraduate transfer separately through ProofNet-Verified. Neither the current diagnostic nor this plan establishes feasibility or a numerical gain target.

The separately authorized acquisition study now prioritizes learning mathematical primitives, retaining them, and composing them. Its protocol appears in [primitive-based midtraining](#primitive-based-midtraining-2026-10-05). This extends Manas's experimental scope without replacing the teammates' interactive interfaces. The README remains the execution-status owner.

Model a tactic interaction as a Markov decision process when the observation contains the Lean proof state needed for the next action. A policy emits a tactic or proof step. Lean executes it and returns an observation, status, diagnostics, and completion flag. Hidden context, timeout, and nondeterministic tool behavior may break the practical Markov assumption; record them rather than assuming them away.

## Order of work

The Lean environment and policy optimizer are assumed unavailable. This document specifies experiments and handoffs. It supplies neither implementation nor launch commands.

| Stage | Owner | Deliverable | Dependency |
| --- | --- | --- | --- |
| Register the study | Manas | Theorem families, split rules, curriculum definition, metrics, and comparisons | Can be planned now |
| Supply Lean interaction | Anwesha | Reconstructed initial states, reset/step, diagnostics, and completed-proof verification | Required before any interactive baseline |
| Supply policy optimization | Abhinav | Fixed-checkpoint loading, multi-turn rollouts, GRPO updates, token masks, and resume | Required before any RL arm |
| Measure readiness | Manas | Unchanged-policy results and training reward diagnostics using the supplied interfaces | Environment, loader, and approved compute budget |
| Run the main comparison | Manas | Matched learning curves and held-out evaluation | Readiness, frozen manifests, and implemented accounting |
| Run isolated extensions | Manas with interface owners | Premise, decomposition, or optimizer comparisons | Main comparison completed within the available budget |

### Fixed experimental conditions

- Initialize every RL arm from Instruct revision `7ae557604adf67be50417f59c2c2f167def9a775`, also used by the earlier diagnostic. Pin the tokenizer and its chat template.
- Supply the formal theorem statement and complete initial context. The model generates tactics; autoformalization is outside this study.
- Use the same interactive Lean observations, tactic boundaries, automation access, error handling, and episode limits in every arm.
- Use one immutable training pool for all arms. Keep curriculum validation and held-out evaluation separate.
- Hold GRPO settings, decoding, trainable parameters, and starting weights fixed. Abhinav supplies the implementation and records its exact version.
- Treat a rollout as a complete episode. Form each GRPO group from the same initial theorem and premise context. Mask environment observations from policy loss.
- Keep the premise library fixed in the main comparison. Prevent use of the target declaration, held-out solutions, new unchecked axioms, and admitted proofs.
- Register resource limits after the hardware and spending cap are known. No live host, rental, throughput, or available budget is assumed.

### Main comparison

The unchanged policy is the reference for all arms. Evaluate it through the same interactive environment and candidate budget.

| Arm | Update method | Reward | Theorem sampling |
| --- | --- | --- | --- |
| A | GRPO | Terminal acceptance | Uniform |
| B | GRPO | Terminal acceptance | Prerequisite-aware curriculum |
| C | GRPO with tactic credit | Terminal acceptance plus tactic feedback | Uniform |
| D | Same update as C | Same reward as C | Same curriculum as B |

Compare B with A and D with C for the curriculum effect. Compare C with A and D with B for the reward effect. Compare the combined effect with the separate effects. Use paired seeds and the same evaluation protocol. Register seed count and resource caps before launching; they remain unset here.

An interactive agent observes compiler feedback even in terminal-reward arms. This comparison tests reward attribution while holding feedback access fixed. A whole-proof versus interactive comparison is a later experiment because it changes the observation and action spaces.

### Curriculum definition

The main curriculum changes theorem selection, not prompts, supplied solution steps, or available premises. Define concept prerequisites from training material. Record each dependency's source and review it before use. Source-file order alone does not establish a mathematical dependency.

The proposed progression starts with logic, equality, integer or natural-number arithmetic, and induction. Later families cover divisibility, gcd, primes, and congruences. Choose the actual families after inspecting training coverage. Do not assume the existing Nat subset contains the complete undergraduate progression.

Order prerequisite families before their dependents. Estimate training success and reward variation from logged rollouts. Downweight mastered examples and prioritize examples with usable feedback. Preserve exploration of harder and previously deferred families. Freeze the selection rule and exploration share using curriculum validation before held-out evaluation.

Do not adopt the suggested success-probability band as an established optimum. Record how many examples remain selectable and how many rollout groups have no reward variation. If the curriculum has no usable band, report this failure and inspect prerequisites or process feedback on training data.

### Data and evaluation

The frozen LeanDojo Nat export is a pipeline-probe input, not the main undergraduate benchmark. Anwesha must reconstruct original theorem contexts before these declarations become prompts. Its earlier Lean/mathlib pair does not establish compatibility with ProofNet-Verified.

Manas defines a larger number-theory training pool and a theorem-family held-out set before training. Check exact statements, near duplicates, proof dependencies, and benchmark overlap. Evaluation declarations and their supplied proofs must remain inaccessible to training and premise retrieval.

Use held-out number-theory learning curves for the controlled efficiency comparison. Use ProofNet-Verified as an external undergraduate endpoint, with its revision, domain labels, and environment pinned before evaluation. Report the full benchmark and any declared subset separately. Analysis or algebra failures do not establish a number-theory ceiling.

Use curriculum validation for selection rules, intermediate checkpoint choice, and stopping. Evaluate held-out sets at the starting and predeclared final checkpoints. Register any additional held-out evaluation schedule in advance and forbid its use in adaptive decisions.

### Measurements and success

The primary efficiency curve is held-out verified solve rate against cumulative generated training tokens, over the shared budget range. Report curves against physical Lean calls and measured GPU time alongside it. The unchanged policy supplies the baseline.

Count unique training theorems, repeated theorem presentations, completed episodes, failed episodes, and generated tokens separately. Include curriculum estimation, discarded groups, and failed generation in training cost. Treat evaluation cost separately and report it in total project cost.

Report pass@1 and pass@k with a fixed candidate budget. For interactive proving, each candidate is a complete bounded episode. Count newly solved theorem IDs, domain performance, repeat states, invalid actions, and failure statuses. A longer accepted prefix is not a successful proof.

Evidence for the curriculum hypothesis requires improved held-out proof success at a shared cost, or reduced cost to a target frozen using curriculum validation. Define the numerical target after readiness and before the main comparison. Preserve flat and negative results. Previously unsolved under finite baseline sampling does not prove acquisition of previously absent knowledge.

### Later isolated experiments

1. Premise unlocking: hold reward and theorem sampling fixed. Compare a fixed library with staged access to previously verified training lemmas. Evaluate all policies with the same premise library. Inspect automation and indirect imports for access to locked lemmas; a prompt-only restriction cannot establish enforcement.
2. Proof decomposition: compare direct-target training with training on audited component lemmas followed by the same targets. Keep the target pool, allowed premises, and total interaction caps fixed. Record every added lemma and its training cost. Verify that the target follows from the proposed components.
3. Policy optimization: compare GRPO with PLPO only after Abhinav supplies both through the same rollout interface. Freeze curriculum, rewards, observations, and cost limits.
4. RLP: treat reinforcement pretraining as a separate objective study. Curriculum remains relevant, but text-prediction rewards do not verify proof correctness. Count preparation cost and evaluate the resulting checkpoint before further RL. This extension does not change the main RL-only comparison.

### Milestones

- First milestone: finish Manas's data and experiment registration. Obtain interface specifications from Anwesha and Abhinav. Compute remains pending.
- Second milestone: after both interfaces arrive, measure unchanged-policy readiness and a bounded optimizer smoke run under a separate approved budget.
- Third milestone: run the matched main arms that fit the remaining budget. Repeat comparisons before making claims about reliable gains.
- Second half of the semester: expand undergraduate evaluation and run isolated premise, decomposition, or optimizer studies. Prioritize the extension indicated by observed failures.

The earlier three-week deadline remains a planning constraint. Record the exact submission date before assigning calendar dates. Dependency delays reduce the experiment scope; they do not justify replacing another teammate's component without agreement.

### Source basis

[The process-verification paper](https://arxiv.org/html/2606.20068v1) motivates tactic credit assignment. Its experiments score generated whole proofs from existing larger prover checkpoints. They do not establish interactive learning for this Instruct checkpoint. [The GRPO documentation](https://verl.readthedocs.io/en/latest/algo/grpo.html) and [multi-turn documentation](https://verl.readthedocs.io/en/latest/sglang_multiturn/multiturn.html) identify existing implementation options for Abhinav. Backend selection remains his work.

[ProofNet-Verified](https://github.com/marcusm117/ProofNet-Verified) supplies formal undergraduate exercises across several domains. Benchmark availability does not establish that this policy can solve them. [RLP](https://arxiv.org/html/2510.01265v1) supplies the separate reinforcement-pretraining objective; its predictive reward must not be presented as Lean verification.

## Planned interfaces

| Interface | Input | Output | Boundary |
| --- | --- | --- | --- |
| `LeanEnvironment` | Theorem ID, tactic or proof step | State, status, diagnostics, completion | No model logic |
| `RewardProvider` | Trajectory and Lean diagnostics | Reward components, values, provenance, verified/heuristic label | No hidden shaping |
| `CurriculumSampler` | Theorem statistics and policy checkpoint | Distribution and selection reason | No held-out examples |
| `RLTrainer` | Rollouts, reward records, config | Update and resumable state | No sampler or reward-specific branches |
| `ExperimentTracker` | Config, revisions, events, costs | Local JSONL/CSV records and run manifest | No inferred counters |

These are planned boundaries, not callable APIs. Anwesha owns the Lean and reward interfaces. Abhinav supplies the policy optimizer. Manas owns curriculum, comparisons, and evaluation. Agree on the episode records before integration. Do not create placeholder implementations to imply readiness.

## Main inference risks

- Checkpoint readiness can reflect earlier training. Fix the initial Instruct checkpoint and restrict claims to additional RL adaptation from that policy.
- Process reward can reflect a heuristic instead of formal progress. Report verified feedback separately from shaping and ablate each component.
- Adaptive sampling can overfit curriculum validation. Freeze held-out theorem access until final evaluation.
- A method can improve solve rate by spending more verifier calls or tokens. Match budgets and show cost curves.

The first result that would challenge the framing is no usable feedback on training problems under the fixed interaction limits. A later flat curriculum or process-reward effect challenges the corresponding hypothesis. Preserve these results and their resource limits.
## Separately authorized whole-proof pilot, 2026-10-03

The user authorizes a separate GRPO pilot with a $10 cap for training and curriculum experiments. This extends Manas's execution scope. It does not implement the registered interactive comparison or replace teammate interfaces.

Reuse [TRL GRPO](https://huggingface.co/docs/trl/v0.24.0/grpo_trainer) with the existing completed-proof checker. Use full-parameter BF16 updates from the pinned Instruct checkpoint. Set `loss_type="grpo"`, `beta=0`, and no dataset shuffling. Record these choices explicitly; the zero KL coefficient removes the reference-policy term.

Compare a seeded random order with a fixed source-family order over identical training occurrences. Reload the starting checkpoint for each arm. The family order is a prerequisite heuristic. It does not establish causal dependencies or optimality. Keep premise access and whole-proof prompts fixed.

Reward accepted original proofs with one and rejected proofs with zero. Abort on verifier infrastructure failures. Use no SFT, CPT, format reward, or tactic reward. Record zero-variance groups, optimizer steps, generated tokens, physical Lean calls, and elapsed time. A zero-variance correctness group cannot establish an effective learning signal.

Freeze limits in [the pilot config](../configs/rl/qwen_05b_whole_proof_grpo.json). Prepare standalone training headers on cpu-angad without gold traces. Use the frozen corpus overlap audit and retain compilation exclusions. Evaluate unchanged and final policies with matched diagnostic validation prompts and decoding. Preserve failed and null runs. These samples do not support a held-out benchmark claim or a curriculum-effect claim without further replication.

### Prerequisite diagnostic, 2026-10-04

The current total ceiling is $64, including earlier runs. [The prerequisite config](../configs/rl/qwen_05b_prerequisite_grpo.json) extends the separate whole-proof pilot. It uses authored elementary tasks, not public undergraduate evaluation. The README records execution status.

Compare random order with equality, natural-number rewrites, divisibility, and induction in prerequisite order. Give each arm identical occurrences, library access, and decreasing assistance. Each complete pass supplies lemma statements, then names, then no task-specific assistance. This comparison does not isolate assistance or enforce premise unlocking.

Measure initial reward variation on training tasks. Keep reference proofs outside model inputs. Freeze separate diagnostic validation and held-out compositions before generation. Evaluate both without task-specific assistance. Record textual near duplicates and preserve the threshold. Semantic independence remains unproved.

Skip optimizer updates if every sampled training group has constant reward. Otherwise run the matched arms with the existing terminal Lean reward and TRL settings. Report unassisted evaluation next to the unchanged policy. A gain on authored elementary tasks does not establish undergraduate proof capability.

## Primitive-based midtraining, 2026-10-05

### Question and boundary

Can information-guided reinforcement learning teach reusable number-theory operations, their conditions, and their composition to the fixed small model? Treat this next stage as exploration. Learning is the primary endpoint; questions, rollouts, tokens, and cost measure how much learning requires. Do not equate a working optimizer or increased prediction reward with acquired mathematical understanding.

Manas owns the concept dependencies, training examples, evaluation, and curriculum comparison. Reuse the existing RLP runner and ordinary GRPO library where appropriate. Anwesha retains interactive Lean integration; Abhinav retains optimizer and architecture research. This section registers a direction, not a new launch or an implemented dataset. The cumulative authorized ceiling remains binding.

### What the model should learn

| Concept | Required operation | Required condition or counterexample | Composition target |
| --- | --- | --- | --- |
| Equality and quantifiers | Substitute equals; introduce a variable; construct an existential witness | A numerical example does not prove a universal claim | Witness-based divisibility arguments |
| Divisibility | Construct and verify the multiplier in the definition | Distinguish divisibility from its converse | Closure under sums and transitivity |
| Euclidean division and gcd | Produce quotient and remainder; preserve common divisors through a reduction | Require a positive divisor and a bounded remainder | A complete Euclidean algorithm with its invariant |
| Bezout identities and coprimality | Produce coefficients and check the identity | An inverse requires coprimality with the modulus | Derive modular inverses from Bezout identities |
| Primes and factorization | Test divisors and verify a factorization | A prime dividing a product is essential in Euclid's lemma; a composite counterexample exposes the condition | Use Euclid's lemma in divisibility arguments |
| Congruences | Rewrite a congruence as divisibility of a difference | Cancellation requires the appropriate coprimality condition | Combine congruences with divisibility and inverses |
| Induction and contradiction | State the hypothesis and justify the next inference | Check the base case and prohibit assuming the conclusion | Unseen elementary number-theory proofs |

Each training unit needs a sourced definition, dependency IDs, justified operations, worked training examples, and false variants with counterexamples. Preserve complete assumptions during extraction. Audit existing textbook blocks before adding more text. Topic labels and file order do not establish a dependency graph. Use human-readable mathematics before requiring a Lean serialization.

### Acquisition and reward

Use coherent training derivations as RLP input. Sample a hidden continuation from training text; let the model generate reasoning that predicts it. Keep the target hidden from that generation. Apply reinforcement loss to generated reasoning tokens. Definitions and worked training examples provide external information, without adding a supervised target loss.

First examine the current prompt and scorer construction on frozen training or curriculum-validation examples. Test whether the true continuation receives sensible probability and whether generation ends normally. Hold weights and examples fixed across prompt variants. This distinguishes prompt construction from learning. Do not infer that short reasoning caused failure from truncation counts alone.

Random-token RLP remains the reference objective. Sampling at a mathematically meaningful continuation boundary is a proposed task-conditioned adaptation, not an exact reproduction. Register its target-selection rule and expose the same information in its control. The observed continuation must contain a mathematical decision, rather than rewarding only prose or delimiters. Keep the information-gain reward separate from mathematical correctness in every record.

After acquisition, use separately identified GRPO exercises for computable mathematical decisions: witnesses, quotient/remainder pairs, Bezout coefficients, theorem conditions, and counterexamples. Verify the returned object and all required constraints. Correct final answers do not certify unchecked reasoning. This stage uses verifiable rewards and must not be reported as RLP. Avoid an untested weighted sum of correctness and prediction rewards.

Later proof RL asks the model to express and combine these operations in Lean. Check the entire original theorem. A good computational answer alone does not establish formal proof ability.

### Curriculum and evidence

Start with primitive applications, then compositions of the same operations. Revisit earlier concepts throughout training. Use curriculum validation to decide when to reduce supplied definitions or examples. Evaluate both with and without this information to distinguish assisted application from retained capability. Freeze advancement thresholds and replay allocation before training.

The historical arithmetic questions now serve as development diagnostics for further redesign. Preserve their earlier held-out role in completed runs. Freeze a fresh evaluation set before the next study; reserve parameter ranges, paraphrases, and composition structures separately. Include balanced valid and invalid theorem applications. Audit shared proof templates and mathematical equivalence, not just exact strings.

The first learning pilot asks whether primitive performance improves and persists when assistance is removed. Its final evaluation also tests unused combinations. A primitive gain without composition is a partial result. A prediction gain without mathematical improvement fails the acquisition objective. A format-only gain must be reported separately.

Once acquisition is measurable, compare prerequisite order with random order over identical training occurrences. Fix starting weights, rewards, assistance, optimizer, and resource caps. Use repeated seeds before claiming a reliable ordering effect. Compare RLP followed by mathematical GRPO with direct mathematical GRPO under a shared total budget; report the extra acquisition exposure separately.

Maintain a concept-level record of baseline success, later success, retained success after subsequent training, and composition success. Preserve exact responses and failed checks. Report unique examples, presentations, input tokens, generated tokens, and verifier calls separately. The final undergraduate endpoint still requires unseen proof-based problems; elementary task performance cannot establish that endpoint.

### Source basis and limits

[RLP](https://arxiv.org/html/2510.01265v2) supplies the information-gain objective. It does not establish acquisition on this checkpoint or corpus. [The controlled rewrite-grammar study](https://arxiv.org/html/2607.07646v1) separates primitive operations from learned compositions and audits generated traces. Its synthetic environment motivates our measurements; it does not demonstrate number-theory acquisition in Qwen.

The next implementation step is CPU preparation and a coverage audit of these training units and separate evaluation families. Preserve the completed precision experiment unchanged. Register a bounded frozen-model prompt comparison before another training launch.

## Knowledge inventory and assisted execution, 2026-10-05

This staged acquisition study follows the RLP evaluation. The user selects early RLP (`checkpoint-118`) as the training start. Preserve unchanged early RLP as the no-training control. Keep the original Instruct model and final RLP as diagnostic references. The first implementation is the [knowledge inventory](../configs/eval/knowledge_inventory_v1.json), using the existing acquisition evaluator. This is an exploratory development diagnostic. It is not a fresh held-out transfer benchmark.

### Stage 0: locate the failure

For each concept, measure recall, recognition, supplied use, and independent use in separate fresh contexts. Recognition requires an explanation as well as selecting a definition. Rotate the options to expose position dependence. Repeated rotations do not increase the number of independent concepts. Supplied and independent use share the same underlying problems. The supplied condition adds definitions and local guidance; its contrast measures the total effect of that assistance, not a pure causal knowledge effect.

The initial inventory covers divisibility, Euclidean division, gcd, Bezout identities, congruence, modular inverses, Euclid's lemma, and modular cancellation. It combines numerical certificates with symbolic arguments and counterexamples. Equality, quantifiers, induction, and longer proof strategy need subsequent operator-specific inventories. Do not describe this first inventory as covering all number theory.

Use free mathematical responses rather than Lean code. The response allowance remains the existing evaluator's full generation budget. Grade with the frozen content rubric from the decomposition study. Record complete core answers, partial progress, ancillary errors, full correctness, and truncation separately. Use checkpoint-blinded independent review and retain disagreements. An explained recognition answer is not equivalent to successful application. A single failed response is not evidence that the corresponding knowledge is absent.

Report a concept-by-condition matrix with correct counts and denominators, not unsupported binary mastery labels. Retain paired outcomes for each supplied/independent problem. Report an input-independent option baseline and the effect of option rotation. Keep this diagnostic outside the final acquisition endpoint because its results guide task design. Freeze fresh retention and transfer manifests before training.

### Stage 1: learn verified local operations

Start with supplied knowledge and short mathematical operations. The first operator set is existential elimination, substitution, witness composition, and integer linear combination. Expand toward quotient/remainder certificates, common-divisor preservation, Bezout certificates, and modular inverses after readiness is measured. Anwesha retains ownership of the Lean interface. Reuse the existing pinned checker for bounded standalone tasks where it is sufficient.

For divisibility, supply `a|b iff there exists an integer k with b=a*k`. First ask for the equality associated with a witness. Then supply `b=a*k` and `c=b*l` and ask for an expression `w` satisfying `c=a*w`. A correct candidate is `k*l`. Another task supplies `x=d*u` and `y=d*v` and asks for a witness for `d|(s*x+t*y)`. A correct candidate is `s*u+t*v`. These are training examples, not reserved transfer problems.

The mathematical action is an expression or certificate. The prototype in `scripts/verify_local_operator.py` inserts a bounded polynomial expression into an immutable Lean obligation. Its training fixtures cover supplied-witness reuse, composition, linear combination, and scaling. It does not yet evaluate existential elimination or model-generated explanations. It must not search for a replacement witness or repair the action. Restrict candidate syntax to the declared expression grammar before interpolation. Check the candidate's identity and side conditions. Check positive controls, wrong witnesses, reversed divisibility, missing hypotheses, and forbidden declarations before using reward outputs. Until that adapter passes these checks, report its integration as pending.

Use binary reward for the requested verified local operation. Do not use prediction reward, proof length, or reduced goal count as a substitute. Record the proof obligation and verifier result with the candidate. Keep syntax errors, mathematical rejection, timeout, and infrastructure failure distinct. An accepted certificate does not certify unchecked explanatory prose. Report informal mathematical correctness separately from Lean serialization readiness. A fixed proof skeleton that performs substitution validates the submitted witness; it does not show that the model generated those proof steps.

Measure unchanged-policy sampled readiness before an optimizer launch. Record accepted actions and within-prompt reward variation. If every action fails, simplify the operation or its serialization using development tasks before training. Preserve the failed readiness result. Do not select favorable held-out tasks or lower the correctness requirement to produce variation.

### Stage 2: remove assistance and test acquisition

The assistance schedule is a hypothesis, not an established ordering of difficulty. Keep the requested sequence as the initial curriculum: full definition, partial definition, discrimination plus application, recall plus application, delayed recall, and independent integration. Separate the selected definition from its application in scoring. Recognition alone earns no full task success. Partial-definition prompts must still require a correct application.

Advance a concept using curriculum validation only. Register validation membership, advancement threshold, replay allocation, maximum attempts, and token/Lean-call caps before training. A readiness diagnostic must determine a feasible task granularity before these launch settings are frozen. Do not choose advancement rules from final test outcomes. Record every presentation and checkpoint transition so that a later fixed-assistance control can replay the same task exposure.

Delayed recall requires subsequent optimizer updates on other concepts with no replay of the tested concept during the declared delay. Report the delay in optimizer steps and training tokens. Mere elapsed time or a fresh chat is not a retention intervention. Use fresh paraphrases at the delayed test, alongside a separately reported repeated-item control. Retention tests must not update the model or determine curriculum promotion.

Independent integration requires withheld composition structures, not renamed variables from trained proofs. Freeze training, curriculum-validation, retention, and transfer manifests before optimization. Audit exact text, normalized statements, mathematical templates, dependencies, and earlier diagnostic exposure. Shared primitives are intentional; shared composition templates limit the transfer claim. Unknown pretraining exposure remains uncontrolled.

The first learning claim requires improvement over the unchanged starting model on fresh unassisted tasks, followed by retention and unused compositions. Improved supplied execution alone establishes assisted learning. Improved recall without application establishes a narrower recall result. Keep these outcomes separate. To attribute a gain to fading assistance, compare against continued full assistance with matched starting weights, task exposure, reward, optimizer, and resource caps. Repeated seeds are required before claiming a reliable curriculum advantage.

### Execution boundary and source basis

Prepare and evaluate the inventory on cpu-angad before requesting another GPU allocation. This decision authorizes preparation and diagnosis; it does not establish unused training budget. Before paid training, reconcile the existing cumulative budget and present the concrete launch config. Use the early RLP checkpoint selected by the user. Do not substitute the final checkpoint.

[Judson's integer chapter](https://twjudson.github.io/aata-files/aata-html/integers-section-division-algorithm.html) supplies the divisibility and division conventions. [Mathematics in Lean](https://leanprover-community.github.io/mathematics_in_lean/C02_Basics.html) documents substitution, equality calculations, and polynomial identity checking. These sources support the mathematical operations. They do not establish that this assistance schedule causes acquisition. Preserve pinned Lean and mathlib revisions with every formal verification record.

## Information availability and the model target, 2026-10-06

The user sets the deliverable: a model with approximately 500 million parameters that reliably constructs proofs and solves undergraduate-level mathematical problems. Start from early RLP (`checkpoint-118`). The curriculum is a proposed means to that goal, not the endpoint. Success on local operators or assisted exercises does not establish this capability. The undergraduate subject coverage, reliability threshold, and deployment budget remain to be specified before final evaluation is frozen.

### Representation and intervention

Use a canonical record per verified reference derivation:

\[
E_i=(\Gamma_i,g_i,\mathcal K_i,G_i,\pi_i,R_i).
\]

Here, the components contain assumptions, the goal, annotated mathematical prerequisites, a scoped dependency graph, proof operations, and renderer/verifier provenance. Include theorem identity, source revision, split membership, and annotation provenance. Knowledge annotations describe this derivation; they need not contain every possible proof's prerequisites. A theorem can have multiple valid derivations. Do not reject a valid alternative because it differs from the reference graph.

Distinguish local operators, reusable compositions of operators, and the complete dependency graph. Preserve variable scope, types, discharged assumptions, and dependencies when abstracting examples. Replacing divisibility with an arbitrary relation does not preserve witness composition without corresponding algebraic assumptions. Recheck each abstracted instance. Tactic text alone is not a verified extraction of the semantic proof graph; audit extracted graphs against replayed states before scaling corpus annotation.

Render matched views of each training theorem: full support, knowledge supplied with structure hidden, structure supplied with knowledge hidden, partial support, and no task-specific support. Keep reference solutions outside model-visible fields unless an explicitly declared training view supplies the corresponding step. Keep every view and abstraction of a theorem in its assigned split.

Treat knowledge demand and structural demand as intervention settings, not measured independent abilities. Implement them through explicit masks over facts, lemma names, subgoals, edges, and operation hints. A scalar between zero and one must identify a declared mask distribution; it is not a calibrated difficulty scale. Record actual visible information. A subgoal can reveal a definition or witness, and a supplied lemma can reveal the strategy. Audit these cross-cues on development examples. Library availability and automation require the same policy across arms.

### Curriculum comparisons

Retain the proposed trajectories, with coordinates `(knowledge demand, structural demand)`:

| Curriculum | Intended path |
| --- | --- |
| Structure first | `(0,0) -> (0,1) -> (1,1)` |
| Knowledge first | `(0,0) -> (1,0) -> (1,1)` |
| Joint | `(0,0) -> (1,1)` with both demands increased together |

Use the same semantic theorem pool, starting checkpoint, reward, optimizer, and evaluation suite. Freeze the mapping from progress to masks before training. Record concept and operator exposure, prompt tokens, generated tokens, verifier calls, and GPU time. Compare within shared realized resource ranges rather than claiming simultaneous equality of all resources.

The paths above change both order and the distribution of support conditions. Matching theorem identities alone does not isolate order. Report them as path comparisons. For a separate order-only claim, give every arm the same multiset of theorem-view presentations and change their order. Include a shuffled-order control. Fixed full assistance tests the value of removing support; direct unassisted training tests its value against the final task. Schedule these controls within the declared compute budget, rather than assuming all fit the next pilot.

Choosing a graph is already a strategy decision. When structure is hidden, label the task as including that decision. To isolate execution and graph completion first, restrict the allowed decompositions or supply graph boundaries. Do not describe autonomous structure generation as strategy-free.

### Evaluation and acceptance

Keep the requested diagnostic curves: knowledge retrieval with structure supplied, operator execution with mathematics supplied, application under partial support, and unassisted full-proof success. These are conditional task scores, not direct measurements of independent internal faculties. A low partial-support score does not by itself demonstrate a knowledge-structure binding mechanism. Compare matched tasks and inspect whether missing facts, invalid operations, or graph selection explain failures.

The final endpoint must include complete proofs on unseen problems without task-specific knowledge or structure hints. Evaluate undergraduate problem solving separately, including tasks where the model must discover the answer. Proving a supplied correct answer does not establish answer discovery. Report informal mathematical reasoning separately from Lean acceptance. The user selects separate standalone and Lean-assisted reporting. Standalone generation receives no verifier feedback, retrieval, or external solution repair during generation; final grading can still use Lean. Lean-assisted generation can use verifier feedback under a preregistered search budget. Report single-attempt success separately from bounded-search success. Use the same held-out problems and declare token, candidate, verifier-call, and elapsed-time caps. Report each mode independently; assisted performance must not substitute for standalone performance. External tools, retrieval, automation, candidate counts, and any larger model must be disclosed; they cannot silently supply the target model's missing capability.

Freeze subject coverage and success thresholds before final testing. Report per-subject results and uncertainty, not an aggregate that hides failed subjects. Use independent instance, concept, composition, motif, and depth splits where feasible. For a withheld concept, distinguish supplied new knowledge from unaided retrieval; the former tests conditional structural transfer. Variable renaming alone does not establish transfer. Preserve retention tests after intervening updates and separate curriculum validation from untouched final evaluation.

Periodic probes used to guide development belong to curriculum validation. Keep final test results unavailable to promotion or checkpoint selection. For cost-to-threshold metrics, predeclare the threshold and sampling schedule; report unreached thresholds as censored. Compute learning-curve area over a shared resource range. Neither a favorable curve nor success on supplied operators establishes the final model target.

### Next implementation boundary

First validate faithful witness extraction and sampled execution readiness with the existing early policy. Then canonicalize a bounded verified development subset and audit its rendered views for knowledge and structure leakage. Use that subset to test the intervention before annotating the entire corpus. The full graph corpus, mask scheduler, curriculum comparison, and undergraduate evaluation suite remain proposals in this section. This update changes documentation only and does not launch training.

### Sources and inference limits

Lean's [auxiliary subgoals documentation](https://lean-lang.org/theorem_proving_in_lean4/Propositions-and-Proofs/#introducing-auxiliary-subgoals) explains how `have` introduces intermediate propositions. [DeepSeek-Prover-V2](https://arxiv.org/html/2504.21801v2) uses subgoal decomposition and builds training tasks with and without preceding subgoals as premises. [LeanAgent](https://proceedings.iclr.cc/paper_files/paper/2025/hash/b67c77f8db8b991d73d6f2e16f491840-Abstract-Conference.html) describes a curriculum organized by mathematical difficulty. These are related mechanisms, not evidence for the proposed ordering effect or the target model's eventual reliability. Novelty and feasibility remain open research questions.

## Bounded training plan, 2026-10-06

This is an exploratory RL-only pilot followed by a conditional curriculum comparison. It starts from early RLP (`checkpoint-118`), with a fresh optimizer and an unchanged reference copy. It does not continue the final policy or the RLP prediction reward. The settings are implemented in `configs/rl/qwen_05b_local_operator_grpo.json` and the local-action mode of `scripts/run_grpo_pilot.py`. Implementation does not establish learning.

### First pilot: establish execution learning

Manas owns the pilot data, adapter integration, and evaluation. Reuse `scripts/run_grpo_pilot.py` and pinned TRL rather than creating another optimizer. Reuse the local witness checker for its supported operations. The local-action mode provides a separate correctness callback; historical whole-proof modes remain unchanged. Anwesha retains ownership of the general interactive Lean interface. This standalone pilot does not require that unfinished interface.

1. Freeze a development pool of supplied-witness reuse, composition, linear combination, and scaling. Add numerical certificates alongside symbolic witnesses. Split mathematical templates before rendering or variable renaming. Keep the earlier inventory as development evidence, not the new test set. Record separate training, curriculum-validation, retention, and composition manifests. Do not promise broad proof competence from these operators.
2. Allow reasoning followed by one explicitly marked final witness. Extract that submitted object without inventing an answer. Support declared mathematical notation through a bounded parser; reject ambiguous final answers. Record extraction, verification, and explanatory-content scores separately. A verified witness earns action credit even if unrelated prose is wrong; it does not earn full-response correctness. Training and evaluation use the same extraction contract.
3. Sample the unchanged early policy on the frozen training pool. Require both accepted and rejected actions within groups across multiple operator families. Report the entire pool, including unsolved tasks; do not select a favorable subset and call it general readiness. If all groups fail, use more supplied intermediate steps on development tasks and repeat within the same cap. If all succeed, remove one declared support component. Do not launch a correctness optimizer on constant-reward groups.
4. Run a short fixed-assistance GRPO pilot. Give reward one for a verified submitted action and zero for a mathematically rejected action. A missing or invalid answer receives zero task-success reward with a separate format status. Exclude infrastructure failures from updates and retry only within the existing cap; never convert them to mathematical zeros. No model-grader reward, prediction reward, or format bonus enters this pilot.
5. Evaluate fresh validation tasks before training and at fixed checkpoints. Inspect actual weight changes, optimizer-state precision, reward variation, and validation correctness. Preserve all checkpoints. A training-reward increase without fresh validation improvement is a failed learning pilot. An improvement caused only by extraction compliance is a format gain, not execution learning.

### Proposed pilot settings and stopping rules

| Setting | Proposal |
| --- | --- |
| Trainable weights | Full parameters; FP32 weights, AdamW moments, training, and inference; no BF16 autocast |
| Optimizer | AdamW; learning rate `1e-6`; constant schedule; weight decay `0`; gradient clipping `1.0` |
| Reference penalty | KL coefficient `0.02` against frozen early RLP; a stabilizing proposal, not a proven remedy |
| Sampling | Eight completions per identical theorem and support mask; temperature `0.7`; top-p `0.9` |
| Context | At most 2,048 prompt tokens; at most 1,024 new tokens; reject oversized prompts rather than truncate premises |
| Pilot length | At most 64 optimizer steps; curriculum validation at steps 0, 16, 32, 48, and 64 |
| Job caps | At most 1,000,000 generated tokens and 4,000 physical Lean calls, including readiness and evaluation; at most two hours including setup |
| Memory | At most 24 GiB CUDA allocation and 16 GiB host memory; serial Lean execution; confirm model-specific peak before launch |
| Readiness | At most 256 sampled completions, charged to the same job caps |
| Checkpoint choice | Highest complete-core validation accuracy; ties choose the earliest; full correctness and per-family regression reported alongside |

Stop at the first resource cap. Stop and preserve the run on nonfinite updates, unchanged weights after an informative update, or repeated verifier infrastructure failure. Pause training after two consecutive validation checkpoints fall below the unchanged baseline on both core completion and full correctness. These rules control waste and regression; they cannot guarantee improvement. Do not silently change settings mid-comparison. Checkpoint selection uses validation only; final retention and composition tests remain untouched until the selected checkpoint is fixed.

The initial memory allowance is a hard cap, not a measured peak. Before renting hardware, calculate parameter, gradient, optimizer, reference-model, activation, and verifier memory using the actual checkpoint. If it does not fit, revise the proposed recipe before launch; do not silently switch precision or parameter scope. The existing cumulative spending ceiling includes past runs. Remaining funds are not established by this document. Reconcile the ledger and present the exact lease cost before requesting a paid launch.

### Expansion after the pilot

Proceed to assistance fading only if fresh execution validation improves beyond output-format compliance. Register a minimum improvement and uncertainty rule after the validation manifest size is fixed, before generating baseline outcomes. Otherwise record failure and inspect action granularity or data coverage; do not repeat a longer run with the same constant signal. Supervised bootstrapping would change the RL-only study and requires a separate decision.

For the curriculum experiment, initialize every arm from the same frozen checkpoint. Use either the selected execution checkpoint for every arm or early RLP for every arm; do not give one arm extra warm-up. Freeze this choice before comparison. Apply the structure-first, knowledge-first, and joint paths to the same semantic pool and resource limits. Treat this as a path comparison unless the theorem-view multiset is also identical. Fixed schedules support the first comparison; validation-adaptive promotion is a later intervention because it changes exposures. Repeat seeds only after a bounded comparison is feasible within a declared budget.

The longer-term expansion moves from local operations to complete short proofs, then fresh undergraduate problems across declared subjects. Include induction, case analysis, and other necessary proof forms when the verified corpus supports them. Preserve separate standalone and bounded Lean-assisted endpoints. Successful local execution is the first milestone; the final model target remains unestablished until those external endpoints pass.

### Implementation sequence and evidence

The dependency chain is: extraction contract and frozen development data -> unchanged-policy sampling -> operator reward adapter and numerically functioning update -> fresh execution validation -> matched curriculum paths -> retention and undergraduate evaluation. Each step produces an artifact consumed by the next. Witness extraction and the bounded operator adapter are implemented. Corpus-wide graph tooling remains outside this pilot.

The existing completion pilot supplies grouped sampling and checkpoint machinery. [TRL v0.24.0 documentation](https://huggingface.co/docs/trl/v0.24.0/en/grpo_trainer) documents custom rewards, group-relative advantages, and nonzero `beta` for a reference penalty. These support implementation choices, not a claim that the proposed hyperparameters will work. Preserve failed and null runs and report the baseline beside every claimed gain.


## Controlled mathematical RLP without Lean, 2026-10-06

The user defers Lean kernel integration and requests RLP development through data control, correctness review, and better grading. This direction replaces the proposed immediate Lean-backed proof-step training as the next experiment. It preserves the completed witness pilot as evidence. The following design is proposed; no new reward, sampler, grader calibration, or training run is implemented by this entry.

### Outcome and boundary

Study whether RLP improves durable access to mathematical knowledge and valid use of proof primitives on fresh instances. Start each comparison from the same early RLP checkpoint, as the user selected. Preserve the unchanged early policy as a control. The correctness-trained descendant remains diagnostic; do not initialize one RLP arm from it while others start earlier. Keep the RL-only scope. No supervised target loss, Lean feedback, or external solution repair enters these RLP arms.

The current local reward scores how generated reasoning changes the likelihood of a hidden mathematical span relative to an EMA teacher. It does not directly score the mathematical validity of the reasoning. Better data can control the truth of the target; it does not establish the truth of every generated intermediate claim. The [official RLP repository](https://github.com/NVlabs/RLP) describes prediction information gain as its reward. No published performance number is used to justify this proposed small-model experiment.

### Data admission and controlled distributions

Reuse the authored primitive corpus and existing exporter. Audit its statements and derivations before expansion; do not assume that prior degradation proves the corpus was incorrect. Each admitted mathematical item needs its domain, assumptions, conclusion, reference derivation, provenance, and review status. Use exact arithmetic or symbolic checks for their supported claims. Proof text receives independent mathematical review; unresolved disagreements remain excluded. Label reviewed proof text accurately rather than calling it formally verified.

Attach matched applications and explicitly labeled invalid applications to the same concept. Examples should expose a missing hypothesis, reversed implication, lost witness factor, or unjustified cancellation. A false example must appear as an identified error with its explanation, not as ordinary correct acquisition text. Keep positive and negative examples matched for style so surface wording cannot reveal correctness.

Control concept exposure, operator exposure, numerical versus symbolic representation, supplied knowledge, supplied structure, graph depth, and replay of earlier items. Record the actual sampled distribution. Prefer a fixed schedule for the first comparison; adaptive promotion changes exposure and is a later intervention. Hold out new instances, operator compositions, and longer derivations. Split before rendering paraphrases or support views. Held-out concepts with their facts supplied measure conditional transfer, not independent acquisition of an unexposed fact.

Choose hidden targets that carry mathematical content: a complete definition with its conditions, a valid local transformation, a constructed witness, or a short derivation. Avoid a target that merely predicts a familiar answer-format phrase. Include both numerical tasks and proof tasks. Keep exact wording likelihood separate from semantic grading; alternate correct answers must not be marked mathematically false because they differ from the target string.

### Comparison sequence

| Arm | Data and sampling | Training reward | Purpose |
| --- | --- | --- | --- |
| Unchanged early policy | No updates | None | Baseline for every capability probe |
| RLP control | Audited shared corpus with flat sampling | Existing mathematical-span RLP | Establish learning with the reviewed corpus |
| RLP curriculum | Same corpus with declared information-availability schedule and replay | Same existing RLP | Test the sampling intervention |
| Conditional objective ablation | Same corpus and schedule as the curriculum arm | Correct-versus-incorrect continuation margin | Test the reward change separately |

For an order-only comparison, give both trained arms the same multiset of support views and change order. If support-view frequencies differ, report a distribution or path comparison. Match starting checkpoint, numerical precision, optimizer, evaluation schedule, and resource ceilings. Compare within shared realized token and scoring-cost ranges. Historical runs are context, not matched controls for a new corpus.

The contrastive objective is a proposed RLP modification. Score the correct target against plausible wrong continuations that differ in one mathematical condition or operation. Use length-normalized scores and record scorer inputs, versions, EMA policy, and baseline conventions. Wrong continuations remain hidden scoring candidates. Audit whether the scorer distinguishes the pair before paying for training. Candidate likelihood is still a model judgment, not a proof certificate. Changes to the scorer or baseline require their own control; do not bundle them with the sampling comparison.

Use the independent grader for corpus admission and evaluation first. Adding its output to the training reward is a separate hybrid objective, with separate grader costs and an independent evaluation judge. Do not call a grader-reward arm unchanged RLP.

### Grading and acquisition evidence

Grade each generated response against the problem and an audited reference. Accept valid alternative methods and conventional notation. Record fact correctness, satisfaction of theorem conditions, validity of operations, requested conclusion, and full-response correctness separately. Preserve partial credit without replacing a missing central argument. Record the first invalid step and its specific mathematical defect. [ProcessBench](https://arxiv.org/abs/2412.06559) evaluates earliest-error identification; this motivates the diagnostic, not a claim that our grader is validated.

Before interpreting scores, test the grader on known-correct derivations, controlled mathematical errors, and equivalent paraphrases. Include correct answers reached through invalid reasoning. Blind checkpoint identity and response order. Preserve independent reviews, disagreements, and adjudication. A larger grader does not establish correctness by its size. Use human review or leave difficult disagreements unresolved where available evidence is insufficient.

Probe recognition, recall, supplied application, independent application, delayed retention, and unfamiliar composition. Measure each before and after updates with matched generation budgets. Immediate success with a fact in the prompt is supplied use. Independent acquisition requires improvement when the fact and reference reasoning are absent, including later retention. Knowledge learned during this experiment is distinguished from knowledge already accessible at baseline. Keep standalone generation separate from any later tool-assisted endpoint.

Do not select checkpoints by RLP reward alone. Use frozen curriculum-validation probes of mathematical content, operation validity, and retention. Keep final held-out results outside selection and adaptive sampling. Reward growth without fresh semantic improvement is a failed acquisition experiment. Improve the grader or corpus when its audited errors demand it; do not change scoring after inspecting a favorable checkpoint to obtain a pass.

### Dependencies and next action

Manas owns the corpus admission records, distribution specification, and evaluation rubric. The dependency chain is: audited shared corpus and known-error grading controls -> frozen probes and unchanged-policy baseline -> matched existing-RLP sampling comparison -> conditional contrastive objective test. Reuse `scripts/prepare_rlp_corpus.py` and `scripts/run_rlp_pilot.py` when their contracts fit. No new optimizer or Lean interface is required by this plan. Numerical settings and funding for additional runs must be recorded before launch; this entry authorizes no new spend.


### Formalization work package

This package makes the preceding design reviewable before implementation. Manas owns each output below. Existing corpus code, inventory probes, and grading fields are reused where they satisfy the contract. The schemas and schedules in this section are specifications, not implemented exports or measured capabilities.

#### Research question and estimands

Ask whether the order of mathematical information availability improves independent use of learned facts and valid proof operations under RLP. Separate this from whether a different prediction reward improves the same outcomes. The initial study is exploratory. A later confirmatory study needs independently frozen tests and repeated seeds.

Knowledge acquisition means improved recall and application of exposed mathematical facts on fresh tasks, without those facts supplied at evaluation. Delayed retention tests this after intervening updates on other material. It does not prove acquisition from scratch: the model may already contain related knowledge from pretraining. Operator learning means improved valid transformations when required mathematics is supplied. Proof composition means valid combinations of these transformations on unfamiliar dependencies. Autonomous decomposition includes strategy and is deferred until these conditional tasks are understood.

Primary endpoint: macro-averaged full-response correctness on unassisted application tasks, averaged equally across concept families. Report each concept's denominator and outcomes. Unknown grader judgments count as unestablished successes in the conservative primary score, with their frequency reported; they are not labeled mathematical errors. Secondary endpoints are recall, condition checking, supplied operator use, composition, delayed retention, and first-error category. Report core completeness and correct final objects alongside full correctness. Never combine these into an opaque capability score.

The treatment effect for the first comparison is the difference between the ordered and shuffled arms on these matched endpoints. Each arm's change from unchanged early RLP is also reported. The baseline controls preexisting accessible knowledge; it does not control unknown pretraining contamination.

#### Existing artifacts and minimum extensions

| Existing owner/path | Reuse | Required extension |
| --- | --- | --- |
| `scripts/prepare_rlp_corpus.py` | Authored primitive records and mathematical-span export | Explicit assumptions, operation labels, support views, review provenance, family splits |
| `scripts/run_rlp_pilot.py` | Grouped RLP generation, scoring, optimizer, counters, checkpoints | View-aware order and exposure records; explicit response/scorer contract; no new optimizer |
| `configs/eval/knowledge_inventory_v1.json` | Recognition, recall, supplied use, independent use | Fresh curriculum-validation and final instances; retention and composition items |
| `configs/eval/decomposition_grading_v2.json` | Partial credit, alternate valid methods, core versus full correctness | Named mathematical dimensions, first-error location, unknown judgments, calibration manifest |
| Completed run records | Failure categories, precision evidence, source examples | Development evidence only; repeatedly inspected examples do not become untouched tests |

Do not build a general proof-graph extractor for this bounded study. Annotate reference dependencies directly where needed. Source correctness, renderer correctness, grader reliability, and training utility receive separate records. A passing data check cannot establish any of the others.

#### Mathematical records

Define a knowledge item as `k = (id, domain, statement, assumptions, prerequisites, valid_uses, invalid_uses, source, review_status)`. Define an operator as `o = (id, input_state, required_conditions, action, output_state, justification)`. Define an example as `e = (id, family_id, concept_ids, operator_ids, assumptions, goal, reference_steps, dependencies, split, provenance)`.

A reference step records its claim, operation, premises, scope, and justification. Every used premise must be an assumption, an exposed fact, or an earlier established step. Preserve integer versus natural-number domains and zero/sign conditions. A valid reference need not be the only acceptable solution. Grading checks alternative solutions against the task rather than requiring the reference sequence.

Begin with the mathematical content already represented: divisibility, witness construction/composition, substitution, linear combination, scaling, and condition checking. Add Euclidean division, gcd, and modular operations only where the audited inventory and corpus support them. Keep induction and contradiction as later compositions unless their local obligations and references are admitted. This is a coverage specification, not a claim that every item has been reviewed.

Admission statuses are `unreviewed`, `accepted_by_exact_check`, `accepted_by_mathematical_review`, `disputed`, and `rejected`. Record the checker or reviewers, source record, and rationale. Exact arithmetic checks certify their declared computation, not a universal proof. Without Lean, reviewed proofs remain mathematically reviewed rather than formally certified. Only admitted records enter positive training targets. Disputed records remain visible in the audit but excluded from training.

For a cancellation item, the domain is integers and the nonzero hypothesis belongs to the rule. A paired invalid use omits that hypothesis. For divisibility composition, a valid witness is constructed by substitution and multiplication, without assuming a nonzero divisor. Invalid examples are explicitly framed as errors to identify; their target explains the error. They must not become unlabeled false facts in the acquisition corpus.

#### Views and information availability

Render `x(e,v)` from the same admitted example, where `v = (knowledge_mask, structure_mask, representation, task_type)`. Model-visible fields contain only the rendered task and declared support. Private fields contain reference steps, hidden targets, review records, and scoring candidates. Keep these field sets separate at export and prompt construction.

Use a discrete knowledge axis: full fact supplied, partial fact supplied, recognition alternatives, fact absent. Use a discrete structure axis: local operation supplied, sequence/subgoals supplied, partial sequence, structure absent. These are intervention labels, not calibrated difficulty numbers. Record exactly which facts and hints each mask exposes. A subgoal that reveals a witness is knowledge leakage and must be annotated as such. A supplied lemma that reveals the decomposition is structural support.

The first order comparison follows the user's information-removal idea while keeping structure supplied or partial. It does not sweep the whole grid. The ordered arm presents complete support, then partial/recognition support, then absent facts, with declared replay. The control shuffles the identical multiset of example-view presentations. No arm receives an extra view or an extra solved reference. Independent structure generation is evaluated separately and is not confused with local execution.

Freeze family membership before generating views, paraphrases, substitutions, or abstracts. All descendants of an example family stay in its split. Shared concepts across training and evaluation are intentional for instance transfer. Held-out compositions may combine known operators in new ways. A wholly unexposed concept is not an acquisition test; supply its definition when testing conditional transfer and report that condition explicitly.

#### Distribution specification

Create a manifest of presentations `P = {(example_id, view_id, repeat_index)}`. The ordered and shuffled arms consume the same `P` under different order functions. Freeze the random seed, prerequisite order, view counts, replay positions, and maximum presentation count before training. Sample without replacement within each declared pass. Log attempted and completed presentations, including partial final passes caused by a resource cap.

Audit marginal and joint exposure by concept, operator, support masks, numerical/symbolic representation, composition depth, and error category. Count source exposures, model-visible tokens, generated tokens, and scoring calls. Equal membership does not guarantee equal realized generation cost. Compare learning curves over their shared realized resource range and report incomplete exposure matching.

Do not discard hard tasks or zero-variance groups silently. Record retained and discarded groups and their mathematical families. Validation-driven resampling is outside the initial order comparison. The curriculum schedule cannot inspect final evaluation outcomes.

#### RLP reward and response contract

Let `x` be the rendered prompt, `r` the generated reasoning, and `y` the hidden audited mathematical target. The existing reward is a difference between mean teacher-forced target log probabilities:

\[
R_{\mathrm{RLP}}(x,r,y)
=\ell_\theta(y\mid x,r)-\ell_{\bar\theta}(y\mid x).
\]

The EMA teacher, denoted by the barred parameter, is held fixed within a sampled group. Generated policy tokens receive the RL update; hidden targets supply detached scoring evidence and no supervised loss. The same prompt, target boundary, output instruction, and scorer formatting conventions apply to both trained arms. Full mathematical spans replace arbitrary text positions in this study.

Group centering cancels the common teacher baseline. Up to a positive group correction factor, the advantage depends on the policy's reasoned score relative to other sampled responses. The existing runner uses no KL reference penalty. Consequently, the EMA baseline must not be described as preventing forgetting or preserving the initial policy. Keep this numerical recipe fixed in the first comparison; a reference penalty or changed scorer is a separate intervention.

Specify generated output as a complete requested answer with its reasoning. Require the same output behavior during training and evaluation. Private scorer prefixes remain explicit in the run record; they cannot be counted as a policy-generated answer. Record final-object correctness, reasoning correctness, and target likelihood separately. This addresses the documented training/evaluation contract mismatch as a common setup change before comparing order. Historical performance cannot isolate that setup change's effect.

For recognition tasks, vary candidate order and require a reason. For recall, hide the missing statement and its assumptions from the prompt. For supplied application, expose the relevant fact but withhold the requested result. For independent use, expose neither the relevant fact nor the reference operation. Alternate correct wording receives semantic evaluation credit even when its literal target likelihood differs.

The contrastive reward remains conditional future work. Its score would compare the audited correct continuation with plausible mathematical errors under the same input. Match candidate syntax and length as far as practical, and check reversed candidate order. Do not claim mathematical validity from a likelihood margin. Do not mix this reward change with a new sampler or scorer baseline in the first ablation.

#### Grading contract

For each response, return `review_id`, `fact_correct`, `conditions_correct`, `operations_correct`, `conclusion_correct`, `core_score`, `full_correct`, `first_error_step`, `error_category`, `uncertain`, `reason`, and `evidence`. Dimensions not requested by the task are marked not applicable. Unknown mathematical judgments remain unknown. Keep output-format compliance separate.

Retain the existing core-score rubric. A correct final witness can receive conclusion credit while an invalid cancellation prevents full correctness. A proof requires a valid argument; a true conclusion alone is insufficient. Graders may recover conventional notation and elementary implicit steps, but cannot supply a missing central lemma or repair an invalid central argument.

Create calibration cases from admitted records: intact derivations, equivalent alternate solutions, and controlled corruptions of assumptions, substitution, witnesses, or arithmetic. Preserve known labels and error positions. Blind checkpoint identity and response order. Use independent reference-informed reviews, preserve disagreement, and adjudicate against the response. Judge agreement is not correctness; disagreements without adequate mathematical evidence remain unresolved. A larger judge receives no automatic trust from model size.

Freeze rubric, grader version, prompt, and adjudication policy before comparing checkpoints. Test for false acceptance of correct conclusions with invalid reasoning and false rejection of valid alternate methods. Record grader errors and unknown rates by task type. The first study uses grading for admission and evaluation only, not policy reward. [ProcessBench](https://arxiv.org/abs/2412.06559) supplies the earliest-error diagnostic precedent; it does not validate this custom grader.

#### Evaluation and decision rules

Freeze separate curriculum-validation and final manifests. Probe recognition, recall with assumptions, supplied operation use, independent application, delayed retention, unfamiliar composition, and full short proofs. Include numeric and symbolic cases, zero/sign conditions, and distractor facts. Do not expose final references in prompts, retrieval, sampling, or grader calibration.

Evaluate the unchanged early model before updates. Use paired problems and matched decoding budgets for both trained arms. Greedy single-attempt evaluation is the initial primary mode; sampled search receives a separate endpoint. Count truncated outputs and unknown grades in denominators. During retention measurement, exclude the probed target concepts from the intervening training block and audit direct or paraphrased exposures. The next return to their replay pool ends that particular retention interval.

Define the concept-macro primary score as `F = mean_c(mean_i(full_correct[c,i]))`. Report per-concept rows, paired gains/losses, grade disagreement, and resource use beside it. Confidence intervals should respect example-family dependence rather than treating related support views as independent samples. Choose sample counts, uncertainty method, an acquisition improvement threshold, and a retention noninferiority margin before the first training update. The pretraining registration now fixes these choices in `configs/midtrain/qwen_05b_controlled_rlp.json`. Use a paired concept-cluster percentile bootstrap with 10,000 draws and seed 20261006. The interval describes this authored corpus, not training-seed variability. The exploratory progression threshold is a concept-macro gain of 0.10. The inverse-retention margin is zero lost successes after the common interlude. The small validation sample makes this a pilot decision rule, not a powered generalization claim.

Use curriculum validation for selection: maximize the primary unassisted full-correctness score among checkpoints satisfying the registered retention rule, with ties choosing the earliest checkpoint. If no checkpoint satisfies retention, report the failed constraint and evaluate the predeclared terminal policy for diagnosis. Final evaluation cannot change that choice. RLP reward alone cannot promote a checkpoint.

A pilot supports a narrow acquisition result only if fresh unassisted use improves and delayed retention meets its registered margin. Supplied-use gains alone support execution learning. Recall gains alone support improved recall. Reward gains alone support a proxy improvement. An order effect requires the matched ordered-versus-shuffled comparison. Reliable undergraduate proof solving remains a later endpoint requiring fresh subject coverage and separate standalone and Lean-assisted reports.

#### Execution milestones

| Milestone | Manas-owned output | Completion condition |
| --- | --- | --- |
| Audit existing material | Admission ledger linked to existing source records | Assumptions and derivations checked; disputes isolated; coverage and exclusions counted |
| Freeze records and views | Updated owning data specification and export manifest | Private fields excluded; masks reproducible; family membership and exposure multiset fixed |
| Calibrate grading | Existing evaluation rubric extended with labeled controls | Error types and alternate methods checked; unknown/disagreement policy recorded |
| Freeze baseline protocol | Probe manifests and resolved comparison config | Same early checkpoint; output/scorer contract; primary metric and retention rule registered |
| Implement minimum extensions | Existing exporter and RLP runner | Export/order invariants and one focused changed-path check; no parallel optimizer |
| Run matched pilot | Bounded run records and immutable policies | Registered caps, complete exposure logs, paired evaluation, failures preserved |
| Decide next intervention | Dated entry in existing decisions document | Acquisition, execution, retention, and uncertainty reported separately; contrastive work justified by evidence |

Do the admission and grader work before renting hardware. The next implementation boundary is the admitted-record/view export and matched ordering in existing modules. It excludes Lean integration, autonomous graph extraction, new optimizers, and paid multi-arm launches. Freeze run lengths, generation caps, scoring caps, numerical precision, and total funding after the baseline setup is costed. Reconcile against the existing cumulative budget; no new spending authority follows from this plan.


#### Pretraining implementation and frozen inputs

The [canonical status](../README.md#controlled-rlp-pretraining-preparation) records execution. The existing preparer now exports reviewed canonical records and matched views through `scripts/mathematical_rlp_data.py`. The existing RLP runner consumes complete-answer prompts and private reference spans. It checks the early weight hash, preserves exposure order, and requires one pass through the presentation multiset. The reward remains prediction-based and heuristic; correct reference data do not certify generated reasoning.

Use `artifacts/mathematical_pretraining_v1/data_v2/` on cpu-angad. This export supersedes ambiguous recall topic names identified by independent reviewers. It supplies the intended integer topic while withholding the rule and conditions. Report that condition as domain-cued recall. Preserve the first export and baseline outputs. Only changed recall prompts require replacement inference; merged manifests record the reused responses and exact source hashes.

The first comparison contains ordered and shuffled arms from the same early checkpoint. It does not implement separate structure-first, knowledge-first, and joint trajectories across the complete grid. Keep the existing original and final RLP diagnostics as historical references. Evaluate this fresh study against its unchanged early baseline under the same prompts and output budget.

The retention interlude uses the same frozen presentations for every candidate. It excludes inverse, Bézout, congruence, and modular-cancellation tasks. Generic integer arithmetic still overlaps, so the exclusion is operational rather than a theorem of independence. Require at least one inverse validation success before the interlude; a zero baseline cannot establish retention. Apply the interlude to a copy of each candidate. Compare inverse validation before and after it, then select the unmodified candidate. Register each candidate hash and fresh optimizer before this test. Final-test prompts and responses cannot influence selection.

The config disables training until funding and a launch allocation are registered. Existing CPU checks do not validate CUDA memory, GPU runtime, or real optimizer behavior on the new data. The first informative update must change sampled parameters with FP32 optimizer moments. Zero-advantage groups remain logged; a run with no informative update fails the learning check. No stronger claim follows from passing preparation checks.

## Lean-verified acquisition evaluation proposal, 2026-10-06

The ordered-arm review motivates a formal correctness endpoint before further acquisition training. This section records the recommendation discussed after that review. It does not modify the completed RLP objective, implement a verifier, or authorize a new run. See the [midterm reference](team_workstreams.md#midterm-reference-rl-correctness-and-acquisition-2026-10-06) for the experimental evidence and presentation narrative.

### Question and scope

Determine whether an existing checkpoint can submit valid mathematical objects and complete short proofs under an exact formal task contract. Start with the inspected acquisition items as development fixtures. They cannot become a fresh final benchmark. Freeze new validation and held-out instances before any subsequent optimizer comparison.

Formal proof production introduces a syntax and library-use demand. A lower Lean acceptance rate does not by itself show less mathematical knowledge. Preserve the informal probe as a separate diagnostic. Compare typed witness or local-operation tasks with complete formal proofs to locate the additional burden.

### Formal task and verifier contract

Each item binds its natural-language problem to a reviewed Lean statement, explicit domains, assumptions, imports, and allowed premises. Freeze the statement before receiving a model answer. The model cannot modify the target or add assumptions. A translator cannot silently repair a response or replace its claimed theorem with a more convenient proposition.

For acquisition and execution tasks, reuse the existing bounded witness parser and immutable obligations where they express the task faithfully. For complete proofs, the model must supply the proof body for the frozen theorem. Fixed proof skeletons receive local-action credit only; they do not establish autonomous proof construction.

Pin Lean and mathlib together. Accept a proof only when the kernel checks the unchanged target under the registered axiom policy. Reject admitted proofs and newly introduced axioms. Record allowed automation and library lemmas. A library theorem that supplies the required concept changes the interpretation from unaided retrieval to supported theorem solving.

Record acceptance, mathematical rejection, extraction failure, syntax failure, timeout, and verifier infrastructure failure separately. A timeout does not certify either a valid or invalid mathematical argument. Preserve complete responses and diagnostics. Fewer goals or a longer accepted tactic prefix is not a complete proof.

### Matched evaluation and next objective

First evaluate the unchanged early RLP policy and the ordered final policy on matched formal development tasks. Add the original policy as a diagnostic reference when feasible. Match premises, decoding, output limits, numerical precision, and attempt budgets. Do not select a favorable intermediate checkpoint after inspecting final-test outcomes.

Standalone evaluation generates without verifier feedback and checks the submitted object afterward. Lean-assisted evaluation allows feedback under a registered candidate and verifier-call budget. Report these endpoints separately. A larger translator, proof repairer, or stronger automation must be disclosed; its work cannot be assigned to the target model.

If the initial policy produces no informative complete-proof reward groups, test more supported local operations before terminal-only proof training. The earlier whole-proof pilot already exposes this failure mode. Count the entire development pool rather than selecting a solvable subset and calling it general readiness.

Once that baseline is measured, register a reward comparison with the same parent checkpoint, task-view multiset, and resource ceilings. Existing RLP information gain, verified correctness, and a hybrid objective are separate arms. Keep curriculum order fixed when testing the reward. Keep reward fixed when testing order. Require improvement on fresh correctness probes, not reward growth alone.

### Ownership and inference boundary

Manas owns the formal-to-informal task mapping, data splits, curriculum controls, evaluation, and run comparison. Anwesha owns the general interactive Lean verifier and its provenance. Abhinav owns the interactive optimizer work. The existing checker and TRL pilots can be reused within their documented scope; no broader interface readiness is inferred.

This design can establish validity under declared formal premises. It cannot alone establish faithful prose, wholly new internal knowledge, retention, or undergraduate competence. Those claims require their separate tests. The [Lean reference](https://lean-lang.org/doc/reference/latest/Tactic-Proofs/) supports the kernel-checking boundary; the [experimental protocol](experimental_protocol.md#verification-and-reward) supplies the project acceptance policy.
