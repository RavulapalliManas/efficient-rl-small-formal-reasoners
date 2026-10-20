# Agent instructions

## Scope and source of truth
- Read `README.md`, `docs/EXPERIMENTS.md`, and the relevant config before editing.
- Find code through `scripts/README.md` and configs through `configs/README.md`. When you add, rename, or move a script or config, update its row there.
- Give each new experiment a row in the register of `docs/EXPERIMENTS.md`, and fill its Results and Report cells when it has them.
- Treat the README status table as the only project status record.
- Do not report a planned command, baseline, dataset, or result as implemented.
- Keep decisions in the decision log of `docs/EXPERIMENTS.md`; append a dated line instead of rewriting history.

## Document requests
- Follow section 9 of `~/.claude/CLAUDE.md` for shared document, catalogue, and verification rules.
- The main report source is `docs/tex/project_report.tex`.
- When asked to make, expand, or update a project document, edit that TeX source unless another target is explicit.
- Read the relevant report section and its source artifacts before drafting. Preserve numbers, citations, and uncertainty.
- Reuse `README.md` for navigation and status, and `docs/EXPERIMENTS.md` for experiment notes and dated decisions.

## Experiment records
- Follow the catalogue and record contract in `docs/EXPERIMENTS.md` before planning, launching, or summarizing a study.
- Reuse stable experiment IDs. Give each arm, seed, retry, and rescore a distinct run ID and output directory.
- Register the question, comparison, owner, config, and evidence location before launch. Mark unknown historical fields explicitly.
- Keep resolved settings, raw outcomes, recounts, and costs in the run artifacts. Preserve failed and null runs.
- Update README status from evidence. Append decisions and corrections to `docs/EXPERIMENTS.md`; never overwrite historical outcomes.
- Default to no new Markdown files, including inside `artifacts/notes/`. Use the existing catalogue or run artifacts.
- Keep scientific narrative in the TeX report and exploit details in `docs/REWARD_HACKING.md`.
- Treat dated notes and `docs/archive/HANDOFF.md` as historical sources. Do not maintain competing status or plans there.

## Environment
- Use uv: `uv sync`. Use the file-scoped check below; run all hooks when the changed scope requires it.
- Treat Lean and mathlib as a pinned pair. Record both revisions before any verifier result.
- Keep data, proof traces, checkpoints, and logs in remote run directories and durable storage, outside Git.
- Read `.private/infrastructure.md` and the existing `.private/ops/` scripts for access and launch procedures; never guess infrastructure identifiers.
- Run small local checks only. Each paid GPU launch needs Manas's approval with its cost; a budget ceiling is not launch approval.
- Keep one writer per output. Use the established managed remote runner with memory and resource caps.
- Keep private infrastructure identifiers out of tracked files and user-facing text.

## Research controls
- Keep training, DEV, curriculum-validation, and held-out test theorems separate. Choose checkpoints and task mixes on DEV only.
- Do not load evaluation proofs into training data, prompts, retrieval, or adaptive sampling.
- Record exact and near-duplicate checks before using a benchmark.
- Label each reward component as Lean-verified or heuristic. Never call subgoal reduction a proof.
- Count generated tokens, Lean calls, wall time, and GPU time from run records.
- Compare methods from the same starting checkpoint, data, and budget; preserve failed and null runs.

## Work and review
- Follow the team ownership listed in `docs/EXPERIMENTS.md`.
- Use the smallest check for the changed path. Lean tests belong in a marked integration group.
- For Markdown edits, run `uv run pre-commit run --files <changed-files>`.
- For TeX edits, use the established report build. Report unresolved references, build failures, or a stale PDF.
- Do not commit or push unless the user asks. Use the Git identity configured on this machine.
- When an AI commit is requested, include `Co-Authored-By` with the agent's model name and attribution address.
