"""Elaborate theorem statements under the pinned Lean and mathlib, with `sorry` as the proof. No proof is checked.

Input: --statements JSONL with `name`, `formal_statement` and optionally `split` (for example the DeepSeek-Prover-V1.5
miniF2F file, 488 rows, each ending in `:= by`). A trailing `:= by`, `:=` or `:= by sorry` is removed; the header gets
one ` := by` and one `sorry`.

Lean path: the one used by verify_group of bench_lean_verifier.py (and so by the held-out screens): the pool HEADER of
verify_external_proofs.py (maxHeartbeats 400000, not the 0 of the DeepSeek file), one REPL per worker thread opened by
open_repl and restarted after --repl-restart-after commands, the checker's source_for and target guard, and
one_command_check. verify_group itself rejects `sorry` before Lean, so this stage sends the same command text with
`sorry` as the body and reads the REPL messages:
- elaborates: no error, TARGET_ABSENT reported, and the expected warning "declaration uses 'sorry'";
- error: any error (first_error is the first error line), or the guard or the sorry warning is missing, or the REPL
  crashed;
- timeout: no answer within --lean-timeout seconds (the REPL restarts).

Output: --output must be new (one writer). checked.jsonl (one row per statement, input order) and summary.json
(n = len(rows), counts by status and by split and status, Lean seconds, pins, input SHA-256).

Memory: one REPL per worker (3.7 GB RSS measured for the screens). Run under 4.5 GiB x --workers + 2 GiB; with the
default 4 workers, 20 GiB.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from bench_lean_verifier import (  # noqa: E402
    LOCK, VerifierSetupError, first_error, one_command_check, open_repl, repl_context, repl_output, split_prefix,
)
from heldout_screens import counts, read, write_json  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from verify_external_proofs import HEADER  # noqa: E402

SORRY_WARNING = "declaration uses 'sorry'"
TAIL = re.compile(r"\s*:=\s*(?:by\s*)?(?:sorry\s*)?$")


def header_of(formal_statement):
    """The theorem header without a trailing `:= by`, `:=` or `:= by sorry`; an inner `:=` stays."""
    return TAIL.sub("", formal_statement.strip())


def name_in(header):
    found = re.match(r"\s*theorem\s+(\S+)", header)
    return found and found[1]


def check_rows(rows):
    """Names are unique and each statement declares a theorem of its row's name (the guard targets that name)."""
    if len({r["name"] for r in rows}) != len(rows):
        raise ValueError("duplicate names in the statements file")
    for r in rows:
        if name_in(header_of(r["formal_statement"])) != r["name"]:
            raise ValueError(f"{r['name']}: the statement does not declare `theorem {r['name']}`")
    return rows


def declaration_for(ctx, header, name):
    """header + ` := by` + target guard + `  sorry`, after the REPL's elaborated prefix, as verify_group builds it."""
    q = {"prefix": HEADER, "header": header, "name": name, "closing": []}
    return ctx.checker.source_for(q, "  sorry")[len(split_prefix(HEADER)[0]):]


def command_for(ctx, header, name):
    """The REPL command: one_command_check (it quotes the declaration as a string) and then the declaration."""
    declaration = declaration_for(ctx, header, name)
    return one_command_check(declaration) + declaration


def classify(response):
    code, output = repl_output(response)
    error = first_error(output)
    sorry = SORRY_WARNING in output
    if code or error:
        return "error", error or "error", sorry
    if "TARGET_ABSENT" not in output:
        return "error", "TARGET_ABSENT not reported", sorry
    if not sorry:
        return "error", "no sorry warning", sorry
    return "elaborates", None, sorry


