"""Prospective DPO math and inventory checks. No model or data access on import."""
import hashlib
import json
import math

TRAIN_ENABLED = False
BETA = 0.1
LEARNING_RATE = 5e-6
WORLD_SIZE = 6
ACCUMULATION = 8
MAX_SECONDS = 86400
SOURCE_INDEX_SHA256 = "7b598b22b129c4a6cfcd11edcd23e3aeaa0f34e697388fa12b39a2a5ecab5438"

class DPOError(ValueError):
    pass

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()

def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def response_logps(logits, labels):
    """Causal next-token summed log probability; -100 masks prompt and padding."""
    import torch
    if logits.ndim != 3 or labels.ndim != 2 or logits.shape[:2] != labels.shape or labels.shape[1] < 2:
        raise DPOError('invalid logit or label shape')
    target = labels[:, 1:]
    mask = target.ne(-100)
    if not bool(mask.any(dim=1).all()):
        raise DPOError('empty response')
    if bool(((target < 0) & mask).any()) or bool((target[mask] >= logits.shape[-1]).any()):
        raise DPOError('invalid response token')
    safe = target.masked_fill(~mask, 0)
    values = torch.log_softmax(logits[:, :-1, :].float(), dim=-1).gather(-1, safe.unsqueeze(-1)).squeeze(-1)
    return (values * mask).sum(dim=-1)

def dpo_loss(policy_chosen, policy_rejected, ref_chosen, ref_rejected):
    import torch.nn.functional as F
    # Reference scores are immutable cached scalars and must not receive gradients.
    return -F.logsigmoid(BETA * ((policy_chosen-policy_rejected) - (ref_chosen.detach()-ref_rejected.detach())))

def validate_reference_cache(pair_ids, cache, binding):
    """Bind each unique reference score to the entire frozen model/data/code context."""
    required = {'pair_manifest_sha256', 'pairs_sha256', 'base_snapshot_sha256', 'adapter_model_sha256',
                'adapter_config_sha256', 'xl6_selection_sha256', 'tokenizer_sha256', 'template_sha256', 'environment_sha256', 'code_sha256'}
    if not isinstance(binding, dict) or set(binding) != required or any(not isinstance(v, str) or len(v) != 64 or any(c not in '0123456789abcdef' for c in v) for v in binding.values()):
        raise DPOError('invalid cache binding')
    if not isinstance(pair_ids, list) or not pair_ids or len(set(pair_ids)) != len(pair_ids) or any(not isinstance(x, str) or not x for x in pair_ids):
        raise DPOError('invalid pair inventory')
    if not isinstance(cache, dict) or set(cache) != {'binding_sha256', 'records'} or cache['binding_sha256'] != fingerprint(binding) or not isinstance(cache['records'], list):
        raise DPOError('reference binding mismatch')
    seen = {}
    for r in cache['records']:
        if not isinstance(r, dict) or set(r) != {'pair_id', 'chosen', 'rejected'} or r['pair_id'] in seen:
            raise DPOError('reference record duplicate or malformed')
        for key in ('chosen', 'rejected'):
            if type(r[key]) not in (int, float) or not math.isfinite(r[key]):
                raise DPOError('nonfinite reference score')
        seen[r['pair_id']] = r
    if set(seen) != set(pair_ids):
        raise DPOError('reference inventory mismatch')
    return seen

def rank_steps(n, rank, world_size=WORLD_SIZE, accumulation=ACCUMULATION):
    """Each real pair once; equal rank schedules use None slots, never duplicate pairs."""
    if type(n) is not int or n <= 0 or type(rank) is not int or type(world_size) is not int or not 0 <= rank < world_size or type(accumulation) is not int or accumulation <= 0:
        raise DPOError('invalid partition')
    total = ((n + world_size * accumulation - 1) // (world_size * accumulation)) * world_size * accumulation
    return [i if i < n else None for i in range(rank, total, world_size)]

def scaled_ddp_pair_loss(loss, actual_in_update, world_size=WORLD_SIZE):
    """Undo DDP's rank average and normalize each partial or full global batch."""
    if type(actual_in_update) is not int or not 1 <= actual_in_update <= world_size * ACCUMULATION:
        raise DPOError('invalid global batch count')
    return loss * (world_size / actual_in_update)

def verify_file_pins(root, pins):
    """Verify declared local source/input files without following symlinks."""
    from pathlib import Path
    if not isinstance(pins, dict) or not pins:
        raise DPOError('file pins missing')
    root=Path(root).resolve()
    for relative, expected in pins.items():
        if not isinstance(relative,str) or not relative or not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):
            raise DPOError('invalid file pin')
        path=Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise DPOError('unsafe pinned path')
        cursor=root
        for part in path.parts:
            cursor=cursor/part
            if cursor.is_symlink():
                raise DPOError('symlink in pinned path')
        if not cursor.is_file():
            raise DPOError('pinned file missing')
        hasher=hashlib.sha256()
        with cursor.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):
                hasher.update(block)
        if hasher.hexdigest()!=expected:
            raise DPOError('pinned file differs')

