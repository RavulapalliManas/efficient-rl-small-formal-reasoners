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
| Learning-curve area | Numerical area of held-out solve rate against cumulative physical Lean calls | Fixed call range and integration rule |
| Non-degenerate groups | Groups containing at least one success and one failure divided by evaluated groups | Group count and group size |
| Difficulty analysis | Solve rate and reward variance by frozen difficulty bin and checkpoint | Theorem and rollout counts per bin |

Report the no-RL solve rate beside each improvement. Do not compare a method's curve beyond the shortest shared budget range. Report attempted calls, physical calls, and cache hits separately. A cached call still counts as an interaction request.

## Group-relative signal diagnostic

For independent Bernoulli outcomes with theorem-family success probability `p` and group size `G`, the chance of a mixed group is `Y_G(p) = 1 - p^G - (1-p)^G`. This is a diagnostic model, not an observed result. Estimate `p` from a specified window of attempts and report its denominator. Log the observed mixed-group fraction as a separate value. Correlated samples can make the formula disagree with observations.

## Run record requirements

Each run manifest should store the Git commit, complete resolved config, seed, checkpoint and tokenizer identifiers, GPU/device details, Lean and mathlib revisions, split hashes, and source licenses. Event records should store theorem ID, split, sampling reason, reward provenance, generation token count, verifier request count, physical execution count, cache-hit count, and timestamps. Save raw rows and the script or notebook used for every reported number.
