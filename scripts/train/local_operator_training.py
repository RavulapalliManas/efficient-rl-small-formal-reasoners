"""Local-action mode for run_grpo_pilot, using the same pinned TRL optimizer.

FP32 weights/gradients/moments/reference: P*20 bytes (P from checkpoint).
One microbatch logits <=3072*151936*4 bytes; activations/cache/runtime reserved
separately by a 24GiB CUDA cap. Host cap16GiB; one Lean worker <=2GiB.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import signal
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ('data', 'eval')]
from prepare_grpo_pilot import load_checker
from verify_local_operator import extract_witness, task_obligation, reward_value


def messages(task):
    return [{'role': 'user', 'content': task['prompt'] +
             ' You may reason first. End with exactly one line: Final witness: <expression>.'
             ' Use only ' + (', '.join(task['variables']) or 'integer constants') +
             ' and integers, +, -, *, parentheses. Use * for multiplication.'
             ' Do not give a list of possible answers.'}]


def score_action(task, response, checker, stem):
    try:
        candidate = extract_witness(response)
        obligation, body = task_obligation(task, candidate)
    except ValueError as error:
        return dict(status='format_error', reward=0, error=str(error), mathematical_correctness=None)
    result = checker.check_candidate(obligation, body, stem)
    reward = reward_value(result)
    return dict(status='infrastructure_error' if reward is None else 'accepted' if reward else 'rejected',
                reward=reward, candidate=candidate, obligation=obligation, verification=result,
                mathematical_correctness=None if reward is None else bool(reward))


def run(args, config):
    import torch
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback, set_seed
    from trl import GRPOConfig, GRPOTrainer

    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    (out / 'lean').mkdir()
    started = time.monotonic()
    counts = dict(generated_tokens=config.get('prior_generated_tokens', 0), physical_lean_calls=config.get('prior_lean_calls', 0), attempts=0)
    run_record = dict(status='starting', config=config, counts=counts, training_updates=0, validation=[])
    def save():
        run_record['wall_seconds'] = time.monotonic() - started
        (out/'run.json.tmp').write_text(json.dumps(run_record, indent=2)+'\n')
        (out/'run.json.tmp').replace(out/'run.json')
    def timeout(*_):
        raise TimeoutError('Operator pilot wall cap reached')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(config['max_wall_seconds'])
    checker = load_checker(args.checker, args.environment, out)
    lean_call = checker.lean_call
    def bounded_call(source, stem):
        if counts['physical_lean_calls'] >= config['max_physical_lean_calls']:
            raise RuntimeError('Lean-call cap reached')
        counts['physical_lean_calls'] += 1
        return lean_call(source, stem)
    checker.lean_call = bounded_call
    def reserve(n):
        if counts['generated_tokens'] + n*config['max_completion_tokens'] > config['max_generated_tokens']:
            raise RuntimeError('Remaining token cap cannot cover generation')
    def record(task, response, ids, phase, step, stream):
        counts['attempts'] += 1
        counts['generated_tokens'] += len(ids)
        scored = score_action(task, response, checker, 'attempt_'+str(counts['attempts']))
        row = dict(id=task['id'], family=task['family'], phase=phase, step=step,
                   response=response, completion_ids=ids, generated_tokens=len(ids), **scored)
        stream.write(json.dumps(row)+'\n'); stream.flush(); save()
        if scored['reward'] is None:
            raise RuntimeError('Verifier failure; group excluded from optimization')
        return row
    try:
        manifest = json.loads(args.data.read_text())
        tasks = manifest['rows']
        assert len({q['id'] for q in tasks}) == len(tasks)
        splits = {s: [q for q in tasks if q['split'] == s] for s in ('train','validation','heldout')}
        assert all(splits.values())
        for a,b in [('train','validation'),('train','heldout'),('validation','heldout')]:
            assert not ({q['template'] for q in splits[a]} & {q['template'] for q in splits[b]})
        run_record['manifest_sha256'] = hashlib.sha256(args.data.read_bytes()).hexdigest()
        run_record['versions'] = {p: importlib.metadata.version(p) for p in ('torch','transformers','trl','accelerate')}
        if run_record['versions']['trl'] != config['trl_version']:
            raise RuntimeError('TRL version differs from registration')
        toolchain = (args.environment/'mathlib/lean-toolchain').read_text().strip()
        if toolchain != 'leanprover/lean4:v4.10.0-rc1':
            raise RuntimeError('Lean toolchain differs from registration')
        run_record['lean'] = 'v4.10.0-rc1'
        run_record['mathlib'] = '29dcec074de168ac2bf835a77ef68bbe069194c5'
        run_record['mathlib_provenance'] = 'Installed archive; replayed task references, not a live Git revision check'
        with (out/'reference_replay.jsonl').open('x') as stream:
            for task in tasks:
                q,body = task_obligation(task,task['reference'])
                result = checker.check_candidate(q,body,'reference_'+task['id'])
                stream.write(json.dumps(dict(id=task['id'],verification=result))+'\n');stream.flush()
                if reward_value(result) != 1:
                    raise RuntimeError('Reference replay failed: '+task['id'])
        if args.dry_run:
            run_record['status']='reference_replay_only';return
        if not torch.cuda.is_available() or torch.cuda.device_count()!=1:
            raise RuntimeError('Training requires one GPU')
        torch.cuda.set_per_process_memory_fraction(config['cuda_memory_gib']*1024**3/torch.cuda.get_device_properties(0).total_memory)
        set_seed(config['seed'])
        tok=AutoTokenizer.from_pretrained(config['checkpoint']);tok.padding_side='left'
        if tok.pad_token_id is None: tok.pad_token=tok.eos_token
        for task in tasks:
            if len(tok.apply_chat_template(messages(task),tokenize=True,add_generation_prompt=True))>config['max_prompt_tokens']:
                raise RuntimeError('Oversized prompt: '+task['id'])
        model=AutoModelForCausalLM.from_pretrained(config['checkpoint'],torch_dtype=torch.float32,attn_implementation='sdpa').to('cuda')
        run_record['parameters']=sum(p.numel() for p in model.parameters())
        run_record['parameter_optimizer_reference_bytes']=run_record['parameters']*20
        def evaluate(pool, phase, step, samples=1):
            cpu_rng=torch.get_rng_state();gpu_rng=torch.cuda.get_rng_state_all()
            was_training=model.training;model.eval();old_cache=model.config.use_cache;model.config.use_cache=True
            results=[]
            try:
                with (out/(phase+'.jsonl')).open('x') as stream:
                    for task in pool:
                        batch=tok.apply_chat_template(messages(task),tokenize=True,add_generation_prompt=True,return_tensors='pt',return_dict=True).to('cuda')
                        for _ in range(samples):
                            reserve(1)
                            sampling=dict(do_sample=True,temperature=config['temperature'],top_p=config['top_p'],top_k=0) if samples>1 else dict(do_sample=False)
                            with torch.inference_mode():
                                seq=model.generate(**batch,max_new_tokens=config['max_completion_tokens'],pad_token_id=tok.pad_token_id,**sampling)
                            ids=seq[0,batch['input_ids'].shape[1]:].tolist()
                            results.append(record(task,tok.decode(ids,skip_special_tokens=True),ids,phase,step,stream))
                return results
            finally:
                model.train(was_training);model.config.use_cache=old_cache
                torch.set_rng_state(cpu_rng);torch.cuda.set_rng_state_all(gpu_rng)
        readiness=evaluate(splits['train'],'readiness',0,config['readiness_samples_per_task'])
        mixed={q['family'] for q in splits['train'] if len({r['reward'] for r in readiness if r['id']==q['id']})>1}
        run_record['mixed_families']=sorted(mixed)
        if len(mixed)<config['required_mixed_families']:
            run_record['status']='no_informative_training_groups';return
        baseline=evaluate(splits['validation'],'validation_0',0)
        baseline_score=sum(r['reward'] for r in baseline)
        run_record['validation'].append(dict(step=0,correct=baseline_score,n=len(baseline)))
        # Held-out statements are not used for optimization or checkpoint selection.
        evaluate(splits['heldout'],'heldout_baseline',0)
        best_score=baseline_score;best_path=config['checkpoint'];regressions=0
        optimizer=torch.optim.AdamW(model.parameters(),lr=config['learning_rate'],weight_decay=0.)
        before={name: p.detach().reshape(-1)[::max(1,p.numel()//32)][:32].clone()
                for name,p in model.named_parameters() if p.requires_grad}
        mixed_groups=0
        rollout=(out/'rollouts.jsonl').open('x');logs=(out/'trainer.jsonl').open('x')
        lookup={q['id']:q for q in splits['train']}
        def reward(completions,completion_ids,theorem_id,trainer_state,**kwargs):
            nonlocal mixed_groups
            if len(set(theorem_id))!=1 or len(completions)!=config['num_generations']:
                raise RuntimeError('Reward group mixes tasks or has wrong size')
            values=[record(lookup[qid],c[0]['content'],ids,'train',trainer_state.global_step,rollout)['reward']
                    for qid,c,ids in zip(theorem_id,completions,completion_ids,strict=True)]
            mixed_groups+=int(len(set(values))>1)
            return values
        class Records(TrainerCallback):
            def on_step_begin(self,args,state,control,**kwargs):
                reserve(config['num_generations'])
            def on_log(self,args,state,control,logs=None,**kwargs):
                row=dict(step=state.global_step,mixed_groups=mixed_groups,counts=dict(counts),**(logs or {}))
                log_stream.write(json.dumps(row)+'\n');log_stream.flush()
                print(json.dumps(row),flush=True)
            def on_step_end(self,args,state,control,**kwargs):
                nonlocal best_score,best_path,regressions
                run_record['training_updates']=state.global_step
                if any(not torch.isfinite(p).all() for p in model.parameters() if p.requires_grad):
                    raise RuntimeError('Nonfinite parameters')
                if mixed_groups and 'first_update' not in run_record:
                    moments=[v for item in optimizer.state.values() for k,v in item.items() if k in ('exp_avg','exp_avg_sq')]
                    if not moments or any(v.dtype!=torch.float32 for v in moments):
                        raise RuntimeError('Optimizer moments are not FP32')
                    changed=any(not torch.equal(before[name],p.detach().reshape(-1)[::max(1,p.numel()//32)][:32])
                                for name,p in model.named_parameters() if name in before)
                    run_record['first_update']=dict(step=state.global_step,sampled_weights_changed=changed,moments_fp32=True)
                    if mixed_groups and not changed:
                        raise RuntimeError('Sampled weights unchanged after informative update')
                if state.global_step%config['eval_steps']==0:
                    rows=evaluate(splits['validation'],'validation_'+str(state.global_step),state.global_step)
                    score=sum(r['reward'] for r in rows)
                    parsed=sum(r['status']!='format_error' for r in rows)
                    run_record['validation'].append(dict(step=state.global_step,correct=score,n=len(rows),parsed=parsed))
                    checkpoint=out/('policy_step_'+str(state.global_step))
                    pending=out/'checkpoint_in_progress'
                    model.save_pretrained(pending);tok.save_pretrained(pending)
                    torch.save(dict(optimizer=optimizer.state_dict(),step=state.global_step,
                                    cpu_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all()),
                               pending/'optimizer_state.pt')
                    (pending/'provenance.json').write_text(json.dumps(dict(config=config,
                        manifest_sha256=run_record['manifest_sha256'],validation_correct=score,
                        step=state.global_step,counts=dict(counts)),indent=2)+'\n')
                    pending.rename(checkpoint)
                    if score>best_score:best_score=score;best_path=str(checkpoint)
                    regressions=regressions+1 if score<baseline_score else 0
                    if regressions>=2:control.should_training_stop=True
                save()
        log_stream=logs
        settings=GRPOConfig(output_dir=str(out/'trainer'),max_steps=config['max_steps'],
            per_device_train_batch_size=1,gradient_accumulation_steps=config['num_generations'],
            generation_batch_size=config['num_generations'],num_generations=config['num_generations'],
            num_iterations=1,shuffle_dataset=False,seed=config['seed'],data_seed=config['seed'],
            learning_rate=config['learning_rate'],lr_scheduler_type='constant',weight_decay=0.,max_grad_norm=1.,bf16=False,
            gradient_checkpointing=True,max_prompt_length=config['max_prompt_tokens'],max_completion_length=config['max_completion_tokens'],
            temperature=config['temperature'],top_p=config['top_p'],top_k=0,beta=config['beta'],loss_type='grpo',scale_rewards='group',
            use_vllm=False,logging_steps=1,save_steps=config['eval_steps'],save_total_limit=4,report_to='none',remove_unused_columns=False)
        dataset=Dataset.from_list([dict(prompt=messages(q),theorem_id=q['id']) for q in splits['train']])
        trainer=GRPOTrainer(model=model,args=settings,train_dataset=dataset,processing_class=tok,reward_funcs=reward,
                            callbacks=[Records()],optimizers=(optimizer,None))
        trainer.train();trainer.save_state();rollout.close();logs.close()
        run_record['selected_checkpoint']=best_path
        run_record['validation_gain']=best_score-baseline_score
        run_record['action_gain_threshold_met']=best_score-baseline_score>=config['validation_min_net_gain']
        # Keep full response correctness for independent content review; a witness does not certify prose.
        run_record['learning_claim']='Pending independent prose review and format-conditioned analysis'
        del trainer
        import gc
        model.to('cpu');del model;gc.collect();torch.cuda.empty_cache()
        model=AutoModelForCausalLM.from_pretrained(best_path,torch_dtype=torch.float32,attn_implementation='sdpa').to('cuda')
        evaluate(splits['heldout'],'heldout_selected',run_record['training_updates'])
        run_record['cuda_peak_allocated_bytes']=torch.cuda.max_memory_allocated()
        run_record['status']='complete'
    except BaseException as error:
        run_record.update(status='failed',error=type(error).__name__+': '+str(error));raise
    finally:
        save()
