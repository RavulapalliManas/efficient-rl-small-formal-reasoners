"""Lean action serialization without verifier or trainer imports."""
import textwrap


def tactic_block(text):
    # The pinned REPL parses one tactic, rather than a bare tactic sequence.
    return 'first |\n' + textwrap.indent(textwrap.dedent(text).strip(), '  ')
