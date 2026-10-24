"""Replay linear LeanDojo training proofs with the pinned Lean checker on cpu-angad.

Input: train.jsonl, or a delivery zip that contains it. Training proofs only.
Memory: training rows <200 MiB, plus one Lean process capped at 2 GiB (-M2048).
Run under a 3 GiB service cap. Each Lean call has the checker's 20-second timeout.
Statuses: accepted, proof_error, context_error, target_present, unavailable_imports,
header_mismatch, timeout, depends_on_eval (the proof uses an evaluation theorem, whose proof is sorry). A context_error means the header itself fails to
elaborate, for example because of missing section variables.
Lean runs directly on the built .olean files through LEAN_PATH, not through
`lake env`. Lake tries to re-clone root-owned packages it cannot read; the
compiled library and the Lean semantics are the same.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("data", "audit")]
from prepare_grpo_pilot import load_checker, module_name, verifier_failed  # noqa: E402
from curriculum_inventory import linear  # noqa: E402

ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
FORBIDDEN = re.compile(r"\b(sorry|admit|axiom|unsafe|native_decide|set_option)\b|#")


def direct_lean_call(checker, environment, timeout=20):
    """Replace the checker's `lake env lean` call with lean on LEAN_PATH; same caps and record."""
    libs = [environment / "mathlib/.lake/build/lib"]
    libs.extend(sorted((environment / "mathlib/.lake/packages").glob("*/.lake/build/lib")))
    env = dict(os.environ, LEAN_PATH=os.pathsep.join(map(str, libs)))

    def call(source, stem):
        path = checker.ROOT / "lean" / (stem + ".lean")
        path.write_text(source)
        start = time.monotonic()
        try:
            result = subprocess.run([str(checker.LEAN / "lean"), "-j1", "-M2048", str(path)],
                                    env=env, capture_output=True, text=True, timeout=timeout)
            record = {"exit_code": result.returncode, "output": result.stdout + result.stderr, "status": "returned"}
        except subprocess.TimeoutExpired as exc:
            record = {"exit_code": None, "output": str(exc.stdout), "status": "timeout"}
        record["wall_seconds"] = time.monotonic() - start
        if "excessive memory consumption" in record["output"]:
            record["status"] = "memory_limit"  # Lean's own -M cap: a rejected candidate, not a broken verifier
        checker.save(checker.ROOT / "lean" / (stem + ".json"), record)
        if record["status"] == "returned" and verifier_failed(record):
            raise RuntimeError("Verifier infrastructure failure: " + record["output"][:300])
        return record
    return call


