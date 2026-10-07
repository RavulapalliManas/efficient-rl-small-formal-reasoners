# RLP number-theory acquisition

This record moved verbatim from `README.md` on 2026-10-07. The [README status table](../../README.md#baselines-and-status) remains the canonical status record.

## RLP TensorBoard views

The RLP continuation uses the same [local TensorBoard address](http://localhost:6006/#scalars). Its CPU server is `formal-rl-rlp-tb-server`; its exporter is `formal-rl-rlp-tb-exporter`. Both have bounded memory and runtime. Events live in `tensorboard_rlp_20261004/events/` under the CPU project root. The `rlp/progress` capability curves use cumulative generated training tokens, including the parent acquisition stage. These evaluations supply an answer-format prefix. Earlier proof-study events remain under `history/`. The scalar endpoint confirms the RLP views are served.

## Saved RLP checkpoint

The final model and tokenizer are at `s3://<project-bucket>/capstate/acq/formal_rl/number_theory_rlp_20261004_continuation/runtime/out/policy_final/`. The CPU copy is `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/number_theory_rlp_v2/audit_final/saved_model/`. Load this directory through `AutoModelForCausalLM.from_pretrained` and `AutoTokenizer.from_pretrained` for the next proof-RL stage. `audit_final/model_manifest.json` records file sizes and hashes. Optimizer and EMA state remain in the S3 trainer checkpoints under `runtime/out/policy/`.

127,389 generated training tokens cover this continuation in `audit_final/summary.json`. 245,361 generated training tokens cover the parent and continuation together. Evaluation tokens remain separate. The audit checks raw token IDs and the scheduled order against the frozen context list. S3 `audit/` preserves the recount code, summary, manifest, log, and exit code.

| Assisted diagnostic | Before continuation | After continuation |
| --- | ---: | ---: |
| Held-out arithmetic and prime questions | 11 / 48 | 11 / 48 |
| Prime curriculum validation | 7 / 16 | 7 / 16 |

All intermediate prime-validation scores also remain unchanged. These are small authored diagnostics with a supplied answer-format prefix. They are not public undergraduate benchmark scores. The checkpoint is available for further RL; this experiment does not establish that mathematical acquisition is complete or that only Lean conventions remain to learn.

## Precision correction for RLP midtraining

The CPU audit compares every stored parameter between the parent and continuation. Its `summary.json` records 8,557,971 changed entries out of 494,032,768. Most entries remain unchanged. The optimizer checkpoint contains BF16 moments without FP32 master parameters. On identical saved weights with constant nonzero gradients, the BF16 control changes 660 of 65,536 entries. The FP32 control changes every sampled entry. Both zero-gradient controls leave weights unchanged. These controls demonstrate numerical update loss; they do not replay historical gradients or establish its contribution to the math score.

The behavior recount finds one corrected answer and one regression. Mean reasoned log probability decreases on the fixed textbook validation positions. Training groups contain reward variation, but positive sampled rewards do not establish improvement. `artifacts/number_theory_rlp_v2/update_audit/` on cpu-angad holds `tensors.jsonl`, `summary.json`, `behavior.json`, and the analysis code.

The correction repeats the continuation from its original parent policy and teacher, with the same data, seed, curriculum, reward, and registered limits. It treats acquisition as midtraining. The only experimental intervention is parameter and computation precision. Checkpoint storage allocation and update diagnostics are operational changes. Preserve the previous run as a precision-confounded null result.

The corrected run is complete. CPU `artifacts/number_theory_rlp_fp32_midtraining/audit_final/summary.json` records the independent recount. Its `comparison.json` pairs before/after answers and prediction records. Both recount commands exit 0. The first real update confirms FP32 optimizer moments and changed sampled weights. These checks establish functioning updates, not mathematical acquisition.

| Assisted diagnostic | Before corrected run | After corrected run |
| --- | ---: | ---: |
| Held-out arithmetic and prime questions | 11 / 48 | 11 / 48 |
| Prime curriculum validation | 7 / 16 | 6 / 16 |

One corrected held-out answer is offset by one regression. The precision settings change some baseline responses, despite identical aggregate baseline accuracy. Compare each run against its own baseline. Mean reasoned target log probability improves on the fixed textbook validation positions, but this does not produce a measured mathematical accuracy gain. These authored diagnostics do not establish undergraduate proof capability.

125,411 generated training tokens cover the corrected continuation in `summary.json`. Evaluation tokens are counted separately. The saved model is CPU `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/number_theory_rlp_fp32_midtraining/audit_final/saved_model/`. Its S3 copy is `capstate/acq/formal_rl/number_theory_rlp_20261004_fp32_midtraining/runtime/out/policy_final/`. Milestone checkpoints retain optimizer and teacher states. This model is a preserved experimental output; acquisition remains incomplete.

The next study prioritizes [primitive learning, retention, and composition](../research_plan.md#primitive-based-midtraining-2026-10-05). It specifies coherent derivations, theorem conditions, counterexamples, and independent tests. It remains planned. The completed precision experiment and its data remain unchanged.

## Revised primitive curriculum

448 unique training examples appear in CPU `primitive_midtraining_v1/prepared_ordered/manifest.json`. Replay expands the registered order to 596 presentations. The same manifest records 56 curriculum-validation items and 108 held-out items. The held-out set includes new instances and reserved witness-composition templates. Shared primitive templates cross splits deliberately; this is not a public benchmark or complete proof evaluation.

The curriculum covers witness construction, substitution, counterexamples, division, gcd, Bezout coefficients, modular inverses, primes, congruences, and computational parts of induction and contradiction. Dependencies precede their dependents. The rejected first draft remains at `primitive_midtraining_v1/prepared/`; its within-stage shuffle violated that ordering.

The new runner starts from the pinned original Instruct model with FP32 parameters and optimizer moments. Its reward measures the mean log-probability gain of a hidden mathematical continuation span. This is a task-conditioned RLP adaptation. Training definitions are assistance. Baseline and final evaluation remove that assistance; separate validation records measure performance with definitions. The prompt comparison occurs before updates within the registered lease. Checkpoints and raw records stream to S3.

The dashboard is [TensorBoard on this Mac](http://localhost:6006/#scalars). The current server is the RLP server named above; `formal-rl-tensorboard-prerequisite` is the earlier diagnostic session. Recorded event files remain under `/mnt/data/manasvenkatasairavulapalli/formal_rl/tensorboard_pilot_20261003/`. The [exporter](../../scripts/monitor/track_tensorboard.py) reads streamed S3 records. Earlier curves show proof sampling and GRPO logs. `prerequisite_20261004_live/progress` shows the completed readiness counters. It contains no invented loss or gradient values. Select the step axis because imported records use their monitoring time. Updates include the S3 synchronization delay. Diagnostic curves do not establish an RL gain.

The server and exporter use separate bounded CPU services. The reused environment's `requirements_resolved.txt` records installed packages. Private `sources.json` files contain expiring signed URLs and stay outside archives. If the tunnel disconnects, reconnect with:

```sh
ssh -N -o ExitOnForwardFailure=yes -i <ssh-key> \
  -L 127.0.0.1:6006:127.0.0.1:16006 \
  <ssh-user>@<cpu-angad-external-ip>
```
