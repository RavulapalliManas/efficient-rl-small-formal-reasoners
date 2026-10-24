"""Shared observation text for training, evaluation and supervised examples."""
import textwrap

from .actions import BACKTRACK_V2, INSTRUCTION_V2


def render_observation(task, shown, goals, last_error, failed, remaining, max_tactics=4,
                       policy_backtrack=False, omit=0, premises=None):
    """The one v2 observation renderer, shared by RL, evaluation and SFT data builders.

    Every section is always present, so SFT rows from verified proofs show the same template.
    premises=None omits the retrieved-lemma section; a list (even empty) shows it."""
    head = task['header'].rstrip()
    head = head if head.endswith(':= by') else head + ' := by'
    if task.get('kind') == 'bank':
        given = [step.rstrip('\n') for step in task['given_steps']]
    else:
        given = task.get('given', '').rstrip('\n').splitlines()
    lines = [head, '  -- supplied lines (already checked):'] + (given or ['  -- (none)']) + ['  -- your steps:']
    if omit:
        lines.append(f'  -- (steps 1-{omit} omitted)')
    for i, text in enumerate(shown[omit:], omit + 1):
        lines += [f'  -- step {i}', textwrap.indent(text, '  ')]
    if not shown:
        lines.append('  -- (none)')
    notes = ['/- tactic state:\n' + '\n\n'.join(goals) + '\n-/',
             '/- last action result:\n' + (last_error or 'none') + '\n-/',
             '/- tactics that already failed here:\n' + ('\n'.join('- ' + f[:200] for f in failed[-8:]) or 'none') + '\n-/',
             f'/- actions remaining: {remaining} -/']
    if premises is not None:
        notes.insert(1, '/- library lemmas that may help:\n' + ('\n'.join('- ' + p for p in premises) or 'none') + '\n-/')
    instruction = INSTRUCTION_V2.format(n=max_tactics) + (BACKTRACK_V2 if policy_backtrack else '')
    return instruction + '\n\n```lean\n' + '\n'.join(lines + [textwrap.indent(n, '  ') for n in notes]) + '\n```'
