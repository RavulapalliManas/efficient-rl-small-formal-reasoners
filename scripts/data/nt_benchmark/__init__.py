"""Number-theory benchmark components. Legacy script commands remain supported."""
from pathlib import Path
import sys

# Legacy proof utilities are script modules, shared by the existing runners.
sys.path[:0] = [str(Path(__file__).resolve().parents[2] / d) for d in ('data', 'eval', 'audit')]

from .artifacts import BenchmarkExporter, reference_profile  # noqa: E402
from .pipeline import BenchmarkPipeline  # noqa: E402
from .selection import BenchmarkSelector, NUMERAL, TOPICS  # noqa: E402
from .storage import digest, rows, write_rows  # noqa: E402
from .verification import BenchmarkVerifier, LeanVerifier  # noqa: E402
