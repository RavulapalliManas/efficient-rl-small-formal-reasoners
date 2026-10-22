"""Bounded JSONL storage and source receipts for benchmark builds."""
import hashlib
import json
from pathlib import Path


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def rows(path):
    with path.open() as stream:
        for index, line in enumerate(stream, 1):
            if len(line.encode()) > 256 * 1024:
                raise ValueError(f'{path.name}:{index}: row exceeds 256 KiB')
            yield json.loads(line)


def write_rows(path, values):
    with path.open('x') as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False) + '\n')



def file_sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_hashes():
    """Record every module after splitting the original builder."""
    package = Path(__file__).resolve().parent
    paths = [package.parent / 'build_nt_benchmark.py', package.parent / 'rebuild_nt_dataset.py',
             *sorted(package.glob('*.py'))]
    result = {}
    for path in paths:
        result[path.name] = file_sha256(path)
    return result
