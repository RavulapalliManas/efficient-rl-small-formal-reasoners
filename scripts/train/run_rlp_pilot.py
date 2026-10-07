"""RLP acquisition through TRL, one L40S, no supervised proof objective.

Memory: 0.5B parameters x20 bytes for policy/optimizer <10 GB;
FP32 EMA teacher <2 GB; scorer logits <=3072x151936x4 <1.87 GB.
CUDA cap 24 GiB; host service cap 16 GiB. No Lean process during training.
Bounded adaptation of RLP equations 5-8: short contexts and sampled positions,
chat-conditioned thoughts, explicit thought delimiters, full-parameter updates.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import random
import re
import signal
import subprocess
import time


def information_gain(reasoned_logp, baseline_logp):
    return reasoned_logp - baseline_logp


def corrected_reward(reward, group_size):
    if group_size < 2:
        raise ValueError("RLP requires multiple thoughts per context")
    return group_size / (group_size - 1) * reward


def thought_messages(context):
    return [{"role": "system", "content": "You are a mathematical assistant."},
            {"role": "user", "content": "Reason briefly about what mathematically follows from this textbook text. Write a useful reasoning chain, without inventing a different problem. The continuation is hidden.\n\nText:\n" + context}]


def primitive_messages(context):
    return [{'role': 'system', 'content': 'You are a mathematics tutor. Use the definitions and check the conditions of each operation.'},
            {'role': 'user', 'content': context + '\nWork through the task with concise mathematical reasoning.'}]


def complete_messages(context):
    """Same complete-response prompt as the standalone acquisition evaluator."""
    return [{'role': 'user', 'content': context}]


def span_scoring_messages(context, thought=''):
    # This private delimiter is never counted as policy-generated output.
    return complete_messages(context) + [{'role':'assistant',
        'content':thought + '\nCanonical solution:\n'}]


def controlled_positions(data, split, tokenizer, cap):
    public = [json.loads(line) for line in (data / (split+'.jsonl')).read_text().splitlines()]
    references = {r['id']:r for r in (json.loads(line) for line in
                  (data / 'private' / (split+'_references.jsonl')).read_text().splitlines())}
    if {r['id'] for r in public} != set(references):
        raise ValueError('Public/private IDs disagree')
    rows = []
    for row in public:
        prompt = row['public']['prompt']
        target = references[row['id']]['target_text']
        messages = complete_messages(prompt)
        prompt_length = len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=False))
        targets = tokenizer.encode(target, add_special_tokens=False)
        if prompt_length > cap or not targets:
            raise ValueError('Controlled prompt exceeds cap or target is empty')
        rows.append({'id':row['id'],'document_id':row['example_id'],'chapter':row['template_family'],
                     'context_ids':tokenizer.encode(prompt, add_special_tokens=False),'target_id':targets,
                     'prompt':messages,'curriculum_stage':row['curriculum_stage'],'replay':row['replay'],
                     'view_id':row['view_id'],'family_id':row['family_id'],'repeat_index':row['repeat_index']})
    return rows


def controlled_checkpoint(path, cfg):
    receipt = json.loads((path/'checkpoint_ready.json').read_text())
    manifest = json.loads((path/'acquisition_manifest.json').read_text())
    if (not receipt['s3'].endswith('/'+cfg['initial_checkpoint'].strip('/')+'/')
            or receipt['optimizer_steps'] != cfg.get('initial_optimizer_steps',118)
            or manifest['optimizer_steps'] != cfg.get('initial_optimizer_steps',118)
            or manifest['config']['revision'] != cfg['revision']):
        raise ValueError('Initial checkpoint provenance differs from registered early RLP')
    digest = hashlib.sha256()
    with (path/'model.safetensors').open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != cfg['initial_weights_sha256']:
        raise ValueError('Initial weights differ from registered early RLP')
    return {'optimizer_steps':receipt['optimizer_steps'],'s3':receipt['s3'],
            'weights_sha256':digest.hexdigest()}


def boxed_integer(text):
    values = re.findall(r"\\boxed\{\s*(-?\d+)\s*\}", text)
    return int(values[-1]) if values else None


def due_milestones(training_tokens, milestones, completed):
    return [m for m in milestones if m <= training_tokens and m not in completed]


def main():
    ap = argparse.ArgumentParser()
    for name in ("config", "data", "output"):
        ap.add_argument("--" + name, type=Path, required=True)
    for name in ("initial-model", "initial-teacher", "parent-metadata"):
        ap.add_argument("--" + name, type=Path)
    ap.add_argument('--inspect-data', action='store_true')
    args = ap.parse_args()
    cfg = json.loads(args.config.read_text())
    controlled = cfg.get('target_mode') == 'controlled_mathematical_span'
    if args.inspect_data:
        if not controlled or args.initial_model is None:
            ap.error('--inspect-data requires controlled data and --initial-model')
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(str(args.initial_model), local_files_only=True)
        rows = controlled_positions(args.data, 'train', tokenizer, cfg['max_prompt_tokens'])
        validation = controlled_positions(args.data, 'validation', tokenizer, cfg['max_prompt_tokens'])
        result = {'training_presentations':len(rows), 'validation_items':len(validation),
                  'max_target_tokens':max(len(r['target_id']) for r in rows+validation),
                  'training_target_tokens':sum(len(r['target_id']) for r in rows),
                  'training_prompt_tokens':sum(len(tokenizer.apply_chat_template(r['prompt'],tokenize=True,add_generation_prompt=True,return_dict=False)) for r in rows),
                  'model_loaded':False,'optimizer_updates':0,'response_contract':'complete mathematical answer',
                  'private_scorer_delimiter':'Canonical solution:', 'training_authorized':cfg.get('training_authorized',False)}
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output/'inspection.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
        return
    if controlled and (not cfg.get('training_authorized',False) or args.initial_model is None):
        raise ValueError('Controlled run requires funded authorization and the registered early checkpoint')
    if controlled and args.initial_teacher:
        raise ValueError('Controlled teacher must initialize from the registered policy')
    identity = controlled_checkpoint(args.initial_model,cfg) if controlled else None
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    run = {"status": "starting", "config": cfg, "generated_tokens": 0,
           "score_forward_calls": 0, "rollouts": 0, "optimizer_steps": 0,
           "generation_wall_seconds": 0.0, "score_wall_seconds": 0.0,
           "training_wall_seconds": 0.0, "physical_lean_calls": 0,
           "active_gpu_seconds": None, "training_generated_tokens": 0,
           "evaluation_generated_tokens": 0, "training_prompt_tokens": 0,
           "evaluation_prompt_tokens": 0, "scorer_input_tokens": 0,
           "source_tokens": {}, "completed_token_milestones": [],
           "training_stages": {}, "unique_training_contexts": 0,
           "unique_training_documents": 0}
    if controlled:
        run['initial_identity'] = identity
    if args.parent_metadata:
        run['parent'] = json.loads(args.parent_metadata.read_text())
    parent_training_tokens = run.get('parent', {}).get('training_generated_tokens', 0)
    def save():
        run["wall_seconds"] = time.monotonic() - started
        tmp = args.output / "run.json.tmp"
        tmp.write_text(json.dumps(run, indent=2) + "\n")
        tmp.replace(args.output / "run.json")
    def deadline(signum, frame):
        raise TimeoutError("Registered RLP wall cap reached")
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(cfg["max_wall_seconds"])
    try:
        import torch
        import numpy as np
        from datasets import Dataset
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback, set_seed
        from trl import GRPOConfig, GRPOTrainer
        if importlib.metadata.version("trl") != cfg["trl_version"] or torch.cuda.device_count() != 1:
            raise RuntimeError("Registered TRL version and one CUDA GPU required")
        torch.cuda.set_per_process_memory_fraction(cfg["cuda_memory_gib"] * 1024**3 / torch.cuda.get_device_properties(0).total_memory)
        run["versions"] = {p: importlib.metadata.version(p) for p in ("torch", "transformers", "trl", "datasets")}
        run["data_manifest"] = json.loads((args.data / "manifest.json").read_text())
        run["data_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.data.glob("*.jsonl")}
        set_seed(cfg["seed"])
        initial = str(args.initial_model) if args.initial_model else cfg['checkpoint']
        revision = {} if args.initial_model else {'revision': cfg['revision']}
        tokenizer = AutoTokenizer.from_pretrained(initial, **revision)
        tokenizer.padding_side = "left"
        model = AutoModelForCausalLM.from_pretrained(initial, **revision, torch_dtype=torch.float32, attn_implementation="sdpa").to("cuda")
        if any(p.dtype != torch.float32 for p in model.parameters() if p.requires_grad):
            raise RuntimeError("RLP trainable parameters must stay FP32")
        run['precision'] = {'parameters': 'float32', 'policy_compute': 'bfloat16 autocast',
                            'teacher': 'float32', 'optimizer_moments': 'checked after first update'}
        teacher = AutoModelForCausalLM.from_pretrained(initial, **revision, torch_dtype=torch.float32, attn_implementation="sdpa").to("cuda")
        teacher.requires_grad_(False).eval()
        with torch.no_grad():
            for baseline, policy in zip(teacher.parameters(), model.parameters(), strict=True):
                baseline.copy_(policy.float())
        if args.initial_teacher:
            teacher.load_state_dict(torch.load(args.initial_teacher, map_location='cpu', weights_only=True))
        run['initial_policy'] = initial
        run['optimizer_initialization'] = 'fresh optimizer for the registered continuation stage'
        begin = tokenizer.encode("\n<reasoning>\n", add_special_tokens=False)
        end = tokenizer.encode("\n</reasoning>\nContinue the textbook text:\n", add_special_tokens=False)
        structured = cfg.get('target_mode') in ('mathematical_span','controlled_mathematical_span')
        def positions(split):
            if controlled:
                return controlled_positions(args.data, split, tokenizer, cfg['max_prompt_tokens'])
            pool = []
            rng = random.Random(cfg["seed"])
            run['source_tokens'][split] = 0
            for line in (args.data / (split + ".jsonl")).read_text().splitlines():
                row = json.loads(line)
                ids = tokenizer.encode(row["text"], add_special_tokens=False)
                run['source_tokens'][split] += len(ids)
                if structured:
                    context_ids = tokenizer.encode(row['prefix'], add_special_tokens=False)
                    target_ids = tokenizer.encode(row['target_text'], add_special_tokens=False)
                    messages = primitive_messages(row['prefix'])
                    if not target_ids or len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=False)) > cfg['max_prompt_tokens']:
                        raise RuntimeError('Structured target empty or prompt exceeds cap')
                    pool.append({'id': row['id'], 'document_id': row['id'], 'chapter': row['chapter'],
                                 'context_ids': context_ids, 'target_id': target_ids,
                                 'curriculum_stage': row['curriculum_stage'], 'prompt': messages,
                                 'replay': row['replay']})
                    continue
                for position in rng.sample(range(24, len(ids)), min(cfg["positions_per_block"], max(0, len(ids) - 24))):
                    context_ids = ids[max(0, position - cfg["context_tokens"]):position]
                    context = tokenizer.decode(context_ids, skip_special_tokens=True)
                    messages = thought_messages(context)
                    if len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=False)) > cfg["max_prompt_tokens"]:
                        raise RuntimeError("Acquisition prompt exceeds the registered token cap")
                    pool.append({"id": row["id"] + ":" + str(position), "document_id": row["id"],
                                 "chapter": row["chapter"], "context_ids": context_ids,
                                 "curriculum_stage": row.get('curriculum_stage', 3),
                                 "target_id": ids[position], "prompt": messages})
            if not structured:
                rng.shuffle(pool)
            if not structured and split == 'train' and cfg.get('curriculum') == 'primitive_structure_first':
                pool.sort(key=lambda q: q['curriculum_stage'])
            if structured and split == 'validation':
                families = {}
                for q in pool:
                    families.setdefault(q['chapter'], []).append(q)
                pool = [family[i] for i in range(max(map(len, families.values())))
                        for family in families.values() if i < len(family)]
            if not pool:
                raise ValueError("No token positions in " + split)
            return pool
        training = positions("train")
        if controlled and cfg['max_steps'] != len(training):
            raise ValueError('Controlled single-pass steps must equal presentation count')
        validation = positions("validation")[:cfg["validation_positions"]]
        with (args.output / "positions.jsonl").open("x") as out:
            for split, pool in (("train", training), ("validation", validation)):
                for q in pool:
                    out.write(json.dumps({"split": split, **q}, ensure_ascii=False) + "\n")
        run["positions"] = {"train": len(training), "validation": len(validation)}
        def score(network, input_ids, target):
            targets = target if isinstance(target, list) else [target]
            prefix_length = len(input_ids)
            input_ids = input_ids + targets[:-1]
            if len(input_ids) > cfg["max_prompt_tokens"] + cfg["max_completion_tokens"]:
                raise RuntimeError("Prediction context exceeds the scorer cap")
            if run["score_forward_calls"] >= cfg["max_score_forward_calls"]:
                raise RuntimeError("Prediction forward-call cap reached")
            mode = network.training
            network.eval()
            torch.cuda.synchronize()
            before = time.monotonic()
            with torch.no_grad(), torch.autocast('cuda', dtype=torch.bfloat16, enabled=network is model):
                batch = torch.tensor([input_ids], dtype=torch.long, device="cuda")
                logits = network(input_ids=batch, use_cache=False).logits[0, prefix_length-1:].float()
                labels = torch.tensor(targets, device='cuda')
                logp = logits.gather(1, labels[:, None]).squeeze(1) - torch.logsumexp(logits, dim=-1)
                value = logp.mean().item()
                predicted = logits.argmax(dim=-1).tolist()
                prediction = predicted if isinstance(target, list) else predicted[0]
            torch.cuda.synchronize()
            run["score_wall_seconds"] += time.monotonic() - before
            run["score_forward_calls"] += 1
            run['scorer_input_tokens'] += len(input_ids)
            network.train(mode)
            if not math.isfinite(value):
                raise RuntimeError("Nonfinite prediction evidence")
            return value, prediction
        def scoring_input(context, thought='', variant=None):
            if controlled:
                return tokenizer.apply_chat_template(span_scoring_messages(
                    tokenizer.decode(context, skip_special_tokens=True), thought),
                    tokenize=True, continue_final_message=True, return_dict=False)
            if structured and variant != 'legacy':
                messages = primitive_messages(tokenizer.decode(context, skip_special_tokens=True))
                messages.append({'role': 'assistant', 'content': thought + '\nFinal answer: \\boxed{'})
                return tokenizer.apply_chat_template(messages, tokenize=True, continue_final_message=True, return_dict=False)
            return context + begin + tokenizer.encode(thought, add_special_tokens=False) + end
        def reserve(n):
            if run["generated_tokens"] + n * cfg["max_completion_tokens"] > cfg["max_generated_tokens"]:
                raise RuntimeError("Generated-token cap cannot cover the next group")
        def generate(messages, seed):
            reserve(1)
            set_seed(seed)
            model.eval()
            model.config.use_cache = True
            prefilled = messages[-1]['role'] == 'assistant'
            batch = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=not prefilled,
                continue_final_message=prefilled, return_tensors="pt", return_dict=True).to("cuda")
            torch.cuda.synchronize()
            before = time.monotonic()
            generation_options = {'do_sample':False} if controlled else {
                'do_sample':True,'temperature':cfg['temperature'],'top_p':cfg['top_p'],'top_k':0}
            with torch.no_grad(), torch.autocast('cuda', dtype=torch.bfloat16):
                sequence = model.generate(**batch, max_new_tokens=cfg["max_completion_tokens"],
                    pad_token_id=tokenizer.pad_token_id, **generation_options)
            torch.cuda.synchronize()
            run["generation_wall_seconds"] += time.monotonic() - before
            ids = sequence[0, batch["input_ids"].shape[1]:].tolist()
            run["generated_tokens"] += len(ids)
            run['evaluation_generated_tokens'] += len(ids)
            run['evaluation_prompt_tokens'] += batch['input_ids'].numel()
            run["rollouts"] += 1
            return tokenizer.decode(ids, skip_special_tokens=True), ids
        def evaluate(phase, question_file='heldout_primitives.jsonl'):
            with (args.output / (phase + "_prediction.jsonl")).open("x") as out:
                for i, q in enumerate(validation):
                    plain, plain_pred = score(model, scoring_input(q['context_ids']), q["target_id"])
                    text, ids = generate(q["prompt"], cfg["seed"] + i)
                    reasoned, predicted = score(model, scoring_input(q['context_ids'], text), q["target_id"])
                    out.write(json.dumps({"id": q["id"], "target_id": q["target_id"], "plain_logp": plain,
                        "reasoned_logp": reasoned, "plain_correct": plain_pred == q["target_id"],
                        "reasoned_correct": predicted == q["target_id"], "response": text, "completion_ids": ids}) + "\n")
                    out.flush()
            if controlled:
                with (args.output / (phase + '_primitives.jsonl')).open('x') as out:
                    for i, q in enumerate(validation):
                        text, ids = generate(q['prompt'], cfg['seed']+i)
                        out.write(json.dumps({'id':q['id'],'family':q['chapter'],'response':text,
                            'completion_ids':ids,'parsed_answer':None,'accepted':None,
                            'grading_status':'pending independent mathematical review'})+'\n')
                        out.flush()
                return
            with (args.output / (phase + "_primitives.jsonl")).open("x") as out:
                for i, line in enumerate((args.data / question_file).read_text().splitlines()):
                    q = json.loads(line)
                    assisted = phase.endswith('_assisted')
                    question = (q.get('assistance', '') + '\n' if assisted else '') + q['question']
                    messages = [{"role": "user", "content": question + " Explain your calculation briefly and put the final integer in \\boxed{...}."}]
                    prefix = cfg.get('evaluation_answer_prefix', '')
                    if prefix:
                        messages[0]['content'] = q['question'] + ' Return only the final integer in \\boxed{...}.'
                        messages.append({'role': 'assistant', 'content': prefix})
                    generated, ids = generate(messages, cfg["seed"] + i)
                    text = prefix + generated
                    answer = boxed_integer(text)
                    out.write(json.dumps({**q, "response": text, "completion_ids": ids,
                                          'generated_response': generated, 'supplied_answer_prefix': prefix,
                                          'assisted': assisted,
                                          "parsed_answer": answer, "accepted": answer == q["answer"]}) + "\n")
                    out.flush()
                    save()
        def capability_point(phase):
            """Read exact rows once; no inferred correctness for missing answers."""
            rows = [json.loads(line) for line in (args.output / (phase + '_primitives.jsonl')).read_text().splitlines()]
            summary = {'phase': phase, 'training_generated_tokens': run['training_generated_tokens'],
                'trajectory_training_generated_tokens': parent_training_tokens + run['training_generated_tokens'],
                'optimizer_steps': run['optimizer_steps'], 'evaluation_file': phase + '_primitives.jsonl',
                'n': len(rows), 'accepted': None if controlled else sum(q['accepted'] for q in rows),
                'unparsed': sum(q['parsed_answer'] is None for q in rows),
                'at_generation_cap': sum(len(q['completion_ids']) >= cfg['max_completion_tokens'] for q in rows)}
            with (args.output / 'capability_curve.jsonl').open('a') as out:
                out.write(json.dumps(summary) + '\n')
            return summary
        def upload_checkpoint(path):
            if not cfg.get('checkpoint_s3_prefix'):
                return
            destination = cfg['checkpoint_s3_prefix'].rstrip('/') + '/' + str(path.relative_to(args.output)) + '/'
            subprocess.run(['aws', 's3', 'sync', str(path), destination, '--only-show-errors'], check=True, timeout=180)
            receipt = path / 'checkpoint_ready.json'
            receipt.write_text(json.dumps({'optimizer_steps': run['optimizer_steps'],
                'training_generated_tokens': run['training_generated_tokens'],
                's3': destination, 'files': {p.name: p.stat().st_size for p in path.iterdir() if p.is_file()}}) + '\n')
            subprocess.run(['aws', 's3', 'cp', str(receipt), destination + receipt.name, '--only-show-errors'], check=True, timeout=60)
        evaluate("baseline")
        capability_point('baseline')
        if structured and not controlled:
            # Diagnostic comparison is inside the approved run, before updates.
            with (args.output / 'prompt_probe.jsonl').open('x') as out:
                for i, q in enumerate(validation[:cfg.get('prompt_probe_positions', 8)]):
                    for variant in ('legacy', 'chat'):
                        context = tokenizer.decode(q['context_ids'], skip_special_tokens=True)
                        messages = thought_messages(context) if variant == 'legacy' else primitive_messages(context)
                        text, ids = generate(messages, cfg['seed'] + i)
                        plain, _ = score(model, scoring_input(q['context_ids'], variant=variant), q['target_id'])
                        reasoned, _ = score(model, scoring_input(q['context_ids'], text, variant), q['target_id'])
                        out.write(json.dumps({'id': q['id'], 'variant': variant, 'plain_logp': plain,
                            'reasoned_logp': reasoned, 'completion_ids': ids, 'response': text}) + '\n')
                        out.flush()
            evaluate('validation_initial_assisted', 'validation_primitives.jsonl')
            capability_point('validation_initial_assisted')
        if cfg.get('evaluation_token_milestones'):
            evaluate('validation_initial', 'validation_primitives.jsonl')
            capability_point('validation_initial')
        set_seed(cfg["seed"])
        model.train()
        model.config.use_cache = False
        rollout_log = (args.output / "rollouts.jsonl").open("x")
        trainer_log = (args.output / "trainer.jsonl").open("x")
        encountered_contexts, encountered_documents = set(), set()
        def reward(prompts, completions, completion_ids, context_ids, target_id, id, trainer_state, document_id, curriculum_stage, **kwargs):
            if len(completion_ids) != cfg["num_generations"] or len(set(id)) != 1:
                raise RuntimeError("RLP group must share one context and target")
            reserve(0)
            # Candidates already exist. Persist their cost before any scorer can fail.
            generated_count = sum(len(ids) for ids in completion_ids)
            run['generated_tokens'] += generated_count
            run['training_generated_tokens'] += generated_count
            run['rollouts'] += len(completion_ids)
            save()
            raw_rewards = []
            baseline, _ = score(teacher, scoring_input(context_ids[0]), target_id[0])
            for text, generated, context, target in zip(completions, completion_ids, context_ids, target_id, strict=True):
                logp, _ = score(model, scoring_input(context, text[0]['content']), target)
                raw = information_gain(logp, baseline)
                raw_rewards.append(raw)
                rollout_log.write(json.dumps({"step": trainer_state.global_step, "id": id[0], "target_id": target,
                    'document_id': document_id[0], 'curriculum_stage': curriculum_stage[0],
                    'view_id':kwargs.get('view_id', [None])[0],
                    'family_id':kwargs.get('family_id', [None])[0],
                    'repeat_index':kwargs.get('repeat_index', [None])[0],
                    "response": text[0]["content"], "completion_ids": generated, "reasoned_logp": logp,
                    "baseline_logp": baseline, "information_gain": raw, "reward": corrected_reward(raw, cfg["num_generations"]),
                    "reward_source": "model prediction evidence; no Lean correctness claim"}) + "\n")
                rollout_log.flush()
            run['training_prompt_tokens'] += sum(len(tokenizer.apply_chat_template(p, tokenize=True, add_generation_prompt=True, return_dict=False)) for p in prompts)
            run['last_group_informative'] = max(raw_rewards) > min(raw_rewards)
            encountered_contexts.update(id)
            encountered_documents.update(document_id)
            run['unique_training_contexts'] = len(encountered_contexts)
            run['unique_training_documents'] = len(encountered_documents)
            stage = str(curriculum_stage[0])
            run['training_stages'][stage] = run['training_stages'].get(stage, 0) + len(completion_ids)
            if run["generated_tokens"] > cfg["max_generated_tokens"]:
                raise RuntimeError("Generated-token cap exceeded")
            save()
            return [corrected_reward(r, cfg["num_generations"]) for r in raw_rewards]
        class Records(TrainerCallback):
            def on_step_begin(self, args, state, control, **kwargs):
                reserve(cfg["num_generations"])
                if run['score_forward_calls'] + 1 + cfg['num_generations'] > cfg['max_score_forward_calls']:
                    raise RuntimeError('Scorer-call cap cannot cover the next group')
                if state.global_step == 0 or (controlled and 'first_update' not in run):
                    # At most 290 tensors x 256 values x 4 bytes, below 0.3 MiB.
                    self.before_update = {name: p.detach().view(-1)[:256].clone()
                                          for name, p in model.named_parameters() if p.requires_grad}
            def on_step_end(self, args, state, control, **kwargs):
                if state.global_step == 1 or (controlled and 'first_update' not in run):
                    changed = count = 0
                    maximum = 0.0
                    for name, p in model.named_parameters():
                        if name in self.before_update:
                            before = self.before_update[name]
                            after = p.detach().view(-1)[:before.numel()]
                            changed += (after != before).sum().item()
                            count += before.numel()
                            maximum = max(maximum, (after - before).abs().max().item())
                    moments = {str(v.dtype) for values in kwargs['optimizer'].state.values()
                               for key, v in values.items() if key in ('exp_avg', 'exp_avg_sq')}
                    informative = run.get('last_group_informative',True)
                    record = {'step': state.global_step, 'informative_group':informative,
                              'sampled_parameters': count, 'changed_parameters': changed,
                              'max_abs_change': maximum, 'optimizer_moment_dtypes': sorted(moments)}
                    args_output = Path(args.output_dir).parent / ('first_update.json' if informative or not controlled else 'uninformative_update.json')
                    args_output.write_text(json.dumps(record, indent=2) + '\n')
                    del self.before_update
                    if (changed == 0 and (informative or not controlled)) or moments != {'torch.float32'}:
                        raise RuntimeError('First RLP update failed the weight or precision check: ' + str(args_output))
                    if informative or not controlled:
                        run['first_update'] = record
                with torch.no_grad():
                    for baseline, policy in zip(teacher.parameters(), model.parameters(), strict=True):
                        baseline.mul_(cfg["ema_decay"]).add_(policy.float(), alpha=1 - cfg["ema_decay"])
                run["optimizer_steps"] = state.global_step
                reached = due_milestones(run['training_generated_tokens'], cfg.get('evaluation_token_milestones', []), run['completed_token_milestones'])
                if reached:
                    python_rng, numpy_rng = random.getstate(), np.random.get_state()
                    mode, cache = model.training, model.config.use_cache
                    try:
                        with torch.random.fork_rng(devices=[0]):
                            phase = 'validation_tokens_' + str(reached[-1])
                            evaluate(phase, 'validation_primitives.jsonl')
                            point = capability_point(phase)
                    finally:
                        random.setstate(python_rng)
                        np.random.set_state(numpy_rng)
                        model.train(mode)
                        model.config.use_cache = cache
                    run['completed_token_milestones'].extend(reached)
                    control.should_save = True
                    metrics = {'capability/n': point['n'],
                        'capability/training_generated_tokens': point['training_generated_tokens']}
                    if point['accepted'] is not None:
                        metrics['capability/accepted'] = point['accepted']
                    trainer.log(metrics)
                save()
                return control
            def on_save(self, args, state, control, **kwargs):
                checkpoint = Path(args.output_dir) / ('checkpoint-' + str(state.global_step))
                torch.save(teacher.state_dict(), checkpoint / 'ema_teacher.pt')
                (checkpoint / 'acquisition_manifest.json').write_text(json.dumps(run, indent=2) + '\n')
                upload_checkpoint(checkpoint)
            def on_log(self, args, state, control, logs=None, **kwargs):
                trainer_log.write(json.dumps({"step": state.global_step, **(logs or {}),
                    "generated_tokens": run["generated_tokens"], "score_forward_calls": run["score_forward_calls"]}) + "\n")
                trainer_log.flush()
                print(json.dumps({"step": state.global_step, **(logs or {})}), flush=True)
        settings = GRPOConfig(output_dir=str(args.output / "policy"), max_steps=cfg["max_steps"],
            per_device_train_batch_size=1, gradient_accumulation_steps=cfg["num_generations"],
            generation_batch_size=cfg["num_generations"], num_generations=cfg["num_generations"],
            num_iterations=1, shuffle_dataset=False, seed=cfg["seed"], data_seed=cfg["seed"],
            learning_rate=cfg["learning_rate"], lr_scheduler_type="constant", weight_decay=0.0,
            bf16=True, gradient_checkpointing=True, max_prompt_length=cfg["max_prompt_tokens"],
            max_completion_length=cfg["max_completion_tokens"], temperature=cfg["temperature"],
            top_p=cfg["top_p"], top_k=0, beta=0.0, loss_type="grpo", scale_rewards=False,
            use_vllm=False, logging_steps=1, save_steps=cfg["max_steps"], save_total_limit=cfg.get('save_total_limit', 1),
            report_to="tensorboard", remove_unused_columns=False)
        class MixedPrecisionGRPO(GRPOTrainer):
            def _generate_single_turn(self, prompts, images):
                with torch.autocast('cuda', dtype=torch.bfloat16):
                    return super()._generate_single_turn(prompts, images)
        trainer = MixedPrecisionGRPO(model=model, args=settings, train_dataset=Dataset.from_list(training),
            processing_class=tokenizer, reward_funcs=reward, callbacks=[Records()])
        run["status"] = "training"
        save()
        torch.cuda.synchronize()
        before = time.monotonic()
        trainer.train()
        if controlled and 'first_update' not in run:
            raise RuntimeError('No informative RLP update observed; learning check failed')
        torch.cuda.synchronize()
        run["training_wall_seconds"] = time.monotonic() - before
        trainer.save_model(str(args.output / "policy_final"))
        trainer.save_state()
        torch.save(teacher.state_dict(), args.output / "ema_teacher.pt")
        (args.output / 'policy_final' / 'acquisition_manifest.json').write_text(json.dumps(run, indent=2) + '\n')
        upload_checkpoint(args.output / 'policy_final')
        rollout_log.close()
        trainer_log.close()
        evaluate("after")
        capability_point('after')
        if cfg.get('evaluation_token_milestones'):
            evaluate('validation_final', 'validation_primitives.jsonl')
            capability_point('validation_final')
        if structured and not controlled:
            evaluate('validation_final_assisted', 'validation_primitives.jsonl')
            capability_point('validation_final_assisted')
        run["cuda_peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
        run["status"] = "complete"
    except BaseException as exc:
        run["status"] = "failed"
        run["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
