import json
import re
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/train'))
sys.path.insert(0, str(ROOT / 'scripts/data'))
from build_v21_sft import is_automation, merge_continuations, rl_tactics  # noqa: E402
from run_tactic_rl import parse_action, render_observation  # noqa: E402

HEADER = 'theorem t1 (n : ℕ) (h : n % 2 = 1) : n ^ 2 % 2 = 1'
STATE = 'n : ℕ\nh : n % 2 = 1\n⊢ n * n % 2 = 1'
BODY = '  rw [pow_two]\n  have h2 : n * n % 2 = 1 := by\n    rw [Nat.mul_mod, h]\n  exact h2'


def write(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))


def build(directory, tasks, aug, extra=(), pool=None, **options):
    d = Path(directory)
    (d / 'splits').mkdir()
    (d / 'splits/dev.txt').write_text('dev_name\n')
    (d / 'splits/excluded_from_training.txt').write_text('')
    pool = pool or [dict(name='t1', split='train', formal_statement=HEADER + ' := by sorry'),
                    dict(name='dev_name', split='train', formal_statement='theorem dev_name : True := by sorry')]
    for name, rows in (('pool', pool), ('tasks', tasks), ('aug', aug)):
        write(d / (name + '.jsonl'), rows)
    command = [sys.executable, str(ROOT / 'scripts/data/build_v21_sft.py'), '--pool', str(d / 'pool.jsonl'),
               '--splits', str(d / 'splits'), '--tasks', str(d / 'tasks.jsonl'), '--aug', str(d / 'aug.jsonl'),
               '--output', str(d / 'out'), '--val-percent', '0', *extra]
    subprocess.run(command, check=True, capture_output=True)
    rows = [json.loads(line) for line in (d / 'out/sft_train.jsonl').read_text().splitlines()]
    for r in rows:  # The target opens with exactly ```lean and ends at the closing fence: generation stops there.
        assert re.fullmatch(r'```lean\n[^`]+\n```', r['messages'][1]['content']), r['messages'][1]['content']
        assert r['messages'][1]['role'] == 'assistant' and len(r['messages']) == 2
    return rows


def task(kind='prefix_top', name='t1', given='  rw [pow_two]\n', rest='  rw [Nat.mul_mod, h]'):
    return dict(task_id=f'{name}:{kind}:1', name=name, kind=kind, header=HEADER if name == 't1' else 'theorem dev_name : True',
                given=given, state=STATE, reference_rest=rest)