def load(path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            with archive.open("train.jsonl") as input_stream:
                return [json.loads(line) for line in input_stream]
    with path.open() as input_stream:
        return [json.loads(line) for line in input_stream]


def proof_body(tactics):
    """Indent the first line of each tactic; continuation lines keep their source indentation."""
    lines = []
    for tactic in tactics:
        first, *rest = tactic["tactic"].split("\n")
        lines.append("  " + first)
        lines.extend(rest)
    return "\n".join(lines)


SCOPED = re.compile(r"^(variable|open|universe|set_option|attribute \[local|(local |scoped )?(notation|infixl?|infixr|prefix|postfix))\b")


def strip_comments(text):
    """Remove nested block comments and line comments; keep line breaks."""
    out, i, depth = [], 0, 0
    while i < len(text):
        if text.startswith("/-", i):
            depth, i = depth + 1, i + 2
        elif depth and text.startswith("-/", i):
            depth, i = depth - 1, i + 2
        elif depth:
            out.append("\n" if text[i] == "\n" else "")
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def source_scope(source, start_line):
    """Rebuild the namespaces, sections, variables, and opens active before a declaration.

    Earlier declarations of the same file are not available; only scope commands are kept.
    """
    lines = strip_comments(source).split("\n")[:start_line - 1]
    top, scopes, i = [], [], 0
    while i < len(lines):
        if not lines[i].strip() or lines[i][0].isspace():
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() and lines[j][0].isspace():
            j += 1
        command, first = "\n".join(lines[i:j]).rstrip(), lines[i].strip()
        if match := re.match(r"namespace\s+(\S+)", first):
            scopes.append({"open": first, "close": "end " + match[1], "namespace": match[1], "commands": []})
        elif match := re.match(r"(noncomputable\s+)?section\b\s*(\S*)", first):
            scopes.append({"open": first, "close": ("end " + match[2]).strip(), "namespace": None, "commands": []})
        elif re.match(r"end\b", first):
            if scopes:
                scopes.pop()
        elif SCOPED.match(first) and not re.search(r"\sin(\s|$)", command):
            (scopes[-1]["commands"] if scopes else top).append(command)
        i = j
    text = "".join(c + "\n" for c in top)
    for scope in scopes:
        text += scope["open"] + "\n" + "".join(c + "\n" for c in scope["commands"])
    namespace = ".".join(s["namespace"] for s in scopes if s["namespace"])
    return text, [s["close"] for s in reversed(scopes)], namespace


def file_prefix(source, start_line, blank=()):
    """The source file up to the declaration, with the guard import first and mathlib's autoImplicit setting.

    Each evaluation declaration in `blank` is (first, last, header) with 1-based inclusive lines.
    With a header, its proof becomes `sorry` and its statement stays, so later declarations
    that use it still compile, and any proof that depends on it shows sorryAx and is rejected.
    Without a header, the whole declaration is blanked. No evaluation proof enters a context.
    `#align` lines are dropped; they only record Lean 3 names.
    """
    lines = source.split("\n")[:start_line - 1]
    for first, last, *header in blank:
        start = first - 1
        if header and header[0] and start < len(lines):
            statement = header[0].split("\n")
            keyword = next(i for i, line in enumerate(statement) if re.search(r"\b(?:lemma|theorem)\b", line))
            for i in range(start, min(last, len(lines))):
                lines[i] = ""
            lines[start] = "\n".join(statement[keyword:]) + " := sorry"
            continue
        if start >= len(lines):
            continue  # this evaluation declaration comes after the target
        # The recorded span starts at the keyword; its attributes and doc comment sit just above.
        while start > 0:
            above = lines[start - 1].strip()
            if above.startswith("@["):
                start -= 1
            elif above.endswith("-/"):
                top = next((k for k in range(start - 1, -1, -1) if "/--" in lines[k]), None)
                if top is None or any("-/" in lines[k] for k in range(top, start - 1)):
                    break
                start = top
            else:
                break
        for i in range(start, min(last, len(lines))):
            lines[i] = ""
    lines = ["" if line.startswith("#align") else line for line in lines]
    last_import = max((i for i, line in enumerate(lines) if line.startswith("import ")), default=-1)
    lines.insert(last_import + 1, "set_option autoImplicit false")
    return "import Lean.Elab.Tactic\n" + "\n".join(lines) + "\n"


def question(row, environment, sources, context="scope", eval_spans=None):
    header = row["declaration_header"]
    match = re.search(r"\b(?:lemma|theorem)\s+([^\s({:]+)", header)
    if match is None:
        return None, "header_mismatch"
    path = environment / "mathlib" / row["file_path"]
    if path not in sources:
        sources[path] = path.read_text()
    scope, closing, namespace = source_scope(sources[path], row["start"][0])
    declared = match[1]
    expected = declared.removeprefix("_root_.") if declared.startswith("_root_.") else ".".join(x for x in (namespace, declared) if x)
    if expected != row["full_name"]:
        return None, "header_mismatch"
    imports = list(dict.fromkeys(module_name(p) for p in row["imports"]))
    roots = [environment / "mathlib/.lake/build/lib", environment / "lean-4.10.0-rc1-linux/lib/lean"]
    roots.extend((environment / "mathlib/.lake/packages").glob("*/.lake/build/lib"))
    if any(not any((r / (p.replace(".", "/") + ".olean")).exists() for r in roots) for p in imports):
        return None, "unavailable_imports"
    if context == "file":
        # The recorded start is the keyword line; attribute lines of the header sit just above it.
        lead = next(i for i, line in enumerate(header.split("\n")) if re.search(r"\b(?:lemma|theorem)\b", line))
        prefix = file_prefix(sources[path], row["start"][0] - lead, (eval_spans or {}).get(row["file_path"], ()))
    else:
        prefix = "".join("import " + p + "\n" for p in imports) + "import Lean.Elab.Tactic\nset_option autoImplicit false\n" + scope
    return {"id": row["theorem_id"], "name": row["full_name"], "header": header, "prefix": prefix,
            "closing": closing}, None


def axioms_of(output):
    if "does not depend on any axioms" in output:
        return []
    match = re.search(r"depends on axioms:\s*\[([^\]]*)\]", output, re.S)
    return [x.strip() for x in match.group(1).split(",")] if match else None


def replay(checker, q, tactics):
    body = proof_body(tactics)
    result = checker.lean_call(checker.source_for(q, body) + "\n#print axioms " + q["name"] + "\n", q["id"])
    output = result["output"]
    axioms = axioms_of(output)
    record = {"status": None, "exit_code": result["exit_code"], "wall_seconds": result["wall_seconds"],
              "axioms": axioms, "forbidden_pattern_in_reference": bool(FORBIDDEN.search(body))}
    if result["status"] == "timeout":
        record["status"] = "timeout"
    elif re.search(r"error: TARGET_PRESENT", output):
        record["status"] = "target_present"
    elif axioms and "sorryAx" in axioms and "error:" not in output:
        record["status"] = "depends_on_eval"
    elif result["exit_code"] == 0 and "TARGET_ABSENT" in output and "error:" not in output \
            and axioms is not None and set(axioms) <= ALLOWED_AXIOMS:
        record["status"] = "accepted"
    else:
        # Classify the failure: does the header elaborate on its own?
        probe = checker.lean_call(checker.source_for(q, "  sorry"), q["id"] + "_header")
        header_ok = probe["exit_code"] == 0 and "error:" not in probe["output"]
        record["status"] = "proof_error" if header_ok else "context_error"
        record["first_error"] = next(iter(re.findall(r"error: ([^\n]*)", output)), None)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--checker", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None, help="Replay at most this many linear proofs")
    parser.add_argument("--shard", default="0/1", help="i/n: replay every n-th linear proof, starting at i")
    parser.add_argument("--context", choices=("scope", "file"), default="scope",
                        help="scope: imports plus rebuilt scope commands; file: the source file up to the declaration")
    parser.add_argument("--timeout", type=int, default=20, help="Seconds per Lean call")
    parser.add_argument("--eval", type=Path, action="append", default=[],
                        help="Validation or test export (jsonl or zip); its declarations are blanked from file contexts")
    args = parser.parse_args()
    index, count = map(int, args.shard.split("/"))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "lean").mkdir()
    checker = load_checker(args.checker, args.environment, args.output)
    checker.lean_call = direct_lean_call(checker, args.environment.resolve(), args.timeout)
    rows = sorted((r for r in load(args.train) if linear(r["traced_tactics"])), key=lambda r: r["theorem_id"])
    rows = rows[index::count][:args.limit]
    statuses, sources, eval_spans = Counter(), {}, {}
    for path in args.eval:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as input_stream:
                rows_eval = [json.loads(line) for line in input_stream]
        else:
            with path.open() as input_stream:
                rows_eval = [json.loads(line) for line in input_stream]
        for r in rows_eval:
            eval_spans.setdefault(r["file_path"], []).append((r["start"][0], r["end"][0], r["declaration_header"]))
    if args.context == "file" and not args.eval:
        raise ValueError("File context needs --eval exports so evaluation declarations can be blanked")
    with (args.output / "replay.jsonl").open("x") as out:
        for row in rows:
            q, skip = question(row, args.environment.resolve(), sources, args.context, eval_spans)
            record = {"theorem_id": row["theorem_id"], "full_name": row["full_name"], "family": row["family"],
                      "tactics": len(row["traced_tactics"])}
            record.update(replay(checker, q, row["traced_tactics"]) if q else {"status": skip})
            statuses[record["status"]] += 1
            out.write(json.dumps(record) + "\n")
            out.flush()
            print(json.dumps({"id": row["theorem_id"][:12], "status": record["status"]}), flush=True)
    summary = {"linear_proofs_in_shard": len(rows), "shard": args.shard, "context": args.context,
               "timeout_seconds": args.timeout, "environment": str(args.environment),
               "eval_declarations_blanked": sum(map(len, eval_spans.values())), "statuses": dict(statuses),
               "lean": "v4.10.0-rc1", "mathlib": "29dcec074de168ac2bf835a77ef68bbe069194c5"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