def disable_dropout(model):
    """Disable stochastic dropout in both otherwise-identical PEFT arms."""
    import torch.nn as nn
    for module in model.modules():
        if isinstance(module, nn.modules.dropout._DropoutNd):
            module.p = 0.0
        for name in ('attention_dropout','hidden_dropout','dropout_rate','resid_pdrop','embd_pdrop','attn_pdrop'):
            value=getattr(module,name,None)
            if type(value) in (int,float): setattr(module,name,0.0)
        config=getattr(module,'config',None)
        if config is not None:
            for name in ('attention_dropout','hidden_dropout','dropout','dropout_rate','resid_pdrop','embd_pdrop','attn_pdrop'):
                value=getattr(config,name,None)
                if type(value) in (int,float): setattr(config,name,0.0)

def load_xl6(base_path, adapter_path, *, trainable, device):
    """Caller must verify all source hashes before invoking; local bytes only."""
    import torch
    from transformers import AutoModelForImageTextToText, BitsAndBytesConfig
    from peft import PeftModel
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                               bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16,
                               bnb_4bit_quant_storage=torch.bfloat16)
    base = AutoModelForImageTextToText.from_pretrained(str(base_path), local_files_only=True,
        quantization_config=quant, dtype=torch.bfloat16, attn_implementation='flash_attention_2',
        device_map={'':device})
    if trainable:
        from peft import prepare_model_for_kbit_training
        base = prepare_model_for_kbit_training(base, use_gradient_checkpointing=True)
    model = PeftModel.from_pretrained(base, str(adapter_path), is_trainable=trainable, local_files_only=True)
    disable_dropout(model)
    if trainable:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()
    else:
        model.eval()
        for p in model.parameters():
            p.requires_grad_(False)
    return model

def score_arm(model, arm, device):
    import torch
    if not isinstance(arm, dict) or set(arm) != {'input_ids', 'labels'}:
        raise DPOError('invalid arm')
    ids=arm['input_ids']; labels=arm['labels']
    if not isinstance(ids,list) or not isinstance(labels,list) or len(ids)!=len(labels) or not 1 < len(ids) <= 2048:
        raise DPOError('invalid arm tokens')
    inputs=torch.tensor([ids],dtype=torch.long,device=device)
    targets=torch.tensor([labels],dtype=torch.long,device=device)
    return response_logps(model(input_ids=inputs, attention_mask=torch.ones_like(inputs), use_cache=False).logits, targets)[0]

def score_pair(model, pair, device, pad_id):
    """Use one DDP forward for both arms, with attention and response masks."""
    import torch
    arms=[pair.get('chosen'),pair.get('rejected')]
    if any(not isinstance(x,dict) or set(x)!={'input_ids','labels'} for x in arms):
        raise DPOError('invalid pair arms')
    lengths=[len(a['input_ids']) for a in arms]
    if any(not 1 < n <= 2048 or len(a['labels']) != n for a,n in zip(arms,lengths)):
        raise DPOError('invalid pair tokens')
    width=max(lengths); ids=[]; labels=[]; masks=[]
    for arm,n in zip(arms,lengths):
        ids.append(arm['input_ids']+[pad_id]*(width-n))
        labels.append(arm['labels']+[-100]*(width-n))
        masks.append([1]*n+[0]*(width-n))
    ids=torch.tensor(ids,dtype=torch.long,device=device)
    labels=torch.tensor(labels,dtype=torch.long,device=device)
    mask=torch.tensor(masks,dtype=torch.long,device=device)
    scores=response_logps(model(input_ids=ids,attention_mask=mask,use_cache=False).logits,labels)
    return scores[0],scores[1]

def reference_partition(model, pairs, rank, device, pad_id=0):
    """Score this rank's unique slice under a frozen eval model."""
    import torch
    if model.training or any(p.requires_grad for p in model.parameters()):
        raise DPOError('reference model must be frozen and in eval mode')
    records=[]
    with torch.no_grad():
        for index in rank_steps(len(pairs), rank):
            if index is None:
                continue
            pair=pairs[index]
            ch, re = score_pair(model,pair,device,pad_id)
            chosen=float(ch.cpu()); rejected=float(re.cpu())
            if not math.isfinite(chosen) or not math.isfinite(rejected):
                raise DPOError('nonfinite reference')
            records.append({'pair_id':pair['pair_id'], 'chosen':chosen, 'rejected':rejected})
    return records

