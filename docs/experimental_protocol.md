# Experimental protocol

## Dataset and checkpoint control

Create immutable theorem IDs from the Lean statement, imports, namespace context, and source revision. Keep train, curriculum validation, and held-out evaluation manifests separate. Split by theorem family or source where possible, then report exact statement duplicates and normalized near duplicates across splits. Inspect proof-text overlap separately. Benchmark proof text must never enter training, prompting, retrieval, or curriculum statistics.

Use one pinned Lean toolchain and mathlib revision per comparison. Pin the model checkpoint, tokenizer, generation settings, seed, theorem manifest, and RL update budget. For readiness, vary the checkpoint along one pretraining trajectory. For reward and curriculum comparisons, hold the starting checkpoint fixed.

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
- Compare the selected RL method with verifier-filtered expert iteration at matched token and verifier budgets.
- Report all seeds, failed runs, and selection rules. Predeclare the primary metric before inspecting held-out results.

Use a marked integration group for tests that invoke Lean. The fast test group should cover splits, reward provenance, cache keys, counters, resume, and one tiny update when those modules exist.
