import importlib.util
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


class OrderingTest(unittest.TestCase):
    def test_checker_paths_survive_a_subprocess_working_directory(self):
        path = Path(__file__).resolve().parents[1] / "scripts/prepare_grpo_pilot.py"
        spec = importlib.util.spec_from_file_location("pilot_prepare", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with TemporaryDirectory() as directory:
            stub = Path(directory) / "checker.py"
            stub.write_text("")
            checker = module.load_checker(stub, Path("lean_env"), Path("out"))
            self.assertTrue(checker.ENV.is_absolute(), "Lean's PATH fails when its subprocess changes directory")
            self.assertTrue(checker.ROOT.is_absolute(), "Lean cannot find relative candidate files from another directory")

    def test_orders_preserve_the_same_theorem_occurrences(self):
        path = Path(__file__).resolve().parents[1] / "scripts/prepare_grpo_pilot.py"
        self.assertTrue(path.exists(), "The separate pilot preparation is missing")
        spec = importlib.util.spec_from_file_location("pilot_prepare", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        questions = [
            {"id": "prime", "family": "primes"},
            {"id": "logic", "family": "logic"},
            {"id": "nat", "family": "arithmetic"},
        ]
        random_order = module.ordered_ids(questions, "random", 6, 42)
        curriculum = module.ordered_ids(questions, "curriculum", 6, 42)
        self.assertEqual(Counter(random_order), Counter({q["id"]: 2 for q in questions}))
        self.assertEqual(Counter(random_order), Counter(curriculum))
        self.assertEqual(curriculum, ["logic", "nat", "prime"] * 2)
        self.assertNotEqual(random_order, curriculum)

    def test_verifier_failure_is_not_a_rejected_proof(self):
        path = Path(__file__).resolve().parents[1] / "scripts/prepare_grpo_pilot.py"
        spec = importlib.util.spec_from_file_location("pilot_prepare", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "verifier_failed"), "Verifier failure classification is missing")
        self.assertTrue(module.verifier_failed({"status": "returned", "exit_code": 1, "output": "permission denied"}))
        self.assertTrue(module.verifier_failed({"status": "timeout", "exit_code": None, "output": ""}))
        self.assertFalse(module.verifier_failed({"status": "returned", "exit_code": 1, "output": "error: unknown identifier foo"}))
        self.assertFalse(module.verifier_failed({"status": "forbidden_construct"}))


if __name__ == "__main__":
    unittest.main()
