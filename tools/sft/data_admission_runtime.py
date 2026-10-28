"""Guarded production adapters for the prospective v2 data-admission contract.

No data is read on import. CLI has no implicit execution path. Source scans
and builds require an explicit flag, a committed CARD, a hash-bound reviewed
prebuild specification and a separately committed root authorization receipt.
"""
import argparse
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

import data_admission as A

class RuntimeErrorClosed(A.AdmissionError):pass

def sha(raw):return hashlib.sha256(raw).hexdigest()
def file_sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def _read_ref(root,ref):
    if not isinstance(ref,dict) or set(ref)!={'path','sha256'} or not A._sha(ref['sha256']):raise RuntimeErrorClosed('unbound artifact reference')
    p=Path(ref['path']);p=p if p.is_absolute() else Path(root)/p
    if p.is_symlink() or not p.is_file():raise RuntimeErrorClosed('artifact missing')
    raw=p.read_bytes()
    if sha(raw)!=ref['sha256']:raise RuntimeErrorClosed('artifact hash differs')
    return raw

def _committed(root,commit,path,expected):
    if not re.fullmatch('[0-9a-f]{40}',commit or '') or not isinstance(path,str) or Path(path).is_absolute() or '..' in Path(path).parts or not A._sha(expected):raise RuntimeErrorClosed('commit reference malformed')
    def git(*args):
        out=subprocess.run(['git','-C',str(root),*args],capture_output=True,check=False)
        if out.returncode:raise RuntimeErrorClosed('commit verification failed')
        return out.stdout
    git('merge-base','--is-ancestor',commit,'HEAD');raw=git('show',commit+':'+path)
    if sha(raw)!=expected:raise RuntimeErrorClosed('committed hash differs')
    return raw

def authorize(root,spec_ref,card_ref,approval_ref,purpose,*,execute=False):
    """Validate code, CARD and committed owner approval before opening data."""
    if execute is not True:raise RuntimeErrorClosed('real data execution disabled without --execute')
    spec_raw=_read_ref(root,spec_ref)
    try:spec=json.loads(spec_raw)
    except (ValueError,UnicodeError) as e:raise RuntimeErrorClosed('specification malformed') from e
    if not isinstance(card_ref,dict) or set(card_ref)!={'commit','path','sha256'} or not isinstance(approval_ref,dict) or set(approval_ref)!={'commit','path','sha256'}:raise RuntimeErrorClosed('committed authorization missing')
    card=_committed(root,card_ref['commit'],card_ref['path'],card_ref['sha256'])
    try:approval=json.loads(_committed(root,approval_ref['commit'],approval_ref['path'],approval_ref['sha256']))
    except (ValueError,UnicodeError) as e:raise RuntimeErrorClosed('approval malformed') from e
    expected={'status':'approved','purpose':purpose,'card_commit':card_ref['commit'],'card_sha256':card_ref['sha256'],'spec_sha256':spec_ref['sha256']}
    if not isinstance(approval,dict) or any(approval.get(k)!=v for k,v in expected.items()) or not isinstance(approval.get('owner'),str) or not approval['owner'].strip():raise RuntimeErrorClosed('root authorization differs')
    if spec.get('status')!='FROZEN_FOR_REVIEW' or spec_ref['sha256'].encode() not in card or spec.get('contract_sha256')!='8547147630b68a0dd78689b3222515bdf48b5469f13d8f49af0eaeec8e1a2b33' or spec.get('amendment_sha256')!='77b82491c07e5d212bfa81403b88a73c8f3f84783f6ca5befec1fdbfe1ebd2e7' or spec.get('source_health_amendment_sha256')!='4a06e7f14df2dad26d8f1472691a3cc9af23e049f8497d685a29ec56a4d02326':raise RuntimeErrorClosed('reviewed CARD or prebuild pins incomplete')
    code=spec.get('code_sha256')
    required={'tools/sft/data_admission.py','tools/sft/data_admission_runtime.py','tools/sft/build_sft.py','tools/sft/serialize.py','tools/sft/dex.json','tools/sft/glimmer_chat.py','tools/dataset/extract.py','tools/belief/roster_hash.py','tools/human/parse_replays.py','azure/student/p6_trainonly_inspect.py'}
    if not isinstance(code,dict) or not required.issubset(code):raise RuntimeErrorClosed('source pins missing')
    if Path(A.__file__).resolve()!=(Path(root)/'tools/sft/data_admission.py').resolve() or Path(__file__).resolve()!=(Path(root)/'tools/sft/data_admission_runtime.py').resolve():raise RuntimeErrorClosed('loaded source root differs')
    for path,expected_sha in code.items():
        if Path(path).is_absolute() or '..' in Path(path).parts or not A._sha(expected_sha):raise RuntimeErrorClosed('source pin malformed')
        p=Path(root)/path
        if p.is_symlink() or not p.is_file() or file_sha(p)!=expected_sha:raise RuntimeErrorClosed('source bytes differ')
    return spec

def read_archive(path,expected_sha):
    """Strict tar member/path validation and hash verification, no extraction to disk."""
    path=Path(path)
    if path.is_symlink() or not path.is_file() or not A._sha(expected_sha) or file_sha(path)!=expected_sha:raise RuntimeErrorClosed('archive hash differs')
    if not path.name.endswith('.tar.gz'):raise RuntimeErrorClosed('archive filename malformed')
    root=path.name[:-7];files={};seen=set()
    with tarfile.open(path,'r:gz') as tar:
        for member in tar:
            name=member.name.rstrip('/');parts=PurePosixPath(name).parts
            if not name or name != PurePosixPath(name).as_posix() or name.startswith('/') or any(x in ('','.','..') for x in parts) or name in seen or not (name==root or name.startswith(root+'/')):raise RuntimeErrorClosed('unsafe or duplicate archive member')
            seen.add(name)
            if member.issym() or member.islnk() or member.isdev() or not (member.isdir() or member.isfile()):raise RuntimeErrorClosed('unsupported archive member')
            if member.isdir():continue
            if PurePosixPath(name).name.startswith('._'):continue
            stream=tar.extractfile(member)
            if stream is None:raise RuntimeErrorClosed('archive member unreadable')
            files[name[len(root)+1:]]=stream.read()
    if file_sha(path)!=expected_sha:raise RuntimeErrorClosed('archive changed during read')
    return root,files

