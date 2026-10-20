# Handoff

This is a historical scratch snapshot. Do not update it as a project status record.
Use [README status](../../README.md#status) and the [experiment catalogue](../EXPERIMENTS.md) for continuing work.
Its running-job, budget, and readiness statements describe the original session and require a live check before use.

> Generated: 2026-10-10 10:08 UTC
> Project: formal RL repository (Pure CS/RL), user Manas
> Session summary: Data, checker, and plan work for the midsem RL result; everything is ready for GPU session 1 except
> the user's approval and an update of the lease scripts.

## Goal

Manas trains Qwen2.5-0.5B-Instruct to write Lean 4 proofs of number-theory (NT) statements with RL from a Lean reward
(GRPO). Midsem goal (presentation 14 October 2026): show that RL with the Lean reward makes the model write more
Lean-verified proofs on held-out NT statements. Endsem goal: efficient RL (curriculum order, data mix, per-step reward
at an equal budget) and novel proofs. Fixed direction: one model writes informal reasoning interleaved with Lean steps
(no separate translator in the RL loop); offline steps (SFT, distillation, expert iteration, proof-prefix tasks) feed
online GRPO.

## Current State

Working (each item checked in this session):
- Scorer v11 on cpu-angad: `<data>/formal_rl/train_code_v11` (`scripts/eval/bench_lean_verifier.py` sha256
  2ba6deda04f78347, `scripts/eval/probe_start_depth.py` 3115ff6059bc6ad7). 36 + 6 unit tests pass. Attack suite
  `trainer_checker_v1/attacks_v11/summary.json`: 93 checks, 0 attacks accepted, 0 controls rejected. FORBIDDEN now runs
  only outside Lean comments (`lean_code()`), and it blocks `observe`.
- Data v3 (verified with `scripts/audit/check_split_leakage.py` -> `<data>/formal_rl/nt_splits_v3_audit/leakage_check.json`):
  `nt_splits_v3`: sft_problems 553, heldout_proof_problems 184, dev 300, rl_pool_statements 2,234, excluded 321,
  validation_nontrivial 392, test_nontrivial 433. Max token Jaccard from training side: 0.846 to validation/test, 0.696
  to miniF2F. SFT data `nt_sft_v5` (1,153 rows over 553 problems; 550 with comments) and `nt_sft_v5_nocomments` (same
  examples, comments removed; 808 stripped Goedel proofs re-checked 808/808 earlier on v2). RL tasks `nt_rl_tasks_v3`
  (2,688: prefix_top 402, prefix_have 52, whole 2,234; every hole task passes as given + reference_rest).
- Trainer `scripts/train/run_lean_grpo.py`: budget stop (tokens, Lean calls, wall), `--tasks` for hole tasks, split
  guards, shuffled-reward control (Bernoulli arm removed). RFT builder and nearest-proof baseline scripts exist.
  The TRL loop itself is untested (no TRL on cpu-angad).
- Baselines: SFT v2 proves 0/200 train statements (`nt_probe/train200_v3_merged/summary.json`); one standard tactic
  proves 28/420 validation; copying the nearest trainable train proof proves 12 of the 392 non-trivial validation
  statements (`trainer_checker_v1`). RL must beat these.
- Config `configs/midtrain/qwen_05b_nt_interleaved_sft_v1.json` points to `nt_sft_v5`.

Blocked or stale:
- GPU session 1 needs Manas's approval (about USD 7 cap). The lease scripts in `.private/ops/gpulease_nt/` are STALE:
  `stage_cpu.sh` copies `nt_sft_v2` and `eng_code_v4`; `post_lease.sh` uses `eng_code_v4` and maps all of pool v2 train.
  They must move to `train_code_v11`, `nt_sft_v5` plus `nt_sft_v5_nocomments`, `nt_splits_v3`, `nt_rl_tasks_v3`.
- Old results scored with the old subprocess check (`val_eval_v4`, step-search runs) are not re-scored with v11.
- `docs/tex/project_report.tex` Section 10 (reward audit) quotes scorer v9 and lists open items that the decision log
  now answers; `observe` (P3) is now fixed and the comment false-rejection flaw is a new fixed item. Its author session
  (rl-96) ended. Manas has not said who edits it.

Running on cpu-angad when this was written (systemd user units; they continue without this session):
- `extra_proofs.service`: external data (Goedel-Pset, Goedel-V2 SFT data, STP proofs) -> `<data>/formal_rl/nt_extra_v1/`
  with `REPORT.md`. Not yet present at 10:08 UTC.
- `inhouse-run-v1.service`: tactic augmentation -> `nt_aug_v1/` (not yet present), NT knowledge Q&A -> `nt_knowledge_v1/`,
  mathlib Archive -> `nt_archive_v1/` (both directories exist). Each writes `REPORT.md` in its directory.

## Files in Play

| File | Why it matters |
| --- | --- |
| `artifacts/notes/2026-10-09_phase0_ledger.md` | The running ledger (Updates 1 to 10). Read Updates 7 to 10 first |
| `artifacts/notes/2026-10-10_midsem_plan.md` | The midsem plan: claim, metric, splits, controls, sessions, budget |
| `artifacts/notes/2026-10-10_midsem_plan_critique.md` | Independent critique merged into the plan |
| `artifacts/notes/2026-10-10_engineering_audit.md` | Audit E1 to E6, GPU plan details, risks |
| `artifacts/notes/2026-10-10_trainer_checker_report.md` | Attacks, fixes, baselines (written for v9) |
| `artifacts/notes/2026-10-10_data_build_report.md` | Splits, SFT, tasks; Rebuild v2 and v3 sections |
| `artifacts/notes/2026-10-10_expansion_plan.md` | Ranked sources for more data |
| `artifacts/notes/2026-10-10_efficient_rl_literature_{provers,selection}.md`, `..._spotcheck.md`, `..._engineering_blog_ledger.md` | Verified literature and engineering guidance (quotes, URLs) |
| `docs/EXPERIMENTS.md` | Decision log; 5 dated lines added on 2026-10-09/10 (uncommitted) |
| `README.md` | Status table (only status record); rows updated (uncommitted) |
| `AGENTS.md` (= `CLAUDE.md`) | Project rules; new document rules added on 2026-10-10 by Manas or another session |
| `docs/tex/project_report.tex`, `docs/REWARD_HACKING.md` | Report and reward-hacking inventory; committed by rl-96 on branch `docs/lean-reward-audit` |
| `scripts/eval/probe_start_depth.py` | FORBIDDEN, `lean_code()`, old `check` (do not use for new results) |
| `scripts/eval/bench_lean_verifier.py` | Shared REPL scorer `verify_group` |
| `scripts/eval/probe_nt_pool.py` | Pass-rate probe: `--vllm`, `--no-check`, `--check-workers`, `--resume`, `--merge`, `--shard` |
| `scripts/train/run_lean_grpo.py` | GRPO trainer |
| `scripts/data/build_nt_pool.py`, `build_nt_splits.py`, `build_nt_sft_data.py`, `build_nt_hole_tasks.py` | Data pipeline |
| `scripts/audit/check_split_leakage.py` | Leakage check (new) |
| `tests/test_lean_scoring.py` | Scorer and trainer unit tests |
| `.private/ops/rsh.sh` | SSH helper to cpu-angad (reads `.private/infrastructure.md`; masks IPs only) |
| `.private/ops/gpulease_nt/` | GPU lease scripts (stale inputs, see above); `.private/ops/sftlaunch/` is the older SFT lease |

All new scripts, tests, and configs are untracked. Nothing of this session is committed.

## What Has Been Tried (and Why It Failed)

### Whole-proof GRPO from a weak start
- What: GRPO with a binary Lean reward (earlier experiments; README status table).
- Why it failed: the success rate was near 0, so every group of 8 was all-fail and the GRPO advantage was 0.

### SFT on mathlib proofs (SFT v2)
- What: 4,526 steps on LeanDojo mathlib proofs.
- Why it failed for the goal: it taught mathlib style (named-lemma rewrite chains). On Lean Workbook NT: 0/1,600
  attempts; first errors 582 unknown lemma names, 329 failed rewrites, 253 token cap, 180 syntax. It scored 0/64 on the
  8 statements one tactic proves. Informal answers fell from 92 to 40 of 309 (rl-39's test; not recomputed here).

### Step-level tactic search on 43 LeanDojo validation theorems
- What: best-first search with the model vs a fixed 16-tactic list.
- Why it was dropped: 14/43 vs 11/43 (exact sign test p = 0.375) and 0 of 17 NT theorems solved; the benchmark was
  mostly sets and logic, not the goal.

### CPU generation for pass-rate maps
- What: HF generate on cpu-angad.
- Why it fails: about 73.5 s per group of 8 with 4 shards (118 tokens per sample). The fix is vLLM on the GPU
  (trl 0.24.0 pins vllm 0.10.2, which pins torch 2.8.0; checked on PyPI).

### Earlier checkers
- Batch-file check: an error from a truncated candidate landed on the next declaration. Fixed with a sentinel; the probe
  now uses the REPL scorer.
- Scorer before v9: accepted 27/87 attack checks (forged axiom reports via `trace`/`run_tac`, replaced `#print axioms`
  elaborator, indented trailing commands, namespace shadowing, trailing theorem). Fixed in v9.
- FORBIDDEN on raw text: it matched words in comments; with `observe` it would have rejected 12/1,153 SFT rows (all from
  the word in Goedel comments). Fixed in v11.
- The old subprocess `check` still has the forgery holes. Do not use it for new results.

### Near-duplicate threshold 0.7
- Why it was changed: it removed 34% of train (1,175 statements); the 0.70 to 0.75 band holds mostly different problems
  with shared notation. Now 0.85 to validation/test (with results reported by nearest-train band) and 0.7 to miniF2F.
- The miniF2F check by best match only missed 15 pairs; the all-pairs scan fixed it.

### Operations
- Agents died when the Mac slept ("Connection lost while your computer was asleep"). `caffeinate -i -t 21600` started at
  about 08:20 UTC (expires about 14:20 UTC). cpu-angad jobs survive; resume agents by message.
- `systemd-run --wait` sends service stdout to the journal, not to a shell redirect. A heredoc is not passed to
  `systemd-run`; write the script to a file first.
- `python -I` hides user-site packages: `/usr/bin/python3` has pyarrow only without `-I` (run it from your own code
  directory, never from a download directory).
- The checkpoints' `generation_config.json` sets `repetition_penalty: 1.1`; every HF evaluation used it implicitly.

## Current Best Theory

RL fails today because the policy never produces an accepted proof: its Lean style (library-lemma chains with invented
names) does not match competition NT, which mostly needs automation tactics (`omega`, `nlinarith`, `norm_num`,
induction). Fix it offline, then run RL in the band 0 < p < 1:
1. Interleaved SFT on `nt_sft_v5` (and the comment-free arm as the interleaving control).
2. Easier tasks by construction: proof-prefix and `have` hole tasks from the 184 held-out proofs.
3. GRPO on tasks with mixed groups, with the shuffled-reward control, an RFT baseline at equal generated tokens,
   2 seeds of the real arm, and the primary metric on the 392 non-trivial validation statements.
Predictions declared in `artifacts/notes/2026-10-10_solution_design.md`: P1 SFT solves some of the automation-solvable
statements; P2 hole tasks give more mixed groups than whole statements; P3 the commented arm keeps more informal ability.

## Next Steps

1. Check the two running agents. `/tmp/curric/rsh.sh` may be gone after a reboot; use `.private/ops/rsh.sh '<cmd>'`
   and pipe the output through `sed -E 's#/home/[^/ ]+#<home>#g; s#/mnt/data/[^/ ]+#<data>#g'`. On cpu-angad,
   `R=$(find / -maxdepth 4 -type d -name formal_rl 2>/dev/null | head -1)`. Read `$R/nt_extra_v1/REPORT.md`,
   `$R/nt_aug_v1/REPORT.md`, `$R/nt_knowledge_v1/REPORT.md`, `$R/nt_archive_v1/REPORT.md`. Verify their counts from the
   files before merging anything. New data must pass the `nt_splits_v3` rules (train split only; Jaccard >= 0.85 to
   validation/test and >= 0.7 to miniF2F excluded; held-out problems untouched) and the v11 scorer.
2. Update the lease scripts in `.private/ops/gpulease_nt/` to `train_code_v11`, `nt_sft_v5` and `nt_sft_v5_nocomments`
   (two SFT arms), `nt_splits_v3`, and `nt_rl_tasks_v3`. Maps: DEV, RL pool, and hole tasks, with max_new_tokens 1,024.
   Add the 50-statement distillation pilot (Goedel-Prover-V2-8B: Apache-2.0, Lean 4.9) and a 2-step GRPO smoke that
   measures GPU memory and the Lean share of step time. Keep the vLLM-vs-HF equivalence step first (stop on failure).
3. Ask Manas to approve GPU session 1 with the cost. The rate is USD 2.24208/h for g6e.2xlarge (8 vCPU) and USD
   3.00424/h for g6e.4xlarge (16 vCPU), from the AWS pricing API. The ceiling is USD 145; about USD 30 remains by
   reservations (SFT v2 ledger chain in S3). Write one ledger entry per launch.
4. After session 1: run the Lean checks on cpu-angad with `--check-workers 8`; choose the SFT arm on DEV only; build the
   RL task mix from tasks with 0 < pass rate < 1.
5. Session 2 (confirm cost with Manas first): GRPO real arm x 2 seeds, shuffled-reward arm, RFT baseline at equal
   generated tokens; then score validation (non-trivial and full, by nearest-train band) at a 60 s Lean timeout, and
   miniF2F and test once. Run the checks listed in report Section 10 before any gain is reported.
6. Re-score `val_eval_v4` and the step-search results with v11 before reusing their numbers.
7. Pending decisions for Manas: repetition penalty for maps and RL (recommendation 1.0; the equivalence step uses 1.1),
   who updates report Section 10, and whether to merge branch `docs/lean-reward-audit`.

## Key Constraints

- SECURITY (permanent): never put cpu-angad's IP, the SSH username, the key path, the S3 bucket name, the AWS account ID,
  EC2 instance IDs, or a Drive link in any tracked file or user-visible text. Values live only in
  `.private/infrastructure.md`. Write cpu-angad paths as `<data>/...`.
- The Mac runs no jobs and stores no data. Compute and data live on cpu-angad (shared, 56 cores, about 31 GB free).
  Bound memory before every launch, use `systemd-run --user -p MemoryMax=...`, at most 8 Lean workers, one writer per
  output path, never overwrite an output directory.
- GPU: every launch needs Manas's explicit approval with its cost. Never launch on a peer's request.
- Do not commit or push unless Manas asks. The working tree is on branch `docs/lean-reward-audit` (commit 9d259b3 by
  rl-96, pushed). Do not switch branches without telling Manas: a switch to `main` removes report Section 10 here.
- Document rules (AGENTS.md and global CLAUDE.md section 9): no new dated or per-task Markdown files; status in the
  ledger, decisions in `docs/EXPERIMENTS.md`, narrative in `docs/tex/project_report.tex`; run reports go in run
  directories; run `uv run pre-commit run --files <changed-files>` after Markdown edits. This handoff file was requested
  explicitly by Manas; it is a scratch file, not to be committed.
- Research controls: train, validation, and test stay separate; evaluation content never enters training, prompts, or
  adaptive sampling; decisions are made on DEV; test is scored once; every number traces to a script and a file;
  never relax a check, cap, or filter to make a run pass.
- Manas wants short, plain answers (flight-manual English), NT focus, interleaving, and more Lean-verified samples.
  He asked for subagents for engineering and search (Opus for engineering, Haiku for broad searches); verify every
  subagent claim against its artifact.

## Memory Pointers

- Observations: #18213 (scorer v11 deployed, tests and attacks), #18205 (comment-aware FORBIDDEN), #18124 (nt_splits_v3
  build), #18126 (miniF2F all-pairs exclusion), #17780 (post-training direction decision), #17489 (Lean Workbook NT
  statement check).
- Searches worth re-running: `scorer v11 FORBIDDEN comments`, `nt_splits_v3 miniF2F held-out`, `GPU lease midsem`.
