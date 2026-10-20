"""Reusable training components. Importing this package does not load model libraries."""
import hashlib
from pathlib import Path

from .objectives import LeaveOneOutObjective, PolicyGradientUpdater
from .sft import SFTDataset
from .tasks import PoolTaskAdmission, TaskCurriculum, TaskSchedule


def component_fingerprints():
    """Hash every component source for the existing run receipts."""
    return {'rl_components/' + path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).parent.glob('*.py'))}
