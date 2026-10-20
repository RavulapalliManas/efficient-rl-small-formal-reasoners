"""On-policy tactic RL with Lean transitions and a leave-one-out baseline.

One group samples independent trajectories from the same training root. One
Adam update uses undiscounted reward-to-go, subtracting the other trajectories'
mean total return. Sum action-token log probabilities; average over trajectories.
Sampling and differentiable replay use the same unwarped policy, with dropout
disabled. No off-policy replay, PPO epochs, reference model, or SFT updates.

Reward: 1 for existing verify_group whole-proof/axiom acceptance. Changed goal
text gets heuristic credit totaling at most exploration_bonus per episode.
The opt-in error_feedback_v1 mode replaces that bonus with diagnostic penalties:
-0.2 for format, syntax, forbidden code or unresolved names; -0.1 for type or
tactic failures; zero for valid unfinished steps and unclassified/infra errors.
These weights and diagnostic classes are heuristic, not proof-progress scores.
In environment v2 the same mode charges one penalty per action, because errors do
not end a v2 episode: unchanged goals, repeated states and unclassified Lean
rejections also cost -0.1, so repeating an action that changes nothing cannot
avoid the penalties. Only an accepted complete proof earns positive reward.
Repeated displayed states stop an episode. An optional single error repair uses
the last verified state and a bounded Lean diagnostic, within the same caps.
An action is one bounded tactic block, possibly containing nested tactics.
This does not replicate AST/first-token process GRPO in arXiv:2606.20068.

Environment v2 (config environment_version 2), aligned with DeepSeek-Prover-V1.5
truncate-and-resume (arXiv:2408.08152) and COPRA failure memory (arXiv:2310.04353).
State: theorem header, supplied proof lines, the policy's accepted tactics,
current goals, tactics that already failed at the current state, last action
result and actions remaining. The observation renders it as a Lean file with a
"/- tactic state: ... -/" comment, hiding the oldest policy steps only when the
prompt cap requires it. Action: up to max_chunk_tactics top-level tactics, each
optionally preceded by "--" comment lines; in the policy-backtrack ablation arm,
"backtrack k" alone also returns to the state after step k. Lean runs the
tactics in order and keeps the valid prefix; the first error, unchanged goals or
cycle on the current path stops the action and enters the observation. An
action that applies no tactic is a self-loop; so are malformed or forbidden
actions and invalid backtracks. Each action uses one step. In the default
harness-backtrack arm, once harness_backtrack_after distinct tactics fail at a
state, the harness reverts the last step and records it as failed at the parent.
Closed goals end the episode with the full-proof replay; a Lean timeout ends it
with reward 0; a REPL crash censors the group. The default reward is terminal
and Lean-verified only; error_feedback_v1 adds the heuristic per-action penalties
above. Returns use the configured discount; supplied lines earn
full reward but stay visible and labelled, and every record keeps the task kind.

Peak: P x 16 bytes for FP32 weights, gradients and Adam moments (<8 GiB for
0.5B), one microbatch logits <=1088 x 152064 x 4 bytes (<0.7 GiB), activations
reserved 6 GiB, and at most two Lean REPLs x 4 GiB. Run under 24 GiB host cap.
CUDA uses FP32 master weights/moments and BF16 compute, one action at a time.
No dataset/proof/model artifacts belong on the Mac. Managed runner must upload
incremental JSONL records and checkpoints and enforce the resource/wall cap.
"""
import argparse
from collections import Counter
from contextlib import nullcontext
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ('train', 'data', 'eval', 'audit')]
from bench_lean_verifier import Repl, VerifierSetupError, first_error, repl_context, repl_output, split_prefix, verify_group  # noqa: E402
from classify_proof_failures import classify as classify_failure  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import header, lean_unit  # noqa: E402
from probe_start_depth import FORBIDDEN, FORBIDDEN_CODE  # noqa: E402
from replay_training_proofs import load, proof_body, question  # noqa: E402
from run_lean_grpo import task_units  # noqa: E402
from search_tactics import STEP  # noqa: E402
from verify_external_proofs import HEADER  # noqa: E402
from rl_components import LeaveOneOutObjective, PolicyGradientUpdater, TaskCurriculum, TaskSchedule, component_fingerprints  # noqa: E402
from rl_components.proofs import tactic_block  # noqa: E402


def generation_record(ids, eos_token_id, pad_token_id):
    """Keep EOS in the action likelihood; distinguish retained tokens from decode work."""
    stops = eos_token_id if isinstance(eos_token_id, list) else [eos_token_id]
    end = next((i + 1 for i, token in enumerate(ids) if token in stops), len(ids))
    action = ids[:end]
    return dict(raw_response_ids=ids, action_ids=action, generated_tokens=len(ids),
                retained_tokens=len(action), pad_before_eos=any(
                    token == pad_token_id and token not in stops for token in action))


def stop_trimmed(ids, stops):
    """Cut batch padding after the first stop token; one unpadded sample is unchanged."""
    end = next((i + 1 for i, token in enumerate(ids) if token in stops), len(ids))
    return ids[:end]


def pad_left(prompts, pad_id):
    """Left-pad token lists for one batched generate call; the attention mask hides every pad."""
    width = max(len(p) for p in prompts)
    return ([[pad_id] * (width - len(p)) + list(p) for p in prompts],
            [[0] * (width - len(p)) + [1] * len(p) for p in prompts], width)


def vllm_action_ids(completion, stops):
    """Token ids of one vLLM completion with its terminating stop token kept, as the HF path keeps it."""
    ids = list(completion.token_ids)
    if completion.finish_reason == 'stop' and (not ids or ids[-1] not in stops):
        reason = completion.stop_reason
        ids.append(reason if isinstance(reason, int) and reason in stops else stops[0])
    return ids


def step_seed(seed, task_id, visit, sample, step):
    """Reproducible sampling stream per (task visit, sample, step) for lockstep rollouts."""
    return int(hashlib.sha256(f'{seed}|{task_id}|{visit}|{sample}|{step}'.encode()).hexdigest()[:8], 16)


class HFGenerator:
    """Batched sampling from the live training model: several prompts, n samples each, one generate call.

    Uses the trainer's untruncated temperature-1 policy. Rows share one random stream seeded from the
    batch's per-sample seeds, so a sample is reproducible for the same active set, not for any batch."""

    def __init__(self, model, tokenizer, torch, autocast, device, stops, max_new_tokens, set_seed):
        self.model, self.tokenizer, self.torch, self.autocast, self.device = model, tokenizer, torch, autocast, device
        self.stops, self.max_new_tokens, self.set_seed = stops, max_new_tokens, set_seed

    def __call__(self, prompts, n, seeds):
        torch = self.torch
        self.set_seed(int(hashlib.sha256(repr(list(seeds)).encode()).hexdigest()[:8], 16))
        ids, mask, width = pad_left(prompts, self.tokenizer.pad_token_id)
        with torch.no_grad(), self.autocast():
            rows = self.model.generate(
                input_ids=torch.tensor(ids, device=self.device), attention_mask=torch.tensor(mask, device=self.device),
                do_sample=True, temperature=1.0, top_p=1.0, top_k=0, repetition_penalty=1.0,
                max_new_tokens=self.max_new_tokens, pad_token_id=self.tokenizer.eos_token_id,
                num_return_sequences=n)[:, width:].tolist()
        return [[generation_record(stop_trimmed(rows[i * n + j], self.stops), self.stops, self.tokenizer.pad_token_id)
                 for j in range(n)] for i in range(len(prompts))]


class VLLMGenerator:
    """Evaluation-only sampling through vLLM with the same untruncated temperature-1 policy.

    Each prompt carries its own request seed, so a sample does not depend on the rest of the batch.
    vLLM samples in bfloat16; check_rollout_backends.py must pass before its results count."""

    def __init__(self, model_path, stops, pad_id, max_new_tokens, memory_fraction, seed):
        from vllm import LLM
        self.llm = LLM(model=str(model_path), dtype='bfloat16', seed=seed, gpu_memory_utilization=memory_fraction,
                       enable_prefix_caching=True)
        self.stops, self.pad_id, self.max_new_tokens = stops, pad_id, max_new_tokens

    def __call__(self, prompts, n, seeds):
        from vllm import SamplingParams
        params = [SamplingParams(n=n, temperature=1.0, top_p=1.0, top_k=-1, repetition_penalty=1.0, seed=s,
                                 max_tokens=self.max_new_tokens, stop_token_ids=list(self.stops), detokenize=False)
                  for s in seeds]
        outputs = self.llm.generate([dict(prompt_token_ids=list(p)) for p in prompts], params, use_tqdm=False)
        return [[generation_record(stop_trimmed(vllm_action_ids(c, self.stops), self.stops), self.stops, self.pad_id)
                 for c in output.outputs] for output in outputs]


def state_key(goals):
    return tuple(' '.join(goal.split()) for goal in goals)


def close_repl(repl):
    repl.close()
    for stream in (repl.proc.stdin, repl.proc.stdout):
        try:
            stream.close()
        except OSError:
            pass  # A dead REPL leaves unflushed bytes in a broken pipe; closing must still finish.


def transition(response, seen, bonus):
    result = dict(status='rejected', reward=0.0, accepted=False, done=True)
    code, output = repl_output(response)
    if code or response.get('sorries') or 'goals' not in response:
        return dict(result, error=output)
    if not response['goals']:
        return dict(result, status='needs_replay')
    key = state_key(response['goals'])
    if key in seen:
        return dict(result, status='repeated_state')
    return dict(result, status='changed', reward=bonus, done=False, state_key=key)


