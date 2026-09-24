# Research decisions

Append a dated entry when the team makes a lasting research or tooling choice. Record the choice, reason, alternatives considered, affected files, and evidence. Do not rewrite old entries; append a correction that links to the earlier entry.

## 2026-09-24: Documentation-first repository setup

- Decision: Set up local Git, installation metadata, the planned directory map, and the research protocol before experiment code.
- Reason: The requested handoff is a grounded starting point. No baseline or smoke run has been implemented.
- Alternatives: Full experiment scaffolding was deferred because it would imply interfaces and commands were tested.
- Evidence: The initial workspace was empty. The current status is in the README.
- Open choices: Repository license, GitHub visibility, Lean/mathlib revision, checkpoint, theorem source, and runtime dependencies.

## 2026-09-24: Lean version boundary

- Decision: Document Lean 4 as the target and require a pinned toolchain with mathlib before verification work.
- Reason: The [original MiniF2F checkout](https://github.com/openai/miniF2F) documents a Lean 3 installation. The [Lean 4 port](https://github.com/google-deepmind/miniF2F) is a separate source. The [original LeanDojo repository](https://github.com/lean-dojo/LeanDojo) marks itself deprecated.
- Alternatives: A Lean 3 environment and the original LeanDojo package remain out of scope for this setup.
- Evidence: Linked upstream project documentation, checked during repository setup.