def extract_joined_archive(path,expected_sha,source,team_metadata,sanitizer,extractor,replay_identity):
    """Re-derive strict game/log/side joins and extractor rows from one archive."""
    root,raw=read_archive(path,expected_sha)
    if root!=source.get('run_id') or source.get('sha256')!=expected_sha:raise RuntimeErrorClosed('source identity differs')
    if 'games.jsonl' not in raw:raise RuntimeErrorClosed('games member missing')
    try:summary=json.loads(raw['summary.json'])
    except (KeyError,ValueError,UnicodeError) as e:raise RuntimeErrorClosed('source summary missing') from e
    if not isinstance(summary,dict) or summary.get('run_id')!=root or type(summary.get('seed')) is not int or summary.get('seed')!=source.get('seed') or summary.get('team_pool')!='gen9/hr_train' or summary.get('cfg_a')!='C1t1' or summary.get('cfg_b')!='C1t1':raise RuntimeErrorClosed('source summary lineage differs')
    logs={}
    for name,body in raw.items():
        if not name.startswith('battlelogs/') or not name.endswith('.log.json'):continue
        try:log=json.loads(body)
        except (ValueError,UnicodeError) as e:raise RuntimeErrorClosed('battle log malformed') from e
        if not isinstance(log,dict):raise RuntimeErrorClosed('battle log malformed')
        pair=(log.get('p1'),log.get('p2'))
        if not all(isinstance(x,str) and x for x in pair) or pair[0]==pair[1] or pair in logs or pair[::-1] in logs:raise RuntimeErrorClosed('ambiguous replay players')
        logs[pair]=(name,body,log)
    games=[];tombstones=[];expected_decision_ids=[];raw_decision_hashes=[];seen_games=set();seen_logs=set();sanitized_side_files={};decision_worlds={};source_decided_count=0;allowed_side_files=set()
    for line_index,line in enumerate(raw['games.jsonl'].splitlines(keepends=True)):
        if not line.strip():raise RuntimeErrorClosed('blank game line')
        try:g=json.loads(line)
        except (ValueError,UnicodeError) as e:raise RuntimeErrorClosed('game JSON malformed') from e
        number=g.get('game') if isinstance(g,dict) else None
        if type(number) is not int or number<0 or number in seen_games:raise RuntimeErrorClosed('duplicate game number')
        if g.get('cfg_a')!='C1t1' or g.get('cfg_b')!='C1t1':raise RuntimeErrorClosed('source game wrong configuration')
        if g.get('winner') is not None and g.get('winner') not in ('A','B'):raise RuntimeErrorClosed('malformed present winner marker')
        decided=g.get('winner') in ('A','B');source_decided_count+=int(decided)
        rc_a,rc_b=g.get('rc_a'),g.get('rc_b');kind=source.get('kind');status=g.get('status')
        if kind not in ('P5','P6'):raise RuntimeErrorClosed('source family differs')
        if any(not isinstance(v,(str,int,float,bool,type(None))) or (isinstance(v,float) and not math.isfinite(v)) for v in (rc_a,rc_b,status)):raise RuntimeErrorClosed('source health field malformed')
        if kind=='P6' and (not decided or type(rc_a) is not int or type(rc_b) is not int or rc_a!=0 or rc_b!=0):raise RuntimeErrorClosed('P6 source health differs')
        typed=type(rc_a) is int and type(rc_b) is int
        incomplete=kind=='P5' and (status!='ok' or not decided or not typed)
        nonzero=kind=='P5' and typed and (rc_a!=0 or rc_b!=0)
        pair=(g.get('a'),g.get('b'))
        if not all(isinstance(x,str) and x for x in pair) or pair[0]==pair[1]:raise RuntimeErrorClosed('source player identity malformed')
        if pair in logs:match=logs[pair];player_a='p1'
        elif pair[::-1] in logs:match=logs[pair[::-1]];player_a='p2'
        else:match=None;player_a=None
        if match is None and not incomplete:raise RuntimeErrorClosed('source replay join missing')
        names=[g.get('team_a'),g.get('team_b')]
        if not all(isinstance(x,str) and x.startswith('gen9/hr_train/') for x in names):raise RuntimeErrorClosed('source team pool differs')
        uid=f'{root}/g{number:04d}'
        allowed_side_files.update(f'g{number:04d}_{side}.decisions.jsonl' for side in ('a','b'))
        if match is None:
            if status!='timeout' or typed:raise RuntimeErrorClosed('missing-log tombstone not linked to timeout/null-RC anomaly')
            if len(set(names))!=2 or any(not isinstance(team_metadata.get(n),dict) or team_metadata[n].get('split')!='dev' or not A._sha(team_metadata[n].get('roster_digest')) for n in names):raise RuntimeErrorClosed('tombstone source roster missing or ambiguous')
            tomb={'uid':uid,'run_id':root,'game':number,'archive_sha256':expected_sha,'producer':source['producer'],'chunk':source['chunk'],'seed':source['seed'],'line_index':line_index,'line_sha256':sha(line),'team_names':names,'roster_digests':[team_metadata[n]['roster_digest'] for n in names],'status':status,'decided':decided,'rc_a':rc_a,'rc_b':rc_b,'side_ids':[],'side_content_hashes':[]}
            for side in ('A','B'):
                member_name=f'g{number:04d}_{side.lower()}.decisions.jsonl'
                for ordinal,dline in enumerate(raw.get(member_name,b'').splitlines(keepends=True)):
                    if not dline.strip():raise RuntimeErrorClosed('blank tombstone side row')
                    tomb['side_ids'].append(f'{uid}/{side}/{ordinal:03d}');tomb['side_content_hashes'].append(sha(dline))
            tombstones.append(tomb);seen_games.add(number);continue
        member,logbytes,log=match
        if member in seen_logs:raise RuntimeErrorClosed('replay used by multiple games')
        try:
            seats=[[x.get('species') or x.get('name') for x in log[seat+'team']] for seat in ('p1','p2')]
        except (KeyError,TypeError,AttributeError) as e:raise RuntimeErrorClosed('joined rosters missing') from e
        a=0 if player_a=='p1' else 1;rosters=[seats[a],seats[1-a]]
        from roster_hash import roster_digest
        try:digests=[roster_digest(x) for x in rosters]
        except (ValueError,TypeError) as e:raise RuntimeErrorClosed('invalid joined roster') from e
        ident=replay_identity(uid,log,sanitizer)
        game={'key':uid,'uid':uid,'joined_uid':uid,'run_id':root,'game':number,'archive_sha256':expected_sha,
              'producer':source['producer'],'chunk':source['chunk'],'seed':source['seed'],'line_index':line_index,'line_sha256':sha(line),
              'raw_replay_sha256':ident['replay_sha256'],'normalized_replay_sha256':ident['normalized_replay_sha256'],
              'source_log_bytes':logbytes,'log_sha256':sha(logbytes),'log':log.get('log'),'player_a':player_a,
              'player_names':[log.get('p1'),log.get('p2')],'source_player_a':g['a'],'team_names':names,'joined_team_names':names[:],
              'joined_rosters':rosters,'roster_digests':digests,'source_kind':source['kind'],'rc_a':rc_a,'rc_b':rc_b,'source_status':status,'decided':decided,'p5_incomplete_game':incomplete,'p5_nonzero_process_rc':nonzero,'decision_count':0}
        games.append(game);seen_games.add(number);seen_logs.add(member)
        for side in ('A','B'):
            name=f'g{number:04d}_{side.lower()}.decisions.jsonl'
            if name not in raw or not raw[name].strip():
                if incomplete or nonzero:continue
                raise RuntimeErrorClosed('side decision log missing')
            sanitized=[]
            for ordinal,dline in enumerate(raw[name].splitlines(keepends=True)):
                if not dline.strip():raise RuntimeErrorClosed('blank decision row')
                try:d=json.loads(dline)
                except (ValueError,UnicodeError) as e:raise RuntimeErrorClosed('decision log malformed') from e
                if not isinstance(d,dict) or d.get('v') not in source['decision_log_versions']:raise RuntimeErrorClosed('decision schema differs')
                decision_id=f'{uid}/{side}/{ordinal:03d}';expected_decision_ids.append(decision_id);raw_decision_hashes.append(sha(dline));game['decision_count']+=1
                decision_worlds[decision_id]=d.get('worlds')
                # The index authenticates identity only. The extractor still derives
                # side and history joins, but must not inspect teacher targets here.
                d=dict(d,worlds=[]);sanitized.append(json.dumps(d,separators=(',',':')).encode()+b'\n')
            sanitized_side_files[name]=b''.join(sanitized)
    if len(seen_logs)!=len(logs) or not seen_games or summary.get('games')!=len(seen_games) or summary.get('completed')!=source_decided_count:raise RuntimeErrorClosed('orphan or incomplete game census')
    if not {name for name in raw if name.endswith('.decisions.jsonl')}.issubset(allowed_side_files):raise RuntimeErrorClosed('source side-file census differs')
    if source.get('kind')=='P6' and (source.get('game_count')!=20 or len(games)!=20):raise RuntimeErrorClosed('P6 game census differs')
    if source.get('kind')=='P5' and source.get('game_count') is not None and len(games)!=source['game_count']:raise RuntimeErrorClosed('P5 game census differs')
    if source.get('kind') not in ('P5','P6'):raise RuntimeErrorClosed('source family differs')
    check=A.index_games(games,team_metadata,{expected_sha:{k:source[k] for k in ('producer','chunk','seed')}})
    extract_input=dict(raw);extract_input.update(sanitized_side_files)
    loaders={name:(lambda b=body:b) for name,body in extract_input.items()};out=io.StringIO();game_out=io.StringIO()
    extractor.extract_run(root,loaders,out,game_out,{ 'games':0,'games_without_log':0,'decisions':0,'decisions_without_cut':0})
    extracted_games=[json.loads(line) for line in game_out.getvalue().splitlines()]
    if len(extracted_games)!=len(games):raise RuntimeErrorClosed('extractor game inventory differs')
    for actual,expected in zip(extracted_games,games):
        if any(actual.get(k)!=v for k,v in {'uid':expected['uid'],'run_id':root,'game':expected['game'],'player_a':expected['player_a'],'team_a':expected['team_names'][0],'team_b':expected['team_names'][1],'log':expected['log']}.items()):raise RuntimeErrorClosed('extractor game join differs')
    rows=[json.loads(line) for line in out.getvalue().splitlines()]
    if [row.get('uid') for row in rows]!=expected_decision_ids:raise RuntimeErrorClosed('extracted decision inventory differs')
    seen_ids=set();seen_order=set()
    for row in rows:
        uid=row['uid'].rsplit('/',2)[0];A.decision_identity(row,check['games'][uid],seen_ids,seen_order)
        row['worlds']=decision_worlds[row['uid']]
    if len(set(raw_decision_hashes))!=len(raw_decision_hashes):raise RuntimeErrorClosed('duplicate source decision content')
    return games,rows,raw_decision_hashes,tombstones


