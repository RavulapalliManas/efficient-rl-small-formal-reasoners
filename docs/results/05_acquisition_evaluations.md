# Acquisition evaluations of saved policies

This record moved verbatim from `README.md` on 2026-10-07. The [README status table](../../README.md#baselines-and-status) remains the canonical status record.

## Acquisition content evaluation

The saved-policy comparison does not establish a reliable acquisition gain from primitive RLP. Early RLP improves some answers; final RLP performs worse on constrained recognition and completed applications under the fixed response limits.

| Correct responses | Original Instruct | Early RLP (`checkpoint-118`) | Final RLP |
| --- | ---: | ---: | ---: |
| Concept recognition, repeated option orders | 19 / 24 | 19 / 24 | 17 / 24 |
| Definition and theorem recall, content review | 3 / 12 | 4 / 12 | 3 / 12 |
| Elementary application, content review | 3 / 12 | 4 / 12 | 0 / 12 |
| Elementary application, strict integer parsing | 3 / 12 | 3 / 12 | 0 / 12 |

These counts come from CPU `artifacts/acquisition_eval_v1/content_recount.json`, produced by archived `acquisition_eval_v1/code/review_recount.py`. The [frozen probe](../../configs/eval/acquisition_probe_v1.json) covers elementary number theory. Recognition repeats each concept with changed option order; those presentations are not independent knowledge items. A constant option label scores 6 / 24. A constant application answer of one scores 4 / 12. These baselines come from `recount.json` and its archived `code/recount.py`.

Content grading ignores answer formatting but requires all requested content and no substantive false claim. Two blinded AI reviewers agree on 71 / 72 responses. The remaining disagreement concerns whether prime-factorization uniqueness permits changing multiplicities. The stricter reading rejects it. The permissive reading raises final recall to 4 / 12 and leaves the application result unchanged. Both reviews, the source responses, and adjudication remain in `content_review/`. Agreement between AI reviewers does not establish independent human validation.

Early RLP gains two correct recall answers and loses one relative to the original model. It gains one correct application without losing a correct application. That application evaluates supplied Bezout coefficients; it does not demonstrate independent coefficient construction. The diagnostic does not isolate acquisition from changed response behavior.

The response limits bind, especially for the final policy. Final recall reaches its limit on 12 / 12 responses. Final applications reach their limit on 11 / 12 responses. Most final applications stop at an introduction or incomplete method. Zero completed correct applications therefore does not establish erased mathematical knowledge. Correct statements followed by false mathematics also occur, so parsing alone does not explain the errors.

This exploratory diagnostic was authored after historical failure analysis. It does not establish independence from training concepts or pretraining exposure. It does not evaluate proof semantics or proof strategy. All policies use the same CPU precision, chat template, greedy decoding, and frozen task-specific limits. No new training occurs. The next acquisition study needs a larger frozen item set and a preregistered response-length comparison before a general acquisition claim.

## Acquisition, proof semantics, and proof strategy

Neither saved RLP policy improves complete acquisition answers in this exploratory evaluation. The original policy completes 4/24 acquisition tasks under the revised content rubric. Each RLP checkpoint completes 2/24. Partial-credit totals also remain below the original. These observations concern this selected probe and response budget; they do not establish a general effect of RLP.

The [task config](../../configs/eval/decomposition_probe_v2.json) covers authored mathematical families with recall, explained application, proof-step checking, and complete proofs. The resolved remote manifest adds the public subsets recorded in its source manifest. Every task uses free generation with a maximum of 1,024 new tokens. The prompt limit is 2,048 tokens, with no prompt truncation. Execution uses matched FP32 CPU inference and greedy decoding. References and grading rubrics never enter the model prompt.

The [revised rubric](../../configs/eval/decomposition_grading_v2.json) accepts valid alternative proofs and conventional implicit steps. A complete core answer can retain credit despite a separable extra mistake. Errors required by the argument or contradicting its conclusion still reduce credit. The separate `full_correct` field excludes substantive mathematical errors anywhere in the answer.

| Complete core answers | Original Instruct | Early RLP (`checkpoint-118`) | Final RLP |
| --- | ---: | ---: | ---: |
| Definition and theorem recall | 1 / 12 | 0 / 12 | 1 / 12 |
| Explained application | 3 / 12 | 2 / 12 | 1 / 12 |
| Proof-step checking | 2 / 12 | 0 / 12 | 1 / 12 |
| Complete proof, unaided | 2 / 12 | 0 / 12 | 1 / 12 |
| Same proof, supplied facts | 4 / 12 | 0 / 12 | 3 / 12 |
| Same proof, facts and plan | 3 / 12 | 0 / 12 | 2 / 12 |
| ProcessBench selected subset | 0 / 4 | 0 / 4 | 0 / 4 |
| ProofNet-Verified selected subset | 0 / 4 | 0 / 4 | 0 / 4 |

Partial credit records valid progress even when an answer is incomplete or contains a central error. Each answer receives an ordinal score from zero to three. The following totals describe the rubric; they are not interval-scale measurements of knowledge.

| Partial-credit points | Original Instruct | Early RLP | Final RLP |
| --- | ---: | ---: | ---: |
| Definition and theorem recall | 15 | 16 | 13 |
| Explained application | 19 | 16 | 11 |
| Proof-step checking | 13 | 11 | 15 |
| Complete proof, unaided | 11 | 6 | 8 |
| Same proof, supplied facts | 14 | 9 | 14 |
| Same proof, facts and plan | 15 | 10 | 12 |
| ProcessBench selected subset | 3 | 3 | 5 |
| ProofNet-Verified selected subset | 2 | 1 | 4 |

Early RLP earns more partial credit on recall. Final RLP earns more partial credit on proof-step checking and the public subsets. Neither gain increases complete answers in those categories. The external samples are too small to support a benchmark-wide comparison. Both independent reviewers place acquisition completion below the original before adjudication.

Supplied facts improve complete-proof counts for the original and final policies. Adding a plan produces no further net gain. The early checkpoint completes no proof in these conditions. These contrasts locate difficulties in using facts and constructing valid deductions, but they do not isolate independent causal components. Facts can suggest a strategy, and a plan can reinforce knowledge. This study evaluates informal mathematics, not Lean syntax or formal proof acceptance.

| Generation record | Original Instruct | Early RLP | Final RLP |
| --- | ---: | ---: | ---: |
| Main responses | 80 | 80 | 80 |
| Generated tokens, including EOS | 40,123 | 32,975 | 63,735 |
| Responses reaching the output cap | 2 / 80 | 0 / 80 | 26 / 80 |

Truncation limits conclusions about latent knowledge, especially for the final checkpoint. Stored unaided-proof prefixes show no increase in complete core proofs between the shorter prefix and the full response at the declared limits. This prefix analysis does not test an increased generation cap. The separate [notation check](../../configs/eval/decomposition_notation_check.json) replaces ambiguous variable names in division uniqueness. It produces no complete proof in any checkpoint or assistance condition; partial progress remains recorded.

Two independent GPT-6 Astra reviewers grade checkpoint-blinded responses against references. A third Astra reviewer resolves disagreements against the saved mathematics. All reviewers use the same revised rubric. The independent reviews disagree on 44/321 real response views, including declared prefixes and the notation check. Both reviewers pass the same ten planted examples in each batch. These repeated controls do not measure accuracy on unseen answers. Scores remain AI judgments without human or Lean validation.

The rubric revision follows the user's request after original responses and prior strict grades were inspected. This is post hoc exploratory regrading. Both reviews, original strict grades, adjudications, full-correctness fields, and reviewer sensitivity counts remain preserved. Shared training concepts are intentional; lexical overlap checks do not establish semantic independence or unknown pretraining exposure.

Source: CPU `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/decomposition_eval_v2/core_summary.json`, produced by `decomposition_eval_v2/code/rl_core_summary.py`. Per-answer grades and evidence excerpts are in `core_review/`; raw responses and run records are in the checkpoint directories. The resolved protocol, source snapshots, and analysis code are in `decomposition_eval_v2/`. S3 prefix: `capstate/acq/formal_rl/decomposition_eval_v2/`. The final archive is `complete.tar.gz`.

Verification: inference and notation launchers return exit 0. `rl_core_review.py` passes for all checkpoint and notation batches. `rl_core_summary.py` passes the coverage, paired-count, matching, and recount checks. `uv run python -m unittest discover -s tests -p test_acquisition_eval.py -v` returns four passing tests. No training or paid GPU rental occurs.

## Knowledge inventory and local operations

Early RLP (`checkpoint-118`) is the selected training start. Preserve its unchanged weights as the no-training control. Original Instruct and final RLP remain diagnostic references. This inventory evaluates early and original; it does not rerun final RLP. The [staged plan](../research_plan.md#knowledge-inventory-and-assisted-execution-2026-10-05) separates supplied execution, acquisition, retention, and transfer.

The early model does not consistently execute operations even with definitions and method guidance supplied. This supports testing supplied execution before assistance fading. It does not establish why the model fails or which knowledge is absent.

| Inventory outcome | Original Instruct: complete core | Early RLP: complete core | Original: fully correct | Early: fully correct |
| --- | ---: | ---: | ---: | ---: |
| Recall | 2 / 16 | 1 / 16 | 0 / 16 | 0 / 16 |
| Explained recognition | 7 / 32 | 14 / 32 | 4 / 32 | 8 / 32 |
| Supplied use | 4 / 16 | 4 / 16 | 1 / 16 | 2 / 16 |
| Independent use | 3 / 16 | 3 / 16 | 2 / 16 | 1 / 16 |

Early RLP improves explained recognition on these repeated presentations. It does not improve application completion. This result supports a narrower recognition gain, not independent knowledge acquisition. These tasks differ from earlier probes; their scores must not be pooled.

0 / 80 early responses reach the output cap in `early_inventory_summary.json`. The original reaches the cap on 2 / 80 responses in `original_inventory_summary.json`. These summaries come from `code/rl_inventory_summary.py a` and `b`, respectively. Both commands finish with `status: PASS` and exit 0 after checking raw generation records, review identities, evidence excerpts, and adjudications.

| Early RLP: complete core answer | Recall | Explained recognition | Supplied use | Independent use |
| --- | ---: | ---: | ---: | ---: |
| Divisibility | 0 / 2 | 2 / 4 | 1 / 2 | 1 / 2 |
| Euclidean division | 0 / 2 | 3 / 4 | 0 / 2 | 1 / 2 |
| Greatest common divisor | 0 / 2 | 3 / 4 | 0 / 2 | 0 / 2 |
| Bezout identities | 1 / 2 | 0 / 4 | 0 / 2 | 0 / 2 |
| Congruence | 0 / 2 | 1 / 4 | 1 / 2 | 0 / 2 |
| Modular inverse | 0 / 2 | 1 / 4 | 2 / 2 | 1 / 2 |
| Euclid's lemma | 0 / 2 | 3 / 4 | 0 / 2 | 0 / 2 |
| Modular cancellation | 0 / 2 | 1 / 4 | 0 / 2 | 0 / 2 |

Family counts come from `artifacts/knowledge_inventory_v1/early_inventory_summary.json`, recomputed by archived `knowledge_inventory_v1/code/rl_inventory_summary.py a`. Complete core answers can contain separable ancillary errors; full correctness excludes them. Both independent reviews, partial scores, and adjudications remain in `review/`. Reviewers inherit the session model, whose exact identifier is unavailable. The historical Astra label in the reused rubric does not identify these reviewers. Planted examples check grading consistency; they do not estimate grading accuracy. These are AI judgments, not human or Lean validation.

The generation limit is 1,024 new tokens per response, as frozen in `configs/eval/knowledge_inventory_v1.json`. Recognition requires an explanation; it is not a single-token test. Recognition rotates the same question, so its presentations are not independent concepts. Supplied use adds method guidance as well as facts. Family samples are small. These development tasks guide the curriculum and cannot serve as fresh held-out transfer evidence.

The [local-operation adapter](../../scripts/eval/verify_local_operator.py) validates submitted polynomial witnesses in immutable Lean obligations. Its fixtures cover supplied-witness reuse, composition, integer linear combination, and scaling. It does not yet train the policy or grade model-generated proof steps.

16 / 16 witness controls match their expected result in `operator_controls_retry4/verification.jsonl`, recounted by `code/rl_operator_recount.py`. Correct witnesses are accepted and incorrect witnesses are rejected. The environment uses Lean `v4.10.0-rc1` with mathlib `29dcec074de168ac2bf835a77ef68bbe069194c5`. The mathlib revision comes from installation provenance, not a live Git checkout. Failed setup attempts remain preserved.

4 / 4 direct early-model responses fail the expression-only parser in `operator_probe_verified/summary.json`, produced by `code/rl_score_operator_probe.py`. This is a format result, not mathematical failure on every response. The separate `operator_content_check.json` records an explicit valid boxed witness and counterexamples to other submitted expressions. It does not repair responses or certify their explanatory prose. This greedy probe uses training fixtures and does not establish sampled reward variation.

Training remains pending. The next step is a declared extraction contract that preserves the submitted mathematical object, followed by sampled readiness. Freeze validation, retention, and withheld-composition manifests before optimizer updates. Reconcile the remaining compute budget before a paid launch. Keep the binary mathematical reward separate from format and infrastructure outcomes.
