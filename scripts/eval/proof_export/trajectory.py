"""Reconstruct the exact retained proof from a complete rollout trajectory."""
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'train'))
from rl_components.proofs import tactic_block  # noqa: E402


class ProofTrajectory:
    """Validate completion and preserve v2's recorded body through fresh replay."""

    def __init__(self, path, environment_version=1):
        self.path = path
        self.environment_version = environment_version

    def proof_body(self):
        path = self.path
        last = path[-1]
        if (last.get('status') != 'accepted' or last.get('done') is not True or
                last.get('replay', {}).get('status') != 'accepted'):
            raise RuntimeError('Accepted trajectory lacks a complete verified final proof')
        if [row['step'] for row in path] != list(range(len(path))):
            raise RuntimeError('Accepted trajectory is not contiguous')
        if self.environment_version == 2:
            # Raw chunks include failed suffixes and steps removed by backtracking.
            body = last.get('proof_body')
            if not isinstance(body, str) or not body.strip():
                raise RuntimeError('Environment v2 export requires the recorded proof body')
        else:
            blocks = []
            for index, row in enumerate(path):
                if row['status'] not in ('changed', 'accepted'):
                    # A repair leaves the Lean proof state unchanged and never enters LeanEpisode.path.
                    diagnostic = '\n\nThe last tactic failed. The verified goal is unchanged. Lean diagnostic:\n'
                    next_state = path[index + 1]['state'].split(diagnostic, 1)[0] if index + 1 < len(path) else None
                    unchanged = bool(row.get('state')) and next_state == row['state'].split(diagnostic, 1)[0]
                    if (row['status'] in ('rejected', 'format_error') and row.get('repair_offered') is True
                            and row.get('done') is False and row.get('accepted') is False
                            and row.get('replay') is None and unchanged):
                        continue
                    raise RuntimeError('Accepted path includes a failed tactic')
                text = row['tactic']
                fence = re.fullmatch(r'```lean[ \t]*\r?\n(.*?)\r?\n```', text.strip(), re.S)
                blocks.append(tactic_block(fence.group(1) if fence else text))
            body = '\n'.join('  ' + line for block in blocks for line in block.splitlines()) + '\n'
        return body