def select_fixed400_ids(action_ids, expected_sha):
    """Apply the historical selection to identity strings only."""
    if not isinstance(action_ids,list) or len(action_ids)!=1066 or len(set(action_ids))!=1066 or any(not isinstance(x,str) or not x.endswith('/action') for x in action_ids):raise RuntimeErrorClosed('fixed-v0 action identity inventory differs')
    ordered=sorted(action_ids)
    selected=[ordered[int(i*(len(ordered)/400))] for i in range(400)]
    if sha('\n'.join(selected).encode())!=expected_sha:raise RuntimeErrorClosed('selected fixed-v0 identity digest differs')
    return selected


def identity_fields(line):
    """Extract only top-level example_id/task string values; skip other values lexically.

    This intentionally does not deserialize labels, scores, outputs or quality fields.
    """
    if not isinstance(line,str):raise RuntimeErrorClosed('identity row must be text')
    n=len(line); i=0; decoder=json.JSONDecoder(); found={}; keys=set()
    def ws(pos):
        while pos<n and line[pos] in ' \t\r\n':pos+=1
        return pos
    def string(pos):
        if pos>=n or line[pos]!='"':raise RuntimeErrorClosed('identity JSON key/value is not a string')
        try:value,end=decoder.raw_decode(line,pos)
        except ValueError as e:raise RuntimeErrorClosed('malformed identity string') from e
        if not isinstance(value,str):raise RuntimeErrorClosed('invalid identity string')
        return value,end
    def skip(pos):
        start=pos; stack=[]; in_string=False; escaped=False
        while pos<n:
            c=line[pos]
            if in_string:
                if escaped:escaped=False
                elif c=='\\':escaped=True
                elif c=='"':in_string=False
            elif c=='"':in_string=True
            elif c in '[{':stack.append(c)
            elif c in ']}':
                if not stack:break
                if (stack[-1],c) not in (('[',']'),('{','}')):raise RuntimeErrorClosed('malformed identity structure')
                stack.pop()
            elif c==',' and not stack:break
            pos+=1
        if in_string or stack or not line[start:pos].strip():raise RuntimeErrorClosed('malformed skipped field')
        return pos
    i=ws(i)
    if i>=n or line[i]!='{':raise RuntimeErrorClosed('identity row is not an object')
    i+=1
    while True:
        i=ws(i)
        if i<n and line[i]=='}':i+=1;break
        key,i=string(i);i=ws(i)
        if key in keys:raise RuntimeErrorClosed('duplicate identity object key')
        keys.add(key)
        if i>=n or line[i]!=':':raise RuntimeErrorClosed('malformed identity field')
        i=ws(i+1)
        if key in ('example_id','task'):
            found[key],i=string(i)
        else:i=skip(i)
        i=ws(i)
        if i<n and line[i]==',':i+=1;continue
        if i<n and line[i]=='}':i+=1;break
        raise RuntimeErrorClosed('malformed identity separator')
    if ws(i)!=n or set(found)!={'example_id','task'} or found['task'] not in ('action','reads') or not found['example_id'].endswith('/'+found['task']):raise RuntimeErrorClosed('identity fields differ')
    return found


def read_identity_file(path,expected_sha):
    """Hash exact frozen gzip bytes and project only IDs/task, checking for changes."""
    path=Path(path)
    if path.is_symlink() or not path.is_file() or not A._sha(expected_sha) or file_sha(path)!=expected_sha:raise RuntimeErrorClosed('frozen identity file differs')
    result=[];seen=set()
    with gzip.open(path,'rt',encoding='utf-8',errors='strict') as stream:
        for line in stream:
            item=identity_fields(line)
            if item['example_id'] in seen:raise RuntimeErrorClosed('duplicate frozen example ID')
            seen.add(item['example_id']);result.append(item)
    if file_sha(path)!=expected_sha:raise RuntimeErrorClosed('frozen identity file changed')
    return result


def holdout_projection(index,decision_to_game,v1_val,v1_eval,fixed_v0,selected_sha):
    """Map pinned ID-only inputs to authenticated source games and rosters."""
    val={r['example_id']:r for r in v1_val};ev={r['example_id']:r for r in v1_eval}
    if len(val)!=2666 or len(ev)!=2666 or set(val)!=set(ev):raise RuntimeErrorClosed('frozen v1 paired identity census differs')
    actions=[r['example_id'] for r in fixed_v0 if r['task']=='action']
    fixed=select_fixed400_ids(actions,selected_sha)
    def project(eids):
        result=[];games=set()
        for eid in sorted(eids):
            decision,sep,task=eid.rpartition('/')
            key=decision_to_game.get(decision);game=index['games'].get(key)
            if not sep or task not in ('action','reads') or game is None:raise RuntimeErrorClosed('frozen identity does not resolve to authenticated decision')
            result.append({'example_id':eid,'game_key':key,'archive_sha256':game['archive_sha256'],'roster_digests':game['roster_digests']});games.add(key)
        return result,games
    v1,v1_games=project(val);f400,f400_games=project(fixed)
    if len(v1_games)!=128 or len(f400_games)!=23:raise RuntimeErrorClosed('holdout source-game census differs')
    return A.project_holdouts(index,{'v1':v1,'fixed400':f400},{'v1':2666,'fixed400':400},decision_to_game)


def roster_metadata(pool_raw,sealed_raw,split_raw,validated_raw):
    """Resolve public metadata only; never open team files or sealed payloads."""
    from roster_hash import roster_digest
    try:
        pool=json.loads(pool_raw);sealed=json.loads(sealed_raw);split=json.loads(split_raw)
        records=[json.loads(line) for line in validated_raw.splitlines() if line.strip()]
    except (ValueError,UnicodeError) as e:raise RuntimeErrorClosed('team metadata malformed') from e
    if not all(isinstance(x,dict) for x in (pool,sealed,split)) or not pool.get('source_split') or not pool['source_split']==sealed.get('source_split')==split.get('split_sha256'):raise RuntimeErrorClosed('team split binding differs')
    def names(values):
        if not isinstance(values,list) or not values or any(not isinstance(x,str) or not x for x in values):raise RuntimeErrorClosed('team name metadata missing')
        result=[PurePosixPath(x).name for x in values]
        if len(set(result))!=len(result):raise RuntimeErrorClosed('duplicate metadata basename')
        return set(result)
    dev=names(split.get('dev_files'));reserved=names(split.get('reserved_files'));sealed_names=names(sealed.get('files'))
    if dev&reserved:raise RuntimeErrorClosed('dev and reserved overlap')
    by_name={}
    for row in records:
        if not isinstance(row,dict):raise RuntimeErrorClosed('team metadata record malformed')
        if row.get('valid') is not True:continue
        if not isinstance(row.get('file'),str) or not isinstance(row.get('species'),list):raise RuntimeErrorClosed('team metadata record missing')
        name=PurePosixPath(row['file']).name
        try:digest=roster_digest(row['species'])
        except (ValueError,TypeError) as e:raise RuntimeErrorClosed('invalid metadata roster') from e
        if name in by_name and by_name[name]!=digest:raise RuntimeErrorClosed('ambiguous team metadata')
        by_name[name]=digest
    needed=dev|reserved|sealed_names
    if needed-set(by_name):raise RuntimeErrorClosed('team roster metadata incomplete')
    heldout=pool.get('val_roster_sha256')
    if not isinstance(heldout,list) or len(heldout)!=596 or len(set(heldout))!=596 or any(not A._sha(x) for x in heldout):raise RuntimeErrorClosed('public heldout roster census differs')
    heldout=set(heldout)
    val_names={name for name,digest in by_name.items() if digest in heldout}
    val_count=(pool.get('teams') or {}).get('hr_val')
    if type(val_count) is not int or len(val_names & dev)!=val_count:raise RuntimeErrorClosed('public heldout roster mapping differs')
    teams={'gen9/hr_train/'+name:{'split':'dev','roster_digest':by_name[name]} for name in dev}
    heldout_names={'gen9/hr_train/'+name for name in val_names}
    reserved_names={'gen9/hr_train/'+name for name in reserved|sealed_names}
    excluded_rosters={by_name[name] for name in reserved|sealed_names}
    return teams,heldout,heldout_names,reserved_names,excluded_rosters


