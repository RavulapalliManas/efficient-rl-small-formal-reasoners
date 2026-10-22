# Efficient RL for Small Formal Reasoners

This project tests whether reinforcement learning (RL) helps a small language model write number-theory proofs under a fixed compute budget.
We use `Qwen/Qwen2.5-0.5B-Instruct`.
The model proposes proof steps; Lean 4 checks them. A complete accepted proof earns the proof reward.

<a id="read-this-first"></a>

## Documentation

| Purpose | Reference |
| --- | --- |
| Study methods and findings | [Report PDF](docs/tex/project_report.pdf), with a reading guide at the start; [TeX source](docs/tex/project_report.tex) |
| Execution status | [Status](#status) |
| Reproduce the main study | [Reproduction workflow](#reproduce-the-main-study) |
| Recreate datasets | [Dataset rebuild](#recreate-datasets) |
| Local setup | [Setup](#setup) |
| Implementation | [Script guide](scripts/README.md#primary-workflow) and [config guide](configs/README.md#configuration-selection) |
| Experiment records | [Experiment register](docs/EXPERIMENTS.md#experiment-register) |
| Main-study data and checkpoints | [Data specification](docs/EXPERIMENTS.md#start-here-one-page-for-abhinav) and [data handoff](docs/EXPERIMENTS.md#data-handoff-for-abhinav-20-october-2026-ist) |

## Terms

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
| B, S, R, C | In the 18–19 October study: unchanged model, SFT model, SFT + shuffled RL, SFT + prerequisite-first RL |

Model letters are local to each study. Their definitions appear in the corresponding protocol.
The [handoff terminology table](docs/EXPERIMENTS.md#start-here-one-page-for-abhinav) maps older names to these terms.

## Reproduce the main study

Start with EXP-026 and EXP-027, the 18–19 October study. Its model sequence is:

```text
B: unchanged Qwen -> supervised fine-tuning -> S
S -> RL with shuffled theorem order          -> R
S -> RL with prerequisite-first order        -> C
```

All four have the same model architecture. They differ in the training applied to their weights.
Compare them on the same DEV statements and attempt budget. A model response proposes tactics; Lean decides whether the complete proof is accepted.

### 1. Inspect the project locally

Install Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from the repository root:

```bash
uv sync --locked
uv run python scripts/reproduce.py overview
uv run python -m unittest tests.test_reproduction tests.test_rl_components
```

These commands need no model, Lean installation or GPU. The tests use synthetic inputs and check local code; they do not reproduce a research result.
Follow [one proof attempt](scripts/README.md#proof-execution) to see how the implementation fits together.

### 2. Obtain the frozen inputs

Ask the project owner for the [archived data, runtime and model directories](docs/EXPERIMENTS.md#6-step-by-step-fetch-and-verify-the-data).
Some dataset builders and the original main-run configs are private, so a clone alone cannot reproduce the full study.
The older [GitHub checkpoint releases](#checkpoints) are separate experiments.

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

JSONL stores one JSON object per line. The [data handoff](docs/EXPERIMENTS.md#4-the-datasets-you-use) describes fields and provenance.
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

### 3. Check inputs and prepare a run

Replace the study path below with your extracted directory:

```bash
uv run python scripts/reproduce.py check --root /absolute/path/to/study --stage dev --model S
uv run python scripts/reproduce.py prepare --root /absolute/path/to/study --stage dev --model S \
  --run-id reproduction-001 --output /tmp/formal-rl-dev-plan-001
```

`check` returns a nonzero exit code for missing or mismatched inputs. It compares recorded file hashes, row counts, runtime layout and selected package versions.
It checks metadata presence but does not establish tokenizer equivalence, execute Lean or load a model.
The model hash check identifies the archived reference checkpoint; a newly trained checkpoint will have a different identity.

`prepare` writes `config.json`, `command.txt` and `plan.json` into a new directory outside Git. It never launches a job.
The plan includes input-check failures, source revision, whether the checkout has edits, and the intended output directory.
Review those files before execution. Training needs the managed runner, resource caps, a registered run and approval of the quoted GPU cost.
The managed runner must enforce the SFT wall cap; `run_sft.py` does not enforce it itself.

| Stage | Starting model | Output |
| --- | --- | --- |
| `sft` | B | A new S checkpoint |
| `rl-shuffled` | S | A new R checkpoint |
| `rl-curriculum` | S | A new C checkpoint |
| `dev` | B, S, R or C | Proof attempts and aggregate counts; no optimizer update |

The versioned [reproduction specification](configs/reproduction_v1.json) holds the file identities and reconstructed settings.
These settings come from the [recorded recipe](docs/EXPERIMENTS.md#7-step-by-step-run-with-this-data-as-the-main-run-did); they are not copies of the private configs, and no rerun is established by preparing them.
Compare scientific outcomes from run artifacts: `run.json` holds resolved settings and counters, `steps.jsonl` holds group outcomes, and `rollouts.jsonl` holds actions.
`counts.accepted` counts accepted attempts; the length of `solved_names` counts distinct proved theorems.

## Recreate datasets

The public rebuild starts from pinned upstream files and produces a new exploratory dataset. It covers source downloads, corpus assembly, duplicate-aware splits, Lean verification, SFT/RL exports and task orders.
Follow the [dataset recreation guide](scripts/README.md#dataset-recreation) for the complete commands and output schemas.

```bash
uv sync --locked --group data
uv run python scripts/data/rebuild_nt_dataset.py sources --source lean_workbook
uv run --group data python -m unittest tests.test_dataset_rebuild tests.test_nt_benchmark
```

`sources` lists immutable revisions, file sizes and checksums without downloading data.
The guide starts with Lean Workbook; the registry also covers Goedel Workbook, Goedel-Pset, Goedel SFT v2 and STP.
Rebuilding needs no trained model. Lean verification runs on a managed Linux CPU host with the pinned toolchain and mathlib.

The rebuilt dataset has its own version, split membership and counts. The original 506-theorem release, action SFT examples and AI-reviewed curriculum still require their frozen archives.
Corpus candidates are marked unverified until Lean admission; the exported SFT examples contain full proofs. Keep these separate from the main study's interactive action examples.

## Status

On 20 October 2026, Manas halted experiment execution and requested analysis only.
EXP-029 and EXP-030 did not run; see the [EXP-029](docs/EXPERIMENTS.md#exp-029-stop) and [EXP-030](docs/EXPERIMENTS.md#exp-030-stop) stop records.

These tables are the current execution status record. Refer to the linked run records for scoring protocols and limitations.
The [experiment register](docs/EXPERIMENTS.md#experiment-register) links each protocol, result and report section.

| ID | Experiment | Config or entry point | Status |
| --- | --- | --- | --- |
| [EXP-030](docs/EXPERIMENTS.md#exp-030) | Do per-action Lean-error penalties make REINFORCE close more proofs than the terminal proof reward alone? | `scripts/train/run_tactic_rl.py` | Not run. EXP-030 does not run, because Manas stops all experiments (EXP-029 stop entry). The question of EXP-030 stays open |
| [EXP-029](docs/EXPERIMENTS.md#exp-029) | Do the RL arms differ from S at 8 attempts on the screened 164-theorem selection set and on the 62 RL_filtered theorems? | `scripts/train/run_tactic_rl.py` | Not run. Neither Lease 5 nor Lease 6 is launched, and no EXP-029 stage runs. The questions of EXP-029 stay open |
| [EXP-028](docs/EXPERIMENTS.md#exp-028) | Low-acceptance remedies: step search, automation fallback, lemma retrieval, search-mined SFT, pass-rate-filtered RL | `scripts/train/run_tactic_rl.py` options; `scripts/data/build_search_sft.py`, `add_premises_sft.py`, `build_premise_corpus.py` | No remedy arm is promoted on the 164-theorem selection set: S proves 1 (E0); search (E2), lemma retrieval (E3), the SFT seed control (E5), R (E7) and pass-rate-filtered RL (E6) prove 0 to 2. E4 did not run: the S_mined SFT stage failed on a data-builder defect, now fixed. RL_filtered raises acceptance on its 62 TRAIN theorems from 0.31 to 0.60 (p = 0.0001), without measurable transfer. Kimina-Prover-Distill-0.6B proves 47 and 40 of 164 within 8 attempts in two draws (56 in either) |
| [EXP-027](docs/EXPERIMENTS.md#exp-027) | Curriculum, Manas: benchmark performance and generalization | [Protocol and execution guide](docs/EXPERIMENTS.md#exp-027); [common data and execution plan](docs/EXPERIMENTS.md#common-data-and-curriculum-execution-plan-18-october-2026-ist); [report plan](docs/tex/project_report.pdf); `scripts/train/run_tactic_rl.py --task-order` | C (prerequisite-first) accepts 255 of 4,048 TRAIN attempts against R's 164 on the same theorems (per-theorem p = 0.0002), with the gain inside the first epoch. C proves 0 of 8 DEV theorems; the held-out R/C contrast is not yet measured. Annotations have no human confirmation or measured prerequisite necessity |
| [EXP-026](docs/EXPERIMENTS.md#exp-026) | Generalization, Abhinav: unchanged Qwen, SFT, and SFT + RLVR | [Revised protocol](docs/EXPERIMENTS.md#generalization-experiment-revised-framing-16-october-2026); [current data and model contract](docs/EXPERIMENTS.md#current-data-and-model-contract-closure-18-october-2026-ist) | Fresh S trained; B, S and R each prove 0 of 8 DEV theorems. R accepts 164 of 4,048 TRAIN attempts with no detected learning (first against second visit, p = 0.93) and proves 1 of 164 selection-set theorems in EXP-028 (E7), no gain over S. RL shifts R toward short, malformed actions (DEV format errors 37 to 113 of 192) |

<details>
<summary>Earlier experiments (EXP-001 to EXP-025) and audits</summary>

| ID | Experiment | Config or entry point | Status |
| --- | --- | --- | --- |
| [EXP-025](docs/EXPERIMENTS.md#exp-025) | Broader benchmark and data pipeline | [Builder](scripts/data/build_nt_benchmark.py), [expanded config](configs/data/nt_benchmark_v2.json); [checked prerequisite release](docs/EXPERIMENTS.md#checked-prerequisite-release-outcome-17-october-2026); [full-corpus mapping](docs/EXPERIMENTS.md#full-corpus-mapping-outcome-17-october-2026) | `validated_v2/` supersedes the initial common release for new consumers. This release retains 1,880 TRAIN SFT examples and 125 separate SFT validation examples. Public pretraining exposure and complete semantic alias independence remain unresolved |
| [EXP-024](docs/EXPERIMENTS.md#exp-024) | Undergraduate number-theory SFT: new data against equal-token old data | `scripts/train/run_sft.py`, `scripts/eval/curriculum_metrics.py` | Protocol approved 15 October. Mathlib identity check, syllabus, corpus v0 (40,732 theorems) and single-tactic screen done; no training or evaluation yet |
| [EXP-023](docs/EXPERIMENTS.md#exp-023) | Broader number theory, reward and curriculum comparisons, PPO and REINFORCE | [Data config](configs/data/nt_expanded_tactic_v1.json), `scripts/train/run_tactic_rl.py`, `scripts/train/run_tactic_ppo.py` | Final curriculum run done 13 October. RL gains on new instances of trained families (`id_val`), no gain on held-out families; the ordered curriculum shows no advantage, and the failed token gate invalidates the efficiency claim |
| [EXP-022](docs/EXPERIMENTS.md#exp-022) | PPO versus GRPO with the same Lean reward | `scripts/train/lean_ppo.py`, `configs/rl/qwen_05b_lean_{ppo,grpo}_comparison.json` | Adapter and paired configs prepared; EXP-023 corrected two scoring defects in the shared adapter. Lean PPO training has not run |
| [EXP-021](docs/EXPERIMENTS.md#exp-021) | Monday recovery: verified distillation before Lean RL | `configs/midtrain/qwen_05b_monday_recovery.json` | Done. The raw DEV recount finds no accepted proof in any arm, including the shared start |
| [EXP-020](docs/EXPERIMENTS.md#exp-020) | Alternating SFT with RLP and Lean GRPO | `configs/midtrain/qwen_05b_interleaved_training_v3.json` | Done. Non-trivial validation statements proved at 8 attempts: initial 0/392, common SFT 13/392, SFT/GRPO 6/392, SFT/RLP 15/392 |
| [EXP-019](docs/EXPERIMENTS.md#exp-019) | Interactive Lean RL (fixed tasks before curriculum) | `scripts/train/run_tactic_rl.py`, [run config](configs/rl/qwen_05b_tactic_mixed_followup.json) | Done. Fixed-mixture v2 DEV: 26/48 accepted attempts against the parent's 18/48, single seed, no accepted composition attempt. See the [completion record](docs/EXPERIMENTS.md#fixed-mixture-midterm-completion) |
| [EXP-018](docs/EXPERIMENTS.md#exp-018) | Interleaved number-theory SFT | `configs/midtrain/qwen_05b_nt_interleaved_sft_v1.json` | Ready. Not run (needs GPU approval) |
| [EXP-017](docs/EXPERIMENTS.md#exp-017) | Midsem RL data (splits, SFT data, RL tasks) | `scripts/data/build_nt_splits.py`, `scripts/data/build_syntax_sft.py` | Done. Splits v3 and paired `nt_sft_v8` data: 5,812 train SFT examples and 245 SFT validation examples. No training in this build |
| [EXP-016](docs/EXPERIMENTS.md#exp-016) | Number-theory statement pool | `scripts/data/build_nt_pool.py` | Done. Pool v2: 4,306 Lean-checked statements (train 3,450, validation 420, test 436) |
| [EXP-015](docs/EXPERIMENTS.md#exp-015) | Lean-verified evaluation | `scripts/eval/lean_eval_statements.py`, `scripts/eval/probe_nt_pool.py` | Done for the SFT model. 0/200 Lean Workbook number-theory statements; one standard tactic proves 8/200 |
| [EXP-014](docs/EXPERIMENTS.md#exp-014) | Lean SFT on mathlib proofs | `configs/midtrain/qwen_05b_lean_sft_v1.json` | Done. Validation 2/43 theorems at 8 attempts; 0/17 number-theory theorems |
| [EXP-013](docs/EXPERIMENTS.md#exp-013) | Controlled RLP comparison | `configs/midtrain/qwen_05b_controlled_rlp.json` | Done. Both arms 0/18 fully correct; final core score 1.056 shuffled vs 0.500 ordered (sign test p = 0.18) |
| [EXP-012](docs/EXPERIMENTS.md#exp-012) | RLP corpus audit | `configs/data/mathematical_rlp_review_v1.json`, `scripts/audit/audit_rlp_corpus.py` | Done. All 612 numeric answers correct. 160/448 training rules lack domain or zero conditions |
| [EXP-011](docs/EXPERIMENTS.md#exp-011) | Local-action pilot | `configs/rl/qwen_05b_local_operator_grpo.json` | Done. Witness accuracy improved |
| [EXP-010](docs/EXPERIMENTS.md#exp-010) | Saved-checkpoint evaluations | `configs/eval/` | Done. No complete-answer gain |
| [EXP-009](docs/EXPERIMENTS.md#exp-009) | Primitive RLP curriculum | `configs/midtrain/qwen_05b_primitive_rlp.json` | Done. Held-out accuracy fell |
| [EXP-008](docs/EXPERIMENTS.md#exp-008) | FP32 RLP rerun | `configs/midtrain/qwen_05b_number_theory_rlp_fp32.json` | Done. No accuracy gain |
| [EXP-007](docs/EXPERIMENTS.md#exp-007) | RLP training + continuation | `configs/pretrain/qwen_05b_number_theory_rlp*.json` | Done. No accuracy gain |
| [EXP-006](docs/EXPERIMENTS.md#exp-006) | Assisted proof completion | `configs/rl/qwen_05b_completion_grpo.json` | Done. One theorem qualified; no accepted held-out attempt |
| [EXP-005](docs/EXPERIMENTS.md#exp-005) | Prerequisite curriculum GRPO | `configs/rl/qwen_05b_prerequisite_grpo*.json` | Done. Training skipped: no accepted attempt |
| [EXP-004](docs/EXPERIMENTS.md#exp-004) | Whole-proof GRPO | `configs/rl/qwen_05b_whole_proof_grpo.json` | Done. No learning signal |
| [EXP-003](docs/EXPERIMENTS.md#exp-003) | Instruct model proof check | `configs/eval/qwen_05b_probe.json`, `scripts/eval/probe_qwen.py` | Done. Lean accepted 0/63 local attempts. A GPU sampling run at four temperatures shows 0/273 in its exported records |
| [EXP-002](docs/EXPERIMENTS.md#exp-002) | Base model probe + GSM8K LoRA | `configs/eval/qwen_05b_probe.json` | Done. No accuracy gain |
| [EXP-001](docs/EXPERIMENTS.md#exp-001) | LeanDojo data subset | `configs/data/leandojo_*.json` | Done. Lean replay done: 5,921 of 8,273 training proofs accepted; task bank v2 keeps 2,150 theorems outside evaluation and dev files |
| [AUD-002](docs/EXPERIMENTS.md#aud-002) | Lean reward and engineering audit | `scripts/eval/check_checker_attacks.py`, `scripts/eval/bench_lean_verifier.py`, `scripts/eval/check_scoring_paths.py` | Scorer adoption appears in the decision log. Attack controls do not establish an exhaustive security guarantee |
| [AUD-001](docs/EXPERIMENTS.md#aud-001) | Proof failures and curriculum inventory | `scripts/audit/classify_proof_failures.py`, `scripts/audit/curriculum_inventory.py` | This is a post hoc diagnostic, not a registered curriculum-effect test |

</details>

Longer historical status cells remain in the [catalogue](docs/EXPERIMENTS.md#readme-status-text-before-condensing-20-october-2026).
Check each experiment's evidence before reusing a score. The catalogue's [10 October checkpoint](docs/EXPERIMENTS.md#adopted-inputs-and-next-actions) is historical.

## Setup

Use Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
From the repository root:

```bash
uv sync
uv run pre-commit install
uv run python scripts/data/build_nt_benchmark.py --help
```

This installs the small local environment and lets you inspect the benchmark command.
The `training` dependency group supplies `torch`, `transformers`, `trl`, `datasets` and `tensorboardX` for the Linux run host:

```bash
uv sync --locked --group training
```

The separate `local-probe` group targets the MLX probe and requires newer Transformers; uv prevents combining it with `training`.
The full unit suite (`uv run --group training python -m unittest discover -s tests`) needs ML packages.
Use the small checks in the [reproduction workflow](#reproduce-the-main-study) for a local checkout without them.

For proof checks, use the archived Linux Lean environment and its pinned mathlib revision; see [Not in Git](#not-in-git).
Installing [elan](https://lean-lang.org/install/manual/) alone does not supply the checker or mathlib snapshot.
Lean integration tests in `tests/test_tactic_rl_integration.py` need `TACTIC_RL_ROOT` and a new `TACTIC_RL_OUTPUT` directory.
The root must contain `lean_env_full/`, `proof_replay_v1/code/checker.py` and the REPL build under `rlvr_verifier/`.

For Markdown changes, run `uv run pre-commit run --files <changed-files>`.
Build the report with `latexmk -pdf -quiet -cd -interaction=nonstopmode -halt-on-error docs/tex/project_report.tex`.

## Layout

| Path | Contents |
| --- | --- |
| [scripts/](scripts/README.md) | Data builders, trainers, evaluations, audits and shared code |
| [configs/](configs/README.md) | Frozen settings, indexed by experiment |
| [tests/](tests/) | Unit tests and a separate Lean integration group |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | Protocols, evidence links, ownership and dated decisions |
| [docs/tex/](docs/tex/) | Main report, [setup brief](docs/tex/training_setup.pdf), TeX sources and figures |
| [docs/REWARD_HACKING.md](docs/REWARD_HACKING.md) | Reward exploits, fixes and remaining risks |
| [docs/archive/](docs/archive/) | Historical handoff; not maintained |
| [patches/](patches/) | Lean REPL patch for errors from aborted tactics |
| `artifacts/` | Ignored local files; data and results belong in remote storage |
| [AGENTS.md](AGENTS.md) | Agent instructions; `CLAUDE.md` links here |

## Curriculum

For the 18–19 October study, use `validated_v2`: 506 TRAIN theorems and 1,880 SFT examples.
Use `release_v3` for evaluation: 8 DEV theorems for choices and 21 TEST theorems reserved for final evaluation.
The separate 164-theorem selection set belongs to EXP-028. SFT loss validation also contains TRAIN theorems, so it is not DEV.
The [data handoff](docs/EXPERIMENTS.md#data-handoff-for-abhinav-20-october-2026-ist) gives provenance, filenames and fetch-and-verify steps.

Earlier inputs remain documented under [EXP-017](docs/EXPERIMENTS.md#exp-017), [EXP-019](docs/EXPERIMENTS.md#exp-019) and [EXP-025](docs/EXPERIMENTS.md#exp-025).
The public [Lean curriculum v8 release](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases/tag/lean-curriculum-v8) belongs to EXP-017.
The [benchmark v2 config](configs/data/nt_benchmark_v2.json) belongs to EXP-025; it predates `validated_v2`.

## Not in Git

A fresh clone cannot reproduce the full historical dataset build: several builders are private.
The [public rebuild](#recreate-datasets) creates a new dataset from upstream sources. The benchmark screen now excludes forbidden tactics and records its resolved policy; old selections containing `exact?` must be regenerated.
Fetch the frozen releases described above when reproducing the original study.

- Data, run outputs and logs: [archive contents and fetch steps](docs/EXPERIMENTS.md#6-step-by-step-fetch-and-verify-the-data). The recorded copies expire between 11 and 19 November 2026 unless moved to permanent storage.
- Lean runtime: archived `checker.py`, REPL binary and pinned environment. Scripts accept their paths through `--checker`, `--repl` and `--environment`; see [runtime files](docs/EXPERIMENTS.md#410-the-lean-runtime-environment-repl-checker-and-identity).
- Original main-run configs and recount scripts: private records under `.private/ops/`. The [run guide](docs/EXPERIMENTS.md#7-step-by-step-run-with-this-data-as-the-main-run-did) gives commands and settings; [reproduction_v1.json](configs/reproduction_v1.json) reconstructs the documented recipe.
- Model weights: [published checkpoints](#checkpoints) and the main study's [archived weights](docs/EXPERIMENTS.md#49-model-weights).

<a id="modify-the-pipeline"></a>

## Implementation

The [script guide](scripts/README.md#implementation-map) maps data selection, proof interaction, rewards and optimizer updates to their owning code.
Use those components when changing behavior. Changes to scientific behavior need a recorded experiment; see [AGENTS.md](AGENTS.md).

## Checkpoints

The following checkpoints belong to the earlier 5–9 October experiments. They are not S, R or C from the 18–19 October main study.
Their weights and tokenizer files are available through [GitHub Releases](https://github.com/RavulapalliManas/efficient-rl-small-formal-reasoners/releases).
The [download index](configs/checkpoints.json) records source files, asset sizes, and GitHub digests.
For the later study, use the [archived weights](docs/EXPERIMENTS.md#49-model-weights) and identities in [reproduction_v1.json](configs/reproduction_v1.json).

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

## Scripts

Run commands from the repository root. The [script guide](scripts/README.md) explains the main workflow, follows one proof attempt and indexes every script.