def elaborate_one(ctx, row):
    """One statement on this thread's REPL; per-thread state and restarts as in verify_group."""
    start = time.monotonic()
    header = header_of(row["formal_statement"])
    head = split_prefix(HEADER)[0]
    local = ctx.local.__dict__
    repl, env, uses = local.get("repl"), local.get("env"), local.get("uses", 0)
    record = {"lean_calls": 0, "lean_seconds": 0.0, "lean_cpu_seconds": 0.0}

    def drop():
        with LOCK:
            ctx.pool_repls.discard(repl)
        repl.close()

    try:
        if repl is not None and uses >= ctx.restart_after:
            drop()
            repl = None
        for attempt in (1, 2):
            if repl is None:
                repl, env = open_repl(ctx, head, f"elab_{threading.get_ident()}", record)
                uses = 0
                if repl is None and attempt == 2:
                    raise VerifierSetupError(f"REPL setup failed twice at {row['name']}: {record['setup_error']}", record)
                if repl is None:
                    continue
                with LOCK:
                    ctx.pool_repls.add(repl)
            break
        sent = time.monotonic()
        record["lean_calls"] += 1
        try:
            response, wall, cpu = repl.send({"cmd": command_for(ctx, header, row["name"]), "env": env}, ctx.timeout)
        except (TimeoutError, EOFError) as exc:  # a stuck or crashed REPL: restart for the next statement
            drop()
            repl = None
            record["lean_seconds"] += time.monotonic() - sent
            status, error, sorry = (("timeout", None, False) if isinstance(exc, TimeoutError)
                                    else ("error", f"REPL crash: {exc}", False))
        else:
            uses += 1
            record["lean_seconds"] += wall
            record["lean_cpu_seconds"] += cpu or 0.0
            status, error, sorry = classify(response)
    finally:
        local.update(repl=repl, env=env, uses=uses)
    return dict(record, name=row["name"], split=row.get("split"), status=status, first_error=error,
                sorry_warning=sorry, wall_seconds=time.monotonic() - start)


def elaborate(ctx, rows, workers):
    """Records in input order; only the calling thread collects them. Every worker REPL is closed at the end."""
    try:
        with ThreadPoolExecutor(workers) as pool:
            return list(pool.map(lambda row: elaborate_one(ctx, row), rows))
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()


def summarize(records):
    splits = sorted({str(r["split"]) for r in records})
    return {"n": len(records), "by_status": counts(records, "status"),
            "by_split_status": {s: dict(sorted(Counter(r["status"] for r in records if str(r["split"]) == s).items()))
                                for s in splits},
            "sorry_warnings": sum(r["sorry_warning"] for r in records),
            "lean_calls": sum(r["lean_calls"] for r in records),
            "lean_seconds": round(sum(r["lean_seconds"] for r in records), 1)}


def write_outputs(out, records, extra):
    with (out / "checked.jsonl").open("x") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    write_json(out / "summary.json", dict(summarize(records), **extra))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for name in ("statements", "environment", "checker", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, help="first N statements only")
    parser.add_argument("--lean-timeout", type=float, default=20, help="seconds per statement, as in the screens")
    parser.add_argument("--setup-timeout", type=float, default=120)
    parser.add_argument("--repl-restart-after", type=int, default=400)
    args = parser.parse_args()
    started = time.monotonic()
    rows = check_rows(read(args.statements))
    rows = rows[:args.limit] if args.limit is not None else rows
    args.output.mkdir(parents=True, exist_ok=False)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)
    ctx = repl_context(checker, environment, args.repl, args.output, args.lean_timeout, args.setup_timeout,
                       args.repl_restart_after)
    records = elaborate(ctx, rows, args.workers)
    extra = {"statements": str(args.statements),
             "statements_sha256": hashlib.sha256(args.statements.read_bytes()).hexdigest(),
             "limit": args.limit, "header": HEADER, "lean": "v4.10.0-rc1",
             "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5", "workers": args.workers,
             "lean_timeout": args.lean_timeout, "wall_seconds": round(time.monotonic() - started, 1)}
    write_outputs(args.output, records, extra)
    print(json.dumps(summarize(records)), flush=True)


if __name__ == "__main__":
    main()
