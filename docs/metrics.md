# Metrics

## Units and denominators

Use a fixed theorem set for every evaluation comparison. Record its size, successful theorem IDs, number of samples per theorem, checkpoint ID, and Lean revision. Keep training interactions separate from evaluation interactions. Record the raw event rows that produce each plot or table.

| Metric | Definition | Required denominator or axis |
| --- | --- | --- |
| Verified solve rate | Distinct theorems with at least one Lean-accepted proof divided by evaluated theorems | Theorem count |
| Pass@1 | Fraction solved by the first sampled candidate | Theorem count |
| Pass@k | Fraction solved by at least one of the first `k` candidates | Theorem count and `k` |
| Lean-call efficiency | Change in held-out solve rate per cumulative physical Lean execution | Starting evaluation and call count |
| Token efficiency | Change in held-out solve rate per cumulative generated policy token | Starting evaluation and token count |
| GPU efficiency | Change in held-out solve rate per measured GPU hour | Starting evaluation and GPU time |
| Learning-curve area | Numerical area of held-out solve rate against generated training tokens; companion area against physical Lean calls | Fixed shared range and integration rule for each axis |
| Outcome reward variation | Groups containing at least one success and one failure divided by evaluated groups | Group count and group size |
| Process reward variation | Groups with unequal registered process returns divided by evaluated groups | Group count, group size, and return definition |
| Difficulty analysis | Solve rate and reward variance by frozen difficulty bin and checkpoint | Theorem and rollout counts per bin |
| Questions needed | Unique training theorem IDs encountered before a registered validation target | Unique IDs and repeated presentations, separately |
| Rollouts needed | Generated training episodes before the registered target | Complete, failed, and interrupted episodes |
| Previously unsolved theorems | Theorem IDs solved after RL with no accepted baseline candidate at the fixed budget | Baseline and final candidate counts per theorem |

For the interactive study, a candidate is a complete bounded episode. Report the observed first-k success fraction with its exact denominator. Do not mix it with an estimator of population pass@k without identifying the estimator. Greedy success and sampled pass@1 are separate quantities.

Register the validation target before the main comparison. When a run never reaches it, report the outcome as censored at its resource cap. Count sampling diagnostics and failed episodes in training cost. Report component-lemma training separately when decomposition adds examples.

The primary efficiency axis is generated training tokens. Supply companion curves for physical Lean calls and measured GPU time. Keep observed proof quality separate from prose coherence: a completed accepted target establishes formal proof validity under the registered environment. Review reasoning explanations separately if they are generated.

Previously unsolved theorem counts describe finite baseline sampling. They do not prove that initial success probability was zero or that the model acquired previously absent knowledge.

Report the no-RL solve rate beside each improvement. Do not compare a method's curve beyond the shortest shared budget range. Report attempted calls, physical calls, and cache hits separately. A cached call still counts as an interaction request.

## Acquisition and proof-stage accounting

For future acquisition comparisons, plot capability against cumulative generated training tokens. Mark acquisition and proof-RL boundaries. Keep evaluation tokens separate. The current RLP runner's `generated_tokens` combines training and evaluation; reconstruct training tokens from training rollout `completion_ids` before plotting. Its before/after evaluation does not supply an intermediate capability curve.

Report distinct corpus tokens under the pinned tokenizer separately from repeated context exposures. Also record sampled prediction targets, unique source blocks encountered, prompt tokens, generated reasoning tokens, and tokens included in the policy loss. A corpus block is not a training question. Do not infer corpus coverage from optimizer steps. TRL's [`num_tokens`](https://huggingface.co/docs/trl/v0.24.0/grpo_trainer) includes prompts and completions; it is not the generated-training-token axis. Report prediction-scoring forward calls and their processed tokens separately when instrumented; do not infer total compute from generated tokens alone.

For the next run, preregister cumulative token milestones for curriculum-validation evaluations and checkpoint saving. Set their numerical values in the resolved config before launch. Preserve the initial and final held-out evaluations, and never use held-out outcomes to select assistance or checkpoints. Use identical evaluation prompts, decoding, and attempt budgets across checkpoints. Record the achieved token count when a complete rollout group crosses a milestone.

Measure definition use and theorem conditions, computational correctness, and verified proof completion separately. Add primality, composite witnesses, factorization, prime divisibility, and modular-arithmetic tasks with independently checked answers. Report parsing failures and truncations separately from incorrect parsed answers. Compare mathematical scores before and after Lean proof training to detect regressions. Lean-valid outputs and accepted completed target proofs require separate denominators.

An increase in prediction reward does not establish independent mathematical improvement. An increase in accepted proofs after Lean training can include better syntax or premise selection. Test the acquisition contribution by comparing acquisition-plus-proof-RL with direct proof-RL at a matched total generated-token budget. Also compare at matched proof-stage budget when feasible; disclose its extra acquisition cost. Prerequisite versus random ordering must share the corpus, assistance, optimizer, and resource limits. These matched comparisons remain planned.

The primitive-first continuation implements separate training and evaluation token counters, source-token counts, prompt and scorer-input counts, encountered-context counts, and token-milestone validation. `capability_curve.jsonl` links each point to its raw evaluation file and parent training cost. Its answer-format prefix is external assistance, held fixed across continuation checkpoints. Do not compare these scores directly with earlier unassisted output. The training-phase wall timer includes callback evaluation and checkpoint uploads; it is not active GPU computation time.

## Group-relative signal diagnostic

For independent Bernoulli outcomes with theorem-family success probability `p` and group size `G`, the chance of a mixed group is `Y_G(p) = 1 - p^G - (1-p)^G`. This is a diagnostic model, not an observed result. Estimate `p` from a specified window of attempts and report its denominator. Log the observed mixed-group fraction as a separate value. Correlated samples can make the formula disagree with observations.

## Run record requirements

Each run manifest should store the Git commit, complete resolved config, seed, checkpoint and tokenizer identifiers, GPU/device details, Lean and mathlib revisions, split hashes, and source licenses. Event records should store theorem ID, split, sampling reason, reward provenance, generation token count, verifier request count, physical execution count, cache-hit count, and timestamps. Save raw rows and the script or notebook used for every reported number.
