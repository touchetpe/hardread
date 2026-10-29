"""Prospective single-H100 DPO qualification; disabled without explicit admission."""
import hashlib
import json
import math
from pathlib import Path
import time

import train_dpo as T

QUALIFY_ENABLED = False
SHARD_SIZE = 48


def measurement_shard(pairs):
    """Ten SHA-ranked IDs per length quartile, then eight longest, then ranked fill."""
    if not isinstance(pairs,list) or len(pairs)<SHARD_SIZE:raise T.DPOError('48 unique pairs required')
    ids=[p.get('pair_id') for p in pairs]
    if any(not isinstance(x,str) or not x for x in ids) or len(set(ids))!=len(ids):raise T.DPOError('shard pair identities differ')
    def length(p):
        try:n=len(p['chosen']['input_ids'])+len(p['rejected']['input_ids'])
        except (KeyError,TypeError) as e:raise T.DPOError('shard length missing') from e
        if n<=0:raise T.DPOError('shard length invalid')
        return n
    ranked=sorted(pairs,key=lambda p:(length(p),p['pair_id']));groups=[[] for _ in range(4)]
    for i,p in enumerate(ranked):groups[min(3,i*4//len(ranked))].append(p)
    if any(len(group)<10 for group in groups):raise T.DPOError('quartile sample unavailable')
    def key(p):return (hashlib.sha256(p['pair_id'].encode()).hexdigest(),p['pair_id'])
    selected=[];seen=set()
    def add(p):
        if p['pair_id'] not in seen:selected.append(p);seen.add(p['pair_id'])
    for group in groups:
        for pair in sorted(group,key=key)[:10]:add(pair)
    for pair in sorted(pairs,key=lambda p:(-length(p),p['pair_id']))[:8]:add(pair)
    for pair in sorted(pairs,key=key):
        if len(selected)==SHARD_SIZE:break
        add(pair)
    if len(selected)!=SHARD_SIZE:raise T.DPOError('measurement shard incomplete')
    bucket={p['pair_id']:i for i,group in enumerate(groups) for p in group}
    return selected,bucket,{'quartile_bounds':[[length(g[0]),length(g[-1])] for g in groups], 'selected_counts':[sum(bucket[p['pair_id']]==i for p in selected) for i in range(4)], 'population_counts':[len(g) for g in groups]}


def policy_coverage_shard(shard,bucket):
    """Longest sampled pair in each bucket; includes the actual global longest."""
    groups=[[p for p in shard if bucket[p['pair_id']]==i] for i in range(4)]
    if any(not g for g in groups): raise T.DPOError('policy coverage bucket missing')
    def key(p):return (len(p['chosen']['input_ids'])+len(p['rejected']['input_ids']),p['pair_id'])
    return [max(group,key=key) for group in groups]


def validate_policy_coverage(coverage,longest_id):
    if not isinstance(coverage,list) or len(coverage)!=4 or [x.get('bucket') for x in coverage]!=list(range(4)):raise T.DPOError('policy coverage incomplete')
    if not any(x.get('pair_id')==longest_id for x in coverage):raise T.DPOError('longest policy coverage missing')
    for x in coverage:
        if x.get('microbatch_pairs')!=1 or x.get('optimizer_state_allocated') is not True or type(x.get('seconds')) not in (int,float) or not math.isfinite(x['seconds']) or x['seconds']<=0 or any(type(x.get(k)) is not int or x[k]<=0 for k in ('peak_allocated_bytes','peak_reserved_bytes')):raise T.DPOError('policy coverage evidence invalid')


def project_seconds(pairs,bucket,reference_seconds,policy_seconds,load_seconds,reserve_seconds):
    """Upper projection from per-quartile max observed time and exact six-rank partition."""
    values=list(reference_seconds)+list(policy_seconds)+[load_seconds,reserve_seconds]
    if len(reference_seconds)!=4 or len(policy_seconds)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in values) or any(x<=0 for x in list(reference_seconds)+list(policy_seconds)):raise T.DPOError('projection inputs invalid')
    reference_times=[];policy_times=[]
    for rank in range(T.WORLD_SIZE):
        indices=T.rank_steps(len(pairs),rank)
        reference_times.append(sum(reference_seconds[bucket[pairs[i]['pair_id']]] for i in indices if i is not None))
        policy_times.append(sum(policy_seconds[bucket[pairs[i]['pair_id']]] for i in indices if i is not None))
    return 1.25*(max(reference_times)+max(policy_times)+load_seconds+reserve_seconds)


def check_approval(path,expected_sha,config_sha,code_sha=None):
    return T.check_root_approval(path,expected_sha,config_sha,'single_h100_qualification',code_sha)


def verify_runtime_receipt(path,expected_sha,cfg,config_sha,binding):
    if Path(path).is_symlink() or T.file_sha(path)!=expected_sha: raise T.DPOError('runtime inventory bytes differ')
    doc=json.loads(Path(path).read_bytes())
    if not isinstance(doc,dict) or doc.get('schema')!='hardread.dpo.runtime-inventory/v1' or doc.get('config_sha256')!=config_sha or doc.get('code_sha256')!=binding['code_sha256'] or doc.get('environment_id')!=cfg['environment_id']: raise T.DPOError('runtime inventory identity differs')
    versions=doc.get('versions')
    if not isinstance(versions,dict) or set(versions)!=T.RUNTIME_NAMES or any(not isinstance(v,str) or not v.strip() for v in versions.values()) or any(versions.get(k)!=v for k,v in cfg['versions'].items()):raise T.DPOError('runtime inventory versions differ')
    if doc.get('environment_sha256')!=T.fingerprint({'environment_id':cfg['environment_id'],'versions':versions}):raise T.DPOError('runtime inventory fingerprint differs')
    return doc


def capture_runtime_receipt(output,cfg,config_sha,binding):
    """Capture exact package inventory, durably, before any model load."""
    import importlib.metadata
    import platform
    versions={}
    for name in sorted(T.RUNTIME_NAMES):
        try: value=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as e: raise T.DPOError('runtime package missing') from e
        if not isinstance(value,str) or not value.strip():raise T.DPOError('runtime package version invalid')
        if name in cfg['versions'] and cfg['versions'][name]!=value:raise T.DPOError('runtime package changed')
        versions[name]=value
    # Re-read all packages to detect changes during inventory capture.
    for name in sorted(T.RUNTIME_NAMES):
        try: again=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as e: raise T.DPOError('runtime package missing') from e
        if again!=versions[name]:raise T.DPOError('runtime package changed during capture')
    full_sha=T.fingerprint({'environment_id':cfg['environment_id'],'versions':versions})
    receipt={'schema':'hardread.dpo.runtime-inventory/v1','config_sha256':config_sha,'code_sha256':binding['code_sha256'],'environment_id':cfg['environment_id'],'python':platform.python_version(),'versions':versions,'environment_sha256':full_sha}
    path=Path(output)/'runtime-inventory.json';raw=T.canonical(receipt)+b'\n';T.exclusive_bytes(path,raw)
    if path.is_symlink() or path.read_bytes()!=raw or json.loads(path.read_bytes())!=receipt:raise T.DPOError('runtime inventory readback differs')
    digest=T.file_sha(path);verify_runtime_receipt(path,digest,cfg,config_sha,binding)
    return receipt,digest


def write_parity_diagnostic(output,pair_id,config_sha,binding,full_positions,selected_positions,metrics):
    """Durably persist scalar threshold diagnostics, never model tensors or data."""
    import re
    fields=('arm_logp_max_abs_error','loss_abs_error','gradient_cosine','gradient_relative_l2_error','adamw_update_relative_l2_error')
    if not isinstance(pair_id,str) or not pair_id or not re.fullmatch('[0-9a-f]{64}',str(config_sha)) or not isinstance(binding,dict) or not re.fullmatch('[0-9a-f]{64}',str(binding.get('code_sha256',''))):raise T.DPOError('parity receipt binding invalid')
    if type(full_positions) is not int or type(selected_positions) is not int or not 0<selected_positions<full_positions or not isinstance(metrics,dict) or set(metrics)!=set(fields):raise T.DPOError('parity receipt shape invalid')
    finite={k:type(metrics[k]) in (int,float) and math.isfinite(metrics[k]) for k in fields}
    safe={k:float(metrics[k]) if finite[k] else None for k in fields}
    limits={'arm_logp_max_abs_error':{'operator':'<=','value':0.01},'loss_abs_error':{'operator':'<=','value':0.001},'gradient_cosine':{'operator':'>=','value':0.9999},'gradient_relative_l2_error':{'operator':'<=','value':0.01},'adamw_update_relative_l2_error':{'operator':'<=','value':0.01}}
    passed=all(finite.values()) and metrics[fields[0]]<=0.01 and metrics[fields[1]]<=0.001 and metrics[fields[2]]>=0.9999 and metrics[fields[3]]<=0.01 and metrics[fields[4]]<=0.01
    pair_hash=hashlib.sha256(pair_id.encode()).hexdigest()
    receipt={'schema':'hardread.dpo.response-parity-diagnostic/v1','status':'passed' if passed else 'failed','config_sha256':config_sha,'binding_sha256':T.fingerprint(binding),'code_sha256':binding['code_sha256'],'pair_id_sha256':pair_hash,'full_positions':full_positions,'selected_positions':selected_positions,'metrics':safe,'finite':finite,'thresholds':limits}
    path=Path(output)/('parity-diagnostic-'+pair_hash+'.json');T.exclusive_bytes(path,T.canonical(receipt)+b'\n')
    if json.loads(path.read_bytes())!=receipt:raise T.DPOError('parity receipt readback differs')
    return receipt


def response_parity_probe(policy,pairs,reference,device,pad_id,check,output,config_sha,binding):
    """Pre-update dense/selected parity, with temporary AdamW state, no tensor output."""
    import torch
    import gc
    named=[(n,p) for n,p in policy.named_parameters() if p.requires_grad]
    if not named or any('lora_' not in n for n,_ in named):raise T.DPOError('parity trainable inventory differs')
    params=[p for _,p in named];base=[p.detach().cpu().clone() for p in params]
    original=T.trainable_fingerprint(policy);started=time.monotonic();results=[]
    def restore():
        with torch.no_grad():
            for p,v in zip(params,base):p.copy_(v.to(device=p.device,dtype=p.dtype))
        for p in params:p.grad=None
    try:
        for pair in pairs:
            check();records=[]
            for scorer in (T._score_pair_dense_oracle,T.score_pair):
                check();restore();opt=torch.optim.AdamW(params,lr=T.LEARNING_RATE)
                ch,rj=scorer(policy,pair,device,pad_id);ref=reference[pair['pair_id']]
                loss=T.dpo_loss(ch,rj,torch.tensor(ref['chosen'],device=device),torch.tensor(ref['rejected'],device=device))
                if not all(bool(torch.isfinite(x)) for x in (ch,rj,loss)):raise T.DPOError('parity score nonfinite')
                loss.backward()
                if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):raise T.DPOError('parity gradient inventory invalid')
                grads=[p.grad.detach().float().cpu().clone() for p in params]
                if not any(bool(torch.count_nonzero(g)) for g in grads):raise T.DPOError('parity gradient zero')
                check();opt.step();updated=[p.detach().float().cpu().clone() for p in params]
                records.append(([float(ch.detach().cpu()),float(rj.detach().cpu())],float(loss.detach().cpu()),grads,updated))
                del opt,ch,rj,loss;gc.collect()
            dense,selected=records
            lpd=max(abs(x-y) for x,y in zip(dense[0],selected[0]));ld=abs(dense[1]-selected[1])
            ga=torch.cat([x.reshape(-1).double() for x in dense[2]]);gb=torch.cat([x.reshape(-1).double() for x in selected[2]])
            na=float(torch.linalg.vector_norm(ga));nb=float(torch.linalg.vector_norm(gb))
            if not na or not nb:raise T.DPOError('parity gradient norm zero')
            cosine=float(torch.dot(ga,gb)/(na*nb));rel=float(torch.linalg.vector_norm(ga-gb)/na)
            da=torch.cat([(x-y.float()).reshape(-1).double() for x,y in zip(dense[3],base)])
            db=torch.cat([(x-y.float()).reshape(-1).double() for x,y in zip(selected[3],base)])
            delta_norm=float(torch.linalg.vector_norm(da))
            if not delta_norm:raise T.DPOError('parity optimizer update zero')
            step_rel=float(torch.linalg.vector_norm(da-db)/delta_norm)
            ids,labels,_=T._pair_tensors(pair,device,pad_id);pos=T._response_positions(labels,T._vocab_size(policy))
            metrics={'arm_logp_max_abs_error':lpd,'loss_abs_error':ld,'gradient_cosine':cosine,'gradient_relative_l2_error':rel,'adamw_update_relative_l2_error':step_rel}
            write_parity_diagnostic(output,pair['pair_id'],config_sha,binding,ids.shape[1],pos.numel(),metrics)
            if not all(math.isfinite(x) for x in (lpd,ld,cosine,rel,step_rel)) or lpd>0.01 or ld>0.001 or cosine<0.9999 or rel>0.01 or step_rel>0.01:raise T.DPOError('dense/selected parity differs')
            results.append({'pair_id_sha256':hashlib.sha256(pair['pair_id'].encode()).hexdigest(),'full_positions':ids.shape[1],'selected_positions':pos.numel(),'arm_logp_max_abs_error':lpd,'loss_abs_error':ld,'gradient_cosine':cosine,'gradient_relative_l2_error':rel,'adamw_update_relative_l2_error':step_rel})
            del records,ga,gb,da,db
        restore()
        if T.trainable_fingerprint(policy)!=original:raise T.DPOError('parity restoration differs')
        return {'status':'passed','native_indexed_logits':True,'parameter_inventory_sha256':T.fingerprint([n for n,_ in named]),'samples':results,'elapsed_seconds':time.monotonic()-started}
    finally:
        restore()