def train_epoch(model, optimizer, pairs, reference, rank, device, *, world_size=WORLD_SIZE, pad_id=0, deadline=None, collective_deadline=None):
    """One epoch over disjoint real pairs; caller supplies a DDP-wrapped model.

    All ranks perform identical forward/backward counts. Padding uses a zero loss;
    partial updates normalize by their true global count, not by 48.
    """
    import torch
    if world_size != WORLD_SIZE:
        raise DPOError('expected six ranks')
    slots=rank_steps(len(pairs),rank,world_size)
    model.train()
    disable_dropout(model)
    optimizer.zero_grad(set_to_none=True)
    for offset, index in enumerate(slots):
        pair=pairs[0 if index is None else index]
        chosen,rejected=score_pair(model,pair,device,pad_id)
        ref=reference[pair['pair_id']]
        rc=torch.tensor(ref['chosen'],device=device); rr=torch.tensor(ref['rejected'],device=device)
        loss=dpo_loss(chosen,rejected,rc,rr)
        update=offset//ACCUMULATION
        count=min(world_size*ACCUMULATION,len(pairs)-update*world_size*ACCUMULATION)
        if index is None:
            loss=loss*0
        scaled_ddp_pair_loss(loss,count,world_size).backward()
        if (offset+1)%ACCUMULATION == 0:
            if collective_deadline is not None:
                collective_deadline(deadline)
            optimizer.step(); optimizer.zero_grad(set_to_none=True)
    return {'pairs':len(pairs), 'rank_slots':len(slots), 'updates':len(slots)//ACCUMULATION, 'epochs':1}

def file_sha(path):
    from pathlib import Path
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()

def verify_tree(root, expected):
    """Require a complete, non-symlink file inventory, then hash every byte."""
    from pathlib import Path
    root=Path(root)
    if root.is_symlink() or not root.is_dir() or not isinstance(expected,dict) or not expected:
        raise DPOError('invalid tree')
    actual=[]
    for p in root.rglob('*'):
        if p.is_symlink(): raise DPOError('symlink in tree')
        if p.is_file(): actual.append(p.relative_to(root).as_posix())
        elif not p.is_dir(): raise DPOError('nonregular tree member')
    if set(actual)!=set(expected): raise DPOError('tree inventory differs')
    verify_file_pins(root,expected)
    return fingerprint(expected)

def validate_pairs(pairs, manifest, config):
    """Verify frozen pair structure and all recorded prompt/response commitments."""
    if not isinstance(pairs,list) or not pairs or len(pairs)>100000 or manifest.get('count')!=len(pairs) or manifest.get('source_index_sha256',manifest.get('index_sha256'))!=config['source_index_sha256']:
        raise DPOError('pair manifest differs')
    if manifest.get('tokenizer_sha256')!=config['tokenizer_sha256'] or manifest.get('template_sha256')!=config['template_sha256']:
        raise DPOError('pair tokenizer differs')
    ids=set(); decisions=set(); content=set()
    for pair in pairs:
        if not isinstance(pair,dict): raise DPOError('malformed pair')
        uid=pair.get('decision_uid'); pid=pair.get('pair_id')
        if not isinstance(uid,str) or not uid or uid in decisions or not isinstance(pid,str) or pid in ids:
            raise DPOError('duplicate pair or decision')
        provkeys=('archive_sha256','decision_uid','game_key','component','chosen_action','rejected_action','value_gap','agreeing_worlds','positive_worlds')
        try: prov={k:pair[k] for k in provkeys}
        except KeyError as e: raise DPOError('missing pair provenance') from e
        if fingerprint(prov)!=pid or pair.get('chosen_id')==pair.get('rejected_id') or pair.get('chosen_action')==pair.get('rejected_action') or type(pair.get('agreeing_worlds')) is not int or pair['agreeing_worlds']<3 or type(pair.get('positive_worlds')) is not int or pair['positive_worlds']<pair['agreeing_worlds'] or type(pair.get('value_gap')) not in (int,float) or not math.isfinite(pair['value_gap']) or pair['value_gap'] < .05:
            raise DPOError('invalid provenance')
        arms=[]
        for key in ('chosen','rejected'):
            arm=pair.get(key)
            if not isinstance(arm,dict) or set(arm)!={'input_ids','labels'}: raise DPOError('invalid arm')
            seq=arm['input_ids']; labels=arm['labels']
            if not isinstance(seq,list) or not isinstance(labels,list) or len(seq)!=len(labels) or not 1<len(seq)<=2048 or any(type(x) is not int or x<0 for x in seq): raise DPOError('invalid tokens')
            try: n=next(i for i,x in enumerate(labels) if x!=-100)
            except StopIteration as e: raise DPOError('empty response') from e
            if not n or labels!=[-100]*n+seq[n:] or seq[-1]!=config['eot_id'] or fingerprint(seq[:n])!=pair.get('prompt_sha256') or fingerprint(seq[n:])!=pair.get(key+'_response_sha256'):
                raise DPOError('response mask or commitment differs')
            arms.append((seq,n))
        if arms[0][1]!=arms[1][1] or arms[0][0][:arms[0][1]]!=arms[1][0][:arms[1][1]] or len(arms[0][0])!=len(arms[1][0]) or sum(x!=y for x,y in zip(arms[0][0],arms[1][0]))!=1:
            raise DPOError('pair target structure differs')
        identity=fingerprint({'chosen':pair['chosen'],'rejected':pair['rejected']})
        if identity in content: raise DPOError('duplicate pair content')
        content.add(identity); ids.add(pid); decisions.add(uid)
    return ids

def exclusive_bytes(path, data):
    import os
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    if file_sha(path)!=hashlib.sha256(data).hexdigest(): raise DPOError('published bytes differ')

def runtime_config(config, *, config_sha, qualification=False):
    """Validate reviewable fixed parameters and evidence pins before model load."""
    required={'schema','status','code_files','base_files','adapter_files','source_index_sha256','pair_manifest_sha256','pairs_sha256','tokenizer_sha256','template_sha256','environment_sha256','environment_id','versions','run_name','pad_id','eot_id','timing_proof','smoke_proof','max_seconds','source_review_sha256','xl6_selection_sha256','job_resources'}
    if qualification: required-={'timing_proof','smoke_proof'}
    schema='hardread.dpo.qualification/v1' if qualification else 'hardread.dpo.job/v1'
    if not isinstance(config,dict) or set(config)!=required or config['schema']!=schema or config['status']!='approved': raise DPOError('job config missing')
    if config['source_index_sha256']!=SOURCE_INDEX_SHA256: raise DPOError('source index differs')
    if type(config['max_seconds']) is not int or not 0<config['max_seconds']<=(7200 if qualification else MAX_SECONDS): raise DPOError('invalid job limit')
    import re
    if not isinstance(config['run_name'],str) or not re.fullmatch(r'hrdpo-[a-z0-9][a-z0-9-]{0,63}',config['run_name']): raise DPOError('invalid output name')
    for k in ('source_index_sha256','pair_manifest_sha256','pairs_sha256','tokenizer_sha256','template_sha256','environment_sha256','source_review_sha256','xl6_selection_sha256'):
        if not isinstance(config[k],str) or not re.fullmatch('[0-9a-f]{64}',config[k]): raise DPOError('invalid config hash')
    if type(config['pad_id']) is not int or config['pad_id']<0 or config['eot_id']!=200008: raise DPOError('invalid special tokens')
    for k in ('code_files','base_files','adapter_files','versions'):
        if not isinstance(config[k],dict) or not config[k]: raise DPOError('missing pinned inventory')
    if not {'azure/sft/train_dpo.py','tools/sft/build_dpo.py','tools/sft/serialize.py','tools/sft/glimmer_chat.py','tools/sft/data_admission.py','tools/sft/data_admission_runtime.py','tools/sft/build_sft.py','tools/sft/dex.json','tools/dataset/extract.py','tools/belief/roster_hash.py','tools/human/parse_replays.py','azure/student/p6_trainonly_inspect.py','azure/sft/submit_dpo.py','azure/sft/submit_dpo.sh','azure/sft/dpo_job.yml.template','azure/sft/qualify_dpo.py','azure/sft/submit_qualification.py','azure/sft/qualification_job.yml.template','azure/absolute_deadline.sh'}.issubset(config['code_files']): raise DPOError('source closure missing')
    if not {'torch','transformers','peft','bitsandbytes','accelerate','tokenizers','safetensors','jinja2'}.issubset(config['versions']): raise DPOError('environment package pins missing')
    if not {'adapter_model.safetensors','adapter_config.json'}.issubset(config['adapter_files']): raise DPOError('XL6 adapter files missing')
    resources=config['job_resources']
    expected_resources={'compute','pairs_uri','base_uri','xl6_uri','timing_uri','smoke_uri','selection_uri','output_uri','resource_group','workspace','approval_uri'}
    if qualification: expected_resources=expected_resources-{'timing_uri','smoke_uri'}
    if not isinstance(resources,dict) or set(resources)!=expected_resources or any(not isinstance(v,str) or not v for v in resources.values()): raise DPOError('job resource pins missing')
    if not isinstance(config['environment_id'],str) or not config['environment_id']:
        raise DPOError('environment identity missing')
    if 'config.json' not in config['base_files'] or not any(x.endswith('.safetensors') for x in config['base_files']): raise DPOError('base weights missing')
    if 'tokenizer.json' not in config['base_files'] or 'chat_template.jinja' not in config['base_files'] or config['base_files']['tokenizer.json']!=config['tokenizer_sha256'] or config['base_files']['chat_template.jinja']!=config['template_sha256']:
        raise DPOError('base tokenizer pin differs')
    for key in (() if qualification else ('timing_proof','smoke_proof')):
        item=config[key]
        if not isinstance(item,dict) or set(item)!={'sha256','status','binding_sha256'} or item['status']!='accepted' or not re.fullmatch('[0-9a-f]{64}',item['sha256']) or not re.fullmatch('[0-9a-f]{64}',item['binding_sha256']): raise DPOError('required preflight proof missing')
    if not isinstance(config_sha,str) or not re.fullmatch('[0-9a-f]{64}',config_sha): raise DPOError('config hash missing')
    return config

def check_root_approval(path, expected_sha, config_sha, purpose, code_sha=None):
    from pathlib import Path
    import re
    if not isinstance(expected_sha,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha) or file_sha(path)!=expected_sha: raise DPOError('root approval bytes differ')
    doc=json.loads(Path(path).read_bytes())
    if not isinstance(doc,dict) or doc.get('status')!='approved' or doc.get('purpose')!=purpose or doc.get('config_sha256')!=config_sha or not isinstance(doc.get('owner'),str) or not doc['owner'].strip() or not re.fullmatch('[0-9a-f]{64}',str(doc.get('code_sha256',''))): raise DPOError('root approval scope differs')
    if code_sha is not None and doc['code_sha256']!=code_sha: raise DPOError('root approval source differs')
    return doc

def load_inputs(config_path, config_sha, code_root, data_dir, base_dir, adapter_dir, selection_path, *, qualification=False):
    """Verify fixed bytes and operational evidence before the first model load."""
    import json
    import importlib.metadata
    from pathlib import Path
    if file_sha(config_path)!=config_sha: raise DPOError('job config bytes differ')
    cfg=runtime_config(json.loads(Path(config_path).read_bytes()),config_sha=config_sha,qualification=qualification)
    verify_file_pins(code_root,cfg['code_files'])
    if Path(__file__).resolve()!=(Path(code_root)/'azure/sft/train_dpo.py').resolve(): raise DPOError('loaded source differs')
    base_hash=verify_tree(base_dir,cfg['base_files']); adapter_hash=verify_tree(adapter_dir,cfg['adapter_files'])
    if fingerprint({'environment_id':cfg['environment_id'],'versions':cfg['versions']})!=cfg['environment_sha256']: raise DPOError('environment fingerprint differs')
    for name,version in cfg['versions'].items():
        try: actual=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as e: raise DPOError('environment package missing') from e
        if actual!=version: raise DPOError('environment package differs')
    data=Path(data_dir); manifest_path=data/'manifest.json'; pairs_path=data/'pairs.jsonl'
    if file_sha(manifest_path)!=cfg['pair_manifest_sha256'] or file_sha(pairs_path)!=cfg['pairs_sha256']: raise DPOError('pair files differ')
    manifest=json.loads(manifest_path.read_bytes())
    if manifest.get('dpo_source_review_sha256')!=cfg['source_review_sha256']: raise DPOError('pair source review differs')
    if manifest.get('pairs_sha256')!=cfg['pairs_sha256']: raise DPOError('manifest data hash differs')
    with pairs_path.open(encoding='utf-8') as f: pairs=[json.loads(line) for line in f]
    validate_pairs(pairs,manifest,cfg)
    if file_sha(pairs_path)!=cfg['pairs_sha256'] or file_sha(manifest_path)!=cfg['pair_manifest_sha256']: raise DPOError('pair files changed during read')
    binding={'pair_manifest_sha256':cfg['pair_manifest_sha256'], 'pairs_sha256':cfg['pairs_sha256'],
             'base_snapshot_sha256':base_hash, 'adapter_model_sha256':cfg['adapter_files']['adapter_model.safetensors'],
             'adapter_config_sha256':cfg['adapter_files']['adapter_config.json'], 'xl6_selection_sha256':cfg['xl6_selection_sha256'], 'tokenizer_sha256':cfg['tokenizer_sha256'],
             'template_sha256':cfg['template_sha256'], 'environment_sha256':cfg['environment_sha256'],
             'code_sha256':fingerprint(cfg['code_files'])}
    if file_sha(selection_path)!=cfg['xl6_selection_sha256']: raise DPOError('XL6 selection proof differs')
    selection=json.loads(Path(selection_path).read_bytes())
    if not isinstance(selection,dict) or selection.get('status')!='selected' or selection.get('adapter_model_sha256')!=binding['adapter_model_sha256'] or selection.get('adapter_config_sha256')!=binding['adapter_config_sha256']:
        raise DPOError('selected XL6 identity differs')
    return cfg,pairs,binding

def load_preflight(config_path, config_sha, code_root, data_dir, base_dir, adapter_dir, timing_path, smoke_path, selection_path):
    from pathlib import Path
    cfg,pairs,binding=load_inputs(config_path,config_sha,code_root,data_dir,base_dir,adapter_dir,selection_path)
    for key,path in (('timing_proof',timing_path),('smoke_proof',smoke_path)):
        claim=cfg[key]
        if file_sha(path)!=claim['sha256']: raise DPOError('preflight proof bytes differ')
        proof=json.loads(Path(path).read_bytes())
        if not isinstance(proof,dict) or proof.get('status')!='accepted' or proof.get('binding_sha256')!=fingerprint(binding) or claim['binding_sha256']!=fingerprint(binding): raise DPOError('preflight proof binding differs')
        if key=='timing_proof' and (type(proof.get('projected_seconds')) not in (int,float) or not math.isfinite(proof['projected_seconds']) or not 0<proof['projected_seconds']<=cfg['max_seconds']): raise DPOError('full epoch exceeds time budget')
        if key=='smoke_proof':
            if proof.get('six_rank_collective') is not None and proof.get('six_rank_collective') is not False: raise DPOError('single-GPU proof cannot assert six ranks')
            if proof.get('qualification_scope')!='single_h100' or proof.get('adapter_load_update') is not True or proof.get('memory_headroom') is not True: raise DPOError('GPU smoke incomplete')
            total=proof.get('device_total_bytes');peak=proof.get('peak_reserved_bytes');headroom=proof.get('required_headroom_bytes');observed=proof.get('observed_receipt_sha256')
            if any(type(x) is not int or x<=0 for x in (total,peak,headroom)) or total-peak<headroom or not isinstance(observed,str) or len(observed)!=64 or any(c not in '0123456789abcdef' for c in observed): raise DPOError('GPU headroom proof invalid')
    return cfg,pairs,binding

def trainable_fingerprint(model):
    """Hash exact trainable tensors in stable name, shape, dtype and byte order."""
    import torch
    h=hashlib.sha256(); count=0
    for name,param in sorted(model.named_parameters()):
        if not param.requires_grad: continue
        h.update(canonical([name,list(param.shape),str(param.dtype)])); h.update(b'\0')
        h.update(param.detach().contiguous().cpu().view(torch.uint8).numpy().tobytes()); count+=1
    if not count: raise DPOError('no trainable tensors')
    return h.hexdigest()


def validate_collective_reports(reports, result):
    if len(reports)!=WORLD_SIZE or any(not isinstance(x,dict) for x in reports): raise DPOError('collective report count differs')
    if sorted(x.get('rank') for x in reports)!=list(range(WORLD_SIZE)): raise DPOError('collective ranks differ')
    fields=('world_size','backend','pair_id_sha256','gradient_sha256','trainable_before_sha256','trainable_after_sha256','gradient_parameters','optimizer_step')
    if any(any(x.get(k)!=result.get(k) for k in fields) for x in reports): raise DPOError('collective rank gradient or weight mismatch')
    if result.get('backend')!='nccl' or result.get('world_size')!=WORLD_SIZE or result.get('optimizer_step') is not False or result.get('trainable_before_sha256')!=result.get('trainable_after_sha256') or type(result.get('gradient_parameters')) is not int or result['gradient_parameters']<=0: raise DPOError('collective evidence invalid')


def six_rank_startup_collective(ddp,policy,pair,reference,rank,device,pad_id,output,deadline_check,deadline):
    """Real NCCL DDP forward/backward barrier; no optimizer update is permitted."""
    import os
    import torch
    import torch.distributed as dist
    from pathlib import Path
    if not dist.is_initialized() or dist.get_backend()!='nccl' or dist.get_world_size()!=WORLD_SIZE or not torch.cuda.is_available():raise DPOError('six-rank NCCL runtime required')
    if rank!=dist.get_rank() or not 0<=rank<WORLD_SIZE:raise DPOError('collective rank differs')
    before=trainable_fingerprint(policy);ddp.train();disable_dropout(ddp);ddp.zero_grad(set_to_none=True)
    deadline_check(deadline)
    chosen,rejected=score_pair(ddp,pair,device,pad_id);ref=reference[pair['pair_id']]
    loss=dpo_loss(chosen,rejected,torch.tensor(ref['chosen'],device=device),torch.tensor(ref['rejected'],device=device))
    if not bool(torch.isfinite(loss)):raise DPOError('collective loss nonfinite')
    loss.backward()
    h=hashlib.sha256();nonzero=False;finite=True;count=0
    for name,param in sorted(policy.named_parameters()):
        if not param.requires_grad:continue
        grad=param.grad
        if grad is None:finite=False;continue
        finite=finite and bool(torch.isfinite(grad).all());nonzero=nonzero or bool(torch.count_nonzero(grad));count+=1
        h.update(canonical([name,list(grad.shape),str(grad.dtype)]));h.update(b'\0');h.update(grad.detach().contiguous().cpu().view(torch.uint8).numpy().tobytes())
    valid=torch.tensor([int(finite and nonzero and count>0)],device=device,dtype=torch.int32)
    dist.all_reduce(valid,op=dist.ReduceOp.MIN)
    if int(valid.item())!=1:raise DPOError('collective gradient is missing, nonfinite or zero')
    gradient_sha=h.hexdigest();ddp.zero_grad(set_to_none=True);after=trainable_fingerprint(policy)
    if before!=after:raise DPOError('collective changed policy weights')
    result={'rank':rank,'world_size':WORLD_SIZE,'backend':'nccl','pair_id_sha256':hashlib.sha256(pair['pair_id'].encode()).hexdigest(),'gradient_sha256':gradient_sha,'trainable_before_sha256':before,'trainable_after_sha256':after,'gradient_parameters':count,'optimizer_step':False}
    reports=[None]*WORLD_SIZE;dist.all_gather_object(reports,result)
    validate_collective_reports(reports,result)
    exclusive_bytes(Path(output)/('collective-rank-%d.json'%rank),canonical(result)+b'\n')
    dist.barrier();deadline_check(deadline)
    if rank==0:exclusive_bytes(Path(output)/'collective.json',canonical({'schema':'hardread.dpo.six-rank-collective/v1','status':'complete','reports':reports})+b'\n')
    dist.barrier();return reports

def execute_job(args):
    """One bounded six-rank job, publishing only a complete cache and final epoch."""
    if not getattr(args,'execute',False) or __import__('os').environ.get('HARDREAD_DPO_TRAIN')!='1': raise DPOError('DPO training disabled pending review and admission')
    import os
    import gc
    import time
    from pathlib import Path
    from datetime import timedelta
    import torch
    import torch.distributed as dist
    from torch.nn.parallel import DistributedDataParallel as DDP
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',WANDB_MODE='offline',NCCL_IB_DISABLE='1')
    approval=check_root_approval(args.approval,args.approval_sha256,args.config_sha256,'six_h100_full_epoch')
    rank=int(os.environ['RANK']); size=int(os.environ['WORLD_SIZE']); local=int(os.environ['LOCAL_RANK'])
    if size!=WORLD_SIZE or local!=0 or not 0<=rank<size or not torch.cuda.is_available() or torch.cuda.device_count()!=1: raise DPOError('expected one H100 per six ranks')
    torch.cuda.set_device(local)
    if 'H100' not in torch.cuda.get_device_name(local): raise DPOError('unexpected GPU')
    started=time.monotonic()
    dist.init_process_group(backend='nccl',timeout=timedelta(seconds=600))
    try:
        cfg,pairs,binding=load_preflight(args.config,args.config_sha256,args.code_root,args.data,args.base,args.adapter,args.timing,args.smoke,args.selection)
        check_root_approval(args.approval,args.approval_sha256,args.config_sha256,'six_h100_full_epoch',binding['code_sha256'])
        deadline=started+cfg['max_seconds']
        def deadline_check(limit):
            expired=torch.tensor([int(time.monotonic()>=limit)],device=local)
            dist.all_reduce(expired,op=dist.ReduceOp.MAX)
            if int(expired.item()): raise DPOError('job deadline exhausted before completion')
        deadline_check(deadline)
        identities=[p['pair_id'] for p in pairs]
        # Rank 0 creates a unique child of the allocated output mount, never reuses it.
        output=Path(args.out)/cfg['run_name']
        if rank==0:
            if Path(args.out).is_symlink() or not Path(args.out).is_dir(): raise DPOError('output mount invalid')
            output.mkdir(mode=0o700)
        dist.barrier(); deadline_check(deadline)
        refmodel=load_xl6(args.base,args.adapter,trainable=False,device=local)
        local_cache=reference_partition(refmodel,pairs,rank,local,cfg['pad_id'])
        parts=[None]*size if rank==0 else None
        dist.gather_object(local_cache,parts,dst=0)
        if rank==0:
            records=[item for part in parts for item in part]
            cache={'binding_sha256':fingerprint(binding),'records':records}
            validate_reference_cache(identities,cache,binding)
            cache_raw=canonical(cache)+b'\n'
            exclusive_bytes(output/'reference-cache.json',cache_raw)
            cache_sha=hashlib.sha256(cache_raw).hexdigest()
        else:
            cache_sha=None
        digest_box=[cache_sha]; dist.broadcast_object_list(digest_box,src=0); dist.barrier()
        if file_sha(output/'reference-cache.json')!=digest_box[0]: raise DPOError('published cache differs')
        cache=json.loads((output/'reference-cache.json').read_bytes())
        reference=validate_reference_cache(identities,cache,binding)
        del refmodel,local_cache,cache; gc.collect(); torch.cuda.empty_cache()
        deadline_check(deadline)
        if verify_tree(args.base,cfg['base_files'])!=binding['base_snapshot_sha256'] or verify_tree(args.adapter,cfg['adapter_files'])!=fingerprint(cfg['adapter_files']): raise DPOError('model inputs changed after reference')
        policy=load_xl6(args.base,args.adapter,trainable=True,device=local)
        params=[p for p in policy.parameters() if p.requires_grad]
        if not params or any('lora_' not in n for n,p in policy.named_parameters() if p.requires_grad): raise DPOError('unexpected trainable parameters')
        ddp=DDP(policy,device_ids=[local],output_device=local,find_unused_parameters=False,broadcast_buffers=False)
        collective=six_rank_startup_collective(ddp,policy,pairs[0],reference,rank,local,cfg['pad_id'],output,deadline_check,deadline)
        optimizer=torch.optim.AdamW(params,lr=LEARNING_RATE)
        report=train_epoch(ddp,optimizer,pairs,reference,rank,local,pad_id=cfg['pad_id'],deadline=deadline,collective_deadline=deadline_check)
        report.update(rank=rank,cache_sha256=digest_box[0],pair_manifest_sha256=cfg['pair_manifest_sha256'],trainable_sha256=trainable_fingerprint(policy))
        reports=[None]*size if rank==0 else None
        dist.gather_object(report,reports,dst=0)
        deadline_check(deadline)
        if rank==0:
            expected={k:report[k] for k in ('pairs','rank_slots','updates','epochs','cache_sha256','pair_manifest_sha256','trainable_sha256')}
            if sorted(x['rank'] for x in reports)!=list(range(size)) or any(any(x[k]!=v for k,v in expected.items()) for x in reports): raise DPOError('rank completion differs')
            # Model and optimizer are saved only after every rank confirms one full epoch.
            final=output/'final'; final.mkdir(mode=0o700)
            policy.save_pretrained(final, safe_serialization=True)
            torch.save(optimizer.state_dict(),final/'optimizer.pt')
            files={p.relative_to(final).as_posix():file_sha(p) for p in final.rglob('*') if p.is_file() and not p.is_symlink()}
            if not {'adapter_model.safetensors','adapter_config.json','optimizer.pt'}.issubset(files): raise DPOError('final adapter incomplete')
            verify_tree(final,files)
            terminal={'schema':'hardread.dpo.terminal/v1','status':'complete','epochs':1,'pair_count':len(pairs),
                      'updates':report['updates'],'beta':BETA,'learning_rate':LEARNING_RATE,'world_size':size,
                      'config_sha256':args.config_sha256,'binding':binding,'reference_cache_sha256':digest_box[0],
                      'final_files':files,'rank_reports':reports,'collective_sha256':file_sha(output/'collective.json'),'elapsed_seconds':time.monotonic()-started}
            if terminal['elapsed_seconds']>cfg['max_seconds']: raise DPOError('deadline exceeded before terminal')
            exclusive_bytes(output/'terminal.json',canonical(terminal)+b'\n')
        dist.barrier()
        if not (output/'terminal.json').is_file(): raise DPOError('terminal marker missing')
    finally:
        dist.destroy_process_group()

def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('config','config-sha256','code-root','data','base','adapter','timing','smoke','selection','approval','approval-sha256','out'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    execute_job(args)

if __name__ == '__main__':
    main()
