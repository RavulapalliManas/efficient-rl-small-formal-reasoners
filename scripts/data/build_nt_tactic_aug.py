"""Tactic-level augmentation: new Lean-checked proofs from the accepted SFT proofs of sft_problems (train split only).

Input: train.jsonl of build_nt_sft_data.py (nt_sft_v5 holds only nt_splits_v3/sft_problems.txt). Each row's proof
body is the text after the pool statement's `:= by` (body_of of build_nt_sft_data.py) without comments
(strip_comments), so every cut falls between tactic lines. Cut k (0 <= k < n top-level steps, top_level_steps of
build_proof_tasks.py) gives the first k steps; a given text that ends in `<;>` is skipped, and each distinct
(problem, given) is checked once. At each cut:
1. state: state_at of build_nt_hole_tasks.py (trace_state, then `all_goals sorry`, in the REPL); a cut with an error
   or no goal gets no tactic checks;
2. each tactic of TACTICS alone, at the step indentation, as the continuation of verify_group of
   bench_lean_verifier.py (the RL reward path: pool header + statement + " := by\n" + given + tactic; accepted when
   Lean reports no error, so the tactic closes every goal, and the axioms are within the allowed set).
An accepted check is a new proof, given + tactic. A proof whose text equals a comment-free source body of the same
problem is not new and is not written. `norm_num [Nat.dvd_iff_mod_eq_zero]` is dropped when `#check` of the lemma
fails at the pin. Leakage: every row must name an sft problem in no held-out list (else the run stops), and a problem
whose statement has a check_leakage.py hit gets no Lean checks.
Budget: --max-checks Lean commands, planned as 1 + len(TACTICS) per cut; cuts are taken in source-row order until the
next one would pass the budget. REPL setups are not in the budget.
Output (a new directory): cuts.jsonl (every checked cut: state or reason, status per tactic), proofs.jsonl (name, k,
tactic, text = the new proof body, source_row = 0-based line of train.jsonl, state), summary.json.

Memory: <100 MiB of rows + one REPL per worker (budget 2 GiB each, as in build_nt_hole_tasks.py): 8 workers need about
16.5 GiB; run under a 20 GiB cap.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval", "audit")]
import bench_lean_verifier  # noqa: E402
from bench_lean_verifier import repl_context, verify_group  # noqa: E402
import build_nt_hole_tasks  # noqa: E402
from build_nt_hole_tasks import drop, in_parallel, run_command, state_at  # noqa: E402
from build_nt_sft_data import body_of, strip_comments  # noqa: E402
from build_proof_tasks import top_level_steps  # noqa: E402
from check_leakage import Leakage  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import header  # noqa: E402
from probe_start_depth import FORBIDDEN  # noqa: E402
from profile_external_proofs import statement  # noqa: E402

TACTICS = ["rfl", "norm_num", "simp", "omega", "linarith", "nlinarith", "positivity", "aesop", "simp_all", "tauto",
           "ring_nf", "decide", "norm_num [Nat.dvd_iff_mod_eq_zero]", "push_cast", "field_simp"]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    for name in ("sft", "pool", "splits", "minif2f", "environment", "checker", "repl", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True, help="LeanDojo validation or test zip")
    parser.add_argument("--max-checks", type=int, default=40000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--lean-timeout", type=float, default=20)
    parser.add_argument("--setup-timeout", type=float, default=120)
    parser.add_argument("--repl-restart-after", type=int, default=400)
    parser.add_argument("--jaccard", type=float, default=0.85)
    parser.add_argument("--minif2f-jaccard", type=float, default=0.7)
    args = parser.parse_args()
    started = time.monotonic()
    with args.pool.open() as input_stream:
        pool = {r["name"]: r for r in map(json.loads, input_stream) if r["split"] == "train"}
    leak = Leakage(args.pool, args.splits, args.eval, args.minif2f, args.jaccard, args.minif2f_jaccard)

    # Comment-free source bodies, one per distinct (problem, body).
    counts, hits, bodies = Counter(), {}, {}
    with args.sft.open() as input_stream:
        for i, line in enumerate(input_stream):
            r = json.loads(line)
            name = r["theorem"]
            counts["rows"] += 1
            if name not in leak.sft_problems or name in leak.held_out_names or name not in pool:
                raise RuntimeError(f"row {i}: {name} is not a train-only sft problem")
            if name not in hits:
                hits[name] = leak.hits(statement(pool[name]["formal_statement"]))
            if hits[name]:
                counts["rows_dropped_leakage"] += 1
                continue
            code = r["messages"][1]["content"].removeprefix("```lean\n").removesuffix("\n```")
            body = body_of(code, header(pool[name]))
            body = strip_comments(body) if body else None
            if body is None or FORBIDDEN.search(body) or any(x.strip() and not x[0].isspace() for x in body.split("\n")):
                counts["rows_skipped_body"] += 1
            elif (name, body) in bodies:
                counts["rows_duplicate_after_strip"] += 1
            else:
                bodies[name, body] = i
    counts["source_bodies"] = len(bodies)

    cuts, seen = [], set()
    for (name, body), row in bodies.items():
        steps, pad = top_level_steps(body)
        for k in range(len(steps)):
            given = "\n".join(steps[:k]) + "\n" if k else ""
            if given.rstrip().endswith("<;>"):
                counts["cuts_skipped_combinator"] += 1
            elif (name, given) in seen:
                counts["cuts_duplicate"] += 1
            else:
                seen.add((name, given))
                cuts.append({"name": name, "k": k, "source_row": row, "steps_source": len(steps), "given": given, "pad": pad})
    counts["cuts"] = len(cuts)

    args.output.mkdir(parents=True, exist_ok=False)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)
    ctx = repl_context(checker, environment, args.repl, args.output, args.lean_timeout, args.setup_timeout, args.repl_restart_after)
    lemma_check, _, failure = run_command(ctx, "#check @Nat.dvd_iff_mod_eq_zero\n")
    lemma_ok = failure is None and "env" in lemma_check and not any(m["severity"] == "error" for m in lemma_check.get("messages", []))
    if ctx.local.__dict__.get("repl") is not None:
        drop(ctx, ctx.local.__dict__)  # the main thread's REPL; each worker opens its own
    tactics = [t for t in TACTICS if lemma_ok or "Nat.dvd_iff_mod_eq_zero" not in t]
    planned = cuts[:args.max_checks // (1 + len(tactics))]
    counts["cuts_over_budget"] = len(cuts) - len(planned)

    def run(cut):
        hdr = header(pool[cut["name"]])
        state, reason, wall = state_at(ctx, hdr, cut["given"], [len(cut["pad"])])
        if state is None:
            return state, reason, wall, []
        unit = {"task_id": f"{cut['name']}:aug:{cut['k']}", "kind": "statement", "name": cut["name"], "header": hdr,
                "given": cut["given"], "given_steps": []}
        return state, None, wall, verify_group(ctx, unit, [cut["pad"] + t for t in tactics])

    accepted, timeouts, new = Counter(), Counter(), {}
    lean = {"state_commands": 0, "tactic_checks": 0, "verify_group_lean_calls": 0, "lean_seconds": 0.0}
    try:
        with (args.output / "cuts.jsonl").open("x") as cut_out, (args.output / "proofs.jsonl").open("x") as proof_out:
            for cut, (state, reason, wall, results) in in_parallel(args.workers, run, planned):
                lean["state_commands"] += 1
                lean["tactic_checks"] += sum(x["status"] in ("accepted", "rejected", "timeout", "crash") for x in results)
                lean["verify_group_lean_calls"] += sum(x["lean_calls"] for x in results)
                lean["lean_seconds"] += wall + sum(x["lean_seconds"] for x in results)
                statuses = {t: x["status"] for t, x in zip(tactics, results)}
                cut_out.write(json.dumps({k: cut[k] for k in ("name", "k", "source_row", "steps_source")} |
                                         {"state": state, "reason": reason, "statuses": statuses}, ensure_ascii=False) + "\n")
                for tactic, status in statuses.items():
                    timeouts[tactic] += status == "timeout"
                    if status != "accepted":
                        continue
                    text = cut["given"] + cut["pad"] + tactic
                    if (cut["name"], text) in bodies:
                        counts["accepted_same_as_source"] += 1
                        continue
                    accepted[tactic] += 1
                    new[cut["name"], cut["given"]] = new.get((cut["name"], cut["given"]), 0) + 1
                    proof_out.write(json.dumps({"name": cut["name"], "k": cut["k"], "tactic": tactic, "text": text,
                                                "source_row": cut["source_row"], "steps_source": cut["steps_source"],
                                                "steps_new": cut["k"] + 1, "state": state}, ensure_ascii=False) + "\n")
                cut_out.flush()
                proof_out.flush()
    finally:
        for repl in list(ctx.pool_repls):
            repl.close()

    with (args.output / "cuts.jsonl").open() as input_stream:
        rows = [json.loads(line) for line in input_stream]
    with (args.output / "proofs.jsonl").open() as input_stream:
        proofs = [json.loads(line) for line in input_stream]
    done = {(c["name"], c["given"]) for c in planned}
    per_body = []
    for (name, body), _ in bodies.items():
        steps, _ = top_level_steps(body)
        keys = {(name, "\n".join(steps[:k]) + "\n" if k else "") for k in range(len(steps))} & done
        if keys:
            per_body.append(sum(new.get(key, 0) for key in keys))
    bucket = lambda v: str(v) if v <= 5 else "6-10" if v <= 10 else "11-20" if v <= 20 else "21+"
    summary = {
        "counts": dict(counts), "tactics": tactics, "nat_dvd_iff_mod_eq_zero_check_ok": lemma_ok,
        "leakage_targets": leak.counts(), "problems": len(hits), "problems_with_leakage_hit": sum(bool(h) for h in hits.values()),
        "leakage_hits": {n: h for n, h in hits.items() if h},
        "cuts_checked": len(rows), "cut_reasons": dict(Counter(str(r["reason"]).split(":")[0] for r in rows)),
        "cuts_with_new_proof": sum(any(s == "accepted" for s in r["statuses"].values()) for r in rows),
        "new_proofs": len(proofs), "new_proofs_shorter_than_source": sum(p["steps_new"] < p["steps_source"] for p in proofs),
        "new_proofs_whole_statement_k0": sum(p["k"] == 0 for p in proofs),
        "problems_with_new_proof": len({p["name"] for p in proofs}),
        "accepted_by_tactic": dict(accepted.most_common()), "timeouts_by_tactic": dict(timeouts),
        "new_proofs_per_source_body": {"n": len(per_body), "mean": round(sum(per_body) / max(len(per_body), 1), 3),
                                       "zero": sum(v == 0 for v in per_body),
                                       "histogram": dict(sorted(Counter(map(bucket, per_body)).items()))},
        "lean": dict(lean, wall_seconds=time.monotonic() - started, workers=args.workers, lean_timeout=args.lean_timeout,
                     max_checks=args.max_checks),
        "inputs": {f"{p.parent.name}/{p.name}": sha256(p) for p in (args.sft, args.pool, args.minif2f, *args.eval,
                                                *(args.splits / f"{f}.txt" for f in ("sft_problems", "heldout_proof_problems", "dev", "excluded_from_training")))},
        "code": {Path(f).name: sha256(f) for f in (bench_lean_verifier.__file__, build_nt_hole_tasks.__file__, __file__)},
        "lean_version": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}
    with (args.output / "summary.json").open("x") as out:
        out.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=1, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