def finish(result, replay):
    accepted = replay['status'] == 'accepted'
    return dict(result, status='accepted' if accepted else 'replay_' + replay['status'],
                reward=float(accepted), accepted=accepted, replay=replay)


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
    """Split lines into logical lines and strip comments the way Lean reads them.

    A line that starts inside a block comment, a string or an open bracket continues the line above it.
    Returns (groups, code): each group is dict(lines=[...], code=bool); code is the text without comments,
    with string literals kept, for the forbidden-construct filter."""
    groups, out = [], []
    depth, in_string, raw, brackets = 0, False, None, 0
    for line in lines:
        if groups and (depth or in_string or raw is not None or brackets):
            group = groups[-1]
        else:
            group = dict(lines=[], code=False)
            groups.append(group)
        group['lines'].append(line)
        i = 0
        while i < len(line):
            ch, two = line[i], line[i:i + 2]
            before = line[i - 1] if i else ' '
            ident = before.isalnum() or before in "_'!?"
            if depth:
                depth += (two == '/-') - (two == '-/')
                i += 2 if two in ('/-', '-/') else 1
                continue
            if raw is not None:
                out.append(ch)
                if line.startswith('"' + '#' * raw, i):
                    out.append('#' * raw)
                    i, raw = i + 1 + raw, None
                else:
                    i += 1
                continue
            if in_string:
                out.append(line[i:i + 2] if ch == '\\' else ch)
                in_string = ch == '\\' or ch != '"'
                i += 2 if ch == '\\' else 1
                continue
            if two == '/-':
                depth, i = 1, i + 2
                continue
            if two == '--':
                break
            group['code'] = group['code'] or not ch.isspace()
            match = None if ident else (RAW_STRING.match(line, i) if ch == 'r' else CHAR_LITERAL.match(line, i) if ch == "'" else None)
            if match and ch == 'r':
                out.append(match.group())
                raw, i = len(match.group(1)), match.end()
            elif match:
                out.append(match.group())
                i = match.end()
            else:
                in_string = ch == '"'
                brackets = max(brackets + (ch in '([{⟨') - (ch in ')]}⟩'), 0)
                out.append(ch)
                i += 1
        out.append('\n')
    return groups, ''.join(out)


def forbidden(text):
    """True when the code of an action uses a forbidden construct; comments are ignored."""
    return bool(FORBIDDEN.search(text) or FORBIDDEN_CODE.search(lex_lines(text.splitlines())[1]))


def action_positions(text):
    """Lean positions refer to the "first |" wrapper: one extra line above and two columns of indentation."""
    return re.sub(r'<input>:(\d+):(\d+)', lambda m: f'<input>:{max(int(m[1]) - 1, 1)}:{max(int(m[2]) - 2, 0)}', text)


def cap_message(prefix, message, note='', limit=600):
    """Shorten the message, never the note: the note tells the policy that the harness changed its state."""
    return prefix + message[:max(limit - len(prefix) - len(note), 0)] + note


def parse_action(text, max_tactics=4, allow_backtrack=False):
    """v2 action: up to max_tactics top-level tactics with leading comments, or "backtrack k".

    Accepted fence labels: lean and lean4, in any letter case. An unlabelled fence, any other label, prose
    around the fence, and more than one fence are format errors. Leading tabs become two spaces, because Lean
    rejects tabs."""
    # SFT emits code fences. Accept one complete block without repairing code.
    fenced = FENCE.fullmatch(text.strip())
    body = fenced.group(1) if fenced else text  # Keep first-line indentation for dedent.
    if not body.strip() or '```' in body:
        return dict(kind='malformed')
    body = '\n'.join(re.sub(r'^[ \t]*', lambda m: m.group().replace('\t', '  '), line) for line in body.splitlines())
    lines = [line for line in textwrap.dedent(body).splitlines() if line.strip()]
    # SFT proof targets restate the declaration; drop it through its ":= by", never executing it.
    first = next((i for i, line in enumerate(lines) if not line.lstrip().startswith(('--', '/-'))), None)
    if first is not None and DECLARATION.match(lines[first].lstrip()):
        end = next((i for i in range(first, len(lines)) if lines[i].rstrip().endswith(':= by')), None)
        if end is None:
            return dict(kind='malformed', reason='a restated theorem header must end with ":= by".')
        lines = lines[:first] + lines[end + 1:]
    groups, _ = lex_lines(lines)
    code = [group for group in groups if group['code']]
    if not code:
        return dict(kind='malformed')
    head = lambda group: group['lines'][0].lstrip()
    indent = lambda group: len(group['lines'][0]) - len(head(group))
    jump = BACKTRACK.fullmatch(head(code[0]).strip()) if len(code) == 1 and len(code[0]['lines']) == 1 else None
    if jump:
        if not allow_backtrack:
            return dict(kind='malformed', reason='backtrack is not an action here; the harness backtracks.')
        return dict(kind='backtrack', step=int(jump.group(1)))
    # A tactic starts at the shallowest code indentation; deeper lines and continuation lines extend it.
    base = min(indent(group) for group in (code if all(CONTINUATION.match(head(g)) for g in code) else
                                           [g for g in code if not CONTINUATION.match(head(g))]))
    tactics, pending = [], []
    for group in groups:
        top = indent(group) <= base and not CONTINUATION.match(head(group))
        if not group['code'] and (top or not tactics):
            pending += group['lines']
        elif top or not tactics:
            tactics.append(pending + group['lines'])
            pending = []
        else:
            tactics[-1] += pending + group['lines']
            pending = []
    tactics[-1] += pending
    if len(tactics) > max_tactics:
        return dict(kind='malformed', reason=f'write at most {max_tactics} tactics per action.')
    # Remove the base indentation; shallower comment lines lose all of theirs.
    strip = lambda line: line[base:] if not line[:base].strip() else line.lstrip()
    return dict(kind='tactics', tactics=['\n'.join(strip(line) for line in t) for t in tactics])


def local_feedback(result):
    """Local validity feedback. No positive credit without whole-proof acceptance."""
    status, reward, component = result['status'], 0.0, result['status']
    if result['accepted']:
        reward, component = 1.0, 'complete_proof'
    elif status in ('format_error', 'forbidden_construct', 'replay_forbidden_construct'):
        reward = -0.2
    elif status == 'replay_rejected':
        reward = -0.1
    elif status == 'rejected':
        message = first_error(result.get('error', '')) or ''
        message = re.sub(r'^<input>:\d+:\d+:\s*', '', message)
        component = classify_failure(dict(status='rejected', first_error=message))
        if message.startswith('unexpected end of input'):
            component = 'syntax_or_structure'
        if component == 'syntax_or_structure':
            reward = -0.2
        elif component == 'name_or_type_resolution' and message.startswith(
                ('unknown identifier', 'unknown constant', 'unknown tactic')):
            reward, component = -0.2, 'unresolved_name'
        elif component in ('name_or_type_resolution', 'proof_construction'):
            reward = -0.1
    return dict(result, reward=reward, reward_component=component)


def v2_feedback(result):
    """error_feedback_v1 for environment v2: one penalty per action, positive reward only for an accepted proof.

    Format, syntax, forbidden code and unresolved names cost -0.2; type and tactic failures, unchanged goals,
    repeated states, unclassified Lean rejections and rejected replays cost -0.1. Valid steps, timeouts, crashes
    and prompt-cap endings cost nothing. A partial action is scored by the failure that stopped it."""
    kind = result.get('failure', result['status'])
    if kind in ('no_progress', 'cycle'):
        return dict(result, reward=-0.1, reward_component=kind)
    if kind == 'invalid_backtrack':
        return dict(result, reward=-0.2, reward_component=kind)
    scored = local_feedback(dict(result, status=kind if kind == 'rejected' else result['status']))
    if kind == 'rejected' and not scored['reward']:
        scored = dict(scored, reward=-0.1)
    return dict(scored, status=result['status'])


def bound_state(result, prompt_tokens, maximum):
    if not result['done'] and prompt_tokens > maximum:
        return dict(result, status='prompt_cap', transition_status=result['status'], done=True)
    return result


def can_export(run, updates):
    return bool(updates and run['status'] in ('completed', 'capped') and run.get('phase') != 'update')


def mathlib_identity(path, expected):
    identity = json.loads(path.read_text())
    if identity['mathlib_revision'] != expected or identity['mismatches'] or not identity['source']:
        raise ValueError('Mathlib source receipt does not establish the registered pin')
    return identity


def advantages(rewards, gamma=1.0):
    """Each baseline excludes its trajectory, including its earlier rewards.

    Step t gets sum_{k>=t} gamma^k r_k minus the other trajectories' mean discounted return from the
    start, so both terms are measured from step 0. This is the exact gradient of the discounted
    objective; groups with equal returns give zero advantage. gamma = 1 reproduces the old values."""
    return LeaveOneOutObjective(gamma).advantages(rewards)


def training_tasks(tasks, pool, excluded, max_remaining):
    return TaskCurriculum(pool, excluded, max_remaining, validate_units=task_units).training(tasks)


def assessment_tasks(tasks, pool, excluded, max_remaining, split='train'):
    if split == 'train':
        return training_tasks(tasks, pool, excluded, max_remaining)
    return TaskCurriculum(pool, excluded, max_remaining, validate_units=task_units).assessment(tasks, split)


def bank_tasks(bank, rows, excluded_files, eval_spans):
    """Admit original-context training units; keep reference suffixes outside policy records."""
    by_id = {row['theorem_id']: row for row in rows}
    if len(by_id) != len(rows) or len({u['task_id'] for u in bank}) != len(bank):
        raise ValueError('Duplicate bank task or training theorem IDs')
    keys = ('task_id', 'theorem_id', 'full_name', 'file_path', 'header', 'k', 'steps_total',
            'steps_remaining', 'state', 'state_source', 'given_steps', 'topic')
    tasks = []
    for unit in bank:
        row = by_id.get(unit['theorem_id'])
        if row is None or row.get('official_split') != 'train':
            raise ValueError('Bank theorem is not an official training row')
        if unit['file_path'] in excluded_files or unit['file_path'] in eval_spans:
            raise ValueError('Bank theorem lies in an excluded or evaluation file')
        if (unit['header'] != row['declaration_header'] or unit['file_path'] != row['file_path']
                or unit['full_name'] != row['full_name']):
            raise ValueError('Bank theorem metadata differs from the training row')
        if (unit['k'] != len(unit['given_steps']) or not 0 <= unit['k'] < unit['steps_total']
                or unit['steps_remaining'] != unit['steps_total'] - unit['k']
                or any(not step.startswith('  ') or FORBIDDEN.search(step) for step in unit['given_steps'])):
            raise ValueError('Invalid bank prefix or remaining-step metadata')
        tasks.append(dict({key: unit[key] for key in keys if key in unit}, kind='bank', name=row['full_name']))
    return tasks


