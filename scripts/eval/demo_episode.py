"""Screen or demonstrate specified problems in the v2.1 Lean environment.

Problem file (JSON): name, informal, header (Lean statement without ":= by"), stage (optional).
Give exactly one of --problem FILE or --problems-dir DIR (every *.json, sorted; batch mode).
--screen loads no model. It checks that the statement compiles with `sorry`, then runs each fixed
tactic and trivial witness as a one-action episode; any accepted proof marks the statement trivial.
Without --screen, --model runs --samples episodes per problem with the evaluation sampling of
run_tactic_rl.py (temperature 1, full softmax). Attempt i uses torch seed --seed + i, so a rerun on
the same hardware repeats the sampling. The run prints the problem, the goal, each action, the Lean
result and the goals that remain, each verified proof, and accepted/samples (k/n). --show-observation
also prints the exact prompt before each action. A Lean timeout or REPL crash ends that attempt, is
shown as such, and the next attempt restarts the REPL. Ctrl-C stops early and still writes the summary.
Records go to a new --output directory: trace.jsonl (every step) and summary.json for one problem;
in batch mode one subdirectory per problem plus batch_summary.json, and a one-line table at the end.
--exclude FILE skips the problem names listed in FILE (one per line, # starts a comment) in batch mode.
--summary-table ends the run with one line per problem: name, statement, k/n, shortest verified proof.
--replay TRACE prints a recorded trace.jsonl (or a run directory, or a batch directory) in the live
format with no model and no Lean, for a presentation fallback. It reuses the live print code, so the
text matches the live run; the timings shown are the recorded ones. --replay-delay sets the pause
between printed steps. A trace recorded by an older version lacks the goal and proof text.
Peak: FP32 0.5B policy about 2 GiB plus one Lean REPL up to 4 GiB; run on the CPU fleet.
"""
import argparse
import json
from pathlib import Path
import sys
import textwrap
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'train'))
import run_tactic_rl as rl  # noqa: E402

# The single-tactic baseline list without `exact?`, which the environment forbids, plus trivial witnesses.
SCREEN = ['rfl', 'decide', 'norm_num', 'simp', 'omega', 'linarith', 'aesop', 'simp_all', 'tauto',
          'use 0\nsimp', 'exact ⟨0, by simp⟩']
LEAN_ERRORS = (RuntimeError, TimeoutError, EOFError, OSError)


class PromptCap(Exception):
    """The observation exceeds the prompt cap even with every earlier step hidden."""


def say(*parts):
    print(*parts, flush=True)


def block(text, prefix='    '):
    return textwrap.indent(str(text).rstrip() or '(empty)', prefix)


def show_problem(problem):
    say(f"\n########## {problem['name']}" + (f" (stage {problem['stage']})" if problem.get('stage') else '') + ' ##########')
    say(f"Problem: {problem['informal']}\nLean:\n{block(problem['header'])}")


def show_attempt_header(sample, samples, seed):
    say(f'\n=== Attempt {sample + 1} of {samples} (seed {seed}) ===')


def show_action(step, action, seconds, observation, show_observation):
    if show_observation:
        say(f'--- Prompt shown to the model at step {step + 1}:\n{block(observation)}')
    say(f'--- Step {step + 1}: model action ({seconds:.1f} s)\n{block(action.strip())}')


def show_lean(result, seconds, goals):
    line = f"Lean ({seconds:.1f} s): {result['status']}"
    if result.get('tactics_applied'):
        line += f", {result['tactics_applied']} tactic(s) applied"
    say(line)
    if result.get('error'):
        say('  Lean message: ' + ' '.join(str(result['error']).split())[:300])
    if result.get('harness_backtrack'):
        say('  The harness reverted the last verified step.')
    if result['status'] == 'timeout':
        say('Lean timed out; this attempt ends. The next attempt restarts the REPL.')
    elif result['status'] == 'crash':
        say('The Lean REPL crashed; this attempt ends. The next attempt restarts it.')
    elif not result.get('done') and goals:
        say('Goals now:\n' + block('\n'.join(goals)))