def _path(root,ref):
    _read_ref(root,ref)
    p=Path(ref['path']);return p if p.is_absolute() else Path(root)/p


def authenticated_catalog(root,spec,p6):
    """Re-derive the exact P5/P6 inventory and producer/seed proofs from pins."""
    refs=spec['source_refs'];p5raw=_read_ref(root,refs['p5_manifest']);invraw=_read_ref(root,refs['p6_inventory'])
    if sha(p5raw)!='1a7c7035c72a3a11a2a9ca5e7546b165c2b72e997caa9748d4fdd346edbc3b1c' or sha(invraw)!='5c856180d2337943c9c87c5d9f5b9c17bee28b2997f927b62035fe6cd27e81f8':raise RuntimeErrorClosed('source universe is not the frozen inventory')
    old=json.loads(p5raw)['source_tarballs'];inventory=json.loads(invraw)
    refs_by_name=spec['p6_receipts']
    receipt_bytes={name:_read_ref(root,ref) for name,ref in refs_by_name.items()}
    A.validate_metadata_inventory(p5raw,invraw,receipt_bytes,{'p5_sha256':sha(p5raw),'inventory_sha256':sha(invraw),'p5_count':264,'candidate_count':100,'quarantine_count':6,'receipt_sha256':{n:r['sha256'] for n,r in refs_by_name.items()}})
    producers={}
    if set(spec['p6_producers'])!=set(p6.P6_SEED_BASES):raise RuntimeErrorClosed('P6 producer census differs')
    for name,ref in spec['p6_producers'].items():
        yaml=_path(root,ref['yaml']);create=_path(root,ref['create']);code=Path(ref['code_root']);code=code if code.is_absolute() else Path(root)/code
        observed=p6.parse_producer_files(yaml,create,code);p6.validate_producer_metadata(observed,ref['frozen'])
        if observed['job_name']!=name:raise RuntimeErrorClosed('P6 producer key differs')
        producers[name]=observed
    p6.verify_seed_lineage(_path(root,refs['seed_audit']),_path(root,refs['seed_reconciliation']),producers)
    old_producers=authenticate_p5_producers(root,spec['p5_producers'])
    old_bindings=spec['p5_archives'];new_bindings=spec['p6_archives']
    if set(old_bindings)!={r['name'] for r in old} or set(new_bindings)!={r['archive_name'] for r in inventory['candidate_archives']}:raise RuntimeErrorClosed('archive binding inventory differs')
    catalog=[]
    for row in old:
        bind=old_bindings[row['name']];path=Path(bind['path']);path=path if path.is_absolute() else Path(root)/path
        producer=old_producers.get(row['job']);match=re.fullmatch(r'(lab-n[1-6])-c([0-9]{3})\.tar\.gz',path.name)
        if path.name!=row['name'] or producer is None or not match or match.group(1)!=producer['prefix']:raise RuntimeErrorClosed('P5 archive lineage binding incomplete')
        chunk=int(match.group(2));seed=producer['seed_base']+chunk
        catalog.append({'kind':'P5','path':str(path),'run_id':path.name[:-7],'sha256':row['sha256'],'bytes':row['bytes'],'producer':row['job'],'chunk':chunk,'seed':seed,'decision_log_versions':[2]})
    for row in inventory['candidate_archives']:
        name=row['archive_name'];bind=new_bindings[name];path=Path(bind['path']);path=path if path.is_absolute() else Path(root)/path
        producer=producers.get(row['producer_job_name'])
        if path.name!=name or not producer:raise RuntimeErrorClosed('P6 archive binding differs')
        p6.validate_chunk_binding(name,row['chunk_index'],row['seed'],producer)
        catalog.append({'kind':'P6','path':str(path),'run_id':name[:-7],'sha256':row['archive_sha256'],'bytes':row['archive_size_bytes'],'producer':row['producer_job_name'],'chunk':row['chunk_index'],'seed':row['seed'],'game_count':20,'decision_log_versions':[3]})
    if len({x['sha256'] for x in catalog})!=364 or len({x['run_id'] for x in catalog})!=364:raise RuntimeErrorClosed('cross-source archive duplicate')
    return sorted(catalog,key=lambda row:row['run_id'])


def compare_p6_inspection(inspected,games,rows,hashes):
    if len(inspected)!=len(games):raise RuntimeErrorClosed('P6 inspector game census differs')
    flattened=[]
    for old,new in zip(inspected,games):
        if old['uid']!=new['uid'] or old['game_line_index']!=new['line_index'] or old['game_line_sha256']!=new['line_sha256'] or old['log_sha256']!=new['log_sha256'] or list(old['teams'])!=new['team_names'] or old['log']!=json.loads(new['source_log_bytes']):raise RuntimeErrorClosed('P6 independent inspector join differs')
        flattened.extend(old['decisions'])
    if flattened!=list(zip([row['uid'] for row in rows],hashes)):raise RuntimeErrorClosed('P6 independent decision inventory differs')


def authenticate_p5_producers(root,refs):
    import shlex
    import yaml
    expected={f'hrlab-20261026-n{i}-01d4' for i in range(1,7)}
    if set(refs)!=expected:raise RuntimeErrorClosed('P5 producer census differs')
    result={}
    for name,ref in refs.items():
        raw=_read_ref(root,ref['yaml']);farm=_read_ref(root,ref['farm_script']);create_raw=_read_ref(root,ref['create'])
        if sha(farm)!='edf67ec7c172321e83f65243a546882d3caa6f0c85262eb0ef1bd870e0d0dfce':raise RuntimeErrorClosed('P5 farm source differs')
        doc=yaml.safe_load(raw);create=json.loads(create_raw);cmd=doc.get('command') if isinstance(doc,dict) else None
        if not isinstance(doc,dict) or not isinstance(create,dict) or doc.get('name')!=name or create.get('name')!=name or not isinstance(cmd,str) or create.get('command')!=cmd:raise RuntimeErrorClosed('P5 producer YAML/create differs')
        tokens=shlex.split(cmd);positions=[i for i,x in enumerate(tokens) if x.endswith('farm_chunks.sh')]
        if len(positions)!=1:raise RuntimeErrorClosed('P5 farm command ambiguous')
        i=positions[0];args=tokens[i+1:i+7];number=int(name.split('-n')[1].split('-')[0]);prefix=f'lab-n{number}'
        if len(args)!=6 or args[:5]!=['${{outputs.artifacts}}',prefix,'25','200','2026-10-27T08:00:00Z'] or not args[5].isdigit() or tokens[i+7:]!=['--concurrency','5','--cfg-a','C1t1','--cfg-b','C1t1','--timeout','1800','--team-pool','gen9/hr_train','--log-decisions']:raise RuntimeErrorClosed('P5 producer configuration differs')
        result[name]={'prefix':prefix,'seed_base':int(args[5])}
    return result