class BuildV21SFTTest(unittest.TestCase):
    def test_rows_match_the_rl_observation_and_exclude_dev_and_prefix_have(self):
        with TemporaryDirectory() as directory:
            tasks = [task(), task(kind='prefix_have'), task(name='dev_name', given='  skip\n', rest='  trivial')]
            aug = [dict(name='t1', k=1, text='  rw [pow_two]\n  omega', state=STATE)]
            rows = build(directory, tasks, aug, ['--supplied-share', '0', '--sources', 'tasks,aug'])
            self.assertEqual({r['theorem'] for r in rows}, {'t1'})
            self.assertEqual({r['source'] for r in rows}, {'nt_rl_tasks_v3:prefix_top', 'nt_aug_v1'})
            by_source = {r['source']: r for r in rows}
            # RL shows a task's `given` as supplied lines and no policy steps.
            supplied = render_observation(dict(kind='prefix_top', header=HEADER, given='  rw [pow_two]\n'), [], [STATE], '', [], 6)
            self.assertEqual(by_source['nt_rl_tasks_v3:prefix_top']['messages'][0]['content'], supplied)
            self.assertEqual(by_source['nt_rl_tasks_v3:prefix_top']['messages'][1]['content'], '```lean\nrw [Nat.mul_mod, h]\n```')
            # The augmentation row shows its earlier step as a policy step: 1 of 6 actions used.
            shown = render_observation(dict(kind='whole', header=HEADER, given=''), ['rw [pow_two]'], [STATE], '', [], 5)
            self.assertEqual(by_source['nt_aug_v1']['messages'][0]['content'], shown)
            self.assertEqual(by_source['nt_aug_v1']['messages'][1]['content'], '```lean\nomega\n```')

    def test_aug_target_is_the_single_closing_tactic_at_the_cut(self):
        with TemporaryDirectory() as directory:
            rows = build(directory, [], [dict(name='t1', k=2, text=BODY, state=STATE)], ['--sources', 'aug'])
            self.assertEqual([r['messages'][1]['content'] for r in rows], ['```lean\nexact h2\n```'])
            self.assertEqual(rows[0]['steps_done'], 2)

    def test_supplied_share_one_moves_the_steps_into_supplied_lines(self):
        with TemporaryDirectory() as directory:
            rows = build(directory, [], [dict(name='t1', k=2, text=BODY, state=STATE)],
                         ['--sources', 'aug', '--supplied-share', '1'])
            given = '  rw [pow_two]\n  have h2 : n * n % 2 = 1 := by\n    rw [Nat.mul_mod, h]\n'
            expected = render_observation(dict(kind='prefix_top', header=HEADER, given=given), [], [STATE], '', [], 6)
            self.assertEqual(rows[0]['messages'][0]['content'], expected)

    def test_trailing_combinator_chain_becomes_one_tactic_that_parse_action_keeps(self):
        rest = '  rcases h with (h | h) <;>\n  simp [h]\n  exact foo'
        with TemporaryDirectory() as directory:
            rows = build(directory, [task(rest=rest)], [], ['--sources', 'tasks'])
            target = rows[0]['messages'][1]['content']
            self.assertEqual(target, '```lean\nrcases h with (h | h) <;>\n  simp [h]\nexact foo\n```')
            action = parse_action(target, 4)
            self.assertEqual(action['tactics'], ['rcases h with (h | h) <;>\n  simp [h]', 'exact foo'])
            self.assertEqual(rows[0]['target_tactics'], action['tactics'])

    def test_merge_and_roundtrip_helpers(self):
        self.assertEqual(merge_continuations(['a <;>', 'b', 'c']), ['a <;>\n  b', 'c'])
        self.assertEqual(rl_tactics('  rw [a]\n  simp'), ['rw [a]', 'simp'])
        self.assertTrue(is_automation(['omega']))
        self.assertFalse(is_automation(['omega', 'simp']))
        self.assertFalse(is_automation(['rw [a]']))

    def test_source_cuts_give_multi_tactic_targets_and_automation_is_capped(self):
        with TemporaryDirectory() as directory:
            d = Path(directory)
            proof = HEADER + ' := by\n' + BODY
            write(d / 'sft.jsonl', [dict(theorem='t1', messages=[dict(role='user', content='x'),
                                                                 dict(role='assistant', content='```lean\n' + proof + '\n```')])])
            write(d / 'cuts.jsonl', [dict(name='t1', k=0, source_row=0, steps_source=3, state=STATE, reason=None),
                                     dict(name='t1', k=1, source_row=0, steps_source=3, state=STATE, reason=None)])
            aug = [dict(name='t1', k=1, text='  rw [pow_two]\n  ' + t, state=STATE) for t in ('omega', 'aesop', 'simp_all')]
            rows = build(directory, [], aug, ['--sources', 'aug,cuts', '--cuts', str(d / 'cuts.jsonl'),
                                              '--sft-train', str(d / 'sft.jsonl'), '--auto-per-problem', '1'])
            targets = sorted(r['messages'][1]['content'] for r in rows)
            self.assertEqual(sum(r['automation'] for r in rows), 1)
            self.assertIn('```lean\nrw [pow_two]\nhave h2 : n * n % 2 = 1 := by\n  rw [Nat.mul_mod, h]\nexact h2\n```', targets)
            self.assertIn('```lean\nhave h2 : n * n % 2 = 1 := by\n  rw [Nat.mul_mod, h]\nexact h2\n```', targets)
            for r in rows:
                self.assertEqual(parse_action(r['messages'][1]['content'], 4)['tactics'], r['target_tactics'])


if __name__ == '__main__':
    unittest.main()