def show_result(name, accepted, done, outcomes):
    say(f"\nResult for {name}: {accepted}/{done} attempts proved (k/n = {accepted}/{done}); outcomes {outcomes}")


def proof_text(header, shown):
    return header + ' := by\n' + '\n'.join('  ' + line for text in shown for line in text.splitlines())


def load_problem(path):
    problem = json.loads(Path(path).read_text())
    missing = [key for key in ('name', 'informal', 'header') if not isinstance(problem.get(key), str)]
    if missing:
        raise ValueError(f'{path}: missing string field(s) {missing}')
    return problem


def make_task(problem):
    return dict(task_id=problem['name'] + ':whole:0', name=problem['name'], kind='whole',
                header=problem['header'], given='', state='')


class TorchPolicy:
    """Evaluation sampling of run_tactic_rl.py: temperature 1, top_p 1, top_k 0, no repetition penalty."""

    def __init__(self, model_path, max_action_tokens, max_prompt_tokens):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch, self.max_action_tokens, self.max_prompt_tokens = torch, max_action_tokens, max_prompt_tokens
        self.model = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype=torch.float32).eval()
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)

    def encode(self, content):
        text = self.tokenizer.apply_chat_template([{'role': 'user', 'content': content}],
                                                  tokenize=False, add_generation_prompt=True)
        return self.tokenizer(text, add_special_tokens=False)['input_ids']

    def seed(self, value):
        self.torch.manual_seed(value)

    def act(self, env):
        """Return (action text, observation text shown). Hides old steps only when the cap requires it."""
        for omit in range(len(env.shown) + 1):  # Same prompt-cap rule as the trainer.
            observation = env.observation(omit)
            ids = self.encode(observation)
            if len(ids) <= self.max_prompt_tokens:
                break
        else:
            raise PromptCap(f'prompt has {len(ids)} tokens, above the cap {self.max_prompt_tokens}')
        inputs = self.torch.tensor(ids)[None]
        with self.torch.no_grad():
            out = self.model.generate(input_ids=inputs, attention_mask=self.torch.ones_like(inputs),
                                      do_sample=True, temperature=1.0, top_p=1.0, top_k=0,
                                      repetition_penalty=1.0, max_new_tokens=self.max_action_tokens,
                                      pad_token_id=self.tokenizer.eos_token_id)[0]
        return self.tokenizer.decode(out[len(ids):], skip_special_tokens=True), observation


def write_trace(trace, record):
    trace.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    trace.flush()


def run_screen(env, trace):
    """Run each fixed tactic as a one-action episode. Return the summary fields."""
    try:
        env.reset()  # Raises if the statement does not compile with `sorry`.
    except LEAN_ERRORS as exc:
        say(f'Screen failed: the statement does not compile with sorry or Lean did not start.\n{block(exc)}')
        return dict(compiles=False, trivial=None, solved_by=[], error=str(exc), errors=1)
    solved, errors = [], 0
    for tactic in SCREEN:
        shown = tactic.replace('\n', '; ')
        try:
            env.reset()
            result = env.step(tactic, 0)
        except LEAN_ERRORS as exc:
            result, errors = dict(status='error', accepted=False, error=str(exc)), errors + 1
        write_trace(trace, dict(tactic=tactic, **result))
        say(f"  {shown:<22} {'ACCEPTED' if result['accepted'] else result['status']}")
        if result['accepted']:
            solved.append(tactic)
    say(f"Screen: compiles with sorry; {'trivial, closed by ' + str(solved) if solved else 'not closed by any fixed tactic'}")
    return dict(compiles=True, trivial=bool(solved), solved_by=solved, errors=errors)


