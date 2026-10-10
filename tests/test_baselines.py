"""Pure-Python checks of the RFT data builder and the nearest-proof neighbour ranking (no Lean, no torch)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path[:0] = [str(ROOT / d) for d in ("data", "audit", "eval")]

import build_rft_data  # noqa: E402
import nearest_proof_baseline  # noqa: E402
from probe_nt_pool import INSTRUCTION  # noqa: E402

POOL = [{"name": "t1", "split": "train", "formal_statement": "theorem t1 (n : ℕ) : n + 0 = n := by sorry"},
        {"name": "t2", "split": "train", "formal_statement": "theorem t2 : (2 : ℕ) + 2 = 4 := by sorry"},
        {"name": "d1", "split": "train", "formal_statement": "theorem d1 : True := by sorry"},
        {"name": "v1", "split": "validation", "formal_statement": "theorem v1 : False := by sorry"}]
SPLIT = {r["name"]: r["split"] for r in POOL}
STATES = {"t1": "n : ℕ\n⊢ n + 0 = n", "t2": "⊢ 2 + 2 = 4", "d1": "⊢ True", "v1": "⊢ False"}


def cand(arm, name, sample, text, tokens):
    return {"arm": arm, "name": name, "sample": sample, "continuation": text, "generated_tokens": tokens}


CANDIDATES = [cand("m", "t1", 0, "  simp", 10), cand("m", "t2", 0, "  rfl", 50), cand("m", "d1", 0, "  trivial", 5),
              cand("m", "t1", 1, "  simp  ", 10), cand("m", "t2", 1, "  norm_num", 20), cand("single_tactic", "t1", 0, "  simp", 0)]
CHECKED = [dict(c, status=s) for c, s in zip(CANDIDATES, ["accepted", "rejected", "accepted", "accepted", "accepted", "accepted"])]


def build(candidates=CANDIDATES, checked=CHECKED, units=None, states=STATES, budget=None, excluded=frozenset({"d1"})):
    units = units or {r["name"]: r for r in POOL}
    train, val, counts = build_rft_data.rft_rows(candidates, checked, units, states, SPLIT, excluded, None, budget)
    return train + val, counts


class RftTest(unittest.TestCase):
    def test_accepted_train_rows_deduplicated_per_statement(self):
        rows, counts = build()
        self.assertEqual(sorted((r["theorem"], r["messages"][1]["content"]) for r in rows),
                         [("t1", "```lean\ntheorem t1 (n : ℕ) : n + 0 = n := by\n  simp\n```"),
                          ("t2", "```lean\ntheorem t2 : (2 : ℕ) + 2 = 4 := by\n  norm_num\n```")])
        self.assertEqual(counts["skipped_duplicate_body"], 1)
        self.assertEqual(counts["skipped_excluded_name"], 1)

    def test_format_matches_sft_proof_rows(self):
        rows, _ = build()
        row = next(r for r in rows if r["theorem"] == "t1")
        self.assertEqual(row["format"], "proof")
        self.assertEqual(row["messages"][0], {"role": "user", "content": INSTRUCTION.format(state=STATES["t1"])})
        self.assertEqual(row["source"], "rft:m")

    def test_budget_counts_every_visited_sample_round_robin(self):
        rows, counts = build(budget=60)  # sample 0 of t1 (10) and t2 (50); d1 (5) would pass 60
        self.assertEqual([r["theorem"] for r in rows], ["t1"])
        self.assertEqual(counts["generated_tokens_used"], 60)
        self.assertEqual(counts["stopped_at_budget"], 1)

    def test_held_out_candidate_stops_the_build(self):
        extra = cand("m", "v1", 0, "  simp", 3)
        with self.assertRaises(RuntimeError):
            build(CANDIDATES + [extra], CHECKED + [dict(extra, status="rejected")])

    def test_task_rows_keep_given_text_and_task_state(self):
        task = {"task_id": "t1:prefix_top:1", "name": "t1", "kind": "prefix_top", "header": "theorem t1 (n : ℕ) : n + 0 = n",
                "given": "  induction n with\n  | zero => rfl\n", "state": "case succ"}
        c = cand("m", task["task_id"], 0, "  | succ k ih => rfl", 7)
        rows, _ = build([c], [dict(c, status="accepted")], units={task["task_id"]: task}, states={task["task_id"]: "case succ"})
        self.assertEqual(rows[0]["messages"][1]["content"],
                         "```lean\ntheorem t1 (n : ℕ) : n + 0 = n := by\n  induction n with\n  | zero => rfl\n  | succ k ih => rfl\n```")
        self.assertEqual(rows[0]["messages"][0]["content"], INSTRUCTION.format(state="case succ"))
        self.assertEqual(rows[0]["task_id"], task["task_id"])


class NeighbourTest(unittest.TestCase):
    def test_rank_by_jaccard_then_name(self):
        library = {"b": {"x", "y"}, "a": {"x", "y"}, "c": {"x"}, "d": {"z"}}
        self.assertEqual(nearest_proof_baseline.neighbours({"x", "y"}, library, 3), [("a", 1.0), ("b", 1.0), ("c", 0.5)])


if __name__ == "__main__":
    unittest.main()
