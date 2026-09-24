# Research plan

## Goal and scope

This is a hypothesis-testing project. The target is a small formal reasoner on one RTX 4090. The mid-semester result should compare verified learning efficiency across policy readiness, reward granularity, and theorem sampling. Later process reward models and other environments remain extensions.

Model a tactic interaction as a Markov decision process when the observation contains the Lean proof state needed for the next action. A policy emits a tactic or proof step. Lean executes it and returns an observation, status, diagnostics, and completion flag. Hidden context, timeout, and nondeterministic tool behavior may break the practical Markov assumption; record them rather than assuming them away.

## Order of work

1. Select a Lean 4 toolchain, mathlib revision, small theorem source, and public checkpoint. Record licenses and revisions.
2. Build the tiny vertical slice in the README. Establish a no-RL and uniform terminal-reward baseline first.
3. Run readiness probes from checkpoints on one shared representation trajectory.
4. Compare terminal and process reward at the same checkpoint and interaction budget.
5. Compare uniform, fixed, and adaptive theorem sampling.
6. Compare the strongest RL method with verifier-filtered expert iteration under matched generated-token and Lean-call budgets.

Each later study depends on a working verifier, split manifest, cost counters, and checkpoint resume. The first milestone should therefore test those boundaries before adding algorithms.

## Planned interfaces

| Interface | Input | Output | Boundary |
| --- | --- | --- | --- |
| `LeanEnvironment` | Theorem ID, tactic or proof step | State, status, diagnostics, completion | No model logic |
| `RewardProvider` | Trajectory and Lean diagnostics | Reward components, values, provenance, verified/heuristic label | No hidden shaping |
| `CurriculumSampler` | Theorem statistics and policy checkpoint | Distribution and selection reason | No held-out examples |
| `RLTrainer` | Rollouts, reward records, config | Update and resumable state | No sampler or reward-specific branches |
| `ExperimentTracker` | Config, revisions, events, costs | Local JSONL/CSV records and run manifest | No inferred counters |

Reserve source paths under `src/formal_rl/` for these owners. The directories contain no implementation yet. Use one canonical trajectory and run-record schema when code begins; choose its fields with the three owners before building adapters.

## Main inference risks

- Checkpoint readiness can reflect changed pretraining data or token exposure. Use checkpoints from one trajectory and record the token count.
- Process reward can reflect a heuristic instead of formal progress. Report verified feedback separately from shaping and ablate each component.
- Adaptive sampling can overfit curriculum validation. Freeze held-out theorem access until final evaluation.
- A method can improve solve rate by spending more verifier calls or tokens. Match budgets and show cost curves.

The first result that would challenge the project framing is a flat RL gain across readiness checkpoints after budgets and theorem sets are matched. Record it as a result, not a failed launch.
