"""Integration test: score the same candidates through the probe, map, and trainer paths and require equal verdicts.

Needs the pinned Lean environment, the REPL, and the run directories on cpu-angad (Lean test group; not part of
unittest discovery). Exit code 1 on any failed requirement.

Pool statements (probe_nt_pool.py vs run_lean_grpo.py):
- Candidates: model and single-tactic candidates of earlier probe runs (--probe-dir), all candidates whose old
  batch-file verdict may come from a neighbour (first error "expected '{' or indented tactic sequence" or
  "unexpected token 'theorem'", or no error and no axioms), plus controls: known-good bodies (nt_sft_v1, Lean-accepted
  external proofs), the same bodies with a tactic after the proof, `exact sorryAx _ false`, and truncated
  candidates placed before a known-good body of the same statement.
- Paths: P1 probe check_candidates with 1 worker; P2 the same with --workers threads; P3 the trainer's `score` on
  completions that still carry the closing fence and trailing text; P4 check_batch (batch files of 50, with the
  sentinel) as a reference. Requirements: P1 = P2 = P3 on every status; known-good accepted and wrong rejected in
  P1; in P4, wrong rejected, and known-good accepted unless an unterminated block comment comes earlier in the same
  file (the documented limit of batch files). Reported: P4 and the old batch verdicts against P1.
Bank units (pass_rate_map.py vs run_lean_grpo.py): groups of --map-samples with any accepted sample plus a random
sample of others. B1 verify_group per group (the map); B2 the trainer's `score`. Requirement: B1 = B2. Reported:
B1 against the old subprocess verdicts in the samples file.

Memory: up to --workers pool REPLs at once (each loads all of mathlib, 3.7 GB peak for one lean process), one
batch-file lean process at other times, bank REPLs (1.9 GiB peak, bench_v1), and the bank train rows. Run under a
cap of 4.5 GiB x --workers + 4 GiB.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import sys
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval", "train")]
from bench_lean_verifier import repl_context, verify_group  # noqa: E402
from prepare_grpo_pilot import load_checker  # noqa: E402
from probe_nt_pool import check_candidates, header, read  # noqa: E402
from replay_training_proofs import load  # noqa: E402
from run_lean_grpo import score  # noqa: E402
from verify_external_proofs import check_batch  # noqa: E402

NEIGHBOUR_ERRORS = ("expected '{' or indented tactic sequence", "unexpected token 'theorem'")
CULPRITS = {"ends_with_by": "  have h : 0 = 0 := by", "dangling_bullet": "  constructor\n  ·",
            "open_paren": "  refine dvd_trans ?_ (", "open_block_comment": "  /- reasoning that never closes\n  simp"}


def lines(path):
    """JSON rows of a file that another job may still append to: an incomplete last line is skipped."""
    out = []
    with path.open() as input_stream:
        for line in input_stream:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def pool_candidates(args, pool, rng):
    with args.sft.open() as input_stream:
        sft = [json.loads(line) for line in input_stream]
    picked = (rng.sample([r for r in sft if r["has_comments"]], args.good // 2)
              + rng.sample([r for r in sft if not r["has_comments"]], args.good // 2))
    items = []
    for r in picked:
        stmt = header(pool[r["theorem"]])
        text = r["messages"][1]["content"]
        assert text.startswith("```lean\n" + stmt + " := by\n") and text.endswith("\n```"), r["theorem"]
        body = text[len("```lean\n" + stmt + " := by\n"):-len("\n```")]
        items.append(("good", r["theorem"], body))
        items.append(("wrong_extra_tactic", r["theorem"], body + "\n  exact False.elim (by assumption)"))
    for k, r in enumerate(picked[:args.good // 2]):
        items.append(("wrong_sorryAx", r["theorem"], "  exact sorryAx _ false"))
        label = list(CULPRITS)[k % len(CULPRITS)]
        items.append(("culprit_" + label, r["theorem"], CULPRITS[label]))
        good = next(body for kind, name, body in items if kind == "good" and name == r["theorem"])
        items.append(("good_after_culprit", r["theorem"], good))
    old = {}
    for d in args.probe_dir:
        for c in lines(d / "checked.jsonl"):
            old[c["arm"], c["name"], c["sample"]] = c
    candidates = [c for d in args.probe_dir for c in lines(d / "candidates.jsonl") if (c["arm"], c["name"], c["sample"]) in old]
    def suspect(r):  # a rejection that a neighbour in the batch file may have caused
        error = r.get("first_error") or ""
        return r["status"] in ("rejected", "sorry_only") and (error.startswith(NEIGHBOUR_ERRORS) or not error and r.get("axioms") is None)
    suspects = {(c["arm"], c["name"]) for c in candidates if suspect(old[c["arm"], c["name"], c["sample"]])}
    solved = {(c["arm"], c["name"]) for c in candidates if old[c["arm"], c["name"], c["sample"]]["status"] == "accepted"}
    others = sorted({(c["arm"], c["name"]) for c in candidates} - suspects - solved)
    chosen = suspects | solved | set(rng.sample(others, min(args.groups, len(others))))
    for c in candidates:
        if (c["arm"], c["name"]) in chosen:
            items.append(("old_" + c["arm"], c["name"], c["continuation"]))
    return items, old, candidates, chosen


def main():
    parser = argparse.ArgumentParser()
    for name in ("pool", "sft", "environment", "checker", "repl", "output", "bank"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--probe-dir", type=Path, action="append", required=True)
    parser.add_argument("--map-samples", type=Path, action="append", required=True)
    parser.add_argument("--train", type=Path, action="append", required=True)
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--good", type=int, default=40)
    parser.add_argument("--groups", type=int, default=40, help="extra random probe groups")
    parser.add_argument("--bank-groups", type=int, default=20, help="extra random map groups")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--seed", type=int, default=20261010)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(args.seed)
    environment = args.environment.resolve()
    checker = load_checker(args.checker, environment, args.output)
    with args.pool.open() as input_stream:
        pool = {r["name"]: r for r in map(json.loads, input_stream)}
    items, old, candidates, chosen = pool_candidates(args, pool, rng)
    report, failures, seconds = {}, [], {}

    # P1, P2: the probe's checking stage, from a candidates file as the probe writes it.
    verdicts = {}
    for label, workers in (("P1", 1), ("P2", args.workers)):
        out = args.output / label
        out.mkdir()
        with (out / "candidates.jsonl").open("x") as f:
            for i, (kind, name, text) in enumerate(items):
                f.write(json.dumps({"arm": kind, "name": name, "sample": i, "continuation": text}, ensure_ascii=False) + "\n")
        ctx = repl_context(checker, environment, args.repl, out, args.timeout, 120, 400)
        start = time.monotonic()
        check_candidates(ctx, pool, out, workers)
        seconds[label] = time.monotonic() - start
        verdicts[label] = {c["sample"]: c for c in read(out / "checked.jsonl")}
    # P3: the trainer's reward path on completions that still carry the fence and trailing text.
    out = args.output / "P3"
    out.mkdir()
    ctx = repl_context(checker, environment, args.repl, out, args.timeout, 120, 400)
    by_id = {"pool:" + n: {"task_id": "pool:" + n, "kind": "statement", "name": n, "header": header(pool[n]), "given_steps": []}
             for n in {name for _, name, _ in items}}
    start = time.monotonic()
    with ThreadPoolExecutor(args.workers) as executor:
        _, _, results = score(ctx, by_id, ["pool:" + name for _, name, _ in items],
                              [text + "\n```\nThe proof is complete." for _, _, text in items], executor)
    for repl in list(ctx.pool_repls):
        repl.close()
    seconds["P3"] = time.monotonic() - start
    verdicts["P3"] = dict(enumerate(results))
    # P4: batch files of 50 with the sentinel (reference).
    out = args.output / "P4"
    out.mkdir()
    lean = environment / "lean-4.10.0-rc1-linux/bin/lean"
    p4, start = [], time.monotonic()
    for b in range(0, len(items), 50):
        batch = [{"text": header(pool[name]) + " := by\n" + text.rstrip()} for _, name, text in items[b:b + 50]]
        p4 += check_batch(lean, ctx.repl_env, out / f"batch_{b // 50}.lean", batch, 1800)[0]
    seconds["P4"] = time.monotonic() - start
    verdicts["P4"] = dict(enumerate(p4))

    acc = lambda r: r["status"] == "accepted"
    rows = []
    for i, (kind, name, text) in enumerate(items):
        row = {"i": i, "kind": kind, "name": name, **{p: verdicts[p][i]["status"] for p in ("P1", "P2", "P3", "P4")},
               "P1_error": verdicts["P1"][i].get("first_error"), "P4_error": verdicts["P4"][i].get("first_error")}
        rows.append(row)
    same = sum(r["P1"] == r["P2"] == r["P3"] for r in rows)
    if same != len(rows):
        failures.append(f"P1/P2/P3 statuses differ on {len(rows) - same} of {len(rows)}")
    controls = {}
    for r in rows:  # P4 only: an unterminated block comment earlier in the same batch file swallows this item
        r["after_open_comment_in_file"] = any(x["kind"] == "culprit_open_block_comment" and x["i"] // 50 == r["i"] // 50
                                              for x in rows[:r["i"]])
    for kind in ("good", "good_after_culprit", "wrong_extra_tactic", "wrong_sorryAx") + tuple("culprit_" + c for c in CULPRITS):
        sub = [r for r in rows if r["kind"] == kind]
        clean = [r for r in sub if not r["after_open_comment_in_file"]]
        controls[kind] = {"P1": f"{sum(r['P1'] == 'accepted' for r in sub)}/{len(sub)} accepted",
                          "P4": f"{sum(r['P4'] == 'accepted' for r in clean)}/{len(clean)} accepted in files without an "
                                f"earlier open comment; {sum(r['P4'] == 'accepted' for r in sub)}/{len(sub)} overall"}
        want = kind.startswith("good")
        if any((r["P1"] == "accepted") != want for r in sub):
            failures.append(f"P1 control {kind}: {controls[kind]['P1']}")
        if want and any(r["P4"] != "accepted" for r in clean) or not want and any(r["P4"] == "accepted" for r in sub):
            failures.append(f"P4 control {kind}: {controls[kind]['P4']}")
    model_rows = [r for r in rows if r["kind"].startswith("old_")]
    old_status = {}
    for i, (kind, name, text) in enumerate(items):
        if kind.startswith("old_"):
            arm = kind[4:]
            matches = [c for c in candidates if c["arm"] == arm and c["name"] == name and c["continuation"] == text]
            old_status[i] = old[arm, name, matches[0]["sample"]]
    flips = [{"name": r["name"], "arm": r["kind"][4:], "old": old_status[r["i"]]["status"],
              "old_error": (old_status[r["i"]].get("first_error") or "")[:80], "new": r["P1"]}
             for r in model_rows if acc({"status": r["P1"]}) != (old_status[r["i"]]["status"] == "accepted")]
    report["pool"] = {
        "items": len(rows), "by_kind": dict(Counter(r["kind"] for r in rows)), "groups_from_probe_runs": len(chosen),
        "P1_P2_P3_equal": f"{same}/{len(rows)}",
        "P4_vs_P1_accept_equal": f"{sum(acc({'status': r['P4']}) == acc({'status': r['P1']}) for r in rows)}/{len(rows)}",
        "P4_vs_P1_disagreements": [{k: r[k] for k in ("kind", "name", "P1", "P4", "P4_error")} for r in rows
                                   if acc({"status": r["P4"]}) != acc({"status": r["P1"]})][:30],
        "controls": controls, "old_batch_vs_P1_flips": flips,
        "old_batch_accepted_model_rows": sum(old_status[r["i"]]["status"] == "accepted" for r in model_rows),
        "P1_accepted_model_rows": sum(r["P1"] == "accepted" for r in model_rows), "model_rows": len(model_rows),
        "P1_statuses": dict(Counter(r["P1"] for r in rows)),
        "lean_seconds_P1": sum(v.get("lean_seconds", 0.0) for v in verdicts["P1"].values()),
        "lean_cpu_seconds_P1": sum(v.get("lean_cpu_seconds", 0.0) for v in verdicts["P1"].values()),
        "lean_calls_P1": sum(v.get("lean_calls", 0) for v in verdicts["P1"].values()), "wall_seconds": seconds}

    # Bank units: the map's call against the trainer's call, on the same continuations.
    samples = [s for path in args.map_samples for s in lines(path)]
    by_task = defaultdict(list)
    for s in samples:
        by_task[s["task_id"]].append(s)
    complete = {t: g for t, g in by_task.items() if len(g) == max(len(x) for x in by_task.values())}
    hit = sorted(t for t, g in complete.items() if any(s["status"] == "accepted" for s in g))
    rest = sorted(set(complete) - set(hit))
    tasks = hit + rng.sample(rest, min(args.bank_groups, len(rest)))
    with args.bank.open() as input_stream:
        bank = {u["task_id"]: dict(u, kind="bank") for u in map(json.loads, input_stream) if u["task_id"] in set(tasks)}
    out = args.output / "bank"
    out.mkdir()
    ctx = repl_context(checker, environment, args.repl, out, args.timeout, 120, 400)
    theorems = {u["theorem_id"] for u in bank.values()}
    ctx.rows = {r["theorem_id"]: r for path in args.train for r in load(path) if r["theorem_id"] in theorems}
    for path in args.eval:
        with zipfile.ZipFile(path) as archive:
            with archive.open(archive.namelist()[0]) as input_stream:
                for line in input_stream:
                    r = json.loads(line)
                    ctx.eval_spans.setdefault(r["file_path"], []).append((r["start"][0], r["end"][0], r["declaration_header"]))
    b1 = {t: verify_group(ctx, bank[t], [s["continuation"] for s in complete[t]]) for t in tasks}
    ids = [t for t in tasks for _ in complete[t]]
    with ThreadPoolExecutor(2) as executor:
        _, _, b2 = score(ctx, bank, ids, [s["continuation"] + "```" for t in tasks for s in complete[t]], executor)
    b1_flat = [r for t in tasks for r in b1[t]]
    b0 = [s for t in tasks for s in complete[t]]
    bank_same = sum(x["status"] == y["status"] for x, y in zip(b1_flat, b2))
    if bank_same != len(b2):
        failures.append(f"B1/B2 statuses differ on {len(b2) - bank_same} of {len(b2)}")
    report["bank"] = {
        "groups": len(tasks), "groups_with_old_accept": len(hit), "samples": len(b2), "B1_B2_equal": f"{bank_same}/{len(b2)}",
        "B1_accepted": sum(acc(r) for r in b1_flat), "B0_old_subprocess_accepted": sum(acc(s) for s in b0),
        "B1_vs_B0_accept_equal": f"{sum(acc(x) == acc(y) for x, y in zip(b1_flat, b0))}/{len(b0)}",
        "B1_vs_B0_disagreements": [{"task_id": s["task_id"], "old": s["status"], "new": r["status"], "new_error": r.get("first_error")}
                                   for r, s in zip(b1_flat, b0) if acc(r) != acc(s)][:20],
        "B1_statuses": dict(Counter(r["status"] for r in b1_flat))}
    report["failures"] = failures
    with (args.output / "rows.jsonl").open("x") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    (args.output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
