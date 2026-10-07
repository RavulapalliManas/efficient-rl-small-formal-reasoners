# Local-action correctness pilot

This record moved verbatim from `README.md` on 2026-10-07. The [README status table](../../README.md#baselines-and-status) remains the canonical status record.

## Local-action correctness pilot

64 optimizer steps complete from early RLP (`checkpoint-118`) with verified-witness reward. `operator_pilot_v1/code/rl_operator_result_recount.py` recounts the raw records on CPU. Both the worker and recount return exit 0. The selected checkpoint is `runtime/out/policy_step_64/` under S3 `capstate/acq/formal_rl/operator_pilot_20261006/`.

All responses below are standalone generations. Lean checks submitted witnesses after generation; it supplies no inference-time feedback. Lean-assisted search performance remains unmeasured. Each evaluation allows at most 1,024 new tokens.

| Endpoint | Unchanged early RLP | Selected checkpoint |
| --- | ---: | ---: |
| Validation: strict extraction plus Lean acceptance | 0 / 8 | 6 / 8 |
| Validation: correct mathematical witness, independent content review | 0 / 8 | 7 / 8 |
| Validation: response without substantive mathematical error | 0 / 8 | 2 / 8 |
| Held-out composition: strict extraction plus Lean acceptance | 0 / 4 | 2 / 4 |
| Held-out composition: correct mathematical witness, independent content review | 1 / 4 | 2 / 4 |
| Held-out composition: response without substantive mathematical error | 0 / 4 | 0 / 4 |

The strict scores come from `gpu_records/out/recount.json`. The content scores come from `gpu_records/out/content_review.json`, produced by `code/rl_operator_content_review.py`. Independent review and root source inspection cover every paired response. This review is checkpoint-visible and exploratory; it is not a blinded or human-validated evaluation. It accepts conventional implicit multiplication and correct content outside the required final-answer format. The unchanged policy already states the correct sum witness in a held-out response, despite an uninformative final box.

The validation gain includes corrected mathematics, not only formatting. The trained model still drops factors on harder compositions. Several valid witnesses accompany division by a possibly zero quantity. Witness verification does not certify those explanations. These supplied-equality tasks support a narrow execution result; they do not establish independent knowledge acquisition or reliable full proofs. The held-out sample is too small for a general reliability claim.

145,873 generated tokens are recounted across the GPU run and CPU development samples. 408 physical Lean calls are recounted over the same scope. The raw records and separate phase costs remain in `recount.json`. Worker wall time includes generation, optimization, evaluation, and Lean; GPU-active time was not separately instrumented. The budget ledger records a conservative cumulative reservation, not billed spend.

The first informative update changes sampled weights with FP32 optimizer moments. Every step checks trainable parameters for nonfinite values. The unchanged early model remains preserved. Checkpoint selection uses the strict verified-action validation score, with earliest ties; full-response correctness is a separate post hoc endpoint. Periodic uploads encounter two transient `run.json` size races. The final stable run record, checkpoints, and exit record are archived.

Next: test valid local proof steps and explicit zero cases while mathematical information remains supplied. Defer the larger information-removal comparison until those checks improve. Preserve the standalone and Lean-assisted deployment endpoints separately.
