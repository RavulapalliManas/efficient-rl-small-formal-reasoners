"""Prepare a small authored curriculum with the existing pinned checker.

Memory: <=40 task records x 64 KiB <=2.5 MiB; one Lean worker <=2 GiB.
Run on cpu-angad under a 3 GiB service cap, one writer, <=1200 seconds.
Completion mode exports training-proof prefixes, never evaluation proofs.
"""
import argparse
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re

from prepare_grpo_pilot import load_checker, verifier_failed

PREFIX = """import Mathlib.Algebra.Order.Ring.Nat
import Lean.Elab.Tactic
set_option autoImplicit false
set_option linter.unusedTactic false
namespace PrereqRL
def tally : Nat → Nat
  | 0 => 0
  | n + 1 => tally n + 1
def twice : Nat → Nat
  | 0 => 0
  | n + 1 => twice n + 2
"""
DEFINITIONS = """Nat denotes natural numbers. a ∣ b means ∃ k : Nat, b = a * k.
tally 0 = 0; tally (n + 1) = tally n + 1.
twice 0 = 0; twice (n + 1) = twice n + 2."""
GUIDANCE = [
    "A proof uses the hypotheses in its context. Equality supports reflexivity, symmetry and transitivity.",
    "Rewrite a natural-number expression with an appropriate equality lemma.",
    "For divisibility, supply a witness or apply a divisibility lemma to context hypotheses.",
    "Natural-number induction has a zero case and a successor case. The successor case can use its induction hypothesis.",
]
SYNTAX_GUIDANCE = """Lean 4 proof-body syntax forms:
rfl
exact <proof term>
apply <lemma name>
rw [<equality lemma>]
simpa [<definition names>] using <proof term>
induction <natural-number variable> with
| zero => <tactic body>
| succ n ih => <tactic body>
Angle-bracket text is a placeholder. Replace it with names from the context.
Use Nat, not nat. Return tactics inside one fenced Lean block.
Do not write imports, namespaces, declarations, or Lean 3 begin/end blocks.
Do not copy the theorem statement or the displayed goal."""
SUPPORT = [
    ["Eq.refl", "Eq.symm", "Eq.trans"],
    ["Nat.zero_add", "Nat.add_zero", "Nat.add_assoc", "Nat.mul_zero"],
    ["Nat.dvd_refl", "Nat.dvd_trans", "Nat.dvd_add", "Nat.dvd_zero"],
    ["Nat.rec", "Nat.succ_mul", "Nat.dvd_add", "Nat.dvd_refl", "Nat.dvd_zero"],
]
# Train, diagnostic validation, and unseen compositions are authored separately.
# This is an elementary diagnostic, not an undergraduate public benchmark.
TASKS = [
    ("train", 0, "reflexivity", "(n : Nat) : n = n", "rfl"),
    ("train", 0, "hypothesis", "(n m : Nat) (h : n = m) : n = m", "exact h"),
    ("train", 0, "symmetry", "(n m : Nat) (h : n = m) : m = n", "exact h.symm"),
    ("train", 0, "equality_chain", "(n m k : Nat) (h1 : n = m) (h2 : m = k) : n = k", "exact h1.trans h2"),
    ("train", 1, "zero_add", "(n : Nat) : 0 + n = n", "exact Nat.zero_add n"),
    ("train", 1, "add_associativity", "(a b c : Nat) : (a + b) + c = a + (b + c)", "exact Nat.add_assoc a b c"),
    ("train", 1, "mul_zero", "(n : Nat) : n * 0 = 0", "rfl"),
    ("train", 1, "simplify_hypothesis", "(n m : Nat) (h : n = m + 0) : n = m", "simpa using h"),
    ("train", 2, "divides_self", "(a : Nat) : a ∣ a", "exact Nat.dvd_refl a"),
    ("train", 2, "divides_transitive", "(a b c : Nat) (h1 : a ∣ b) (h2 : b ∣ c) : a ∣ c", "exact Nat.dvd_trans h1 h2"),
    ("train", 2, "divides_witness", "(a k : Nat) : a ∣ a * k", "exact ⟨k, rfl⟩"),
    ("train", 2, "divides_sum", "(a b c : Nat) (h1 : a ∣ b) (h2 : a ∣ c) : a ∣ b + c", "exact Nat.dvd_add h1 h2"),
    ("train", 3, "induction_rule", "(P : Nat → Prop) (h0 : P 0) (hs : ∀ n, P n → P (n + 1)) : ∀ n, P n",
     "intro n\ninduction n with\n| zero => exact h0\n| succ n ih => exact hs n ih"),
    ("train", 3, "tally_identity", "(n : Nat) : tally n = n",
     "induction n with\n| zero => rfl\n| succ n ih => simp [tally, ih]"),
    ("train", 3, "twice_identity", "(n : Nat) : twice n = n * 2",
     "induction n with\n| zero => rfl\n| succ n ih => simp [twice, ih, Nat.succ_mul]"),
    ("train", 3, "twice_divisible", "(n : Nat) : 2 ∣ twice n",
     "induction n with\n| zero => exact Nat.dvd_zero 2\n| succ n ih => simpa [twice] using Nat.dvd_add ih (Nat.dvd_refl 2)"),
    ("validation", 0, "equality_substitution", "(a b c : Nat) (h : a = b) (k : c = a) : c = b", "exact k.trans h"),
    ("validation", 1, "one_mul", "(n : Nat) : 1 * n = n", "exact Nat.one_mul n"),
    ("validation", 2, "double_divisible", "(a b : Nat) (h : a ∣ b) : a ∣ b + b", "exact Nat.dvd_add h h"),
    ("validation", 3, "tally_zero_tail", "(n : Nat) : tally n + 0 = n",
     "induction n with\n| zero => rfl\n| succ n ih => simpa [tally] using congrArg Nat.succ ih"),
    ("heldout", 2, "equality_and_divisibility", "(a b c d : Nat) (h : a + b = c) (k : c ∣ d) : a + b ∣ d", "simpa [h] using k"),
    ("heldout", 2, "sum_and_transitivity", "(a b c d : Nat) (h1 : a ∣ b) (h2 : a ∣ c) (h3 : b + c ∣ d) : a ∣ d", "exact Nat.dvd_trans (Nat.dvd_add h1 h2) h3"),
    ("heldout", 2, "scaled_sum", "(a b c k l : Nat) (h1 : a ∣ b) (h2 : a ∣ c) : a ∣ b * k + c * l",
     "have hb : a ∣ b * k := Nat.dvd_trans h1 ⟨k, rfl⟩\nhave hc : a ∣ c * l := Nat.dvd_trans h2 ⟨l, rfl⟩\nexact Nat.dvd_add hb hc"),
    ("heldout", 3, "combined_recurrences", "(n : Nat) : tally n + twice n = n * 3",
     "induction n with\n| zero => rfl\n| succ n ih =>\n  simp only [tally, twice, Nat.succ_mul]\n  omega"),
]