def run_attempt(env, policy, sample, args, trace, problem_name):
    """One episode with printing. Return dict(outcome, steps, proof, error)."""
    seed = args.seed + sample
    show_attempt_header(sample, args.samples, seed)
    key = dict(problem=problem_name, sample=sample)
    try:
        env.reset()
    except LEAN_ERRORS as exc:
        say(f'Lean setup failed, attempt skipped:\n{block(str(exc)[:400])}')
        write_trace(trace, dict(event='attempt_start', samples=args.samples, seed=seed, goals=None, **key))
        write_trace(trace, dict(event='attempt_end', outcome='error', steps=0, proof=None, error=str(exc),
                                stop='setup', **key))
        return dict(outcome='error', steps=0, proof=None, error=str(exc))
    goals = list(env.goals)
    say('Goal:\n' + block('\n'.join(goals)))
    write_trace(trace, dict(event='attempt_start', samples=args.samples, seed=seed, goals=goals, **key))
    policy.seed(seed)
    result, steps, outcome, error, stop = dict(status='horizon', accepted=False), 0, 'not_proved', None, None
    for step in range(args.horizon):
        start = time.monotonic()
        try:
            action, observation = policy.act(env)
        except PromptCap as exc:
            say(f'Stopped: {exc}.')
            outcome, error, stop = 'prompt_cap', str(exc), 'prompt_cap'
            break
        except Exception as exc:  # A live demo must continue after a generation failure.
            error = f'{type(exc).__name__}: {exc}'
            say('Generation failed: ' + error[:300])
            outcome, stop = 'error', 'generation'
            break
        generated = time.monotonic() - start
        show_action(step, action, generated, observation, args.show_observation)
        start = time.monotonic()
        try:
            result = env.step(action, 0)
        except LEAN_ERRORS as exc:
            error = f'{type(exc).__name__}: {exc}'
            say('Lean failed during the step: ' + error[:300])
            outcome = 'error'
            env.close()  # The next reset starts a fresh REPL.
            write_trace(trace, dict(event='step', step=step, observation=observation, action=action,
                                    status='error', accepted=False, error=error, generated_seconds=generated,
                                    **key))
            break
        steps += 1
        lean_seconds = time.monotonic() - start
        show_lean(result, lean_seconds, env.goals)
        write_trace(trace, dict(event='step', step=step, observation=observation, action=action,
                                generated_seconds=generated, lean_seconds=lean_seconds, goals=list(env.goals),
                                **key, **result))
        if result['done']:
            break
    proof = None
    if result.get('accepted'):
        outcome, proof = 'proved', proof_text(env.task['header'], env.shown)
        say('Verified proof (full-proof replay accepted):\n' + block(proof))
    elif outcome == 'not_proved' and result.get('status') in ('timeout', 'crash'):
        outcome = result['status']
    say(f"Outcome: {outcome.replace('_', ' ')}")
    write_trace(trace, dict(event='attempt_end', outcome=outcome, steps=steps, proof=proof, error=error,
                            stop=stop, **key))
    return dict(outcome=outcome, steps=steps, proof=proof, error=error)


def proof_body(proof):
    return [line.strip() for line in proof.split(':= by', 1)[-1].splitlines() if line.strip()]


def shortest_proof(proofs):
    """The proof with the fewest tactic lines, ties broken by length, or None."""
    return min(proofs, key=lambda text: (len(proof_body(text)), len(text)), default=None)


def run_demo(env, policy, args, trace, problem):
    accepted, proofs, outcomes, attempts, interrupted = 0, [], {}, [], False
    for sample in range(args.samples):
        try:
            attempt = run_attempt(env, policy, sample, args, trace, problem['name'])
        except KeyboardInterrupt:
            say('\nInterrupted: stopping after the attempts so far.')
            interrupted = True
            break
        attempts.append(attempt)
        outcomes[attempt['outcome']] = outcomes.get(attempt['outcome'], 0) + 1
        if attempt['outcome'] == 'proved':
            accepted += 1
            proofs.append(attempt['proof'])
    done = len(attempts)
    show_result(problem['name'], accepted, done, outcomes)
    return dict(model=str(args.model), samples=args.samples, attempts=done, accepted=accepted, k_over_n=f'{accepted}/{done}',
                outcomes=outcomes, proofs=proofs, shortest_proof=shortest_proof(proofs), horizon=args.horizon, seed=args.seed, interrupted=interrupted,
                errors=outcomes.get('error', 0))