def _load_runtime_modules(root):
    """Import the reviewed local source only after source-pin authorization."""
    import importlib.util
    import extract
    import roster_hash
    for module,path in ((extract,'tools/dataset/extract.py'),(roster_hash,'tools/belief/roster_hash.py')):
        if Path(module.__file__).resolve()!=(Path(root)/path).resolve():raise RuntimeErrorClosed('imported module path differs')
    path=Path(root)/'azure/student/p6_trainonly_inspect.py'
    modspec=importlib.util.spec_from_file_location('glimmer_v2_p6_inspector',path)
    if not modspec or not modspec.loader:raise RuntimeErrorClosed('P6 inspector unavailable')
    p6=importlib.util.module_from_spec(modspec);modspec.loader.exec_module(p6)
    sanitizer=p6.load_pinned_sanitizer(Path(root)/'tools/human/parse_replays.py')
    return p6,extract,sanitizer


def _hash_set(values):return A.digest(sorted(values))



_COMPACT_GAME_FIELDS=('key','uid','run_id','game','archive_sha256','producer','chunk','seed','line_index','line_sha256','raw_replay_sha256','normalized_replay_sha256','log_sha256','player_a','team_names','roster_digests','source_kind','rc_a','rc_b','source_status','decided','p5_incomplete_game','p5_nonzero_process_rc','decision_count')

def compact_game(game):
    return {name:game[name] for name in _COMPACT_GAME_FIELDS}

def verified_archive_batch(source,teams,p6,extractor,sanitizer):
    path=Path(source['path'])
    if path.is_symlink() or not path.is_file() or path.stat().st_size!=source['bytes']:raise RuntimeErrorClosed('source archive size or location differs')
    inspected=p6.inspect_archive(path,source['sha256'],source['seed']) if source['kind']=='P6' else None
    batch,rows,hashes,tombs=extract_joined_archive(path,source['sha256'],source,teams,sanitizer,extractor,p6.replay_identity)
    if inspected is not None:compare_p6_inspection(inspected,batch,rows,hashes)
    return batch,rows,hashes,tombs

def append_compact_batch(index,decisions,content,batch,rows,hashes,teams,source):
    """Validate one batch, keep only identity links and discard row payloads."""
    local=A.index_games(batch,teams,{source['sha256']:{k:source[k] for k in ('producer','chunk','seed')}})
    if content.intersection(hashes):raise RuntimeErrorClosed('cross-source duplicate raw decision content')
    content.update(hashes)
    for key,game in local['games'].items():
        if key in index['games']:raise RuntimeErrorClosed('duplicate game UID')
        for token in A.game_tokens(game):
            if token in index['token_owner']:raise RuntimeErrorClosed('conflicting or duplicate game identity')
            index['token_owner'][token]=key
        index['games'][key]=compact_game(game);index['components'][key]=local['components'][key]
    for row in rows:
        uid=row['uid'];key=uid.rsplit('/',2)[0]
        if uid in decisions or key not in local['games']:raise RuntimeErrorClosed('cross-source duplicate or missing decision ID')
        decisions[uid]=key

def iter_verified_batches(root,state,receipt):
    """Reauthenticate and release each archive before advancing to the next."""
    p6,extractor,sanitizer=_load_runtime_modules(root)
    sources=state['catalog'];records=receipt['archive_records']
    if len(sources)!=len(records):raise RuntimeErrorClosed('reviewed archive census differs')
    for source,record in zip(sources,records):
        batch,rows,hashes,tombs=verified_archive_batch(source,state['teams'],p6,extractor,sanitizer)
        if (record['archive_sha256']!=source['sha256'] or len(batch)!=record['game_count'] or len(rows)!=record['decision_count'] or A.digest([r['uid'] for r in rows])!=record['decision_ids_sha256'] or A.digest(hashes)!=record['decision_content_sha256'] or A.digest(tombs)!=record['tombstone_sha256']):raise RuntimeErrorClosed('second-pass archive decisions differ')
        for game in batch:
            if state['index']['games'].get(game['key'])!=compact_game(game):raise RuntimeErrorClosed('second-pass game identity differs')
        yield batch,rows
        del batch,rows,hashes,tombs
    if authenticated_catalog(root,state['spec'],p6)!=sources:raise RuntimeErrorClosed('source producer metadata changed during build')

class RecordSpool:
    """Length-prefixed private records with only 64-bit offsets in memory."""
    def __init__(self,path):
        from array import array
        self.path=Path(path);self.offsets=array('Q');self._space(64*1024*1024)
        self.file=os.fdopen(os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600),'w+b')
    def _space(self,need):
        import shutil
        if shutil.disk_usage(self.path.parent).free<need:raise RuntimeErrorClosed('insufficient private spool space')
    def append(self,record):
        data=A.canonical(record)+b'\n'
        if len(self.offsets)%1024==0:self._space(64*1024*1024+len(data))
        self.offsets.append(self.file.tell());self.file.write(len(data).to_bytes(8,'big'));self.file.write(data)
    def replay(self,gz):
        import random
        self.file.flush();os.fsync(self.file.fileno());self._space(self.file.tell()+65*1024*1024)
        random.Random(0).shuffle(self.offsets)
        for offset in self.offsets:
            self.file.seek(offset);header=self.file.read(8)
            if len(header)!=8:raise RuntimeErrorClosed('spool offset truncated')
            size=int.from_bytes(header,'big');data=self.file.read(size)
            if len(data)!=size:raise RuntimeErrorClosed('spool record truncated')
            gz.write(data)
    def close(self):self.file.close()



def p5_health_flags(game):
    a,b=game['rc_a'],game['rc_b'];status=game.get('source_status',game.get('status'));decided=game['decided']
    malformed=type(a) is not int or type(b) is not int
    nonzero=any(type(v) is int and v!=0 for v in (a,b))
    return {'composite_health_failure':not (status=='ok' and not malformed and a==0 and b==0 and decided),'winner_marker_absent':not decided,'malformed_rc':malformed,'nonzero_rc':nonzero,'status_not_ok':status!='ok','status_ok_nonzero_process':status=='ok' and nonzero}

def verify_source_census(per_archive):
    p5_records=[r for r in per_archive if r['source']=='P5'];p6_records=[r for r in per_archive if r['source']=='P6']
    if any(r['raw_game_lines']!=r['game_count']+r['missing_log_tombstones'] for r in per_archive) or any(r['missing_log_tombstones'] for r in p6_records):raise RuntimeErrorClosed('per-archive raw versus joined census differs')
    expected_health={'composite_health_failure':13,'malformed_rc':9,'nonzero_rc':12,'status_not_ok':9,'status_ok_nonzero_process':4}
    if any(sum(r['p5_health_counts'].get(k,0) for r in p5_records)!=v for k,v in expected_health.items()):raise RuntimeErrorClosed('P5 source health census differs')
    if len(p5_records)!=264 or any(r['raw_game_lines']!=25 for r in p5_records) or sum(r['raw_game_lines'] for r in p5_records)!=6600 or sum(r['missing_log_tombstones'] for r in p5_records)!=3 or {r['run_id'] for r in p5_records if r['missing_log_tombstones']}!={'lab-n3-c015','lab-n3-c037','lab-n3-c043'} or any(r['missing_log_tombstones']!=1 for r in p5_records if r['missing_log_tombstones']) or sum(r['game_count'] for r in p5_records)!=6597 or sum(r['decision_count'] for r in p5_records)!=551493 or sum(r['game_count'] for r in p6_records)!=2000 or sum(r['decision_count'] for r in p6_records)!=172815:raise RuntimeErrorClosed('source census differs from pinned evidence')


