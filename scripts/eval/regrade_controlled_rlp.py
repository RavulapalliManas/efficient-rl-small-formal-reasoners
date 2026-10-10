"""Blinded regrade of both arms of the controlled RLP study (Experiment 13); no model calls.

Subcommands:
  adjudication-packet  cases where reviewers A and B disagree on core_score or full_correct
  summarize            final grades per arm and phase, the arm contrast, and agreement
                       between these grades and the original ordered-arm grades
Final grade of a case: the A/B value when they agree, else the adjudicated value.
Inputs are <=1000 cases of <=64 KiB each; peak memory <100 MiB.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import statistics


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def aligned(packet, reviews_a, reviews_b):
    mapping = json.loads((packet / "private_map.json").read_text())
    result = {}
    for name, path in (("a", reviews_a), ("b", reviews_b)):
        data = rows(path)
        if {r["review_id"] for r in data} != set(mapping[name]) or len(data) != len(mapping[name]):
            raise ValueError("Review IDs do not match packet " + name)
        result[name] = {mapping[name][r["review_id"]]: r for r in data}
    return result


def adjudication_packet(args):
    grades = aligned(args.packet, args.reviews_a, args.reviews_b)
    source = {r["id"]: r for r in rows(args.source)}
    cases = [cid for cid in sorted(source) if any(grades["a"][cid][f] != grades["b"][cid][f] for f in ("core_score", "full_correct"))]
    random.Random(20261009).shuffle(cases)
    args.output.mkdir(parents=True, exist_ok=False)
    private = {}
    with (args.output / "adjudication_packet.jsonl").open("x") as out:
        for i, cid in enumerate(cases):
            rid = hashlib.sha256(f"adj:{i}:{cid}".encode()).hexdigest()[:16]
            private[rid] = cid
            keep = ("core_score", "full_correct", "ancillary_error", "error_category", "reason", "evidence")
            out.write(json.dumps({"adjudication_id": rid, **{k: source[cid][k] for k in ("prompt", "reference", "response")},
                                  "review_1": {k: grades["a"][cid][k] for k in keep},
                                  "review_2": {k: grades["b"][cid][k] for k in keep}}, ensure_ascii=False) + "\n")
    (args.output / "private_map.json").write_text(json.dumps(private, indent=2) + "\n")
    print(json.dumps({"cases": len(source), "disagreements": len(cases)}))


def final_grades(grades, adjudicated):
    final = {}
    for cid in grades["a"]:
        a, b = grades["a"][cid], grades["b"][cid]
        if cid in adjudicated:
            final[cid] = (adjudicated[cid]["core_score"], adjudicated[cid]["full_correct"])
        else:
            final[cid] = (a["core_score"] if a["core_score"] == b["core_score"] else None,
                          a["full_correct"] if a["full_correct"] == b["full_correct"] else None)
    return final


def old_ordered_grades(root):
    """Final grades of the original ordered-arm review, by the same rule (stage::item keys)."""
    reviews = json.loads((root / "recount_v2" / "aligned_reviews.json").read_text())
    private = json.loads((root / "adjudication_v2_packet" / "private_map.json").read_text())
    adjudicated = {private[r["review_id"]]: r for r in rows(root / "adjudication_v2_raw.jsonl")}
    return final_grades(reviews, adjudicated)


def summarize(args):
    grades = aligned(args.packet, args.reviews_a, args.reviews_b)
    private = json.loads((args.adjudication / "private_map.json").read_text())
    adjudicated = {private[r["adjudication_id"]]: r for r in rows(args.adjudication / "adjudication_raw.jsonl")}
    final = final_grades(grades, adjudicated)
    cases = {r["id"]: r for r in rows(args.cases)}
    table = defaultdict(lambda: {"n": 0, "full_correct": 0, "full_unknown": 0, "core": [], "core_unknown": 0, "capped": 0})
    for cid, (core, full) in final.items():
        c = cases[cid]
        for arm in (("ordered", "shuffled") if c["arm"] == "shared" else (c["arm"],)):
            cell = table[f"{arm}:stage{c['stage']}"]
            cell["n"] += 1
            cell["capped"] += c["capped"]
            cell["full_correct"] += full is True
            cell["full_unknown"] += full is None
            cell["core_unknown"] += core is None
            if core is not None:
                cell["core"].append(core)
    report = {key: {**{k: v for k, v in cell.items() if k != "core"},
                    "mean_core": round(statistics.mean(cell["core"]), 3) if cell["core"] else None}
              for key, cell in sorted(table.items())}
    by_item = {(c["arm"], c["stage"], c["item"]): final[c["id"]] for c in cases.values()}
    contrast = {}
    for stage in range(1, 5):
        diffs = [by_item[("shuffled", stage, item)][0] - by_item[("ordered", stage, item)][0]
                 for (arm, s, item) in by_item if arm == "ordered" and s == stage
                 and by_item[("ordered", stage, item)][0] is not None and by_item[("shuffled", stage, item)][0] is not None]
        contrast[f"stage{stage}"] = {"paired_items": len(diffs), "shuffled_minus_ordered_mean_core": round(statistics.mean(diffs), 3) if diffs else None,
                                     "shuffled_higher": sum(d > 0 for d in diffs), "ordered_higher": sum(d < 0 for d in diffs), "equal": sum(d == 0 for d in diffs)}
    old = old_ordered_grades(args.original)
    agreement = {"core_equal": 0, "full_equal": 0, "n": 0}
    for c in cases.values():
        if c["arm"] in ("shared", "ordered"):
            key = f"stage{c['stage']}::{c['item']}"
            new, before = final[c["id"]], old.get(key)
            if before is None:
                continue
            agreement["n"] += 1
            agreement["core_equal"] += new[0] == before[0]
            agreement["full_equal"] += new[1] == before[1]
    reviewer = {"core_agree": sum(grades["a"][c]["core_score"] == grades["b"][c]["core_score"] for c in final),
                "full_agree": sum(grades["a"][c]["full_correct"] == grades["b"][c]["full_correct"] for c in final),
                "n": len(final), "adjudicated": len(adjudicated)}
    result = {"cells": report, "arm_contrast": contrast, "agreement_with_original_ordered_grades": agreement,
              "reviewer_agreement": reviewer,
              "limitations": "AI reviewers (Claude subagents), blinded to arm and phase; no human or Lean validation. One training seed per arm."}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("adjudication-packet")
    s = sub.add_parser("summarize")
    for p in (a, s):
        for name in ("packet", "reviews-a", "reviews-b", "output"):
            p.add_argument("--" + name, type=Path, required=True)
    a.add_argument("--source", type=Path, required=True)
    for name in ("adjudication", "cases", "original"):
        s.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    adjudication_packet(args) if args.command == "adjudication-packet" else summarize(args)


if __name__ == "__main__":
    main()
