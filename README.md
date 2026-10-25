# Reinforcement learning for Lean proofs with a small language model

<a id="efficient-rl-for-small-formal-reasoners"></a>

We test whether reinforcement learning (RL), after supervised fine-tuning (SFT), helps `Qwen/Qwen2.5-0.5B-Instruct` write number-theory proofs.
The model proposes proof steps; Lean 4 checks the complete proof.

<a id="read-this-first"></a>

[Research report](docs/tex/project_report.pdf) · [Script guide](scripts/README.md) · [Experiment records](docs/EXPERIMENTS.md)

<a id="status"></a>

## Study status

Experiments ended on 20 October 2026, without an established RL gain on unseen theorems.

- The unchanged, SFT, shuffled-RL and prerequisite-ordered-RL models each proved **0/8 development theorems**. This set cannot resolve modest differences.
- On the exploratory selection set, SFT proved **1/163** theorems, shuffled RL **1/164**, and filtered RL **2/164**. An automation timeout left one SFT theorem unassessed.
- Prerequisite-ordered RL accepted **255/4,048 training attempts**, versus **164/4,048** for shuffled RL. Each arm had one run; training gains do not establish transfer.

No variant met the promotion rule: at least a 5 percentage-point gain over its control, with a paired 95% interval excluding zero.
The [report](docs/tex/project_report.pdf) gives methods and limitations. The [execution record](docs/EXPERIMENTS.md#execution-record) covers all 32 studies, including two follow-ups that did not run.

<a id="setup"></a>

## Local setup

Install Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from the repository root:

```bash
uv sync --locked
uv run python scripts/reproduce.py overview
uv run python -m unittest tests.test_reproduction tests.test_rl_components
```

These checks need no GPU, model weights or Lean installation.
For training dependencies and Lean integration tests, see [Runtime and development](scripts/README.md#runtime-and-development).

<a id="curriculum"></a>

<a id="reproduce-the-main-study"></a>

## Study reproduction

Follow the [reproduction guide](scripts/README.md#main-study-reproduction). It requires external data, checkpoints and the pinned Linux Lean runtime; some builders remain private.
The helper checks files and prepares commands without running training or reproducing results.

<a id="recreate-datasets"></a>

## Dataset generation

The [dataset workflow](scripts/README.md#dataset-recreation) verifies candidates from pinned sources and exports an exploratory release.
It cannot reconstruct the original 506-theorem release or its action examples.

<a id="repository-layout"></a>
<a id="layout"></a>
<a id="documentation"></a>
<a id="scripts"></a>
<a id="implementation"></a>
<a id="modify-the-pipeline"></a>

## Code and documentation

| Find | Start here |
| --- | --- |
| Workflow commands | [Script guide](scripts/README.md#primary-workflow) |
| Proof interaction, rewards and updates | [Implementation map](scripts/README.md#implementation-map) |
| Configs and checkpoint index | [Config guide](configs/README.md) |
| Experiments and evidence | [Experiment index](docs/EXPERIMENTS.md) |
| Report source and figures | [docs/tex/](docs/tex/) |
| Historical verifier audit | [Lean verifier audit](docs/REWARD_HACKING.md) |
| Tests | [tests/](tests/) |

<a id="terms"></a>

See the [glossary](scripts/README.md#terms) for model and metric definitions.

<a id="not-in-git"></a>
<a id="checkpoints"></a>

## Data and checkpoints

Data, proof traces, weights and logs are external. See [archive retrieval](docs/EXPERIMENT_HISTORY.md#6-step-by-step-fetch-and-verify-the-data).
Recorded archive copies expire on 11–19 November 2026 unless moved to permanent storage.
[Published checkpoint releases](scripts/README.md#checkpoints) belong to earlier experiments, not the main study.
