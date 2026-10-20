"""Pure-Python checks of the held-out screens (no Lean). Planted inputs have known answers; each screen has a negative.

The Lean group at the end is the marked integration group: it runs only with TACTIC_RL_ROOT set (fleet layout of
test_tactic_rl_integration.py) and checks planted statements with the real verifier. Peak there: one REPL, < 4 GiB.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path[:0] = [str(ROOT / d) for d in ("data", "audit", "eval")]

import automation_chain_screen as auto  # noqa: E402
import false_hypothesis_sweep as sweep  # noqa: E402
import heldout_screens as hs  # noqa: E402
import stratify_heldout as strat  # noqa: E402
import train_similarity_strata as sim  # noqa: E402


def pool_row(name, stmt, split="validation", topic="Divisibility"):
    return {"name": name, "split": split, "topic": topic, "formal_statement": f"theorem {name} {stmt} := by sorry"}


def write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return path


class Parsing(unittest.TestCase):
    def test_binders_and_premises(self):
        stmt = "(a b : ℕ) (h₀ : a ∣ b ∧ 0 < a) [Fact (Nat.Prime a)] (h : ∀ x : ℕ, x = x) : a ≤ b"
        binders, conclusion = hs.split_binders(stmt)
        self.assertEqual(binders[0], ("(", "a b", "ℕ"))
        self.assertEqual(binders[3], ("(", "h", "∀ x : ℕ, x = x"))
        self.assertEqual(conclusion, "a ≤ b")
        self.assertEqual(len(hs.premises(stmt)["hypotheses"]), 3)  # h₀, the instance binder, h

    def test_data_binders_are_not_premises(self):
        p = hs.premises("(f : ℕ → ℕ) (x : ZMod 7) {m : Fin (n+1)} : ¬ (x = 1)")
        self.assertEqual(p, {"hypotheses": [], "goal_arrow": False, "conclusion_false": False})
        self.assertTrue(hs.premises(": ∀ n : ℕ, n < 0 → n = 5")["goal_arrow"])
        self.assertTrue(hs.premises("(a : ℤ) (h : a = 2) : False")["conclusion_false"])

    def test_header_and_proof_text(self):
        sft = {"theorem": "t", "messages": [{"role": "user", "content": "q"},
                                            {"role": "assistant", "content": "```lean\ntheorem t (n : ℕ) : n = n := by\n  rfl\n```"}]}
        self.assertEqual(hs.header_of(sft), "theorem t (n : ℕ) : n = n")
        self.assertEqual(hs.header_of({"header": "theorem t : True"}), "theorem t : True")
        self.assertEqual(hs.header_of(pool_row("v", ": True")), "theorem v : True")
        self.assertEqual(hs.name_of(sft), "t")

    def test_theorem_of(self):
        self.assertEqual(hs.theorem_of("dev:ntb_abc"), "ntb_abc")
        self.assertEqual(hs.theorem_of("ntb_abc:whole:0"), "ntb_abc")
        self.assertEqual(hs.theorem_of("ntb_abc"), "ntb_abc")


class Vacuity(unittest.TestCase):
    def test_status(self):
        named = hs.premises("(n : ℕ) (h : n < 0) : n = 5")
        self.assertEqual(hs.vacuity_status(named, ["rejected", "accepted"]), "contradictory")
        self.assertEqual(hs.vacuity_status(named, ["rejected", "timeout"]), "undetermined")  # negative
        free = hs.premises("(n : ℕ) : n + 0 = n")
        self.assertEqual(hs.vacuity_status(free, ["rejected"]), "consistent")
        false_goal = hs.premises("(a : ℤ) (h : a = 2 ∧ a = 3) : False")
        self.assertEqual(hs.vacuity_status(false_goal, ["accepted"]), "undetermined")

    def test_sweep_rows(self):
        pool = [pool_row("v1", "(n : ℕ) (h : n < 0) : n = 5"), pool_row("v2", "(n : ℕ) (h : n < 3) : n < 5")]
        checked = [{"arm": "intros_exfalso", "name": "v1", "sample": 1, "status": "accepted"}]
        checked += [{"arm": "intros_exfalso", "name": "v2", "sample": i, "status": "rejected"}
                    for i in range(len(sweep.TACTICS))]
        checked.append({"arm": "exfalso", "name": "v2", "sample": 0, "status": "accepted"})  # other arm: ignored
        rows = {r["name"]: r for r in sweep.theorem_rows(pool, checked, "intros_exfalso")}
        self.assertEqual(rows["v1"]["status"], "contradictory")
        self.assertFalse(rows["v1"]["complete"])
        self.assertEqual(rows["v2"]["status"], "undetermined")
        self.assertTrue(rows["v2"]["complete"])
        self.assertIn("intros", rows["v2"]["goal"])


class Signature(unittest.TestCase):
    def test_match_after_renaming_numerals_moduli_and_conjunct_order(self):
        a = "(x y : ℕ) (h : x ∣ y ∧ 3 < x) (hm : y ≡ 1 [ZMOD 4]) : x ≤ y + 7"
        b = "(a b : ℕ) (h₀ : 5 < a ∧ a ∣ b) (h₁ : b ≡ 2 [ZMOD m]) : a ≤ b + 1"
        self.assertEqual(hs.signature(a), hs.signature(b))

    def test_different_relation_does_not_match(self):
        a = "(x y : ℕ) (h : x ∣ y) : x ≤ y"
        self.assertNotEqual(hs.signature(a), hs.signature("(x y : ℕ) (h : x ∣ y) : x < y"))
        self.assertNotEqual(hs.template(a), hs.template("(x y : ℕ) (h : y ∣ x) : x ≤ y"))

    def test_bands(self):
        self.assertEqual([hs.band(v) for v in (1.0, 0.7, 0.69, 0.5, 0.3, 0.29, 0.0)],
                         [">=0.7", ">=0.7", "0.5-0.7", "0.5-0.7", "0.3-0.5", "<0.3", "<0.3"])


class Similarity(unittest.TestCase):
    TRAIN = {"t_b": "(n : ℕ) (h : 3 ∣ n) : 9 ∣ n ^ 2", "t_a": "(n : ℕ) (h : 3 ∣ n) : 9 ∣ n ^ 2",
             "t_far": "(p : ℕ) (hp : p.Prime) (h : p ≡ 3 [MOD 4]) : ¬ ∃ x y : ℕ, x ^ 2 + y ^ 2 = p"}

    def library(self):
        return {n: hs.features(s) for n, s in self.TRAIN.items()}

    def test_nearest_exact_copy_ties_to_smaller_name(self):
        row = hs.nearest(hs.features("(m : ℕ) (hm : 5 ∣ m) : 25 ∣ m ^ 2"), self.library())
        self.assertEqual(row["nearest"], "t_a")
        self.assertEqual(row["signature_matches"], ["t_a", "t_b"])
        self.assertEqual(row["template_matches"], ["t_a", "t_b"])
        self.assertEqual(row["band"], "0.5-0.7")  # numerals differ, so the text measures stay below 0.7

    def test_unrelated_statement_negative(self):
        row = hs.nearest(hs.features("(s : Finset ℕ) : ∑ i in s, 0 = 0"), self.library())
        self.assertEqual((row["signature_matches"], row["template_matches"]), ([], []))
        self.assertLess(row["max_similarity"], 0.5)

    def test_train_guard_and_copy_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            train = write(tmp / "train_tasks.jsonl", [{"name": n, "header": f"theorem {n} {s}"} for n, s in self.TRAIN.items()])
            proofs = write(tmp / "sft_train.jsonl", [{"theorem": "t_b", "messages": [{"role": "assistant", "content":
                           "```lean\ntheorem t_b " + self.TRAIN["t_b"] + " := by\n  obtain ⟨k, rfl⟩ := h\n  ring_nf\n```"}]}])
            with self.assertRaises(ValueError):
                sim.train_library([train], {"t_a"})
            private = tmp / "private"
            private.mkdir()
            with self.assertRaises(ValueError):
                sim.proof_library([write(private / "references.jsonl", [])], set())
            with self.assertRaises(ValueError):  # each condition alone also stops the run
                sim.proof_library([write(private / "sft_train.jsonl", [])], set())
            with self.assertRaises(ValueError):
                sim.proof_library([write(tmp / "references_v3.jsonl", [])], set())
            library = sim.train_library([train], {"v1"})
            bodies = sim.proof_library([proofs], {"v1"})
            self.assertEqual(bodies, {"t_b": ["  obtain ⟨k, rfl⟩ := h\n  ring_nf"]})
            held = [pool_row("v1", "(m : ℕ) (hm : 5 ∣ m) : 25 ∣ m ^ 2"), pool_row("v2", "(s : Finset ℕ) : s = s")]
            rows = sim.similarity_rows(held, library)
            cands = sim.copy_candidates(rows, held, library, bodies)
            self.assertEqual([(c["name"], c["neighbour"]) for c in cands], [("v1", "t_b"), ("v2", "t_b")])
            sim.add_copy_status(rows, [dict(cands[0], status="rejected"), dict(cands[1], status="timeout")])
            self.assertEqual([r["copy_status"] for r in rows], ["rejected", "timeout"])
            summary = sim.summarize(rows, len(library), True)
            self.assertEqual((summary["statements"], summary["signature_match"]), (2, 1))


class Automation(unittest.TestCase):
    def test_chain_list_is_statement_independent(self):
        self.assertTrue(12 <= len(auto.CHAINS) <= 20)
        self.assertEqual(len(set(auto.CHAINS)), len(auto.CHAINS))
        self.assertFalse(any("rcases" in c or "obtain" in c or "use " in c for c in auto.CHAINS))

    def test_resume_skips_done_candidates(self):
        names = ["a", "b"]
        first = hs.pending_candidates(names, auto.CHAINS, auto.ARM, [])
        self.assertEqual(len(first), 2 * len(auto.CHAINS))
        self.assertEqual(first[1]["continuation"], "  " + auto.CHAINS[1])
        rest = hs.pending_candidates(names, auto.CHAINS, auto.ARM, first[:5])
        self.assertEqual(rest, first[5:])
        self.assertEqual(hs.pending_candidates(names, auto.CHAINS, auto.ARM, first), [])

    def test_rows_and_summary(self):
        pool = [pool_row("a", ": 2 + 2 = 4"), pool_row("b", "(n : ℕ) : n = n", topic="Congruences")]
        checked = [{"arm": auto.ARM, "name": n, "sample": i, "lean_calls": 1, "lean_seconds": 0.5,
                    "status": "accepted" if (n, i) in {("a", 1), ("a", 4)} else "timeout" if i == 0 else "rejected"}
                   for n in ("a", "b") for i in range(len(auto.CHAINS))]
        rows = auto.theorem_rows(pool, checked)
        self.assertEqual(rows[0]["accepting_chains"], ["norm_num", "decide"])
        self.assertEqual(rows[0]["first_accepting_chain"], "norm_num")
        self.assertEqual(rows[1]["accepting_chains"], [])  # negative
        self.assertEqual(rows[1]["chains"][0]["status"], "timeout")
        summary = auto.summarize(rows, checked)
        self.assertEqual((summary["solved_any"], summary["complete_statements"]), (1, 2))
        self.assertEqual(summary["statuses"], {"accepted": 2, "rejected": 2 * len(auto.CHAINS) - 4, "timeout": 2})
        self.assertEqual(summary["lean_calls"], 2 * len(auto.CHAINS))


class Strata(unittest.TestCase):
    NAMES = ["s_vac", "s_auto", "s_copy", "s_sig", "s_sim", "s_other"]

    def screens(self):
        vac = [{"name": n, "status": "contradictory" if n == "s_vac" else "undetermined"} for n in self.NAMES]
        chains = lambda accepted: [{"chain": "omega", "status": "accepted" if accepted else "timeout"}]  # noqa: E731
        automation = [{"name": n, "accepting_chains": ["omega"] if n in ("s_vac", "s_auto") else [],
                       "chains": chains(n in ("s_vac", "s_auto"))} for n in self.NAMES]
        similarity = [{"name": n, "copy_status": "accepted" if n == "s_copy" else "rejected",
                       "signature_matches": ["t"] if n == "s_sig" else [], "template_matches": [],
                       "max_similarity": {"s_sim": 0.5, "s_other": 0.49}.get(n, 0.9 if n == "s_auto" else 0.1),
                       "band": "x", "nearest": "t"} for n in self.NAMES]
        return vac, automation, similarity

    def test_fixed_order(self):
        pool = [pool_row(n, ": True") for n in self.NAMES]
        rows = {r["name"]: r["stratum"] for r in strat.join(pool, *self.screens())}
        self.assertEqual(rows, {"s_vac": "vacuous", "s_auto": "automation", "s_copy": "near_train",
                                "s_sig": "near_train", "s_sim": "near_train", "s_other": "other"})

    def test_missing_screen_row_stops(self):
        vac, automation, similarity = self.screens()
        with self.assertRaises(ValueError):
            strat.join([pool_row(n, ": True") for n in self.NAMES], vac[1:], automation, similarity)

    def test_counts_and_arms(self):
        pool = [pool_row(n, ": True", topic="Primes" if n == "s_vac" else "Divisibility") for n in self.NAMES]
        rows = strat.join(pool, *self.screens())
        steps = [{"task_id": "dev:s_vac", "returns": [0, 1]}, {"task_id": "dev:s_other", "returns": [0, 0]},
                 {"task_id": "dev:s_sim", "status": "censored", "returns": []}, {"task_id": "dev:ghost", "returns": [1]}]
        report = strat.arm_report(rows, steps)
        self.assertEqual(report["vacuous"], {"statements": 1, "attempted": 1, "solved": 1})
        self.assertEqual(report["other"], {"statements": 1, "attempted": 1, "solved": 0})
        self.assertEqual(report["near_train"], {"statements": 3, "attempted": 0, "solved": 0})
        self.assertEqual((report["censored_only"], report["unmatched_task_names"]), (1, 1))
        summary = strat.summarize(rows, {"E0": report})
        self.assertEqual(summary["by_stratum"], {"vacuous": 1, "automation": 1, "near_train": 3, "other": 1})
        self.assertEqual(summary["by_topic"]["Primes"]["vacuous"], 1)
        self.assertEqual(sum(summary["by_stratum"].values()), len(self.NAMES))

    def test_load_pool_topic_from_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pool = write(tmp / "pool.jsonl", [{"name": "b", "split": "validation", "formal_statement": "theorem b : True"},
                                              {"name": "a", "split": "train", "formal_statement": "theorem a : True"}])
            tasks = write(tmp / "tasks_dev.jsonl", [{"name": "b", "topic": "Congruences"}])
            rows = hs.load_pool(pool, "validation", tasks)
            self.assertEqual([(r["name"], r["topic"]) for r in rows], [("b", "Congruences")])
            self.assertEqual([r["name"] for r in hs.load_pool(pool)], ["a", "b"])


@unittest.skipUnless(os.environ.get("TACTIC_RL_ROOT"), "Fleet Lean integration only")
class LeanScreens(unittest.TestCase):
    """Planted statements through the real verifier: a vacuous one, a goal-premise one, and a consistent negative."""

    def test_planted(self):
        from bench_lean_verifier import repl_context
        from prepare_grpo_pilot import load_checker
        from probe_nt_pool import check_candidates
        root = Path(os.environ["TACTIC_RL_ROOT"])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            environment = (root / "lean_env_full").resolve()
            checker = load_checker(root / "proof_replay_v1/code/checker.py", environment, out)
            ctx = repl_context(checker, environment, root / "rlvr_verifier/src/repl/.lake/build/bin/repl", out, 20, 120, 64)
            pool = [pool_row("p_vac", "(n : ℕ) (h : n < 0) : n = 5"), pool_row("p_goal", ": ∀ n : ℕ, n < 0 → n = 5"),
                    pool_row("p_ok", "(n : ℕ) (h : n < 3) : n < 5")]
            names = [r["name"] for r in pool]
            texts = ["intros\nexfalso\n" + t for t in sweep.TACTICS]
            hs.append(out / "candidates.jsonl", hs.pending_candidates(names, texts, "intros_exfalso", []))
            hs.append(out / "candidates.jsonl", hs.pending_candidates(names, auto.CHAINS, auto.ARM, []))
            check_candidates(ctx, {r["name"]: r for r in pool}, out, 1)
            checked = hs.read(out / "checked.jsonl")
            vac = {r["name"]: r["status"] for r in sweep.theorem_rows(pool, checked, "intros_exfalso")}
            self.assertEqual(vac, {"p_vac": "contradictory", "p_goal": "contradictory", "p_ok": "undetermined"})
            chains = {r["name"]: r["accepting_chains"] for r in auto.theorem_rows(pool, checked)}
            self.assertIn("omega", chains["p_ok"])


if __name__ == "__main__":
    unittest.main()