def run(args):
    if not args.execute or __import__('os').environ.get('HARDREAD_DPO_QUALIFY')!='1':raise T.DPOError('qualification disabled pending explicit admission')
    import gc
    import os
    import platform
    import torch
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',WANDB_MODE='offline')
    started=time.monotonic();check_approval(args.approval,args.approval_sha256,args.config_sha256)
    cfg,pairs,binding=T.load_inputs(args.config,args.config_sha256,args.code_root,args.data,args.base,args.adapter,args.selection,qualification=True)
    check_approval(args.approval,args.approval_sha256,args.config_sha256,binding['code_sha256'])
    if Path(__file__).resolve()!=(Path(args.code_root)/'azure/sft/qualify_dpo.py').resolve():raise T.DPOError('qualification source differs')
    if not torch.cuda.is_available() or torch.cuda.device_count()!=1 or 'H100' not in torch.cuda.get_device_name(0):raise T.DPOError('one H100 required')
    torch.cuda.set_device(0);deadline=started+cfg['max_seconds']
    def check():
        if time.monotonic()>=deadline:raise T.DPOError('qualification deadline exhausted')
    shard,bucket,hist=measurement_shard(pairs);ids=[p['pair_id'] for p in shard]
    output=Path(args.out)/cfg['run_name']
    if Path(args.out).is_symlink() or not Path(args.out).is_dir():raise T.DPOError('output mount invalid')
    output.mkdir(mode=0o700)
    runtime,runtime_sha=capture_runtime_receipt(output,cfg,args.config_sha256,binding)
    binding=dict(binding,environment_sha256=runtime['environment_sha256'])
    check();free,total=torch.cuda.mem_get_info(0);torch.cuda.reset_peak_memory_stats(0)
    times={'reference':[[] for _ in range(4)],'policy':[[] for _ in range(4)]}
    check();t=time.monotonic();ref=T.load_xl6(args.base,args.adapter,trainable=False,device=0);ref_load=time.monotonic()-t
    if ref.training or any(p.requires_grad for p in ref.parameters()):raise T.DPOError('reference must be frozen')
    records=[]; longest_id=max(shard,key=lambda p:(len(p['chosen']['input_ids'])+len(p['rejected']['input_ids']),p['pair_id']))['pair_id']
    prior_peak_alloc=prior_peak_reserved=0;longest_peak=None
    with torch.no_grad():
        for pair in shard:
            check()
            if pair['pair_id']==longest_id:
                prior_peak_alloc=int(torch.cuda.max_memory_allocated(0));prior_peak_reserved=int(torch.cuda.max_memory_reserved(0));torch.cuda.reset_peak_memory_stats(0)
            t=time.monotonic();ch,rj=T.score_pair(ref,pair,0,cfg['pad_id']);torch.cuda.synchronize(0);elapsed=time.monotonic()-t
            if pair['pair_id']==longest_id:longest_peak={'peak_allocated_bytes':int(torch.cuda.max_memory_allocated(0)),'peak_reserved_bytes':int(torch.cuda.max_memory_reserved(0))}
            chosen,rejected=float(ch.cpu()),float(rj.cpu())
            if not math.isfinite(chosen) or not math.isfinite(rejected):raise T.DPOError('reference score nonfinite')
            times['reference'][bucket[pair['pair_id']]].append(elapsed);records.append({'pair_id':pair['pair_id'],'chosen':chosen,'rejected':rejected})
    cache={'binding_sha256':T.fingerprint(binding),'records':records};T.validate_reference_cache(ids,cache,binding)
    cache_raw=T.canonical(cache)+b'\n';T.exclusive_bytes(output/'reference-cache.json',cache_raw);cache_sha=hashlib.sha256(cache_raw).hexdigest()
    if T.file_sha(output/'reference-cache.json')!=cache_sha:raise T.DPOError('reference cache readback differs')
    cached=T.validate_reference_cache(ids,json.loads((output/'reference-cache.json').read_bytes()),binding)
    del ref,cache,records;gc.collect();torch.cuda.empty_cache();check()
    if T.verify_tree(args.base,cfg['base_files'])!=binding['base_snapshot_sha256'] or T.verify_tree(args.adapter,cfg['adapter_files'])!=T.fingerprint(cfg['adapter_files']):raise T.DPOError('model inputs changed after reference')
    t=time.monotonic();policy=T.load_xl6(args.base,args.adapter,trainable=True,device=0);policy_load=time.monotonic()-t
    params=[p for p in policy.parameters() if p.requires_grad]
    if not params or any('lora_' not in name for name,p in policy.named_parameters() if p.requires_grad):raise T.DPOError('unexpected trainable parameters')
    optimizer=torch.optim.AdamW(params,lr=T.LEARNING_RATE);policy.train();T.disable_dropout(policy)
    length=lambda p:(len(p['chosen']['input_ids'])+len(p['rejected']['input_ids']),p['pair_id'])
    shortest=min(shard,key=length);longest=max(shard,key=length)
    parity=response_parity_probe(policy,[shortest,longest],cached,0,cfg['pad_id'],check,output,args.config_sha256,binding)
    check()
    def backward(pair,scale):
        check();t=time.monotonic();ch,rj=T.score_pair(policy,pair,0,cfg['pad_id']);v=cached[pair['pair_id']]
        loss=T.dpo_loss(ch,rj,torch.tensor(v['chosen'],device=0),torch.tensor(v['rejected'],device=0))
        if not bool(torch.isfinite(loss)):raise T.DPOError('nonfinite training loss')
        (loss*scale).backward();torch.cuda.synchronize(0);elapsed=time.monotonic()-t;times['policy'][bucket[pair['pair_id']]].append(elapsed);return elapsed
    optimizer.zero_grad(set_to_none=True);backward(shard[0],1.0);optimizer.zero_grad(set_to_none=True)
    before=T.trainable_fingerprint(policy)
    for pair in shard[:T.ACCUMULATION]:backward(pair,1/T.ACCUMULATION)
    if not all(p.grad is not None and bool(torch.isfinite(p.grad).all()) for p in params) or not any(bool(torch.count_nonzero(p.grad)) for p in params):raise T.DPOError('qualification gradients invalid')
    check();t=time.monotonic();optimizer.step();torch.cuda.synchronize(0);step_seconds=time.monotonic()-t
    if T.trainable_fingerprint(policy)==before:raise T.DPOError('qualification optimizer did not update')
    if not optimizer.state or any(not optimizer.state.get(p) for p in params):raise T.DPOError('optimizer state not allocated')
    optimizer.zero_grad(set_to_none=True)
    coverage=[]; measured_ids=set();global_alloc=prior_peak_alloc;global_reserved=prior_peak_reserved
    for pair in policy_coverage_shard(shard,bucket):
        check();torch.cuda.synchronize(0)
        global_alloc=max(global_alloc,int(torch.cuda.max_memory_allocated(0)));global_reserved=max(global_reserved,int(torch.cuda.max_memory_reserved(0)))
        torch.cuda.reset_peak_memory_stats(0);elapsed=backward(pair,1.0);check()
        if not all(p.grad is not None and bool(torch.isfinite(p.grad).all()) for p in params) or not any(bool(torch.count_nonzero(p.grad)) for p in params):raise T.DPOError('policy coverage gradients invalid')
        torch.cuda.synchronize(0)
        coverage.append({'bucket':bucket[pair['pair_id']],'pair_id':pair['pair_id'],'microbatch_pairs':1,'optimizer_state_allocated':True,'seconds':elapsed,'peak_allocated_bytes':int(torch.cuda.max_memory_allocated(0)),'peak_reserved_bytes':int(torch.cuda.max_memory_reserved(0))})
        measured_ids.add(pair['pair_id']);optimizer.zero_grad(set_to_none=True)
    validate_policy_coverage(coverage,longest_id);check()
    for pair in shard[T.ACCUMULATION:]:
        if pair['pair_id'] in measured_ids:continue
        if time.monotonic()+max(30.0,step_seconds)>=deadline:break
        backward(pair,1.0);optimizer.zero_grad(set_to_none=True)
    check();peaks={'peak_allocated_bytes':max(global_alloc,int(torch.cuda.max_memory_allocated(0))),'peak_reserved_bytes':max(global_reserved,int(torch.cuda.max_memory_reserved(0)))}
    report={'schema':'hardread.dpo.single-h100-observation/v1','status':'observed','qualification_scope':'single_h100','binding':binding,'config_sha256':args.config_sha256,'approval_sha256':args.approval_sha256,'pair_count':len(pairs),'sample_ids_sha256':T.fingerprint(ids),'sample_ids':ids,'histogram':hist,'reference_longest_pair_peak':longest_peak,'policy_coverage':coverage,'longest_policy_pair_peak':next(x for x in coverage if x['pair_id']==longest_id),'reference_cache_sha256':cache_sha,'reference_load_seconds':ref_load,'policy_load_seconds':policy_load,'optimizer_step_seconds':step_seconds,'per_bucket_seconds':times,'device_name':torch.cuda.get_device_name(0),'device_free_before_bytes':int(free),'device_total_bytes':int(total),**peaks,'gradient_update_completed':True,'elapsed_seconds':time.monotonic()-started,'python':platform.python_version(),'versions':runtime['versions'],'runtime_inventory_sha256':runtime_sha,'response_position_parity':parity}
    if report['elapsed_seconds']>cfg['max_seconds']:raise T.DPOError('qualification deadline exceeded')
    T.exclusive_bytes(output/'observation.json',T.canonical(report)+b'\n')
    del policy,optimizer;return {'status':'observed','observation_sha256':T.file_sha(output/'observation.json')}


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('config','config-sha256','code-root','data','base','adapter','selection','approval','approval-sha256','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--execute',action='store_true');args=p.parse_args(argv);run(args)

if __name__=='__main__':main()
