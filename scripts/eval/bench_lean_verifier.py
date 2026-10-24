"""Compare three Lean verifier modes for a GRPO reward on the same proof attempts. No model runs.

All modes use the pinned Lean v4.10.0-rc1 and mathlib 29dcec07 on training tasks only (the
replay-accepted tasks of build_proof_tasks.py). Each sampled task starts at depth 2 and gets eight
attempts: the reference suffix and seven negatives or variants.
1. subprocess: the current checker. One `lean -j1 -M2048` call per attempt on the full file, 20-second timeout.
2. repl_env: one leanprover-community/repl process per task. The file prefix is elaborated once; each
   attempt is one command (guard + proof + closings + `#print axioms`) in that environment.
3. repl_tactic: one REPL process per task. `header := by guard; sorry` gives a proof state, the given
   steps run as tactics, then each attempt runs one top-level step at a time from that state.
Modes 1 and 2 accept with the rule of `check` in probe_start_depth.py: no FORBIDDEN match in the
continuation, no error, TARGET_ABSENT, and axioms within ALLOWED_AXIOMS. Mode 3 cannot print axioms:
it accepts with no FORBIDDEN match, no error, no reported sorry, and no goal left. Every attempt goes
to Lean, even when FORBIDDEN matches, so `lean_ok` keeps Lean's own verdict.

The REPL is leanprover-community/repl tag v4.10.0-rc1, run under `stdbuf -oL` because that revision
does not flush stdout. Its first command holds the file prefix up to the last command boundary; a
trailing doc comment moves into each later command, so the joined text equals the subprocess file.

`verify_group` (repl-env mode) is the one Lean scoring path that probe_nt_pool.py, pass_rate_map.py, and
run_lean_grpo.py import, so a model is scored the same way in the pass-rate maps and in RL.

Memory: --workers threads, each with one Lean process at a time. Smoke run (4 tasks, 32 attempts per
mode): one lean subprocess peaked at 1.09 GiB RSS (its heap is capped by -M2048), one REPL process at
1.10 GiB (no internal cap). Budget 2 GiB per process, twice the smoke peak, + 0.3 GiB of Python and
training rows: 8.3 GiB for 4 workers. Run under a 12 GiB service cap. The 20-task run (bench_v1)
peaked at 1.91 GiB RSS per process in every mode.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import random
import re
import select
import subprocess
import sys
import threading
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_start_depth import FORBIDDEN, lean_code  # noqa: E402
from replay_training_proofs import ALLOWED_AXIOMS, axioms_of, load, proof_body, question  # noqa: E402
from verify_external_proofs import HEADER  # noqa: E402

DEPTH = 2
MODES = ("subprocess", "repl_env", "repl_tactic")
LOCK = threading.Lock()


def sample_tasks(paths, count, seed):
    """Accepted tasks with more than DEPTH steps, every cut checked, and a reference suffix FORBIDDEN passes."""
    rows = []
    for path in paths:
        with path.open() as input_stream:
            rows.extend(json.loads(line) for line in input_stream)
    pool = sorted((r for r in rows if r["status"] == "accepted" and len(r["steps"]) > DEPTH
                   and all(c["ok"] for c in r["cuts"]) and not FORBIDDEN.search("\n".join(r["steps"][-DEPTH:]))),
                  key=lambda r: r["theorem_id"])
    return random.Random(seed).sample(pool, count), len(pool)


def variants(steps):
    """The given steps and eight continuations, each a list of top-level steps."""
    given, suffix = steps[:-DEPTH], steps[-DEPTH:]
    pad = " " * (len(suffix[0]) - len(suffix[0].lstrip()))
    return given, pad, {"reference": suffix, "empty": [], "rfl": [pad + "rfl"], "simp": [pad + "simp"],
                        "omega": [pad + "omega"], "drop_last": suffix[:-1], "sorry_last": suffix[:-1] + [pad + "sorry"],
                        "native_decide": [pad + "have _h : (2 : Nat) + 2 = 4 := by native_decide"] + suffix}


def body_of(given, steps):
    """The proof body of `check`: given steps, a line break, then the continuation."""
    return "\n".join(given) + "\n" + "\n".join(steps).rstrip()


def split_prefix(prefix):
    """Split the file prefix at its last command boundary. A trailing doc comment, `open/set_option/attribute ... in` line,
    attribute block (`@[...]`, possibly with indented continuation lines), or lone modifier line belongs to the
    target declaration, so it moves to each REPL command; the joined text is unchanged."""
    head = prefix
    while True:
        stripped = head.rstrip()
        start = stripped.rfind("/--")
        if stripped.endswith("-/") and start >= 0 and "-/" not in stripped[start + 3:-2]:
            head = stripped[:start]
            continue
        last = stripped.rsplit("\n", 1)[-1]
        if last.lstrip().startswith("--"):
            head = stripped[:len(stripped) - len(last)]
            continue
        if re.match(r"(open|set_option|attribute)\b.*\sin$", last) or re.fullmatch(r"(protected|private|noncomputable|nonrec)\s*", last):
            head = stripped[:len(stripped) - len(last)]
            continue
        attribute = re.search(r"(?:^|\n)(@\[[^\n]*(?:\n[ \t]+[^\n]*)*)$", stripped)
        if attribute:
            # Only a standalone attribute belongs to the next declaration. A complete
            # attributed lemma must remain available in the environment prefix.
            code = re.sub(r'"(?:\\.|[^"\\])*"|«[^»]*»', 'x', lean_code(attribute.group(1)))
            depth = 0
            for index, char in enumerate(code[1:], start=1):
                depth += (char == "[") - (char == "]")
                if depth == 0:
                    break
            if depth == 0 and not code[index + 1:].strip():
                head = stripped[:attribute.start(1)]
                continue
        return head, prefix[len(head):]


def proc_memory(pid):
    """VmHWM, VmRSS, RssAnon, RssFile in KiB from /proc; empty once the process has exited."""
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    except OSError:
        return {}
    pairs = (line.split(":", 1) for line in lines)
    return {k: int(v.split()[0]) for k, v in pairs if k in ("VmHWM", "VmRSS", "RssAnon", "RssFile")}


def cpu_seconds(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except OSError:
        return None
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def first_error(output):
    return next(iter(re.findall(r"error: ([^\n]*)", output)), None)


def verdict(exit_code, output):
    """The Lean part of `check` in probe_start_depth.py."""
    axioms = axioms_of(output)
    ok = (exit_code == 0 and "TARGET_ABSENT" in output and "error:" not in output
          and axioms is not None and set(axioms) <= ALLOWED_AXIOMS)
    return ok, axioms


def lean_file(ctx, path, source):
    """One call as in direct_lean_call, plus CPU time and peak RSS from wait4 and an RSS split sampled every 20 ms."""
    path.write_text(source)
    peak, done, expired = {}, threading.Event(), threading.Event()
    with path.with_suffix(".out").open("w+b") as out:
        start = time.monotonic()
        proc = subprocess.Popen([str(ctx.lean), "-j1", "-M2048", str(path)], env=ctx.lean_env,
                                stdout=out, stderr=subprocess.STDOUT)

        def watch():
            while not done.wait(0.02):
                for key, value in proc_memory(proc.pid).items():
                    peak[key] = max(peak.get(key, 0), value)
        threading.Thread(target=watch, daemon=True).start()
        timer = threading.Timer(ctx.timeout, lambda: (expired.set(), proc.kill()))
        timer.start()
        _, status, usage = os.wait4(proc.pid, 0)
        wall = time.monotonic() - start
        timer.cancel()
        done.set()
        proc.returncode = os.waitstatus_to_exitcode(status)
        out.seek(0)
        output = out.read().decode(errors="replace")
    state = "timeout" if expired.is_set() else "memory_limit" if "excessive memory consumption" in output else "returned"
    return output, {"status": state, "exit_code": proc.returncode, "wall_seconds": wall,
                    "cpu_seconds": usage.ru_utime + usage.ru_stime, "peak_rss_kib": usage.ru_maxrss,
                    "rss_anon_kib": peak.get("RssAnon"), "rss_file_kib": peak.get("RssFile")}


class Repl:
    """One REPL process. `send` returns (response, wall s, CPU s) or raises TimeoutError or EOFError."""

    def __init__(self, ctx, log):
        self.log = log.open("ab")
        # This REPL revision does not flush stdout; stdbuf makes it line-buffered on a pipe.
        self.proc = subprocess.Popen(["stdbuf", "-oL", str(ctx.repl)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.log, env=ctx.repl_env)
        self.buffer = b""

    def send(self, request, timeout):
        start, cpu = time.monotonic(), cpu_seconds(self.proc.pid)
        try:
            self.proc.stdin.write((json.dumps(request, ensure_ascii=False) + "\n\n").encode())
            self.proc.stdin.flush()
        except BrokenPipeError:
            raise EOFError("REPL closed its input")
        while b"\n\n" not in self.buffer.lstrip(b"\n"):
            left = start + timeout - time.monotonic()
            if left <= 0 or not select.select([self.proc.stdout], [], [], left)[0]:
                raise TimeoutError
            chunk = os.read(self.proc.stdout.fileno(), 1 << 20)
            if not chunk:
                raise EOFError(f"REPL exited with code {self.proc.poll()}")
            self.buffer += chunk
        text, _, self.buffer = self.buffer.lstrip(b"\n").partition(b"\n\n")
        after = cpu_seconds(self.proc.pid)
        return json.loads(text), time.monotonic() - start, None if None in (cpu, after) else after - cpu

    def close(self):
        memory = proc_memory(self.proc.pid)
        self.proc.kill()
        self.proc.wait()
        self.log.close()
        return memory


def repl_output(response):
    """Lean-style text of a REPL response, so `verdict` applies; an Error object becomes one error line."""
    if "env" not in response and "proofState" not in response:
        return 1, "error: " + response.get("message", json.dumps(response)).removeprefix("Lean error:\n")
    messages = response.get("messages", [])
    return int(any(m["severity"] == "error" for m in messages)), "\n".join(f"{m['severity']}: {m['data']}" for m in messages)


def axiom_report(response, line, name):
    """Axioms of `name` from the one info message at `line` (the `#print axioms` command), or None. A message
    elsewhere (for example `trace "... does not depend on any axioms"` in the proof) or a report on another constant
    (for example `Shadow.name` after `namespace Shadow`) does not count. A `private` target (bank units) is printed
    as `_private.<module>.0.name`."""
    found = [m["data"].strip() for m in response.get("messages", []) if m["severity"] == "info" and m["pos"]["line"] == line]
    target = rf"'(?:_private(?:\.[^.'\s]+)*\.0\.)?{re.escape(name)}'"
    if len(found) != 1:
        return None
    if re.fullmatch(target + " does not depend on any axioms", found[0]):
        return []
    match = re.fullmatch(target + r" depends on axioms: \[([^\]]*)\]", found[0], re.S)
    return [x.strip() for x in match.group(1).split(",")] if match else None


def lean_string(text):
    """`text` as a Lean string literal."""
    escapes = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t"}
    return '"' + "".join(escapes.get(c, f"\\x{ord(c):02x}" if ord(c) < 32 else c) for c in text) + '"'


def one_command_check(source):
    """A run_cmd that fails with EXTRA_COMMAND when `source` parses as more than one command in the current
    environment, with the parser the REPL uses. It runs before `source` in the same REPL command, so nothing the
    candidate declares can change it. A source with a parse error is left to Lean, which reports the same error."""
    return ("run_cmd do\n  let ictx := Lean.Parser.mkInputContext " + lean_string(source) + " \"<candidate>\"\n"
            "  let env ← Lean.getEnv\n  let options ← Lean.getOptions\n"
            "  let currNamespace ← Lean.getCurrNamespace\n  let openDecls ← Lean.getOpenDecls\n"
            "  let pmctx : Lean.Parser.ParserModuleContext := { env := env, options := options, currNamespace := currNamespace, openDecls := openDecls }\n"
            "  let (_, s, m) := Lean.Parser.parseCommand ictx pmctx {} {}\n"
            "  let (stx, _, _) := Lean.Parser.parseCommand ictx pmctx s m\n"
            "  unless m.hasErrors || stx.isOfKind ``Lean.Parser.Command.eoi do\n"
            "    throwError \"EXTRA_COMMAND\"\n")


def repl_context(checker, environment, repl, output, timeout, setup_timeout, restart_after):
    """Settings and per-thread REPL state for verify_group. The caller sets `rows` and `eval_spans` for bank units."""
    libs = [environment / "mathlib/.lake/build/lib"] + sorted((environment / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    lean_env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    (output / "repl").mkdir(exist_ok=True)
    return argparse.Namespace(output=output.resolve(), environment=environment, checker=checker, repl=repl.resolve(),
                              timeout=timeout, setup_timeout=setup_timeout, restart_after=restart_after,
                              repl_env=dict(lean_env, LEAN_SYSROOT=str(checker.LEAN.parent),
                                            PATH=str(checker.LEAN) + os.pathsep + os.environ["PATH"]),
                              sources={}, local=threading.local(), pool_repls=set(), rows={}, eval_spans={})


def open_repl(ctx, head, tag, record):
    """A REPL with `head` elaborated, or None if the setup command fails or times out; its cost goes into `record`."""
    repl = Repl(ctx, ctx.output / "repl" / f"{tag}.log")
    start = time.monotonic()
    record["lean_calls"] += 1
    try:
        response, wall, cpu = repl.send({"cmd": head}, ctx.setup_timeout)
    except (TimeoutError, EOFError) as exc:
        repl.close()
        record["lean_seconds"] += time.monotonic() - start
        record["setup_error"] = type(exc).__name__
        return None, None
    record["lean_seconds"] += wall
    record["lean_cpu_seconds"] += cpu or 0.0
    code, output = repl_output(response)
    if code:
        repl.close()
        record["setup_error"] = first_error(output)
        return None, None
    return repl, response["env"]


class VerifierSetupError(RuntimeError):
    """A failed replay setup whose physical calls must survive propagation."""

    def __init__(self, message, counts):
        super().__init__(message)
        self.counts = dict(counts)


def verify_group(ctx, unit, continuations):
    """Lean verdicts for one unit's completions: the one scoring path of probe_nt_pool.py, pass_rate_map.py, and
    run_lean_grpo.py.

    Acceptance follows `check` in probe_start_depth.py: no FORBIDDEN match (rejected without a Lean call), then no
    error, TARGET_ABSENT, and axioms within ALLOWED_AXIOMS. Two rules close holes found by check_checker_attacks.py
    (2026-10-10): the axioms come only from the message of the `#print axioms` line and must name the target
    (axiom_report), and the declaration (given steps + completion) must parse as one command (one_command_check), so
    an indented `theorem`, `namespace`, or `@[command_elab]` after the proof is rejected. Each completion is its own
    REPL command in the environment after the prefix, so one completion cannot change the verdict of another. Bank
    units: one REPL per call with the unit's file prefix. Pool statements and RL tasks (unit kind "statement"): the pool
    header of verify_external_proofs.py; Lean checks header + " := by\n" + guard + the unit's optional "given" text +
    the completion; one REPL per worker thread kept across calls and restarted after restart_after commands; an empty
    completion or a line that is not indented is rejected without Lean. A setup that fails twice stops the run. A
    command that times out or crashes is rejected, counted as a Lean call with its wall time, and the REPL restarts."""
    statement = unit["kind"] == "statement"
    if statement:
        q = {"prefix": HEADER, "header": unit["header"], "name": unit["name"], "closing": []}
    else:
        with LOCK:
            q, skip = question(ctx.rows[unit["theorem_id"]], ctx.environment, ctx.sources, "file", ctx.eval_spans)
        if q is None or q["header"] != unit["header"]:
            raise RuntimeError(f"Unit {unit['task_id']} does not rebuild as in the bank: {skip or 'header differs'}")
    given = [{"tactic": step[2:]} for step in unit["given_steps"]]  # bank steps carry a 2-space indent
    head = split_prefix(q["prefix"])[0]
    local = ctx.local.__dict__  # per worker thread: the pool REPL, its env, and its command count
    repl, env, uses = (local.get("repl"), local.get("env"), local.get("uses", 0)) if statement else (None, None, 0)

    def drop():
        with LOCK:
            ctx.pool_repls.discard(repl)
        repl.close()

    results = []
    try:
        for text in continuations:
            record = {"lean_calls": 0, "lean_seconds": 0.0, "lean_cpu_seconds": 0.0}
            if FORBIDDEN.search(text):
                results.append(dict(record, status="forbidden_construct"))
                continue
            if statement and (not text.strip() or any(line.strip() and not line[0].isspace() for line in text.split("\n"))):
                results.append(dict(record, status="empty_or_top_level_line"))
                continue
            if statement and repl is not None and uses >= ctx.restart_after:
                drop()
                repl = None
            for attempt in (1, 2):
                if repl is None:
                    tag = f"pool_{threading.get_ident()}" if statement else unit["theorem_id"][:16]
                    repl, env = open_repl(ctx, head, tag, record)
                    uses = 0
                    if repl is None and attempt == 2:
                        raise VerifierSetupError(
                            f"REPL setup failed twice for {unit['task_id']}: {record['setup_error']}", record)
                    if repl is None:
                        continue
                    if statement:
                        with LOCK:
                            ctx.pool_repls.add(repl)
                body = (proof_body(given) + "\n" if given else "") + unit.get("given", "") + text.rstrip()
                declaration = ctx.checker.source_for(dict(q, closing=[]), body)[len(head):]
                command = (one_command_check(declaration) + ctx.checker.source_for(q, body)[len(head):]
                           + "\n#print axioms " + q["name"] + "\n")
                start = time.monotonic()
                record["lean_calls"] += 1
                try:
                    response, wall, cpu = repl.send({"cmd": command, "env": env}, ctx.timeout)
                except (TimeoutError, EOFError) as exc:  # a stuck or crashed REPL: reject, restart for the rest
                    drop()
                    repl = None
                    record["lean_seconds"] += time.monotonic() - start
                    record.update(status="timeout" if isinstance(exc, TimeoutError) else "crash")
                    break
                uses += 1
                code, output = repl_output(response)
                axioms = axiom_report(response, command.count("\n"), q["name"])  # the `#print axioms` line
                ok = (code == 0 and "TARGET_ABSENT" in output and "error:" not in output
                      and axioms is not None and set(axioms) <= ALLOWED_AXIOMS)
                record["lean_seconds"] += wall
                record["lean_cpu_seconds"] += cpu or 0.0
                record.update(status="accepted" if ok else "rejected", first_error=None if ok else first_error(output))
                break
            results.append(record)
    finally:
        if statement:
            local.update(repl=repl, env=env, uses=uses)
        elif repl is not None:
            repl.close()
    return results


def repl_setup(ctx, q, given, pad, mode, tag):
    """Start a REPL and elaborate the file prefix; for repl_tactic also the sorry header and the given steps."""
    start = time.monotonic()
    repl = Repl(ctx, ctx.output / "repl" / f"{tag}_{mode}.log")
    head, tail = split_prefix(q["prefix"])
    record = {"ok": False, "moved_prefix_chars": len(tail)}
    try:
        response, _, _ = repl.send({"cmd": head}, ctx.setup_timeout)
        code, output = repl_output(response)
        record.update(prefix_seconds=time.monotonic() - start, prefix_error=first_error(output))
        if code:
            return repl, record
        record["env"] = response["env"]
        if mode == "repl_env":
            record["ok"] = True
            return repl, record
        text = tail + q["header"] + " := by\n" + ctx.checker.guard(q["name"]) + pad + "sorry\n"
        response, _, _ = repl.send({"cmd": text, "env": record["env"]}, ctx.timeout)
        code, output = repl_output(response)
        sorries = response.get("sorries", [])
        if "error:" in output or "TARGET_ABSENT" not in output or len(sorries) != 1:
            record["header_error"] = first_error(output) or f"{len(sorries)} sorries"
            return repl, record
        state, goals = sorries[0]["proofState"], 1
        for i, step in enumerate(given):
            response, _, _ = repl.send({"tactic": step, "proofState": state}, ctx.timeout)
            code, output = repl_output(response)
            if code or response.get("sorries"):
                record["given_error"] = {"step": i, "error": first_error(output) or "sorry"}
                return repl, record
            state, goals = response["proofState"], len(response["goals"])
        record.update(ok=True, state=state, goals=goals)
        return repl, record
    except (TimeoutError, EOFError) as exc:
        record["failure"] = type(exc).__name__ + (": " + str(exc) if str(exc) else "")
        return repl, record
    finally:
        record["seconds"] = time.monotonic() - start
        record["rss_after_setup_kib"] = proc_memory(repl.proc.pid).get("VmRSS")


def run_subprocess(ctx, q, tag, given, pad, cases):
    results = {}
    for name, steps in cases.items():
        source = ctx.checker.source_for(q, body_of(given, steps)) + "\n#print axioms " + q["name"] + "\n"
        output, record = lean_file(ctx, ctx.output / "lean" / f"{tag}_{name}.lean", source)
        ok, axioms = verdict(record["exit_code"], output) if record["status"] == "returned" else (False, None)
        results[name] = dict(record, lean_ok=ok, axioms=axioms, first_error=first_error(output))
    return results, {"processes": []}


def run_repl(ctx, q, tag, given, pad, cases, mode):
    """Run all attempts of one task in one REPL; restart it after a timeout or crash."""
    results, processes, setups, repl, setup = {}, [], [], None, None
    for name, steps in cases.items():
        if repl is None:
            repl, setup = repl_setup(ctx, q, given, pad, mode, tag)
            setups.append(setup)
        result = {"setup_ok": setup["ok"], "lean_ok": False, "status": "returned", "wall_seconds": 0.0, "cpu_seconds": 0.0}
        start = time.monotonic()
        try:
            if not setup["ok"]:
                result["status"] = "setup_failed"
            elif mode == "repl_env":
                head = split_prefix(q["prefix"])[0]
                text = ctx.checker.source_for(q, body_of(given, steps))[len(head):] + "\n#print axioms " + q["name"] + "\n"
                response, wall, cpu = repl.send({"cmd": text, "env": setup["env"]}, ctx.timeout)
                code, output = repl_output(response)
                ok, axioms = verdict(code, output)
                result.update(wall_seconds=wall, cpu_seconds=cpu, lean_ok=ok, axioms=axioms, first_error=first_error(output))
            else:
                state, goals, tactics = setup["state"], setup["goals"], []
                for step in steps:
                    response, wall, cpu = repl.send({"tactic": step, "proofState": state}, ctx.timeout - (time.monotonic() - start))
                    code, output = repl_output(response)
                    tactic = {"ok": not code, "goals": len(response.get("goals", [])), "sorries": len(response.get("sorries", [])),
                              "seconds": wall, "error": first_error(output)}
                    tactics.append(tactic)
                    result["wall_seconds"] += wall
                    result["cpu_seconds"] = None if None in (cpu, result["cpu_seconds"]) else result["cpu_seconds"] + cpu
                    if code:
                        break
                    state, goals = response["proofState"], tactic["goals"]
                done = len(tactics) == len(steps) and all(t["ok"] for t in tactics)
                result.update(tactics=tactics, start_goals=setup["goals"], final_goals=goals if done else None,
                              lean_ok=done and goals == 0 and not any(t["sorries"] for t in tactics),
                              first_error=next((t["error"] for t in tactics if t["error"]), None))
        except (TimeoutError, EOFError) as exc:
            result.update(status="timeout" if isinstance(exc, TimeoutError) else "crash", wall_seconds=time.monotonic() - start,
                          cpu_seconds=None, detail=str(exc))
            processes.append(repl.close())
            repl = None
        if repl is not None:
            result["rss_after_kib"] = proc_memory(repl.proc.pid).get("VmRSS")
        results[name] = result
    if repl is not None:
        processes.append(repl.close())
    return results, {"setups": setups, "processes": processes}


def run_task(ctx, index, task):
    q = ctx.questions[task["theorem_id"]]
    given, pad, cases = variants(task["steps"])
    tag = f"{index:02d}_{task['theorem_id'][:12]}"
    order = MODES[index % 3:] + MODES[:index % 3]
    results, setup = {}, {}
    for mode in order:
        runner = run_subprocess if mode == "subprocess" else lambda *a, m=mode: run_repl(*a, m)
        results[mode], setup[mode] = runner(ctx, q, tag, given, pad, cases)
    rows = []
    for name, steps in cases.items():
        continuation = "\n".join(steps)
        forbidden = bool(FORBIDDEN.search(continuation))
        row = {"task_index": index, "theorem_id": task["theorem_id"], "full_name": task["full_name"],
               "family": task["family"], "steps": len(task["steps"]), "depth": DEPTH, "variant": name,
               "continuation": continuation, "forbidden": forbidden, "mode_order": list(order)}
        for mode in MODES:
            row[mode] = dict(results[mode][name], accepted=not forbidden and results[mode][name]["lean_ok"])
        rows.append(row)
    with LOCK:
        with (ctx.output / "attempts.jsonl").open("a") as out:
            out.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        with (ctx.output / "tasks.jsonl").open("a") as out:
            out.write(json.dumps({"task_index": index, "theorem_id": task["theorem_id"], "full_name": task["full_name"],
                                  "mode_order": list(order), **setup}, ensure_ascii=False) + "\n")
    print(json.dumps({"task": index, "id": task["theorem_id"][:12],
                      **{m: sum(r[m]["accepted"] for r in rows) for m in MODES}}), flush=True)


def stats(values):
    """n, median, p90 (nearest rank), max, and mean."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0}
    n = len(values)
    median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
    return {"n": n, "median": median, "p90": values[math.ceil(0.9 * n) - 1], "max": values[-1], "mean": sum(values) / n}


