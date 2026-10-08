# Agent instructions

## Scope and source of truth
- Read `README.md`, `docs/EXPERIMENTS.md`, and the relevant config before editing.
- Treat the README status table as the only project status record.
- Do not report a planned command, baseline, dataset, or result as implemented.
- Keep decisions in the decision log of `docs/EXPERIMENTS.md`; append a dated line instead of rewriting history.

## Environment
- Use uv: `uv sync` and `uv run pre-commit run --all-files`.
- Treat Lean and mathlib as a pinned pair. Record both revisions before any verifier result.
- Keep generated data, proof traces, checkpoints, and logs in `artifacts/`, outside Git.
- Run only small local checks. Request approval before substantial GPU spend or external changes.

## Research controls
- Keep train, curriculum-validation, and held-out evaluation theorems separate.
- Do not load evaluation proofs into training data, prompts, retrieval, or adaptive sampling.
- Record exact and near-duplicate checks before using a benchmark.
- Label each reward component as Lean-verified or heuristic. Never call subgoal reduction a proof.
- Count generated tokens, Lean calls, wall time, and GPU time from run records.
- Compare methods from the same starting checkpoint, data, and budget; preserve failed and null runs.

## Work and review
- Follow the team ownership listed in `docs/EXPERIMENTS.md`.
- Use the smallest check for the changed path. Lean tests belong in a marked integration group.
- Do not commit or push unless the user asks. Use the Git identity configured on this machine.
- When an AI commit is requested, include `Co-Authored-By` with the agent's model name and attribution address.
