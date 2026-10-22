"""Lean tactic-action parsing and diagnostic message formatting.

These helpers operate on text without starting Lean or loading a model.
The parser preserves comments and nested tactic blocks in the action text.
"""
import re
import textwrap


BACKTRACK = re.compile(r'backtrack[ \t]+(\d+)')
DECLARATION = re.compile(r'(theorem|lemma|example)\b')
INSTRUCTION_V2 = ('Complete the following Lean 4 proof. Write the next action: up to {n} tactics, each optionally '
                  'preceded by "-- " comment lines. Lean runs them in order and stops at the first error.')
BACKTRACK_V2 = ' Or write only "backtrack k" to return to the state after your step k (0 = before your first step).'


# A Lean char literal such as '"' and a raw string such as r"\" confuse a comment stripper that knows only
# ordinary strings. lex_lines below knows both. An identifier character before ' is a prime, not a literal.
CHAR_LITERAL = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)|[^\\'\n])'")
RAW_STRING = re.compile(r'r(#*)"')
# A line that starts with one of these continues the tactic above it, even at the same indentation
# (checked with real Lean: induction/match alternatives, a <;> chain, else, a calc step "_ = ...", "with").
CONTINUATION = re.compile(r'(\||<;>|else\b|with\b|_(\s|$))')
FENCE = re.compile(r'```(?:lean4?)[ \t]*\r?\n(.*?)\r?\n```', re.S | re.I)


def lex_lines(lines):
    """Group logical lines and remove comments while preserving string literals.

    A line continues the previous group while a block comment, string, or
    bracket is open. Each group keeps its original lines and a code flag.
    The separate code text is used by the forbidden-construct check.
    """
    groups, code = [], []
    comment_depth, in_string, raw_delimiter_length, bracket_depth = 0, False, None, 0
    for line in lines:
        continues_group = comment_depth or in_string or raw_delimiter_length is not None or bracket_depth
        if groups and continues_group:
            group = groups[-1]
        else:
            group = dict(lines=[], code=False)
            groups.append(group)
        group['lines'].append(line)

        index = 0
        while index < len(line):
            character = line[index]
            pair = line[index:index + 2]
            previous = line[index - 1] if index else ' '
            follows_identifier = previous.isalnum() or previous in "_'!?"

            if comment_depth:
                if pair == '/-':
                    comment_depth += 1
                    index += 2
                elif pair == '-/':
                    comment_depth -= 1
                    index += 2
                else:
                    index += 1
                continue

            if raw_delimiter_length is not None:
                code.append(character)
                closing_delimiter = '"' + '#' * raw_delimiter_length
                if line.startswith(closing_delimiter, index):
                    code.append('#' * raw_delimiter_length)
                    index += 1 + raw_delimiter_length
                    raw_delimiter_length = None
                else:
                    index += 1
                continue

            if in_string:
                if character == '\\':
                    code.append(line[index:index + 2])
                    index += 2
                else:
                    code.append(character)
                    in_string = character != '"'
                    index += 1
                continue

            if pair == '/-':
                comment_depth = 1
                index += 2
                continue
            if pair == '--':
                break

            group['code'] = group['code'] or not character.isspace()
            literal = None
            if not follows_identifier:
                if character == 'r':
                    literal = RAW_STRING.match(line, index)
                elif character == "'":
                    literal = CHAR_LITERAL.match(line, index)

            if literal:
                code.append(literal.group())
                if character == 'r':
                    raw_delimiter_length = len(literal.group(1))
                index = literal.end()
            else:
                in_string = character == '"'
                bracket_depth = max(
                    bracket_depth + (character in '([{⟨') - (character in ')]}⟩'), 0)
                code.append(character)
                index += 1
        code.append('\n')
    return groups, ''.join(code)


def action_positions(text):
    """Convert Lean's wrapper positions to action line and column numbers."""
    def action_position(match):
        line = max(int(match[1]) - 1, 1)
        column = max(int(match[2]) - 2, 0)
        return f'<input>:{line}:{column}'

    return re.sub(r'<input>:(\d+):(\d+)', action_position, text)


def cap_message(prefix, message, note='', limit=600):
    """Shorten the diagnostic while keeping the state-change note intact."""
    available = max(limit - len(prefix) - len(note), 0)
    return prefix + message[:available] + note


def parse_action(text, max_tactics=4, allow_backtrack=False):
    """Parse a bounded tactic block or an explicitly enabled backtrack action.

    Accept one complete lean/lean4 fence or an unfenced tactic block. Reject
    prose around a fence, incomplete fences, and unsupported fence labels.
    Preserve leading comments and nested tactic indentation.
    """
    fenced = FENCE.fullmatch(text.strip())
    body = fenced.group(1) if fenced else text
    if not body.strip() or '```' in body:
        return dict(kind='malformed')

    # Lean rejects indentation tabs. Convert only leading whitespace.
    normalized_lines = []
    for line in body.splitlines():
        leading = re.match(r'^[ \t]*', line).group()
        normalized_lines.append(leading.replace('\t', '  ') + line[len(leading):])
    body = '\n'.join(normalized_lines)
    lines = [line for line in textwrap.dedent(body).splitlines() if line.strip()]

    # SFT targets may repeat a declaration. Remove its header without executing it.
    declaration_start = next(
        (index for index, line in enumerate(lines) if not line.lstrip().startswith(('--', '/-'))),
        None,
    )
    if declaration_start is not None and DECLARATION.match(lines[declaration_start].lstrip()):
        declaration_end = next(
            (index for index in range(declaration_start, len(lines)) if lines[index].rstrip().endswith(':= by')),
            None,
        )
        if declaration_end is None:
            return dict(kind='malformed', reason='a restated theorem header must end with ":= by".')
        lines = lines[:declaration_start] + lines[declaration_end + 1:]

    groups, _ = lex_lines(lines)
    code_groups = [group for group in groups if group['code']]
    if not code_groups:
        return dict(kind='malformed')

    def first_line(group):
        return group['lines'][0].lstrip()

    def indentation(group):
        return len(group['lines'][0]) - len(first_line(group))

    backtrack = None
    if len(code_groups) == 1 and len(code_groups[0]['lines']) == 1:
        backtrack = BACKTRACK.fullmatch(first_line(code_groups[0]).strip())
    if backtrack:
        if not allow_backtrack:
            return dict(kind='malformed', reason='backtrack is not an action here; the harness backtracks.')
        return dict(kind='backtrack', step=int(backtrack.group(1)))

    # Continuation lines belong to their preceding tactic even at the same indentation.
    tactic_starts = [group for group in code_groups if not CONTINUATION.match(first_line(group))]
    base_indent = min(indentation(group) for group in (tactic_starts or code_groups))
    tactics, pending_comments = [], []
    for group in groups:
        starts_tactic = indentation(group) <= base_indent and not CONTINUATION.match(first_line(group))
        if not group['code'] and (starts_tactic or not tactics):
            pending_comments += group['lines']
        elif starts_tactic or not tactics:
            tactics.append(pending_comments + group['lines'])
            pending_comments = []
        else:
            tactics[-1] += pending_comments + group['lines']
            pending_comments = []
    tactics[-1] += pending_comments
    if len(tactics) > max_tactics:
        return dict(kind='malformed', reason=f'write at most {max_tactics} tactics per action.')

    blocks = []
    for tactic in tactics:
        stripped_lines = [
            line[base_indent:] if not line[:base_indent].strip() else line.lstrip()
            for line in tactic
        ]
        blocks.append('\n'.join(stripped_lines))
    return dict(kind='tactics', tactics=blocks)
