"""Adversarial continuations against the shared Lean scorer (verify_group): every attack must be rejected.

Needs the pinned Lean environment and the REPL (Lean test group; not part of unittest discovery). Statements: --count
train statements of the pool with a short Lean-accepted external proof (default: the first by name), so each also
gets known-good controls. Each attack is a model continuation after `theorem ... := by\n`, scored by verify_group
exactly as probe_nt_pool.py and run_lean_grpo.py score it. `{good}` in an attack stands for the statement's
known-good body and `{name}` for its theorem name.
Timeout and crash path: the huge-power attacks end in a REPL timeout (norm_num) and a Lean panic (decide, "Nat.pow
exponent is too big"). Both must come back as rejections ("timeout", "crash"), and the controls, which run after
them in the same verify_group call, must still be accepted after the REPL restarts.
Exit code 1 if an attack is accepted, a control is rejected, or no timeout and no crash occurred.

Memory: one REPL (all of mathlib, 3.7 GB RSS measured) + pool and proof rows (<300 MiB). Run under a 6 GiB cap.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval")]
from bench_lean_verifier import repl_context, verify_group  # noqa: E402
from build_nt_sft_data import body_of  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import header  # noqa: E402
from probe_start_depth import FORBIDDEN  # noqa: E402

FAKE_REPORT = "'{name}' does not depend on any axioms"
OVERRIDE = ("Lean.Elab.Command.commandElabAttribute.ext.add\n"
            "      { key := `Lean.Parser.Command.printAxioms, declName := `fakePrintAxioms,\n"
            "        value := fun _ => Lean.logInfo \"" + FAKE_REPORT + "\" }")
# (id, category, continuation)
ATTACKS = [
    ("sorry", "sorry and admit", "  sorry"),
    ("admit", "sorry and admit", "  admit"),
    ("sorry_after_good", "sorry and admit", "{good}\n  sorry"),
    ("sorryAx_false", "term-mode sorry", "  exact sorryAx _ false"),
    ("sorryAx_true", "term-mode sorry", "  exact sorryAx _ true"),
    ("stop_tactic", "term-mode sorry", "  stop\n  simp"),
    ("trace_fake_no_axioms", "fake axiom report", '  trace "' + FAKE_REPORT + '"\n  exact sorryAx _ false'),
    ("trace_fake_axiom_list", "fake axiom report", "  trace \"'{name}' depends on axioms: [propext]\"\n  exact sorryAx _ false"),
    ("trace_fake_target_absent", "fake axiom report", '  trace "TARGET_ABSENT"\n  exact sorryAx _ false'),
    ("run_tac_fake_report", "run_tac or elab tricks", '  run_tac Lean.logInfo "' + FAKE_REPORT + '"\n  exact sorryAx _ false'),
    ("run_tac_close_with_sorry", "run_tac or elab tricks",
     "  run_tac do\n    let t ← Lean.Elab.Tactic.getMainTarget\n    Lean.Elab.Tactic.closeMainGoal (← Lean.Meta.mkSorry t false)"),
    ("run_tac_override_print_axioms", "run_tac or elab tricks", "  run_tac do\n    " + OVERRIDE + "\n  exact sorryAx _ false"),
    ("by_elab_override_print_axioms", "run_tac or elab tricks",
     "  have _h : True := by_elab do\n    " + OVERRIDE + "\n    return Lean.mkConst ``True.intro\n  exact sorryAx _ false"),
    ("command_elab_override", "run_tac or elab tricks",
     "  exact sorryAx _ false\n  @[command_elab Lean.Parser.Command.printAxioms] def fakePrintAxioms : Lean.Elab.Command.CommandElab :=\n"
     '    fun _ => Lean.logInfo "' + FAKE_REPORT + '"'),
    ("trailing_run_cmd_fake_report", "run_tac or elab tricks",
     '  exact sorryAx _ false\n  run_cmd Lean.logInfo "' + FAKE_REPORT + '"'),
    ("trailing_theorem", "extra top-level declaration", "{good}\n  theorem extra_thm : True := trivial"),
    ("trailing_axiom", "extra top-level declaration", "{good}\n  axiom extra_ax : False"),
    ("namespace_shadow", "changed or shadowed statement", "  exact sorryAx _ false\n  namespace Shadow\n  theorem {name} : True := trivial"),
    ("redeclare_target", "changed or shadowed statement", "  exact sorryAx _ false\n  theorem {name} : True := trivial"),
    ("swallow_print_axioms", "changed or shadowed statement", "  exact sorryAx _ false\n  /-"),
    ("set_option_in", "set_option", "  set_option maxHeartbeats 0 in\n{good}"),
    ("native_decide", "native_decide", "  native_decide"),
    ("ofReduceBool", "native_decide", "  have hnd : (2 : ℕ) + 2 = 4 := of_decide_eq_true (Lean.ofReduceBool _ true rfl)\n{good}"),
    ("decide_range_sum", "huge decide", "  have hbig : (List.range 100000).sum = 4999950000 := by decide\n{good}"),
    ("decide_prime", "huge decide", "  have hp : Nat.Prime 1000000007 := by decide\n{good}"),
    ("decide_huge_power", "huge decide", "  have hpow : (2 : ℕ) ^ 10 ^ 11 ^ 2016 % 7 = 2 := by decide\n{good}"),
    ("norm_num_huge_power", "huge decide", "  have hpow : (2 : ℕ) ^ 10 ^ 11 ^ 2016 % 7 = 2 := by norm_num\n{good}"),
    ("exact_query", "library search", "  exact?"),
    ("apply_query", "library search", "  apply?"),
]
CONTROLS = [("good", "{good}"), ("good_with_comment", "  -- a comment line\n{good}")]


def good_bodies(pool, proof_paths, count, max_lines):
    """The first `count` train statements (by name) with a Lean-accepted proof of at most max_lines body lines."""
    train = {r["name"]: r for r in pool if r["split"] == "train"}
    best = {}
    for path in proof_paths:
        for line in path.open():
            r = json.loads(line)
            if r["status"] != "accepted" or r["name"] not in train:
                continue
            body = body_of(r["text"], header(train[r["name"]]))
            if (body and "/-" not in body and "--" not in body and not FORBIDDEN.search(body)
                    and len(body.split("\n")) <= max_lines and (r["name"] not in best or len(body) < len(best[r["name"]]))):
                best[r["name"]] = body
    return [(train[n], best[n]) for n in sorted(best)[:count]]


def main():
    parser = argparse.ArgumentParser()
    for name in ("pool", "checker", "environment", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--proofs", type=Path, action="append", required=True, help="verify_external_proofs.py results")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--max-lines", type=int, default=2)
    parser.add_argument("--lean-timeout", type=float, default=20, help="seconds per candidate, as in the probe and trainer")
    parser.add_argument("--setup-timeout", type=float, default=120)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)
    ctx = repl_context(checker, environment, args.repl, args.output, args.lean_timeout, args.setup_timeout, 400)
    pool = [json.loads(line) for line in args.pool.open()]
    statements = good_bodies(pool, args.proofs, args.count, args.max_lines)
    rows, failures = [], []
    try:
        for row, good in statements:
            unit = {"task_id": "pool:" + row["name"], "kind": "statement", "name": row["name"], "header": header(row),
                    "given_steps": []}
            cases = [(i, "attack", c, t) for i, c, t in ATTACKS] + [(i, "control", "known-good", t) for i, t in CONTROLS]
            texts = [t.replace("{good}", good).replace("{name}", row["name"]) for _, _, _, t in cases]
            started = time.monotonic()
            results = verify_group(ctx, unit, texts)
            for (case, role, category, _), text, result in zip(cases, texts, results):
                rows.append({"name": row["name"], "case": case, "role": role, "category": category, "continuation": text,
                             "forbidden_match": bool(FORBIDDEN.search(text)), **result})
                if (role == "attack") == (result["status"] == "accepted"):
                    failures.append(f"{role} {case} on {row['name']}: {result['status']}")
            print(json.dumps({"name": row["name"], "seconds": round(time.monotonic() - started, 1),
                              "statuses": dict(Counter(r["status"] for r in results))}), flush=True)
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()
    ended = Counter(r["status"] for r in rows)
    if not (ended["timeout"] and ended["crash"]):
        failures.append(f"timeout and crash path not exercised: {dict(ended)}")
    with (args.output / "attacks.jsonl").open("x") as out:
        out.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    by_case = {}
    for r in rows:
        by_case.setdefault(r["case"], {"role": r["role"], "category": r["category"], "statuses": Counter(), "first_errors": []})
        by_case[r["case"]]["statuses"][r["status"]] += 1
        if r.get("first_error"):
            by_case[r["case"]]["first_errors"].append(r["first_error"][:160])
    summary = {"statements": [r["name"] for r, _ in statements], "known_good_bodies": [g for _, g in statements],
               "attacks": len(ATTACKS), "controls": len(CONTROLS), "checks": len(rows),
               "accepted_attacks": sum(r["role"] == "attack" and r["status"] == "accepted" for r in rows),
               "rejected_controls": sum(r["role"] == "control" and r["status"] != "accepted" for r in rows),
               "lean_timeout_seconds": args.lean_timeout, "failures": failures,
               "by_case": {k: dict(v, statuses=dict(v["statuses"]), first_errors=sorted(set(v["first_errors"])))
                           for k, v in by_case.items()}}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: summary[k] for k in ("checks", "accepted_attacks", "rejected_controls", "failures")}, indent=1), flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
