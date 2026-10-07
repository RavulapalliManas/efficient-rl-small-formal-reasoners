# Experimental protocol

## Dataset and checkpoint control

Create immutable theorem IDs from the Lean statement, imports, namespace context, and source revision. Keep train, curriculum validation, and held-out evaluation manifests separate. Split by theorem family or source where possible, then report exact statement duplicates and normalized near duplicates across splits. Inspect proof-text overlap separately. Benchmark proof text must never enter training, prompting, retrieval, or curriculum statistics.

Use one pinned Lean toolchain and mathlib revision per comparison. Pin the model checkpoint, tokenizer, generation settings, seed, theorem manifest, and RL update budget. Manas's main comparison fixes the Instruct checkpoint specified in [the research plan](research_plan.md). Readiness along a pretraining trajectory belongs to a separate study. For reward and curriculum comparisons, hold the starting checkpoint fixed.

## Interaction and budget accounting

Count each Lean execution request, including failed and cached requests. Record physical verifier executions and cache hits separately. Count generated policy tokens from tokenizer output, including failed rollouts. Record wall time and measured GPU time separately. Match both generated-token and physical Lean-call budgets for the RL versus expert-iteration comparison; report cache use and elapsed time. Use the same held-out evaluation schedule and report its costs separately from training.

Before a costly run, require a dry run that resolves the config, data manifest, checkpoint, device, Lean version, output path, and projected interaction cap. Save rollout records before optimizer updates when practical. Save model, optimizer, sampler, random-number-generator, and interaction-counter state for resume. Never overwrite a completed run.

## Verification and reward

Define success as Lean accepting the completed proof for the target statement under the pinned environment. A tactic prefix is verified only up to the state Lean reached. A reduced subgoal count is a heuristic measure of progress. A timeout, crash, parser error, or unavailable verifier is not a proof failure; log it as a separate status.

Every reward record must contain the component name, value, source diagnostic, verification status, and tactic span. Keep terminal and process components separate. For process reward, log the first failed tactic and the longest accepted prefix. Do not infer logical progress from goal count alone.

## Comparisons

- Evaluate every starting checkpoint before RL.
- Use the same short probe budget for readiness checkpoints.
- Compare terminal-only and process reward with a fixed sampler.
- Compare uniform, fixed difficulty, and adaptive sampling with a fixed reward definition.
- Keep verifier-filtered expert iteration outside the current RL-only comparison. If a later study includes it, match token and verifier budgets.
- Report all seeds, failed runs, and selection rules. Predeclare the primary metric before inspecting held-out results.

Use a marked integration group for tests that invoke Lean. The fast test group should cover splits, reward provenance, cache keys, counters, resume, and one tiny update when those modules exist.

## Registered comparison, 2026-10-01

The current deliverable is a plan. The environment, policy optimizer, live L40S allocation, and spending cap are not supplied. No launch or benchmark readiness follows from documentation.

Use the unchanged Instruct policy as the reference. Cross terminal versus terminal-plus-tactic reward with uniform versus prerequisite-aware theorem sampling. All arms use the same interactive observations, allowed premises, reset rules, action limits, automation, and terminal checker. Compiler feedback remains visible in terminal-reward arms. Do not substitute whole-proof generation for the terminal-reward arm.

Define a candidate as a complete bounded episode from the original theorem state. An action is a tactic. Lean supplies the next observation. Success requires a completed proof of the unchanged target, no admitted proof or added axiom, and the registered axiom policy. A syntax failure, invalid tactic, or repeated state must follow the same stopping or recovery rule in all arms. Freeze this rule before the baseline.

Anwesha supplies the reward components and their verifier provenance. Manas registers their weights and credit assignment before the comparison. Local validity means successful execution under the reached context; it does not establish useful progress. Inspect unchanged states, cycles, and unfinished valid prefixes. Do not provide unrestricted positive reward for longer valid prefixes. Freeze weights and penalties using training and curriculum validation only.

Abhinav supplies GRPO and multi-turn optimization. Group episodes by initial theorem and premise context. Optimize generated policy actions, with environment observations excluded from loss. Record zero-variance groups and the implementation's numerical treatment. Do not compare token advantages from unrelated proof states as if they were candidates for one prompt.

Use the same trainable-parameter choice, optimizer settings, seeds, decoding, and checkpoint schedule across main arms. Full-parameter and adapter-based RL are separate policy choices; neither is selected by this plan. Register them once Abhinav supplies the optimizer. No supervised objective is permitted in the main arms.

Set explicit caps for generated training tokens, physical Lean calls, episode actions, prompt length, generation length, elapsed time, and GPU time before launching. Stop when any resource cap is reached. Record partial episodes and discarded groups. Charge curriculum measurements to training. If an arm finishes earlier, compare curves within the shared realized resource range. Do not assume simultaneous equality of every consumed resource.

Use training or curriculum validation for intermediate selection and stopping. Evaluate held-out data at the initial and predeclared final checkpoints. Repeat seeds according to a registered schedule. Record all runs, including failures. Report paired theorem outcomes and uncertainty; a confidence interval describes these samples, not unknown pretraining exposure.

The pilot Nat export retains its earlier environment. The main training pool and ProofNet-Verified need a compatible pinned environment before integration. Freeze the benchmark revision and theorem manifests before generating evaluation attempts. Preserve all supplied benchmark proofs outside training, retrieval, and curriculum computation. Do not inspect these proofs to design component lemmas.

The main curriculum changes theorem selection only. Premise unlocking and decomposition receive separate ablations. Evaluate all premise conditions with the same library. When imports or automation expose locked lemmas, label the experiment as unenforced premise prompting or defer it until enforcement exists. Newly introduced axioms are outside the planned intervention.
