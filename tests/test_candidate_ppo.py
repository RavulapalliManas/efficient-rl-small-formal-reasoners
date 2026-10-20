"""Tiny tensor checks for fixed candidate support and complete-rollout PPO."""
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/train'))
try:
    import torch
except ImportError:
    torch = None


def modules():
    path = ROOT / 'scripts/train/candidate_policy.py'
    if not path.exists():
        raise AssertionError('Shared candidate policy is missing')
    import candidate_policy as policy
    import run_candidate_ppo as ppo
    return policy, ppo


def support(policy):
    return policy.CandidateSupport('current goal', (policy.Candidate(3, 'constructor'),
        policy.Candidate(9, 'trivial'), policy.Candidate(10, 'bad')), (True, True, False))


class TinyTokenizer:
    eos_token_id = 1
    def apply_chat_template(self, messages, **kwargs): return messages[0]['content']
    def __call__(self, text, **kwargs): return dict(input_ids=[2] * len(text.split()))


class CandidateContracts(unittest.TestCase):
    def test_only_current_observation_and_actions_reach_policy(self):
        p, _ = modules()
        menu = dict(status='ready', observation='current goal', actions=[dict(id=3, tactic='trivial')],
                    probes=[dict(reward=1, proof='hidden')], owner=123)
        frozen = p.CandidateSupport.from_menu(menu)
        menu['actions'][0]['tactic'] = 'mutated'
        self.assertEqual(frozen.actions[0].tactic, 'trivial')
        self.assertEqual(set(frozen.__dict__), {'observation', 'actions', 'mask'})
        with self.assertRaises(ValueError):
            p.CandidateSupport('goal', ())
        with self.assertRaises(ValueError):
            p.CandidateSupport('goal', frozen.actions, (False,))

    def test_terminal_returns_and_explicit_truncation(self):
        p, _ = modules()
        s, d = support(p), p.Decision(0, -math.log(2), .25)
        first = p.Transition(s, d, 0., False, False)
        terminal = p.Transition(s, d, 1., True, False)
        episode = p.Episode((first, terminal), 'accepted', True)
        self.assertEqual(p.discounted_returns(episode, .5), [.5, 1.])
        truncated = p.Episode((p.Transition(s, d, 0., False, True, next_value=.8),), 'horizon', True)
        self.assertEqual(p.discounted_returns(truncated, .5), [.4])
        crash = p.Episode((p.Transition(s, d, 0., True, False, censored=True),), 'crash', True)
        for bad in [crash, p.Episode((first,), 'resource_cap', False),
                    p.Episode((first,), 'unfinished', True),
                    p.Episode((p.Transition(s, d, 1., True, False, next_value=.8),), 'accepted', True)]:
            with self.assertRaises(ValueError):
                p.discounted_returns(bad)
        self.assertEqual(p.discounted_returns(p.Episode((), 'empty', True)), [])
        for status in ('crash', 'replay_crash', 'timeout', 'wall_cap', 'scoring_token_cap', 'lean_call_cap'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                p.validate_episode(p.Episode((terminal,), status, True))

    def test_config_rejects_missing_timeouts_and_invalid_bounds_before_loading(self):
        _, ppo = modules()
        config = dict(seed=7, max_episodes=4, episodes_per_update=2, max_candidates=2, horizon=2,
            max_lean_calls=50, max_scoring_tokens=1000, max_wall_seconds=30, max_prompt_tokens=8,
            max_action_tokens=4, learning_rate=.01, lean='pin', mathlib='revision', experiment_id='EXP',
            run_id='run', interaction_contract='candidate_selection_v1', split='train', num_ppo_epochs=1)
        with self.assertRaises(ValueError): ppo.validate_candidate_config(config, 'ppo')
        config.update(lean_timeout_seconds=1, lean_setup_timeout_seconds=2)
        ppo.validate_candidate_config(config, 'ppo')
        for field, value in [('max_candidates', 0), ('episodes_per_update', 1), ('learning_rate', float('nan')),
                             ('horizon', True), ('max_wall_seconds', -1), ('num_ppo_epochs', 0), ('gamma', 2)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                ppo.validate_candidate_config(dict(config, **{field: value}), 'ppo')
        for baseline in ('uniform', 'frozen_lm'):
            baseline_config = {key: value for key, value in config.items()
                               if key not in ('learning_rate', 'num_ppo_epochs')}
            ppo.validate_candidate_config(baseline_config, baseline)

    def test_clipping_value_targets_and_negative_advantage(self):
        _, ppo = modules()
        self.assertAlmostEqual(ppo.clipped_surrogate(math.log(2), 0., 1., .2), 1.2)
        self.assertAlmostEqual(ppo.clipped_surrogate(math.log(.5), 0., -1., .2), -.8)
        self.assertAlmostEqual(ppo.clipped_surrogate(math.log(.5), 0., 1., .2), .5)


@unittest.skipIf(torch is None, 'torch is absent from this interpreter')
class CandidateTensors(unittest.TestCase):
    def policy(self, p):
        class Tiny(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.logits = torch.nn.Parameter(torch.tensor([0., 0., 99.]))
                self.critic = torch.nn.Parameter(torch.tensor(.25))
                self.observed = []
            def evaluate(self, s):
                self.observed.append(s)
                return self.logits, self.critic
            def value(self, observation):
                return self.critic
        return Tiny()

    def test_sampling_update_mask_and_log_probability_agree(self):
        p, _ = modules()
        s, model = support(p), self.policy(p)
        dist = p.masked_distribution(model.logits, s.mask)
        self.assertEqual(dist.probs[-1].item(), 0.)
        decision = p.sample_decision(model, s)
        self.assertIn(decision.action_index, [0, 1])
        self.assertAlmostEqual(decision.old_log_prob, dist.log_prob(torch.tensor(decision.action_index)).item())
        with self.assertRaises(ValueError):
            p.masked_distribution(model.logits, (False, False, False))

    def test_actor_and_critic_update_from_frozen_menu(self):
        p, ppo = modules()
        s, model = support(p), self.policy(p)
        decision = p.Decision(0, -math.log(2), .25)
        episode = p.Episode((p.Transition(s, decision, 1., True, False),), 'accepted', True)
        optimizer = torch.optim.SGD(model.parameters(), lr=.1)
        before = model.logits.detach().clone()
        stats = ppo.update_ppo(model, optimizer, [episode], epochs=2, target_kl=1.)
        self.assertGreater(model.logits[0].item(), before[0].item())
        self.assertLess(model.logits[1].item(), before[1].item())
        self.assertEqual(model.logits[2].item(), before[2].item())
        self.assertGreater(model.critic.item(), .25)
        self.assertEqual(stats['updates'], 2)
        self.assertTrue(all(item is s for item in model.observed))
        for bad in [p.Episode(episode.transitions, 'cap', False),
                    p.Episode((p.Transition(s, decision, 0., True, False, censored=True),), 'crash', True)]:
            with self.assertRaises(ValueError):
                ppo.update_ppo(model, optimizer, [bad])

    def test_old_log_probability_drift_refuses_update(self):
        p, ppo = modules()
        model, s = self.policy(p), support(p)
        episode = p.Episode((p.Transition(s, p.Decision(0, -99., .25), 1., True, False),), 'accepted', True)
        with self.assertRaisesRegex(ValueError, 'log.prob'):
            ppo.update_ppo(model, torch.optim.SGD(model.parameters(), lr=.1), [episode])

    def test_collector_uses_terminal_reward_and_preserves_empty_failure(self):
        p, _ = modules()
        model = self.policy(p)
        class Env:
            counts = dict(lean_calls=0)
            steps_used = 0
            def reset(self): return 'current goal'
            def candidate_menu(self, candidates, **kwargs):
                return dict(status='ready', observation='current goal',
                            actions=[dict(id=3, tactic='trivial'), dict(id=9, tactic='constructor'),
                                     dict(id=10, tactic='other')], probes=[dict(reward=999)])
            def select_candidate(self, menu, choice):
                self.steps_used += 1
                return dict(status='accepted', reward=1., done=True, accepted=True)
            def prompt_state(self): return 'next goal'
        env = Env()
        episode = p.collect_episode(env, model, ['trivial'], max_candidates=3,
                                    max_lean_calls=50, max_steps=2)
        self.assertEqual(episode.status, 'accepted')
        self.assertEqual(episode.transitions[0].reward, 1.)
        self.assertTrue(episode.transitions[0].terminated)
        env.candidate_menu = lambda *a, **k: dict(status='empty', actions=[])
        self.assertEqual(p.collect_episode(env, model, ['trivial'], max_candidates=3,
            max_lean_calls=50, max_steps=2), p.Episode((), 'empty', True))

    def test_task_horizon_terminates_and_external_interruption_is_incomplete(self):
        p, _ = modules()
        model = self.policy(p)
        class Env:
            counts = dict(lean_calls=0)
            def reset(self): return 'current goal'
            def candidate_menu(self, *a, **k):
                return dict(status='ready', observation='current goal', actions=[
                    dict(id=i, tactic=str(i)) for i in range(3)])
            def select_candidate(self, *a):
                return dict(status='changed', done=False, accepted=False, reward=5.)
            def prompt_state(self): return 'next goal'
        env = Env()
        kwargs = dict(max_candidates=3, max_lean_calls=50, max_steps=1)
        episode = p.collect_episode(env, model, ['x'], **kwargs)
        self.assertTrue(episode.transitions[-1].terminated)
        self.assertFalse(episode.transitions[-1].truncated)
        self.assertEqual(episode.transitions[-1].next_value, 0.)
        self.assertEqual(episode.transitions[-1].reward, 0.)
        env.select_candidate = lambda *a: dict(status='crash', done=True, accepted=False, censored=True)
        self.assertFalse(p.collect_episode(env, model, ['x'], **kwargs).completed)
        env.counts['lean_calls'] = 49
        self.assertFalse(p.collect_episode(env, model, ['x'], **kwargs).completed)


    def test_real_tiny_lm_updates_backbone_and_accounts_all_forward_tokens(self):
        from transformers import Qwen2Config, Qwen2ForCausalLM
        p, ppo = modules()
        torch.set_num_threads(1)
        torch.manual_seed(7)
        class Tokenizer:
            eos_token_id = 1
            def apply_chat_template(self, messages, **kwargs): return messages[0]['content']
            def __call__(self, text, **kwargs):
                return dict(input_ids=[2, 3] if text == 'current goal' else [4 if text == 'a' else 5])
        config = Qwen2Config(vocab_size=8, hidden_size=8, intermediate_size=16, num_hidden_layers=1,
                            num_attention_heads=2, num_key_value_heads=1, max_position_embeddings=32)
        lm = Qwen2ForCausalLM(config)
        budget = p.ScoringBudget(1000, 30)
        policy = p.TorchCandidatePolicy(lm, Tokenizer(), max_prompt_tokens=4, max_action_tokens=3, budget=budget)
        menu = p.CandidateSupport('current goal', (p.Candidate(0, 'a'), p.Candidate(1, 'b')))
        decision = p.sample_decision(policy, menu)
        self.assertEqual(budget.counts['scoring_tokens'], 10)
        self.assertEqual(budget.counts['scoring_forward_calls'], 3)
        before = lm.model.embed_tokens.weight.detach().clone()
        episode = p.Episode((p.Transition(menu, decision, 1., True, False),), 'accepted', True)
        ppo.update_ppo(policy, torch.optim.SGD(policy.parameters(), lr=.01), [episode], epochs=1)
        self.assertFalse(torch.equal(before, lm.model.embed_tokens.weight))
        self.assertEqual(budget.counts['scoring_tokens'], 20)
        self.assertEqual(budget.counts['proposal_tokens'], 0)
        capped = p.ScoringBudget(1, 30)
        with self.assertRaises(p.WorkCapReached): capped.reserve(2)
        self.assertEqual(capped.counts['scoring_tokens'], 0)

    def test_training_keeps_failed_and_censored_records_without_updating(self):
        from tempfile import TemporaryDirectory
        import json
        p, ppo = modules()
        policy = self.policy(p)
        optimizer = torch.optim.SGD(policy.parameters(), lr=.1)
        class Env:
            counts = dict(lean_calls=0)
            def reset(self): return 'current goal'
            def candidate_menu(self, *a, **k): return dict(status='empty', actions=[])
            def close(self): pass
        settings = dict(max_episodes=2, episodes_per_update=2, max_candidates=3, max_lean_calls=50,
                        horizon=2, gamma=1., num_ppo_epochs=1)
        with TemporaryDirectory() as directory:
            report = ppo.run_training(policy, optimizer, lambda task: Env(), ['task'], ['x'],
                                      settings, Path(directory))
            self.assertEqual(report['episodes'], 2)
            rows = [json.loads(line) for line in (Path(directory) / 'episodes.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row['status'] == 'empty' for row in rows))
            self.assertEqual(report['optimizer_updates'], 0)
        class Crash(Env):
            def candidate_menu(self, *a, **k): return dict(status='crash', censored=True, actions=[])
        with TemporaryDirectory() as directory:
            report = ppo.run_training(policy, optimizer, lambda task: Crash(), ['task'], ['x'],
                                      settings, Path(directory))
            self.assertEqual(report['status'], 'stopped')
            self.assertEqual(report['optimizer_updates'], 0)
            rows = [json.loads(line) for line in (Path(directory) / 'episodes.jsonl').read_text().splitlines()]
            self.assertFalse(rows[0]['completed'])

    def test_uniform_and_frozen_lm_collect_without_updates_and_keep_probe_costs(self):
        from tempfile import TemporaryDirectory
        import json
        p, ppo = modules()
        budget = p.ScoringBudget(1000, 30)
        uniform = p.UniformCandidatePolicy(TinyTokenizer(), max_prompt_tokens=8, max_action_tokens=4, budget=budget)
        menu = support(p)
        logits, value = uniform.evaluate(menu)
        distribution = p.masked_distribution(logits, menu.mask)
        self.assertTrue(torch.equal(distribution.probs, torch.tensor([.5, .5, 0.])))
        self.assertEqual(value.item(), 0.)
        self.assertEqual(budget.counts['scoring_tokens'], 0)
        frozen = self.policy(p)
        before = {key: value.detach().clone() for key, value in frozen.state_dict().items()}
        settings = dict(max_episodes=2, episodes_per_update=2, max_candidates=2,
                        max_lean_calls=100, horizon=2)
        def forbidden_update(*args, **kwargs):
            self.fail('A no-update baseline invoked an optimizer')
        for algorithm, policy in [('uniform', uniform), ('frozen_lm', frozen)]:
            counts = dict(lean_calls=0)
            class Env:
                def __init__(self): self.counts = counts
                def reset(self): counts['lean_calls'] += 2
                def candidate_menu(self, candidates, **kwargs):
                    counts['lean_calls'] += len(candidates)
                    return dict(status='ready', observation='current goal', actions=[
                        dict(id=i, tactic=tactic) for i, tactic in enumerate(candidates)])
                def select_candidate(self, *args):
                    counts['lean_calls'] += 1
                    return dict(status='accepted', done=True, accepted=True)
                def close(self): pass
            # The tiny LM fixture has three logits, matching this three-entry menu.
            candidates = ['a', 'b', 'c'] if algorithm == 'frozen_lm' else ['a', 'b']
            local_settings = dict(settings, max_candidates=len(candidates))
            with self.subTest(algorithm=algorithm), TemporaryDirectory() as directory:
                report = ppo.run_training(policy, None, lambda task: Env(), ['task'], candidates,
                    local_settings, Path(directory), algorithm=algorithm, update_fn=forbidden_update, counts=counts)
                self.assertEqual(report['optimizer_updates'], 0)
                self.assertEqual(report['episodes'], 2)
                self.assertEqual(counts['lean_calls'], 2 * (3 + len(candidates)))
                rows = [json.loads(line) for line in (Path(directory) / 'episodes.jsonl').read_text().splitlines()]
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(row['completed'] for row in rows))
                metrics = [json.loads(line) for line in (Path(directory) / 'steps.jsonl').read_text().splitlines()]
                self.assertEqual(metrics[0]['updates'], 0)
        self.assertTrue(all(torch.equal(before[key], value) for key, value in frozen.state_dict().items()))
        self.assertTrue(all(parameter.grad is None for parameter in frozen.parameters()))
        self.assertEqual(budget.counts['proposal_tokens'], 0)

    def test_uniform_and_lm_share_token_admission_without_truncation_or_forwards(self):
        from types import SimpleNamespace
        p, _ = modules()
        class NoForwardLM(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.weight = torch.nn.Parameter(torch.zeros(1))
                self.config = SimpleNamespace(hidden_size=1)
            def forward(self, *args, **kwargs): raise AssertionError('Model forward occurred before token admission')
        tokenizer = TinyTokenizer()
        for kind in ('uniform', 'lm'):
            budget = p.ScoringBudget(1000, 30)
            kwargs = dict(max_prompt_tokens=2, max_action_tokens=2, budget=budget)
            policy = p.UniformCandidatePolicy(tokenizer, **kwargs) if kind == 'uniform' else \
                p.TorchCandidatePolicy(NoForwardLM(), tokenizer, **kwargs)
            for observation, text in [('observation is oversized', 'valid'), ('current goal', 'oversized action')]:
                menu = p.CandidateSupport(observation, (p.Candidate(0, text),))
                with self.subTest(kind=kind, text=text), self.assertRaisesRegex(ValueError, 'token cap'):
                    policy.evaluate(menu)
            self.assertEqual(budget.counts['scoring_tokens'], 0)
            self.assertEqual(budget.counts['scoring_forward_calls'], 0)

    def test_uniform_cli_loads_tokenizer_without_weights_or_baseline_checkpoint(self):
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import patch
        import json
        import run_tactic_rl
        import prepare_grpo_pilot
        import bench_lean_verifier
        _, ppo = modules()
        with TemporaryDirectory() as directory:
            base = Path(directory)
            config = dict(seed=7, max_episodes=2, episodes_per_update=2, max_candidates=2, horizon=2,
                max_lean_calls=50, max_scoring_tokens=1000, max_wall_seconds=30, max_prompt_tokens=8,
                max_action_tokens=4, lean='pin', mathlib='revision', experiment_id='EXP', run_id='run',
                interaction_contract='candidate_selection_v1', split='train',
                lean_timeout_seconds=1, lean_setup_timeout_seconds=2)
            files = dict(config=json.dumps(config), tasks=json.dumps(dict(task_id='task')) + '\n',
                         pool='{}\n', candidates='["trivial", "constructor"]', exclude_names='')
            for name, text in files.items(): (base / name).write_text(text)
            (base / 'environment/mathlib').mkdir(parents=True)
            (base / 'environment/mathlib/lean-toolchain').write_text('pin')
            args = SimpleNamespace(**{name: base / name for name in files}, baseline='uniform', model=base,
                checker=base / 'checker', environment=base / 'environment', repl=base / 'repl',
                mathlib_identity=base / 'identity', output=base / 'out', device='cpu')
            ctx = SimpleNamespace(pool_repls=[])
            report = dict(status='complete', optimizer_updates=0)
            with patch('argparse.ArgumentParser.parse_args', return_value=args), \
                    patch.object(run_tactic_rl, 'training_tasks', return_value=[dict(task_id='task')]), \
                    patch.object(run_tactic_rl, 'mathlib_identity', return_value={}), \
                    patch.object(prepare_grpo_pilot, 'load_checker', return_value=SimpleNamespace(LEAN=base)), \
                    patch.object(bench_lean_verifier, 'repl_context', return_value=ctx), \
                    patch('subprocess.run', return_value=SimpleNamespace(stdout='4.10.0-rc1')), \
                    patch('transformers.AutoModelForCausalLM.from_pretrained', side_effect=AssertionError('weights loaded')), \
                    patch('transformers.AutoTokenizer.from_pretrained', return_value=TinyTokenizer()) as tokenizer, \
                    patch.object(ppo, 'run_training', return_value=report) as run:
                result = ppo.main()
            self.assertEqual(run.call_args.kwargs['algorithm'], 'uniform')
            tokenizer.assert_called_once_with(args.model, local_files_only=True)
            self.assertIsNone(run.call_args.args[1])
            self.assertEqual(result['optimizer_updates'], 0)
            for name in ('final', 'critic.pt', 'optimizer.pt'):
                self.assertFalse((args.output / name).exists())


if __name__ == '__main__':
    unittest.main()