NUMBER_THEORY_BOOTSTRAP = [q for q in TASKS if q[0] == "train"] + [
    ("train", 1, "remainder_7_3", ": (7 : Nat) % 3 = 1", "rfl"),
    ("train", 1, "remainder_11_4", ": (11 : Nat) % 4 = 3", "rfl"),
    ("train", 1, "remainder_17_5", ": (17 : Nat) % 5 = 2", "rfl"),
    ("train", 1, "remainder_23_7", ": (23 : Nat) % 7 = 2", "rfl"),
    ("train", 1, "quotient_12_3", ": (12 : Nat) / 3 = 4", "rfl"),
    ("train", 1, "quotient_20_4", ": (20 : Nat) / 4 = 5", "rfl"),
    ("train", 1, "quotient_26_5", ": (26 : Nat) / 5 = 5", "rfl"),
    ("train", 1, "quotient_35_6", ": (35 : Nat) / 6 = 5", "rfl"),
    ("train", 2, "witness_2_8", ": 2 ∣ (8 : Nat)", "exact ⟨4, rfl⟩"),
    ("train", 2, "witness_3_15", ": 3 ∣ (15 : Nat)", "exact ⟨5, rfl⟩"),
    ("train", 2, "witness_4_28", ": 4 ∣ (28 : Nat)", "exact ⟨7, rfl⟩"),
    ("train", 2, "witness_5_30", ": 5 ∣ (30 : Nat)", "exact ⟨6, rfl⟩"),
    ("train", 2, "witness_6_42", ": 6 ∣ (42 : Nat)", "exact ⟨7, rfl⟩"),
    ("train", 2, "witness_7_63", ": 7 ∣ (63 : Nat)", "exact ⟨9, rfl⟩"),
    ("train", 2, "witness_8_40", ": 8 ∣ (40 : Nat)", "exact ⟨5, rfl⟩"),
    ("train", 2, "witness_9_54", ": 9 ∣ (54 : Nat)", "exact ⟨6, rfl⟩"),
] + [q for q in TASKS if q[0] != "train"]


