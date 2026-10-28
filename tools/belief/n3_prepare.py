"""Disabled N3 preparation: authenticate hr_val provenance and publish private cohort.

No file is read on import. Source review and a committed configuration are
required before the frozen example or archive inputs are opened.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

PREP_ENABLED=os.environ.get('HARDREAD_N3_PREP_ENABLED')=='1'
MAP_SHA='400ae6ea6020c583ecc59f8c389423454a6c8d9952d8822e1d558509fd5e1b78'
AUDIT_SHA='6882cf46977bcefe71ae5bab36f0a1e3e37628814497ede4ac4e9dc8ac69f110'
ROWS_SHA='99e8c4914dc0cca6674d7e6fca2ec0bdcaf07c3a7f2f9f12da6969a1514c416a'
CATALOG_SHA='6440fdbb7cd0de33604aefc69ba65367dc2ae5b50b00339b7ef9bf372977b778'
SOURCE_REVIEW_SHA='feaf234ba11041f9b861db1516acaca1f944d7f328d15c80678e9fede7b6348b'
CARD_SHA='d5cf4690beaac067796fa4ffcc5f32f082a55562e0a1596f62782d601df08eb2'
FROZEN={'examples':'34baa58c45c4be4aead4f85f654abf5afe2a1390f644cf635acf321d0596d9b7','usage':'2bd961e6edb2679e048ea185f22dca1ad16f858594fa5a06c4be166e451841ae','belief_v1':'f33d6e9fa10c8db83c12ad96347272ea976fcc255ebb0d08d2dca72802ff2fb7','benchmark':'463145f7965f740017a7c6d05e14a12658f76768bde9a53661b211ab37ff7558'}
REQUIRED={'azure/sft/eval_reads_n3.py','tools/belief/n3_prepare.py','tools/belief/n3_hrval_join.py','tools/belief/reads_n3.py','tools/belief/benchmark.py','tools/belief/targets.py','tools/belief/roster_hash.py','tools/sft/serialize.py','tools/sft/glimmer_chat.py','tools/sft/dex.json','tools/sft/data_admission.py','tools/sft/data_admission_runtime.py','tools/dataset/extract.py','tools/human/parse_replays.py','foul-play/fp/data/pokedex.json','scratch_ds/codex/training-roadmap/source-audit/audit_sources.py'}
class PrepError(ValueError):pass

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def strict(raw):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise PrepError('duplicate JSON key')
            d[k]=v
        return d
    def bad(_):raise PrepError('nonfinite JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=bad)

def relfile(root,name):
    p=Path(name)
    if p.is_absolute() or not p.parts or '..' in p.parts:raise PrepError('unsafe input path')
    cur=root
    for part in p.parts:
        cur=cur/part
        if cur.is_symlink():raise PrepError('symlink input')
    if not cur.is_file():raise PrepError('input file missing')
    return cur

def pinned(root,ref,expected=None):
    if not isinstance(ref,dict) or set(ref)!={'path','sha256'} or not re.fullmatch('[0-9a-f]{64}',str(ref.get('sha256',''))):raise PrepError('invalid artifact pin')
    if expected is not None and ref['sha256']!=expected:raise PrepError('artifact pin differs')
    path=relfile(root,ref['path'])
    if sha(path)!=ref['sha256']:raise PrepError('artifact bytes differ')
    return path

def committed(root,path,commit):
    if not re.fullmatch('[0-9a-f]{40}',str(commit)):raise PrepError('commit missing')
    rel=path.relative_to(root).as_posix()
    for args in (['merge-base','--is-ancestor',commit,'HEAD'],['show',commit+':'+rel]):
        p=subprocess.run(['git','-C',str(root),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if p.returncode:raise PrepError('commit verification failed')
    if hashlib.sha256(p.stdout).hexdigest()!=sha(path):raise PrepError('committed bytes differ')

def rows(path):
    opener=gzip.open if path.name.endswith('.gz') else open
    with opener(path,'rb') as f:
        return [strict(line) for line in f if line.strip()]

def authenticate_metadata(root,source_map,audit,source_rows,audit_tool):
    catalogs=[v for v in audit.get('source_catalog',[]) if v.get('role')=='hr_val']
    if audit.get('schema')!='t1-source-isolation-audit-v1' or len(catalogs)!=1:raise PrepError('audit catalog missing')
    cat=catalogs[0]
    if cat.get('archive_count')!=79 or cat.get('record_count')!=1580 or cat.get('complete_log_count')!=1579 or cat.get('producer_bound_archive_count')!=79:raise PrepError('audit population differs')
    entries=cat.get('archives',[]);by_path={x['source_path']:x for x in entries}
    if len(by_path)!=79:raise PrepError('audit archive inventory differs')
    source_index={}
    for row in source_rows:
        if row.get('role')=='hr_val':
            uid=row.get('uid')
            if uid in source_index:raise PrepError('duplicate source game')
            source_index[uid]=row
    seen_producers={}
    for m in source_map['archives']:
        a=by_path.get(m['source_path'])
        if not a or a.get('source_role')!='hr_val' or a.get('producer_match') is not True:raise PrepError('unbound archive')
        for key in ('source_path','source_sha256','bytes','chunk_number','records','records_with_log'):
            if a.get(key)!=m.get(key):raise PrepError('archive catalog mismatch')
        p=a.get('producer') or {}
        for mk,ak in (('producer_job','job_name'),('producer_yaml_sha256','yaml_sha256'),('producer_create_sha256','job_create_sha256'),('producer_code_tree_sha256','code_tree_sha256'),('producer_pool','pool'),('producer_prefix','prefix'),('producer_seed_base','seed_base')):
            if m.get(mk)!=p.get(ak):raise PrepError('producer identity mismatch')
        if p.get('pool')!='gen9/hr_val' or p.get('seed_rule')!='seed_base + chunk_number' or m['expected_seed']!=p['seed_base']+m['chunk_number'] or p.get('job_name_matches_create') is not True:raise PrepError('producer contract differs')
        if p['job_name'] not in seen_producers:
            yaml=pinned(root,{'path':p['yaml_path'],'sha256':p['yaml_sha256']});pinned(root,{'path':p['job_create_path'],'sha256':p['job_create_sha256']})
            actual=audit_tool.load_job_yaml(yaml)
            for key in ('job_name','job_create_name','job_name_matches_create','pool','prefix','seed_base','seed_rule','cfg_a','cfg_b','side_swap','log_decisions','chunk_games','max_chunks','code_path','yaml_sha256','job_create_sha256','command_sha256'):
                if actual.get(key)!=p.get(key):raise PrepError('producer YAML/create differs')
            code_name=Path(p['code_path'])
            if code_name.is_absolute() or '..' in code_name.parts:raise PrepError('producer code path invalid')
            code=root
            for part in code_name.parts:
                code=code/part
                if code.is_symlink():raise PrepError('producer code symlink')
            if not code.is_dir():raise PrepError('producer code missing')
            if any(x.is_symlink() for x in code.rglob('*')):raise PrepError('producer code symlink')
            digest,count,_,_=audit_tool.code_tree_digest(code)
            if digest!=p['code_tree_sha256'] or count!=p['code_file_count']:raise PrepError('producer code tree differs')
            seen_producers[p['job_name']]=p
        elif seen_producers[p['job_name']]!=p:raise PrepError('inconsistent producer')
        f=relfile(root,m['source_path'])
        if f.stat().st_size!=m['bytes'] or sha(f)!=m['source_sha256']:raise PrepError('archive bytes differ')
    for g in source_map['games']:
        r=source_index.get(g['game_uid'])
        if not r or r.get('identity_status')!='complete':raise PrepError('source game identity missing')
        for mk,rk in (('game_uid','uid'),('run_id','run_id'),('seed','seed'),('source_path','source_path'),('source_sha256','source_sha256'),('source_record_index','source_record_index'),('source_record_sha256','source_record_sha256'),('replay_sha256','replay_sha256'),('normalized_replay_sha256','normalized_replay_sha256')):
            if g.get(mk)!=r.get(rk):raise PrepError('source game identity differs')
        p=by_path[g['source_path']]['producer']
        for rk,pk in (('source_job','job_name'),('source_job_yaml_sha256','yaml_sha256'),('source_job_create_sha256','job_create_sha256'),('source_code_tree_sha256','code_tree_sha256')):
            if r.get(rk)!=p.get(pk):raise PrepError('source row producer differs')

def publish(destination,selected,choices,decisions,games,usage,belief,N,S,chat,metadata):
    dest=Path(destination)
    if dest.exists() or dest.is_symlink() or dest.parent.is_symlink() or not dest.parent.is_dir() or dest.parent.stat().st_uid!=os.getuid() or dest.parent.stat().st_mode&0o077:raise PrepError('private exclusive destination required')
    prompts,labels=N.build_prompt_records(selected,decisions,games,S,chat)
    if len(prompts)!=len(choices) or len(labels)!=len(selected):raise PrepError('publication counts differ')
    projected_usage=N.project_baselines(selected,usage);projected_belief=N.project_baselines(selected,belief)
    dest.mkdir(mode=0o700)
    try:
        hashes={}
        for name,data in (('prompts.jsonl',prompts),('labels.jsonl',labels),('usage.jsonl',projected_usage),('belief.jsonl',projected_belief)):
            body=b''.join(N.canonical(row)+b'\n' for row in data)
            fd=os.open(dest/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(body);f.flush();os.fsync(f.fileno())
            hashes[name]=hashlib.sha256(body).hexdigest()
        manifest={'schema':'hardread.reads-n3.cohort/v1','games':len(choices),'decisions':len(prompts),'pokemon_rows':len(selected),'selected':sorted([x['game'],x['duid'],x['uid']] for x in selected),'prompt_sha256':hashes['prompts.jsonl'],'label_sha256':hashes['labels.jsonl'],'usage_sha256':hashes['usage.jsonl'],'belief_sha256':hashes['belief.jsonl'],**metadata}
        fd=os.open(dest/'manifest.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(N.canonical(manifest)+b'\n');f.flush();os.fsync(f.fileno())
        return {'manifest_sha256':sha(dest/'manifest.json'),'prompt_sha256':hashes['prompts.jsonl'],'games':len(choices),'pokemon_rows':len(selected)}
    except BaseException:
        shutil.rmtree(dest,ignore_errors=True);raise

def _load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise PrepError('source import failed')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def prepare(root,config_ref,config_commit,review_ref,review_commit,destination,*,execute=False):
    if not PREP_ENABLED or execute is not True:raise PrepError('N3 preparation disabled pending combined review and commit')
    root=Path(root).resolve()
    config_path=pinned(root,config_ref);committed(root,config_path,config_commit)
    cfg=strict(config_path.read_bytes())
    keys={'schema','status','card','map','audit','source_rows','catalog','source_admission','frozen','code_sha256','tokenizer_dir'}
    if not isinstance(cfg,dict) or set(cfg)!=keys or cfg['schema']!='hardread.n3.preparation-config/v1' or cfg['status']!='frozen':raise PrepError('preparation config differs')
    review_path=pinned(root,review_ref);committed(root,review_path,review_commit)
    review=strict(review_path.read_bytes())
    if review.get('status')!='accepted' or review.get('config_sha256')!=config_ref['sha256'] or review.get('source_map_sha256')!=MAP_SHA or review.get('source_audit_sha256')!=AUDIT_SHA or review.get('card_sha256')!=CARD_SHA:
        raise PrepError('accepted combined source review missing')
    pinned(root,cfg['card'],CARD_SHA)
    pinned(root,cfg['source_admission'],SOURCE_REVIEW_SHA)
    source_map_path=pinned(root,cfg['map'],MAP_SHA);audit_path=pinned(root,cfg['audit'],AUDIT_SHA)
    rows_path=pinned(root,cfg['source_rows'],ROWS_SHA);pinned(root,cfg['catalog'],CATALOG_SHA)
    code=cfg['code_sha256']
    if not isinstance(code,dict) or not REQUIRED.issubset(code):raise PrepError('source closure missing')
    for name,digest in code.items():pinned(root,{'path':name,'sha256':digest})
    if sha(relfile(root,'tools/belief/benchmark.py'))!=FROZEN['benchmark']:raise PrepError('benchmark changed')
    if Path(__file__).resolve()!=(root/'tools/belief/n3_prepare.py').resolve():raise PrepError('loaded preparer differs')
    frozen=cfg['frozen']
    if not isinstance(frozen,dict) or set(frozen)!={'examples','usage','belief_v1'}:raise PrepError('frozen source pins missing')
    source_paths={name:pinned(root,ref,FROZEN[name]) for name,ref in frozen.items()}
    # Only after the committed review and source closure have been verified do
    # we open identity/source data. All paths remain local and read-only.
    source_map=strict(source_map_path.read_bytes());audit=strict(audit_path.read_bytes())
    if source_map.get('s_a_examples_sha256')!=FROZEN['examples'] or source_map.get('source_rows_sha256')!=ROWS_SHA or source_map.get('catalog_79_sha256')!=CATALOG_SHA:raise PrepError('map ancestry differs')
    sys.path[:0]=[str(root/'tools/belief'),str(root/'tools/sft')]
    import roster_hash as roster
    import n3_hrval_join as J
    import reads_n3 as N
    import serialize as S
    import glimmer_chat as G
    import data_admission_runtime as R
    for mod,rel in ((roster,'tools/belief/roster_hash.py'),(R.A,'tools/sft/data_admission.py'),(J,'tools/belief/n3_hrval_join.py'),(N,'tools/belief/reads_n3.py'),(S,'tools/sft/serialize.py'),(G,'tools/sft/glimmer_chat.py'),(R,'tools/sft/data_admission_runtime.py')):
        if Path(mod.__file__).resolve()!=(root/rel).resolve():raise PrepError('loaded source differs')
    audit_tool=_load('n3_audit',root/'scratch_ds/codex/training-roadmap/source-audit/audit_sources.py')
    extractor=_load('n3_extract',root/'tools/dataset/extract.py')
    if audit_tool.ROOT.resolve()!=root:raise PrepError('audit source root differs')
    authenticate_metadata(root,source_map,audit,rows(rows_path),audit_tool)
    examples=rows(source_paths['examples'])
    if len(examples)!=146225 or len({x['duid'] for x in examples})!=33253:raise PrepError('frozen primary population differs')
    selected,choices=N.select_examples(examples,expected_games=440)
    if len(selected)!=1954:raise PrepError('selected row count differs')
    decisions={};games={}
    for entry in source_map['archives']:
        selected_in_archive=J.map_selections(source_map,entry['source_path'],selected)
        ds,gs=J.join_one(relfile(root,entry['source_path']),entry['source_sha256'],selected_in_archive,R.read_archive,extractor,audit_tool.canonical_log_hash,expected_seed=entry['expected_seed'])
        if set(ds)&set(decisions) or set(gs)&set(games):raise PrepError('duplicate source join')
        decisions.update(ds);games.update(gs)
    if len(decisions)!=440 or len(games)!=440:raise PrepError('source join count differs')
    tokenizer=Path(cfg['tokenizer_dir'])
    if not tokenizer.is_absolute():tokenizer=root/tokenizer
    if tokenizer.is_symlink() or not tokenizer.is_dir():raise PrepError('tokenizer directory missing')
    chat=G.GlimmerChat(tokenizer,verify=True)
    usage=rows(source_paths['usage']);belief=rows(source_paths['belief_v1'])
    metadata={'frozen_source_sha256':FROZEN,'source_code_sha256':code,'source_map_sha256':MAP_SHA,'source_audit_sha256':AUDIT_SHA,'source_rows_sha256':ROWS_SHA,'source_review_sha256':review_ref['sha256'],'selected_source_admission_sha256':SOURCE_REVIEW_SHA,'config_sha256':config_ref['sha256'],'tokenizer_sha256':chat.tokenizer_sha256,'template_sha256':chat.template_sha256,'archives':[{k:e[k] for k in ('source_path','source_sha256','bytes')} for e in source_map['archives']]}
    return publish(destination,selected,choices,decisions,games,usage,belief,N,S,chat,metadata)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','config','config-sha256','config-commit','review','review-sha256','review-commit','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--execute',action='store_true');a=p.parse_args()
    result=prepare(a.root,{'path':a.config,'sha256':a.config_sha256},a.config_commit,{'path':a.review,'sha256':a.review_sha256},a.review_commit,a.out,execute=a.execute)
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