def evaluation_tasks(pool, names, dev):
    if not names or not names <= dev:
        raise ValueError('Evaluation names must be a nonempty subset of DEV')
    rows = {r['name']: r for r in pool}
    if not names <= rows.keys():
        raise ValueError('Evaluation name missing from pool')
    return [dict(task_id='dev:' + name, name=name, kind='whole', header=header(rows[name]),
                 given='', state='') for name in sorted(names)]


def task_order(tasks, seed):
    """Equal root exposure for whole and assisted tasks, shuffled within each kind."""
    return TaskSchedule(tasks).balanced(seed)


def scheduled_tasks(tasks, order, max_groups):
    """Consume a frozen schedule exactly; repeats specify explicit exposure."""
    return TaskSchedule(tasks).frozen(order, max_groups)


def pass_rate_filter(tasks, spec):
    """Keep training tasks whose measured pass rate lies in (min_exclusive, max_inclusive].

    Rates come from steps.jsonl of a completed or capped training-state assessment, so no DEV or held-out
    outcome selects training tasks. Only complete groups are recorded; a task without a measured rate is dropped.
    """
    source = Path(spec['pass_rates_from'])
    run = json.loads((source / 'run.json').read_text())
    if run.get('mode') != 'training_state_assessment' or run.get('status') not in ('completed', 'capped'):
        raise ValueError('Pass rates must come from a completed or capped training-state assessment run')
    accepted, attempts = Counter(), Counter()
    with (source / 'steps.jsonl').open() as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('status') == 'censored':
                continue
            if row['task_id'].startswith('dev:') or any(v not in (0, 1) for v in row['returns']):
                raise ValueError('Pass rates need training tasks with terminal 0/1 returns')
            accepted[row['task_id']] += sum(row['returns'])
            attempts[row['task_id']] += len(row['returns'])
    kept, dropped = [], Counter()
    for task in tasks:
        n = attempts[task['task_id']]
        rate = accepted[task['task_id']] / n if n else None
        if rate is None:
            dropped['unmeasured'] += 1
        elif rate <= spec['min_exclusive']:
            dropped['at_or_below_min'] += 1
        elif rate > spec['max_inclusive']:
            dropped['above_max'] += 1
        else:
            kept.append(task)
    if not kept:
        raise ValueError('The pass-rate filter keeps no training task')
    return kept, dict(spec, steps_sha256=file_sha256(source / 'steps.jsonl'), kept=len(kept), dropped=dict(dropped))


def filter_schedule(schedule, kept_ids):
    """Drop filtered tasks from a frozen order; equal multiplicities across arms stay equal."""
    filtered = [task_id for task_id in schedule if task_id in kept_ids]
    if not filtered:
        raise ValueError('The pass-rate filter removes every scheduled group')
    return filtered


def execution_mode(dev_evaluation, assessment_only, assessment_split='train'):
    if assessment_split not in ('train', 'validation', 'test'):
        raise ValueError('Unknown assessment split')
    if assessment_split != 'train' and not assessment_only:
        raise ValueError('A non-training split requires assessment-only mode')
    if dev_evaluation and assessment_only:
        raise ValueError('Training-state assessment cannot be combined with DEV evaluation')
    if dev_evaluation:
        return 'DEV_evaluation'
    if assessment_only:
        return 'training_state_assessment' if assessment_split == 'train' else assessment_split + '_assessment'
    return 'training'


class BudgetReached(Exception):
    pass


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


def anonymous_header(header):
    """Return (universe command, nonrec example header) without a recursive target binding.

    Put the universe command before any scoped declaration prefix (for example, ``open Nat in``).
    Keep scoped commands, but drop declaration attributes/modifiers. The signature stays unchanged.
    """
    _, code = lex_lines(header.splitlines())
    ident = r'(?:«[^»]*»|[^\s.:({\[]+)'
    declaration = re.search(r'\b(?:theorem|lemma)\s+' + ident + r'(?:\.' + ident + r')*'
                            r'(?:\.\{([^}]*)\})?', code)
    if declaration is None:
        raise ValueError('Expected a named theorem or lemma header')
    universes = declaration.group(1)
    command = 'universe ' + ' '.join(universes.replace(',', ' ').split()) + '\n' if universes else ''
    prefix = code[:declaration.start()]
    while re.search(r'\b(?:private|protected|noncomputable|nonrec)\s*$', prefix):
        prefix = re.sub(r'\b(?:private|protected|noncomputable|nonrec)\s*$', '', prefix)
    # Reuse the verifier's supported declaration-prefix boundary; never discard other commands.
    remaining, _ = split_prefix(prefix)
    if remaining.strip():
        raise ValueError('Unsupported declaration prefix for anonymous root')
    scope = ''.join(line.strip() + '\n' for line in prefix.splitlines()
                    if re.fullmatch(r'(?:open|set_option|attribute)\b.*\sin', line.strip()))
    # Without nonrec, Lean inserts its auxiliary _example into the saved local context.
    return command, scope + 'nonrec example' + code[declaration.end():].rstrip()


# Lockstep rollouts step several episodes in worker threads; every shared counter update holds this lock.
COUNTS_LOCK = threading.Lock()


