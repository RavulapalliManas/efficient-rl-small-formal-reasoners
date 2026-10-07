"""Verify supplied-knowledge witness expressions with the existing Lean checker.

Candidate boundary: <=256 ASCII characters and <=64 AST nodes. Never execute
candidate code. Serialize allowed integer expressions into a fixed obligation.
One Lean worker <=2 GiB plus Python/task metadata <128 MiB. Run on cpu-angad
under a 3 GiB cgroup and 1200-second wall limit. No model or optimizer is loaded.
"""
import ast
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'data'))


def extract_witness(response):
    """Extract one final answer, without algebraic repair or prose interpretation."""
    if len(response) > 32768:
        raise ValueError('Response exceeds extraction limit')
    text = response.strip()
    boxes = list(re.finditer(r'\\boxed\{([^{}]*)\}', text))
    labels = list(re.finditer(r'(?im)^Final witness:\s*([^\n]+)', text))
    if len(boxes) + len(labels) > 1:
        raise ValueError('Multiple final witnesses')
    if boxes or labels:
        match = (boxes or labels)[0]
        if text[match.end():].strip(' \t\r\n.$\\()[]'):
            raise ValueError('Content follows the final witness')
        candidate = match.group(1).strip()
    else:
        final_equation = re.search(r'\\\[\s*w\s*=\s*([^\[\]{}]+?)\s*\\\]\s*\.?$', text)
        final_statement = re.search(r'(?i)final witness is\s*\\\(\s*([^{}]+?)\s*\\\)\s*\.?$', text)
        match = final_equation or final_statement
        candidate = match.group(1).strip() if match else text
    for opening, closing in [(r'\(', r'\)'), (r'\[', r'\]'), ('$', '$')]:
        if candidate.startswith(opening) and candidate.endswith(closing):
            candidate = candidate[len(opening):-len(closing)].strip()
    candidate = re.sub(r'^w\s*=\s*', '', candidate)
    candidate = candidate.replace(r'\cdot', '*').replace(r'\times', '*')
    if not candidate or len(candidate) > 256 or not re.fullmatch(r'[a-zA-Z0-9_ +*()\-]+', candidate):
        raise ValueError('Final witness is not in the declared polynomial grammar')
    return candidate


def lean_expression(candidate, variables):
    """Preserve the submitted expression; reject all non-arithmetic syntax."""
    candidate = candidate.strip()
    if not candidate or len(candidate) > 256 or not re.fullmatch(r'[a-zA-Z0-9_ +*()\-]+', candidate):
        raise ValueError('Expected a bounded integer expression')
    try:
        tree = ast.parse(candidate, mode='eval')
    except (SyntaxError, RecursionError) as error:
        raise ValueError('Invalid expression syntax') from error
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError('Expression exceeds node limit')

    def render(node):
        if isinstance(node, ast.Name) and node.id in variables:
            return node.id
        if isinstance(node, ast.Constant) and type(node.value) is int and 0 <= node.value <= 10000:
            return str(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            sign = '-' if isinstance(node.op, ast.USub) else '+'
            if sign == '+':
                return render(node.operand)
            return '(-' + render(node.operand) + ')'
        if isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult):
            operator = {ast.Add: '+', ast.Sub: '-', ast.Mult: '*'}[type(node.op)]
            return '(' + render(node.left) + ' ' + operator + ' ' + render(node.right) + ')'
        raise ValueError('Expression uses an undeclared variable or unsupported operation')

    return render(tree.body)


# Trusted training fixtures. No held-out theorem or reference proof is loaded.
OPERATORS = {
    'unpack': {
        'variables': ['k'],
        'context': '(a b k : Int) (hk : b = a * k)',
        'target': 'b = a * {candidate}',
        'proof': 'rw [hk] <;> ring1',
        'prompt': 'Definition: a|b means b=a*k for an integer k. Given an integer witness k and b=a*k, return a multiplier w such that b=a*w.',
        'correct': ['k', 'k+0'], 'incorrect': ['k+1', '-k'],
    },
    'compose': {
        'variables': ['k', 'l'],
        'context': '(a b c k l : Int) (h1 : b = a * k) (h2 : c = b * l)',
        'target': 'c = a * {candidate}',
        'proof': 'rw [h2, h1]\nring1',
        'prompt': 'All variables are integers. Given b=a*k and c=b*l, return a witness expression w such that c=a*w.',
        'correct': ['k*l', 'l*k'], 'incorrect': ['k+l', 'k'],
    },
    'combine': {
        'variables': ['s', 't', 'u', 'v'],
        'context': '(d x y s t u v : Int) (h1 : x = d * u) (h2 : y = d * v)',
        'target': 's*x + t*y = d * {candidate}',
        'proof': 'rw [h1, h2]\nring1',
        'prompt': 'All variables are integers. Given x=d*u and y=d*v, return a witness expression w such that s*x+t*y=d*w.',
        'correct': ['s*u+t*v', 'v*t+u*s'], 'incorrect': ['s*u-t*v', 'u+v'],
    },
    'scale': {
        'variables': ['k', 't'],
        'context': '(a b k t : Int) (h : b = a * k)',
        'target': 't*b = a * {candidate}',
        'proof': 'rw [h]\nring1',
        'prompt': 'All variables are integers. Given b=a*k, return a witness expression w such that t*b=a*w.',
        'correct': ['t*k', 'k*t'], 'incorrect': ['t+k', 'k'],
    },
}