def reconcile_exclusions(index,kept,hold,heldout,reserved_names,reserved_rosters,heldout_names,per_archive):
    """Account once per game; preserve independently overlapping reason flags."""
    from collections import Counter
    records={r['archive_sha256']:r for r in per_archive};kept=set(kept);flags_by_game={}
    if len(records)!=len(per_archive):raise RuntimeErrorClosed('duplicate archive reconciliation')
    totals=Counter();all_primary_games=Counter();all_primary_decisions=Counter();all_flags=Counter()
    for record in per_archive:record['_counts']=Counter();record['_primary_games']=Counter();record['_primary_decisions']=Counter();record['_flags']=Counter()
    for key,game in index['games'].items():
        record=records.get(game['archive_sha256']);n=game.get('decision_count')
        if record is None or type(n) is not int or n<0:raise RuntimeErrorClosed('game decision census malformed')
        flags=A.game_exclusion_flags(index,key,hold,heldout,reserved_names,reserved_rosters,heldout_names)
        primary=A.primary_exclusion(game,flags)
        if (primary is None)!=(key in kept):raise RuntimeErrorClosed('partition and primary exclusion differ')
        flags_by_game[key]={'primary':primary,'flags':flags}
        counters=record['_counts'];counters['source_games']+=1;counters['source_raw_decisions']+=n
        if primary is None:counters['admitted_games']+=1;counters['admitted_raw_decisions']+=n
        else:
            counters['excluded_games']+=1;counters['excluded_raw_decisions']+=n
            record['_primary_games'][primary]+=1;record['_primary_decisions'][primary]+=n
            all_primary_games[primary]+=1;all_primary_decisions[primary]+=n
        for reason,active in flags.items():
            if active:record['_flags'][reason]+=1;all_flags[reason]+=1
    for record in per_archive:
        c=record.pop('_counts');pg=record.pop('_primary_games');pd=record.pop('_primary_decisions');fl=record.pop('_flags')
        if c['source_games']!=record['game_count'] or c['source_raw_decisions']!=record['decision_count'] or c['source_games']!=c['admitted_games']+c['excluded_games'] or c['source_raw_decisions']!=c['admitted_raw_decisions']+c['excluded_raw_decisions']:raise RuntimeErrorClosed('per-archive exclusion arithmetic differs')
        fields=('source_games','admitted_games','excluded_games','source_raw_decisions','admitted_raw_decisions','excluded_raw_decisions')
        result={k:c[k] for k in fields};result.update({'primary_game_counts':dict(pg),'primary_decision_counts':dict(pd),'independent_flag_game_counts':dict(fl)})
        record['reconciliation']=result;record['game_exclusions']=dict(pg)
        for k in fields:totals[k]+=c[k]
    if totals['source_games']!=len(index['games']) or totals['admitted_games']!=len(kept) or totals['source_games']!=totals['admitted_games']+totals['excluded_games'] or totals['source_raw_decisions']!=totals['admitted_raw_decisions']+totals['excluded_raw_decisions']:raise RuntimeErrorClosed('total exclusion arithmetic differs')
    result={k:totals[k] for k in ('source_games','admitted_games','excluded_games','source_raw_decisions','admitted_raw_decisions','excluded_raw_decisions')}
    result['raw_archive_game_lines']=sum(r['raw_game_lines'] for r in per_archive);result['missing_log_tombstones']=sum(r['missing_log_tombstones'] for r in per_archive);result['tombstone_side_lines']=sum(r['tombstone_side_lines'] for r in per_archive)
    if result['raw_archive_game_lines']!=result['source_games']+result['missing_log_tombstones']:raise RuntimeErrorClosed('raw versus joined game census differs')
    result.update({'primary_game_counts':dict(all_primary_games),'primary_decision_counts':dict(all_primary_decisions),'independent_flag_game_counts':dict(all_flags)})
    return result,flags_by_game



def add_tombstone(tomb,tomb_ids,index,decisions,content):
    if tomb['uid'] in tomb_ids or tomb['uid'] in index['games'] or any(x in decisions for x in tomb['side_ids']) or len(set(tomb['side_content_hashes']))!=len(tomb['side_content_hashes']) or content.intersection(tomb['side_content_hashes']):raise RuntimeErrorClosed('tombstone source identity conflicts')
    tomb_ids.add(tomb['uid']);content.update(tomb['side_content_hashes'])

def verify_tombstone_holdout(tomb_ids,example_ids):
    for eid in example_ids:
        decision=eid.rpartition('/')[0];game_uid=decision.rsplit('/',2)[0]
        if game_uid in tomb_ids:raise RuntimeErrorClosed('frozen holdout references missing-log tombstone')