class LeanEpisode:
    def __init__(self, ctx, task, output, counts, reward_mode='goal_change_v1', max_repair_attempts=0,
                 version=1, horizon=None, max_tactics=4, backtrack_owner='harness', backtrack_after=2, premises=None,
                 repl_log='episodes.log'):
        self.ctx, self.task, self.output, self.counts = ctx, task, output, counts
        self.repl_log = repl_log  # Lockstep rollouts give each sample's REPL its own log.
        self.premises = premises  # Optional goals -> lemma lines; part of the observation in training and evaluation.
        self.reward_mode = reward_mode
        if version not in (1, 2) or (version == 2 and (max_repair_attempts or reward_mode not in
                                                        ('goal_change_v1', 'error_feedback_v1'))):
            raise ValueError('Environment v2 has no repair and only the terminal or error-feedback reward')
        if backtrack_owner not in ('harness', 'policy') or max_tactics < 1 or backtrack_after < 1:
            raise ValueError('Invalid v2 chunk or backtrack setting')
        self.version, self.horizon = version, horizon
        self.max_tactics, self.backtrack_owner, self.backtrack_after = max_tactics, backtrack_owner, backtrack_after
        if max_repair_attempts not in (0, 1):
            raise ValueError('Error repair allows zero or one retry per trajectory')
        self.max_repair_attempts = max_repair_attempts
        self.repairs_used, self.last_error = 0, ''
        self.repl = None

    def send(self, request, timeout):
        with COUNTS_LOCK:
            self.counts['lean_calls'] += 1
        start = time.monotonic()
        try:
            response, wall, cpu = self.repl.send(request, timeout)
        except (TimeoutError, EOFError):
            with COUNTS_LOCK:
                self.counts['lean_seconds'] += time.monotonic() - start
            self.close()
            raise
        with COUNTS_LOCK:
            self.counts['lean_seconds'] += wall
            self.counts['lean_cpu_seconds'] += cpu or 0.0
        return response

    def _setup(self):
        bank = self.task['kind'] == 'bank'
        if bank:
            q, skip = question(self.ctx.rows[self.task['theorem_id']], self.ctx.environment,
                               self.ctx.sources, 'file', self.ctx.eval_spans)
            if q is None or q['header'] != self.task['header'] or q['name'] != self.task['name']:
                raise RuntimeError('Bank root does not rebuild: ' + str(skip or 'metadata differs'))
            head, tail = split_prefix(q['prefix'])
        self.repl = Repl(self.ctx, self.output / 'repl' / self.repl_log)
        response = self.send({'cmd': head if bank else HEADER}, self.ctx.setup_timeout)
        if repl_output(response)[0]:
            raise RuntimeError('Lean environment setup failed: ' + repl_output(response)[1])
        self.env = response['env']
        universes, local_header = anonymous_header((tail if bank else '') + self.task['header'])
        if bank:
            root = self.ctx.checker.source_for(dict(q, prefix=head, header=local_header), '  sorry\n')[len(head):]
        else:
            root = (local_header + ' := by\n' + self.ctx.checker.guard(self.task['name'])
                    + self.task['given'] + '  sorry\n')
        response = self.send({'cmd': universes + root, 'env': self.env}, self.ctx.timeout)
        sorries = response.get('sorries', [])
        if repl_output(response)[0] or len(sorries) != 1:
            raise RuntimeError('Training root does not replay: ' + repl_output(response)[1])
        self.root = sorries[0]
        if bank and self.task['given_steps']:
            given = proof_body([dict(tactic=step[2:]) for step in self.task['given_steps']])
            response = self.send({'tactic': tactic_block(given), 'proofState': self.root['proofState']},
                                 self.ctx.timeout)
            if repl_output(response)[0] or response.get('sorries') or not response.get('goals'):
                raise RuntimeError('Bank prefix does not leave verified goals: ' + repl_output(response)[1])
            self.root = dict(proofState=response['proofState'], goals=response['goals'],
                             goal='\n\n'.join(response['goals']))

    def reset(self):
        if self.repl is None:
            try:
                self._setup()
            except Exception:
                self.close()  # A half-built REPL has no root state; the next reset must rebuild it.
                raise
        self.state = self.root['proofState']
        self.goals = list(self.root.get('goals', [self.root['goal']]))
        self.seen = {state_key(self.goals)}
        self.path = []
        self.repairs_used, self.last_error = 0, ''
        # v2: frames[i] is the verified state after policy step i; shown[i] is that step's action text.
        self.frames = [(self.state, list(self.goals), state_key(self.goals))]
        self.shown, self.steps_used, self.failed = [], 0, {}
        return self.prompt_state()

    def prompt_state(self):
        if self.version == 2:
            return self.observation()
        state = '\n\n'.join(self.goals)
        if self.last_error:
            state += '\n\nThe last tactic failed. The verified goal is unchanged. Lean diagnostic:\n' + self.last_error
        return state

    def observation(self, omit=0):
        """v2 Lean-file rendering of the state; omit hides the oldest policy steps for the prompt cap."""
        return render_observation(self.task, self.shown, self.goals, self.last_error,
                                  self.failed.get(self.frames[-1][2], []), self.horizon - self.steps_used,
                                  self.max_tactics, self.backtrack_owner == 'policy', omit,
                                  self.premises(self.goals) if self.premises else None)

    def step(self, tactic, bonus):
        if self.version == 2:
            self.last_error = ''
            self.steps_used += 1
            result = self._step_v2(tactic)
            return v2_feedback(result) if self.reward_mode == 'error_feedback_v1' else result
        feedback = self.reward_mode == 'error_feedback_v1'
        result = self._step(tactic, 0.0 if feedback else bonus)
        result = local_feedback(result) if feedback else result
        self.last_error = ''
        if result['status'] in ('rejected', 'format_error') and self.repairs_used < self.max_repair_attempts:
            self.repairs_used += 1
            self.last_error = (result.get('error') or 'Return a tactic or one complete fenced Lean block.')[:600]
            result = dict(result, done=False, repair_offered=True, repair_attempt=self.repairs_used)
        return result

    def candidate_menu(self, candidates, *, max_candidates, max_lean_calls):
        """Check bounded single-tactic branches without committing a policy action.

        Probes share the physical call counters and REPL snapshots. Keep one
        branch in memory. The caller must also enforce its wall/token budgets.
        Only fully valid transitions enter the menu; partial chunks do not.
        This is an environment API, not a masked PPO implementation.
        """
        if self.version != 2 or not self.goals or self.horizon is None or self.steps_used >= self.horizon:
            raise ValueError('Candidate menus require an active v2 episode')
        if not 0 < len(candidates) <= max_candidates:
            raise ValueError('Candidate count exceeds the registered bound')
        # One tactic, up to two replay setup attempts, and one proof call.
        if self.counts['lean_calls'] + 4 * (len(candidates) + 1) > max_lean_calls:
            raise BudgetReached('lean_call_cap')
        menu = dict(status='ready', owner=id(self), proof_state=self.state,
                    observation=self.prompt_state(), steps_used=self.steps_used,
                    max_lean_calls=max_lean_calls, actions=[], probes=[])
        fields = ('state', 'goals', 'path', 'shown', 'frames', 'failed', 'steps_used', 'last_error')
        for index, text in enumerate(candidates):
            action = parse_action(text, 1)
            if action['kind'] != 'tactics' or forbidden(text):
                menu['probes'].append(dict(id=index, status='invalid_candidate'))
                continue
            branch = copy.copy(self)
            for field in fields:
                setattr(branch, field, copy.deepcopy(getattr(self, field)))
            result = branch.step(text, 0.0)
            menu['probes'].append(dict(id=index, **result))
            if result.get('censored') or result['status'] == 'timeout':
                self.close()  # A closed or busy shared REPL cannot supply a mask.
                return dict(menu, status=result['status'], actions=[],
                            censored=bool(result.get('censored')))
            if result['status'] == 'changed' or (result['accepted'] and result['done']):
                menu['actions'].append(dict(id=index, tactic=text))
        if not menu['actions']:
            menu['status'] = 'empty'
        return menu

    def select_candidate(self, menu, choice):
        """Recheck a menu choice against the same live state, then commit it."""
        if (menu['owner'] != id(self) or menu['proof_state'] != self.state
                or menu['steps_used'] != self.steps_used or menu['observation'] != self.prompt_state()):
            raise ValueError('Candidate menu is stale')
        selected = next((a for a in menu['actions'] if type(choice) is int and a['id'] == choice), None)
        if menu['status'] != 'ready' or selected is None:
            raise ValueError('Invalid candidate choice')
        if self.counts['lean_calls'] + 4 > menu['max_lean_calls']:
            raise BudgetReached('lean_call_cap')
        return self.step(selected['tactic'], 0.0)

    BRANCH_FIELDS = ('state', 'goals', 'path', 'shown', 'frames', 'failed', 'steps_used', 'last_error')

    def _branch(self):
        """A copy that shares the REPL and call counters but owns its proof path."""
        branch = copy.copy(self)
        for field in self.BRANCH_FIELDS:
            setattr(branch, field, copy.deepcopy(getattr(self, field)))
        return branch

    def _adopt(self, branch):
        for field in self.BRANCH_FIELDS:
            setattr(self, field, getattr(branch, field))

    def search_step(self, texts):
        """Try each sampled action on a copy and keep the best; the result equals one step with it.

        Rank: accepted proof, changed goals, partial progress by applied tactics, then sample order.
        A timeout or crash ends the search, because the shared REPL may stop answering. The kept
        action is selected, not an on-policy sample: use this for evaluation and data collection.
        A probe that leaves the state unchanged records the observation the policy would see next.
        """
        if self.version != 2 or not texts:
            raise ValueError('Step search needs environment v2 and at least one action')

        def rank(result):
            if result['accepted']:
                return 3
            return {'changed': 2, 'partial': 1}.get(result['status'], -1 if result['done'] else 0)
        probes, best = [], None
        for index, text in enumerate(texts):
            branch = self._branch()
            result = branch.step(text, 0.0)
            probe = dict(index=index, status=result['status'], error=(result.get('error') or '')[:600],
                         tactics_applied=result.get('tactics_applied', 0))
            if not result['done'] and branch.state == self.state and branch.shown == self.shown:
                probe['observation_after'] = branch.prompt_state()
            probes.append(probe)
            if result.get('censored') or result['status'] == 'timeout':
                best = (None, index, branch, result)
                break
            key = (rank(result), result.get('tactics_applied', 0))
            if best is None or key > best[0]:
                best = (key, index, branch, result)
            if result['accepted']:
                break
        _, index, branch, result = best
        self._adopt(branch)
        return dict(result=result, chosen=index, probes=probes)

    def try_automation(self, tactics, commit=True):
        """Try fixed automation tactics on the current goals; return the first that completes the proof.

        Each try runs on a copy. An automation-completed proof is Lean-verified but is not a policy
        action, so the caller reports it apart from model proofs. commit=False only probes.
        """
        if self.version != 2 or not self.goals:
            return None
        for tactic in tactics:
            branch = self._branch()
            result = branch.step(tactic, 0.0)
            if result.get('censored') or result['status'] == 'timeout':
                return dict(tactic=tactic, result=result)
            if result['accepted']:
                if commit:
                    self._adopt(branch)
                return dict(tactic=tactic, result=result)
        return None

    def _verify(self, unit, body):
        record = {}
        try:
            record = verify_group(self.ctx, unit, [body])[0]
            return record
        except VerifierSetupError as error:
            record = error.counts
            raise
        finally:
            with COUNTS_LOCK:
                for key in ('lean_calls', 'lean_seconds', 'lean_cpu_seconds'):
                    self.counts[key] += record.get(key, 0)

    def _step(self, tactic, bonus):
        raw_tactic = tactic
        # SFT emits code fences. Parse one complete block without repairing code.
        fenced = re.fullmatch(r'```lean[ \t]*\r?\n(.*?)\r?\n```', tactic.strip(), re.S)
        if fenced:
            tactic = fenced.group(1)
        if not tactic.strip() or '```' in tactic:
            return dict(status='format_error', reward=0.0, done=True, accepted=False)
        if FORBIDDEN.search(raw_tactic):
            return dict(status='forbidden_construct', reward=0.0, done=True, accepted=False)
        tactic = tactic_block(tactic)
        try:
            response = self.send({'tactic': tactic, 'proofState': self.state}, self.ctx.timeout)
        except (TimeoutError, EOFError) as exc:
            return dict(status='timeout' if isinstance(exc, TimeoutError) else 'crash',
                        reward=0.0, done=True, accepted=False, censored=isinstance(exc, EOFError))
        result = transition(response, self.seen, bonus)
        if result['status'] not in ('changed', 'needs_replay'):
            return result
        self.path.append(tactic)
        self.state, self.goals = response['proofState'], response['goals']
        if result['status'] == 'needs_replay':
            # Every line gets the outer proof indentation. Preserve nested blocks.
            body = '\n'.join('  ' + line for block in self.path for line in block.splitlines()) + '\n'
            unit = self.task if self.task['kind'] == 'bank' else lean_unit(self.task)
            replay = self._verify(unit, body)
            result = finish(result, replay)
            return dict(result, censored=True) if replay['status'] == 'crash' else result
        self.seen.add(result.pop('state_key'))
        return result

    def _loop(self, status, message, note=''):
        """v2 self-loop: the verified state is unchanged and one step is used."""
        self.last_error = cap_message(f'{status}: ', message, note)
        return dict(status=status, reward=0.0, done=False, accepted=False, error=message + note)

    def _revert(self, k):
        del self.frames[k + 1:], self.shown[k:], self.path[k:]
        self.state, goals, _ = self.frames[-1]
        self.goals = list(goals)

    def _fail(self, tactic, revert=True):
        """Remember a failed tactic at the current state; the harness may then revert one step."""
        failed = self.failed.setdefault(self.frames[-1][2], [])
        if tactic not in failed:
            failed.append(tactic)
        if not revert or self.backtrack_owner != 'harness' or len(failed) < self.backtrack_after or not self.shown:
            return False
        undone = self.shown[-1]
        self._revert(len(self.shown) - 1)
        self._fail(undone, revert=False)  # At most one revert per action.
        return True

    def _step_v2(self, text):
        action = parse_action(text, self.max_tactics, self.backtrack_owner == 'policy')
        if action['kind'] == 'malformed':
            return self._loop('format_error', action.get('reason', INSTRUCTION_V2.format(n=self.max_tactics)))
        if forbidden(text):
            return self._loop('forbidden_construct', 'the action uses a forbidden construct.')
        if action['kind'] == 'backtrack':
            k = action['step']
            if k >= len(self.shown):
                return self._loop('invalid_backtrack', f'k must be below your current step count {len(self.shown)}.')
            self._revert(k)
            self.last_error = f'backtracked to the state after step {k}.'
            return dict(status='backtrack', reward=0.0, done=False, accepted=False)
        applied, failure = 0, None
        for tactic in action['tactics']:
            block = tactic_block(tactic)
            try:
                response = self.send({'tactic': block, 'proofState': self.state}, self.ctx.timeout)
            except TimeoutError:
                return dict(status='timeout', reward=0.0, done=True, accepted=False, tactics_applied=applied)
            except EOFError:
                return dict(status='crash', reward=0.0, done=True, accepted=False, censored=True)
            code, output = repl_output(response)
            key = state_key(response.get('goals', []))
            if code or response.get('sorries') or 'goals' not in response:
                failure = ('rejected', action_positions(output) or 'Lean returned no proof state.')
            elif response['goals'] and key == self.frames[-1][2]:
                failure = ('no_progress', 'the goals are unchanged.')
            elif response['goals'] and key in {frame[2] for frame in self.frames}:
                failure = ('cycle', 'the goals repeat an earlier state on your current proof path.')
            if failure:
                failure += (tactic,)
                break
            applied += 1
            self.path.append(block)
            self.shown.append(tactic)
            self.state, self.goals = response['proofState'], response['goals']
            self.frames.append((self.state, list(self.goals), key))
            if not self.goals:
                break  # First-error truncation also drops tactics after the proof closes.
        if failure:
            status, message, tactic = failure
            reverted = self._fail(tactic)
            note = ' The harness reverted your last step.' if reverted else ''
            if applied:
                self.last_error = cap_message(f'{applied} tactic(s) applied, then {status}: ', message, note)
                return dict(status='partial', failure=status, reward=0.0, done=False, accepted=False, error=message,
                            tactics_applied=applied, harness_backtrack=reverted)
            return dict(self._loop(status, message, note), tactics_applied=0, harness_backtrack=reverted)
        if self.goals:
            return dict(status='changed', reward=0.0, done=False, accepted=False, tactics_applied=applied)
        # Every line gets the outer proof indentation. Preserve nested blocks.
        body = '\n'.join('  ' + line for block in self.path for line in block.splitlines()) + '\n'
        unit = self.task if self.task['kind'] == 'bank' else lean_unit(self.task)
        replay = self._verify(unit, body)
        result = finish(dict(status='needs_replay', reward=0.0, done=True, accepted=False,
                             tactics_applied=applied, proof_body=body), replay)
        # A replay crash is an infrastructure failure, not a failed proof: censor the group.
        return dict(result, censored=True) if replay['status'] == 'crash' else result

    def close(self):
        if self.repl is not None:
            close_repl(self.repl)
            self.repl = None


