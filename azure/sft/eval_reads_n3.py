"""Disabled prospective N3 prompt-only greedy inference. Labels are never an input."""
import hashlib
import json
import math
import os
from pathlib import Path
from datetime import datetime, timezone
import re
import time

INFERENCE_ENABLED=os.environ.get('HARDREAD_N3_INFERENCE_ENABLED')=='1'
MAX_SECONDS=14400
EXPECTED_NATIVE_LORA_TENSORS=728
LORA_PARAMETER_MARKERS={'lora_A','lora_B','lora_embedding_A','lora_embedding_B'}
class N3RunError(ValueError):pass

def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise N3RunError('duplicate JSON key')
            result[key]=value
        return result
    def invalid(value):raise N3RunError('nonfinite JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def verify_tree(root,pins):
    root=Path(root)
    if root.is_symlink() or not root.is_dir() or not isinstance(pins,dict) or not pins:raise N3RunError('tree or pins missing')
    actual=[]
    for p in root.rglob('*'):
        if p.is_symlink(): raise N3RunError('symlink in model tree')
        if p.is_file():actual.append(p.relative_to(root).as_posix())
        elif not p.is_dir():raise N3RunError('nonregular model file')
    if set(actual)!=set(pins):raise N3RunError('model tree inventory differs')
    for name,expected in pins.items():
        if not re.fullmatch('[0-9a-f]{64}',str(expected)) or sha(root/name)!=expected:raise N3RunError('model bytes differ')

def inventory_sha256(pins):
    if not isinstance(pins,dict) or not pins:
        raise N3RunError('model inventory missing')
    payload=json.dumps(pins,sort_keys=True,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()

def validate_config(cfg):
    required={'schema','arm','status','inference_precision','base_precision','adapter_precision','adapter_parameter_tensors','prompt_sha256','cohort_manifest_sha256','source_review_sha256','selection_sha256',
              'tokenizer_sha256','template_sha256','code_files','base_files','adapter_files','deadline_unix','max_seconds','xl6_completion_sha256','run_name'}
    if not isinstance(cfg,dict) or set(cfg)!=required or cfg.get('schema')!='hardread.reads-n3.runtime/v2' or cfg.get('status')!='approved':raise N3RunError('runtime config differs')
    if cfg.get('inference_precision')!='native_bf16':raise N3RunError('inference precision differs')
    if (cfg.get('base_precision')!='bf16' or cfg.get('adapter_precision')!='fp32' or
        type(cfg.get('adapter_parameter_tensors')) is not int or
        cfg.get('adapter_parameter_tensors')!=EXPECTED_NATIVE_LORA_TENSORS):raise N3RunError('base/adapter precision policy differs')
    if cfg['arm'] not in ('xl6','selected_v2') or type(cfg['max_seconds']) is not int or not 0<cfg['max_seconds']<=MAX_SECONDS or type(cfg['deadline_unix']) is not int:raise N3RunError('arm or deadline differs')
    for key in ('prompt_sha256','cohort_manifest_sha256','source_review_sha256','selection_sha256','tokenizer_sha256','template_sha256'):
        if not isinstance(cfg[key],str) or not re.fullmatch('[0-9a-f]{64}',cfg[key]):raise N3RunError('runtime pin malformed')
    if cfg['arm']=='selected_v2' and (not isinstance(cfg['xl6_completion_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',cfg['xl6_completion_sha256'])):raise N3RunError('XL6 completion proof required')
    if cfg['arm']=='xl6' and cfg['xl6_completion_sha256'] is not None:raise N3RunError('unexpected XL6 completion proof')
    if not all(isinstance(cfg[k],dict) and cfg[k] for k in ('base_files','adapter_files','code_files')):raise N3RunError('model or code pins missing')
    if not isinstance(cfg['run_name'],str) or not re.fullmatch('hrreads-[a-z0-9-]{1,64}',cfg['run_name']):raise N3RunError('invalid output name')
    return cfg

def validate_memory_snapshot(snapshot):
    if not isinstance(snapshot,dict) or set(snapshot)!={'free_bytes','total_bytes'}:
        raise N3RunError('CUDA memory snapshot differs')
    free,total=snapshot['free_bytes'],snapshot['total_bytes']
    if type(free) is not int or type(total) is not int or free<=0 or total<=0 or free>total:
        raise N3RunError('CUDA memory snapshot differs')
    return {'free_bytes':free,'total_bytes':total}

def cuda_memory_snapshot(torch,device):
    try: free,total=torch.cuda.mem_get_info(device)
    except Exception as exc: raise N3RunError('CUDA memory query unavailable') from exc
    return validate_memory_snapshot({'free_bytes':free,'total_bytes':total})

def verify_native_bf16_residency(model,torch,device):
    expected=f'cuda:{device}'
    try: params=list(model.named_parameters())
    except Exception as exc: raise N3RunError('loaded model parameters unavailable') from exc
    if not params: raise N3RunError('loaded model has no parameters')
    base_floating=0
    adapter_tensors=0
    for name,param in params:
        if str(getattr(param,'device',None))!=expected:
            raise N3RunError('native model and adapter are not fully resident on the H100')
        try: is_float=param.is_floating_point()
        except Exception as exc: raise N3RunError('loaded model parameter dtype unavailable') from exc
        segments=str(name).split('.')
        is_adapter=any(part in LORA_PARAMETER_MARKERS for part in segments)
        if any(part.startswith('lora_') for part in segments) and not is_adapter:
            raise N3RunError('unexpected LoRA parameter type')
        if is_adapter:
            adapter_tensors+=1
            if not is_float or param.dtype is not torch.float32:
                raise N3RunError('LoRA adapter parameter dtype differs from FP32 policy')
            continue
        if is_float:
            if param.dtype is not torch.bfloat16:
                raise N3RunError('base model parameter dtype differs from BF16 policy')
            base_floating+=1
    if base_floating==0: raise N3RunError('loaded base model has no floating-point parameters')
    if adapter_tensors!=EXPECTED_NATIVE_LORA_TENSORS:
        raise N3RunError('LoRA adapter tensor count differs from pinned FP32 inventory')
    device_map=getattr(model,'hf_device_map',None)
    if device_map is not None:
        if not isinstance(device_map,dict) or not device_map:
            raise N3RunError('loaded model device map differs')
        for value in device_map.values():
            if type(value) is int:
                if value!=device: raise N3RunError('CPU/disk or off-device model map rejected')
            elif str(value)!=expected:
                raise N3RunError('CPU/disk or off-device model map rejected')
    return {'device':expected,'base_dtype':str(torch.bfloat16),'adapter_dtype':str(torch.float32),
            'base_floating_parameter_tensors':base_floating,'adapter_parameter_tensors':adapter_tensors}

def timing_allows(elapsed,completed,total,remaining):
    if completed!=20 or total!=440 or not math.isfinite(elapsed) or elapsed<=0 or not math.isfinite(remaining) or remaining<=0:
        raise N3RunError('timing projection inputs differ')
    return elapsed/completed*(total-completed)<remaining

def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')

def write_first20_timing(path,*,load_start,load_end,decode_start,checkpoint,deadline,checkpoint_epoch,
                         load_start_utc,load_end_utc,decode_start_utc,checkpoint_utc,source_sha256,config_sha256,
                         inference_precision,base_precision,adapter_precision,cuda_memory_before_load,cuda_memory_after_load,
                         loaded_model_device,loaded_model_base_dtype,loaded_model_adapter_dtype,
                         loaded_model_base_floating_parameter_tensors,loaded_model_adapter_parameter_tensors):
    monotonic=(load_start,load_end,decode_start,checkpoint)
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in monotonic):
        raise N3RunError('timing checkpoint monotonic values differ')
    if type(deadline) not in (int,float) or type(checkpoint_epoch) not in (int,float) or not math.isfinite(deadline) or not math.isfinite(checkpoint_epoch):
        raise N3RunError('timing checkpoint wall values differ')
    if load_end<load_start or decode_start<load_end or checkpoint<decode_start:
        raise N3RunError('timing checkpoint event order differs')
    for stamp in (load_start_utc,load_end_utc,decode_start_utc,checkpoint_utc):
        if not isinstance(stamp,str): raise N3RunError('timing checkpoint UTC timestamp differs')
        try: datetime.fromisoformat(stamp.replace('Z','+00:00'))
        except ValueError: raise N3RunError('timing checkpoint UTC timestamp differs')
    for digest in (source_sha256,config_sha256):
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise N3RunError('timing checkpoint source pin differs')
    if inference_precision!='native_bf16': raise N3RunError('timing checkpoint precision differs')
    if base_precision!='bf16' or adapter_precision!='fp32': raise N3RunError('timing checkpoint base/adapter precision policy differs')
    cuda_memory_before_load=validate_memory_snapshot(cuda_memory_before_load)
    cuda_memory_after_load=validate_memory_snapshot(cuda_memory_after_load)
    if (loaded_model_device!='cuda:0' or loaded_model_base_dtype!='torch.bfloat16' or
        loaded_model_adapter_dtype!='torch.float32' or
        type(loaded_model_base_floating_parameter_tensors) is not int or
        loaded_model_base_floating_parameter_tensors<=0 or
        type(loaded_model_adapter_parameter_tensors) is not int or
        loaded_model_adapter_parameter_tensors!=EXPECTED_NATIVE_LORA_TENSORS):
        raise N3RunError('timing checkpoint loaded model identity differs')
    load_seconds=load_end-load_start
    decode_seconds=checkpoint-decode_start
    available_remaining=deadline-checkpoint_epoch
    projected_remaining=decode_seconds/20*(440-20)
    within_budget=timing_allows(decode_seconds,20,440,available_remaining)
    record={'schema':'hardread.reads-n3.first20-timing/v2','source_sha256':source_sha256,
            'config_sha256':config_sha256,'inference_precision':inference_precision,
            'base_precision':base_precision,'adapter_precision':adapter_precision,
            'cuda_memory_before_load':cuda_memory_before_load,'cuda_memory_after_load':cuda_memory_after_load,
            'loaded_model_device':loaded_model_device,'loaded_model_base_dtype':loaded_model_base_dtype,
            'loaded_model_adapter_dtype':loaded_model_adapter_dtype,
            'loaded_model_base_floating_parameter_tensors':loaded_model_base_floating_parameter_tensors,
            'loaded_model_adapter_parameter_tensors':loaded_model_adapter_parameter_tensors,
            'load_start_utc':load_start_utc,'load_end_utc':load_end_utc,
            'decode_start_utc':decode_start_utc,'checkpoint_utc':checkpoint_utc,
            'load_seconds':load_seconds,'first20_decode_seconds':decode_seconds,
            'projected_remaining_seconds':projected_remaining,'available_remaining_seconds':available_remaining,
            'completed_decisions':20,'within_budget':within_budget}
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(record,f,sort_keys=True,separators=(',',':'))
        f.write('\n');f.flush();os.fsync(f.fileno())
    return record

def load_model(base,adapter,device):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForImageTextToText
    model=AutoModelForImageTextToText.from_pretrained(str(base),local_files_only=True,
             dtype=torch.bfloat16,attn_implementation='flash_attention_2',device_map={'':device})
    model=PeftModel.from_pretrained(model,str(adapter),is_trainable=False,local_files_only=True)
    model.eval()
    return model

def validate_xl6_completion(cfg,receipt_path):
    if not receipt_path or sha(receipt_path)!=cfg['xl6_completion_sha256']:
        raise N3RunError('XL6 completion proof differs')
    xl6=strict_json(Path(receipt_path).read_bytes())
    if (xl6.get('schema')!='hardread.reads-n3.terminal/v2' or xl6.get('status')!='complete' or
        xl6.get('arm')!='xl6' or xl6.get('completed')!=440 or
        xl6.get('inference_precision')!='native_bf16' or
        xl6.get('base_precision')!=cfg['base_precision'] or xl6.get('adapter_precision')!=cfg['adapter_precision'] or
        type(xl6.get('adapter_parameter_tensors')) is not int or
        xl6.get('adapter_parameter_tensors')!=cfg['adapter_parameter_tensors'] or
        xl6.get('loaded_model_base_dtype')!='torch.bfloat16' or
        xl6.get('loaded_model_adapter_dtype')!='torch.float32' or
        type(xl6.get('loaded_model_base_floating_parameter_tensors')) is not int or
        xl6.get('loaded_model_base_floating_parameter_tensors')<=0 or
        type(xl6.get('loaded_model_adapter_parameter_tensors')) is not int or
        xl6.get('loaded_model_adapter_parameter_tensors')!=cfg['adapter_parameter_tensors'] or
        xl6.get('cohort_manifest_sha256')!=cfg['cohort_manifest_sha256'] or
        xl6.get('prompt_sha256')!=cfg['prompt_sha256'] or
        xl6.get('base_files_sha256')!=inventory_sha256(cfg['base_files']) or
        xl6.get('adapter_files_sha256')!=inventory_sha256(cfg['adapter_files']) or
        not isinstance(xl6.get('outputs_sha256'),str) or
        not re.fullmatch('[0-9a-f]{64}',xl6['outputs_sha256'])):
        raise N3RunError('complete native-BF16 XL6 proof required')
    return xl6

def verify_source(root,pins):
    root=Path(root).resolve()
    required={'azure/sft/eval_reads_n3.py','tools/belief/reads_n3.py','tools/belief/roster_hash.py',
              'tools/sft/glimmer_chat.py','tools/sft/serialize.py','tools/sft/dex.json'}
    if not required.issubset(pins):raise N3RunError('source closure missing')
    for name,expected in pins.items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or not re.fullmatch('[0-9a-f]{64}',str(expected)):
            raise N3RunError('invalid source pin')
        current=root
        for part in path.parts:
            current=current/part
            if current.is_symlink():raise N3RunError('source symlink')
        if not current.is_file() or sha(current)!=expected:raise N3RunError('source bytes differ')

def run(config_path,config_sha,prompt_path,cohort_path,review_path,selection_path,base_path,adapter_path,output_parent,code_root,*,xl6_receipt_path=None):
    if not INFERENCE_ENABLED: raise N3RunError('N3 inference disabled pending source and runtime approval')
    import sys
    import threading
    import subprocess
    if sha(config_path)!=config_sha:raise N3RunError('config bytes differ')
    cfg=validate_config(strict_json(Path(config_path).read_bytes()))
    verify_source(code_root,cfg['code_files'])
    if Path(__file__).resolve()!=(Path(code_root)/'azure/sft/eval_reads_n3.py').resolve():raise N3RunError('loaded runner source differs')
    if any(sha(path)!=cfg[key] for path,key in ((prompt_path,'prompt_sha256'),(cohort_path,'cohort_manifest_sha256'),(review_path,'source_review_sha256'),(selection_path,'selection_sha256'))):
        raise N3RunError('input proof hash differs')
    cohort=strict_json(Path(cohort_path).read_bytes());review=strict_json(Path(review_path).read_bytes());selection=strict_json(Path(selection_path).read_bytes())
    if cohort.get('prompt_sha256')!=cfg['prompt_sha256'] or cohort.get('games')!=440 or cohort.get('decisions')!=440 or not cohort.get('label_sha256'):
        raise N3RunError('cohort manifest differs')
    if not isinstance(cohort.get('source_code_sha256'),dict) or any(cohort['source_code_sha256'].get(k)!=v for k,v in cfg['code_files'].items()):raise N3RunError('runtime code differs from reviewed preparation source')
    if review.get('status')!='accepted' or review.get('config_sha256')!=cohort.get('config_sha256') or review.get('source_map_sha256')!=cohort.get('source_map_sha256') or review.get('source_audit_sha256')!=cohort.get('source_audit_sha256') or cfg['source_review_sha256']!=cohort.get('source_review_sha256'):
        raise N3RunError('independent source review binding differs')
    if selection.get('status')!='selected' or selection.get('arm')!=cfg['arm'] or selection.get('adapter_files')!=cfg['adapter_files'] or selection.get('base_files')!=cfg['base_files']:
        raise N3RunError('selected model identity differs')
    if cfg['arm']=='selected_v2':
        validate_xl6_completion(cfg,xl6_receipt_path)
    verify_tree(base_path,cfg['base_files']);verify_tree(adapter_path,cfg['adapter_files'])
    with open(prompt_path,encoding='utf-8') as f:prompts=[strict_json(line) for line in f]
    if len(prompts)!=440 or len({p.get('duid') for p in prompts})!=440 or len({p.get('game') for p in prompts})!=440:
        raise N3RunError('prompt inventory differs')
    selected=cohort.get('selected')
    if not isinstance(selected,list): raise N3RunError('selected identity list missing')
    actual=sorted([p['game'],p['duid'],uid] for p in prompts for uid in p['row_uids'])
    if selected!=actual or len({tuple(x) for x in actual})!=len(actual):raise N3RunError('selected identity manifest differs')
    if any(set(p)!={'game','duid','row_uids','row_species','displayed_species','prompt','tools','prompt_sha256'} for p in prompts):
        raise N3RunError('prompt-only schema differs')
    if any(hashlib.sha256(p['prompt'].encode()).hexdigest()!=p['prompt_sha256'] for p in prompts):raise N3RunError('prompt bytes differ')
    if time.time()>=cfg['deadline_unix'] or os.getpgrp()!=os.getpid():raise N3RunError('deadline or owned process group differs')
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',WANDB_MODE='offline')
    sys.path[:0]=[str(Path(code_root)/'tools/belief'),str(Path(code_root)/'tools/sft')]
    from glimmer_chat import GlimmerChat,parse_tool_call,EOS_IDS
    import serialize as S
    import reads_n3 as N
    import torch
    if torch.cuda.device_count()!=1 or 'H100' not in torch.cuda.get_device_name(0):raise N3RunError('one H100 required')
    gc=GlimmerChat(base_path,verify=True)
    if gc.tokenizer_sha256!=cfg['tokenizer_sha256'] or gc.template_sha256!=cfg['template_sha256']:
        raise N3RunError('tokenizer/template differs')
    if any(p['tools']!=[S._reads_tool()] for p in prompts):raise N3RunError('reads schema differs')
    output=Path(output_parent)/cfg['run_name'];output.mkdir(mode=0o700)
    deadline=min(cfg['deadline_unix'],time.time()+cfg['max_seconds'])
    stop=threading.Event();first_sample=threading.Event();sample_error=threading.Event()
    def sample_gpu():
        with open(output/'gpu-samples.log','x') as f:
            while not stop.is_set():
                try:
                    p=subprocess.run(['nvidia-smi','--query-gpu=timestamp,name,memory.used,memory.total,utilization.gpu','--format=csv,noheader'],capture_output=True,text=True,timeout=10)
                    if p.returncode!=0 or not p.stdout.strip():sample_error.set()
                    f.write(p.stdout if p.returncode==0 else 'sample_failed\n');f.flush()
                except (OSError,subprocess.TimeoutExpired):sample_error.set();f.write('sample_failed\n');f.flush()
                first_sample.set()
                if stop.wait(60):break
    sampler=threading.Thread(target=sample_gpu,daemon=True);sampler.start()
    status='incomplete';count=0;reason='unexpected interruption'
    native_memory_before_load=None;native_memory_after_load=None
    loaded_model_device=None;loaded_model_base_dtype=None;loaded_model_adapter_dtype=None
    loaded_model_base_floating_parameter_tensors=None;loaded_model_adapter_parameter_tensors=None
    try:
        if not first_sample.wait(15) or sample_error.is_set():raise N3RunError('GPU sampling unavailable')
        native_memory_before_load=cuda_memory_snapshot(torch,0)
        load_start_utc=utc_timestamp()
        load_start=time.monotonic()
        model=load_model(base_path,adapter_path,0)
        loaded_identity=verify_native_bf16_residency(model,torch,0)
        loaded_model_device=loaded_identity['device'];loaded_model_base_dtype=loaded_identity['base_dtype']
        loaded_model_adapter_dtype=loaded_identity['adapter_dtype']
        loaded_model_base_floating_parameter_tensors=loaded_identity['base_floating_parameter_tensors']
        loaded_model_adapter_parameter_tensors=loaded_identity['adapter_parameter_tensors']
        native_memory_after_load=cuda_memory_snapshot(torch,0)
        load_end=time.monotonic()
        load_end_utc=utc_timestamp()
        decode_start=None;decode_start_utc=None
        fd=os.open(output/'outputs.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            for p in prompts:
                if sample_error.is_set():reason='GPU sample failed';break
                if time.time()>=deadline:reason='hard deadline';break
                if count==0:
                    decode_start_utc=utc_timestamp()
                    decode_start=time.monotonic()
                ids=gc.ids(p['prompt'])
                if not ids or len(ids)>2048:reason='prompt length';break
                x=torch.tensor([ids],device=0)
                with torch.inference_mode():
                    result=model.generate(input_ids=x,attention_mask=torch.ones_like(x),max_new_tokens=512,
                             do_sample=False,eos_token_id=list(EOS_IDS),pad_token_id=200018)
                tokens=result[0,len(ids):].tolist();raw=gc.tok.decode(tokens,skip_special_tokens=False)
                _,error=N.parse_reads(raw,p['tools'],parse_tool_call,p['displayed_species'])
                record={'duid':p['duid'],'raw':raw,'parse_status':'ok' if error is None else error,
                        'generated_tokens':len(tokens),'prompt_sha256':p['prompt_sha256'],'selection_sha256':cfg['selection_sha256'],'config_sha256':config_sha}
                out.write(json.dumps(record,separators=(',',':'),ensure_ascii=False)+'\n');out.flush();count+=1
                if count==20:
                    checkpoint=time.monotonic()
                    checkpoint_epoch=time.time()
                    checkpoint_utc=utc_timestamp()
                    reason='20-decision timing receipt failed'
                    timing=write_first20_timing(output/'timing.json',load_start=load_start,load_end=load_end,
                            decode_start=decode_start,checkpoint=checkpoint,deadline=deadline,checkpoint_epoch=checkpoint_epoch,
                            load_start_utc=load_start_utc,load_end_utc=load_end_utc,decode_start_utc=decode_start_utc,
                            checkpoint_utc=checkpoint_utc,source_sha256=sha(Path(__file__).resolve()),config_sha256=config_sha,
                            inference_precision=cfg['inference_precision'],base_precision=cfg['base_precision'],
                            adapter_precision=cfg['adapter_precision'],cuda_memory_before_load=native_memory_before_load,
                            cuda_memory_after_load=native_memory_after_load,loaded_model_device=loaded_model_device,
                            loaded_model_base_dtype=loaded_model_base_dtype,loaded_model_adapter_dtype=loaded_model_adapter_dtype,
                            loaded_model_base_floating_parameter_tensors=loaded_model_base_floating_parameter_tensors,
                            loaded_model_adapter_parameter_tensors=loaded_model_adapter_parameter_tensors)
                    if not timing['within_budget']:
                        reason='20-decision timing projection exceeds deadline';break
                    reason='unexpected interruption'
            os.fsync(out.fileno())
        if count==440 and time.time()<=deadline:status='complete';reason=None
    except Exception as exc:
        reason='native BF16 execution failed: '+type(exc).__name__
        raise
    finally:
        stop.set();sampler.join(timeout=15)
        terminal={'schema':'hardread.reads-n3.terminal/v2','status':status,'arm':cfg['arm'],
                  'inference_precision':cfg['inference_precision'],'base_precision':cfg['base_precision'],
                  'adapter_precision':cfg['adapter_precision'],'adapter_parameter_tensors':cfg['adapter_parameter_tensors'],
                  'completed':count,'reason':reason,
                  'cohort_manifest_sha256':cfg['cohort_manifest_sha256'],'prompt_sha256':cfg['prompt_sha256'],
                  'selection_sha256':cfg['selection_sha256'],'config_sha256':config_sha,
                  'base_files_sha256':inventory_sha256(cfg['base_files']),
                  'adapter_files_sha256':inventory_sha256(cfg['adapter_files']),
                  'cuda_memory_before_load':native_memory_before_load,'cuda_memory_after_load':native_memory_after_load,
                  'loaded_model_device':loaded_model_device,'loaded_model_base_dtype':loaded_model_base_dtype,
                  'loaded_model_adapter_dtype':loaded_model_adapter_dtype,
                  'loaded_model_base_floating_parameter_tensors':loaded_model_base_floating_parameter_tensors,
                  'loaded_model_adapter_parameter_tensors':loaded_model_adapter_parameter_tensors,
                  'outputs_sha256':sha(output/'outputs.jsonl') if (output/'outputs.jsonl').is_file() else None}
        fd=os.open(output/'terminal.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(terminal,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
    return terminal


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('config','config-sha256','prompts','cohort','review','selection','base','adapter','out','code-root'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--xl6-receipt');p.add_argument('--execute',action='store_true');a=p.parse_args(argv)
    if not a.execute:raise SystemExit('N3 execution requires explicit authorization')
    result=run(a.config,a.config_sha256,a.prompts,a.cohort,a.review,a.selection,a.base,a.adapter,a.out,a.code_root,xl6_receipt_path=a.xl6_receipt)
    if result['status']!='complete':raise SystemExit('N3 incomplete; no score authorized')

if __name__=='__main__':main()
