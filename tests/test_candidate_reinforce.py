"""Tiny tensor checks of candidate REINFORCE; no Lean or pretrained model."""
import importlib
import math
from pathlib import Path
import sys
import unittest


TRAIN = Path(__file__).resolve().parents[1] / 'scripts/train'
sys.path.insert(0, str(TRAIN))


class CandidateReinforceTest(unittest.TestCase):
    def modules(self):
        self.assertTrue((TRAIN / 'run_candidate_reinforce.py').exists(), 'Candidate REINFORCE is missing')
        import torch
        return torch, importlib.import_module('candidate_policy'), importlib.import_module('run_candidate_reinforce')

    def fixtures(self, rewards=(1.0, 0.0)):
        torch, common, driver = self.modules()
        class Policy(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.logits = torch.nn.Parameter(torch.tensor([0.0, 0.0, 50.0]))
                self.critic = torch.nn.Parameter(torch.tensor(0.0))
            def evaluate(self, support):
                return self.logits, self.critic
        policy = Policy()
        support = common.CandidateSupport('same TRAIN root',
            tuple(common.Candidate(i, t) for i, t in [(3, 'intro h'), (9, 'constructor'), (22, 'bad')]),
            (True, True, False))
        episodes = tuple(common.Episode((common.Transition(support,
            common.Decision(i, -math.log(2), 0.0), reward, True, False),), 'terminal', True)
            for i, reward in enumerate(rewards))
        return torch, common, driver, policy, episodes

    def test_actor_update_uses_masked_probabilities_without_a_critic_update(self):
        torch, _, driver, policy, episodes = self.fixtures()
        optimizer = torch.optim.SGD(policy.parameters(), lr=0.1)
        result = driver.update_reinforce(policy, optimizer, episodes)
        self.assertTrue(result['optimizer_updated'])
        self.assertEqual(result['updates'], 1)
        self.assertGreater(policy.logits[0].item(), policy.logits[1].item())
        self.assertEqual(policy.logits[2].item(), 50.0)
        self.assertEqual(policy.critic.item(), 0.0)
        self.assertIsNone(policy.critic.grad)

    def test_all_zero_returns_preserve_parameters(self):
        torch, _, driver, policy, episodes = self.fixtures((0.0, 0.0))
        before = policy.logits.detach().clone()
        result = driver.update_reinforce(policy, torch.optim.SGD(policy.parameters(), lr=0.1), episodes)
        self.assertFalse(result['optimizer_updated'])
        self.assertTrue(torch.equal(policy.logits, before))

    def test_incomplete_censored_and_truncated_episodes_cannot_update(self):
        from dataclasses import replace
        torch, common, driver, policy, episodes = self.fixtures()
        step = episodes[0].transitions[0]
        bad = [replace(episodes[0], completed=False),
               common.Episode((replace(step, censored=True),), 'crash', True),
               common.Episode((replace(step, terminated=False, truncated=True, next_value=0.5),),
                              'truncated', True)]
        before = policy.logits.detach().clone()
        for episode in bad:
            with self.subTest(episode=episode), self.assertRaises(ValueError):
                driver.update_reinforce(policy, torch.optim.SGD(policy.parameters(), lr=0.1),
                                        (episode, episodes[1]))
        self.assertTrue(torch.equal(policy.logits, before))

    def test_complete_empty_failures_remain_in_the_baseline_denominator(self):
        torch, common, driver, policy, _ = self.fixtures()
        episodes = (common.Episode((), 'empty', True), common.Episode((), 'empty', True))
        result = driver.update_reinforce(policy, torch.optim.SGD(policy.parameters(), lr=0.1), episodes)
        self.assertEqual(result['episodes'], 2)
        self.assertEqual(result['transitions'], 0)
        self.assertFalse(result['optimizer_updated'])

    def test_stale_policy_log_probabilities_fail_before_an_update(self):
        from dataclasses import replace
        torch, common, driver, policy, episodes = self.fixtures()
        step = episodes[0].transitions[0]
        stale = common.Episode((replace(step, decision=replace(step.decision, old_log_prob=-9.0)),),
                               'terminal', True)
        before = policy.logits.detach().clone()
        with self.assertRaisesRegex(ValueError, 'on-policy'):
            driver.update_reinforce(policy, torch.optim.SGD(policy.parameters(), lr=0.1),
                                    (stale, episodes[1]))
        self.assertTrue(torch.equal(policy.logits, before))

    def test_shared_training_loop_dispatches_reinforce_on_one_root(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        torch, _, driver, policy, episodes = self.fixtures()
        import run_candidate_ppo as runner
        roots = []
        class Env:
            def close(self): pass
        def factory(task):
            roots.append(task)
            return Env()
        config = dict(max_episodes=2, episodes_per_update=2, max_candidates=3,
                      max_lean_calls=50, horizon=2, gamma=1.)
        with TemporaryDirectory() as directory, patch('candidate_policy.collect_episode', side_effect=episodes):
            report = runner.run_training(policy, torch.optim.SGD(policy.parameters(), lr=0.1),
                factory, ['root a', 'root b'], ['intro h'], config, Path(directory),
                update_fn=driver.update_reinforce, algorithm='reinforce')
        self.assertEqual(roots, ['root a', 'root a'])
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['optimizer_updates'], 1)


if __name__ == '__main__':
    unittest.main()
