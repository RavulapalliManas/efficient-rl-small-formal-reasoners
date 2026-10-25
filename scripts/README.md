# Scripts

Run commands from the repository root. Start with the workflow that matches your task.

<a id="start-here"></a>

| Task | Guide |
| --- | --- |
| Reproduce the recorded study | [Main-study reproduction](#main-study-reproduction) |
| Build a new dataset | [Dataset recreation](#dataset-recreation) |
| Follow a proof attempt | [Proof execution](#proof-execution) |
| Change the code | [Implementation map](#implementation-map) |
| Install dependencies or run checks | [Runtime and development](#runtime-and-development) |
| Find a script | [File index](#file-index) |

Use the [config guide](../configs/README.md) to match settings to an experiment.

## Primary workflow

The 18–19 October study (EXP-026 to EXP-028) follows this workflow:

| Step | Entry point | What it does |
| --- | --- | --- |
| Fetch data | [Data handoff](../docs/EXPERIMENT_HISTORY.md#6-step-by-step-fetch-and-verify-the-data) | Fetch and verify the archived inputs |
| Rebuild a new dataset | [Dataset recreation](#dataset-recreation) | Fetch pinned public sources, assemble a corpus, verify a new partition and create orders |
| Train SFT | [train/run_sft.py](train/run_sft.py) | Learn proof actions from supervised examples |
| Train RL | [train/run_tactic_rl.py](train/run_tactic_rl.py) | Sample proof attempts, check actions in Lean and update the model with REINFORCE |
| Evaluate | [train/run_tactic_rl.py](train/run_tactic_rl.py) | Use `--evaluation-names` for DEV. Use `--assessment-only` to assess tasks without updates; its default split is TRAIN |
| Draw report figures | [report/make_report_figures.py](report/make_report_figures.py) | Plot recorded counts for the report |

The figure script rebuilds 12 plots from recorded counts. The other `fig_oct21_*` PDFs retain their archived plotted data.
Their generators and recount inputs remain private. To rebuild the public plots and standardize labels in the archived vector figures, run:

```bash
uv run --with matplotlib --with pymupdf python scripts/report/make_report_figures.py --standardize-archived-labels
```

The optional label pass changes titles and display aliases; it preserves plotted geometry and stores original titles in PDF metadata.
The dense-reward plot is rebuilt from the counts in its earlier PDF. Historical filenames remain stable.

Use [Main-study reproduction](#main-study-reproduction) for the recorded study.
Public configs also cover older experiments and unrun proposals; match them through the [config guide](../configs/README.md).
[`data/build_nt_benchmark.py`](data/build_nt_benchmark.py) builds the earlier EXP-025 releases from which the later data derives.

## Main-study reproduction

Start with EXP-026 and EXP-027, the 18–19 October study. Its model sequence is:

```text
B: unchanged Qwen -> supervised fine-tuning -> S
S -> RL with shuffled theorem order          -> R
S -> RL with prerequisite order        -> C
```

All four use the same architecture. Compare their complete-proof acceptance on the same DEV statements and attempt budget.

Original data, weights, runtime and configs are external; several dataset builders remain private.
[`reproduction_v1.json`](../configs/reproduction_v1.json) reconstructs the [recorded recipe](../docs/EXPERIMENT_HISTORY.md#7-step-by-step-run-with-this-data-as-the-main-run-did).
It has not been validated by a rerun. The helper checks inputs and prepares commands; you must execute those commands separately.

<a id="1-inspect-the-project-locally"></a>

### 1. Local verification

Run the [local setup checks](../README.md#setup). They use synthetic inputs and need no model, Lean installation or GPU.
Follow [one proof attempt](#proof-execution) to see how the implementation fits together.

<a id="2-obtain-the-frozen-inputs"></a>

### 2. Archived inputs

Ask the project owner for the [archived data, runtime and model directories](../docs/EXPERIMENT_HISTORY.md#6-step-by-step-fetch-and-verify-the-data).
The older [GitHub checkpoint releases](../README.md#checkpoints) are separate experiments.

| Input | Meaning and use |
| --- | --- |
| `data/actor/train_pool.jsonl` | 506 TRAIN theorem statements: `name`, `formal_statement`, `split` |
| `data/actor/train_tasks.jsonl` | 506 RL starts: task ID, theorem header, empty supplied proof and initial Lean state |
| `data/sft_action/sft_train.jsonl` | 1,880 supervised examples. Each row's `messages` pairs a user observation with the assistant's next action; `format` identifies the observation format |
| `data/sft_action/sft_val.jsonl` | 125 separate examples from TRAIN theorems for monitoring SFT loss. This is not research DEV |
| `runtime/inputs/dev_pool.jsonl`, `dev_names.txt` | The 8 DEV statements and names used for model and setting choices |
| `schedules/` | Fixed R and C task orders. Both reuse the same TRAIN tasks |
| `models/B`, `models/S`, `models/R`, `models/C` | Complete checkpoint folders, including weights, tokenizer and model metadata |
| `runtime/inputs/`, `lean_env/` | Archived checker, REPL, identity receipt, Lean and compiled mathlib |

JSONL stores one JSON object per line. The [data handoff](../docs/EXPERIMENT_HISTORY.md#4-the-datasets-you-use) describes fields and provenance.
Keep the 21 TEST theorems reserved. The 164-theorem EXP-028 selection set is a separate exploratory set.

Place the extracted inputs outside Git in this layout. Here `/absolute/path/to/study` is a directory you choose:

```text
study/
  data/manifest.json
  data/actor/{train_pool.jsonl,train_tasks.jsonl,exclude_names.txt}
  data/sft_action/{sft_train.jsonl,sft_val.jsonl}
  runtime/inputs/{dev_pool.jsonl,dev_names.txt,checker.py,repl,mathlib_source_identity.json}
  schedules/{order_R_runtime.json,order_C_AI_runtime.json}
  lean_env/{lean-4.10.0-rc1-linux/,mathlib/}
  models/{B/,S/,R/,C/}
```

For a DEV check, only the selected model, data manifest, exclusion list, DEV files and runtime are needed.
On the Linux run host, install the locked ML environment with `uv sync --locked --group training`.
This downloads large ML packages. It uses the documented 19 October library versions; the original main-run package receipts remain external.

<a id="3-check-inputs-and-prepare-a-run"></a>

### 3. Input verification and run preparation

Replace the study path below with your extracted directory:

```bash
uv run python scripts/reproduce.py check --root /absolute/path/to/study --stage dev --model S
uv run python scripts/reproduce.py prepare --root /absolute/path/to/study --stage dev --model S \
  --run-id reproduction-001 --output /tmp/formal-rl-dev-plan-001
```

`check` returns a nonzero exit code for missing or mismatched inputs. It compares recorded file hashes, row counts, runtime layout and selected package versions.
Metadata checks confirm presence, not tokenizer equivalence. The checker neither executes Lean nor loads a model.
The model hash check identifies the archived reference checkpoint; a newly trained checkpoint will have a different identity.

`prepare` writes `config.json`, `command.txt` and `plan.json` into a new directory outside Git.
The plan includes input-check failures, source revision, whether the checkout has edits, and the intended output directory.
Review those files before execution. Training needs the managed runner, resource caps, a registered run and approval of the quoted GPU cost.
The managed runner must enforce the SFT wall cap; `run_sft.py` does not enforce it itself.

| Stage | Starting model | Output |
| --- | --- | --- |
| `sft` | B | A new S checkpoint |
| `rl-shuffled` | S | A new R checkpoint |
| `rl-curriculum` | S | A new C checkpoint |
| `dev` | B, S, R or C | Proof attempts and aggregate counts; no optimizer update |

Compare scientific outcomes from run artifacts: `run.json` holds resolved settings and counters, `steps.jsonl` holds group outcomes, and `rollouts.jsonl` holds actions.
`counts.accepted` counts accepted attempts; the length of `solved_names` counts distinct proved theorems.

## Dataset recreation

Use [`data/rebuild_nt_dataset.py`](data/rebuild_nt_dataset.py) with [`nt_sources_v1.json`](../configs/data/nt_sources_v1.json) and [`nt_rebuild_v1.json`](../configs/data/nt_rebuild_v1.json).
This workflow creates a new exploratory dataset. It does not recover private semantic reviews, the original 506-theorem release, interactive action examples or the original C order.
All generated files belong outside Git. Use new directories at every stage; failed downloads and builds retain their receipts.

### Fetch and assemble candidates

From the repository root, replace `/absolute/path/to/rebuild` with an external working directory:

```bash
uv sync --locked --group data
uv run python scripts/data/rebuild_nt_dataset.py sources --source lean_workbook
uv run python scripts/data/rebuild_nt_dataset.py fetch --source lean_workbook \
  --output /absolute/path/to/rebuild/raw
uv run --group data python scripts/data/rebuild_nt_dataset.py corpus \
  --raw /absolute/path/to/rebuild/raw --unchanged-base --output /absolute/path/to/rebuild/corpus
uv run python scripts/data/build_nt_benchmark.py select \
  --corpus /absolute/path/to/rebuild/corpus/corpus.jsonl --config configs/data/nt_rebuild_v1.json \
  --output /absolute/path/to/rebuild/selection
```

`fetch` reads full commit hashes and expected file sizes and SHA-256 values. Its default download ceiling is 128 MiB.
Repeat `--source` to combine sources; `sources` reports the total before download. Larger selections need an explicit `--max-bytes` value.
Available names: `lean_workbook`, `goedel_workbook`, `goedel_pset_v1`, `goedel_v2_sft`, `stp`.
The latter three resolve the revision prefixes recorded in the original importer. The first two pin public snapshots inspected on 21 October; their historical revisions were not recorded.
The [Hub download guide](https://huggingface.co/docs/huggingface_hub/guides/download) explains revision pinning.

`corpus` streams JSON/Parquet and uses SQLite to merge exact statement copies. It retains two distinct proof bodies per statement in hash order and preserves binder names.
Each retained row has `statement`, `sources`, `proofs`, `seen_by_parent_lineage`, `near_syllabus` and a canonical statement hash.
References remain `not_lean_verified`. `private/dispositions.jsonl` links parsed candidates to source files, origin IDs and body hashes; source-parser exclusions also appear in the counts.
SQLite holds candidate text on disk; provide disk space for the sources and extracted proofs.

`--unchanged-base` explicitly declares that the chosen parent has received no project fine-tuning. Upstream pretraining exposure stays unknown.
For another parent, use `--exposure-audit audit.json` instead. It requires `parent_checkpoint`, `audit_description` and a `statements` object mapping every retained canonical statement SHA-256 to a boolean exposure flag. Missing entries fail the build.
The rebuild does not recreate the private syllabus reservations. To protect existing studies, supply their statement-only JSONL lists to `select --protect-statements` (field `statement`) or their selection packages to `--protect-package`. Both exact/numeric matches and configured lexical near matches are excluded.

### Prepare the pinned verifier

Verification needs a managed x86-64 Linux CPU host, Git, curl, tar with zstd support, and the project's memory and wall limits. The existing verifier documents a 6 GiB managed memory cap.
Either use a suitable existing environment or prepare clean public sources with this layout:

```bash
mkdir -p /absolute/path/to/rebuild/environment
curl --fail --location --output /absolute/path/to/rebuild/environment/lean.tar.zst \
  https://github.com/leanprover/lean4/releases/download/v4.10.0-rc1/lean-4.10.0-rc1-linux.tar.zst
tar --zstd -xf /absolute/path/to/rebuild/environment/lean.tar.zst -C /absolute/path/to/rebuild/environment
git clone https://github.com/leanprover-community/mathlib4.git /absolute/path/to/rebuild/environment/mathlib
git -C /absolute/path/to/rebuild/environment/mathlib checkout --detach 29dcec074de168ac2bf835a77ef68bbe069194c5
export PATH="/absolute/path/to/rebuild/environment/lean-4.10.0-rc1-linux/bin:$PATH"
(cd /absolute/path/to/rebuild/environment/mathlib && lake exe cache get)
uv run python scripts/data/rebuild_nt_dataset.py identity --config configs/data/nt_rebuild_v1.json \
  --environment /absolute/path/to/rebuild/environment --output /absolute/path/to/rebuild/identity
```

The [Lean release](https://github.com/leanprover/lean4/releases/tag/v4.10.0-rc1) and [pinned mathlib source](https://github.com/leanprover-community/mathlib4/tree/29dcec074de168ac2bf835a77ef68bbe069194c5) identify the pair.
`identity` checks every Git dependency against `lake-manifest.json`, rejects changed worktrees and records the live Lean version and binary hash.
This checks source identity; it does not attest compiled caches. These preparation commands have not been executed as a full rebuild here.

### Verify, export and order

```bash
uv run python scripts/data/build_nt_benchmark.py verify --package /absolute/path/to/rebuild/selection \
  --environment /absolute/path/to/rebuild/environment --identity /absolute/path/to/rebuild/identity/identity.json \
  --output /absolute/path/to/rebuild/verified
uv run python scripts/data/rebuild_nt_dataset.py orders --package /absolute/path/to/rebuild/verified \
  --epochs 2 --seed 20261021 --output /absolute/path/to/rebuild/orders
```

The verifier replays references, extracts goal states and tries each supported screen tactic independently.
Every split must remain nonempty after admission. Failures and incomplete screens stay recorded; the builder never resamples or changes split labels to obtain a pass.
Its manifest binds exported files by hash. `orders` checks the frozen TRAIN task hash and preserves the same task multiplicity on every pass.
The default writes the shuffled R order only. Add `--prerequisites reviewed.json` for a C order; the file must contain `reviewer`, `review_status` and `prerequisites`, mapping every TRAIN task ID to a list of prerequisite task IDs. Unknown nodes, omitted tasks, duplicates and cycles fail.
Annotations remain supplied claims; the builder does not establish mathematical necessity or human review.

| Output | Consumer and meaning |
| --- | --- |
| `verified/data/pool.jsonl` | Statement pool for the RL runner; no reference proof bodies |
| `verified/data/tasks_train.jsonl`, `tasks_dev.jsonl`, `tasks_test.jsonl` | Split-specific whole-theorem starts with empty supplied proofs |
| `verified/data/sft/sft_train.jsonl`, `sft_val.jsonl` | Full-proof SFT examples drawn only from TRAIN families; loss validation also uses TRAIN |
| `verified/data/private/references.jsonl` | Private audit references; never a policy prompt or training input for DEV/TEST |
| `orders/order_R_runtime.json`, optional `order_C_AI_runtime.json` | New task-ID orders accepted by `--task-order`; their length supplies `max_groups` |

Keep TEST reserved and select model settings on DEV. Before using any SFT export, run the consumer's tokenizer and length checks; full proofs may exceed the interactive action budget.
Historical `scripts/reproduce.py check` deliberately expects the original release hashes and will reject this new dataset.
No full public-corpus or real Lean rebuild has been validated by these additions; local tests use synthetic JSON/Parquet and scripted verifier outcomes.

<a id="follow-one-proof-attempt"></a>

## Proof execution

Start with [`train/run_tactic_rl.py`](train/run_tactic_rl.py), which runs training and evaluation.
The proof environment, [`LeanEpisode`](train/rl_components/environment.py), applies actions and checks proof completion.
[`render_observation()`](train/rl_components/observations.py) builds the model's input from the current proof state.
Environment version 2 follows this execution path:

1. `main()` selects training or evaluation, validates inputs and applies the run limits. Task admission and scheduling live in [`rl_components/tasks.py`](train/rl_components/tasks.py).
2. `LeanEpisode.reset()` opens the theorem in Lean. `render_observation()` formats the statement, goals, accepted tactics and recent errors for the model.
3. `parse_action()` reads the model's tactic block. `LeanEpisode._step_v2()` applies tactics until an error and handles the configured backtracking behavior.
4. When no goals remain, `LeanEpisode._verify()` calls `verify_group()` in [`eval/bench_lean_verifier.py`](eval/bench_lean_verifier.py) to replay the complete proof. A changed goal alone does not earn the proof reward.
5. `update()` passes the sampled attempts to [`LeaveOneOutObjective` and `PolicyGradientUpdater`](train/rl_components/objectives.py). They compute the baseline and update the policy. Evaluation skips the optimizer.

An action is one model response; an attempt is a sequence of actions on one theorem.
A group contains multiple independent attempts on the same theorem. See the [term definitions](../README.md#terms).
The runner records settings and status in `run.json`, group outcomes in `steps.jsonl`, and individual actions in `rollouts.jsonl`.

<a id="where-to-make-a-change"></a>

## Implementation map

| Change | Owning code |
| --- | --- |
| Benchmark candidate selection or duplicate-aware splits | [`BenchmarkSelector`](data/nt_benchmark/selection.py) |
| Benchmark orchestration | [`BenchmarkPipeline`](data/nt_benchmark/pipeline.py) |
| Data exports or pinned Lean verification | [`BenchmarkExporter`](data/nt_benchmark/artifacts.py), [`LeanVerifier` and `BenchmarkVerifier`](data/nt_benchmark/verification.py) |
| Training task admission, curriculum cutoff or sampling order | [`PoolTaskAdmission`, `TaskCurriculum` and `TaskSchedule`](train/rl_components/tasks.py) |
| Action syntax and diagnostic formatting | [actions.py](train/rl_components/actions.py) |
| Sampling backends, stop tokens and generation seeds | [generation.py](train/rl_components/generation.py) |
| Run settings and training/evaluation restrictions | [configuration.py](train/rl_components/configuration.py) |
| Model observations | [`render_observation()`](train/rl_components/observations.py) |
| Proof interaction | [`LeanEpisode`](train/rl_components/environment.py) |
| Tactic rewards | `transition()`, `finish()`, `local_feedback()` and `v2_feedback()` in [`environment.py`](train/rl_components/environment.py) |
| REINFORCE objective or optimizer update | [`LeaveOneOutObjective` and `PolicyGradientUpdater`](train/rl_components/objectives.py) |
| SFT message conversion | [`SFTDataset`](train/rl_components/sft.py) |
| Proof reconstruction or fresh replay export | [`ProofTrajectory`](eval/proof_export/trajectory.py), [`ProofExporter`](eval/proof_export/exporter.py) |

Keep model, sampling, reward and budget settings in configs. Whole-proof GRPO uses TRL's optimizer through [`train/run_lean_grpo.py`](train/run_lean_grpo.py).

`BenchmarkPipeline` can take a replacement `selector` with `select(paths, max_input_bytes)` and `partition(candidates)` methods.
Import it from `scripts.data.nt_benchmark` in a repository checkout. Verification reads the frozen release config.
`PolicyGradientUpdater` accepts separate advantage and token-log-probability callables.

<a id="reusing-the-proof-components"></a>

## Proof-component reuse

The proof components connect theorem tasks, model observations, tactic actions and verifier outcomes.
With `scripts/train` on the Python import path, import `LeanEpisode` from `rl_components.environment`, `render_observation` from `rl_components.observations`, and model sampling from `rl_components.generation`.
Observation rendering needs neither Lean nor model libraries. The environment loads the existing verifier transport but no model or optimizer.
These modules are imported from the repository checkout.

A host must supply the pinned checker/REPL context, admitted theorem task, output directory and shared verifier counters.
Use the context setup in `run_tactic_rl.py` as the runtime reference: construct an episode, call `reset()`, sample an action from its observation, call `step(action, bonus)`, and always call `close()` in a `finally` block.
Keep original theorem context and runtime identities with every trajectory. A terminal candidate receives proof credit only after fresh whole-proof replay.
Goal-text changes and error penalties are heuristic signals. `state_key()` normalizes goal text for cycle detection; it does not identify a complete Lean state.
Branch handles belong to the live REPL and cannot be transferred across workers or serialized as portable proof states.

<details>
<summary>Possible search and value-learning extensions</summary>

<a id="toward-a-larger-prover"></a>

### Proposed proof-search extensions

[AlphaProof's published design](https://www.nature.com/articles/s41586-025-09833-y) combines policy/value learning with tree search that tracks multiple proof obligations.
It is a design reference here. No connection to the AlphaProof service has been implemented or validated.
The candidate-PPO path has a critic for its finite tactic catalogue; the main tactic learner uses a leave-one-out baseline.
Neither supplies a general value-guided AND–OR proof search implementation.

Proceed in this order:

1. Validate one admitted theorem end to end on the pinned Lean/mathlib pair, including a failing action, reset and fresh terminal replay. Local scripted tests do not establish runtime compatibility.
2. Freeze dataset version, provenance, duplicate checks and TRAIN/DEV/TEST membership. Establish independent-sampling results with token, Lean-call, wall-time and GPU budgets.
3. Register a bounded-search comparison against that baseline. Start with the existing `search_step()` interface; a larger search must explicitly manage all subgoals, branch lifetimes and budget accounting. Search-selected actions are not on-policy samples.
4. Only then register value learning and verified replay-data collection. Keep reference and held-out proofs outside training, retrieval and adaptive sampling; select settings on DEV. Preserve unsuccessful and censored outcomes with their costs.
5. Compare distinct proved theorems at equal budgets on the reserved test set after choices are frozen. Register every arm before launch and obtain approval for each paid GPU run.

Literal integration into another service also requires its documented input/output and runtime contract. Shared use of Lean does not establish that contract.

</details>

<a id="how-scripts-import-each-other"></a>

## Import structure

Most scripts add sibling folders such as `scripts/data` and `scripts/eval` to `sys.path` and import scripts by file name.
Some command modules also provide shared functions.
New callers should import environment, observation, action, objective, generation and configuration helpers from their owning modules.
The trainer retains its old names as compatibility exports. Tests that replace verifier functions should patch `rl_components.environment`, where episodes resolve those functions.
Update callers and source receipts when moving code; private run packages must include the full component directory.

The Lean checker module `checker.py` is not in Git.
Scripts that check proofs take its path with `--checker` and load it with `load_checker`.
[Section 4.10 of the data handoff](../docs/EXPERIMENT_HISTORY.md#410-the-lean-runtime-environment-repl-checker-and-identity) lists its archived copy, with the REPL binary and the Lean environment.

| Shared file | What other scripts import from it |
| --- | --- |
| [`data/prepare_grpo_pilot.py`](data/prepare_grpo_pilot.py) | `load_checker`, which loads the Lean checker module named by `--checker` |
| [`eval/bench_lean_verifier.py`](eval/bench_lean_verifier.py) | `repl_context` and `verify_group`, the shared Lean REPL scoring path |
| [`eval/probe_nt_pool.py`](eval/probe_nt_pool.py) | `check_candidates`, `lean_unit`, `header` and the proof prompt `INSTRUCTION` |
| [`eval/probe_start_depth.py`](eval/probe_start_depth.py) | `FORBIDDEN`, the matcher for forbidden code outside comments |
| [`data/replay_training_proofs.py`](data/replay_training_proofs.py) | `question`, `proof_body`, `direct_lean_call` and `ALLOWED_AXIOMS` |
| [`train/run_tactic_rl.py`](train/run_tactic_rl.py) | Legacy compatibility exports and training command helpers |
| [`audit/profile_external_proofs.py`](audit/profile_external_proofs.py) | `tokens`, `statement` and the `NUMBER_THEORY` pattern |
| [`data/verify_external_proofs.py`](data/verify_external_proofs.py) | `HEADER`, the Lean file header, and `check_batch` |
| [`train/rl_components/`](train/rl_components/) | Proof episodes, observations, parsing, generation, task scheduling, objectives and SFT examples |
| [`data/build_nt_sft_data.py`](data/build_nt_sft_data.py) | `body_of` and `strip_comments` |
| [`data/build_proof_tasks.py`](data/build_proof_tasks.py) | `top_level_steps` and the target guard `GUARD` |

## Runtime and development

The `training` dependency group supplies `torch`, `transformers`, `trl`, `datasets` and `tensorboardX` for the Linux run host:

```bash
uv sync --locked --group training
```

The separate `local-probe` group targets the MLX probe and requires newer Transformers; uv prevents combining it with `training`.
The unit suite needs the training packages and `math-verify` for answer-scoring tests:

```bash
uv run --locked --group training --with 'math-verify[antlr4-13-2]==0.9.0' python -m unittest discover -s tests
```

Tests that need the external Lean runtime remain skipped until it is configured.
Use the small checks in the [reproduction workflow](#main-study-reproduction) for a local checkout without them.

For proof checks, use the archived Linux Lean environment and its pinned mathlib revision; see [external artifacts](../README.md#not-in-git).
Installing [elan](https://lean-lang.org/install/manual/) alone does not supply the checker or mathlib snapshot.
Lean integration tests in `tests/test_tactic_rl_integration.py` need `TACTIC_RL_ROOT` and a new `TACTIC_RL_OUTPUT` directory.
The root must contain `lean_env_full/`, `proof_replay_v1/code/checker.py` and the REPL build under `rlvr_verifier/`.

For Markdown changes, run `uv run pre-commit run --files <changed-files>`.
Build the report with `latexmk -pdf -quiet -cd -interaction=nonstopmode -halt-on-error docs/tex/project_report.tex`.

Register scientific changes in the [experiment catalogue](../docs/EXPERIMENTS.md#protocol-and-result-updates).
Keep human authorship in commits and documents. Software and automated methods belong in the relevant methods or provenance record.

## Checkpoints

The following checkpoints belong to the earlier 5–9 October experiments. They are not S, R or C from the 18–19 October main study.
Their weights and tokenizer files are available through [GitHub Releases](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases).
The [download index](../configs/checkpoints.json) records source files, asset sizes, and GitHub digests.
For the later study, use the [archived weights](../docs/EXPERIMENT_HISTORY.md#49-model-weights) and identities in [reproduction_v1.json](../configs/reproduction_v1.json).

| Checkpoint | Download |
| --- | --- |
| SFT final | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean_sft_20261009_v2-final) |
| Primitive RLP step 118 | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/primitive_rlp_20261005-checkpoint-118) |
| Primitive RLP final | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/primitive_rlp_20261005-final) |
| Controlled RLP ordered final | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/controlled_rlp_20261006-ordered-final) |
| Controlled RLP shuffled final | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/controlled_rlp_20261006-shuffled-final) |
| SFT step 300 | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean_sft_20261009_v2-checkpoint-300) |
| SFT step 1500 | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean_sft_20261009_v2-checkpoint-1500) |
| SFT step 4526 | [Release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean_sft_20261009_v2-checkpoint-4526) |

Download all assets for a release into one directory:

```bash
gh release download lean_sft_20261009_v2-final --repo RavulapalliManas/efficient-rl-small-formal-reasoners --dir sft-final
```

If a release contains split files, use the reassembly command in its release notes.
Auxiliary metadata with private infrastructure identifiers uses redacted `.public.json` files.
Weights and optimizer state retain their original contents.

<a id="terms"></a>

## Terminology

| Term | Meaning |
| --- | --- |
| Lean / mathlib | The proof assistant / its mathematics library. Runs use a fixed revision of each |
| Tactic | One Lean proof instruction |
| Action | One model response containing tactics |
| Attempt | A sequence of actions on one theorem |
| Accepted attempt | An attempt whose complete proof Lean accepts |
| Proved theorem | A theorem with at least one accepted attempt. Counts of proved theorems and accepted attempts have different denominators |
| SFT | Supervised fine-tuning: learning from example proof steps |
| RL / RLVR | Reinforcement learning / RL with verifiable rewards. A Lean-accepted complete proof supplies the proof reward; some experiments add heuristic rewards or penalties |
| Unchanged model (B) | Pinned Qwen2.5-0.5B-Instruct before project fine-tuning |
| SFT model (S) | B after supervised fine-tuning |
| Shuffled RL (R) | S after REINFORCE on the shuffled TRAIN theorem order |
| Prerequisite-ordered RL (C) | S after REINFORCE on the annotated prerequisite order; prerequisite necessity was not verified |
| Filtered RL (`RL_filtered`, E6) | In EXP-028, RL on 62 TRAIN theorems that S proves in 1 to 3 of 4 reference attempts |
| GRPO | Group relative policy optimization; used by the whole-proof trainer |
| REINFORCE with a leave-one-out baseline | Policy-gradient estimator used by the interactive trainer; group sampling does not make it GRPO |
| PPO | Proximal policy optimization; the prepared comparisons include a learned value function |

Model letters are local to each study. Their definitions appear in the corresponding protocol.
Use the [study titles and naming rules](../docs/EXPERIMENTS.md#naming-and-terminology) in new documentation. Historical run IDs and config keys retain their original names.
The [handoff terminology table](../docs/EXPERIMENT_HISTORY.md#start-here-one-page-for-abhinav) maps older names to these terms.

<a id="every-script"></a>

## File index

The index covers 116 command entry points and shared modules, grouped by folder.

Purpose describes the script. Experiments lists recorded uses; a dash means no association is recorded.
Shared scripts can serve several experiments.

### Reproduction

<details>
<summary>Show files</summary>

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`reproduce.py`](reproduce.py) | Inspect, check, and prepare the documented number-theory study without launching jobs. | EXP-026, EXP-027 |

</details>

### data/

<details>
<summary>Show files</summary>

Build datasets, splits, SFT examples and RL tasks.

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`data/rebuild_nt_dataset.py`](data/rebuild_nt_dataset.py) | Fetch pinned sources, assemble candidates, record verifier identity and build task orders. | EXP-025 |
| [`data/add_premises_sft.py`](data/add_premises_sft.py) | Add the retrieved-lemma section to existing v2 SFT examples. | EXP-028 |
| [`data/build_answer_tasks.py`](data/build_answer_tasks.py) | Build Lean-checked number-theory answer tasks (formal Q&A) and check every reference answer in Lean. | EXP-024 |
| [`data/build_dev_set.py`](data/build_dev_set.py) | Build a development set of LeanDojo Benchmark 4 random/val (+ test) theorems for comparing Lean 4 proof models. | – |
| [`data/build_nt_archive.py`](data/build_nt_archive.py) | Number-theory theorems of the mathlib Archive at the pin, from files compiled at the pin. | – |
| [`data/build_nt_benchmark.py`](data/build_nt_benchmark.py) | Build a versioned, exploratory number-theory benchmark from the existing corpus. | EXP-025 |
| [`data/build_nt_hole_tasks.py`](data/build_nt_hole_tasks.py) | RL hole tasks from Lean-checked proofs of the held-out theorems, plus one whole-statement task per RL pool statement. | EXP-017 |
| [`data/build_nt_knowledge.py`](data/build_nt_knowledge.py) | Number-theory knowledge items from mathlib at the pin: doc comment (informal) + Lean statement + proof as written. | – |
| [`data/build_nt_pool.py`](data/build_nt_pool.py) | Build the number-theory statement pool for RL from Lean-checked Lean Workbook statements. | EXP-016 |
| [`data/build_nt_sft_data.py`](data/build_nt_sft_data.py) | SFT examples from Lean-checked external proofs of number-theory pool statements (train split only). | EXP-017 |
| [`data/build_nt_splits.py`](data/build_nt_splits.py) | Construct theorem name lists for training, held-out evaluation and scoring. | EXP-017 |
| [`data/build_nt_states.py`](data/build_nt_states.py) | Initial goal states (Lean trace_state) for one split of the number-theory statement pool. | EXP-016 |
| [`data/build_nt_tactic_aug.py`](data/build_nt_tactic_aug.py) | Tactic-level augmentation: new Lean-checked proofs from the accepted SFT proofs of sft_problems (train split only). | – |
| [`data/build_premise_corpus.py`](data/build_premise_corpus.py) | Dump Mathlib theorem names and statements from the pinned Lean environment as a premise corpus. | EXP-028 |
| [`data/build_proof_tasks.py`](data/build_proof_tasks.py) | Build Lean-checked proof tasks from the original mathlib proof text, with cut points. | – |
| [`data/build_rft_data.py`](data/build_rft_data.py) | SFT examples for the rejection-sampling fine-tuning (RFT) baseline from Lean-accepted map attempts (train split only). | EXP-019 |
| [`data/build_search_sft.py`](data/build_search_sft.py) | Mine SFT examples from a step-search assessment of TRAIN theorems (expert iteration and error repair). | EXP-028 |
| [`data/build_sft_data.py`](data/build_sft_data.py) | Build Lean 4 SFT data from LeanDojo Benchmark 4 training proofs at the pinned mathlib commit. | EXP-014 |
| [`data/build_syntax_sft.py`](data/build_syntax_sft.py) | Syntax SFT from existing replay-accepted bank proofs, at the project's Lean pin. | EXP-017 |
| [`data/build_task_bank.py`](data/build_task_bank.py) | Merge Lean-checked proof tasks into one task bank with curriculum metadata. | – |
| [`data/build_v21_sft.py`](data/build_v21_sft.py) | SFT examples in the environment version 2.1 format, from existing Lean-checked number-theory proofs. | – |
| [`data/export_task_pool.py`](data/export_task_pool.py) | Export a larger training-theorem pool from LeanDojo Benchmark 4 for curriculum experiments. | – |
| [`data/import_extra_nt.py`](data/import_extra_nt.py) | Import extra public Lean 4 number-theory statements and proofs, filter them like nt_pool_v2, and Lean-check them. | – |
| [`data/mathematical_rlp_data.py`](data/mathematical_rlp_data.py) | Bounded, authored records and matched information views for mathematical RLP. | EXP-013 |
| [`data/merge_sft_rows.py`](data/merge_sft_rows.py) | Assemble an SFT data directory from base SFT examples plus optional extra TRAIN SFT examples, within the SFT token limits. | – |
| [`data/prepare_grpo_pilot.py`](data/prepare_grpo_pilot.py) | Prepare gold-free whole-proof prompts on cpu-angad. | – |
| [`data/prepare_leandojo.py`](data/prepare_leandojo.py) | Prepare a project-defined Nat subset of LeanDojo's official random splits. | EXP-001 |
| [`data/prepare_nt_tactic_expansion.py`](data/prepare_nt_tactic_expansion.py) | Prepare frozen TRAIN-source theorems and available prefixes, with separate DEV theorems. | – |
| [`data/prepare_prerequisite_curriculum.py`](data/prepare_prerequisite_curriculum.py) | Prepare a small authored curriculum with the existing pinned checker. | EXP-005 |
| [`data/prepare_rlp_corpus.py`](data/prepare_rlp_corpus.py) | Prepare RLP acquisition text on cpu-angad, without evaluation solutions. | – |
| [`data/prepare_short_proof_subset.py`](data/prepare_short_proof_subset.py) | Select short original LeanDojo training proofs without exposing hidden suffixes. | – |
| [`data/prepare_tactic_curriculum.py`](data/prepare_tactic_curriculum.py) | Freeze existing training proof starts and paired starting-state schedules. | – |
| [`data/replay_training_proofs.py`](data/replay_training_proofs.py) | Replay linear LeanDojo training proofs with the pinned Lean checker on cpu-angad. | – |
| [`data/retrace_v21_states.py`](data/retrace_v21_states.py) | Re-derive each v2.1 SFT prompt's goal through the RL environment and keep only SFT examples whose target RL can run. | – |
| [`data/verify_external_proofs.py`](data/verify_external_proofs.py) | Check Lean Workbook statements and proofs (Lean Workbook and Goedel-Prover releases) at our pinned Lean. | EXP-016, EXP-024, EXP-025, EXP-029 |

</details>

### data/nt_benchmark/

<details>
<summary>Show files</summary>

Components used by `data/build_nt_benchmark.py` and `data/rebuild_nt_dataset.py` (EXP-025).

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`data/nt_benchmark/__init__.py`](data/nt_benchmark/__init__.py) | Number-theory benchmark components. Legacy script commands remain supported. | EXP-025 |
| [`data/nt_benchmark/artifacts.py`](data/nt_benchmark/artifacts.py) | Proof profiling and split-safe exports consumed by SFT and RL runners. | – |
| [`data/nt_benchmark/corpus.py`](data/nt_benchmark/corpus.py) | Build a deterministic, disk-backed corpus of unverified number-theory proofs. | EXP-025 |
| [`data/nt_benchmark/identity.py`](data/nt_benchmark/identity.py) | Record the pinned Lean/mathlib pair from clean Git checkouts and a live binary. | EXP-025 |
| [`data/nt_benchmark/pipeline.py`](data/nt_benchmark/pipeline.py) | Compose replaceable selection, verification, and export components. | – |
| [`data/nt_benchmark/schedules.py`](data/nt_benchmark/schedules.py) | Recreate paired task orders from one frozen TRAIN bank and explicit annotations. | EXP-025 |
| [`data/nt_benchmark/selection.py`](data/nt_benchmark/selection.py) | Deterministic selection and duplicate-aware split assignment. | – |
| [`data/nt_benchmark/sources.py`](data/nt_benchmark/sources.py) | Fetch pinned public files and adapt their proof candidates to one input schema. | EXP-025 |
| [`data/nt_benchmark/storage.py`](data/nt_benchmark/storage.py) | Bounded JSONL storage and source receipts for benchmark builds. | – |
| [`data/nt_benchmark/verification.py`](data/nt_benchmark/verification.py) | Pinned Lean replay, goal extraction, and complete baseline screening. | – |

</details>

### train/

<details>
<summary>Show files</summary>

Training entry points and the Lean environment.

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`train/band_sampler.py`](train/band_sampler.py) | Arm C task sampler: Thompson sampling toward families that give mixed groups. | – |
| [`train/candidate_policy.py`](train/candidate_policy.py) | Shared categorical policy over a frozen menu of Lean-admitted tactics. | – |
| [`train/lean_ppo.py`](train/lean_ppo.py) | Pinned TRL PPO with the existing binary Lean scorer, not a learned reward model. | EXP-022 |
| [`train/local_operator_training.py`](train/local_operator_training.py) | Local-action mode for run_grpo_pilot, using the same pinned TRL optimizer. | EXP-011 |
| [`train/run_candidate_ppo.py`](train/run_candidate_ppo.py) | PPO-Clip updates for complete Lean candidate-selection attempts. | – |
| [`train/run_candidate_reinforce.py`](train/run_candidate_reinforce.py) | Monte Carlo REINFORCE over the shared frozen candidate policy. | – |
| [`train/run_grpo_pilot.py`](train/run_grpo_pilot.py) | Thin TRL GRPO wrapper for the separate whole-proof pilot, one L40S. | EXP-004, EXP-005, EXP-006 |
| [`train/run_interleaved_training.py`](train/run_interleaved_training.py) | Alternate existing SFT, hidden-target RLP, and Lean GRPO runners. | EXP-020 |
| [`train/run_lean_grpo.py`](train/run_lean_grpo.py) | GRPO on Lean tasks with a Lean-verified binary reward, and cost records per optimizer step. | EXP-019 |
| [`train/run_rlp_pilot.py`](train/run_rlp_pilot.py) | RLP acquisition through TRL, one L40S, no supervised proof objective. | EXP-007, EXP-008, EXP-009, EXP-013, EXP-021 |
| [`train/run_sft.py`](train/run_sft.py) | Supervised fine-tuning on Lean 4 proofs with TRL 0.24 (prompt-completion, loss on the completion only). | EXP-014, EXP-018, EXP-023, EXP-024, EXP-025, EXP-027 |
| [`train/run_tactic_ppo.py`](train/run_tactic_ppo.py) | Pinned library PPO for one tactic block, with the REINFORCE Lean attempt contract. | EXP-023 |
| [`train/run_tactic_rl.py`](train/run_tactic_rl.py) | On-policy tactic RL with Lean transitions and a leave-one-out baseline. | EXP-019, EXP-020, EXP-021, EXP-023, EXP-024, EXP-026, EXP-027, EXP-028, EXP-029, EXP-030 |

</details>

### train/rl_components/

<details>
<summary>Show files</summary>

Reusable training components. Importing these modules does not load model weights or start Lean.

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`train/rl_components/__init__.py`](train/rl_components/__init__.py) | Reusable training components. Importing this package does not load model libraries. | EXP-025 |
| [train/rl_components/actions.py](train/rl_components/actions.py) | Lean tactic-action parsing and diagnostic message formatting. | – |
| [train/rl_components/configuration.py](train/rl_components/configuration.py) | Configuration validation and training/evaluation mode restrictions. | – |
| [train/rl_components/environment.py](train/rl_components/environment.py) | Lean proof episodes, replay acceptance, backtracking and local feedback. | – |
| [train/rl_components/observations.py](train/rl_components/observations.py) | Shared observation renderer for training, evaluation and SFT data. | – |
| [train/rl_components/generation.py](train/rl_components/generation.py) | Sampling backends, stop-token handling, and reproducible generation seeds. | – |
| [`train/rl_components/objectives.py`](train/rl_components/objectives.py) | The existing leave-one-out estimator and streamed policy-gradient update. | – |
| [`train/rl_components/premises.py`](train/rl_components/premises.py) | BM25 premise retrieval over library lemmas, independent of Lean processes. | – |
| [`train/rl_components/proofs.py`](train/rl_components/proofs.py) | Lean action serialization without verifier or trainer imports. | – |
| [`train/rl_components/sft.py`](train/rl_components/sft.py) | SFT message conversion without importing datasets, Torch, or TRL. | – |
| [`train/rl_components/tasks.py`](train/rl_components/tasks.py) | Task admission and curriculum scheduling, independent of Lean processes. | EXP-026 |

</details>

### eval/

<details>
<summary>Show files</summary>

Evaluations, baselines, probes and scoring checks.

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`eval/bench_lean_verifier.py`](eval/bench_lean_verifier.py) | Compare three Lean verifier modes for a GRPO reward on the same proof attempts. No model runs. | AUD-002 |
| [`eval/check_checker_attacks.py`](eval/check_checker_attacks.py) | Adversarial continuations against the shared Lean scorer (verify_group): every attack must be rejected. | AUD-002, EXP-023 |
| [`eval/check_inhouse_controls.py`](eval/check_inhouse_controls.py) | Lean integration controls for build_nt_tactic_aug.py, build_nt_knowledge.py, and build_nt_archive.py (not unittest). | – |
| [`eval/check_rollout_backends.py`](eval/check_rollout_backends.py) | GPU check that batched HF and vLLM sampling match single-prompt HF sampling of the tactic policy. | EXP-029 |
| [`eval/check_scoring_paths.py`](eval/check_scoring_paths.py) | Integration test: score the same candidates through the probe, map, and trainer paths and require equal verdicts. | AUD-002 |
| [`eval/check_task_scoring.py`](eval/check_task_scoring.py) | Integration test: RL tasks score the same in the pass-rate map (probe_nt_pool.py) and in the GRPO reward. | – |
| [`eval/check_vllm_equivalence.py`](eval/check_vllm_equivalence.py) | vLLM-versus-HF equivalence test for probe_nt_pool.py (GPU; first step of the GPU lease; exit 1 on failure). | – |
| [`eval/curriculum_metrics.py`](eval/curriculum_metrics.py) | Curriculum metrics from run_tactic_rl.py output directories (assessment or training). | EXP-023, EXP-024 |
| [`eval/demo_episode.py`](eval/demo_episode.py) | Screen or demonstrate specified theorems in Lean environment version 2.1. | – |
| [`eval/elaborate_statements.py`](eval/elaborate_statements.py) | Elaborate theorem statements under the pinned Lean and mathlib, with `sorry` as the proof. No proof is checked. | EXP-029 |
| [`eval/eval_answer_tasks.py`](eval/eval_answer_tasks.py) | Pass rate of frozen models on Lean-checked answer tasks, next to two no-model baselines. | – |
| [`eval/evaluate_acquisition.py`](eval/evaluate_acquisition.py) | Evaluation only: recognition, recall, and elementary number-theory application. | EXP-010 |
| [`eval/export_tactic_proofs.py`](eval/export_tactic_proofs.py) | Export complete model proof paths and replay with the existing strict scorer. | EXP-023, EXP-026, EXP-027 |
| [`eval/false_hypothesis_sweep.py`](eval/false_hypothesis_sweep.py) | False-hypothesis sweep: which statements of the number-theory pool have hypotheses that prove False. | EXP-029 |
| [`eval/grade_mathematical_responses.py`](eval/grade_mathematical_responses.py) | Prepare blinded review packets and recount independent mathematical grades. | EXP-013 |
| [`eval/lean_eval_statements.py`](eval/lean_eval_statements.py) | Lean-checked whole-proof evaluation of frozen models on curriculum-validation theorems. | EXP-015 |
| [`eval/nearest_proof_baseline.py`](eval/nearest_proof_baseline.py) | Nearest-proof baseline: how many held-out statements a copied train proof proves, with no learning. | – |
| [`eval/pass_rate_map.py`](eval/pass_rate_map.py) | Pass-rate map of one checkpoint over the task bank: a group of attempts per unit, by steps remaining. | EXP-019 |
| [`eval/probe_nt_pool.py`](eval/probe_nt_pool.py) | Pass rates of frozen models and a single-tactic baseline on sampled statements of the number-theory pool. | EXP-015 |
| [`eval/probe_qwen.py`](eval/probe_qwen.py) | Bounded, unquantized MLX math probe. Lean statements remain unverified. | EXP-002, EXP-003 |
| [`eval/probe_start_depth.py`](eval/probe_start_depth.py) | Measure how often a frozen model finishes a training proof from a later starting point. | – |
| [`eval/probe_tactic_burdens.py`](eval/probe_tactic_burdens.py) | Frozen prompt/premise diagnostic. No optimizer or generated-code execution. | – |
| [`eval/regrade_controlled_rlp.py`](eval/regrade_controlled_rlp.py) | Blinded regrade of both arms of the controlled RLP study (Experiment 13); no model calls. | EXP-013 |
| [`eval/rescore_accepted.py`](eval/rescore_accepted.py) | Re-score earlier accepted proofs with the current shared scorer and scan them for the checker holes of 2026-10-10. | – |
| [`eval/search_tactics.py`](eval/search_tactics.py) | Best-first tactic search with a frozen model on curriculum-validation theorems (Lean REPL tactic mode). | EXP-015 |
| [`eval/single_tactic_baseline.py`](eval/single_tactic_baseline.py) | No-model baseline: each fixed tactic alone as the whole proof of each curriculum-validation theorem. | EXP-015, EXP-024 |
| [`eval/verify_local_operator.py`](eval/verify_local_operator.py) | Verify supplied-knowledge witness expressions with the existing Lean checker. | EXP-011 |

</details>

### eval/proof_export/

<details>
<summary>Show files</summary>

Package used by `eval/export_tactic_proofs.py`.

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`eval/proof_export/__init__.py`](eval/proof_export/__init__.py) | Reusable proof replay and attempt reconstruction components. | EXP-025 |
| [`eval/proof_export/exporter.py`](eval/proof_export/exporter.py) | Re-admit saved tasks, replay complete proofs, and serialize export artifacts. | – |
| [`eval/proof_export/trajectory.py`](eval/proof_export/trajectory.py) | Reconstruct the exact retained proof from a complete attempt. | – |

</details>

### audit/

<details>
<summary>Show files</summary>

Read-only audits, leakage checks and held-out screens.

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`audit/audit_primitive_rlp.py`](audit/audit_primitive_rlp.py) | Audit saved primitive RLP records on CPU; no model inference. | EXP-009 |
| [`audit/audit_proofs.py`](audit/audit_proofs.py) | Stream existing GRPO failures on cpu-angad; no model or Lean execution. | – |
| [`audit/audit_rlp_corpus.py`](audit/audit_rlp_corpus.py) | Audit immutable structured RLP exports on CPU, without model inference. | EXP-012 |
| [`audit/audit_rlp_updates.py`](audit/audit_rlp_updates.py) | Compare saved RLP weights and isolate BF16 update rounding on CPU. | EXP-008 |
| [`audit/automation_chain_screen.py`](audit/automation_chain_screen.py) | Automation-chain screen: which held-out statements a short, statement-independent automation chain proves. | EXP-029 |
| [`audit/check_leakage.py`](audit/check_leakage.py) | Near-duplicate checks of new training items against held-out statements, with the thresholds of nt_splits_v3. | EXP-017 |
| [`audit/check_split_leakage.py`](audit/check_split_leakage.py) | Check the training side of a split for leakage into validation, test, and miniF2F. | EXP-017 |
| [`audit/classify_proof_failures.py`](audit/classify_proof_failures.py) | Classify the Lean errors in a saved whole-proof diagnostic; no model or Lean execution. | AUD-001, EXP-003, EXP-023 |
| [`audit/curriculum_inventory.py`](audit/curriculum_inventory.py) | Count start points for a reverse proof-state curriculum in a LeanDojo export; read-only. | AUD-001 |
| [`audit/heldout_screens.py`](audit/heldout_screens.py) | Pure helpers shared by the held-out screens: pool loading, statement parsing, similarity, bands and strata. | – |
| [`audit/number_theory_priors.py`](audit/number_theory_priors.py) | Rank the prior knowledge that mathlib number-theory proofs use, from LeanDojo premise annotations. | – |
| [`audit/profile_external_proofs.py`](audit/profile_external_proofs.py) | Profile the Lean-checked external proofs (verify_external_proofs.py output) before any use in training. | EXP-024 |
| [`audit/stratify_heldout.py`](audit/stratify_heldout.py) | Join the three held-out screens into one stratum per statement, and count proved theorems per stratum and arm. | EXP-029 |
| [`audit/train_similarity_strata.py`](audit/train_similarity_strata.py) | TRAIN-similarity screen: how close each held-out statement is to a TRAIN statement, with an optional copy baseline. | EXP-029 |

</details>

### monitor/

<details>
<summary>Show files</summary>

Run monitoring.

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`monitor/track_tensorboard.py`](monitor/track_tensorboard.py) | Display the frozen-policy pilot's S3 records in TensorBoard. | – |

</details>

### report/

<details>
<summary>Show files</summary>

Figures for the report.

| Script | Purpose | Experiments |
| --- | --- | --- |
| [`report/make_report_figures.py`](report/make_report_figures.py) | Draw the data figures of docs/tex/project_report.tex from recorded counts. | – |

</details>
