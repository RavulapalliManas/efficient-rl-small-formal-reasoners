"""Pretraining contracts: mathematical conditions, information masks, and exposure."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys
import json
import tempfile
import hashlib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class MathematicalRLPChecks(unittest.TestCase):
    def test_registered_checkpoint_rejects_changed_weights(self):
        from run_rlp_pilot import controlled_checkpoint
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'model.safetensors').write_bytes(b'early weights')
            cfg = {'initial_checkpoint':'policy/checkpoint-118', 'revision':'revision',
                   'initial_weights_sha256':hashlib.sha256(b'early weights').hexdigest()}
            (root/'checkpoint_ready.json').write_text(json.dumps({'s3':'s3://bucket/policy/checkpoint-118/', 'optimizer_steps':118}))
            (root/'acquisition_manifest.json').write_text(json.dumps({'optimizer_steps':118,'config':{'revision':'revision'}}))
            self.assertEqual(controlled_checkpoint(root,cfg)['optimizer_steps'],118)
            (root/'model.safetensors').write_bytes(b'wrong weights')
            with self.assertRaisesRegex(ValueError,'weights'):
                controlled_checkpoint(root,cfg)

    def test_prompt_cap_uses_tokens_not_dictionary_keys(self):
        from run_rlp_pilot import controlled_positions
        class Tokenizer:
            def apply_chat_template(self, messages, return_dict=True, **kwargs):
                return {'input_ids': list(range(10))} if return_dict else list(range(10))
            def encode(self, text, **kwargs):
                return [1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'private').mkdir()
            row = {'id':'x','public':{'prompt':'test'},'example_id':'x','template_family':'x',
                   'curriculum_stage':0,'replay':False,'view_id':'full','family_id':'x','repeat_index':0}
            (root / 'train.jsonl').write_text(json.dumps(row)+'\n')
            (root / 'private/train_references.jsonl').write_text(json.dumps({'id':'x','target_text':'answer'})+'\n')
            with self.assertRaisesRegex(ValueError, 'exceeds cap'):
                controlled_positions(root, 'train', Tokenizer(), 5)

    def test_matched_views_and_split_isolation(self):
        from mathematical_rlp_data import build_study
        study = build_study()
        ordered, shuffled = study['ordered'], study['shuffled']
        self.assertEqual(Counter(r['id'] for r in ordered), Counter(r['id'] for r in shuffled))
        self.assertNotEqual([r['id'] for r in ordered], [r['id'] for r in shuffled])
        splits = {s: {r['family_id'] for r in study['records'] if r['split'] == s}
                  for s in ('train', 'validation', 'heldout')}
        self.assertFalse(splits['train'] & splits['validation'])
        self.assertFalse(splits['train'] & splits['heldout'])
        self.assertFalse(splits['validation'] & splits['heldout'])
        for r in ordered:
            self.assertNotIn('reference_steps', r['public'])
            self.assertNotIn('target_text', r['public'])
            self.assertIn('Give a complete answer', r['public']['prompt'])
            if r['view_id'] == 'independent':
                self.assertNotIn('Knowledge:', r['public']['prompt'])
                self.assertNotIn('Method:', r['public']['prompt'])

    def test_reference_conditions_and_false_target(self):
        from mathematical_rlp_data import build_study, validate_record
        study = build_study()
        for record in study['records']:
            self.assertTrue(validate_record(record))
        inverse = next(r for r in study['records'] if r['template_family'] == 'inverse'
                       and not r['parameters']['exists'])
        damaged = deepcopy(inverse)
        damaged['parameters']['exists'] = True
        self.assertFalse(validate_record(damaged))
        self.assertTrue(any(r['template_family'] == 'division' and r['parameters']['a'] < 0
                            for r in study['records']))

    def test_generation_and_scoring_contract(self):
        from run_rlp_pilot import complete_messages, span_scoring_messages
        context = 'Prove the stated implication. Give a complete answer.'
        public = complete_messages(context)
        self.assertEqual(public, [{'role': 'user', 'content': context}])
        scored = span_scoring_messages(context, 'A complete candidate proof.')
        self.assertEqual(scored[0], public[0])
        self.assertNotIn('boxed', str(scored))
        self.assertNotIn('SECRET_REFERENCE', str(scored))
        self.assertIn('A complete candidate proof.', str(scored))


if __name__ == '__main__':
    unittest.main()
