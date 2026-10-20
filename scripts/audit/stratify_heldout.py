"""Join the three held-out screens into one stratum per statement, and count solved statements per stratum and arm.

Inputs: the pool (--split rows), theorems.jsonl of false_hypothesis_sweep.py (--vacuous), theorems.jsonl of
automation_chain_screen.py (--automation) and rows.jsonl of train_similarity_strata.py (--similarity). Every held-out
statement must have a row in all three, else the run stops: a missing screen would silently fall through to `other`.
Strata, assigned in this fixed order (heldout_screens.stratum):
1. vacuous: the sweep proved False from the premises (status contradictory);
2. automation: at least one automation chain is accepted;
3. near_train: the copy baseline is accepted, or a TRAIN statement has the same construction signature, or the
   maximum TRAIN similarity is at least 0.5;
4. other.
A statement that the screens could not settle (timeouts, an undetermined sweep) falls to a later stratum; the output
keeps the screen fields so such rows stay visible.
--arm NAME=PATH (repeat) reads a steps.jsonl of run_tactic_rl.py: a statement is solved when any non-censored group
has a return of 1 (model-only returns; automation outcomes are separate fields). task_id maps to the pool name by
removing `dev:` and `:whole:N`. Statements absent from an arm's file are counted as not attempted, never as failed.

Output: --output/strata.jsonl (one row per statement) and summary.json (counts per stratum, per topic, and solved
statements per stratum per arm). No Lean, no model.

Memory: three screen files and the steps files, each < 100 MiB for <= 4,096 statements; run under 1 GiB.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit", "eval")]
from heldout_screens import STRATA, counts, load_pool, read, solved_by_arm, stratum, write_json  # noqa: E402


def index(rows, label, names):
    by_name = {r["name"]: r for r in rows}
    missing = sorted(names - by_name.keys())
    if missing:
        raise ValueError(f"{label} has no row for {len(missing)} held-out statements, for example {missing[0]}")
    return by_name


def join(pool, vacuous, automation, similarity):
    names = {r["name"] for r in pool}
    vac, auto, sim = (index(rows, label, names) for rows, label in
                      ((vacuous, "vacuous sweep"), (automation, "automation screen"), (similarity, "similarity screen")))
    out = []
    for r in pool:
        v, a, s = vac[r["name"]], auto[r["name"]], sim[r["name"]]
        out.append({"name": r["name"], "split": r["split"], "topic": r["topic"], "stratum": stratum(v, a, s),
                    "vacuous_status": v["status"], "automation_accepting_chains": a["accepting_chains"],
                    "automation_timeouts": sum(c["status"] == "timeout" for c in a["chains"]),
                    "copy_status": s.get("copy_status", "not_run"), "signature_match": bool(s["signature_matches"]),
                    "template_match": bool(s["template_matches"]), "max_similarity": s["max_similarity"],
                    "band": s["band"], "nearest_train": s["nearest"]})
    return out


def arm_report(rows, steps):
    """Solved and attempted statements per stratum for one arm's steps.jsonl rows."""
    solved, attempted, censored = solved_by_arm(steps)
    names = {r["name"] for r in rows}
    report = {"unmatched_task_names": len((solved | attempted | censored) - names),
              "censored_only": len(censored & names)}
    for s in STRATA:
        members = {r["name"] for r in rows if r["stratum"] == s}
        report[s] = {"statements": len(members), "attempted": len(members & attempted), "solved": len(members & solved)}
    report["all"] = {"statements": len(names), "attempted": len(names & attempted), "solved": len(names & solved)}
    return report


def summarize(rows, arms):
    by_stratum = Counter(r["stratum"] for r in rows)
    topics = counts(rows, "topic")
    return {"statements": len(rows), "strata_order": list(STRATA),
            "by_stratum": {s: by_stratum[s] for s in STRATA},
            "by_topic": {t: {s: sum(r["topic"] == t and r["stratum"] == s for r in rows) for s in STRATA} for t in topics},
            "vacuous_status": counts(rows, "vacuous_status"), "copy_status": counts(rows, "copy_status"),
            "by_band": counts(rows, "band"), "arms": arms}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for name in ("pool", "vacuous", "automation", "similarity", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--tasks", type=Path, help="tasks_dev.jsonl or similar, for the topic of each name")
    parser.add_argument("--split", default="validation")
    parser.add_argument("--arm", action="append", default=[], help="NAME=PATH to a steps.jsonl; repeat")
    args = parser.parse_args()
    pool = load_pool(args.pool, args.split, args.tasks)
    rows = join(pool, read(args.vacuous), read(args.automation), read(args.similarity))
    arms = {}
    for spec in args.arm:
        name, _, path = spec.partition("=")
        if not path or name in arms or not Path(path).is_file():
            parser.error(f"--arm needs a distinct NAME=PATH to an existing file, got {spec}")
        arms[name] = dict(arm_report(rows, read(Path(path))), steps=path)
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / "strata.jsonl").open("x") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    summary = summarize(rows, arms)
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
