# Team workstreams

Each workstream owns its paths and its acceptance checks. Agree on the trajectory and run-record schemas before parallel implementation. Merge through the interfaces in [the research plan](research_plan.md).

## A. Representation and training

Own `src/formal_rl/models/`, `data/`, `training/`, and `configs/model/`, `data/`, `pretrain/`, and `midtrain/`. Select the public or custom model, construct one shared checkpoint trajectory, prepare licensed formal traces, and implement supervised fine-tuning and resume. Deliver checkpoint manifests with tokenizer, data, token count, and configuration. A readiness probe must load each checkpoint through one shared interface.

## B. Lean and reward

Own `src/formal_rl/lean/`, `rewards/`, and `configs/reward/`. Pin Lean and mathlib, implement reset and step, cache by theorem context, tactic sequence, and Lean revision, and record verifier statuses. Deliver terminal reward and prefix diagnostics with verified versus heuristic provenance. Tiny Lean examples must pass in the marked integration group before RL uses the interface.

## C. RL, curriculum, and evaluation

Own `src/formal_rl/rl/`, `curricula/`, `eval/`, and their config directories. Implement the first small policy update, uniform and later adaptive sampling, the matched expert-iteration baseline, and cost accounting. Deliver a held-out solve-rate curve against Lean calls with raw event rows. The trainer must accept samplers and rewards through the agreed interfaces.

Coordinate changes to `README.md`, `docs/experimental_protocol.md`, `docs/metrics.md`, and `docs/decisions.md` through one reviewer. The README status table has one writer per merge. Put artifacts in run-specific paths to avoid concurrent writes.