def derive_index(root,spec,spec_sha):
    """Only called after authorize; authenticate and derive the complete population."""
    p6,extractor,sanitizer=_load_runtime_modules(root)
    refs=spec['source_refs'];catalog=authenticated_catalog(root,spec,p6)
    teams,heldout,heldout_names,reserved_names,reserved_rosters=roster_metadata(*[_read_ref(root,refs[name]) for name in ('public_pool','sealed_metadata','team_split','validated_teams')])
    games=[];tombstones=[];tomb_ids=set();per_archive=[];content=set();decisions={};index={'games':{},'components':{},'token_owner':{}}
    for source in catalog:
        batch,extracted,hashes,tombs=verified_archive_batch(source,teams,p6,extractor,sanitizer)
        append_compact_batch(index,decisions,content,batch,extracted,hashes,teams,source)
        games.extend(index['games'][g['key']] for g in batch)
        for tomb in tombs:
            add_tombstone(tomb,tomb_ids,index,decisions,content)
            tombstones.append(tomb)
        if tomb_ids.intersection(index['games']):raise RuntimeErrorClosed('joined and tombstone UID conflict')
        health={name:sum(int(p5_health_flags(g)[name]) for g in batch+tombs) for name in ('composite_health_failure','winner_marker_absent','malformed_rc','nonzero_rc','status_not_ok','status_ok_nonzero_process')} if source['kind']=='P5' else {}
        per_archive.append({'p5_health_counts':health,'run_id':source['run_id'],'raw_game_lines':len(batch)+len(tombs),'missing_log_tombstones':len(tombs),'tombstone_side_lines':sum(len(t['side_ids']) for t in tombs),'tombstone_sha256':A.digest(tombs),'archive_sha256':source['sha256'],'source':source['kind'],'producer_sha256':sha(source['producer'].encode()),'chunk':source['chunk'],'seed':source['seed'],'game_count':len(batch),'decision_count':len(extracted),'decision_ids_sha256':A.digest([r['uid'] for r in extracted]),'decision_content_sha256':A.digest(hashes)})
        del batch,extracted,hashes,tombs
    verify_source_census(per_archive)
    if authenticated_catalog(root,spec,p6)!=catalog:raise RuntimeErrorClosed('source producer metadata changed during scan')
    frozen=spec['frozen_refs']
    fixed_sha='dcf05ed82eaf7b487df1741f27c091c581aa65aa1af6869fb1153bd44225c545'
    expected={'v1_val':'aed05c91f3140fbffb4b37b429baa4d456a8ba75e0da85888c4c0c3081cb384e','v1_eval':'7ea29968fe36c61bd7283f8f72b770a5eeb0074d4829f04cd2fa2550160abb10','fixed_v0':'3b0ba8fa2e2db60b9149d4ac03f1ae5bcbcb152745c4fa8fe1bf54fcc5e06276'}
    if any(frozen[k]['sha256']!=v for k,v in expected.items()):raise RuntimeErrorClosed('frozen file pin differs')
    identity={name:read_identity_file(_path(root,frozen[name]),expected[name]) for name in expected}
    selected=set(select_fixed400_ids([r['example_id'] for r in identity['fixed_v0'] if r['task']=='action'],fixed_sha))
    verify_tombstone_holdout(tomb_ids,[r['example_id'] for r in identity['v1_val']+identity['v1_eval']]+list(selected))
    hold=holdout_projection(index,decisions,identity['v1_val'],identity['v1_eval'],identity['fixed_v0'],fixed_sha)
    hold['tombstone_names']={name for t in tombstones for name in t['team_names']}
    hold['tombstone_rosters']={digest for t in tombstones for digest in t['roster_digests']}
    kept,excluded=A.partition_games(index,hold,heldout,reserved_names,reserved_rosters,heldout_names=heldout_names)
    reconciled,flag_by_game=reconcile_exclusions(index,kept,hold,heldout,reserved_names,reserved_rosters,heldout_names,per_archive)
    if reconciled['primary_game_counts']!=excluded:raise RuntimeErrorClosed('primary exclusion totals differ')
    receipt={'schema':'hardread.glimmer-v2.identity-index/v1','spec_sha256':spec_sha,'archive_records':per_archive,'game_count':len(games),'decision_count':sum(r['decision_count'] for r in per_archive),'admitted_game_count':len(kept),'excluded_game_counts':excluded,'exclusion_reconciliation':reconciled,'p5_health_counts':{key:sum(r['p5_health_counts'].get(key,0) for r in per_archive) for key in ('composite_health_failure','winner_marker_absent','malformed_rc','nonzero_rc','status_not_ok','status_ok_nonzero_process')},'raw_p5_game_lines':sum(r['raw_game_lines'] for r in per_archive if r['source']=='P5'),'missing_log_tombstones':len(tombstones),'tombstone_side_lines':sum(len(t['side_ids']) for t in tombstones),'tombstone_name_barrier_sha256':_hash_set(hold['tombstone_names']),'tombstone_roster_barrier_sha256':_hash_set(hold['tombstone_rosters']),'identity_game_components_sha256':_hash_set(index['components'].values()),'holdout_component_sha256':_hash_set(hold['components']),'holdout_roster_sha256':_hash_set(hold['rosters']),'holdout_example_id_sha256':_hash_set(hold['example_ids']),'holdout_decision_id_sha256':_hash_set(hold['decision_ids']),'holdout_counts':{k:len(hold[k]) for k in ('components','rosters','example_ids','decision_ids')},'heldout_roster_sha256':_hash_set(heldout),'heldout_team_name_sha256':_hash_set(heldout_names),'reserved_roster_sha256':_hash_set(reserved_rosters)}
    receipt['game_identities']=[{'archive_sha256':g['archive_sha256'],'line_index':g['line_index'],'line_sha256':g['line_sha256'],'source_log_sha256':g['log_sha256'],'uid_token_sha256':sha(('uid:'+g['uid']).encode()),'raw_replay_sha256':g['raw_replay_sha256'],'normalized_replay_sha256':g['normalized_replay_sha256'],'component_sha256':index['components'][g['key']],'roster_digests':g['roster_digests'],'team_name_sha256':[sha(x.encode()) for x in g['team_names']],'new_validation':A.is_new_val(g),'source_status':g['source_status'],'decided':g['decided'],'rc_a':g['rc_a'],'rc_b':g['rc_b'],'decision_count':g['decision_count'],'primary_exclusion':flag_by_game[g['key']]['primary'],'exclusion_flags':flag_by_game[g['key']]['flags']} for g in games]
    receipt['tombstones']=[{'archive_sha256':t['archive_sha256'],'line_index':t['line_index'],'line_sha256':t['line_sha256'],'uid_sha256':sha(t['uid'].encode()),'team_name_sha256':[sha(x.encode()) for x in t['team_names']],'roster_digests':t['roster_digests'],'replay_identity':'MISSING','side_line_count':len(t['side_ids']),'side_ids_sha256':A.digest(t['side_ids']),'side_content_sha256':A.digest(t['side_content_hashes']),'side_identity_hashes':[{'uid_sha256':sha(uid.encode()),'content_sha256':content} for uid,content in zip(t['side_ids'],t['side_content_hashes'])],'new_validation':A.is_new_val(t),'primary_exclusion':'p5_incomplete_game','health_flags':p5_health_flags(t),'status':t['status'],'decided':t['decided'],'rc_a':t['rc_a'],'rc_b':t['rc_b']} for t in tombstones]
    receipt['holdout_projection']=[]
    fixed_ids=set(select_fixed400_ids([r['example_id'] for r in identity['fixed_v0'] if r['task']=='action'],fixed_sha));v1_ids={r['example_id'] for r in identity['v1_val']}
    for eid in sorted(hold['example_ids']):
        decision=eid.rpartition('/')[0];key=decisions[decision];game=index['games'][key]
        receipt['holdout_projection'].append({'groups':(['v1'] if eid in v1_ids else [])+(['fixed400'] if eid in fixed_ids else []),'example_id_sha256':sha(eid.encode()),'decision_id_sha256':sha(decision.encode()),'component_sha256':index['components'][key],'archive_sha256':game['archive_sha256'],'roster_digests':game['roster_digests']})
    return receipt,{'index':index,'catalog':catalog,'teams':teams,'spec':spec,'kept':kept,'hold':hold,'heldout':heldout,'heldout_names':heldout_names,'reserved_names':reserved_names,'reserved_rosters':reserved_rosters,'frozen':frozen}


def _private_output(root,value):
    root=Path(root).resolve();p=Path(value)
    if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='scratch_ds':raise RuntimeErrorClosed('output must be under private scratch')
    parent=(root/p).parent.resolve()
    if not parent.is_relative_to(root/'scratch_ds') or not parent.is_dir() or (root/p).exists():raise RuntimeErrorClosed('output parent or destination differs')
    return root/p


def scan(root,spec_ref,card_ref,approval_ref,*,execute=False):
    spec=authorize(root,spec_ref,card_ref,approval_ref,'index',execute=execute)
    destination=_private_output(root,spec['index_receipt_path'])
    receipt,_=derive_index(root,spec,spec_ref['sha256'])
    authorize(root,spec_ref,card_ref,approval_ref,'index',execute=execute)
    data=A.canonical(receipt)+b'\n'
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
    return {'index_receipt_sha256':sha(data),'index_receipt_path':str(destination),'game_count':receipt['game_count']}


