# Data preparation

This record moved verbatim from `README.md` on 2026-10-07. The [README status table](../../README.md#baselines-and-status) remains the canonical status record.

## Data preparation

The [data config](../../configs/data/leandojo_nat_v1.json) selects a project-defined subset of [LeanDojo Benchmark 4 v10](https://zenodo.org/records/12740403). It preserves membership in the official `random` splits. It selects declared arithmetic and number-theory source files, then excludes declarations containing listed types or ambiguous proof syntax. This is a source and syntax filter. It does not establish theorem difficulty or restrict every inferred type to natural numbers.

The source uses mathlib commit `29dcec074de168ac2bf835a77ef68bbe069194c5`. Its [toolchain file](https://github.com/leanprover-community/mathlib4/blob/29dcec074de168ac2bf835a77ef68bbe069194c5/lean-toolchain) specifies `leanprover/lean4:v4.10.0-rc1`. The audit records this pair; Lean replay remains pending.

The local [audit report](../../artifacts/leandojo_nat_v1/frozen/audit_report.json) records the following counts. The [artifact check](../../artifacts/leandojo_nat_v1/frozen/verification.json) recounts the exported rows.

| Split | Selected before the declaration filter | Retained | Export |
| --- | ---: | ---: | --- |
| Training | 810 | 706 | [train.jsonl](../../artifacts/leandojo_nat_v1/frozen/train.jsonl) |
| Curriculum validation | 18 | 17 | [val.jsonl](../../artifacts/leandojo_nat_v1/frozen/val.jsonl) |
| Held-out test | 15 | 15 | [test.jsonl](../../artifacts/leandojo_nat_v1/frozen/test.jsonl) |

447 training theorems contain tactic traces, as counted in the audit report. Those traces contain 1,312 tactic records. Abhinav can use those records for supervised training. Trace-free training theorems remain available as RL targets after Lean replay.

The audit removes training declarations with normalized statement overlap or token-set Jaccard similarity at the configured threshold. It also removes annotated references to selected validation or test theorems. It records proof-text equality separately because common tactics can produce identical proof text. The [removal records](../../artifacts/leandojo_nat_v1/frozen/removed.jsonl) and [overlap records](../../artifacts/leandojo_nat_v1/frozen/overlap_audit.jsonl) preserve the reasons. These textual checks do not establish semantic independence. The official splits share source files and theorem families. Model pretraining exposure remains unknown.

Validation and test exports contain declaration headers, imports, and source locators. They omit proof traces. Headers can depend on section variables from the source file. Anwesha must reconstruct each theorem in the pinned environment before it becomes a prompt. The first recorded tactic state can be an intermediate subgoal and must not become the initial theorem state.

The raw archive and premise corpus contain evaluation proofs. Keep them outside training, prompting, retrieval, and adaptive sampling. Load the exported training file explicitly. Do not load the artifact directory recursively.

15 test theorems limit the resolution of the RL comparison. Treat this frozen subset as a pipeline probe. A larger evaluation set and a held-out Lean baseline remain necessary before fixing the main success criterion. The Instruct validation diagnostic above does not establish a held-out score or an RL gain.

The [preparation script](../../scripts/data/prepare_leandojo.py) streams the archive with `ijson`, spools training traces to disk, and caps retained candidate data. It does not extract the full corpus. The local audit used 44.39 MiB of peak resident memory, recorded in the artifact check. Local execution follows the user's explicit request for this task.

To reproduce the audit, download the pinned archive into an ignored artifact directory:

```sh
mkdir -p artifacts/leandojo_nat_v1/source
curl --fail --location --max-time 240 --max-filesize 104857600 \
  --output artifacts/leandojo_nat_v1/source/leandojo_benchmark_4.tar.gz \
  https://zenodo.org/records/12740403/files/leandojo_benchmark_4.tar.gz
uv sync --locked
uv run python scripts/data/prepare_leandojo.py \
  --archive artifacts/leandojo_nat_v1/source/leandojo_benchmark_4.tar.gz \
  --config configs/data/leandojo_nat_v1.json \
  --output artifacts/leandojo_nat_v1/reproduction
uv run python -m unittest discover -s tests -v
```

Use a new output path for each audit. The script refuses to overwrite an existing output directory. It records the archive checksum, resolved config, script digest, and manifest digests. The original `prepared/` output preserves the preliminary filter. Use `frozen/` for the team handoff.

### Broader candidate pool on cpu-angad

The [broader filter](../../configs/data/leandojo_number_theory_v2.json) reuses the existing curator and the pinned LeanDojo archive. It includes logic, sets, functions, relations, natural and integer arithmetic, and number theory. Source families extend through quadratic residues and Diophantine problems. Family labels describe source coverage, not measured difficulty or learning prerequisites.

The remote artifact root is `/mnt/data/manasvenkatasairavulapalli/formal_rl/artifacts/leandojo_number_theory_v2/` on cpu-angad. The `frozen/` directory preserves the exported pool and overlap audit. This freezes the export for reproduction; it does not register the main benchmark. The earlier Nat probe remains unchanged.

| Export | Retained theorems | Proof traces |
| --- | ---: | --- |
| `frozen/train.jsonl` | 2,658 | Training traces retained |
| `frozen/val.jsonl` | 43 | Omitted |
| `frozen/test.jsonl` | 38 | Omitted |

The remote `verification.json` recounts the exports and checks manifest hashes, disjoint IDs, official memberships, and absent evaluation trace fields. The audit uses the same textual duplicate and annotated-reference checks as the original runner. It does not establish semantic separation or family-held-out transfer.

The training export includes 1,350 theorems with tactic traces. Those traces contain 5,233 tactic records. `curriculum_catalog.jsonl` records source families, header lengths, and trace lengths for training only. `training_references.jsonl` records 1,402 references between retained training theorems. These annotations are candidate dependency edges. They are neither kernel-replayed dependencies nor validated learning prerequisites. Difficulty remains unmeasured.

The delivery directory contains separate `training.zip`, `curriculum_validation.zip`, and `heldout_evaluation.zip` archives. `provenance.zip` contains the audit, filter, licenses, source inventory, dependency lock, and scripts. `manifest.json` records delivery sizes and SHA-256 digests. Load the training archive explicitly. Keep evaluation archives and the raw source outside training and retrieval.

The private Drive archive contains the separate delivery archives and manifest. Drive metadata readback confirms their sizes, destination, and unshared status. It does not verify downloaded bytes. The remote `storage_receipt.json` preserves file IDs and URLs. Drive serves as an archive and download source. Curation and packaging run on cpu-angad under a fixed memory cap. The Mac supplies temporary transfer files for the connector.

The remote curation command is:

```sh
cd /mnt/data/manasvenkatasairavulapalli/formal_rl/curation_v2
.venv/bin/python scripts/prepare_leandojo.py \
  --archive ../artifacts/leandojo_nat_v1/source/leandojo_benchmark_4.tar.gz \
  --config configs/leandojo_number_theory_v2.json \
  --output ../artifacts/leandojo_number_theory_v2/reproduction
```

The completed launch uses tmux session `rl-nt-v2-curate`, a 2 GiB address-space cap, and a 600-second timeout. The export check runs `.venv/bin/python scripts/formal_rl_curate_audit.py` from the archived script. Both completed with exit code 0. The first packaging launch returned exit code 2 because its script transfer was incomplete. Its log remains under `verification-transfer-failure.log`.

Lean replay and initial-state reconstruction remain pending. The retained test pool cannot support detailed domain comparisons. A larger evaluation split, prerequisite review, and model readiness measurements remain necessary before RL.
