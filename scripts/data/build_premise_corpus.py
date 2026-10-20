"""Dump Mathlib theorem names and statements from the pinned Lean environment as a premise corpus.

Usage: build_premise_corpus.py ENVIRONMENT_DIR OUTPUT_DIR [TIMEOUT_SECONDS]

ENVIRONMENT_DIR is the pinned Lean environment (`lean-4.10.0-rc1-linux/` and a built `mathlib/`).
Lean runs directly with LEAN_PATH set to the Mathlib and package build libraries, as the REPL does.
The Lean program imports Mathlib, keeps public, non-internal theorems whose module is under Mathlib,
and prints one JSON line per theorem with its pretty-printed type. Run it on the managed remote runner
with explicit memory and time caps: importing Mathlib and printing every statement is not measured yet.
NOT YET EXECUTED: check the Lean meta API on leanprover/lean4:v4.10.0-rc1 at the first run.
Python peak memory: the set of names plus one line at a time.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

DUMP = r'''import Mathlib
open Lean Meta Elab Command

def keepPremise (env : Environment) (name : Name) : Bool :=
  !name.isInternal && !isPrivateName name &&
  match env.getModuleIdxFor? name with
  | some idx => (env.header.moduleNames.get! idx.toNat).getRoot == `Mathlib
  | none => false

#eval show CommandElabM Unit from do
  let env ← getEnv
  for (name, info) in env.constants.map₁.toList do
    if let .thmInfo val := info then
      if keepPremise env name then
        let statement ← liftTermElabM do
          return (← ppExpr val.type).pretty 100000
        IO.println (Json.compress (Json.mkObj [("name", toJson name.toString), ("statement", toJson statement)]))
'''


def build(environment, output_dir, timeout=14400):
    environment, output_dir = Path(environment), Path(output_dir)
    mathlib_dir = environment / 'mathlib'
    libs = [mathlib_dir / '.lake/build/lib'] + sorted(mathlib_dir.glob('.lake/packages/*/.lake/build/lib'))
    lean_env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / 'dump.lean').write_text(DUMP)
    raw = output_dir / 'raw.jsonl'
    with raw.open('x') as stream:
        subprocess.run([str(environment / 'lean-4.10.0-rc1-linux/bin/lean'), str((output_dir / 'dump.lean').resolve())],
                       env=lean_env, stdout=stream, check=True, timeout=timeout)
    seen, count = set(), 0
    with raw.open() as source, (output_dir / 'corpus.jsonl').open('x') as out:
        for line in source:
            if not line.startswith('{'):
                continue
            record = json.loads(line)
            if record['name'] in seen:
                continue
            seen.add(record['name'])
            record['statement'] = ' '.join(record['statement'].split())
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
            count += 1
    receipt = dict(environment=str(environment), lean_toolchain=(mathlib_dir / 'lean-toolchain').read_text().strip(),
                   theorems=count, corpus_sha256=hashlib.sha256((output_dir / 'corpus.jsonl').read_bytes()).hexdigest())
    (output_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    print(json.dumps(build(sys.argv[1], sys.argv[2], *(int(a) for a in sys.argv[3:]))))