def lockstep_group(envs, generate, decode, observe, pool, *, depth, candidates, automation, max_prompt_tokens,
                   bonus, seeds, emit, counts, solved, name):
    """Roll out one group with all samples advancing together; each episode owns its Lean REPL.

    Each step makes one generate call for every active sample, then steps their episodes in the worker
    pool. Per-sample rules match the sequential loop: prompt cap, state bound, step search, automation
    fallback, censoring and the PAD-before-EOS refusal. Records go to emit in sample order, from this
    thread only. Returns (trajectories, censored, reason, root_automation, automation_done).
    """
    from concurrent.futures import wait
    size = len(envs)
    trajectories, automation_done, root_automation = [[] for _ in envs], [False] * size, None
    resets = [pool.submit(env.reset) for env in envs]
    wait(resets)
    states = []
    for future in resets:
        error = future.exception()
        if isinstance(error, (TimeoutError, EOFError, OSError, RuntimeError)):
            return trajectories, True, 'setup_failure: ' + str(error)[:200], None, automation_done
        states.append(future.result())
    if automation:
        root_automation = envs[0].try_automation(automation, commit=False)
        if root_automation and root_automation['result'].get('censored'):
            return trajectories, True, 'automation_root_' + root_automation['result']['status'], None, automation_done
        if root_automation and not root_automation['result']['accepted']:
            # A timed-out probe is a failed automation outcome. Its REPL closed, so rebuild the root.
            try:
                states[0] = envs[0].reset()
            except (TimeoutError, EOFError, OSError, RuntimeError) as error:
                return trajectories, True, 'setup_failure: ' + str(error)[:200], root_automation, automation_done
        elif root_automation:
            with COUNTS_LOCK:
                counts['automation_root_solved'] += 1
    active, pending = list(range(size)), [None] * size
    for step in range(depth):
        if not active:
            break
        prompts = []
        for i in active:
            ids = pending[i] or observe(envs[i])
            pending[i] = None
            if len(ids) > max_prompt_tokens:
                return trajectories, True, 'prompt_cap', root_automation, automation_done
            prompts.append(ids)
        batches = generate(prompts, candidates, [seeds(i, step) for i in active])
        texts = {}
        for i, ids, generations in zip(active, prompts, batches):
            for generation in generations:
                with COUNTS_LOCK:
                    counts['generated_tokens'] += generation['generated_tokens']
                    counts['retained_tokens'] += generation['retained_tokens']
                if generation['pad_before_eos']:
                    emit(dict(sample=i, step=step, prompt_ids=ids, status='generation_contract_violation', reward=None,
                              accepted=False, done=True, censored=True, **generation))
                    raise RuntimeError('Sampled PAD before EOS; refuse an unmatched training update')
            texts[i] = [decode(g['action_ids']) for g in generations]
        act = (lambda i: envs[i].step(texts[i][0], bonus)) if candidates == 1 else (lambda i: envs[i].search_step(texts[i]))
        futures = {i: pool.submit(act, i) for i in active}
        wait(futures.values())
        finished, censored = [], None
        for i, ids, generations in zip(active, prompts, batches):
            outcome = futures[i].result()  # An unexpected environment error propagates, as in the sequential loop.
            if candidates == 1:
                search, generation, tactic, result = None, generations[0], texts[i][0], outcome
            else:
                search, chosen = outcome, outcome['chosen']
                generation, tactic, result = generations[chosen], texts[i][chosen], outcome['result']
            if not result['done']:
                pending[i] = observe(envs[i])
                result = bound_state(result, len(pending[i]), max_prompt_tokens)
                if result['status'] == 'prompt_cap':
                    with COUNTS_LOCK:
                        counts['prompt_cap_ends'] += 1  # Truncation scored 0, counted separately.
            item = dict(prompt_ids=ids, **generation, tactic=tactic, state=states[i], **result)
            if search:
                item.update(chosen=search['chosen'], candidates=[
                    dict(probe, tactic=texts[i][probe['index']],
                         generated_tokens=generations[probe['index']]['generated_tokens'])
                    for probe in search['probes']])
            trajectories[i].append(item)
            if result['accepted']:
                with COUNTS_LOCK:
                    counts['accepted'] += 1
                if name not in solved:
                    solved.append(name)
            emit(dict(sample=i, step=step, **item))
            if result.get('censored'):
                censored = censored or result['status']
            if result['done']:
                finished.append(i)
            else:
                states[i] = envs[i].prompt_state()
        if censored:
            return trajectories, True, censored, root_automation, automation_done
        active = [i for i in active if i not in finished]
    if automation:
        eligible = [i for i in range(size) if trajectories[i] and not trajectories[i][-1]['done']]
        futures = {i: pool.submit(envs[i].try_automation, automation) for i in eligible}
        wait(futures.values())
        for i in eligible:
            automated = futures[i].result()
            if not automated:
                continue
            emit(dict(sample=i, step=len(trajectories[i]), automation=True, tactic=automated['tactic'], **automated['result']))
            if automated['result'].get('censored'):
                return trajectories, True, 'automation_' + automated['result']['status'], root_automation, automation_done
            if automated['result']['accepted']:
                automation_done[i] = True
                with COUNTS_LOCK:
                    counts['automation_accepted'] += 1
    return trajectories, False, None, root_automation, automation_done


def action_logprob(model, prompt_ids, action_ids, torch):
    """Log probability of the sampled tokens, including EOS, without retokenizing.

    Only the last len(action_ids)+1 positions reach the LM head (logits_to_keep), so the logits
    tensor is (A+1, vocab) instead of (P+A, vocab). The values equal the full-logits path.
    """
    ids = torch.tensor(prompt_ids + action_ids, device=next(model.parameters()).device)[None]
    mask = torch.ones_like(ids)
    targets = torch.tensor(action_ids, device=ids.device)
    try:
        # Kept positions are P-1 .. P+A-1; the last one predicts nothing in the action.
        logits = model(input_ids=ids, attention_mask=mask, use_cache=False,
                       logits_to_keep=len(action_ids) + 1).logits[0, :-1]
    except TypeError:  # An older transformers forward has no logits_to_keep; only the call may fall back.
        logits = model(input_ids=ids, attention_mask=mask, use_cache=False).logits[0, len(prompt_ids) - 1:-1]
    predicted = logits.float().log_softmax(-1)
    return predicted.gather(1, targets[:, None]).sum()


def update(model, optimizer, trajectories, torch, autocast, gamma=1.0, checkpointing=False):
    """Stream one action's backward pass; hold no graph across a Lean call.

    With checkpointing, Hugging Face recomputes activations only in train mode; main() enables it
    only for models without dropout, so train mode computes the same function as eval mode."""
    updater = PolicyGradientUpdater(model, optimizer, torch, autocast,
        advantages=lambda rewards: advantages(rewards, gamma), action_logprob=action_logprob)
    return updater.update(trajectories, checkpointing)


REQUIRED_KEYS = ('run_id', 'seed', 'lean', 'mathlib', 'group_size', 'max_groups', 'max_episode_steps',
                 'max_prompt_tokens', 'max_action_tokens', 'exploration_bonus', 'learning_rate', 'cpu_threads',
                 'lean_timeout_seconds', 'lean_setup_timeout_seconds', 'max_wall_seconds', 'max_generated_tokens',
                 'max_lean_calls', 'curriculum_max_lines_remaining')
V2_KEYS = ('discount', 'max_chunk_tactics', 'backtrack_owner', 'harness_backtrack_after')


