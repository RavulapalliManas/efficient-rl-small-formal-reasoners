"""RL hole tasks from Lean-checked proofs of the held-out problems, plus one whole-statement task per RL pool statement.

A hole task cuts a proof at a line boundary; the model writes everything after the cut. The reward check of a task is
the pool HEADER + header + " := by\n" + given + continuation; given ends with a newline when it is not empty.
Proofs: accepted rows of verify_external_proofs.py (--proofs) for the --problems names, train split of the pool, with
the filters of build_nt_sft_data.py (statement equal to the pool statement, no FORBIDDEN construct, no non-indented
line, at most --max-chars). Comments are removed with strip_comments of build_nt_sft_data.py, so every cut falls
between tactic lines. One proof per problem keeps task_id unique: among the proofs that verify_group accepts, the one
with the most top-level steps; ties go to the smallest sha256("nt_splits_v1/" + name + "/" + body).
Kinds (task_id "<name>:<kind>:<k>"):
- prefix_top: given = the first k top-level steps (top_level_steps of build_proof_tasks.py), k = 1 .. n-1.
- prefix_have: given = the first k lines, where line k is `have ... := by` (bullets allowed before it) and line k+1
  is indented deeper, so the continuation starts with the proof of that have.
- whole (k = 0): one per --rl-pool statement; given and reference_rest empty, state from --states, lines_remaining null.
A cut is valid when (1) verify_group of bench_lean_verifier.py (the RL reward path) accepts the stripped proof, which
equals given + reference_rest for every cut of that proof; (2) the REPL, on header + given + `trace_state` and then
`all_goals sorry` at the cut column and at every enclosing block column, reports no error and exactly one information
message on the trace_state line (the state); (3) the state is not empty, which is how trace_state reports no goals
at this pin (an empty continuation would pass);
(4) the state's token Jaccard (tokens() of profile_external_proofs.py) to every --eval-states state is below --jaccard;
(5) given does not end in `<;>` (the first tactic of the continuation would bind to that combinator; checked first,
without Lean).
Output (a new directory): proofs.jsonl, cuts.jsonl, tasks.jsonl, invalid.jsonl, summary.json.

Memory: ~11k proof rows (<300 MiB) + one REPL per worker (budget 2 GiB each, as for probe_nt_pool.py).
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
import threading
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval", "audit")]
from bench_lean_verifier import LOCK, open_repl, repl_context, verify_group  # noqa: E402
from build_nt_sft_data import body_of, strip_comments  # noqa: E402
from build_proof_tasks import top_level_steps  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import header  # noqa: E402
from probe_start_depth import FORBIDDEN  # noqa: E402
from profile_external_proofs import tokens  # noqa: E402
from verify_external_proofs import HEADER  # noqa: E402

SALT = "nt_splits_v1"
HAVE = re.compile(r"\s*(·\s*)*have\b.*:=\s*by\s*$")


def indent(line):
    return len(line) - len(line.lstrip())


def cut_points(body):
    """(kind, k, given, rest, columns) per cut; trace_state goes at columns[0], `all_goals sorry` at every column."""
    steps, pad = top_level_steps(body)
    for k in range(1, len(steps)):
        yield "prefix_top", k, "\n".join(steps[:k]) + "\n", "\n".join(steps[k:]), [len(pad)]
    lines, stack, opens = body.split("\n"), [], True  # stack: columns of the tactic blocks open after each line
    for i, line in enumerate(lines[:-1]):
        if opens:
            stack.append(indent(line))
        while stack and stack[-1] > indent(line):
            stack.pop()
        rest = line.strip()
        while rest.startswith("·"):  # a bullet opens a block at its content column
            rest = rest[1:].lstrip()
            stack.append(len(line) - len(rest))
        opens = rest == "by" or rest.endswith((" by", "=>"))
        if HAVE.fullmatch(line) and indent(lines[i + 1]) > stack[-1]:
            yield ("prefix_have", i + 1, "\n".join(lines[:i + 1]) + "\n", "\n".join(lines[i + 1:]),
                   [indent(lines[i + 1])] + stack[::-1])


def run_command(ctx, command):
    """(response, wall s, failure) of one REPL command in the pool-header environment; one REPL per worker thread."""
    local = ctx.local.__dict__
    if local.get("repl") is not None and local["uses"] >= ctx.restart_after:
        drop(ctx, local)
    if local.get("repl") is None:
        record = {"lean_calls": 0, "lean_seconds": 0.0, "lean_cpu_seconds": 0.0}
        for _ in (1, 2):
            repl, env = open_repl(ctx, HEADER, f"state_{threading.get_ident()}", record)
            if repl is not None:
                break
        if repl is None:
            raise RuntimeError(f"REPL setup failed twice: {record.get('setup_error')}")
        with LOCK:
            ctx.pool_repls.add(repl)
        local.update(repl=repl, env=env, uses=0)
    start = time.monotonic()
    try:
        response, wall, _ = local["repl"].send({"cmd": command, "env": local["env"]}, ctx.timeout)
    except (TimeoutError, EOFError) as exc:
        drop(ctx, local)
        return None, time.monotonic() - start, "state_timeout" if isinstance(exc, TimeoutError) else "state_crash"
    local["uses"] += 1
    return response, wall, None


def drop(ctx, local):
    with LOCK:
        ctx.pool_repls.discard(local["repl"])
    local["repl"].close()
    local["repl"] = None


def state_at(ctx, head, given, columns):
    """The trace_state message at the cut, or None and the reason the cut is invalid."""
    text = head + " := by\n" + given
    command = text + " " * columns[0] + "trace_state\n" + "".join(" " * c + "all_goals sorry\n" for c in columns)
    response, wall, failure = run_command(ctx, command)
    if failure:
        return None, failure, wall
    if "env" not in response:
        return None, "state_error: " + response.get("message", "")[:200], wall
    messages = response.get("messages", [])
    errors = [m["data"] for m in messages if m["severity"] == "error"]
    found = [m["data"] for m in messages if m["severity"] == "info" and m["pos"]["line"] == text.count("\n") + 1]  # REPL label
    if errors:
        return None, "state_error: " + errors[0][:200], wall
    if len(found) != 1:
        return None, "no_state_message", wall
    state = found[0].strip()
    return (None, "no_goals_at_cut", wall) if state in ("", "no goals") else (state, None, wall)


def in_parallel(workers, function, items):
    with ThreadPoolExecutor(workers) as pool:
        yield from zip(items, pool.map(function, items))


def main():
    parser = argparse.ArgumentParser()
    for name in ("pool", "problems", "rl_pool", "states", "environment", "checker", "repl", "output"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--proofs", type=Path, action="append", required=True)
    parser.add_argument("--eval-states", type=Path, action="append", required=True, help="states.jsonl of eval statements")
    parser.add_argument("--max-chars", type=int, default=4000, help="as in build_nt_sft_data.py")
    parser.add_argument("--jaccard", type=float, default=0.7)
    parser.add_argument("--lean-timeout", type=float, default=20)
    parser.add_argument("--setup-timeout", type=float, default=120)
    parser.add_argument("--repl-restart-after", type=int, default=400)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    started = time.monotonic()
    with args.pool.open() as input_stream:
        pool = {r["name"]: r for r in map(json.loads, input_stream) if r["split"] == "train"}
    problems, rl_pool = set(args.problems.read_text().split()), sorted(set(args.rl_pool.read_text().split()))
    if not problems <= set(pool) or not set(rl_pool) <= set(pool):
        raise RuntimeError("--problems and --rl-pool must name train statements of the pool")
    args.output.mkdir(parents=True, exist_ok=False)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)

    # Distinct comment-free bodies of accepted proofs, with the filters of build_nt_sft_data.py.
    counts, found = Counter(), {}
    for path in args.proofs:
        with path.open() as input_stream:
            for line in input_stream:
                r = json.loads(line)
                if r["name"] not in problems:
                    continue
                counts["proof_rows"] += 1
                body = body_of(r["text"], header(pool[r["name"]])) if r["status"] == "accepted" else None
                if r["status"] != "accepted":
                    counts["skipped_not_accepted"] += 1
                elif body is None:
                    counts["skipped_statement_differs"] += 1
                elif FORBIDDEN.search(body) or any(x.strip() and not x[0].isspace() for x in body.split("\n")):
                    counts["skipped_forbidden_or_top_level"] += 1
                elif len(body) > args.max_chars:
                    counts["skipped_too_long"] += 1
                elif (r["name"], strip_comments(body)) in found:
                    counts["skipped_duplicate_after_strip"] += 1
                else:
                    found[r["name"], strip_comments(body)] = r["source"].split("_proof")[0]
    counts["distinct_bodies"] = len(found)

    # (1) The reward path on every distinct body; then one accepted proof per problem.
    ctx = repl_context(checker, environment, args.repl, args.output, args.lean_timeout, args.setup_timeout, args.repl_restart_after)
    unit = lambda name: {"task_id": "proof:" + name, "kind": "statement", "name": name, "header": header(pool[name]), "given_steps": []}
    proofs, lean_calls, lean_seconds = [], 0, 0.0
    try:
        for (name, body), (result,) in in_parallel(args.workers, lambda key: verify_group(ctx, unit(key[0]), [key[1]]), list(found)):
            proofs.append({"name": name, "source": found[name, body], "body": body, "top_level_steps": len(top_level_steps(body)[0]),
                           "status": result["status"], "first_error": result.get("first_error")})
            lean_calls, lean_seconds = lean_calls + result["lean_calls"], lean_seconds + result["lean_seconds"]
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()
    chosen = {}
    for p in sorted((p for p in proofs if p["status"] == "accepted"),
                    key=lambda p: (-p["top_level_steps"], hashlib.sha256(f"{SALT}/{p['name']}/{p['body']}".encode()).hexdigest())):
        chosen.setdefault(p["name"], p)
    with (args.output / "proofs.jsonl").open("x") as out:
        for p in sorted(proofs, key=lambda p: (p["name"], p["body"])):
            out.write(json.dumps(dict(p, selected=chosen.get(p["name"]) is p), ensure_ascii=False) + "\n")

    # (2)-(3) The goal state at each cut of each chosen proof.
    cuts = []
    for name, p in sorted(chosen.items()):
        for kind, k, given, rest, columns in cut_points(p["body"]):
            if given + rest != p["body"]:
                raise RuntimeError(f"cut {name}:{kind}:{k} does not rebuild the proof")
            cuts.append({"task_id": f"{name}:{kind}:{k}", "name": name, "kind": kind, "k": k, "given": given,
                         "reference_rest": rest, "columns": columns})
    ctx = repl_context(checker, environment, args.repl, args.output, args.lean_timeout, args.setup_timeout, args.repl_restart_after)
    try:
        with (args.output / "cuts.jsonl").open("x") as out:
            for cut in cuts:
                if cut["given"].rstrip().endswith("<;>"):
                    cut.update(state=None, reason="given_ends_in_combinator", lean_seconds=0.0)
                    out.write(json.dumps(cut, ensure_ascii=False) + "\n")
            pending = [c for c in cuts if "reason" not in c]
            for cut, (state, reason, wall) in in_parallel(args.workers, lambda c: state_at(ctx, header(pool[c["name"]]), c["given"], c["columns"]), pending):
                cut.update(state=state, reason=reason, lean_seconds=wall)
                lean_calls, lean_seconds = lean_calls + 1, lean_seconds + wall
                out.write(json.dumps(cut, ensure_ascii=False) + "\n")
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()

    # (4) Near-duplicate states, then the whole-statement tasks.
    evaluation = []
    for path in args.eval_states:
        with path.open() as input_stream:
            evaluation.extend((row["name"], tokens(row["state"]))
                              for row in map(json.loads, input_stream) if row["state"])
    eval_without_state = 0
    for path in args.eval_states:
        with path.open() as input_stream:
            eval_without_state += sum(not row["state"] for row in map(json.loads, input_stream))
    tasks, invalid = [], []
    for cut in cuts:
        if cut["reason"] is None:
            mine = tokens(cut["state"])
            j, match = max((len(mine & t) / max(len(mine | t), 1), n) for n, t in evaluation)
            cut.update(eval_jaccard=round(j, 3), eval_match=match)
            if j >= args.jaccard:
                cut["reason"] = "near_duplicate_eval_state"
        if cut["reason"] is None:
            tasks.append({"task_id": cut["task_id"], "name": cut["name"], "kind": cut["kind"], "header": header(pool[cut["name"]]),
                          "given": cut["given"], "state": cut["state"], "reference_rest": cut["reference_rest"],
                          "lines_remaining": len(cut["reference_rest"].split("\n"))})
        else:
            invalid.append({k: cut.get(k) for k in ("task_id", "reason", "eval_jaccard", "eval_match")})
    with args.states.open() as input_stream:
        states = {r["name"]: r["state"] for r in map(json.loads, input_stream)}
    for name in rl_pool:
        if states.get(name):
            tasks.append({"task_id": f"{name}:whole:0", "name": name, "kind": "whole", "header": header(pool[name]),
                          "given": "", "state": states[name], "reference_rest": "", "lines_remaining": None})
        else:
            invalid.append({"task_id": f"{name}:whole:0", "reason": "no_state"})
    with (args.output / "tasks.jsonl").open("x") as out:
        for t in tasks:
            out.write(json.dumps(t, ensure_ascii=False) + "\n")
    with (args.output / "invalid.jsonl").open("x") as out:
        for row in invalid:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

    def spread(values):
        bucket = Counter(str(v) if v <= 5 else "6-10" if v <= 10 else "11-20" if v <= 20 else "21+" for v in values)
        return {"n": len(values), "min": min(values), "median": statistics.median(values), "max": max(values),
                "histogram": {b: bucket[b] for b in ("1", "2", "3", "4", "5", "6-10", "11-20", "21+")}} if values else {"n": 0}
    kinds = ("prefix_top", "prefix_have", "whole")
    summary = {
        "proof_filter_counts": dict(counts), "proof_statuses": dict(Counter(p["status"] for p in proofs)),
        "problems": len(problems), "problems_with_accepted_proof": len(chosen),
        "accepted_proofs_not_selected": sum(p["status"] == "accepted" for p in proofs) - len(chosen),
        "selected_by_source": dict(Counter(p["source"] for p in chosen.values())),
        "cuts_by_kind": dict(Counter(c["kind"] for c in cuts)),
        "tasks_by_kind": {k: sum(t["kind"] == k for t in tasks) for k in kinds},
        "problems_with_hole_tasks": len({t["name"] for t in tasks if t["kind"] != "whole"}),
        "lines_remaining": {k: spread([t["lines_remaining"] for t in tasks if t["kind"] == k]) for k in kinds[:2]},
        "invalid_by_kind_and_reason": {k: dict(Counter(re.sub(r":.*", "", i["reason"]) for i in invalid if i["task_id"].split(":")[-2] == k))
                                       for k in kinds},
        "eval_states": len(evaluation), "eval_without_state": eval_without_state, "jaccard": args.jaccard,
        "eval_jaccard_of_valid_cuts_at_least": {x: sum(c.get("eval_jaccard", 0) >= x for c in cuts) for x in (0.5, 0.6, 0.7, 0.8)},
        "lean_calls": lean_calls, "lean_seconds": lean_seconds, "wall_seconds": time.monotonic() - started,
        "lean_timeout": args.lean_timeout, "workers": args.workers}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