def summarize(output, workers_projected, group):
    with (output / "attempts.jsonl").open() as input_stream:
        rows = [json.loads(line) for line in input_stream]
    with (output / "tasks.jsonl").open() as input_stream:
        tasks = [json.loads(line) for line in input_stream]
    gib = 1024 ** 2
    summary = {"attempts": len(rows), "tasks": len(tasks), "depth": DEPTH, "forbidden": sum(r["forbidden"] for r in rows),
               "variants": dict(Counter(r["variant"] for r in rows)),
               **json.loads((output / "run.json").read_text()), "modes": {}}
    for mode in MODES:
        results = [r[mode] for r in rows]
        disagreements = [{"theorem_id": r["theorem_id"][:12], "variant": r["variant"], "subprocess": r["subprocess"]["accepted"],
                          mode: r[mode]["accepted"], "subprocess_error": r["subprocess"]["first_error"],
                          "error": r[mode].get("first_error"), "status": r[mode]["status"]}
                         for r in rows if r[mode]["accepted"] != r["subprocess"]["accepted"]]
        lean_disagreements = [{"theorem_id": r["theorem_id"][:12], "variant": r["variant"], "subprocess_lean_ok": r["subprocess"]["lean_ok"],
                               "lean_ok": r[mode]["lean_ok"], "error": r[mode].get("first_error")}
                              for r in rows if r[mode]["lean_ok"] != r["subprocess"]["lean_ok"]]
        # Timing covers attempts that reached Lean; a failed REPL setup sends no attempt.
        evaluated = [r for r in rows if r[mode]["status"] != "setup_failed"]
        latency = stats(r[mode]["wall_seconds"] for r in evaluated)
        entry = {"evaluated": len(evaluated), "latency_seconds": latency,
                 "latency_seconds_without_empty": stats(r[mode]["wall_seconds"] for r in evaluated if r["variant"] != "empty"),
                 "cpu_seconds": stats(r[mode]["cpu_seconds"] for r in evaluated),
                 "agreement_on_evaluated": sum(r[mode]["accepted"] == r["subprocess"]["accepted"] for r in evaluated),
                 "statuses": dict(Counter(x["status"] for x in results)),
                 "accepted": sum(x["accepted"] for x in results), "lean_ok": sum(x["lean_ok"] for x in results),
                 "accepted_by_variant": {v: sum(r[mode]["accepted"] for r in rows if r["variant"] == v) for v in summary["variants"]},
                 "lean_ok_by_variant": {v: sum(r[mode]["lean_ok"] for r in rows if r["variant"] == v) for v in summary["variants"]},
                 "agreement_with_subprocess": len(rows) - len(disagreements), "disagreements": disagreements,
                 "lean_ok_agreement_with_subprocess": len(rows) - len(lean_disagreements), "lean_ok_disagreements": lean_disagreements}
        if mode == "subprocess":
            setup = {"n": 0, "mean": 0.0}
            peak = stats(x["peak_rss_kib"] / gib for x in results)
            anon = stats(x["rss_anon_kib"] / gib for x in results if x["rss_anon_kib"])
            file_backed = stats(x["rss_file_kib"] / gib for x in results if x["rss_file_kib"])
        else:
            setups = [s for t in tasks for s in t[mode]["setups"]]
            processes = [p for t in tasks for p in t[mode]["processes"] if p]
            setup = stats(t[mode]["setups"][0]["seconds"] for t in tasks if t[mode]["setups"][0]["ok"])
            peak = stats(p["VmHWM"] / gib for p in processes)
            anon = stats(p["RssAnon"] / gib for p in processes)
            file_backed = stats(p["RssFile"] / gib for p in processes)
            growth = []
            for t in tasks:
                first = t[mode]["setups"][0]
                after = [r[mode].get("rss_after_kib") for r in rows if r["theorem_id"] == t["theorem_id"]]
                if first["ok"] and first.get("rss_after_setup_kib") and after and after[-1] and len(t[mode]["setups"]) == 1:
                    growth.append((after[-1] - first["rss_after_setup_kib"]) / 1024 / len(after))
            entry.update(setups=len(setups), setup_failures=sum(not s["ok"] for s in setups),
                         restarts=len(setups) - len(tasks), rss_growth_mib_per_attempt=stats(growth))
            if mode == "repl_tactic":
                tactics = [t for x in results for t in x.get("tactics", [])]
                entry["tactic_calls"] = stats(t["seconds"] for t in tactics)
                reference = [r[mode] for r in rows if r["variant"] == "reference"]
                entry["reference_tactics_ok"] = [sum(t["ok"] for t in x.get("tactics", [])) for x in reference]
                entry["reference_tactics_sent"] = [len(x.get("tactics", [])) for x in reference]
        per_attempt_cold = latency["mean"] + setup.get("mean", 0.0) / group
        entry.update(setup_seconds_per_task=setup, peak_rss_gib=peak, rss_anon_gib=anon, rss_file_gib=file_backed,
                     projection={"workers": workers_projected, "group_size": group,
                                 "attempts_per_second_setup_per_group": workers_projected / per_attempt_cold,
                                 "attempts_per_second_setup_cached": workers_projected / latency["mean"],
                                 "memory_gib_upper_bound": workers_projected * peak["max"],
                                 "memory_gib_shared_file_pages": workers_projected * anon["max"] + file_backed["max"]})
        summary["modes"][mode] = entry
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    for name in ("train", "checker", "environment", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--tasks", type=Path, action="append", required=True, help="tasks.jsonl of build_proof_tasks.py; repeat")
    parser.add_argument("--eval", type=Path, action="append", required=True,
                        help="Validation or test export (zip); its declarations are blanked from file contexts")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--limit", type=int, help="Run only the first n sampled tasks (smoke test)")
    parser.add_argument("--workers", type=int, default=4, help="Threads; each runs one Lean process at a time")
    parser.add_argument("--timeout", type=float, default=20, help="Seconds per attempt, in every mode")
    parser.add_argument("--setup-timeout", type=float, default=120, help="Seconds for the REPL file-prefix command")
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--project-workers", type=int, default=16)
    parser.add_argument("--summarize", action="store_true", help="Only rebuild summary.json from the output folder")
    args = parser.parse_args()
    if args.summarize:
        print(json.dumps(summarize(args.output, args.project_workers, 8)["modes"], indent=1)[:4000])
        return
    args.output.mkdir(parents=True, exist_ok=False)
    for sub in ("lean", "repl"):
        (args.output / sub).mkdir()
    environment = args.environment.resolve()
    ctx = argparse.Namespace(output=args.output.resolve(), timeout=args.timeout, setup_timeout=args.setup_timeout,
                             repl=args.repl.resolve(), checker=load_checker(args.checker, environment, args.output))
    ctx.lean = ctx.checker.LEAN / "lean"
    libs = [environment / "mathlib/.lake/build/lib"] + sorted((environment / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    ctx.lean_env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    ctx.repl_env = dict(ctx.lean_env, LEAN_SYSROOT=str(ctx.checker.LEAN.parent),
                        PATH=str(ctx.checker.LEAN) + os.pathsep + os.environ["PATH"])
    tasks, pool = sample_tasks(args.tasks, args.count, args.seed)
    tasks = tasks[:args.limit]
    eval_spans = defaultdict(list)
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as input_stream:
                for line in input_stream:
                    r = json.loads(line)
                    eval_spans[r["file_path"]].append((r["start"][0], r["end"][0], r["declaration_header"]))
    wanted = {t["theorem_id"] for t in tasks}
    rows = {r["theorem_id"]: r for r in load(args.train) if r["theorem_id"] in wanted}
    ctx.questions, sources = {}, {}
    for task in tasks:
        q, skip = question(rows[task["theorem_id"]], environment, sources, "file", eval_spans)
        if q is None:
            raise RuntimeError("Task failed to rebuild: " + skip)
        ctx.questions[task["theorem_id"]] = q
    del rows, sources
    commit = subprocess.run(["git", "-C", str(ctx.repl.parents[3]), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    (args.output / "run.json").write_text(json.dumps({
        "lean": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5", "repl_commit": commit,
        "seed": args.seed, "pool": pool, "sampled": [t["theorem_id"] for t in tasks], "workers": args.workers,
        "timeout_seconds": args.timeout, "setup_timeout_seconds": args.setup_timeout}, indent=2) + "\n")
    with ThreadPoolExecutor(args.workers) as executor:
        for future in [executor.submit(run_task, ctx, i, t) for i, t in enumerate(tasks)]:
            future.result()
    print(json.dumps(summarize(args.output, args.project_workers, len(variants(tasks[0]["steps"])[2]))["modes"], indent=1)[:4000], flush=True)


if __name__ == "__main__":
    main()
