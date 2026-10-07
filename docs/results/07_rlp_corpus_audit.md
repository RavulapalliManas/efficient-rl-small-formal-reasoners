# Structured RLP corpus audit

This record moved verbatim from `README.md` on 2026-10-07. The [README status table](../../README.md#baselines-and-status) remains the canonical status record.

## Structured RLP corpus audit

The audit checks the saved `primitive_midtraining_v1/prepared_ordered/` export used by the completed primitive run. It does not regenerate data or audit every earlier textbook export. Historical inputs remain unchanged. `scripts/audit/audit_rlp_corpus.py` recomputes numeric answers independently of the historical preparer's predicate. A separate mathematical review checks the assistance strings against the source and an independent reviewer. This is AI-assisted mathematical review, not human validation or formal certification.

612 unique numeric targets pass in CPU `artifacts/rlp_data_audit_v1/final/report.json`. The report counts training, curriculum validation, and historical held-out records separately. No exact question or equivalent numeric-task key crosses those splits. Decimal-numeral normalization finds shared templates in every split pair. This check does not establish semantic separation or family-held-out generalization.

| Historical split | Unique records | Numeric admission | Prospective support admission |
| --- | ---: | --- | --- |
| Training | 448 | All correct | 288 reviewed; 160 disputed |
| Curriculum validation | 56 | All correct | 36 reviewed; 20 disputed |
| Historical held-out | 108 | All correct | 54 reviewed; 30 disputed; 24 numeric compositions admitted by exact check |

The disputed records contain reusable remainder, gcd, Bézout, inverse, or cancellation assistance with unstated domain or zero conventions. Their concrete positive-domain answers are correct. The hold applies to prospective reuse of unqualified support, not historical arithmetic correctness. State integer domains, positive divisors or moduli, and zero conventions before exporting those rules again. The [division theorem](https://twjudson.github.io/aata-files/aata-html/integers-section-division-algorithm.html) and [modular arithmetic](https://twjudson.github.io/aata-files/aata-html/groups-section-mod-n-sym.html) supply the mathematical reference. `support_review.json` records conditions, counterexamples, reviewer provenance, and required repairs by family.

The corpus cannot establish independent knowledge acquisition or complete proof competence. Every unique training task supplies mathematical assistance. The saved parameters contain no zero or negative operand cases. Inverse tasks require an existing inverse. The induction target is a numerical successor difference. The contradiction target is a remainder, not a contradiction argument. Explicit assumption fields and checked reference-step fields are absent from this export.

The constant-answer baseline solves every historical contradiction item. Its validation result is 4/4 in `final/report.json`; its historical held-out result is 6/6. A constant prime-factor answer fitted on training scores 4/4 on validation and 3/6 on historical held-out. These are data baselines, not model results. The counterexample task also permits copying its first operand. Such targets cannot distinguish the intended proof skill from a shortcut.

Replay changes the actual exposure distribution. Witness receives 73 presentations in the recorded training order. Induction receives 32 presentations. Each family has the same unique-record count. Preserve the exact presentation multiset across ordered and shuffled arms, including replay; drawing replay separately would change both order and exposure.

The numeric audit returns `PASS` with zero mismatches across its selected checker probes. Positive and negative planted controls pass before measurement. Source snapshots match the recorded manifest hashes. These checks certify their declared arithmetic and file contracts, not generated reasoning. Previously inspected evaluation items remain development material. Fresh family-aware tests, explicit assumptions, checked derivations, matched support views, and grader calibration remain necessary before training.

CPU artifacts live under `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/rlp_data_audit_v1/`. The final report and admission ledger are `final/report.json` and `final/admission.jsonl`. The support review is `support_review.json`. The source, logs, earlier audit stages, and historical preparer are archived in S3 `capstate/acq/formal_rl/rlp_data_audit_20261006/audit.tar.gz`. No model inference, optimizer update, GPU launch, or Lean call occurs in this audit.

The final command runs inside tmux `formal-rl-data-distribution-v1` with a 2 GiB service cap:

```sh
python3 primitive_midtraining_v1/code/scripts/audit_rlp_corpus.py \
  --data primitive_midtraining_v1/prepared_ordered \
  --preparer primitive_midtraining_v1/code/scripts/prepare_rlp_corpus.py \
  --support-review artifacts/rlp_data_audit_v1/support_review.json \
  --output artifacts/rlp_data_audit_v1/reproduction
```

Run from the CPU project root with a fresh output directory. The completed run uses `final/`; its process exits 0. The report sets `training_ready` to false. The next work follows the [formalization package](../research_plan.md#formalization-work-package), starting with scoped records and support views.
