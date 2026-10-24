"""Initial goal states (Lean trace_state) for one split of the number-theory statement pool.

Uses `states` and `header` of probe_nt_pool.py, so the prompt text equals the probe's. Batches of --batch statements
per Lean file after the pool header. A row whose file errs inside its declaration gets state null.
Output: one line per row: {"name", "state"}.

Memory: one Lean process that loads all of mathlib (3.7 GB peak measured by probe_nt_pool.py).
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "eval")]
from probe_nt_pool import states  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    for name in ("pool", "environment", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--split", default="train")
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    environment = args.environment.resolve()
    libs = [environment / "mathlib/.lake/build/lib"] + sorted((environment / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    lean = environment / "lean-4.10.0-rc1-linux/bin/lean"
    with args.pool.open() as input_stream:
        rows = [r for r in map(json.loads, input_stream) if r["split"] == args.split]
    found = 0
    with (args.output / "states.jsonl").open("x") as out:
        for b in range(0, len(rows), args.batch):
            batch = rows[b:b + args.batch]
            for row, state in zip(batch, states(lean, env, args.output / f"batch_{b // args.batch:03d}.lean", batch, args.timeout)):
                out.write(json.dumps({"name": row["name"], "state": state}, ensure_ascii=False) + "\n")
                found += state is not None
            out.flush()
            print(json.dumps({"done": b + len(batch), "of": len(rows), "with_state": found}), flush=True)


if __name__ == "__main__":
    main()
