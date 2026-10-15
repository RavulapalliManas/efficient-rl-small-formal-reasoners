"""Pure-Python tests of the arm C band sampler (no torch, no Lean)."""
import json
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "train"))

from band_sampler import BandSampler, simulate  # noqa: E402


def make_tasks(per_family):
    return [dict(task_id=f"{f}_{i}", family=f, stage=1) for f, n in per_family.items() for i in range(n)]


def run(sampler, p_by_family, steps, env_seed=0):
    env, picks = random.Random(env_seed), []
    for _ in range(steps):
        task = sampler.next_task()
        p = p_by_family[task["family"]]
        sampler.observe(task, sum(env.random() < p for _ in range(sampler.G)))
        picks.append(task["task_id"])
    return picks


class BandSamplerTest(unittest.TestCase):
    def test_deterministic_for_fixed_seed(self):
        tasks = make_tasks({"a": 3, "b": 3, "c": 3})
        p = dict(a=0.1, b=0.5, c=0.9)
        self.assertEqual(run(BandSampler(tasks, seed=7), p, 60), run(BandSampler(tasks, seed=7), p, 60))
        self.assertNotEqual(run(BandSampler(tasks, seed=7), p, 60), run(BandSampler(tasks, seed=8), p, 60))

    def test_cap_holds(self):
        tasks = make_tasks({"a": 4, "b": 4, "c": 4, "d": 4})
        sampler = BandSampler(tasks, seed=1, cap_ratio=1.5)
        env, p = random.Random(1), dict(a=0.0, b=0.0, c=0.5, d=0.5)
        for _ in range(300):
            task = sampler.next_task()
            sampler.observe(task, sum(env.random() < p[task["family"]] for _ in range(8)))
            for visits in sampler.family_visits.values():
                self.assertLessEqual(visits, 1.5 * sampler.groups / 4 + 1)

    def test_zero_probability_family_is_deprioritised(self):
        tasks = make_tasks({"dead": 3, "live": 3})
        sampler = BandSampler(tasks, seed=3, cap_ratio=10)
        picks = run(sampler, dict(dead=0.0, live=0.5), 300)
        late = [t.split("_")[0] for t in picks[50:]]
        self.assertLess(late.count("dead"), 0.2 * late.count("live"))

    def test_least_visited_rotation(self):
        tasks = make_tasks({"only": 4})
        sampler = BandSampler(tasks, seed=0)
        for _ in range(3):
            seen = []
            for _ in range(4):
                task = sampler.next_task()
                seen.append(task["task_id"])
                sampler.observe(task, 4)
            self.assertEqual(sorted(seen), sorted(t["task_id"] for t in tasks))

    def test_observe_bounds(self):
        sampler = BandSampler(make_tasks({"a": 1}), group_size=8)
        task = sampler.next_task()
        for bad in (-1, 9, 2.5, True):
            with self.assertRaises(ValueError):
                sampler.observe(task, bad)
        self.assertEqual(sampler.groups, 0)
        sampler.observe(task, 0)
        sampler.observe(task, 8)
        self.assertEqual(sampler.posterior["a"], [1 + 4, 1 + 4])

    def test_held_out_ids_raise(self):
        for bad in ("ho_1", "ho2_4", "iv_3"):
            with self.assertRaises(ValueError):
                BandSampler([dict(task_id=bad, family="a", stage=1)])
        # A training theorem whose name merely starts with "ho" is allowed.
        BandSampler([dict(task_id="horner_rule:whole:0", family="a", stage=1),
                     dict(task_id="hom_comp:whole:0", family="a", stage=1)])

    def test_state_is_json_serialisable(self):
        sampler = BandSampler(make_tasks({"a": 2, "b": 2}))
        sampler.observe(sampler.next_task(), 3)
        state = json.loads(json.dumps(sampler.state()))
        self.assertEqual(state["groups"], 1)
        self.assertAlmostEqual(sum(state["shares"].values()), 1.0)

    def test_beats_round_robin_on_planted_population(self):
        tasks = make_tasks({"dead": 4, "mid": 4, "easy": 4})
        p = {t["task_id"]: dict(dead=0.0, mid=0.5, easy=0.95)[t["family"]] for t in tasks}
        band = [simulate(tasks, p, 120, s) for s in range(20)]
        rr = [simulate(tasks, p, 120, s, policy="round_robin") for s in range(20)]
        self.assertGreaterEqual(sum(band) / 20, sum(rr) / 20)


if __name__ == "__main__":
    unittest.main()
