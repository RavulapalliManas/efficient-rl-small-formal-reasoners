"""Build Lean-checked number-theory answer tasks (formal Q&A) and check every reference answer in Lean.

Each task has an informal question, a unique answer, and a closed Lean statement with answer slots {0}, {1}.
Lean closes the statement with a fixed tactic, so Lean verifies the answer, not any reasoning around it.
Every task is checked twice: the reference answer must pass, and a wrong answer must fail.
Splits are disjoint by construction; equivalent parameter sets (for example gcd(a, b) and gcd(b, a)) are one task.

Memory: up to --workers Lean processes with `import Mathlib.Tactic`, about 2.6 GB RSS each (-M4096 heap cap).
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from math import gcd, lcm
import os
from pathlib import Path
import random
import re
import subprocess

LEVELS = {1: 30, 2: 300, 3: 3000}
SPLITS = (("train", 60), ("val", 15), ("test", 15))


def primes(hi):
    return [n for n in range(2, hi + 1) if all(n % d for d in range(2, int(n**0.5) + 1))]


def witness(rng, hi):
    b, k = rng.randint(2, hi), rng.randint(2, hi)
    return (b, k), f"Find the natural number k such that {b * k} = {b} * k.", f"{b * k} = {b} * {{0}}", (k,)


def gcd_task(rng, hi):
    a, b = sorted(rng.sample(range(2, hi + 1), 2))
    g = rng.randint(1, max(1, hi // 10))
    return (a * g, b * g), f"Compute gcd({a * g}, {b * g}).", f"Nat.gcd {a * g} {b * g} = {{0}}", (gcd(a * g, b * g),)


def lcm_task(rng, hi):
    a, b = sorted(rng.sample(range(2, hi + 1), 2))
    return (a, b), f"Compute lcm({a}, {b}).", f"Nat.lcm {a} {b} = {{0}}", (lcm(a, b),)


def mod_inverse(rng, hi):
    n = rng.randint(5, hi)
    a = rng.choice([x for x in range(2, n) if gcd(x, n) == 1])
    return ((n, a), f"Find the natural number x with x < {n} and {a} * x ≡ 1 (mod {n}).",
            f"{{0}} < {n} ∧ {a} * {{0}} % {n} = 1", (pow(a, -1, n),))


def factor(rng, hi):
    p, q = sorted(rng.choices(primes(hi), k=2))
    return ((p, q), f"The number {p * q} is a product of two primes p ≤ q. Find p and q.",
            f"{p * q} = {{0}} * {{1}} ∧ Nat.Prime {{0}} ∧ Nat.Prime {{1}} ∧ {{0}} ≤ {{1}}", (p, q))


def crt(rng, hi):
    m1, m2 = rng.sample(range(2, hi + 1), 2)
    if gcd(m1, m2) != 1:
        return None
    r1, r2 = rng.randrange(m1), rng.randrange(m2)
    x = next(x for x in range(r1, m1 * m2, m1) if x % m2 == r2)
    key = tuple(sorted([(m1, r1), (m2, r2)]))
    return (key, f"Find the natural number x with x < {m1 * m2}, x ≡ {r1} (mod {m1}), and x ≡ {r2} (mod {m2}).",
            f"{{0}} < {m1 * m2} ∧ {{0}} % {m1} = {r1} ∧ {{0}} % {m2} = {r2}", (x,))


def pow_mod(rng, hi):
    a, e, n = rng.randint(2, hi), rng.randint(2, 12), rng.randint(3, hi)
    return (a, e, n), f"Compute {a}^{e} mod {n}.", f"{a} ^ {e} % {n} = {{0}}", (pow(a, e, n),)


FAMILIES = {"witness": witness, "gcd": gcd_task, "lcm": lcm_task, "mod_inverse": mod_inverse,
            "factor": factor, "crt": crt, "pow_mod": pow_mod}


def build(seed):
    tasks = []
    for family, make in FAMILIES.items():
        seen = set()  # shared across levels: level ranges overlap, and a repeat could cross splits
        for level, hi in LEVELS.items():
            rng, picked = random.Random(f"{seed}/{family}/{level}"), []
            for _ in range(20000):
                made = make(rng, hi)
                if made and made[0] not in seen:
                    seen.add(made[0])
                    picked.append(made)
                if len(picked) == sum(n for _, n in SPLITS):
                    break
            # A family-level with fewer distinct tasks than requested (for example factor at level 1) is split in proportion.
            total, start = sum(n for _, n in SPLITS), 0
            sizes = [len(picked) * n // total for _, n in SPLITS]
            sizes[0] += len(picked) - sum(sizes)
            for (split, _), n in zip(SPLITS, sizes):
                for key, question, statement, answer in picked[start:start + n]:
                    tasks.append({"task_id": f"{family}_{level}_{len(tasks):05d}", "family": family, "level": level,
                                  "split": split, "question": question, "statement": statement,
                                  "answer": list(answer), "tactic": "norm_num"})
                start += n
    return tasks


def wrong(task):
    return [task["answer"][0] + 1] + task["answer"][1:]


def lean_check(theorems, environment, path, timeout):
    """Check one-line theorems in one Lean file; return one accepted flag per theorem (no error on its line)."""
    libs = [environment / "mathlib/.lake/build/lib", *sorted((environment / "mathlib/.lake/packages").glob("*/.lake/build/lib"))]
    path.write_text("\n".join(["import Mathlib.Tactic", *theorems]) + "\n")
    result = subprocess.run([str(environment / "lean-4.10.0-rc1-linux/bin/lean"), "-j1", "-M4096", str(path)],
                            env=dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs))),
                            capture_output=True, text=True, timeout=timeout)
    text = result.stdout + result.stderr
    path.with_suffix(".out").write_text(text)
    if any(line in text for line in ("unknown package", "object file", "excessive memory")):
        raise RuntimeError(f"{path.name}: verifier infrastructure failure: {text[:300]}")
    failed = {int(n) for n in re.findall(r"\.lean:(\d+):\d+: error", text)}
    return [i + 2 not in failed for i in range(len(theorems))]


def check_chunk(job):
    index, chunk, environment, output, timeout = job
    theorems = [f"theorem {t['task_id']}_{kind} : {t['statement'].format(*answer)} := by {t['tactic']}"
                for t in chunk for kind, answer in (("ok", t["answer"]), ("bad", wrong(t)))]
    flags = lean_check(theorems, environment, output / "lean" / f"chunk_{index:03d}.lean", timeout)
    return {t["task_id"]: {"reference_accepted": flags[2 * i], "wrong_rejected": not flags[2 * i + 1]}
            for i, t in enumerate(chunk)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--chunk", type=int, default=150)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    tasks = build(args.seed)
    chunks = [tasks[i:i + args.chunk] for i in range(0, len(tasks), args.chunk)]
    checks = {}
    with ThreadPoolExecutor(args.workers) as pool:
        jobs = [(i, c, args.environment.resolve(), args.output.resolve(), args.timeout) for i, c in enumerate(chunks)]
        for result in pool.map(check_chunk, jobs):
            checks.update(result)
    counts = {}
    with (args.output / "tasks.jsonl").open("x") as out:
        for t in tasks:
            t.update(checks[t["task_id"]])
            out.write(json.dumps(t, ensure_ascii=False) + "\n")
            key = f"{t['family']}/{t['level']}/{t['split']}"
            c = counts.setdefault(key, {"tasks": 0, "reference_accepted": 0, "wrong_rejected": 0})
            c["tasks"] += 1
            c["reference_accepted"] += t["reference_accepted"]
            c["wrong_rejected"] += t["wrong_rejected"]
    summary = {"tasks": len(tasks), "reference_accepted": sum(t["reference_accepted"] for t in tasks),
               "wrong_rejected": sum(t["wrong_rejected"] for t in tasks), "by_family_level_split": counts}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("tasks", "reference_accepted", "wrong_rejected")}), flush=True)


if __name__ == "__main__":
    main()
