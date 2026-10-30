#!/usr/bin/env python3
"""One-shot, root-admitted local selector. Importing this module does no I/O.

Admission is a root-verified JSON file, bound by its CLI SHA-256. It has exact
keys schema, projection_path, projection_sha256, rule_sha256, runner_sha256,
selector_sha256, runtime. Runtime has exact keys model_sha256, model_bytes,
llama_commit, binary_path, binary_sha256, pid, birth_sec, birth_usec,
model_path, template_sha256, owner_verified, loopback_owner_verified,
base_only_verified, launch_receipt_sha256. Root must verify process/port owner,
no adapter, launch identity and model provenance before binding this receipt.
No process or port-owner inference in this script replaces that attestation.
A candidate sidecar requires separate root acceptance before the app reads it.
"""
import argparse
import ctypes
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from own_ladder_review import build_projection, FACTS_SCHEMA
from own_ladder_selection import Registry, validate_completion
from evallock import eval_lock
from llm import GlimmerClient

BASE = 'http://127.0.0.1:18131'
MODEL_SHA = '4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e'
LLAMA_COMMIT = 'f8def7fe168bab245fbf15d3f18b26dbb1ef73c8'
STAGE = ROOT / 'scratch_ds/codex/win-sprint-20261030/W3-local-selector.private'
SELECTOR = ROOT / 'app/own_ladder_selection.py'
MAX_RESPONSE = 1_000_000
HASH = re.compile(r'[a-f0-9]{64}\Z')
SYSTEM = 'Select exactly one cited recorded fact per turn. Do not infer strategy or outcomes. Call select_evidence.'


def _keys(obj, keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys): raise ValueError('invalid structure')


def sha_bytes(data): return hashlib.sha256(data).hexdigest()
def sha_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def read_bound(path, expected, limit=4_000_000):
    if not isinstance(path,str) or not Path(path).is_absolute() or not isinstance(expected,str) or not HASH.fullmatch(expected): raise ValueError('invalid binding')
    p=Path(path)
    if p.is_symlink() or p.resolve()!=p or not p.is_file(): raise ValueError('binding unavailable')
    with p.open('rb') as f: raw=f.read(limit+1)
    if len(raw)>limit or sha_bytes(raw)!=expected: raise ValueError('binding mismatch')
    return raw


def json_unique(raw):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d: raise ValueError('duplicate field')
            d[k]=v
        return d
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))


def write_new(path, obj):
    raw=json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()+b'\n'
    if len(raw)>MAX_RESPONSE: raise ValueError('receipt too large')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return sha_bytes(raw)


class BSDInfo(ctypes.Structure):
    _fields_=[(x,ctypes.c_uint32) for x in ('flags','status','xstatus','pid','ppid','uid','gid','ruid','rgid','svuid','svgid','rfu')]+[('comm',ctypes.c_char*16),('name',ctypes.c_char*32)]+[(x,ctypes.c_uint32) for x in ('nfiles','pgid','pjobc','e_tdev','e_tpgid','nice')]+[('start_sec',ctypes.c_uint64),('start_usec',ctypes.c_uint64)]


def verify_process(runtime):
    if sys.platform!='darwin': raise ValueError('runtime identity unsupported')
    lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True); info=BSDInfo()
    n=lib.proc_pidinfo(runtime['pid'],3,0,ctypes.byref(info),ctypes.sizeof(info))
    if n!=ctypes.sizeof(info) or info.pid!=runtime['pid'] or (info.start_sec,info.start_usec)!=(runtime['birth_sec'],runtime['birth_usec']): raise ValueError('runtime process differs')
    buf=ctypes.create_string_buffer(4096)
    if lib.proc_pidpath(runtime['pid'],buf,len(buf))<=0 or Path(os.fsdecode(buf.value)).resolve()!=Path(runtime['binary_path']): raise ValueError('runtime binary differs')
    if sha_file(runtime['binary_path'])!=runtime['binary_sha256']: raise ValueError('runtime binary hash differs')


