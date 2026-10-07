"""Focused checks for RLP reward direction and data boundaries."""
import importlib.util
import math
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


class RLPChecks(unittest.TestCase):
    def test_structured_curriculum_targets_and_split_boundaries(self):
        import prepare_rlp_corpus as corpus
        self.assertTrue(hasattr(corpus, 'structured_curriculum'), 'Structured curriculum missing')
        data = corpus.structured_curriculum()
        train, validation, heldout = (data[k] for k in ('train', 'validation', 'heldout'))
        self.assertFalse({q['id'] for q in train} & {q['id'] for q in validation + heldout})
        self.assertFalse({q['question'] for q in train} & {q['question'] for q in validation + heldout})
        self.assertTrue(any(q['replay'] for q in train))
        introduced = set()
        for q in train:
            self.assertTrue(set(q['dependencies']) <= introduced, 'A dependent concept precedes its prerequisite')
            introduced.add(q['family'])
        self.assertTrue(any(q['evaluation_kind'] == 'composition' for q in heldout))
        for q in train + validation + heldout:
            self.assertTrue(corpus.verify_primitive(q, q['answer']))
            self.assertFalse(corpus.verify_primitive(q, q['answer'] + 1))
            self.assertEqual(q['text'], q['prefix'] + q['target_text'])
            self.assertEqual(q['target_text'], str(q['answer']))
        import run_rlp_pilot as rlp
        self.assertTrue(hasattr(rlp, 'primitive_messages'), 'Structured prompt missing')
        prompt = rlp.primitive_messages('A HIDDEN TARGET FOLLOWS')
        self.assertNotIn('target_text', str(prompt))

    def test_information_gain_has_correct_sign_and_inclusive_correction(self):
        self.assertTrue((SCRIPTS / "run_rlp_pilot.py").exists(), "RLP runner missing")
        import run_rlp_pilot as rlp
        self.assertAlmostEqual(rlp.information_gain(math.log(0.6), math.log(0.2)), math.log(3))
        self.assertAlmostEqual(rlp.information_gain(math.log(0.2), math.log(0.2)), 0)
        self.assertLess(rlp.information_gain(math.log(0.1), math.log(0.2)), 0)
        rewards = [rlp.corrected_reward(v, 4) for v in [1, 2, 3, 4]]
        advantages = [v - sum(rewards) / 4 for v in rewards]
        for actual, expected in zip(advantages, [-2, -2 / 3, 2 / 3, 2], strict=True):
            self.assertAlmostEqual(actual, expected)

    def test_corpus_excludes_exercises_and_preserves_math_and_proof_context(self):
        self.assertTrue((SCRIPTS / "prepare_rlp_corpus.py").exists(), "RLP corpus preparer missing")
        import prepare_rlp_corpus as corpus
        xml = '<chapter><section><title>Integers</title><theorem xml:id="t"><statement><p>If <m>a | b</m>, a witness exists.</p></statement><proof><p>Choose the witness.</p></proof></theorem><exercises><exercise><p>SECRET EVALUATION ANSWER</p></exercise></exercises></section></chapter>'
        blocks = corpus.text_blocks(xml)
        self.assertEqual(len(blocks), 1)
        self.assertIn('a | b', blocks[0][1])
        self.assertIn('Choose the witness.', blocks[0][1])
        self.assertNotIn('SECRET', str(blocks))

    def test_prime_evaluations_have_checked_answers_and_separate_inputs(self):
        import prepare_rlp_corpus as corpus
        self.assertTrue(hasattr(corpus, 'prime_questions'), 'Prime evaluation missing')
        validation = corpus.prime_questions('validation')
        heldout = corpus.prime_questions('heldout')
        self.assertFalse({q['n'] for q in validation} & {q['n'] for q in heldout})
        answers = {(q['family'], q['n']): q['answer'] for q in heldout}
        self.assertEqual(answers['primality', 131], 1)
        self.assertEqual(answers['primality', 133], 0)
        self.assertEqual(answers['smallest_prime_factor', 143], 11)
        self.assertEqual(answers['distinct_prime_factors', 143], 2)
        self.assertEqual(answers['fermat_applicable', 137], 0)
        self.assertEqual(answers['fermat_applicable', 131], 1)
        blocks = corpus.primitive_structure_blocks()
        self.assertEqual(sorted(stage for stage, _, _ in blocks), [stage for stage, _, _ in blocks])
        self.assertIn('divisibility_witness', [name for _, name, _ in blocks])

    def test_milestones_use_training_tokens_and_do_not_repeat(self):
        import run_rlp_pilot as rlp
        self.assertTrue(hasattr(rlp, 'due_milestones'), 'Token milestones missing')
        self.assertEqual(rlp.due_milestones(99, [100, 200], []), [])
        self.assertEqual(rlp.due_milestones(104, [100, 200], []), [100])
        self.assertEqual(rlp.due_milestones(204, [100, 200], [100]), [200])
        self.assertEqual(rlp.due_milestones(204, [100, 200], [100, 200]), [])


if __name__ == "__main__":
    unittest.main()
