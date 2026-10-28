"""Create quarantined explain SFT examples from independently verified tool traces."""
import hashlib
import json

class ExplainDataError(ValueError):pass

def _canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def _hash(x):return hashlib.sha256(_canonical(x)).hexdigest()

def build_examples_pure(items,targets,attempts,*,tokenizer,max_tokens,trace_validator):
    if not isinstance(max_tokens,int) or max_tokens<=0 or not callable(getattr(tokenizer,'encode',None)) or not callable(trace_validator):
        raise ExplainDataError('tokenizer, length and independent trace validator required')
    if len({item.get('decision_id') for item in items})!=len(items):raise ExplainDataError('duplicate decision')
    by_id={item['decision_id']:item for item in items}
    if set(targets)!=set(by_id):raise ExplainDataError('target population differs')
    output={'train':[],'dev':[]};dropped={'too_long':0};selected_types={'model':0,'template_fallback':0}
    for did,item in by_id.items():
        target=targets[did]
        if not isinstance(target,dict) or target.get('type') not in selected_types or item.get('split') not in output:
            raise ExplainDataError('target or split invalid')
        candidate=attempts.get(target.get('candidate_id')) if target['type']=='model' else target.get('verified_template_attempt')
        if not isinstance(candidate,dict) or trace_validator(item,target,candidate) is not True:
            raise ExplainDataError('selected raw trace or template trace unverified')
        calls=candidate.get('calls')
        if not isinstance(calls,list) or not calls:raise ExplainDataError('full tool trace required')
        for i,call in enumerate(calls):
            req=call.get('request') or {};response=call.get('response') or {};messages=req.get('messages');tools=req.get('tools');message=response.get('message')
            if not isinstance(messages,list) or not isinstance(tools,list) or not isinstance(message,dict) or message.get('role')!='assistant':
                raise ExplainDataError('raw assistant/tool context incomplete')
            if i and messages[:len(calls[i-1]['request']['messages'])]!=calls[i-1]['request']['messages']:
                raise ExplainDataError('history prefix changed')
            encoded=tokenizer.encode(messages,tools,message)
            ids=encoded.get('input_ids');labels=encoded.get('labels');n=encoded.get('n_prompt')
            if not isinstance(ids,list) or not isinstance(labels,list) or len(ids)!=len(labels) or type(n) is not int or not 0<n<len(ids) or labels[:n]!=[-100]*n or labels[n:]!=ids[n:]:
                raise ExplainDataError('assistant target label boundary invalid')
            if len(ids)>max_tokens:dropped['too_long']+=1;continue
            output[item['split']].append({'example_id':did+'/'+str(i),'decision_id':did,'game_id':item['game_id'],
                'task':'explain','input_ids':ids,'labels':labels,'trace_sha256':_hash(calls),'target_type':target['type']})
        selected_types[target['type']]+=1
    if not output['train'] or not output['dev']:raise ExplainDataError('nonempty game-group train/dev required')
    train_games={x['game_id'] for x in output['train']};dev_games={x['game_id'] for x in output['dev']}
    if train_games&dev_games:raise ExplainDataError('game crosses train/dev')
    return {'status':'quarantined_not_training_approval','splits':output,'drops':dropped,'target_types':selected_types}

def build_examples(items,targets,attempts,*,tokenizer,max_tokens,trace_validator,privacy_literals):
    """Local promotion helper: scan privately before deterministic construction."""
    if not isinstance(privacy_literals,list) or not privacy_literals or any(not isinstance(x,str) or not x for x in privacy_literals):
        raise ExplainDataError('reviewed privacy inventory required')
    encoded=_canonical({'items':items,'targets':targets,'attempts':attempts}).decode().casefold()
    if any(x.casefold() in encoded for x in privacy_literals):raise ExplainDataError('private identifier in training context')
    return build_examples_pure(items,targets,attempts,tokenizer=tokenizer,max_tokens=max_tokens,trace_validator=trace_validator)

def write_dataset(result,outdir,*,tokenizer_sha256,template_sha256):
    """Write deterministic, still-quarantined train/dev files; never promote a manifest."""
    import gzip
    import os
    import re
    from pathlib import Path
    if result.get('status')!='quarantined_not_training_approval':raise ExplainDataError('quarantined example set required')
    if any(not isinstance(v,str) or not re.fullmatch(r'[0-9a-f]{64}',v) for v in (tokenizer_sha256,template_sha256)):
        raise ExplainDataError('pinned tokenizer and template hashes required')
    out=Path(outdir)
    if out.exists():raise ExplainDataError('dataset directory already exists')
    out.mkdir(mode=0o700,parents=False)
    hashes={};counts={}
    for split,name in (('train','train.jsonl.gz'),('dev','val.jsonl.gz')):
        rows=result['splits'][split];path=out/name
        with path.open('xb') as raw:
            os.chmod(path,0o600)
            with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as stream:
                for row in rows:stream.write(_canonical(row)+b'\n')
        hashes[split]=hashlib.sha256(path.read_bytes()).hexdigest();counts[split]=len(rows)
    manifest={'status':'quarantined_not_training_approval','task':'explain','counts':counts,'drops':result['drops'],
              'target_types':result['target_types'],'train_sha256':hashes['train'],'val_sha256':hashes['dev'],
              'tokenizer_sha256':tokenizer_sha256,'chat_template_sha256':template_sha256,
              'serializer_version':'t1-explain-v1'}
    path=out/'manifest.json'
    with path.open('x') as f:os.chmod(path,0o600);json.dump(manifest,f,sort_keys=True);f.write('\n')
    return manifest
