# Qwen capability probe

This record moved verbatim from `README.md` on 2026-10-07. The [README status table](../../README.md#baselines-and-status) remains the canonical status record.

## Qwen capability probe

The earlier capability probe used the user-selected `Qwen/Qwen2.5-0.5B` base. The [probe config](../../configs/eval/qwen_05b_probe.json) pins revision `060db6499f32faf8b98477b0a26969ef7d8b9987`. The local runner uses original BF16 weights through [MLX-LM](https://github.com/ml-explore/mlx-lm), without a chat template or quantization. This execution followed the user's earlier request for local work. These historical results are not the baseline for the planned Instruct RL comparison.

The [official Qwen report](https://arxiv.org/html/2412.15115v2) reports base-model math results in Table 5. Local [source extraction](../../artifacts/qwen_05b_probe/research/public_scores.json) preserves the columns and values. These results do not measure Lean proof capability or isolate an RL effect. Public scores cannot establish exact pretraining exposure.

The exploratory pilot uses fixed samples from public GSM8K and MATH number theory. It uses training examples for four-shot prompts and preserves public test membership. MATH sampling balances its supplied difficulty levels. [Math-Verify](https://github.com/huggingface/Math-Verify) scores boxed math answers. GSM8K scoring requires the final `####` answer. Missing answer markers count as incorrect. Raw responses remain available for format analysis.

The earlier base formal diagnostic uses curriculum-validation declaration headers without gold proofs. Its header completions remain unverified. The separate Instruct diagnostic below reconstructs initial goals and executes Lean. The frozen project test set remains untouched.

The [SFT config](../../configs/midtrain/qwen_05b_gsm8k_lora.yaml) specifies one fixed LoRA probe on separate GSM8K training questions. Its diagnostic validation also comes from the public training split. It does not train on formal validation or public test solutions. A change after this probe measures supervised adaptation under this configuration, not RL readiness.

```sh
uv sync --locked --group local-probe
uv run --group local-probe hf download Qwen/Qwen2.5-0.5B \
  --revision 060db6499f32faf8b98477b0a26969ef7d8b9987 \
  --local-dir artifacts/qwen_05b_probe/model --exclude .gitattributes
uv run --group local-probe python scripts/eval/probe_qwen.py prepare
uv run --group local-probe python scripts/eval/probe_qwen.py evaluate \
  --output artifacts/qwen_05b_probe/base
uv run --group local-probe mlx_lm.lora \
  --config configs/midtrain/qwen_05b_gsm8k_lora.yaml
uv run --group local-probe python scripts/eval/probe_qwen.py evaluate \
  --adapter artifacts/qwen_05b_probe/sft_adapter \
  --output artifacts/qwen_05b_probe/after_sft
```

The model download must use the revision in the probe config. Preparation and evaluation refuse existing output directories. Use fresh paths for reproduction. The canonical status table records completed runs; planned commands do not establish results.

### Pilot results

The [paired comparison](../../artifacts/qwen_05b_probe/comparison.json) recounts raw responses and checks identical questions and prompts. The [analysis script](../../artifacts/qwen_05b_probe/analyze.py) also scores a constant-zero diagnostic. This constant baseline was added after the run and does not constitute a registered comparison.

| Final-answer metric | Base | After GSM8K SFT | Constant zero | Questions |
| --- | ---: | ---: | ---: | ---: |
| GSM8K correct answers | 9 | 8 | 0 | 24 |
| MATH number theory correct answers | 3 | 3 | 1 | 20 |

These are single-seed sample counts, not full public benchmark scores. The cap is 512 generated tokens per question. Some attempts reach that cap. A correct final answer does not establish a correct derivation.

64 training questions feed the [LoRA probe](../../artifacts/qwen_05b_probe/sft_run.json). 32 optimizer updates complete the fixed budget. 11,471 teacher-forced tokens are recorded in [training events](../../artifacts/qwen_05b_probe/sft_events.jsonl). These tokens differ from generated RL rollouts; no RL rollout occurs.

| Full-text diagnostic validation loss | Value | Completed updates | Questions |
| --- | ---: | ---: | ---: |
| Initial | 0.701403 | 0 | 16 |
| Last recorded validation | 0.514280 | 31 | 16 |

The upstream trainer evaluates before the current update. Its last validation therefore precedes the final update. This loss covers questions and solutions together. It does not measure answer-only loss or proof quality. The reduced loss does not accompany a final-answer accuracy gain in this pilot.

The bounded launch wrapper is [run_sft.py](../../artifacts/qwen_05b_probe/run_sft.py). It enforces the wall-time cap and records training callbacks. The first launch fails before training because the callback wrapper rejects a positional argument. [The failed launch](../../artifacts/qwen_05b_probe/failed_sft_launch/sft.log) remains preserved. The corrected launch uses the same training budget.

The [base responses](../../artifacts/qwen_05b_probe/base/responses.jsonl) and [SFT responses](../../artifacts/qwen_05b_probe/after_sft/responses.jsonl) preserve generated text and measured token counts. Their formal headers remain unverified. These results establish some informal math capability and text adaptation. They do not establish Lean readiness, an RL benefit, curriculum efficiency, or pretraining exposure.

### Instruct proof diagnostic

The bounded diagnostic tests the original `Qwen/Qwen2.5-0.5B-Instruct` weights with the checkpoint's chat template. The [run record](../../artifacts/qwen_05b_instruct_proof/run.json) pins the model, Lean, mathlib, seeds, and limits. The project still selected the base checkpoint when this diagnostic ran. The new plan selects Instruct for a future interactive comparison; this earlier whole-proof diagnostic is not its matched baseline.

The [recount](../../artifacts/qwen_05b_instruct_proof/summary.json) reads raw generation and verifier records. Each formal goal receives one greedy attempt and two sampled attempts. The formal cap is 512 generated tokens per attempt. The informal cap is 768 generated tokens per question.

| Diagnostic | Result | Denominator |
| --- | ---: | ---: |
| Project validation goals solved within three attempts | 0 | 17 goals |
| Elementary formal goals solved within three attempts | 0 | 4 goals |
| Formal attempts accepted by Lean | 0 | 63 attempts |
| Formal responses reaching the token cap | 23 | 63 attempts |
| Informal proofs judged sound and complete | 1 | 8 responses |
| Informal proofs with a valid core and false explanations | 2 | 8 responses |
| Informal proofs judged invalid or incomplete | 5 | 8 responses |

21,358 generated tokens are counted from [responses](../../artifacts/qwen_05b_instruct_proof/responses.jsonl). Lean accepts the positive control and rejects the false-statement control in [controls](../../artifacts/qwen_05b_instruct_proof/controls.json). Acceptance requires the original target, successful compilation, and permitted transitive axioms. Candidate verification records distinguish forbidden constructs from compiler returns. No candidate verifier call times out.

The [informal review](../../artifacts/qwen_05b_instruct_proof/informal_review.json) records case judgments from two AI reviewers. It is not a calibrated coherence score. Divisibility transitivity supplies a complete argument. The gcd response uses a false remainder identity. The infinitude-of-primes response uses a false algebraic identity. Formal responses often supply invalid imports, invented names, or repeated statements.

This small diagnostic establishes failures under these prompts and budgets. It does not establish a capability ceiling, a held-out benchmark score, or an RL effect. The [runner](../../artifacts/qwen_05b_instruct_proof/probe.py) and [analysis](../../artifacts/qwen_05b_instruct_proof/analyze.py) preserve reproduction details. The initial elementary import failures remain preserved separately from model results.