def validate_config(config):
    """Reject a bad config before any output directory or model load."""
    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise ValueError('Config misses keys: ' + ', '.join(missing))
    version = config.get('environment_version', 1)
    if version == 1 and any(key in config for key in V2_KEYS):
        raise ValueError('Environment v1 config contains v2-only keys')
    if version == 2:
        if 'discount' not in config:
            raise ValueError('Environment v2 requires an explicit discount')
        for key in ('max_chunk_tactics', 'harness_backtrack_after'):
            value = config.get(key, 1)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(key + ' must be an integer of at least 1')
        if config.get('backtrack_owner', 'harness') not in ('harness', 'policy'):
            raise ValueError('backtrack_owner must be harness or policy')
    every = config.get('checkpoint_every', 0)
    if not isinstance(every, int) or every < 0:
        raise ValueError('checkpoint_every must be a non-negative integer')
    if not isinstance(config.get('gradient_checkpointing', False), bool):
        raise ValueError('gradient_checkpointing must be true or false')
    keep = config.get('checkpoint_keep', [])
    if not isinstance(keep, list) or any(isinstance(g, bool) or not isinstance(g, int) or g < 1 for g in keep):
        raise ValueError('checkpoint_keep must be a list of positive group counts')
    if config.get('task_sampler', 'fixed') not in ('fixed', 'band'):
        raise ValueError('task_sampler must be fixed or band')
    cap = config.get('band_cap_ratio', 1.5)
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap < 1:
        raise ValueError('band_cap_ratio must be a number of at least 1')
    candidates = config.get('step_candidates', 1)
    if isinstance(candidates, bool) or not isinstance(candidates, int) or candidates < 1:
        raise ValueError('step_candidates must be an integer of at least 1')
    automation = config.get('automation_fallback', [])
    if not isinstance(automation, list) or any(
            not isinstance(t, str) or forbidden(t) or parse_action(t, 1)['kind'] != 'tactics' for t in automation):
        raise ValueError('automation_fallback must be a list of single allowed tactics')
    if (candidates > 1 or automation) and version != 2:
        raise ValueError('Step search and automation fallback require environment v2')
    spec = config.get('task_filter')
    if spec is not None:
        if not isinstance(spec, dict) or set(spec) != {'pass_rates_from', 'min_exclusive', 'max_inclusive'}:
            raise ValueError('task_filter needs exactly pass_rates_from, min_exclusive and max_inclusive')
        low, high = spec['min_exclusive'], spec['max_inclusive']
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (low, high))
                or not 0 <= low < high <= 1):
            raise ValueError('task_filter needs 0 <= min_exclusive < max_inclusive <= 1')
    rollout = config.get('rollout', 'sequential')
    if rollout not in ('sequential', 'lockstep') or (rollout == 'lockstep' and version != 2):
        raise ValueError('rollout must be sequential, or lockstep with environment v2')
    workers = config.get('lean_workers', config['group_size'])
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError('lean_workers must be an integer of at least 1')
    backend = config.get('generation_backend', 'hf')
    if backend not in ('hf', 'vllm') or (backend == 'vllm' and rollout != 'lockstep'):
        raise ValueError('generation_backend must be hf, or vllm with lockstep rollout')
    fraction = config.get('vllm_memory_fraction', 0.4)
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0.05 <= fraction <= 0.9:
        raise ValueError('vllm_memory_fraction must lie in [0.05, 0.9]')
    retrieval = config.get('premise_retrieval')
    if retrieval is not None:
        if (version != 2 or not isinstance(retrieval, dict) or set(retrieval) != {'corpus', 'k'}
                or isinstance(retrieval['k'], bool) or not isinstance(retrieval['k'], int) or not 1 <= retrieval['k'] <= 16):
            raise ValueError('premise_retrieval needs environment v2, a corpus path and 1 <= k <= 16')


def check_mode_settings(config, evaluating):
    """Keep search, automation and task filtering in the mode where their measurement stays valid."""
    if not evaluating and (config.get('step_candidates', 1) > 1 or config.get('automation_fallback')):
        raise ValueError('Step search and automation fallback are evaluation and data-collection settings; '
                         'a selected or automation-completed action is not an on-policy REINFORCE sample')
    if evaluating and config.get('task_filter'):
        raise ValueError('task_filter selects training tasks; evaluation populations stay fixed')
    if not evaluating and config.get('generation_backend', 'hf') == 'vllm':
        raise ValueError('vLLM sampling is evaluation-only until a weight-sync path is validated on a GPU; '
                         'training samples must come from the live policy')


def lean_reservation(size, depth, per_action, setup_calls, replay_calls):
    """Worst-case Lean calls of one group: every action uses its full chunk, every trajectory replays."""
    return size * (depth * per_action + setup_calls + replay_calls)


def trajectory_seed(seed, task_id, visit, sample):
    """Independent, reproducible sampling stream per (task visit, sample), also across shards."""
    return int(hashlib.sha256(f'{seed}|{task_id}|{visit}|{sample}'.encode()).hexdigest()[:8], 16)