def run_problem(ctx, problem, policy, args, out_dir):
    """Run one problem in its own output directory. Return its summary dict."""
    counts = dict(lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0)
    (out_dir / 'repl').mkdir(exist_ok=True)  # The REPL wrapper appends its log to out_dir/repl/episodes.log.
    env = rl.LeanEpisode(ctx, make_task(problem), out_dir, counts, version=2, horizon=args.horizon)
    summary = dict(problem=problem, mode='screen' if args.screen else 'demo')
    show_problem(problem)
    started = time.monotonic()
    with (out_dir / 'trace.jsonl').open('x') as trace:
        if not args.screen:
            write_trace(trace, dict(event='problem', problem=problem, samples=args.samples, horizon=args.horizon,
                                    model=str(args.model)))
        try:
            summary.update(run_screen(env, trace) if args.screen else run_demo(env, policy, args, trace, problem))
        finally:
            env.close()
    summary['lean'] = counts
    summary['wall_seconds'] = time.monotonic() - started
    (out_dir / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    return summary


def table_line(summary):
    name = summary['problem']['name']
    if summary['mode'] == 'screen':
        status = 'does not compile' if not summary['compiles'] else ('TRIVIAL ' + str(summary['solved_by'])
                                                                    if summary['trivial'] else 'not trivial')
        return f'{name:<28} screen: {status}'
    return (f"{name:<28} k/n {summary['k_over_n']:<6} {summary['outcomes']}  "
            f"lean {summary['lean']['lean_calls']} calls / {summary['lean']['lean_seconds']:.0f} s")


def summary_row(problem, accepted, done, proofs):
    best = shortest_proof(proofs)
    return dict(name=problem['name'], informal=problem['informal'], k_over_n=f'{accepted}/{done}',
                proof='; '.join(proof_body(best)) if best else '—')  # Display only: one line per problem.


def print_summary_table(rows):
    """One line per problem: name | statement | k/n | shortest verified proof."""
    say('\n=== Summary table (name | statement | k/n | shortest verified proof) ===')
    for row in rows:
        say(f"{row['name']} | {row['informal']} | {row['k_over_n']} | {row['proof']}")


def read_exclude(path):
    """Names from an --exclude file: one per line, blank lines and # comments ignored."""
    names = [line.split('#', 1)[0].strip() for line in Path(path).read_text().splitlines()]
    return {name for name in names if name}


def find_traces(path):
    path = Path(path)
    if path.is_file():
        return [path]
    if (path / 'trace.jsonl').is_file():
        return [path / 'trace.jsonl']
    found = sorted(child / 'trace.jsonl' for child in path.iterdir() if (child / 'trace.jsonl').is_file()) \
        if path.is_dir() else []
    if not found:
        raise SystemExit(f'No trace.jsonl found at {path}')
    return found


def load_trace(path):
    """Return (problem, attempts). Each attempt is dict(sample, start, steps, end) in trace order."""
    records = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    if not records:
        raise SystemExit(f'{path} is empty')
    if all('tactic' in r and 'event' not in r for r in records):
        raise SystemExit(f'{path} is a --screen trace; it has no model steps to replay')
    problem, attempts = None, {}
    for r in records:
        event = r.get('event') or ('step' if 'step' in r else None)
        if event == 'problem':
            problem = r['problem']
        elif event in ('attempt_start', 'step', 'attempt_end'):
            at = attempts.setdefault(r['sample'], dict(sample=r['sample'], start=None, steps=[], end=None))
            if event == 'step':
                at['steps'].append(r)
            else:
                at['start' if event == 'attempt_start' else 'end'] = r
    if problem is None:  # Older trace: take the statement from the summary next to it, if there is one.
        name = next((r['problem'] for r in records if 'problem' in r and isinstance(r['problem'], str)), Path(path).parent.name)
        beside = Path(path).parent / 'summary.json'
        problem = json.loads(beside.read_text())['problem'] if beside.is_file() else \
            dict(name=name, informal='(not recorded)', header='(not recorded)')
    return problem, [attempts[k] for k in sorted(attempts)]


def pause(delay):
    if delay > 0:
        time.sleep(delay)


def replay_attempt(attempt, samples, args):
    """Print one recorded attempt with the live print functions. Return (outcome, proof)."""
    start, end = attempt['start'] or {}, attempt['end']
    show_attempt_header(attempt['sample'], start.get('samples', samples), start.get('seed', '?'))
    pause(args.replay_delay)
    if start.get('goals') is None and end and end.get('stop') == 'setup':
        say(f"Lean setup failed, attempt skipped:\n{block(str(end['error'])[:400])}")
    elif start.get('goals') is not None:
        say('Goal:\n' + block('\n'.join(start['goals'])))
    last = None
    for r in attempt['steps']:
        pause(args.replay_delay)
        show_action(r['step'], r['action'], r.get('generated_seconds', 0.0), r.get('observation', ''), args.show_observation)
        pause(args.replay_delay)
        if r['status'] == 'error' and 'goals' not in r:
            say('Lean failed during the step: ' + str(r.get('error', ''))[:300])
        else:
            show_lean(r, r.get('lean_seconds', 0.0), r.get('goals', []))
        last = r
    if end and end.get('stop') == 'prompt_cap':
        say(f"Stopped: {end['error']}.")
    elif end and end.get('stop') == 'generation':
        say('Generation failed: ' + str(end['error'])[:300])
    if end:
        outcome, proof = end['outcome'], end.get('proof')
    else:  # Older trace without attempt records.
        outcome = 'proved' if last and last.get('accepted') else (last or {}).get('status', 'not_proved')
        outcome = outcome if outcome in ('proved', 'timeout', 'crash') else 'not_proved'
        proof = None
    pause(args.replay_delay)
    if outcome == 'proved':
        say('Verified proof (full-proof replay accepted):\n' + block(proof or '(proof text not recorded in this trace)'))
    say(f"Outcome: {outcome.replace('_', ' ')}")
    return outcome, proof


def run_replay(args):
    loaded = [load_trace(path) for path in find_traces(args.replay)]
    excluded = read_exclude(args.exclude) if args.exclude else set()
    rows = []
    for problem, attempts in loaded:
        if problem['name'] in excluded:
            say(f"Skipping excluded problem {problem['name']}")
            continue
        show_problem(problem)
        accepted, outcomes = 0, {}
        proofs = []
        for attempt in attempts:
            outcome, proof = replay_attempt(attempt, len(attempts), args)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if outcome == 'proved':
                accepted += 1
                proofs.extend([proof] if proof else [])
        show_result(problem['name'], accepted, len(attempts), outcomes)
        rows.append(summary_row(problem, accepted, len(attempts), proofs))
    if args.summary_table:
        print_summary_table(rows)
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--problem', type=Path)
    source.add_argument('--problems-dir', type=Path)
    source.add_argument('--replay', type=Path, help='Print a recorded trace.jsonl, run directory or batch directory')
    for name in ('checker', 'environment', 'repl', 'output'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--screen', action='store_true')
    parser.add_argument('--show-observation', action='store_true', help='Print the exact prompt before each action')
    parser.add_argument('--exclude', type=Path, help='File of problem names to skip (batch and replay modes)')
    parser.add_argument('--summary-table', action='store_true',
                        help='End with one line per problem: name, statement, k/n, shortest verified proof')
    parser.add_argument('--replay-delay', type=float, default=0.6, help='Seconds between replayed steps')
    parser.add_argument('--samples', type=int, default=8)
    parser.add_argument('--horizon', type=int, default=6)
    parser.add_argument('--max-action-tokens', type=int, default=192)
    parser.add_argument('--max-prompt-tokens', type=int, default=2048)
    parser.add_argument('--seed', type=int, default=20261013)
    args = parser.parse_args(argv)
    if args.replay is not None:
        if args.screen or args.model:
            parser.error('--replay takes neither --screen nor --model')
        return args
    if not (args.problem or args.problems_dir):
        parser.error('Give one of --problem, --problems-dir or --replay')
    missing = [name for name in ('checker', 'environment', 'repl', 'output') if getattr(args, name) is None]
    if missing:
        parser.error('Missing --' + ', --'.join(missing))
    if args.screen == bool(args.model):
        parser.error('Give exactly one of --screen or --model')
    if args.screen and args.summary_table:
        parser.error('--summary-table needs model attempts; it does not apply to --screen')
    if args.exclude and not args.problems_dir:
        parser.error('--exclude applies to --problems-dir or --replay')
    if args.samples < 1 or args.horizon < 1:
        parser.error('--samples and --horizon must be at least 1')
    return args


def main(argv=None, policy_factory=TorchPolicy):
    args = parse_args(argv)
    if args.replay is not None:
        return run_replay(args)
    if args.problems_dir is not None:
        files = sorted(args.problems_dir.glob('*.json'))
        if not files:
            raise SystemExit(f'No *.json problem files in {args.problems_dir}')
    else:
        files = [args.problem]
    problems = [load_problem(path) for path in files]  # Fail on a bad file before any Lean or model start.
    if len({p['name'] for p in problems}) != len(problems):
        raise SystemExit('Problem names must be unique')
    if args.exclude:
        excluded = read_exclude(args.exclude)
        unknown = excluded - {p['name'] for p in problems}
        if unknown:
            say(f'Note: --exclude names no problem in the directory: {sorted(unknown)}')
        problems = [p for p in problems if p['name'] not in excluded]
        say(f'Excluded {len(excluded - unknown)} problem(s); running {len(problems)}: {[p["name"] for p in problems]}')
        if not problems:
            raise SystemExit('Every problem is excluded')
    args.output.mkdir(exist_ok=False)
    policy = None if args.screen else policy_factory(args.model, args.max_action_tokens, args.max_prompt_tokens)
    checker = rl.load_checker(args.checker, args.environment, args.output)
    ctx = rl.repl_context(checker, args.environment.resolve(), args.repl, args.output, 20, 120, 32)
    summaries, interrupted = [], False
    try:
        for problem in problems:
            out_dir = args.output / problem['name'] if args.problems_dir is not None else args.output
            if out_dir != args.output:
                out_dir.mkdir()
            summaries.append(run_problem(ctx, problem, policy, args, out_dir))
            if summaries[-1].get('interrupted'):
                interrupted = True
                break
    finally:
        for repl in ctx.pool_repls:
            try:
                rl.close_repl(repl)
            except Exception:  # A crashed REPL may already be closed.
                pass
    if args.problems_dir is not None:
        say('\n=== Batch summary ===')
        for summary in summaries:
            say(table_line(summary))
        (args.output / 'batch_summary.json').write_text(json.dumps(
            [{k: v for k, v in s.items() if k != 'proofs'} for s in summaries], indent=2, ensure_ascii=False) + '\n')
    else:
        say(json.dumps({k: v for k, v in summaries[0].items() if k != 'proofs'}, ensure_ascii=False))
    if args.summary_table:
        print_summary_table([summary_row(s['problem'], s['accepted'], s['attempts'], s['proofs']) for s in summaries])
    return 1 if interrupted or any(s.get('errors') for s in summaries) else 0


if __name__ == '__main__':
    sys.exit(main())