def completion_forms(reference):
    """Cut before the last expression or tactic; leave a nonempty suffix.

    One-tactic proofs have identical tactic and body tasks. A simp-list
    expression completion includes its closing delimiter in the continuation.
    """
    canonical = "exact rfl" if reference == "rfl" else reference
    before, separator, last = canonical.rpartition("\n")
    beginning = before + separator
    tactic_start = last.find("=> ") + 3 if "=> " in last else len(last) - len(last.lstrip())
    tactic = last[tactic_start:]
    if " using " in tactic:
        expression_start = tactic.rfind(" using ") + len(" using ")
    elif tactic.startswith("exact "):
        expression_start = len("exact ")
    elif tactic.startswith("simp [") and ", " in tactic:
        expression_start = tactic.rfind(", ") + 2
    else:
        raise ValueError("No registered expression boundary: " + reference)
    return canonical, {"expression": beginning + last[:tactic_start + expression_start],
                       "tactic": beginning + last[:tactic_start], "body": ""}


def main():
    ap = argparse.ArgumentParser()
    for name in ("checker", "environment", "output"):
        ap.add_argument("--" + name, type=Path, required=True)
    ap.add_argument("--completion-tasks", action="store_true")
    ap.add_argument("--number-theory-bootstrap", action="store_true")
    args = ap.parse_args()
    if args.number_theory_bootstrap and args.completion_tasks:
        ap.error("Number-theory bootstrap uses complete proof bodies, without reference prefixes")
    tasks = NUMBER_THEORY_BOOTSTRAP if args.number_theory_bootstrap else TASKS
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    checker = load_checker(args.checker, args.environment, args.output)
    support = {}
    for name in dict.fromkeys(name for names in SUPPORT for name in names):
        result = checker.lean_call(PREFIX + "#check " + name + "\nend PrereqRL\n", "support_" + name.replace(".", "_"))
        if verifier_failed(result) or result["exit_code"] != 0:
            raise RuntimeError("Unavailable support lemma: " + name)
        support[name] = result["output"].strip()
    (args.output / "support.json").write_text(json.dumps(support, ensure_ascii=False, indent=2) + "\n")
    questions = []
    with (args.output / "verification.jsonl").open("x") as out:
        for split, stage, label, proposition, reference in tasks:
            name = split + "_" + label
            header = "theorem " + name + " " + proposition
            q = {"id": hashlib.sha256((PREFIX + header).encode()).hexdigest(), "name": "PrereqRL." + name,
                 "header": header, "prefix": PREFIX, "closing": ["end PrereqRL"],
                 "family": ["logic", "arithmetic", "divisibility", "induction"][stage],
                 "prerequisite_rank": stage, "split": split, "label": label}
            initial = checker.lean_call(checker.source_for(q, "  trace_state\n  sorry"), name + "_initial")
            if verifier_failed(initial) or initial["exit_code"] != 0 or "TARGET_ABSENT" not in initial["output"]:
                raise RuntimeError("Invalid theorem context: " + name)
            state = initial["output"].split("TARGET_ABSENT", 1)[1]
            state = "\n".join(line for line in state.splitlines() if ".lean:" not in line).strip()
            if "⊢" not in state or any(x in state for x in ("TARGET_", "run_tac", "Lean.getEnv", "Lean.logInfo")):
                raise ValueError("Checker implementation leaked into the goal: " + name)
            q.update(initial_state=state, definitions=DEFINITIONS, support_names=SUPPORT[stage],
                     support_statements=[support[n] for n in SUPPORT[stage]], guidance=SYNTAX_GUIDANCE + "\n" + GUIDANCE[stage])
            if args.completion_tasks and split == "train":
                reference, q["completion_prefixes"] = completion_forms(reference)
            check = checker.check_candidate(q, reference, name + "_reference")
            if verifier_failed(check) or not check["accepted"]:
                raise RuntimeError("Reference proof does not verify: " + name + "\n" + check.get("output", ""))
            out.write(json.dumps({"id": q["id"], "split": split, "context_valid": True, "reference": check}, ensure_ascii=False) + "\n")
            out.flush()
            questions.append(q)
            print(json.dumps({"label": label, "split": split, "accepted": True}), flush=True)
    def normalized(q):
        return re.sub(r"\s+", " ", q["header"].split(" ", 2)[2]).strip()
    overlap = []
    for a in questions:
        for b in questions:
            if a["split"] >= b["split"]:
                continue
            left, right = normalized(a), normalized(b)
            similarity = SequenceMatcher(None, left, right, autojunk=False).ratio()
            if left == right:
                raise ValueError("Exact statement duplication across splits")
            if similarity >= 0.9:
                overlap.append({"left": a["label"], "right": b["label"], "left_split": a["split"], "right_split": b["split"], "similarity": similarity})
    if any("heldout" in (r["left_split"], r["right_split"]) for r in overlap):
        raise ValueError("Heldout composition exceeds the registered near-duplicate threshold")
    for file, pool in (("train.jsonl", [q for q in questions if q["split"] == "train"]),
                       ("validation.jsonl", [q for q in questions if q["split"] != "train"])):
        (args.output / file).write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in pool))
    manifest = {"counts": {split: sum(q["split"] == split for q in questions) for split in ("train", "validation", "heldout")},
                "lean": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5",
                "scope": "authored elementary diagnostic; heldout compositions are not a public benchmark or family-heldout test",
                "premises": "identical imports and recursive definitions in every task; assistance changes text, never library access",
                "prompt_variant": ("generic Lean 4 syntax templates plus supplied training-reference proof prefixes"
                                   if args.completion_tasks else "generic Lean 4 syntax templates in full assistance only; no task-specific reference proof"),
                "number_theory_bootstrap": args.number_theory_bootstrap,
                "automation_policy": "Existing checker policy; rfl and ordinary Lean automation remain allowed. Closed arithmetic success alone does not establish proof composition.",
                "prerequisites": [{"stage": i, "requires": list(range(i)), "rationale": GUIDANCE[i]} for i in range(4)],
                "sources": ["https://leanprover-community.github.io/mathematics_in_lean/C05_Elementary_Number_Theory.html",
                            "https://lean-lang.org/doc/reference/latest/Tactic-Proofs/Tactic-Reference/"],
                "exact_cross_split_duplicates": 0, "near_duplicate_threshold": 0.9, "near_duplicate_flags": overlap,
                "overlap_limitations": "text comparison is not semantic independence; diagnostic validation can reuse proof operations",
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "reference_policy": ("training-proof prefixes are supplied assistance; reference suffixes and evaluation proofs stay in CPU artifacts"
                                     if args.completion_tasks else "reference proofs stay in CPU verification artifacts; exported tasks contain no solution steps")}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
