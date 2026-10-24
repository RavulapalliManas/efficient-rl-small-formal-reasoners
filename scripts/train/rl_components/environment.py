"""Lean proof episodes, replay acceptance and local feedback.

The host owns the checker context, runtime pins and output directory. Episodes
share that context but own their proof paths; branches share call counters.
No model or optimizer is loaded by this module.
"""
import copy
from pathlib import Path
import re
import sys
import threading
import time

# The verifier transport still lives in sibling command modules.
sys.path[:0] = [str(Path(__file__).resolve().parents[2] / d) for d in ('data', 'eval', 'audit')]
from bench_lean_verifier import Repl, VerifierSetupError, first_error, repl_output, split_prefix, verify_group  # noqa: E402
from classify_proof_failures import classify as classify_failure  # noqa: E402
from probe_nt_pool import lean_unit  # noqa: E402
from probe_start_depth import FORBIDDEN, FORBIDDEN_CODE  # noqa: E402
from replay_training_proofs import proof_body, question  # noqa: E402
from verify_external_proofs import HEADER  # noqa: E402
from .actions import BACKTRACK, INSTRUCTION_V2, action_positions, cap_message, lex_lines, parse_action  # noqa: E402
from .observations import render_observation  # noqa: E402
from .proofs import tactic_block  # noqa: E402

# Worker threads share verifier counters across episodes and branches.
COUNTS_LOCK = threading.Lock()


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


def forbidden(text):
    """True when the code of an action uses a forbidden construct; comments are ignored."""
    return bool(FORBIDDEN.search(text) or FORBIDDEN_CODE.search(lex_lines(text.splitlines())[1]))


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


class BudgetReached(Exception):
    pass


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


class LeanEpisode:
    """Maintain the verified proof path and the state used by the next action.

    The replay path contains accepted tactic blocks. Displayed tactics and
    saved frames support observations and backtracking. Rejected tactics stay
    outside that path; rollout records retain every attempted action.
    """
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
        for index, text in enumerate(candidates):
            action = parse_action(text, 1)
            if action['kind'] != 'tactics' or forbidden(text):
                menu['probes'].append(dict(id=index, status='invalid_candidate'))
                continue
            branch = self._branch()
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

    def _replay_path(self):
        """Replay the accepted tactic path against the original proof target."""
        body = '\n'.join('  ' + line for block in self.path for line in block.splitlines()) + '\n'
        unit = self.task if self.task['kind'] == 'bank' else lean_unit(self.task)
        return body, self._verify(unit, body)

    def _transition_error(self, response):
        """Classify a rejected, unchanged, or cyclic Lean transition."""
        error_code, message = repl_output(response)
        next_state = state_key(response.get('goals', []))
        if error_code or response.get('sorries') or 'goals' not in response:
            return 'rejected', action_positions(message) or 'Lean returned no proof state.'
        if response['goals'] and next_state == self.frames[-1][2]:
            return 'no_progress', 'the goals are unchanged.'
        if response['goals'] and next_state in {frame[2] for frame in self.frames}:
            return 'cycle', 'the goals repeat an earlier state on your current proof path.'
        return None

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
            _, replay = self._replay_path()
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
            failure = self._transition_error(response)
            if failure:
                failed_tactic = tactic
                break
            applied += 1
            self.path.append(block)
            self.shown.append(tactic)
            self.state, self.goals = response['proofState'], response['goals']
            self.frames.append((self.state, list(self.goals), state_key(self.goals)))
            if not self.goals:
                break  # First-error truncation also drops tactics after the proof closes.
        if failure:
            status, message = failure
            reverted = self._fail(failed_tactic)
            note = ' The harness reverted your last step.' if reverted else ''
            if applied:
                self.last_error = cap_message(f'{applied} tactic(s) applied, then {status}: ', message, note)
                return dict(status='partial', failure=status, reward=0.0, done=False, accepted=False, error=message,
                            tactics_applied=applied, harness_backtrack=reverted)
            return dict(self._loop(status, message, note), tactics_applied=0, harness_backtrack=reverted)
        if self.goals:
            return dict(status='changed', reward=0.0, done=False, accepted=False, tactics_applied=applied)
        body, replay = self._replay_path()
        result = finish(dict(status='needs_replay', reward=0.0, done=True, accepted=False,
                             tactics_applied=applied, proof_body=body), replay)
        # A replay crash is an infrastructure failure, not a failed proof: censor the group.
        return dict(result, censored=True) if replay['status'] == 'crash' else result

    def close(self):
        if self.repl is not None:
            close_repl(self.repl)
            self.repl = None
