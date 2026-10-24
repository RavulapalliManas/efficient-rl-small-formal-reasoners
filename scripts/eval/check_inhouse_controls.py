"""Lean integration controls for build_nt_tactic_aug.py, build_nt_knowledge.py, and build_nt_archive.py (not unittest).

1. Augmentation path (state_at + verify_group with given): for the first --proofs multi-step SFT bodies whose cut n-1
   does not end in `<;>`, the state exists, the source's last step is accepted, and `skip` and `done` are rejected. A
   cut whose given text ends in `<;>` goes to observations with its verdicts: the continuation binds to the combinator,
   so `skip` can pass there, which is why build_nt_tactic_aug.py skips such cuts.
2. Knowledge replay (replay_file): the parsed items of --knowledge-file are accepted; with the second item changed to
   a `sorry` proof, a false proof, an extra command after it, a wrong name for `#print axioms`, or the text of the
   first item (already declared), it is rejected.
3. Archive compile (compile_file) on a scratch root: a correct theorem is verified; a `sorry` theorem builds but is not
   verified; a file with an error does not build.
Exit 1 when any expectation fails. Output: --output/controls.json (a new directory).

Memory: one REPL or lean process at a time (<4 GiB with shared olean pages).
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval", "audit")]
from bench_lean_verifier import repl_context, verify_group  # noqa: E402
from build_nt_archive import compile_file  # noqa: E402
from build_nt_hole_tasks import state_at  # noqa: E402
from build_nt_knowledge import declarations, replay_file  # noqa: E402
from build_nt_sft_data import body_of, strip_comments  # noqa: E402
from build_proof_tasks import top_level_steps  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import header  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    for name in ("sft", "pool", "environment", "checker", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--knowledge-file", default="Mathlib/NumberTheory/LegendreSymbol/Basic.lean")
    parser.add_argument("--proofs", type=int, default=3)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)
    ctx = repl_context(checker, environment, args.repl, args.output, 20, 900, 400)
    with args.pool.open() as input_stream:
        pool = {r["name"]: r for r in map(json.loads, input_stream)}
    results, failures, observations = [], [], []

    def expect(label, got, want):
        results.append({"control": label, "got": got, "want": want})
        if got != want:
            failures.append(label)

    done = 0
    with args.sft.open() as input_stream:
        for line in input_stream:
            r = json.loads(line)
            code = r["messages"][1]["content"].removeprefix("```lean\n").removesuffix("\n```")
            body = strip_comments(body_of(code, header(pool[r["theorem"]])))
            steps, pad = top_level_steps(body)
            if len(steps) < 3 or "\n" in steps[-1]:
                continue
            given, hdr = "\n".join(steps[:-1]) + "\n", header(pool[r["theorem"]])
            state, reason, _ = state_at(ctx, hdr, given, [len(pad)])
            unit = {"task_id": "control", "kind": "statement", "name": r["theorem"], "header": hdr, "given": given, "given_steps": []}
            verdicts = [x["status"] for x in verify_group(ctx, unit, [steps[-1], pad + "skip", pad + "done"])]
            if given.rstrip().endswith("<;>"):  # build_nt_tactic_aug.py skips this cut
                observations.append({"proof": r["theorem"], "given_ends_in": "<;>", "state_reason": reason, "last step, skip, done": verdicts})
                continue
            expect(f"aug {r['theorem']} state at n-1", reason, None)
            expect(f"aug {r['theorem']} last step, skip, done", verdicts, ["accepted", "rejected", "rejected"])
            done += 1
            if done == args.proofs:
                break
    for repl in list(ctx.pool_repls):
        repl.close()

    mathlib = environment / "mathlib"
    source = (mathlib / args.knowledge_file).read_text()
    items = [d for d in declarations(source) if d["docstring"] and not d["wrapped"] and d["kind"] and not d["forbidden"]][:3]
    expect("knowledge items parsed", len(items), 3)
    expect("knowledge items as written", [x["status"] for x in replay_file(ctx, mathlib / args.knowledge_file, source, items)],
           ["accepted"] * 3)
    first, second = items[0], items[1]
    changes = {"sorry proof": dict(second, text=second["text"][:second["text"].rindex(second["proof"])] + "by\n  sorry"),
               "false proof": dict(second, text=second["text"][:second["text"].rindex(second["proof"])] + "by\n  exact absurd rfl id"),
               "extra command": dict(second, text=second["text"] + "\ntheorem control_extra : True := trivial"),
               "wrong name": dict(second, full_name=second["full_name"] + "_control"),
               "already declared": dict(second, text=first["text"], full_name=first["full_name"])}
    for label, changed in changes.items():
        verdicts = [x["status"] for x in replay_file(ctx, mathlib / args.knowledge_file, source, [first, changed])]
        expect(f"knowledge second item: {label}", verdicts, ["accepted", "rejected"])

    root = args.output / "archive_root"
    (root / "Archive").mkdir(parents=True)
    (args.output / "lean").mkdir()
    (root / "Archive/Good.lean").write_text("import Mathlib.Data.Nat.Defs\n\ntheorem control_good (n : ℕ) : n + 0 = n := rfl\n\n"
                                            "theorem control_sorry (n : ℕ) : n = n + 1 := sorry\n")
    (root / "Archive/Bad.lean").write_text("import Mathlib.Data.Nat.Defs\n\ntheorem control_bad (n : ℕ) : n = n + 1 := rfl\n")
    libs = [mathlib / ".lake/build/lib"] + sorted((mathlib / ".lake/packages").glob("*/.lake/build/lib"))
    env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    lean = environment / "lean-4.10.0-rc1-linux/bin/lean"
    record, decls = compile_file(lean, env, root, args.output, "Archive/Good.lean", 600)
    expect("archive good file builds; verified per theorem", [record["builds"]] + [d["verified"] for d in decls], [True, True, False])
    record, _ = compile_file(lean, env, root, args.output, "Archive/Bad.lean", 600)
    expect("archive file with an error builds", record["builds"], False)

    (args.output / "controls.json").write_text(json.dumps({"results": results, "failures": failures, "observations": observations}, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"controls": len(results), "failures": failures}, ensure_ascii=False), flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
