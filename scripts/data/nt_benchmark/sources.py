"""Fetch pinned public files and adapt their proof candidates to one input schema."""
import json
from pathlib import Path, PurePosixPath
import re
import textwrap
from urllib.parse import quote
from urllib.request import urlopen

from import_extra_nt import body_after_by, clean_statement, parse_code, source_records, split_declaration
from build_nt_pool import key
from .storage import digest, file_sha256


REPOSITORY = Path(__file__).resolve().parents[3]


def external_output(path):
    path = path.resolve()
    if path.is_relative_to(REPOSITORY):
        raise ValueError('Dataset outputs must be outside the repository.')
    return path


def registry_digest(registry):
    return digest(json.dumps(registry, sort_keys=True))


def source_files(registry, selected):
    """Expand only explicitly selected sources; never follow a moving branch."""
    files = []
    for name in sorted(set(selected)):
        if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            raise ValueError('Invalid source name.')
        if name not in registry['sources']:
            raise ValueError('Unknown source: ' + name)
        source = registry['sources'][name]
        if not re.fullmatch(r'[0-9a-f]{40}', source['revision']):
            raise ValueError('Source revision must be a full commit hash.')
        seen_paths = set()
        for entry in source['files']:
            path = PurePosixPath(entry['path'])
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Source file path must be relative.')
            if entry['path'] in seen_paths:
                raise ValueError('Source file paths repeat.')
            seen_paths.add(entry['path'])
            if not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']) or entry['bytes'] < 1:
                raise ValueError('Source file requires a SHA-256 and a positive byte size.')
            files.append(dict(entry, source=name, repo=source['repo'], revision=source['revision']))
    if not files:
        raise ValueError('Select at least one source.')
    return files


def fetch_sources(registry, selected, output, max_bytes):
    """Stream downloads into a new directory; preserve a receipt if a download fails."""
    files = source_files(registry, selected)
    total = sum(entry['bytes'] for entry in files)
    if total > max_bytes:
        raise ValueError(f'Selected files total {total:,} bytes, above --max-bytes {max_bytes:,}.')
    output = external_output(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = dict(status='downloading', registry_sha256=registry_digest(registry), files=files)
    try:
        for entry in files:
            target = output / entry['source'] / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            url = (f"https://huggingface.co/datasets/{entry['repo']}/resolve/"
                   f"{entry['revision']}/{quote(entry['path'])}")
            partial = target.with_name(target.name + '.partial')
            received = 0
            with urlopen(url, timeout=30) as response, partial.open('xb') as stream:
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > entry['bytes']:
                        raise ValueError('Download exceeds its recorded byte size: ' + entry['path'])
                    stream.write(block)
            if received != entry['bytes'] or file_sha256(partial) != entry['sha256']:
                raise ValueError('Download differs from the source checksum: ' + entry['path'])
            partial.rename(target)
        manifest['status'] = 'downloaded'
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def checked_source_files(raw, registry):
    """Recheck bytes before extraction; a successful download receipt is insufficient."""
    manifest = json.loads((raw / 'manifest.json').read_text())
    if manifest['status'] != 'downloaded' or manifest['registry_sha256'] != registry_digest(registry):
        raise ValueError('A completed download matching this source registry is required.')
    selected = {entry['source'] for entry in manifest['files']}
    expected = source_files(registry, selected)
    if manifest['files'] != expected:
        raise ValueError('Downloaded file list differs from the registry.')
    for entry in expected:
        path = raw / entry['source'] / entry['path']
        if path.stat().st_size != entry['bytes'] or file_sha256(path) != entry['sha256']:
            raise ValueError('Source file changed: ' + entry['path'])
    return expected


def workbook_records(path, counts):
    """Stream Lean Workbook JSON; keep each proof paired with its exact binder names."""
    import ijson
    with path.open('rb') as stream:
        for index, row in enumerate(ijson.items(stream, 'item')):
            counts['lean_workbook:rows'] += 1
            parts = split_declaration(row['formal_statement'])
            if parts is None:
                counts['lean_workbook:unsupported_statement'] += 1
                continue
            _, header, _ = parts
            name, statement = clean_statement(header)
            for proof_index, proof in enumerate(row.get('proof') or []):
                if not isinstance(proof, str):
                    counts['lean_workbook:unsupported_proof'] += 1
                    continue
                code = proof if re.search(r'\b(?:theorem|lemma)\s', proof) else header + ' := by\n' + textwrap.indent(proof, '  ')
                parsed = parse_code(code, counts, 'lean_workbook:', require_open=False)
                if parsed is None:
                    continue
                if key(parsed[1]) != key(statement):
                    counts['lean_workbook:proof_statement_mismatch'] += 1
                    continue
                yield f'{index}:{name}:{proof_index}', parsed[1], body_after_by(parsed[2])


def proof_candidates(raw, entries, counts):
    """Yield unverified candidates. Lean admission happens in the benchmark verifier."""
    for entry in entries:
        path = raw / entry['source'] / entry['path']
        source = entry['source']
        if source == 'lean_workbook':
            records = workbook_records(path, counts)
        elif source == 'goedel_workbook':
            records = goedel_workbook_records(path, counts)
        else:
            records = ((origin, statement, body) for origin, _, statement, _, body
                       in source_records(source, [path], counts))
        for origin, statement, body in records:
            yield dict(source=source, file=entry['path'], origin=str(origin), statement=statement, body=body)


def goedel_workbook_records(path, counts):
    from import_extra_nt import rows_of
    for row in rows_of(path, ['problem_id', 'full_proof']):
        counts['goedel_workbook:rows'] += 1
        parsed = parse_code(row['full_proof'], counts, 'goedel_workbook:', require_open=False)
        if parsed is not None:
            yield row['problem_id'], parsed[1], body_after_by(parsed[2])
