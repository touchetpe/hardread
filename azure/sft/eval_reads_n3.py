"""Disabled prospective N3 prompt-only greedy inference. Labels are never an input."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

INFERENCE_ENABLED=os.environ.get('HARDREAD_N3_INFERENCE_ENABLED')=='1'
MAX_SECONDS=10800
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

def validate_config(cfg):
    required={'schema','arm','status','prompt_sha256','cohort_manifest_sha256','source_review_sha256','selection_sha256',
              'tokenizer_sha256','template_sha256','code_files','base_files','adapter_files','deadline_unix','max_seconds','xl6_completion_sha256','run_name'}
    if not isinstance(cfg,dict) or set(cfg)!=required or cfg.get('schema')!='hardread.reads-n3.runtime/v1' or cfg.get('status')!='approved':raise N3RunError('runtime config differs')
    if cfg['arm'] not in ('xl6','selected_v2') or type(cfg['max_seconds']) is not int or not 0<cfg['max_seconds']<=MAX_SECONDS or type(cfg['deadline_unix']) is not int:raise N3RunError('arm or deadline differs')
    for key in ('prompt_sha256','cohort_manifest_sha256','source_review_sha256','selection_sha256','tokenizer_sha256','template_sha256'):
        if not isinstance(cfg[key],str) or not re.fullmatch('[0-9a-f]{64}',cfg[key]):raise N3RunError('runtime pin malformed')
    if cfg['arm']=='selected_v2' and (not isinstance(cfg['xl6_completion_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',cfg['xl6_completion_sha256'])):raise N3RunError('XL6 completion proof required')
    if cfg['arm']=='xl6' and cfg['xl6_completion_sha256'] is not None:raise N3RunError('unexpected XL6 completion proof')
    if not all(isinstance(cfg[k],dict) and cfg[k] for k in ('base_files','adapter_files','code_files')):raise N3RunError('model or code pins missing')
    if not isinstance(cfg['run_name'],str) or not re.fullmatch('hrreads-[a-z0-9-]{1,64}',cfg['run_name']):raise N3RunError('invalid output name')
    return cfg

def timing_allows(elapsed,completed,total,remaining):
    if completed!=20 or total!=440 or not math.isfinite(elapsed) or elapsed<=0 or not math.isfinite(remaining) or remaining<=0:
        raise N3RunError('timing projection inputs differ')
    return elapsed/completed*(total-completed)<remaining

def load_model(base,adapter,device):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForImageTextToText,BitsAndBytesConfig
    quant=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,
                            bnb_4bit_compute_dtype=torch.bfloat16,bnb_4bit_quant_storage=torch.bfloat16)
    model=AutoModelForImageTextToText.from_pretrained(str(base),local_files_only=True,quantization_config=quant,
             dtype=torch.bfloat16,attn_implementation='flash_attention_2',device_map={'':device})
    model=PeftModel.from_pretrained(model,str(adapter),is_trainable=False,local_files_only=True)
    model.eval()
    return model

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
        if not xl6_receipt_path or sha(xl6_receipt_path)!=cfg['xl6_completion_sha256']:
            raise N3RunError('XL6 completion proof differs')
        xl6=strict_json(Path(xl6_receipt_path).read_bytes())
        if xl6.get('status')!='complete' or xl6.get('arm')!='xl6' or xl6.get('cohort_manifest_sha256')!=cfg['cohort_manifest_sha256']:
            raise N3RunError('XL6 completion missing')
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
    started=time.monotonic();deadline=min(cfg['deadline_unix'],time.time()+cfg['max_seconds'])
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
    try:
        if not first_sample.wait(15) or sample_error.is_set():raise N3RunError('GPU sampling unavailable')
        model=load_model(base_path,adapter_path,0)
        fd=os.open(output/'outputs.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            for p in prompts:
                if sample_error.is_set():reason='GPU sample failed';break
                if time.time()>=deadline:reason='hard deadline';break
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
                if count==20 and not timing_allows(time.monotonic()-started,20,440,deadline-time.time()):
                    reason='20-decision timing projection exceeds deadline';break
            os.fsync(out.fileno())
        if count==440 and time.time()<=deadline:status='complete';reason=None
    finally:
        stop.set();sampler.join(timeout=15)
        terminal={'status':status,'arm':cfg['arm'],'completed':count,'reason':reason,'cohort_manifest_sha256':cfg['cohort_manifest_sha256'],
                  'prompt_sha256':cfg['prompt_sha256'],'selection_sha256':cfg['selection_sha256'],'config_sha256':config_sha,
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
