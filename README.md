# Reinforcement learning for Lean proofs with a small language model

<a id="efficient-rl-for-small-formal-reasoners"></a>

This project studies reinforcement learning (RL) for number-theory proofs with `Qwen/Qwen2.5-0.5B-Instruct`.
The model proposes proof steps; Lean 4 checks the complete proof.

<a id="read-this-first"></a>

[Research report](docs/tex/project_report.pdf) · [Script guide](scripts/README.md) · [Experiment records](docs/EXPERIMENTS.md)

## Status

Experiment execution stopped on 20 October 2026. The recorded results do not establish better proof performance on unseen theorems after RL.

- The unchanged model, supervised fine-tuning (SFT), shuffled RL and prerequisite-ordered RL each prove 0 of 8 development theorems. This set is too small to resolve modest differences.
- On the separate selection set, SFT proves 1 of 163 assessed theorems, shuffled RL 1 of 164, and filtered RL 2 of 164. No variant meets the promotion rule. One SFT theorem was not assessed after an automation timeout.
- Prerequisite-ordered RL accepts 255 of 4,048 training attempts, against 164 for shuffled RL, with one training run per arm. These training results do not establish transfer.

Promotion requires at least a 5 percentage-point gain over the control, with a paired 95% interval excluding zero.
The [report](docs/tex/project_report.pdf) explains the comparisons and limits.
The [execution record](docs/EXPERIMENTS.md#execution-record) covers all 32 experiments and audits, including the two follow-ups that did not run.

## Setup

Install Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from the repository root:

```bash
uv sync --locked
uv run python scripts/reproduce.py overview
uv run python -m unittest tests.test_reproduction tests.test_rl_components
```

These local checks need no GPU, model weights or Lean installation.
See [Runtime and development](scripts/README.md#runtime-and-development) for training dependencies and Lean integration tests.

<a id="curriculum"></a>

## Reproduce the main study

Follow the [reproduction procedure](scripts/README.md#main-study-reproduction) for the 18–19 October comparison of the unchanged model, SFT and two RL orders.
Original data, checkpoints and the pinned Linux Lean runtime are external. Some original dataset builders are private.
The reproduction helper checks supplied files and prepares commands; it does not launch training or reproduce a result by itself.

## Recreate datasets

The [public dataset workflow](scripts/README.md#dataset-recreation) fetches pinned upstream sources, verifies proof candidates and exports a new exploratory release.
It does not reconstruct the historical 506-theorem training release or its action examples.

<a id="repository-layout"></a>
<a id="layout"></a>
<a id="documentation"></a>
<a id="scripts"></a>
<a id="implementation"></a>
<a id="modify-the-pipeline"></a>

## Code and documentation

| Find | Start here |
| --- | --- |
| Data, training and evaluation commands | [Script guide](scripts/README.md#primary-workflow) |
| Proof interaction, rewards and policy updates | [Implementation map](scripts/README.md#implementation-map) |
| Run settings and checkpoint download index | [Config guide](configs/README.md) |
| Experiment summaries and evidence links | [Experiment index](docs/EXPERIMENTS.md) |
| Report source and figures | [docs/tex/](docs/tex/) |
| Historical verifier exploits and controls | [Lean verifier audit](docs/REWARD_HACKING.md) |
| Local and Lean integration checks | [tests/](tests/) |

<a id="terms"></a>

The [script glossary](scripts/README.md#terms) defines training variants, actions, attempts and proved theorems.

<a id="not-in-git"></a>
<a id="checkpoints"></a>

## Data and checkpoints

Data, proof traces, weights and run logs live outside Git. Use the [archive retrieval procedure](docs/EXPERIMENT_HISTORY.md#6-step-by-step-fetch-and-verify-the-data).
Recorded archive copies expire between 11 and 19 November 2026 unless moved to permanent storage.
[Published checkpoint releases](scripts/README.md#checkpoints) belong to earlier experiments, not the main study.