def file_sha256(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'model', 'checker', 'environment',
                 'mathlib-identity', 'repl', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--pool', type=Path)
    parser.add_argument('--exclude-names', type=Path)
    parser.add_argument('--tasks', type=Path, help='Training tasks; required unless evaluating DEV')
    parser.add_argument('--bank', type=Path, help='Native file-context bank; requires --train, --eval and --exclude-files')
    parser.add_argument('--train', type=Path, action='append', default=[], help='Official train rows for bank context; repeat')
    parser.add_argument('--eval', type=Path, action='append', default=[], help='Official evaluation exports for file exclusion; repeat')
    parser.add_argument('--exclude-files', type=Path, help='Additional DEV/excluded mathlib files, one per line')
    parser.add_argument('--units', type=Path, help='Optional bank task IDs, one per line')
    parser.add_argument('--evaluation-names', type=Path, help='Frozen DEV subset of --exclude-names; disables all optimizer updates')
    parser.add_argument('--assessment-only', action='store_true', help='Assess frozen reset states without an optimizer or checkpoint export')
    parser.add_argument('--assessment-split', choices=('train', 'validation', 'test'),
                        help='Declared pool split for --assessment-only; defaults to train')
    parser.add_argument('--task-order', type=Path, help='Frozen JSON list of admitted training task IDs, including explicit repeats')
    args = parser.parse_args()
    if args.assessment_split is not None and (not args.assessment_only or args.bank):
        parser.error('assessment-split requires --assessment-only with pool tasks')
    if args.bank:
        if not (args.train and args.eval and args.exclude_files) or any(
                (args.pool, args.tasks, args.exclude_names, args.evaluation_names)):
            parser.error('Give --bank with --train, --eval and --exclude-files, without pool inputs')
    elif (not (args.pool and args.exclude_names) or bool(args.tasks) == bool(args.evaluation_names)
          or args.train or args.eval or args.exclude_files or args.units):
        parser.error('Give --pool, --exclude-names and exclusively --tasks or --evaluation-names')
    if args.evaluation_names and (args.assessment_only or args.task_order):
        parser.error('DEV evaluation cannot use training assessment or task schedules')
    config = json.loads(args.config.read_text())
    validate_config(config)
    reward_mode = config.get('reward_mode', 'goal_change_v1')
    max_repair_attempts = config.get('max_repair_attempts', 0)
    if max_repair_attempts not in (0, 1):
        raise ValueError('Error repair allows zero or one retry per trajectory')
    if reward_mode not in ('goal_change_v1', 'error_feedback_v1'):
        raise ValueError('Unknown tactic reward mode')
    if reward_mode == 'error_feedback_v1' and config['exploration_bonus'] != 0:
        raise ValueError('Local verifier feedback requires zero goal-text exploration bonus')
    if config['group_size'] < 2 or not 0 <= config['exploration_bonus'] < 1 or config['max_episode_steps'] < 1:
        raise ValueError('Invalid group, episode, or shaping bound')
    version, gamma = config.get('environment_version', 1), config.get('discount', 1.0)
    if version not in (1, 2) or not 0 < gamma <= 1:  # validate_config already rejects a v1 discount.
        raise ValueError('Unknown environment version or invalid discount')
    if version == 2 and (config['exploration_bonus'] or max_repair_attempts):
        raise ValueError('Environment v2 has no goal-text bonus and no repair attempt')
    chunk = dict(max_tactics=config.get('max_chunk_tactics', 4), backtrack_owner=config.get('backtrack_owner', 'harness'),
                 backtrack_after=config.get('harness_backtrack_after', 2)) if version == 2 else {}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    started = time.monotonic()
    counts = dict(generated_tokens=0, retained_tokens=0, lean_calls=0, lean_seconds=0.0, lean_cpu_seconds=0.0,
                  trajectories=0, accepted=0, groups=0, optimizer_updates=0, censored_groups=0, prompt_cap_ends=0)
    run = dict(status='starting', config=config, counts=counts, experiment_id=config.get('experiment_id', 'EXP-019'),
               run_id=config['run_id'], environment_version=version, discount=gamma, **chunk,
               action_format='comment_tactic_chunk_v2' if version == 2 else 'raw_or_single_closed_lean_fence_v1',
               reward_mode=reward_mode,
               max_repair_attempts=max_repair_attempts,
               reward_components={'terminal': 'Lean-verified',
                                  'error_penalties' if reward_mode == 'error_feedback_v1' else 'exploration': 'heuristic'})
    candidates, automation = config.get('step_candidates', 1), config.get('automation_fallback', [])
    if candidates > 1 or automation:
        run.update(step_candidates=candidates, automation_fallback=automation)
    if automation:
        counts.update(automation_accepted=0, automation_root_solved=0)
        run['reward_components']['automation'] = 'Lean-verified; automation-completed proofs are counted apart from policy proofs'

    def save():
        run['wall_seconds'] = time.monotonic() - started
        run['gpu_seconds'] = run['wall_seconds'] if run.get('device') == 'cuda' else 0.0
        path = args.output / 'run.json'
        path.with_suffix('.tmp').write_text(json.dumps(run, indent=2) + '\n')
        path.with_suffix('.tmp').replace(path)

    def alarm(*_):
        raise BudgetReached('wall_cap')

    def terminated(*_):
        raise BudgetReached('terminated')

    signal.signal(signal.SIGALRM, alarm)
    signal.signal(signal.SIGTERM, terminated)  # Service stop or Spot interruption still saves run.json.
    signal.alarm(config['max_wall_seconds'])
    ctx = executor = None
    try:
        pool = []
        if args.pool:
            with args.pool.open() as stream:
                pool = [json.loads(line) for line in stream]
        excluded = set(args.exclude_names.read_text().split()) if args.exclude_names else set()
        rows, eval_spans = [], {}
        dev_evaluation = args.evaluation_names is not None
        assessment_split = args.assessment_split or 'train'
        mode = execution_mode(dev_evaluation, args.assessment_only, assessment_split)
        evaluating = mode != 'training'
        check_mode_settings(config, evaluating)
        if args.bank:
            for path in args.eval:
                with zipfile.ZipFile(path) as archive:
                    for line in archive.open(archive.namelist()[0]):
                        row = json.loads(line)
                        eval_spans.setdefault(row['file_path'], []).append(
                            (row['start'][0], row['end'][0], row['declaration_header']))
            rows = [row for path in args.train for row in load(path)]
            tasks = bank_tasks([json.loads(line) for line in args.bank.open()], rows,
                               set(args.exclude_files.read_text().split()), eval_spans)
            if args.units:
                wanted = set(args.units.read_text().split())
                if not wanted <= {task['task_id'] for task in tasks}:
                    raise ValueError('Requested bank task ID is missing')
                tasks = [task for task in tasks if task['task_id'] in wanted]
        elif dev_evaluation:
            tasks = evaluation_tasks(pool, set(args.evaluation_names.read_text().split()), excluded)
        else:
            with args.tasks.open() as stream:
                task_rows = [json.loads(line) for line in stream]
            if args.assessment_only:
                tasks = assessment_tasks(task_rows, pool, excluded, config['curriculum_max_lines_remaining'], assessment_split)
            else:
                tasks = training_tasks(task_rows, pool, excluded, config['curriculum_max_lines_remaining'])
        if not tasks:
            raise ValueError('Empty training curriculum')
        if config.get('task_filter'):
            tasks, run['task_filter'] = pass_rate_filter(tasks, config['task_filter'])
        run['mode'] = mode
        run['assessment_split'] = assessment_split if args.assessment_only else None
        run['population'] = [t['name'] for t in tasks] if evaluating else None
        run['solved_names'] = []
        population_file = 'evaluation_task_ids.json' if evaluating else 'training_task_ids.json'
        (args.output / population_file).write_text(json.dumps([t['task_id'] for t in tasks]) + '\n')
        input_names = ['config', 'checker', 'mathlib_identity']
        if args.bank:
            input_names += ['bank', 'exclude_files'] + (['units'] if args.units else [])
        else:
            input_names += ['pool', 'exclude_names', 'evaluation_names' if dev_evaluation else 'tasks']
        planned_groups = len(tasks) if evaluating else config['max_groups']
        if args.task_order:
            input_names.append('task_order')
            schedule = json.loads(args.task_order.read_text())
            planned, planned_groups = schedule, config['max_groups']
            if config.get('task_filter'):
                if len(schedule) != config['max_groups']:
                    raise ValueError('Task schedule must contain exactly max_groups entries')
                planned = filter_schedule(schedule, {t['task_id'] for t in tasks})
                planned_groups = len(planned)
                run['filtered_task_order'] = planned
            order = scheduled_tasks(tasks, planned, planned_groups)
            run['planned_task_order'] = schedule
            (args.output / 'planned_task_order.json').write_text(json.dumps(schedule) + '\n')
        else:
            order = iter(tasks) if evaluating else task_order(tasks, config['seed'])
        sampler = None
        if config.get('task_sampler', 'fixed') == 'band':
            if evaluating or args.task_order:
                raise ValueError('The band sampler is a training sampler; it excludes evaluation and frozen schedules')
            if any('family' not in t for t in tasks):
                raise ValueError('The band sampler needs a family label on every training task')
            import band_sampler
            sampler = band_sampler.BandSampler(tasks, config['group_size'], config['seed'], config.get('band_cap_ratio', 1.5))
            run['task_sampler'] = 'band'
            input_names.append('task_sampler_module')
            args.task_sampler_module = Path(band_sampler.__file__)
        entry = lambda path: dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())  # noqa: E731
        run['inputs'] = {k: entry(getattr(args, k)) for k in input_names}
        for kind in ('train', 'eval'):
            for index, path in enumerate(getattr(args, kind)):
                run['inputs'][f'{kind}_{index}'] = entry(path)
        premise_index = None
        if config.get('premise_retrieval'):
            from rl_components.premises import load_index
            corpus = Path(config['premise_retrieval']['corpus'])
            premise_index = load_index(corpus, config['premise_retrieval']['k'], {t['name'] for t in tasks})
            run['inputs']['premise_corpus'] = entry(corpus)
        run['model_source'] = str(args.model)
        run['model_sha256'] = file_sha256(args.model / 'model.safetensors')
        run['script_sha256'] = file_sha256(__file__)
        run['component_sha256'] = component_fingerprints()
        run['repl_sha256'] = file_sha256(args.repl)
        mathlib = args.environment / 'mathlib'
        run['lean'] = (mathlib / 'lean-toolchain').read_text().strip()
        run['mathlib_source_identity'] = mathlib_identity(args.mathlib_identity, config['mathlib'])
        run['mathlib'] = run['mathlib_source_identity']['mathlib_revision']
        lean_version = subprocess.run([str(args.environment / 'lean-4.10.0-rc1-linux/bin/lean'), '--version'],
                                 check=True, capture_output=True, text=True)
        run['lean_binary_version'] = lean_version.stdout.strip()
        if run['lean'] != config['lean'] or '4.10.0-rc1' not in run['lean_binary_version']:
            raise RuntimeError('Lean/mathlib pair differs from config')
        checker = load_checker(args.checker, args.environment, args.output)
        ctx = repl_context(checker, args.environment.resolve(), args.repl, args.output,
                           config['lean_timeout_seconds'], config['lean_setup_timeout_seconds'], 32)
        if args.bank:
            theorems = {task['theorem_id'] for task in tasks}
            ctx.rows = {row['theorem_id']: row for row in rows if row['theorem_id'] in theorems}
            ctx.eval_spans = eval_spans
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig, set_seed
        set_seed(config['seed'])
        torch.set_num_threads(config['cpu_threads'])
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        run.update(device=device, versions={p: importlib.metadata.version(p) for p in ('torch', 'transformers')},
                   device_name=torch.cuda.get_device_name(0) if device == 'cuda' else 'cpu')
        model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.float32).to(device)
        model.eval()  # Dropout must match generation during differentiable replay.
        tokenizer = AutoTokenizer.from_pretrained(args.model)
        # A fresh config drops checkpoint sampling warpers (min_p, n-gram bans, ...) that would make the
        # sampled distribution differ from the policy that update() differentiates.
        old = model.generation_config
        model.generation_config = GenerationConfig(eos_token_id=old.eos_token_id, pad_token_id=old.pad_token_id,
                                                   bos_token_id=old.bos_token_id, do_sample=True, temperature=1.0,
                                                   top_p=1.0, top_k=0, repetition_penalty=1.0)
        end_turn = tokenizer.convert_tokens_to_ids('<|im_end|>')
        stops = old.eos_token_id if isinstance(old.eos_token_id, list) else [old.eos_token_id]
        if end_turn is not None and end_turn != tokenizer.unk_token_id and end_turn not in stops:
            raise RuntimeError('The end-of-turn token does not stop generation')
        run['generation_stop_ids'] = stops
        optimizer = None if evaluating else torch.optim.AdamW(model.parameters(), lr=config['learning_rate'], weight_decay=0.0)
        checkpointing = bool(config.get('gradient_checkpointing', False)) and not evaluating
        if checkpointing:
            dropout = {k: v for k, v in model.config.to_dict().items() if 'dropout' in k and v}
            if dropout:
                raise RuntimeError('Gradient checkpointing needs train mode; refuse a model with dropout ' + str(dropout))
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
        run['gradient_checkpointing'] = checkpointing
        autocast = (lambda: torch.autocast('cuda', dtype=torch.bfloat16)) if device == 'cuda' else nullcontext

        def encode(content):
            text = tokenizer.apply_chat_template([{'role': 'user', 'content': content}],
                                                 tokenize=False, add_generation_prompt=True)
            return tokenizer(text, add_special_tokens=False)['input_ids']

        def observe(env):
            if version == 1:
                return encode(STEP.format(state=env.prompt_state()))
            for omit in range(len(env.shown) + 1):  # Hide the oldest policy steps only when needed.
                ids = encode(env.observation(omit))
                if len(ids) <= config['max_prompt_tokens']:
                    break
            return ids

        lockstep = config.get('rollout', 'sequential') == 'lockstep'
        if lockstep:
            from concurrent.futures import ThreadPoolExecutor
            # One thread per concurrently stepped episode; each episode and replay thread owns its Lean REPL.
            executor = ThreadPoolExecutor(max_workers=config.get('lean_workers', config['group_size']))
            if config.get('generation_backend', 'hf') == 'vllm':
                generator = VLLMGenerator(args.model, stops, tokenizer.pad_token_id, config['max_action_tokens'],
                                          config.get('vllm_memory_fraction', 0.4), config['seed'])
            else:
                generator = HFGenerator(model, tokenizer, torch, autocast, device, stops, config['max_action_tokens'], set_seed)
            decode = lambda ids: tokenizer.decode(ids, skip_special_tokens=True)  # noqa: E731
            run.update(rollout='lockstep', generation_backend=config.get('generation_backend', 'hf'),
                       lean_workers=config.get('lean_workers', config['group_size']),
                       sampling_seeds='per (task visit, sample, step) request seed' if config.get('generation_backend') == 'vllm'
                       else 'one stream per batched step, seeded from the active samples')
        run.update(status='running', phase='idle')
        save()
        with (args.output / 'rollouts.jsonl').open('x') as stream, (args.output / 'steps.jsonl').open('x') as steps:
            groups = planned_groups
            visits = {}
            for group in range(groups):
                size, depth = config['group_size'], config['max_episode_steps']
                # Reserve the whole group so later trajectories keep identical caps.
                if counts['generated_tokens'] + size * depth * candidates * config['max_action_tokens'] > config['max_generated_tokens']:
                    raise BudgetReached('token_cap')
                setup_calls = 6 if args.bank else 5  # Bank prefixes use one additional tactic-mode call.
                per_action, replay_calls = (chunk['max_tactics'], 2) if version == 2 else (1, 0)
                # Automation tries each tactic once per trajectory and once at the root, each with a replay.
                automation_calls = (size + 1) * len(automation) * (1 + replay_calls)
                if counts['lean_calls'] + lean_reservation(size, depth * candidates, per_action, setup_calls,
                                                           replay_calls) + automation_calls > config['max_lean_calls']:
                    raise BudgetReached('lean_call_cap')
                task = sampler.next_task() if sampler else next(order)
                visit = visits.get(task['task_id'], 0)
                visits[task['task_id']] = visit + 1
                env = LeanEpisode(ctx, task, args.output, counts, reward_mode=reward_mode,
                                  max_repair_attempts=max_repair_attempts, version=version, horizon=depth,
                                  premises=premise_index, **chunk)
                trajectories, censored = [], False
                root_automation, automation_done = None, []
                run['phase'] = 'sampling'
                try:
                    for sample in (() if lockstep else range(size)):
                        set_seed(trajectory_seed(config['seed'], task['task_id'], visit, sample))
                        try:
                            state, trajectory = env.reset(), []
                        except (TimeoutError, EOFError, OSError, RuntimeError) as exc:
                            censored = True  # Setup failure is infrastructure or data, never a policy outcome.
                            run['last_censor_reason'] = 'setup_failure: ' + str(exc)[:200]
                            break
                        if automation and sample == 0:
                            # Automation alone at the root is the baseline for automation-completed proofs.
                            root_automation = env.try_automation(automation, commit=False)
                            if root_automation and root_automation['result'].get('censored'):
                                censored = True  # A crashed REPL is infrastructure.
                                run['last_censor_reason'] = 'automation_root_' + root_automation['result']['status']
                                break
                            if root_automation and not root_automation['result']['accepted']:
                                # A timed-out probe is a failed automation outcome. Its REPL closed, so rebuild the root.
                                try:
                                    state = env.reset()
                                except (TimeoutError, EOFError, OSError, RuntimeError) as exc:
                                    censored = True
                                    run['last_censor_reason'] = 'setup_failure: ' + str(exc)[:200]
                                    break
                            elif root_automation:
                                counts['automation_root_solved'] += 1
                        pending = None
                        for step in range(depth):
                            prompt_ids = pending or observe(env)
                            pending = None
                            if len(prompt_ids) > config['max_prompt_tokens']:
                                censored = True
                                run['last_censor_reason'] = 'prompt_cap'
                                break
                            inputs = torch.tensor(prompt_ids, device=device)[None]
                            with torch.no_grad(), autocast():
                                rows = model.generate(input_ids=inputs, attention_mask=torch.ones_like(inputs),
                                    do_sample=True, temperature=1.0, top_p=1.0, top_k=0, repetition_penalty=1.0,
                                    max_new_tokens=config['max_action_tokens'], pad_token_id=tokenizer.eos_token_id,
                                    num_return_sequences=candidates)
                            generations = [generation_record(stop_trimmed(row[len(prompt_ids):].tolist(), stops),
                                                             stops, tokenizer.pad_token_id) for row in rows]
                            for generation in generations:
                                for key in ('generated_tokens', 'retained_tokens'):
                                    counts[key] += generation[key]
                                if generation['pad_before_eos']:
                                    stream.write(json.dumps(dict(group=group, sample=sample, step=step,
                                        task_id=task['task_id'], kind=task['kind'], prompt_ids=prompt_ids,
                                        status='generation_contract_violation', reward=None, accepted=False,
                                        done=True, censored=True, **generation)) + '\n')
                                    stream.flush()
                                    save()
                                    raise RuntimeError('Sampled PAD before EOS; refuse an unmatched training update')
                            texts = [tokenizer.decode(g['action_ids'], skip_special_tokens=True) for g in generations]
                            if candidates == 1:
                                search, generation, tactic = None, generations[0], texts[0]
                                result = env.step(tactic, config['exploration_bonus'] / depth)
                            else:
                                search = env.search_step(texts)
                                generation, tactic = generations[search['chosen']], texts[search['chosen']]
                                result = search['result']
                            if not result['done']:
                                pending = observe(env)
                                result = bound_state(result, len(pending), config['max_prompt_tokens'])
                                if result['status'] == 'prompt_cap':
                                    counts['prompt_cap_ends'] += 1  # Truncation scored 0, counted separately.
                            item = dict(prompt_ids=prompt_ids, **generation, tactic=tactic, state=state, **result)
                            if search:
                                item.update(chosen=search['chosen'], candidates=[
                                    dict(probe, tactic=texts[probe['index']],
                                         generated_tokens=generations[probe['index']]['generated_tokens'])
                                    for probe in search['probes']])
                            trajectory.append(item)
                            if result['accepted']:
                                counts['accepted'] += 1
                                if task['name'] not in run['solved_names']:
                                    run['solved_names'].append(task['name'])
                            stream.write(json.dumps(dict(group=group, sample=sample, step=step, task_id=task['task_id'],
                                                         kind=task['kind'], **item), ensure_ascii=False) + '\n')
                            stream.flush()
                            save()
                            if result.get('censored'):
                                censored = True
                                run['last_censor_reason'] = result['status']
                            if result['done']:
                                break
                            state = env.prompt_state()
                        automated = None
                        if automation and not censored and trajectory and not trajectory[-1]['done']:
                            automated = env.try_automation(automation)
                            if automated:
                                stream.write(json.dumps(dict(group=group, sample=sample, step=len(trajectory),
                                    task_id=task['task_id'], kind=task['kind'], automation=True,
                                    tactic=automated['tactic'], **automated['result']), ensure_ascii=False) + '\n')
                                stream.flush()
                                if automated['result'].get('censored'):
                                    censored = True
                                    run['last_censor_reason'] = 'automation_' + automated['result']['status']
                                elif automated['result']['accepted']:
                                    counts['automation_accepted'] += 1
                        trajectories.append(trajectory)
                        automation_done.append(bool(automated and automated['result']['accepted']))
                        if trajectory:
                            counts['trajectories'] += 1
                        if censored:
                            break
                finally:
                    env.close()
                if lockstep:
                    envs = [LeanEpisode(ctx, task, args.output, counts, reward_mode=reward_mode,
                                        max_repair_attempts=max_repair_attempts, version=version, horizon=depth,
                                        premises=premise_index, repl_log=f'episodes_s{i}.log', **chunk) for i in range(size)]

                    def emit(record):
                        stream.write(json.dumps(dict(group=group, task_id=task['task_id'], kind=task['kind'], **record),
                                                ensure_ascii=False) + '\n')
                        stream.flush()
                        save()
                    try:
                        trajectories, censored, reason, root_automation, automation_done = lockstep_group(
                            envs, generator, decode, observe, executor, depth=depth, candidates=candidates,
                            automation=automation, max_prompt_tokens=config['max_prompt_tokens'],
                            bonus=config['exploration_bonus'] / depth,
                            seeds=lambda i, step: step_seed(config['seed'], task['task_id'], visit, i, step),
                            emit=emit, counts=counts, solved=run['solved_names'], name=task['name'])
                    finally:
                        for episode in envs:
                            episode.close()
                    if censored:
                        run['last_censor_reason'] = reason
                    counts['trajectories'] += sum(1 for path in trajectories if path)
                if censored:
                    counts['censored_groups'] += 1
                    steps.write(json.dumps(dict(group=group, status='censored', reason=run['last_censor_reason'])) + '\n')
                    steps.flush()
                    continue  # Never optimize a partially sampled group.
                if evaluating:
                    result = dict(loss=None, gradient_norm=None, optimizer_updated=False)
                else:
                    run['phase'] = 'update'
                    result = update(model, optimizer, trajectories, torch, autocast, gamma, checkpointing)
                counts['groups'] += 1
                counts['optimizer_updates'] += result['optimizer_updated']
                if sampler:  # Lean-verified successes only; censored groups never reach this line.
                    sampler.observe(task, sum(any(s['accepted'] for s in path) for path in trajectories))
                    result = dict(result, sampler=sampler.state())
                run['phase'] = 'idle'
                # Policy returns exclude automation; automation outcomes stay in their own fields.
                extra = dict(automation_accepted=automation_done,
                             automation_root=root_automation['tactic'] if root_automation and root_automation['result']['accepted'] else None,
                             automation_root_status=root_automation['result']['status'] if root_automation else None) if automation else {}
                steps.write(json.dumps(dict(group=group, task_id=task['task_id'], kind=task['kind'],
                    returns=[sum(s['reward'] for s in path) for path in trajectories],
                    discounted_returns=[sum(s['reward'] * gamma ** k for k, s in enumerate(path)) for path in trajectories],
                    n_steps=[len(path) for path in trajectories],
                    # Penalties can make a proved episode's return zero or negative; count proofs from this field.
                    accepted=[any(s['accepted'] for s in path) for path in trajectories], **result, **extra)) + '\n')
                steps.flush()
                every = config.get('checkpoint_every', 0)
                if every and not evaluating and counts['groups'] % every == 0:
                    run['phase'] = 'checkpoint'
                    staging = args.output / 'checkpoint.tmp'
                    model.save_pretrained(staging)
                    tokenizer.save_pretrained(staging)
                    torch.save(optimizer.state_dict(), staging / 'optimizer.pt')
                    (staging / 'checkpoint.json').write_text(json.dumps(dict(groups=counts['groups'], group=group,
                        optimizer_updates=counts['optimizer_updates'])) + '\n')
                    if (args.output / 'checkpoint').exists():
                        shutil.rmtree(args.output / 'checkpoint')
                    staging.rename(args.output / 'checkpoint')
                    run.update(phase='idle', last_checkpoint_groups=counts['groups'])
                if not evaluating and counts['groups'] in config.get('checkpoint_keep', []):
                    # Kept snapshots (for example the midpoint) are never deleted; weights and tokenizer only.
                    run['phase'] = 'checkpoint'
                    kept = args.output / f"checkpoint_g{counts['groups']}"
                    staging = kept.with_name(kept.name + '.tmp')
                    model.save_pretrained(staging)
                    tokenizer.save_pretrained(staging)
                    (staging / 'checkpoint.json').write_text(json.dumps(dict(groups=counts['groups'], group=group,
                        optimizer_updates=counts['optimizer_updates'])) + '\n')
                    staging.rename(kept)
                    run.setdefault('kept_checkpoints', []).append(kept.name)
                    run['phase'] = 'idle'
                save()
        run['status'] = 'completed'
    except BudgetReached as exc:
        run.update(status='capped', stop_reason=str(exc),
                   partial_group_optimized='unknown' if run.get('phase') == 'update' else False)
    except BaseException as exc:
        run.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        signal.alarm(0)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if ctx is not None:
            for repl in ctx.pool_repls:
                close_repl(repl)
        save()
    if can_export(run, counts['optimizer_updates']):
        model.save_pretrained(args.output / 'final')
        tokenizer.save_pretrained(args.output / 'final')
        torch.save(optimizer.state_dict(), args.output / 'optimizer.pt')
        run['checkpoint'] = 'final'
        save()
    print(json.dumps(run))


if __name__ == '__main__':
    main()
