"""Name lists of the midsem plan (artifacts/notes/2026-10-10_midsem_plan.md): what trains, what is held out, what is scored.

Every random choice ranks names by sha256("nt_splits_v1/" + name) and takes the first ones. Train = the pool's train split.
1. excluded_from_training: train statements with best_pool_jaccard >= --minif2f-check-jaccard in the miniF2F overlap
   check (check_summary.jsonl), vacuous train statements (false_hypothesis_sweep.py), train statements with token
   Jaccard >= --jaccard to any validation or test statement, and train statements with token Jaccard >= --minif2f-jaccard
   to any miniF2F statement (tokens() of profile_external_proofs.py on the statement without name and proof, as in the
   miniF2F check; check_summary.jsonl lists only each miniF2F problem's best pool match). The scan covers every train statement before any draw, so every
   training-side list is scanned and the draws below keep their sizes.
2. The problems with rows in the SFT data (train.jsonl and val.jsonl) minus excluded: the first --heldout-fraction by
   rank are heldout_proof_problems, the rest sft_problems.
3. dev: the first --dev train statements by rank among train statements outside the SFT problems and excluded.
4. rl_pool_statements: train minus the SFT problems, dev, and excluded, plus heldout_proof_problems, minus the
   single-tactic statements (also those among heldout_proof_problems, so no whole RL statement falls to one tactic).
5. validation_nontrivial: validation minus single-tactic solved minus vacuous. test_nontrivial: test minus vacuous
   (no single-tactic sweep on test).
6. nearest_train_jaccard.jsonl: for every validation and test statement, its best token Jaccard to a training-side
   statement (sft_problems, heldout_proof_problems, rl_pool_statements, dev) and a band (< BAND, BAND to --jaccard), so
   results can be reported by band.
Output (a new directory): <list>.txt with one name per line, excluded_reasons.jsonl, near_duplicates.jsonl (best match
per target set for each train statement at or above its threshold), minif2f_pairs.jsonl (every miniF2F statement at
or above --minif2f-jaccard, per train statement), nearest_train_jaccard.jsonl, summary.json.

Memory: 4,306 + 244 token sets, 3,450 x 1,100 and 856 x 3,450 set comparisons (<200 MiB).
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "audit")]
from profile_external_proofs import statement, tokens  # noqa: E402

SALT = "nt_splits_v1"
BAND = 0.70  # the first threshold (nt_splits_v1); bands of nearest_train_jaccard.jsonl


def rank(name):
    return hashlib.sha256(f"{SALT}/{name}".encode()).hexdigest()


def jaccard(a, b):
    return len(a & b) / max(len(a | b), 1)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--minif2f-check", type=Path, required=True, help="check_summary.jsonl of the miniF2F overlap check")
    parser.add_argument("--minif2f", type=Path, required=True, help="miniF2F statements as rows with name, formal_statement")
    parser.add_argument("--vacuous", type=Path, required=True, help="vacuous.jsonl of false_hypothesis_sweep.py")
    parser.add_argument("--sft", type=Path, required=True, help="SFT data directory (train.jsonl, val.jsonl)")
    parser.add_argument("--train-single-tactic", type=Path, required=True, help="solved_by_single_tactic.txt (train)")
    parser.add_argument("--val-single-tactic", type=Path, required=True, help="statements.jsonl of the validation probe")
    parser.add_argument("--jaccard", type=float, default=0.7, help="near-duplicate scan against validation, test, miniF2F")
    parser.add_argument("--minif2f-check-jaccard", type=float, default=0.7, help="threshold on best_pool_jaccard")
    parser.add_argument("--minif2f-jaccard", type=float, help="near-duplicate scan against miniF2F (default: --jaccard)")
    parser.add_argument("--heldout-fraction", type=float, default=0.25)
    parser.add_argument("--dev", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pool = [json.loads(line) for line in args.pool.open()]
    split = {r["name"]: r["split"] for r in pool}
    names = {s: sorted(n for n, x in split.items() if x == s) for s in ("train", "validation", "test")}
    train = set(names["train"])
    toks = {r["name"]: tokens(statement(r["formal_statement"])) for r in pool}
    minif2f = [json.loads(line) for line in args.minif2f.open()]
    targets = {"validation": [(n, toks[n]) for n in names["validation"]], "test": [(n, toks[n]) for n in names["test"]],
               "minif2f": [(r["name"], tokens(statement(r["formal_statement"]))) for r in minif2f]}

    reasons = defaultdict(list)
    check = [json.loads(line) for line in args.minif2f_check.open()]
    minif2f_check = sorted({c["best_pool_name"] for c in check if c["best_pool_split"] == "train" and c["best_pool_jaccard"] >= args.minif2f_check_jaccard})
    for n in minif2f_check:
        reasons[n].append("minif2f_check_jaccard")
    vacuous = {r["name"]: r for r in map(json.loads, args.vacuous.open())}
    for n in sorted(train & set(vacuous)):
        reasons[n].append("vacuous")
    limit = {"validation": args.jaccard, "test": args.jaccard,
             "minif2f": args.jaccard if args.minif2f_jaccard is None else args.minif2f_jaccard}
    near, pairs = [], []
    for n in names["train"]:
        best = {t: max((jaccard(toks[n], tt), m) for m, tt in rows) for t, rows in targets.items()}
        hits = [t for t, (j, _) in best.items() if j >= limit[t]]
        matched = sorted(((jaccard(toks[n], tt), m) for m, tt in targets["minif2f"]), reverse=True)
        matched = [(j, m) for j, m in matched if j >= limit["minif2f"]]
        if matched:
            pairs.append({"name": n, "minif2f": [{"name": m, "jaccard": round(j, 3)} for j, m in matched]})
        for t in hits:
            reasons[n].append("near_duplicate_" + t)
        if hits:
            near.append({"name": n, "best": {t: {"name": m, "jaccard": round(j, 3)} for t, (j, m) in best.items()}})
    excluded = set(reasons)

    sft_all = {json.loads(line)["theorem"] for f in ("train.jsonl", "val.jsonl") for line in (args.sft / f).open()}
    single = set(args.train_single_tactic.read_text().split())
    val_rows = [json.loads(line) for line in args.val_single_tactic.open()]
    val_single = {r["name"] for r in val_rows if r["single_tactic"] > 0}
    if not (excluded <= train and sft_all <= train and single <= train and {r["name"] for r in val_rows} == set(names["validation"])):
        raise RuntimeError("an input names statements outside the expected split")
    ranked = sorted(sft_all - excluded, key=rank)
    cut = round(args.heldout_fraction * len(ranked))
    heldout, sft = set(ranked[:cut]), set(ranked[cut:])
    dev = set(sorted(train - sft_all - excluded, key=rank)[:args.dev])
    rl_pool = ((train - sft_all - dev - excluded) | heldout) - single
    lists = {"excluded_from_training": excluded, "sft_problems": sft, "heldout_proof_problems": heldout, "dev": dev,
             "rl_pool_statements": rl_pool,
             "validation_nontrivial": set(names["validation"]) - val_single - set(vacuous),
             "test_nontrivial": set(names["test"]) - set(vacuous)}
    sides = [excluded, sft, dev, rl_pool - heldout, heldout]
    if sum(map(len, sides)) != len(set().union(*sides)) or len(dev) != args.dev:
        raise RuntimeError("training-side lists overlap or dev is short")

    args.output.mkdir(parents=True, exist_ok=False)
    for label, members in lists.items():
        with (args.output / f"{label}.txt").open("x") as out:
            out.write("".join(n + "\n" for n in sorted(members)))
    with (args.output / "excluded_reasons.jsonl").open("x") as out:
        for n in sorted(excluded):
            out.write(json.dumps({"name": n, "reasons": reasons[n], "in_sft_data": n in sft_all}) + "\n")
    with (args.output / "near_duplicates.jsonl").open("x") as out:
        for row in near:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (args.output / "minif2f_pairs.jsonl").open("x") as out:
        for row in pairs:
            out.write(json.dumps(row) + "\n")
    near_names = {r["name"] for r in near}
    side = sorted(sft | heldout | rl_pool | dev)
    band = lambda j: f"<{BAND:.2f}" if j < BAND else f"{BAND:.2f}-{args.jaccard:.2f}" if j < args.jaccard else f">={args.jaccard:.2f}"
    nearest = []
    with (args.output / "nearest_train_jaccard.jsonl").open("x") as out:
        for n in names["validation"] + names["test"]:
            j, m = max((jaccard(toks[n], toks[x]), x) for x in side)
            nearest.append({"name": n, "split": split[n], "nearest_train_jaccard": round(j, 3), "nearest_train_name": m,
                            "band": band(j)})
            out.write(json.dumps(nearest[-1]) + "\n")
    summary = {
        "salt": SALT, "rank": "sha256(salt + '/' + name), ascending", "jaccard": args.jaccard,
        "minif2f_check_jaccard": args.minif2f_check_jaccard, "minif2f_jaccard": limit["minif2f"],
        "train_statements_with_minif2f_pairs": len(pairs), "minif2f_pairs": sum(len(r["minif2f"]) for r in pairs),
        "heldout_fraction": args.heldout_fraction, "counts": {k: len(v) for k, v in lists.items()},
        "pool_by_split": {k: len(v) for k, v in names.items()},
        "excluded_by_reason": dict(Counter(x for n in excluded for x in reasons[n])),
        "excluded_minif2f_check_names": len(minif2f_check),
        "minif2f_check_names_also_found_by_scan": sum("near_duplicate_minif2f" in reasons[n] for n in minif2f_check),
        "near_duplicate_train_statements": len(near_names),
        "near_duplicate_by_target": {t: sum("near_duplicate_" + t in reasons[n] for n in near_names) for t in targets},
        "near_duplicate_in_sft_data": len(near_names & sft_all),
        "vacuous_by_split": dict(Counter(r["split"] for r in vacuous.values())),
        "sft_data_problems": len(sft_all), "sft_data_problems_excluded": len(sft_all & excluded),
        "single_tactic_train": len(single), "single_tactic_in_sft": len(single & sft),
        "single_tactic_in_heldout_removed_from_rl_pool": len(single & heldout), "single_tactic_in_dev": len(single & dev),
        "single_tactic_validation": len(val_single),
        "nearest_train_band": {s: dict(Counter(r["band"] for r in nearest if r["split"] == s)) for s in ("validation", "test")},
        "nearest_train_band_validation_nontrivial": dict(Counter(r["band"] for r in nearest if r["name"] in lists["validation_nontrivial"])),
        "nearest_train_band_test_nontrivial": dict(Counter(r["band"] for r in nearest if r["name"] in lists["test_nontrivial"])),
        "inputs": {f"{p.parent.name}/{p.name}": sha256(p) for p in (args.pool, args.minif2f_check, args.minif2f, args.vacuous,
                   args.sft / "train.jsonl", args.sft / "val.jsonl", args.train_single_tactic, args.val_single_tactic)}}
    with (args.output / "summary.json").open("x") as out:
        out.write(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