def validate_admission(admission, process_check=verify_process):
    _keys(admission,('schema','projection_path','projection_sha256','rule_sha256','runner_sha256','selector_sha256','runtime'))
    if admission['schema']!='own-ladder-selector-admission-v1': raise ValueError('invalid admission')
    for k in ('projection_sha256','rule_sha256','runner_sha256','selector_sha256'):
        if not isinstance(admission[k],str) or not HASH.fullmatch(admission[k]): raise ValueError('invalid digest')
    if admission['runner_sha256']!=sha_file(Path(__file__).resolve()) or admission['selector_sha256']!=sha_file(SELECTOR) or admission['rule_sha256']!=sha_file(ROOT / 'app/own_ladder_review.py'): raise ValueError('source differs')
    r=admission['runtime']
    _keys(r,('model_sha256','model_bytes','llama_commit','binary_path','binary_sha256','pid','birth_sec','birth_usec','model_path','template_sha256','owner_verified','loopback_owner_verified','base_only_verified','launch_receipt_sha256'))
    if r['model_sha256']!=MODEL_SHA or r['model_bytes']!=16756683904 or r['llama_commit']!=LLAMA_COMMIT or any(r[k] is not True for k in ('owner_verified','loopback_owner_verified','base_only_verified')): raise ValueError('runtime admission missing')
    if any(not isinstance(r[k],str) or not HASH.fullmatch(r[k]) for k in ('binary_sha256','template_sha256','launch_receipt_sha256')): raise ValueError('runtime digest missing')
    if any(type(r[k]) is not int or r[k]<=0 for k in ('pid','birth_sec')) or type(r['birth_usec']) is not int or not 0<=r['birth_usec']<1000000: raise ValueError('runtime identity missing')
    if any(not isinstance(r[k],str) or not Path(r[k]).is_absolute() or str(Path(r[k]).resolve())!=r[k] for k in ('binary_path','model_path')): raise ValueError('runtime path invalid')
    process_check(r)
    raw=read_bound(admission['projection_path'],admission['projection_sha256'])
    projection=json_unique(raw)
    if not isinstance(projection,dict) or projection.get('schema')!=FACTS_SCHEMA: raise ValueError('projection differs')
    view=build_projection(projection)
    return view


def bounded_call(fn, args, timeout):
    """One owned fork with a hard wall; never signal the shared model server."""
    if timeout<=0: raise TimeoutError('deadline')
    ctx=multiprocessing.get_context('fork'); recv,send=ctx.Pipe(duplex=False)
    def work():
        recv.close()
        try:
            value=fn(*args)
            raw=json.dumps({'ok':True,'value':value},allow_nan=False).encode()
            if len(raw)>MAX_RESPONSE: raise ValueError('response too large')
            send.send_bytes(raw)
        except BaseException:
            try: send.send_bytes(b'{"ok":false}')
            except BaseException: pass
        finally: send.close()
    p=ctx.Process(target=work); p.start(); send.close()
    try:
        if not recv.poll(timeout): raise TimeoutError('request deadline')
        try: payload=json_unique(recv.recv_bytes(MAX_RESPONSE))
        except (EOFError,OSError,ValueError): raise RuntimeError('request failed')
        if not payload.get('ok'): raise RuntimeError('request failed')
        return payload['value']
    finally:
        recv.close()
        if p.is_alive(): p.terminate()
        p.join(2)
        if p.is_alive(): p.kill(); p.join(2)
        if p.is_alive(): raise RuntimeError('owned request cleanup unconfirmed')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('redirect rejected')


def loopback_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def http_json(endpoint, body=None):
    if endpoint not in ('/health','/props','/apply-template','/tokenize'): raise ValueError('unsupported endpoint')
    data=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(BASE+endpoint,data=data,headers={'Content-Type':'application/json'})
    with loopback_opener().open(req,timeout=15) as r:
        raw=r.read(MAX_RESPONSE+1)
    if len(raw)>MAX_RESPONSE: raise ValueError('response too large')
    return json_unique(raw)