def make_obligation(operator, candidate):
    return task_obligation(dict(OPERATORS[operator], id=operator), candidate)


def task_obligation(task, candidate):
    """Task fields are trusted frozen data; only the candidate is model supplied."""
    expression = lean_expression(candidate, task['variables'])
    if not re.fullmatch(r'[a-z][a-z0-9_]*', task['id']):
        raise ValueError('Invalid task identifier')
    name = 'local_operator_' + task['id']
    q = {'id': name, 'name': name, 'closing': [],
         'prefix': 'import Mathlib.Algebra.Ring.Int\nimport Mathlib.Tactic.Ring.Basic\nimport Lean.Elab.Tactic\nset_option autoImplicit false\n',
         'header': 'theorem ' + name + ' ' + task['context'] + ' : '
                   + task['target'].format(candidate=expression)}
    return q, task['proof']


def reward_value(result):
    """Infrastructure failure is unscored, never a mathematical negative."""
    from prepare_grpo_pilot import verifier_failed
    # A candidate cannot introduce tactics; an unknown tactic is a broken skeleton.
    if verifier_failed(result) or 'unknown tactic' in result.get('output', '').lower():
        return None
    return int(result['accepted'])


def main():
    import argparse
    import hashlib
    import json
    from pathlib import Path
    import signal
    from prepare_grpo_pilot import load_checker

    ap = argparse.ArgumentParser()
    for name in ('checker', 'environment', 'output', 'revisions'):
        ap.add_argument('--' + name, type=Path, required=True)
    ap.add_argument('--controls', action='store_true')
    ap.add_argument('--operator', choices=sorted(OPERATORS))
    ap.add_argument('--candidate')
    args = ap.parse_args()
    if not args.controls and (args.operator is None or args.candidate is None):
        ap.error('Supply --controls or both --operator and --candidate')
    if args.controls and (args.operator is not None or args.candidate is not None):
        ap.error('Controls and candidate verification are separate modes')

    def timeout(signum, frame):
        raise TimeoutError('Local-operation verification exceeded 1200 seconds')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(1200)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'lean').mkdir()
    checker = load_checker(args.checker, args.environment, args.output)
    provenance = json.loads(args.revisions.read_text())
    revisions = {key: provenance[key] for key in ('lean', 'mathlib')}
    installed_toolchain = (args.environment / 'mathlib/lean-toolchain').read_text().strip()
    if installed_toolchain != 'leanprover/lean4:' + revisions['lean']:
        raise ValueError('Installed toolchain differs from the supplied environment provenance')
    revisions['provenance_path'] = str(args.revisions)
    revisions['mathlib_provenance'] = 'Installed archive environment; revision recorded by its earlier preparation, not a live Git checkout'
    jobs = [(args.operator, args.candidate, None)]
    if args.controls:
        jobs = [(name, candidate, expected)
                for name, task in OPERATORS.items()
                for key, expected in [('correct', True), ('incorrect', False)]
                for candidate in task[key]]
    with (args.output / 'verification.jsonl').open('x') as out:
        for index, (name, candidate, expected) in enumerate(jobs):
            q, body = make_obligation(name, candidate)
            result = checker.check_candidate(q, body, f'candidate_{index}')
            record = dict(operator=name, candidate=candidate, obligation=q,
                          expected=expected, reward=reward_value(result),
                          reward_source='Lean acceptance of fixed candidate obligation', verification=result)
            out.write(json.dumps(record) + '\n')
            out.flush()
            if record['reward'] is None:
                raise RuntimeError('Verifier infrastructure failure: ' + result.get('output', ''))
            if expected is not None and result['accepted'] != expected:
                raise RuntimeError('Operator control failed: ' + name + ' ' + candidate)
            print(json.dumps({'operator': name, 'candidate': candidate,
                              'accepted': result['accepted'], 'expected': expected}), flush=True)
    manifest = {'status': 'complete', 'controls': args.controls, 'physical_lean_calls': len(jobs),
                'revisions': revisions, 'training_updates': 0,
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'checker_sha256': hashlib.sha256(args.checker.read_bytes()).hexdigest(),
                'scope': 'Fixed proof skeleton validates the candidate witness. No model-generated proof or prose is certified.'}
    (args.output / 'run.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
