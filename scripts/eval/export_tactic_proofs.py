"""Export complete model proof paths and replay with the existing strict scorer.

No model or optimizer. Metadata <16 MiB, source context <256 MiB,
one Lean process at a time <=4 GiB. Managed 8 GiB, two cores, 600 seconds.
Only original starts enter this demo. It is not a new accuracy evaluation.
"""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from proof_export import ProofExporter, ProofTrajectory  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    for name in ('code', 'run', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    ProofExporter(args.code, args.run, args.output).export()


if __name__ == '__main__':
    main()
