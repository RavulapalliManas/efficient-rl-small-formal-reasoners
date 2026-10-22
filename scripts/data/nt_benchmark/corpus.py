"""Build a deterministic, disk-backed corpus of unverified number-theory proofs."""
from collections import Counter
import json
import sqlite3

from build_nt_pool import key, REAL
from import_extra_nt import EXTRA
from profile_external_proofs import NUMBER_THEORY, closed
from probe_start_depth import FORBIDDEN
from .sources import checked_source_files, external_output, proof_candidates, registry_digest
from .storage import digest, file_sha256, source_hashes


def candidate_error(record):
    statement, body = record['statement'], record['body']
    if not NUMBER_THEORY.search(statement) or REAL.search(statement) or closed(statement):
        return 'outside_number_theory_scope'
    if len(statement) > 4096 or '\n' in statement or ':=' in statement or FORBIDDEN.search(statement):
        return 'unsupported_statement'
    if not body or len(body) > 4096 or FORBIDDEN.search(body) or EXTRA.search(body):
        return 'unsupported_proof'
    if any(line.strip() and not line[0].isspace() for line in body.splitlines()):
        return 'unindented_proof'
    return None


def load_exposure(path):
    """An audit explicitly covers every retained canonical statement; absence is unknown."""
    receipt = json.loads(path.read_text())
    if not receipt.get('parent_checkpoint') or not receipt.get('audit_description'):
        raise ValueError('Exposure audit needs parent_checkpoint and audit_description.')
    entries = receipt['statements']
    if any(not isinstance(value, bool) for value in entries.values()):
        raise ValueError('Exposure audit values must be explicit booleans.')
    return receipt


def build_corpus(raw, registry, output, *, unchanged_base=False, exposure_path=None):
    """Merge exact statement copies and retain two proofs per statement in hash order.

    SQLite bounds memory independently of corpus size. Alpha-renamed statements
    keep their own proof texts; the benchmark selector groups their identities.
    This stage does not execute Lean or certify a reference proof.
    """
    if unchanged_base == (exposure_path is not None):
        raise ValueError('Choose exactly one of --unchanged-base or --exposure-audit.')
    exposure = load_exposure(exposure_path) if exposure_path else None
    entries = checked_source_files(raw, registry)
    output = external_output(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'private').mkdir()
    counts = Counter()
    manifest = dict(status='building', reference_status='not_lean_verified', registry_sha256=registry_digest(registry),
                    input_manifest_sha256=file_sha256(raw / 'manifest.json'), source_sha256=source_hashes(),
                    lineage=dict(parent_checkpoint='unchanged upstream base', project_fine_tuning_exposure='none_declared_by_caller')
                    if unchanged_base else dict(parent_checkpoint=exposure['parent_checkpoint'],
                        audit_description=exposure['audit_description'], sha256=file_sha256(exposure_path)),
                    pretraining_exposure='unknown', historical_equivalence=False)
    database = sqlite3.connect(output / 'private/corpus.sqlite3')
    try:
        database.execute('CREATE TABLE proofs (statement TEXT, proof_sha256 TEXT, body TEXT, source TEXT, '
                         'PRIMARY KEY (statement, proof_sha256, source))')
        with (output / 'private/dispositions.jsonl').open('x') as dispositions:
            for record in proof_candidates(raw, entries, counts):
                reason = candidate_error(record)
                statement_id = digest(key(record['statement']))
                disposition = {name: record[name] for name in ('source', 'file', 'origin')}
                disposition.update(canonical_statement_sha256=statement_id, statement_sha256=digest(record['statement']),
                                   body_sha256=digest(record['body']) if record['body'] else None,
                                   status=reason or 'candidate')
                dispositions.write(json.dumps(disposition, ensure_ascii=False) + '\n')
                counts[reason or 'proof_candidates'] += 1
                if reason:
                    continue
                if exposure is not None and statement_id not in exposure['statements']:
                    raise ValueError('Exposure audit omits canonical statement: ' + statement_id)
                database.execute('INSERT OR IGNORE INTO proofs VALUES (?, ?, ?, ?)',
                                 (record['statement'], digest(record['body']), record['body'], record['source']))
        database.commit()
        statements = database.execute('SELECT DISTINCT statement FROM proofs ORDER BY statement')
        with (output / 'corpus.jsonl').open('x') as stream:
            for (statement,) in statements:
                sources = [row[0] for row in database.execute(
                    'SELECT DISTINCT source FROM proofs WHERE statement = ? ORDER BY source', (statement,))]
                proofs = database.execute('SELECT proof_sha256, body, MIN(source) FROM proofs WHERE statement = ? '
                                          'GROUP BY proof_sha256, body ORDER BY proof_sha256 LIMIT 2', (statement,)).fetchall()
                canonical_id = digest(key(statement))
                record = dict(statement=statement, sources=sources, near_syllabus=[],
                              canonical_statement_sha256=canonical_id,
                              seen_by_parent_lineage=False if unchanged_base else exposure['statements'][canonical_id],
                              reference_status='not_lean_verified', proofs=[dict(source=source, body_sha256=proof_hash,
                                  text='theorem original ' + statement + ' := by\n' + body)
                                  for proof_hash, body, source in proofs])
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                counts['statements'] += 1
                counts['proofs_retained'] += len(proofs)
        if not counts['statements']:
            raise ValueError('No supported proof candidates; inspect the dispositions and source counts.')
        manifest.update(status='candidates_not_verified', corpus_sha256=file_sha256(output / 'corpus.jsonl'),
                        dispositions_sha256=file_sha256(output / 'private/dispositions.jsonl'))
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        database.close()
        manifest['counts'] = dict(sorted(counts.items()))
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