def complete(messages, tool):
    # This runs only in the owned fork; disable system proxy and redirects.
    urllib.request.install_opener(loopback_opener())
    client=GlimmerClient(max_tokens=1024,temperature=0.0,reasoning='low',timeout=120,seed=42)
    return client.chat(messages,tools=[tool],tool_choice='required',max_tokens=1024)


def prompt_of(reg):
    return [{'role':'system','content':SYSTEM}, {'role':'user','content':json.dumps(reg.prompt(),ensure_ascii=False,separators=(',',':'))}]


def verify_props(props,runtime):
    if not isinstance(props,dict) or props.get('total_slots')!=1 or props.get('model_path')!=runtime['model_path'] or not isinstance(props.get('chat_template'),str): raise ValueError('runtime properties differ')
    if sha_bytes(props['chat_template'].encode())!=runtime['template_sha256']: raise ValueError('template differs')
    n=props.get('default_generation_settings',{}).get('n_ctx')
    if type(n) is not int or n<3072: raise ValueError('context insufficient')
    return n


def synthetic_batch(games, client, record_attempt, preflight, clock):
    """Small fake-client protocol check; never used by the production entrypoint."""
    if len(games)!=3 or [g.get('alias') for g in games]!=['G1','G2','G3']: raise ValueError('three fixed games required')
    result=[]; started=clock(); attempts=0
    for game in games:
        try: reg=Registry(game)
        except ValueError:
            result.append({'alias':game['alias'],'status':'fallback','refs':[]}); continue
        if clock()-started>=480 or not preflight(reg):
            result.extend({'alias':g['alias'],'status':'fallback','refs':[]} for g in games[len(result):]); break
        if attempts>=3: raise ValueError('attempt limit')
        record_attempt(game['alias']); attempts+=1
        try: response=client.chat(reg.prompt(),reg.schema())
        except Exception:
            result.extend({'alias':g['alias'],'status':'fallback','refs':[]} for g in games[len(result):]); break
        try: result.append({'alias':game['alias'],'status':'success','refs':validate_completion(reg,response)})
        except (ValueError,TypeError,KeyError): result.append({'alias':game['alias'],'status':'fallback','refs':[]})
    return result


