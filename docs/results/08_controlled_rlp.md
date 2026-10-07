# Controlled mathematical RLP

This record moved verbatim from `README.md` on 2026-10-07. The [README status table](../../README.md#baselines-and-status) remains the canonical status record.

## Controlled RLP pretraining preparation

The implementation keeps the existing RLP objective and starts the controlled arms from early RLP checkpoint-118. It changes the admitted mathematical targets, information supplied in prompts, and independent evaluation. It introduces no Lean integration or model-grader reward. This preparation preceded GPU execution; the subsequent [ordered-arm results](#ordered-arm-results) report its measured outcome. The user authorized the bounded GPU comparison on 2026-10-06. The launch config enables training under the cumulative $95 ceiling.

The source is [mathematical_rlp_data.py](../../scripts/data/mathematical_rlp_data.py), called through the existing preparer. Records include assumptions, goals, knowledge identifiers, operations, reference steps, and dependencies. Mathematical review covers universal templates; exact arithmetic checks cover their declared numeric certificates. Neither is Lean certification or human validation.

72 canonical training instances appear in CPU `preparation_check_v2/report.json`. The same report counts 18 curriculum-validation instances and 29 final-test instances. Each arm receives 432 presentations, including identical replay occurrences. Five views supply full information, partial information, recognition alternatives, domain-cued recall, or an independent problem. This first study compares order; it does not implement the full knowledge-by-structure factorial experiment.

The final test reserves a composition template that combines known operators. Ordinary proof templates remain shared across splits. Exact instance checks find no cross-split duplicate, while numeral-normalized statements expose shared templates. Recall repeats trained facts and measures access to those facts. Unknown pretraining exposure prevents a claim that the model acquires wholly new concepts.

The [grading contract](../../configs/eval/mathematical_grading_v1.json) accepts valid alternate proofs and conventional elementary steps. It separates facts, conditions, operations, conclusions, partial progress, and full correctness. Blinded reviewers receive references but no checkpoint labels or other reviews. Unknown grades remain explicit. Disagreements require mathematical evidence; agreement does not establish truth.

24 authored controls appear in `calibration_recount/report.json`. Each reviewer makes no false acceptance or false rejection on these controls. The adjudication records six diagnostic-field disagreements and preserves corrected partial-credit gold separately. These short development controls do not validate long-proof grading. The summaries count unknowns and truncations and support paired comparisons through [grade_mathematical_responses.py](../../scripts/eval/grade_mathematical_responses.py).

The runner generates complete answers and scores a private reference span. The generation limit is 1,024 new tokens. Reference spans contain multiple tokens; they are not single-number targets. A tokenizer return-type defect initially corrupted the prompt count. The original inspection remains preserved; corrected inspections explicitly request token lists. The first reference export also used ambiguous recall topic names. Independent grading identified this defect. `data_v2/` clarifies the integer topics, and affected recall responses are regenerated. Unchanged responses retain their original provenance.

The [resolved configuration](../../configs/midtrain/qwen_05b_controlled_rlp.json) fixes the primary metric, decision threshold, retention rule, output limits, and resource caps. The primary score averages unassisted full correctness equally across concepts. A gain of 0.10 is an exploratory progression threshold, not a statistical power claim. Delayed inverse retention requires a nonzero starting success count and no lost successes after the common unrelated interlude. Selection uses validation correctness and retention, with earliest ties. Final-test quality stays sealed until selection.

CPU artifacts live at `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/mathematical_pretraining_v1/`. `data_v2/` is the active export; `data/` remains the original export. The resolved retention package is `data_v2/retention/`. Its config remains disabled until a selected descendant and its hash exist. No delayed-retention result exists before intervening updates.

The source checks run on cpu-angad from `pretraining_v1/code/`:

```sh
python3 -m unittest discover -s tests -p 'test_mathematical*.py' -v
python3 tests/test_rlp.py
```

The CPU baseline finishes under an 8 GiB memory cap. The inference sessions are `formal-rl-pretrain-baseline-v1` and `formal-rl-recall-v2`; both finish with exit 0. The scorer session `formal-rl-scorer-cpu-v1` also finishes with exit 0. No optimizer update or GPU rental occurs in this preparation.

The unchanged early baseline gives the following full-correctness counts in `baseline_validation_summary_v2/report.json`:

| Validation condition | Fully correct / evaluated |
| --- | ---: |
| Independent use | 1 / 18 |
| Facts and method supplied | 3 / 18 |
| Domain-cued rule recall | 1 / 8 |
| Recognize a rule and apply it | 1 / 8 |

The primary concept-macro score is 0.03125 in that report. This averages concepts equally; divisibility has more instances than the other concepts. No validation response reaches the generation cap. Both reviewers agree on full correctness. Diagnostic adjudications preserve unresolved partial-credit boundaries and first-error segmentation. The recognition condition includes application, so its full-correctness count is not isolated multiple-choice accuracy. These results use a new authored task set and are not directly comparable with earlier probe percentages.

The private scorer completes six CPU forwards in `scoring_check/report.json`. Its target spans contain multiple tokens and produce finite log probabilities. An explicitly false prefix increases target likelihood in both inspected examples, as recounted in `scoring_diagnostic.json`. The diagnostic uses CPU FP32 arithmetic. It does not establish positive group-relative advantage or explain historical degradation. The result reinforces the need for independent mathematical evaluation; correct targets do not turn RLP into a correctness verifier.

The preparation snapshot `readiness.json` marked CPU preparation complete and training not ready. Its authorization check rejected an unfunded training invocation before loading a model or creating its output directory. Funding and the ordered run followed this snapshot; see the results below. Delayed retention remains unmeasured. The final-test responses stay sealed in `baseline_heldout_v2/`. The preparation archive is S3 `capstate/acq/formal_rl/mathematical_pretraining_v1/complete.tar.gz`.

The focused checks return `Ran 13 tests ... OK` for `test_mathematical*.py` and `Ran 5 tests ... OK` for `test_rlp.py`. Both commands exit 0. Independent unequal-family controls also check macro weighting and heterogeneous paired changes. Final CPU recount returns `cpu_preparation_complete: true`, confirms the disabled-training guard, and exits 0.

The approved comparison launched on instance `<ec2-instance-id>` in `us-east-2`. GPU tmux was `formal-rl-controlled`; the independent cpu-angad guard was `formal-rl-controlled-lease-20261006`. Arm outputs use separate `runtime/out/ordered/` and `runtime/out/shuffled/` paths under S3 `capstate/acq/formal_rl/controlled_rlp_20261006/`. The launch receipt, price quote, deadline, and cumulative reservation ledger remain beside them. The first ordered update changed 68,032 of 68,096 sampled parameters, with FP32 Adam moments. The group had nonzero reward spread. These checks verified an update, not mathematical learning. The ordered arm is now scored below. The shuffled arm and final-test comparison remain pending.

### Ordered-arm results

The ordered arm continued from early RLP checkpoint-118. It completed 432 optimizer steps and generated 2,794,874 training tokens, as recorded in `run.json`. The evaluator tested the same 18 independent-use items at five phases across eight concept families. These authored validation items are not a held-out benchmark.

Adjudicated full correctness was 0/18 at every phase. The initial and final checkpoints both scored 0/18 on the paired full-correctness endpoint. No item changed to or from fully correct.

The partial-credit core score ranges from 0 to 3. Its mean was 0.89 initially, reached 1.33 at the one-million-token phase, and fell to 0.72 at the final checkpoint. From initial to final, 4 items improved, 6 worsened, and 8 stayed at the same core score. The clearest family signal is congruence: its mean core score rose from 0 to 3 across the same two items and remained there. Both final answers still contain invalid extra reasoning and fail full correctness. This narrow signal does not establish reliable acquisition.

| Phase | Core score, mean of 18 | Fully correct | Responses at token cap |
| --- | ---: | ---: | ---: |
| Initial | 0.89 | 0/18 | 0/18 |
| 500,000-token checkpoint | 0.89 | 0/18 | 2/18 |
| 1,000,000-token checkpoint | 1.33 | 0/18 | 6/18 |
| 2,000,000-token checkpoint | 1.17 | 0/18 | 17/18 |
| Final | 0.72 | 0/18 | 7/18 |

The 2,000,000-token phase is heavily censored by the 1,024-token response limit. It cannot support a clean partial-credit comparison. The final result still shows no full-correctness gain.

The original second-review file failed evidence alignment and was excluded. Reviewer A's verified copy changes evidence excerpts only. A fresh second review and blinded adjudication passed exact-ID, evidence, schema, and consistency checks. Reviewer A's model identity is unavailable; the second reviewer and adjudicator report GPT-6 at runtime. This is independent AI review, not human grading or formal verification.

The scored report, integrity record, and scoring script are in cpu-angad at `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/controlled_ordered_review_20261006/ordered_results_v2e/`. The original rejected review and interim recounts remain preserved and are excluded. No held-out response was accessed. This evaluation does not measure proof semantics, proof strategy, Lean acceptance, or undergraduate-level performance.
