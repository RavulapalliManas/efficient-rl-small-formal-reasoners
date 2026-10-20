from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/eval'))
from check_rollout_backends import divergence_gaps, sign_flip_p  # noqa: E402


class SignFlipTest(unittest.TestCase):
    """Ground truth for the backend check: no shift must pass, a real shift must fail."""

    def test_no_shift_rarely_rejects_and_a_real_shift_is_detected(self):
        rng = random.Random(7)
        null_ps = [sign_flip_p([rng.gauss(0, 1) for _ in range(48)], replicates=2000, seed=s) for s in range(40)]
        self.assertLessEqual(sum(p < 0.01 for p in null_ps), 2)  # About 0.4 expected under the null.
        shifted = [rng.gauss(0.8, 1) for _ in range(48)]
        self.assertLess(sign_flip_p(shifted, replicates=2000), 0.01)

    def test_p_value_is_bounded_and_reproducible(self):
        data = [0.1, -0.2, 0.3, 0.0]
        self.assertEqual(sign_flip_p(data, 500, 1), sign_flip_p(data, 500, 1))
        self.assertTrue(1 / 501 <= sign_flip_p(data, 500, 1) <= 1)


class DivergenceGapTest(unittest.TestCase):
    """Ground truth for the greedy diagnostic: a near tie gives a small gap, a clear choice a large one."""

    def test_gap_is_scored_at_the_first_differing_token_only(self):
        table = {(0, (5,)): {6: -0.70, 9: -0.71}, (1, ()): {1: -0.1, 2: -4.0}}
        lookups = []

        def logprobs_at(i, prefix):
            lookups.append((i, tuple(prefix)))
            return table[(i, tuple(prefix))]
        reference = [[5, 6, 7], [1, 3], [4, 4], [8, 9, 2]]
        others = [[5, 9, 7], [2, 3], [4, 4], [8, 9]]
        gaps, identical, length_only = divergence_gaps(reference, others, logprobs_at)
        self.assertEqual(lookups, [(0, (5,)), (1, ())])
        self.assertAlmostEqual(gaps[0], 0.01)  # Near tie.
        self.assertAlmostEqual(gaps[1], 3.9)  # Clear choice.
        self.assertEqual((identical, length_only), (1, 1))

    def test_identical_outputs_give_no_gaps(self):
        self.assertEqual(divergence_gaps([[1, 2]], [[1, 2]], None), ([], 1, 0))


if __name__ == '__main__':
    unittest.main()
