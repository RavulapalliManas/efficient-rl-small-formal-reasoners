"""Pure-Python checks of elaborate_statements.py (no Lean): the `:= by sorry` construction and status mapping.

A fake REPL stands in for open_repl, as in test_lean_scoring.py, so the per-thread REPL path of the stage runs
unchanged. The checker's guard and source_for are copied from the fleet checker (see test_lean_scoring.py).
"""
from argparse import Namespace
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts" / d) for d in ("data", "audit", "eval")]

import elaborate_statements as es  # noqa: E402


def guard(name):
    return ('  run_tac do\n    if (← Lean.getEnv).contains (' + json.dumps(name) +
            '.toName) then\n      throwError "TARGET_PRESENT"\n    else\n      Lean.logInfo "TARGET_ABSENT"\n')


def source_for(q, body):
    return q['prefix'] + q['header'] + ' := by\n' + guard(q['name']) + body + '\n' + '\n'.join(q['closing']) + '\n'


def msg(severity, data, line=1):
    return {"severity": severity, "pos": {"line": line, "column": 0}, "data": data}


class FakeRepl:
    """Answers by theorem name: t_ok elaborates, t_err has an elaboration error, t_slow times out."""

    def __init__(self):
        self.commands = []

    def send(self, request, timeout):
        cmd = request["cmd"]
        self.commands.append(cmd)
        if "t_slow" in cmd:
            raise TimeoutError
        if "t_err" in cmd:
            return {"env": 1, "messages": [msg("info", "TARGET_ABSENT"),
                                           msg("error", "unknown identifier 'Nat.foo'\nmore detail")]}, 0.2, 0.1
        return {"env": 1, "messages": [msg("info", "TARGET_ABSENT"),
                                       msg("warning", "declaration uses 'sorry'")]}, 0.1, 0.1

    def close(self):
        return {}


def context():
    return Namespace(local=threading.local(), pool_repls=set(), restart_after=400, timeout=20,
                     checker=Namespace(source_for=source_for, guard=guard))


ROWS = [{"name": n, "split": s, "formal_statement": f"theorem {n} (n : ℕ) (h₀ : n % 5 = 3) :\n  2 * n % 5 = 1 := by"}
        for n, s in (("t_ok", "valid"), ("t_err", "test"), ("t_slow", "test"))]


class Construction(unittest.TestCase):
    def test_header_variants(self):
        stmt = "theorem t (n : ℕ) : n = n"
        for text in (stmt + " := by", stmt + " :=", stmt, stmt + " := by sorry", stmt + "  :=  by\n"):
            self.assertEqual(es.header_of(text), stmt, text)

    def test_inner_assignment_is_kept(self):
        stmt = "theorem t : (let x := 5; x) = 5"
        self.assertEqual(es.header_of(stmt + " := by"), stmt)

    def test_one_sorry_and_one_by(self):
        for text in ("theorem t : True := by", "theorem t : True :=", "theorem t : True"):
            declaration = es.declaration_for(context(), es.header_of(text), "t")
            self.assertEqual(declaration.count(":= by"), 1, text)
            self.assertEqual(declaration.count("sorry"), 1, text)
            self.assertIn("theorem t : True := by\n", declaration)
            self.assertTrue(declaration.rstrip().endswith("sorry"))
            self.assertTrue(es.command_for(context(), es.header_of(text), "t").endswith(declaration))

    def test_rows_need_a_theorem_named_like_the_row(self):
        with self.assertRaises(ValueError):
            es.check_rows([{"name": "a", "formal_statement": "theorem b : True := by"}])
        with self.assertRaises(ValueError):
            es.check_rows([{"name": "a", "formal_statement": "theorem a : True := by"}] * 2)


class Statuses(unittest.TestCase):
    def run_stage(self, rows, workers=1):
        repl = FakeRepl()
        original = es.open_repl
        es.open_repl = lambda ctx, head, tag, record: (repl, 0)
        try:
            return es.elaborate(context(), rows, workers), repl
        finally:
            es.open_repl = original

    def test_ok_error_timeout(self):
        records, repl = self.run_stage(ROWS)
        by = {r["name"]: r for r in records}
        self.assertEqual([r["name"] for r in records], ["t_ok", "t_err", "t_slow"])  # input order
        self.assertEqual(by["t_ok"]["status"], "elaborates")
        self.assertTrue(by["t_ok"]["sorry_warning"])  # the expected warning, not an error
        self.assertIsNone(by["t_ok"]["first_error"])
        self.assertEqual((by["t_err"]["status"], by["t_err"]["first_error"]), ("error", "unknown identifier 'Nat.foo'"))
        self.assertEqual(by["t_slow"]["status"], "timeout")
        self.assertEqual(by["t_err"]["split"], "test")
        self.assertTrue(all(r["wall_seconds"] >= 0 for r in records))
        self.assertEqual(len(repl.commands), 3)

    def test_no_sorry_warning_is_an_error(self):
        class Silent(FakeRepl):
            def send(self, request, timeout):
                return {"env": 1, "messages": [msg("info", "TARGET_ABSENT")]}, 0.1, 0.1
        original = es.open_repl
        es.open_repl = lambda ctx, head, tag, record: (Silent(), 0)
        try:
            (record,) = es.elaborate(context(), ROWS[:1], 1)
        finally:
            es.open_repl = original
        self.assertEqual(record["status"], "error")

    def test_summary_counts(self):
        records, _ = self.run_stage(ROWS, workers=2)
        summary = es.summarize(records)
        self.assertEqual(summary["n"], 3)
        self.assertEqual(summary["by_status"], {"elaborates": 1, "error": 1, "timeout": 1})
        self.assertEqual(summary["by_split_status"], {"test": {"error": 1, "timeout": 1}, "valid": {"elaborates": 1}})

    def test_write_outputs_one_writer(self):
        records, _ = self.run_stage(ROWS)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            es.write_outputs(out, records, {"extra": 1})
            rows = [json.loads(line) for line in open(out / "checked.jsonl")]
            self.assertEqual(len(rows), 3)
            self.assertEqual(json.loads((out / "summary.json").read_text())["n"], 3)
            with self.assertRaises(FileExistsError):
                es.write_outputs(out, records, {})


if __name__ == "__main__":
    unittest.main()