def run(admission, admission_sha, view, stage=STAGE, call=bounded_call, process_check=verify_process):
    stage.mkdir(mode=0o700,parents=True,exist_ok=True)
    if stage.is_symlink() or stage.stat().st_mode & 0o077: raise ValueError('private stage permissions')
    # Atomic marker prevents any automatic second batch even after interruption.
    write_new(stage/'batch-started.private.json',{'admission_sha256':admission_sha,'started_unix':time.time()})
    now=time.monotonic(); total_end=now+480; preflight_end=now+60
    regs=[]
    for game in view['games']:
        try: regs.append(Registry(game))
        except ValueError: regs.append(None)
    results=[{'alias':a,'status':'fallback','refs':[]} for a in ('G1','G2','G3')]
    records=[]; request_count=0; completion_seconds=0.0
    with eval_lock('W3 local product selection',600):
        process_check(admission['runtime'])
        def preflight(endpoint, body=None):
            nonlocal request_count
            request_count+=1
            if request_count>8: raise ValueError('preflight limit')
            return call(http_json,(endpoint,body),min(15,preflight_end-time.monotonic(),total_end-time.monotonic()))
        health=preflight('/health')
        if health.get('status')!='ok': raise ValueError('runtime unavailable')
        context=verify_props(preflight('/props'),admission['runtime'])
        prompts=[]
        for reg in regs:
            if reg is None: prompts.append(None); continue
            messages=prompt_of(reg); tool=reg.schema()
            body={'messages':messages,'tools':[tool],'tool_choice':'required','chat_template_kwargs':{'reasoning_strength':'low'}}
            applied=preflight('/apply-template',body)
            if not isinstance(applied,dict) or not isinstance(applied.get('prompt'),str): raise ValueError('template unavailable')
            tokens=preflight('/tokenize',{'content':applied['prompt'],'add_special':True,'parse_special':True})
            token_list=tokens.get('tokens') if isinstance(tokens,dict) else None
            if not isinstance(token_list,list) or any(type(t) is not int for t in token_list) or len(token_list)>2048 or len(token_list)+1024>context: raise ValueError('prompt token budget unavailable')
            prompts.append((messages,tool,len(token_list)))
        for i,(reg,prompt) in enumerate(zip(regs,prompts)):
            alias=('G1','G2','G3')[i]
            if reg is None: continue
            process_check(admission['runtime'])
            remaining=min(120,360-completion_seconds,total_end-time.monotonic())
            if remaining<=0: break
            directory=stage/alias; directory.mkdir(mode=0o700,exist_ok=False)
            messages,tool,prompt_tokens=prompt
            write_new(directory/'registry.private.json',{'facts':reg.facts,'turns':reg.turns})
            write_new(directory/'prompt.private.json',{'messages':messages,'tools':[tool]})
            write_new(directory/'attempt.private.json',{'alias':alias,'admission_sha256':admission_sha,'at_unix':time.time()})
            started=time.monotonic(); status='fallback'; refs=[]; response=None; reason='invalid_selection'
            try:
                response=call(complete,(messages,tool),remaining)
                elapsed=time.monotonic()-started; completion_seconds+=elapsed
                write_new(directory/'response.private.json',response)
                completion_tokens=response.get('completion_tokens')
                if type(completion_tokens) is not int or not 0<=completion_tokens<=1024:
                    reason='usage_unavailable'
                    raise ValueError('token count unavailable')
                refs=validate_completion(reg,response); status='success'; reason=None
            except (TimeoutError,RuntimeError,OSError):
                completion_seconds+=time.monotonic()-started; reason='transport_or_timeout'
            except (ValueError,TypeError,KeyError):
                if response is None: completion_seconds+=time.monotonic()-started
            entry={'alias':alias,'status':status,'refs':refs}; results[i]=entry
            record={'alias':alias,'status':status,'reason':reason,'prompt_tokens':prompt_tokens,
                    'elapsed_seconds':time.monotonic()-started,'completion_tokens':response.get('completion_tokens') if isinstance(response,dict) else None,
                    'projection_sha256':admission['projection_sha256'],'selector_sha256':admission['selector_sha256'],
                    'rule_sha256':admission['rule_sha256'],'runtime':admission['runtime'],'selected_refs':refs}
            write_new(directory/'selection.private.json',entry); write_new(directory/'receipt.private.json',record); records.append(record)
            if reason in ('transport_or_timeout','usage_unavailable'): break
    receipt={'schema':'own-ladder-selector-receipt-v1','admission_sha256':admission_sha,'projection_sha256':admission['projection_sha256'],
             'selector_sha256':admission['selector_sha256'],'rule_sha256':admission['rule_sha256'],
             'preflight_requests':request_count,'completion_attempts':len(records),'games':records}
    receipt_sha=write_new(stage/'batch-receipt.private.json',receipt)
    sidecar={'schema':'own-ladder-selection-v1','projection_sha256':admission['projection_sha256'],
             'selector_sha256':admission['selector_sha256'],'receipt_sha256':receipt_sha,'games':results}
    sidecar_sha=write_new(stage/'candidate-sidecar.private.json',sidecar)
    return {'receipt_sha256':receipt_sha,'sidecar_sha256':sidecar_sha,'completion_attempts':len(records)}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--admission',required=True); parser.add_argument('--sha256',required=True)
    args=parser.parse_args(); os.umask(0o077)
    admission=json_unique(read_bound(args.admission,args.sha256))
    view=validate_admission(admission)
    result=run(admission,args.sha256,view)
    print(json.dumps({'status':'completed','completion_attempts':result['completion_attempts']}))


if __name__=='__main__':
    try: main()
    except Exception:
        try:
            if (STAGE/'batch-started.private.json').exists():
                write_new(STAGE/'batch-failure.private.json', {'status':'unavailable'})
        except (OSError,ValueError):
            pass
        print(json.dumps({'status':'unavailable'})); raise SystemExit(1)