def build(root,spec_ref,card_ref,approval_ref,index_ref,index_review_ref,*,execute=False):
    import random
    import shutil
    import tempfile
    from collections import Counter,defaultdict
    spec=authorize(root,spec_ref,card_ref,approval_ref,'build',execute=execute)
    if not isinstance(index_review_ref,dict) or set(index_review_ref)!={'commit','path','sha256'}:raise RuntimeErrorClosed('independent index review missing')
    try:review=json.loads(_committed(root,index_review_ref['commit'],index_review_ref['path'],index_review_ref['sha256']))
    except (ValueError,UnicodeError) as e:raise RuntimeErrorClosed('index review malformed') from e
    if not isinstance(review,dict) or review.get('status')!='accepted' or review.get('index_sha256')!=index_ref.get('sha256') or review.get('spec_sha256')!=spec_ref['sha256'] or review.get('card_sha256')!=card_ref['sha256'] or not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip():raise RuntimeErrorClosed('independent index review differs')
    expected=_read_ref(root,index_ref);receipt,state=derive_index(root,spec,spec_ref['sha256'])
    if A.canonical(receipt)+b'\n'!=expected:raise RuntimeErrorClosed('index recomputation differs from reviewed receipt')
    authorize(root,spec_ref,card_ref,approval_ref,'build',execute=execute)
    destination=_private_output(root,spec['output_dir'])
    from glimmer_chat import GlimmerChat
    import build_sft as B
    for module,path in ((sys.modules['glimmer_chat'],'tools/sft/glimmer_chat.py'),(B,'tools/sft/build_sft.py'),(sys.modules['serialize'],'tools/sft/serialize.py')):
        if Path(module.__file__).resolve()!=(Path(root)/path).resolve():raise RuntimeErrorClosed('serializer module path differs')
    tokenizer_dir=Path(spec['tokenizer_dir']);tokenizer_dir=tokenizer_dir if tokenizer_dir.is_absolute() else Path(root)/tokenizer_dir
    gc=GlimmerChat(tokenizer_dir,verify=True)
    kept=set(state['kept']);seen_decisions=set();seen_order=set();seen_examples=set();seen_content=set()
    task_drops=Counter();row_drops=Counter();totals=Counter();per_source=defaultdict(Counter)
    totals['source_raw_decisions']=sum(r['decision_count'] for r in receipt['archive_records'])
    import extract as extractor
    temp=Path(tempfile.mkdtemp(prefix='.v2-build-',dir=destination.parent));os.chmod(temp,0o700)
    spool=None;record_count=0
    try:
        spool=RecordSpool(temp/'records.spool')
        for batch,rows in iter_verified_batches(root,state,receipt):
            batch_games={game['key']:game for game in batch}
            for row in rows:
                game_key=row['uid'].rsplit('/',2)[0]
                if game_key not in kept:continue
                game=batch_games[game_key]
                source=game['archive_sha256'];per_source[source]['admitted_raw_decisions']+=1;totals['admitted_raw_decisions']+=1
                try:legal,policy,_=extractor.search_targets(row.get('worlds') or [])
                except (ValueError,TypeError,KeyError,OverflowError,ZeroDivisionError,AttributeError,IndexError) as e:raise RuntimeErrorClosed('malformed admitted teacher search') from e
                if not policy:raise RuntimeErrorClosed('empty admitted teacher policy')
                row=dict(row,legal=legal,policy=policy)
                target,reason=A.teacher_row(row,game,seen_decisions,seen_order)
                if reason is not None:row_drops[reason]+=1;per_source[source]['row_drop_'+reason]+=1;continue
                totals['action_pre_tokenization']+=1;per_source[source]['action_pre_tokenization']+=1
                if B.h01('reads/'+row['uid'])<.1:totals['reads_pre_tokenization']+=1;per_source[source]['reads_pre_tokenization']+=1
                records,drops=A.serialize_tasks(row,game,target,gc,seen_examples,seen_content)
                for reason,n in drops.items():task_drops[reason]+=n;per_source[source]['task_drop_'+reason]+=n
                for record in records:
                    task=record['task'];totals[task+'_post_tokenization']+=1;per_source[source][task+'_post_tokenization']+=1
                    spool.append(record);record_count+=1
            del batch,rows,batch_games
        if totals['admitted_raw_decisions']!=receipt['exclusion_reconciliation']['admitted_raw_decisions'] or any(per_source[r['archive_sha256']]['admitted_raw_decisions']!=r['reconciliation']['admitted_raw_decisions'] for r in receipt['archive_records']):raise RuntimeErrorClosed('build admitted-decision reconciliation differs')
        if not record_count:raise RuntimeErrorClosed('empty admitted dataset')
        zero=A.verify_zero_overlap(state['index'],kept,state['hold'],state['heldout'],state['reserved_names'],state['reserved_rosters'],seen_decisions,seen_examples,heldout_names=state['heldout_names'])
        train=temp/'train.jsonl.gz'
        with open(train,'xb') as raw:
            with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as gz:spool.replay(gz)
            raw.flush();os.fsync(raw.fileno())
        spool.close();spool=None;(temp/'records.spool').unlink()
        for key,name in (('v1_val','val.jsonl.gz'),('v1_eval','eval.jsonl.gz')):
            source=_path(root,state['frozen'][key]);shutil.copyfile(source,temp/name)
            if file_sha(temp/name)!=state['frozen'][key]['sha256'] or file_sha(source)!=state['frozen'][key]['sha256']:raise RuntimeErrorClosed('frozen copy differs')
        check_ids=set();check_decisions=set();check_tasks=Counter();count=0
        with gzip.open(train,'rt',encoding='utf-8') as stream:
            for line in stream:
                rec=json.loads(line);eid=rec.get('example_id')
                if not isinstance(eid,str) or eid in check_ids or eid not in seen_examples or rec.get('task') not in ('action','reads') or not eid.endswith('/'+rec['task']):raise RuntimeErrorClosed('train readback identity differs')
                inputs=rec.get('input_ids');labels=rec.get('labels')
                if not isinstance(inputs,list) or not isinstance(labels,list) or len(inputs)!=len(labels) or len(inputs)>2048:raise RuntimeErrorClosed('train readback token shape differs')
                if rec['task']=='action':
                    pos=rec.get('choice_pos');probs=rec.get('soft_p');toks=rec.get('soft_tok')
                    if type(pos) is not int or not 0<=pos<len(inputs) or labels[pos]!=rec.get('hard_tok') or inputs[pos]!=rec.get('hard_tok') or not isinstance(probs,list) or not isinstance(toks,list) or len(probs)!=len(toks) or not probs or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in probs) or abs(sum(probs)-1)>1e-5:raise RuntimeErrorClosed('train readback target differs')
                check_ids.add(eid);check_decisions.add(eid.rpartition('/')[0]);check_tasks[rec['task']]+=1;count+=1
        if count!=record_count or check_ids!=seen_examples or any(check_tasks[t]!=totals[t+'_post_tokenization'] for t in ('action','reads')):raise RuntimeErrorClosed('train readback count differs')
        if A.verify_zero_overlap(state['index'],kept,state['hold'],state['heldout'],state['reserved_names'],state['reserved_rosters'],check_decisions,check_ids,heldout_names=state['heldout_names'])!=zero:raise RuntimeErrorClosed('readback overlap differs')
        hashes={name:{'sha256':file_sha(temp/name),'bytes':(temp/name).stat().st_size} for name in ('train.jsonl.gz','val.jsonl.gz','eval.jsonl.gz')}
        manifest={'schema':'hardread.glimmer-v2.train-manifest/v1','source_index_sha256':index_ref['sha256'],'spec_sha256':spec_ref['sha256'],'train_games':len(kept),'train_rosters':len({r for key in kept for r in state['index']['games'][key]['roster_digests']}),'counts':dict(totals),'per_source':{r['archive_sha256']:dict(per_source[r['archive_sha256']],source=r['source'],source_raw_decisions=r['decision_count'],admitted_raw_decisions=per_source[r['archive_sha256']]['admitted_raw_decisions'],source_games=r['game_count'],game_exclusions=r.get('game_exclusions',{}),reconciliation=r['reconciliation']) for r in receipt['archive_records']},'game_exclusions':receipt['excluded_game_counts'],'exclusion_reconciliation':receipt['exclusion_reconciliation'],'row_drops':dict(row_drops),'task_drops':dict(task_drops),'overlap_counts':zero,'files':hashes,'tokenizer_sha256':gc.tokenizer_sha256,'template_sha256':gc.template_sha256}
        (temp/'manifest.json').write_bytes(A.canonical(manifest)+b'\n')
        hashes['manifest.json']={'sha256':file_sha(temp/'manifest.json'),'bytes':(temp/'manifest.json').stat().st_size}
        (temp/'receipt.json').write_bytes(A.canonical({'schema':'hardread.glimmer-v2.output-receipt/v1','files':hashes,'index_sha256':index_ref['sha256']})+b'\n')
        for path in temp.iterdir():os.chmod(path,0o600)
        authorize(root,spec_ref,card_ref,approval_ref,'build',execute=execute)
        if destination.exists():raise RuntimeErrorClosed('output already exists')
        os.rename(temp,destination)
    except BaseException:
        if spool is not None:spool.close()
        shutil.rmtree(temp,ignore_errors=True);raise
    return {'output_path':str(destination),'receipt_sha256':file_sha(destination/'receipt.json'),'train_rows':count}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Reviewed, default-off Glimmer v2 source admission')
    parser.add_argument('operation',choices=('index','build'));parser.add_argument('--root',required=True)
    parser.add_argument('--spec-ref',required=True,help='JSON object with path and sha256')
    parser.add_argument('--card-ref',required=True,help='JSON object with commit, path and sha256')
    parser.add_argument('--approval-ref',required=True,help='JSON object with commit, path and sha256')
    parser.add_argument('--index-ref',help='reviewed index JSON object with path and sha256')
    parser.add_argument('--index-review-ref',help='committed review JSON object with commit, path and sha256')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    try:
        spec=json.loads(args.spec_ref);card=json.loads(args.card_ref);approval=json.loads(args.approval_ref)
        if args.operation=='index':
            if args.index_ref or args.index_review_ref:raise RuntimeErrorClosed('index arguments unexpected')
            result=scan(args.root,spec,card,approval,execute=args.execute)
        else:
            if not args.index_ref or not args.index_review_ref:raise RuntimeErrorClosed('index and independent review required')
            result=build(args.root,spec,card,approval,json.loads(args.index_ref),json.loads(args.index_review_ref),execute=args.execute)
        print(json.dumps(result,sort_keys=True))
    except (A.AdmissionError,ValueError,OSError,KeyError,TypeError) as e:
        parser.exit(2,'admission blocked: '+str(e)+'\n')

if __name__=='__main__':main()
