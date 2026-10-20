"""Build a versioned, exploratory number-theory benchmark from the existing corpus.

select freezes candidates and splits without a model or Lean. verify replays the
references, extracts goals, screens fixed tactics, and exports SFT and RL inputs.
describe writes evaluation questions, an answer contract, and private references.
Run both stages on cpu-angad. Inputs remain unchanged; outputs must be new.

Peak selection: <=256 candidates x 24 KiB + 200,000 IDs x 512 bytes, <256 MiB.
Prior matching holds at most max_protected token sets (default 256, at most 4096), each from
<=4096 characters; 4096 sets against 100,000 rows is about 4e8 small set operations.
Verification adds one Mathlib process (previously measured <4 GiB); use a 6 GiB
managed cap. One writer, incremental JSONL. No model or optimizer is loaded.
Text groups are duplicate controls, not verified mathematical proof families."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nt_benchmark import (
    BenchmarkExporter, BenchmarkPipeline, BenchmarkSelector, BenchmarkVerifier, LeanVerifier,
    NUMERAL, TOPICS, digest, reference_profile, rows, write_rows,
)


def candidate(row, config):
    return BenchmarkSelector(config).candidate(row)


def select(paths, config, max_input_bytes):
    return BenchmarkSelector(config).select(paths, max_input_bytes)


def partition(candidates, config):
    return BenchmarkSelector(config).partition(candidates)


def export(verified, output, config):
    return BenchmarkExporter().export(verified, output)


def verify(package, output, environment, identity_path):
    return BenchmarkVerifier().verify(package, output, environment, identity_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='stage', required=True)
    select_parser = subparsers.add_parser('select')
    select_parser.add_argument('--corpus', type=Path, action='append', required=True)
    select_parser.add_argument('--config', type=Path, required=True)
    select_parser.add_argument('--protect-package', type=Path, action='append', default=[],
                               help='Frozen selection to exclude, including numeric and lexical matches; repeat')
    select_parser.add_argument('--protect-statements', type=Path, action='append', default=[],
                               help='JSONL with a statement field; exclude numeric and lexical matches; repeat')
    select_parser.add_argument('--exclude-statement-sha256', type=Path, action='append', default=[],
                               help='Text file of raw-statement SHA-256 values never to select; repeat')
    select_parser.add_argument('--max-input-bytes', type=int, default=512 * 1024**2)
    select_parser.add_argument('--output', type=Path, required=True)
    verify_parser = subparsers.add_parser('verify')
    for option in ('package', 'output', 'environment', 'identity'):
        verify_parser.add_argument('--' + option, type=Path, required=True)
    describe_parser = subparsers.add_parser('describe')
    for option in ('package', 'output'):
        describe_parser.add_argument('--' + option, type=Path, required=True)
    args = parser.parse_args()
    if args.stage == 'describe':
        print(json.dumps(BenchmarkExporter().describe(args.package, args.output)))
        return
    if args.stage == 'verify':
        verify(args.package, args.output, args.environment, args.identity)
        return
    config = json.loads(args.config.read_text())
    BenchmarkPipeline(config, protected_packages=args.protect_package, protected_statements=args.protect_statements,
                      excluded_ids=args.exclude_statement_sha256).select(args.corpus, args.output, args.max_input_bytes)


if __name__ == '__main__':
    main()
