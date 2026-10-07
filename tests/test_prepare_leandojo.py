import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_leandojo.py"


class DataAuditTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), "The dataset entry point is not implemented")
        spec = importlib.util.spec_from_file_location("prepare_leandojo", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_header_removes_proof_but_keeps_named_argument(self):
        code = "lemma target (h : f (n := 2) = 2) : True := by exact True.intro"
        self.assertEqual(
            self.module.declaration_header(code),
            "lemma target (h : f (n := 2) = 2) : True",
        )

    def test_normalization_keeps_numerals_and_constants(self):
        left = "lemma left (n : ℕ) : n + 1 = Nat.succ n"
        right = "lemma right (k : ℕ) : k + 1 = Nat.succ k"
        self.assertEqual(self.module.normalize(left), self.module.normalize(right))
        self.assertNotEqual(
            self.module.normalize(left), self.module.normalize(right.replace("1", "2"))
        )

    def test_split_export_excludes_overlap_and_evaluation_proofs(self):
        path = "Mathlib/Data/Nat/Defs.lean"
        source = {"url": "https://example.org/mathlib", "commit": "pinned"}
        declarations = {
            "Nat.train": "lemma train (n : ℕ) : n + 1 = Nat.succ n := by rfl",
            "Nat.alias": "lemma alias (k : ℕ) : k + 2 = Nat.succ (Nat.succ k) := by rfl",
            "Nat.ref": "lemma ref (n : ℕ) : n = n := by exact Nat.test n",
            "Nat.val": "lemma val (n : ℕ) : n < n + 3 := by omega",
            "Nat.test": "lemma test (n : ℕ) : n + 2 = Nat.succ (Nat.succ n) := by rfl",
        }

        def row(name):
            refs = [{"full_name": "Nat.test", "def_path": path}] if name == "Nat.ref" else []
            return dict(source, file_path=path, full_name=name, start=[1, 1], end=[1, 80],
                        traced_tactics=[{"state_before": "⊢ True", "state_after": "no goals",
                                         "tactic": "rfl", "annotated_tactic": ["rfl", refs]}])

        files = {
            "random/train.json": [row(n) for n in ("Nat.train", "Nat.alias", "Nat.ref")],
            "random/val.json": [row("Nat.val")],
            "random/test.json": [row("Nat.test")],
            "metadata.json": {"from_repo": source},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                for name, value in files.items():
                    data = json.dumps(value).encode()
                    member = tarfile.TarInfo("leandojo_benchmark_4/" + name)
                    member.size = len(data)
                    tar.addfile(member, io.BytesIO(data))
                corpus = {"path": path, "imports": ["Init.lean"], "premises": [
                    {"full_name": name, "code": code, "start": [1, 1]}
                    for name, code in declarations.items()
                ]}
                data = (json.dumps(corpus) + "\n").encode()
                member = tarfile.TarInfo("leandojo_benchmark_4/corpus.jsonl")
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
            config = dict(source=source, archive_md5=hashlib.md5(archive.read_bytes()).hexdigest(),
                          families={"arithmetic": [path]}, excluded_header_tokens=[],
                          near_duplicate_threshold=0.9, max_candidates=100, max_candidate_mib=8,
                          lean_toolchain="leanprover/lean4:test")
            report = self.module.prepare(archive, config, root / "out")
            self.assertEqual(report["retained"], {"train": 1, "val": 1, "test": 1})
            train = json.loads((root / "out/train.jsonl").read_text())
            self.assertEqual(train["full_name"], "Nat.train")
            self.assertIn("traced_tactics", train)
            for split in ("val", "test"):
                manifest = json.loads((root / f"out/{split}.jsonl").read_text())
                self.assertNotIn("traced_tactics", manifest)
                self.assertNotIn("first_trace_state", manifest)
                self.assertNotIn("by rfl", manifest["declaration_header"])
                self.assertNotIn("initial_state", manifest)
            self.assertEqual(report["removed"]["train"]["statement_overlap"], 1)
            self.assertEqual(report["removed"]["train"]["heldout_reference"], 1)


if __name__ == "__main__":
    unittest.main()
