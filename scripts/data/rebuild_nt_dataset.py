"""Rebuild public number-theory data: sources, corpus, verifier identity and task orders."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nt_benchmark.corpus import build_corpus  # noqa: E402
from nt_benchmark.identity import record_identity  # noqa: E402
from nt_benchmark.schedules import build_orders  # noqa: E402
from nt_benchmark.sources import fetch_sources, source_files  # noqa: E402

REGISTRY = Path(__file__).resolve().parents[2] / 'configs/data/nt_sources_v1.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=REGISTRY)
    commands = parser.add_subparsers(dest='stage', required=True)
    for stage in ('sources', 'fetch'):
        command = commands.add_parser(stage)
        command.add_argument('--source', action='append', required=True, help='Source name from the registry; repeat to combine.')
        if stage == 'fetch':
            command.add_argument('--output', type=Path, required=True)
            command.add_argument('--max-bytes', type=int, default=128 * 1024**2)
    corpus = commands.add_parser('corpus')
    corpus.add_argument('--raw', type=Path, required=True)
    corpus.add_argument('--output', type=Path, required=True)
    lineage = corpus.add_mutually_exclusive_group(required=True)
    lineage.add_argument('--unchanged-base', action='store_true', help='Declare no project fine-tuning exposure; upstream pretraining remains unknown.')
    lineage.add_argument('--exposure-audit', type=Path, help='Checkpoint-specific audit covering every retained canonical statement.')
    identity = commands.add_parser('identity', help='Record a clean, pinned Lean/mathlib source checkout.')
    for name in ('environment', 'config', 'output'):
        identity.add_argument('--' + name, type=Path, required=True)
    orders = commands.add_parser('orders', help='Build new paired orders from a verified TRAIN bank.')
    orders.add_argument('--package', type=Path, required=True)
    orders.add_argument('--output', type=Path, required=True)
    orders.add_argument('--seed', type=int, default=20261021)
    orders.add_argument('--epochs', type=int, default=2)
    orders.add_argument('--prerequisites', type=Path, help='Complete prerequisite annotation JSON; required for a C order.')
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text())
    try:
        if args.stage == 'sources':
            entries = source_files(registry, args.source)
            result = dict(files=entries, total_bytes=sum(entry['bytes'] for entry in entries), downloaded=False)
        elif args.stage == 'fetch':
            result = fetch_sources(registry, args.source, args.output, args.max_bytes)
        elif args.stage == 'identity':
            result = record_identity(args.environment, json.loads(args.config.read_text()), args.output)
        elif args.stage == 'orders':
            result = build_orders(args.package, args.output, args.seed, args.epochs, args.prerequisites)
        else:
            result = build_corpus(args.raw.resolve(), registry, args.output,
                                  unchanged_base=args.unchanged_base, exposure_path=args.exposure_audit)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
