"""Prospective admission primitives; real source scans and builds are disabled.

Only synthetic in-memory fixtures may be passed to these primitives during source
review. No archive reader, tokenizer, model, or cloud client runs on import.
"""
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

DATA_EXECUTION_ENABLED=False
DOMAIN=b'hardread-v2-game-component-v1\0'
HEX=re.compile(r'^[0-9a-f]{64}$')

class AdmissionError(ValueError):pass

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def _sha(value):return isinstance(value,str) and HEX.fullmatch(value) is not None

def game_tokens(game):
    uid=game.get('uid');raw=game.get('raw_replay_sha256');normalized=game.get('normalized_replay_sha256')
    if not isinstance(uid,str) or not uid or not _sha(raw) or not _sha(normalized):raise AdmissionError('incomplete tagged game identity')
    return {'uid:'+uid,'raw_replay:'+raw,'normalized_replay:'+normalized}

def index_games(games,team_metadata,archive_metadata):
    """Build components only after the caller authenticates joins and metadata.

    This pure synthetic primitive is not an archive-admission or scanner entrypoint.
    """
    from roster_hash import roster_digest
    if not isinstance(games,list) or not games:raise AdmissionError('no games')
    tokens_by_key={};index={};owner={};uids={};components={}
    for g in games:
        if not isinstance(g,dict):raise AdmissionError('game malformed')
        key=g.get('key');archive=g.get('archive_sha256');meta=archive_metadata.get(archive)
        if not isinstance(key,str) or not key or key in index or not _sha(archive) or not isinstance(meta,dict):raise AdmissionError('source game or archive missing')
        if any(g.get(k)!=meta.get(k) for k in ('producer','chunk','seed')):raise AdmissionError('producer lineage differs')
        if type(g.get('line_index')) is not int or g['line_index']<0 or not _sha(g.get('line_sha256')) or not _sha(g.get('log_sha256')):raise AdmissionError('game provenance missing')
        if not isinstance(g.get('log'),list) or not g['log'] or not all(isinstance(x,str) for x in g['log']):raise AdmissionError('joined log missing')
        raw_log=g.get('source_log_bytes')
        if not isinstance(raw_log,bytes) or hashlib.sha256(raw_log).hexdigest()!=g['log_sha256']:raise AdmissionError('joined log hash differs')
        try:payload=json.loads(raw_log)
        except (ValueError,UnicodeDecodeError) as e:raise AdmissionError('joined log malformed') from e
        if not isinstance(payload,dict) or payload.get('log')!=g['log'] or [payload.get('p1'),payload.get('p2')]!=g.get('player_names'):raise AdmissionError('joined replay differs')
        if g.get('joined_uid')!=g.get('uid') or g.get('player_a') not in ('p1','p2'):raise AdmissionError('joined game or side mismatch')
        names=g.get('team_names');joined=g.get('joined_team_names');species=g.get('joined_rosters');rosters=g.get('roster_digests')
        if not all(isinstance(x,list) and len(x)==2 for x in (names,joined,species,rosters)) or names!=joined or len(set(names))!=2:raise AdmissionError('ambiguous team join')
        seat_a=0 if g['player_a']=='p1' else 1
        if g.get('source_player_a')!=g['player_names'][seat_a]:raise AdmissionError('source player assignment differs')
        seat_species=[]
        for seat in ('p1team','p2team'):
            team=payload.get(seat)
            if not isinstance(team,list) or any(not isinstance(x,dict) for x in team):raise AdmissionError('replay roster missing')
            seat_species.append([x.get('species') or x.get('name') for x in team])
        if species!=[seat_species[seat_a],seat_species[1-seat_a]]:raise AdmissionError('joined replay rosters differ')
        for name,roster,expected in zip(names,species,rosters):
            info=team_metadata.get(name)
            if not isinstance(name,str) or not name.startswith('gen9/hr_train/') or '..' in Path(name).parts or not isinstance(info,dict) or info.get('split')!='dev':raise AdmissionError('reserved or unknown team')
            try:actual=roster_digest(roster)
            except (TypeError,ValueError) as e:raise AdmissionError('invalid roster') from e
            if not _sha(expected) or actual!=expected or info.get('roster_digest')!=actual:raise AdmissionError('roster join mismatch')
        tok=game_tokens(g)
        if g['uid'] in uids:raise AdmissionError('duplicate game UID')
        uids[g['uid']]=key
        for t in tok:
            if t in owner:raise AdmissionError('conflicting or duplicate game identity')
            owner[t]=key
        tokens_by_key[key]=tok;index[key]=g
    # Unique source games have singleton components. Token matching to any holdout
    # is evaluated in the same tagged domain; roster values never join components.
    for key,tokens in tokens_by_key.items():
        components[key]=hashlib.sha256(DOMAIN+'\n'.join(sorted(tokens)).encode()).hexdigest()
    return {'games':index,'components':components,'token_owner':owner}

def is_new_val(game):
    uid=game['uid']
    if type(game.get('game')) is not int or game['game']<0 or not isinstance(game.get('run_id'),str):raise AdmissionError('run/game UID differs')
    expected=f"{game['run_id']}/g{game['game']:04d}"
    if uid!=expected:raise AdmissionError('run/game UID differs')
    return int(hashlib.sha256(uid.encode()).hexdigest()[:8],16)/2**32 < .02

def project_holdouts(index,examples,expected_counts,decision_to_game):
    """ID-only projections: references must resolve uniquely to indexed games."""
    if set(examples)!={'v1','fixed400'} or set(expected_counts)!={'v1','fixed400'}:raise AdmissionError('holdout membership missing')
    hold={'components':set(),'tokens':set(),'rosters':set(),'team_names':set(),'example_ids':set(),'decision_ids':set()}
    seen_ids=set()
    for group in ('v1','fixed400'):
        rows=examples[group]
        if not isinstance(rows,list) or len(rows)!=expected_counts[group] or not rows:raise AdmissionError('holdout count differs')
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'example_id','game_key','archive_sha256','roster_digests'}:raise AdmissionError('holdout projection not identity-only')
            eid=row['example_id'];key=row['game_key'];game=index['games'].get(key)
            if not isinstance(eid,str) or not eid or (group,eid) in seen_ids or game is None:raise AdmissionError('holdout example or game unresolved')
            seen_ids.add((group,eid))
            decision,sep,task=eid.rpartition('/')
            if not sep or task not in ('action','reads') or decision_to_game.get(decision)!=key or not decision.startswith(game['uid']+'/'):raise AdmissionError('holdout decision-to-game link missing')
            if row['archive_sha256']!=game['archive_sha256'] or row['roster_digests']!=game['roster_digests']:raise AdmissionError('holdout provenance differs')
            hold['example_ids'].add(eid);hold['decision_ids'].add(decision);hold['components'].add(index['components'][key]);hold['tokens'].update(game_tokens(game));hold['rosters'].update(game['roster_digests']);hold['team_names'].update(game['team_names'])
    for key,game in index['games'].items():
        if is_new_val(game):
            hold['components'].add(index['components'][key]);hold['tokens'].update(game_tokens(game));hold['rosters'].update(game['roster_digests']);hold['team_names'].update(game['team_names'])
            hold['decision_ids'].update(decision for decision,owner in decision_to_game.items() if owner==key)
    return hold

def game_exclusion_flags(index,key,hold,heldout_rosters,reserved_names,reserved_rosters,heldout_names=frozenset()):
    g=index['games'][key];names=set(g['team_names']);rosters=set(g['roster_digests'])
    kind=g.get('source_kind');a=g.get('rc_a');b=g.get('rc_b');bad=g.get('p5_nonzero_process_rc')
    if kind not in ('P5','P6') or type(a) is not int or type(b) is not int or type(bad) is not bool or bad!=(kind=='P5' and (a!=0 or b!=0)) or (kind=='P6' and (a!=0 or b!=0)):raise AdmissionError('source process RC identity differs')
    return {'p5_nonzero_process_rc':bad,
        'holdout_game':bool(index['components'][key] in hold['components'] or game_tokens(g)&hold['tokens']),
        'holdout_team_name':bool(names&(hold['team_names']|heldout_names)),
        'holdout_roster':bool(rosters&hold['rosters']),
        'reserved_team_name':bool(names&reserved_names),
        'excluded_roster':bool(rosters&(heldout_rosters|reserved_rosters))}

def partition_games(index,hold,heldout_rosters,reserved_names,reserved_rosters,*,heldout_names=frozenset(),expected_heldout=596):
    if not isinstance(heldout_names,(set,frozenset)) or any(not isinstance(x,set) for x in (heldout_rosters,reserved_names,reserved_rosters)) or not hold.get('components') or not hold.get('rosters') or not heldout_rosters or not reserved_names or not reserved_rosters:raise AdmissionError('exclusion pool missing')
    if len(heldout_rosters)!=expected_heldout or any(not _sha(x) for x in heldout_rosters|reserved_rosters):raise AdmissionError('held-out roster manifest differs')
    kept=[];counts=Counter()
    for key in index['games']:
        flags=game_exclusion_flags(index,key,hold,heldout_rosters,reserved_names,reserved_rosters,heldout_names)
        reason=next((name for name,active in flags.items() if active),None)
        if reason is None:kept.append(key)
        else:counts[reason]+=1
    return kept,dict(counts)

def decision_identity(row,game,seen_ids,seen_order):
    """Authenticate decision identity, side and roster without teacher eligibility."""
    if not isinstance(row,dict):raise AdmissionError('decision malformed')
    uid=row.get('uid');side=row.get('side');turn=row.get('turn');k=row.get('k');player=row.get('player')
    if side not in ('A','B') or player not in ('p1','p2') or player!=(game['player_a'] if side=='A' else ('p2' if game['player_a']=='p1' else 'p1')):raise AdmissionError('decision side mismatch')
    from roster_hash import roster_digest
    try:
        own=roster_digest([x.get('species') for x in row['own_team']]);opp=roster_digest([x.get('species') for x in row['opp_team']])
    except (KeyError,TypeError,ValueError,AttributeError) as e:raise AdmissionError('decision roster missing') from e
    expected=game['roster_digests'] if side=='A' else list(reversed(game['roster_digests']))
    if [own,opp]!=expected:raise AdmissionError('decision roster side mismatch')
    if row.get('run_id')!=game['run_id'] or row.get('game')!=game['game'] or not isinstance(uid,str) or not re.fullmatch(re.escape(game['uid'])+'/'+side+r'/\d{3,}',uid) or type(turn) is not int or turn<0 or type(k) is not int or k<0:raise AdmissionError('decision identity mismatch')
    order=(game['uid'],side,turn,k)
    if uid in seen_ids or order in seen_order:raise AdmissionError('duplicate decision identity or order')
    seen_ids.add(uid);seen_order.add(order)

def teacher_row(row,game,seen_ids,seen_order):
    """Apply teacher eligibility only after whole-game admission."""
    decision_identity(row,game,seen_ids,seen_order)
    cut=row.get('history_end');log=game['log']
    if type(cut) is not int or not 0<=cut<=len(log):raise AdmissionError('invalid history cut')
    if row.get('cfg')!='C1t1':return None,'cfg'
    legal=row.get('legal')
    if not isinstance(legal,list) or any(not isinstance(x,str) or not x for x in legal) or len(set(legal))!=len(legal):raise AdmissionError('malformed legal menu')
    if len(legal)<2:return None,'min_legal'
    if row.get('choice') not in legal:return None,'choice_not_legal'
    policy=row.get('policy')
    if not isinstance(policy,dict) or not policy or any(k not in legal for k in policy):raise AdmissionError('empty or unsupported policy')
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in policy.values()):raise AdmissionError('invalid policy mass')
    mass=sum(policy.values())
    if not math.isfinite(mass) or mass<=0:raise AdmissionError('nonpositive policy mass')
    return max(policy,key=policy.get),None

def serialize_tasks(row,game,target,gc,seen_examples,seen_content):
    """Reuse the frozen serializer and choice-token routine, with strict checks."""
    import serialize as S
    import build_sft as B
    legal=row['legal'];policy=row['policy'];results=[];drops=Counter()
    for task in ('action','reads'):
        if task=='reads' and B.h01('reads/'+row['uid'])>=.1:continue
        ex=S.build_example(row['view'],game['log'][:row['history_end']],legal,row['player'],row['turn'],row['k'],bool(row.get('team_preview')),task=task)
        mapping=ex.get('id_map')
        if task=='action':
            if not isinstance(mapping,dict) or len(mapping)!=len(legal) or set(mapping.values())!=set(legal):raise AdmissionError('menu mapping differs')
            inv={v:k for k,v in mapping.items()}
            if target not in inv:raise AdmissionError('target absent from menu')
            tgt=S.target_message(target,mapping)
        else:
            reads=S.reads_label(ex['species'],row.get('opp_team') or [])
            if not reads:drops['reads_no_label']+=1;continue
            tgt=S.target_message(None,{},task='reads',reads=reads)
        enc=gc.encode(ex['messages'],ex['tools'],tgt)
        if not isinstance(enc,dict) or not isinstance(enc.get('input_ids'),list) or not isinstance(enc.get('labels'),list) or len(enc['input_ids'])!=len(enc['labels']):raise AdmissionError('tokenization mismatch')
        if len(enc['input_ids'])>2048:drops['too_long_'+task]+=1;continue
        eid=row['uid']+'/'+task
        if eid in seen_examples:raise AdmissionError('duplicate example ID')
        rec={'example_id':eid,'task':task,'input_ids':enc['input_ids'],'labels':enc['labels']}
        if task=='action':
            try:info=B.choice_token_info(gc,enc,mapping,inv[target],{inv[a]:p for a,p in policy.items()})
            except (ValueError,KeyError,IndexError,StopIteration) as e:raise AdmissionError('choice token mapping failed') from e
            info.pop('multi_token_ids',None);pos=info.get('choice_pos')
            if type(pos) is not int or not 0<=pos<len(enc['labels']) or enc['labels'][pos]!=info.get('hard_tok') or enc['input_ids'][pos]!=info.get('hard_tok'):raise AdmissionError('choice token misaligned')
            probs=info.get('soft_p');toks=info.get('soft_tok')
            if not isinstance(probs,list) or not isinstance(toks,list) or len(probs)!=len(toks) or not probs or any(type(t) is not int for t in toks) or len(set(toks))!=len(toks) or info.get('hard_tok') not in toks or any(type(p) not in (int,float) or not math.isfinite(p) or p<0 for p in probs) or abs(sum(probs)-1)>1e-5:raise AdmissionError('soft target invalid')
            rec.update(info)
        content=digest({k:v for k,v in rec.items() if k!='example_id'})
        if content in seen_content:raise AdmissionError('duplicate example content')
        seen_examples.add(eid);seen_content.add(content);results.append(rec)
    return results,dict(drops)

def real_source_scan(*args,**kwargs):
    from data_admission_runtime import scan
    return scan(*args,**kwargs)

def real_dataset_build(*args,**kwargs):
    from data_admission_runtime import build
    return build(*args,**kwargs)


def verify_zero_overlap(index,kept,hold,heldout_rosters,reserved_names,reserved_rosters,decision_ids,example_ids,*,heldout_names=frozenset()):
    if not isinstance(decision_ids,set) or not isinstance(example_ids,set) or not hold.get('example_ids') or not hold.get('decision_ids'):raise AdmissionError('decision or holdout ID set missing')
    counts={'canonical_game':0,'team_name':0,'roster_digest':0,'decision_id':0,'example_id':0,'p5_nonzero_process_rc':0}
    for key in kept:
        game=index['games'][key]
        counts['p5_nonzero_process_rc']+=int(game.get('p5_nonzero_process_rc') is True)
        counts['canonical_game']+=int(index['components'][key] in hold['components'] or bool(game_tokens(game)&hold['tokens']))
        counts['team_name']+=int(bool(set(game['team_names'])&(hold['team_names']|heldout_names|reserved_names)))
        counts['roster_digest']+=int(bool(set(game['roster_digests'])&(hold['rosters']|heldout_rosters|reserved_rosters)))
    counts['decision_id']=len(decision_ids&hold['decision_ids'])
    counts['example_id']=len(example_ids&hold['example_ids'])
    if any(counts.values()):raise AdmissionError('nonzero overlap')
    return counts

def verify_frozen_bytes(val_bytes,eval_bytes,expected_val,expected_eval):
    if not isinstance(val_bytes,bytes) or not isinstance(eval_bytes,bytes) or not _sha(expected_val) or not _sha(expected_eval):raise AdmissionError('frozen file pin missing')
    if hashlib.sha256(val_bytes).hexdigest()!=expected_val or hashlib.sha256(eval_bytes).hexdigest()!=expected_eval:raise AdmissionError('frozen file bytes differ')
    return val_bytes,eval_bytes


def copy_frozen_synthetic(destination,val_bytes,eval_bytes,expected_val,expected_eval):
    """Fixture-only exact-copy exercise; not a real dataset builder."""
    import os
    val,ev=verify_frozen_bytes(val_bytes,eval_bytes,expected_val,expected_eval)
    directory=Path(destination)
    if not directory.is_dir() or directory.is_symlink():raise AdmissionError('fixture destination invalid')
    for name,data in (('val.jsonl.gz',val),('eval.jsonl.gz',ev)):
        fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    if (directory/'val.jsonl.gz').read_bytes()!=val or (directory/'eval.jsonl.gz').read_bytes()!=ev:raise AdmissionError('frozen byte copy differs')


def evaluate_synthetic_fixture(games,teams,archives,decisions,examples,expected_counts,heldout_rosters,reserved_names,reserved_rosters,encoder,*,expected_heldout=596):
    """Exercise the ordered checks on fixtures only; never an admission receipt."""
    index=index_games(games,teams,archives)
    if not isinstance(decisions,list):raise AdmissionError('fixture decisions missing')
    decision_to_game={};seen_ids=set();seen_order=set();checked=[];per_source={};row_drops=Counter()
    for key,row in decisions:
        game=index['games'].get(key)
        if game is None:raise AdmissionError('decision game unresolved')
        uid=row.get('uid');target,reason=teacher_row(row,game,seen_ids,seen_order)
        decision_to_game[uid]=key
        src=per_source.setdefault(game['archive_sha256'],{'raw_decisions':0,'eligible_decisions':0,'action_rows':0,'reads_rows':0})
        src['raw_decisions']+=1;checked.append((key,row,target,reason))
    hold=project_holdouts(index,examples,expected_counts,decision_to_game)
    kept,game_drops=partition_games(index,hold,heldout_rosters,reserved_names,reserved_rosters,expected_heldout=expected_heldout)
    selected=set(kept);records=[];seen_examples=set();seen_content=set();train_decisions=set();task_drops=Counter()
    for key,row,target,reason in checked:
        if key not in selected:continue
        if reason:row_drops[reason]+=1;continue
        game=index['games'][key];src=per_source[game['archive_sha256']];src['eligible_decisions']+=1
        train_decisions.add(row['uid']);encoded,drops=serialize_tasks(row,game,target,encoder,seen_examples,seen_content)
        task_drops.update(drops)
        for result in encoded:src[result['task']+'_rows']+=1;records.append(result)
    zero=verify_zero_overlap(index,kept,hold,heldout_rosters,reserved_names,reserved_rosters,train_decisions,seen_examples)
    return {'status':'synthetic_only_not_admission','game_exclusions':game_drops,'row_drops':dict(row_drops),'task_drops':dict(task_drops),
            'per_source':per_source,'train_game_count':len(kept),'train_roster_count':len({r for key in kept for r in index['games'][key]['roster_digests']}),
            'rows':records,'overlap_counts':zero}


def validate_metadata_inventory(p5_bytes,inventory_bytes,receipt_bytes,pins):
    """Validate metadata bytes only; this never opens archive payloads or rows."""
    if not isinstance(p5_bytes,bytes) or not isinstance(inventory_bytes,bytes) or not isinstance(receipt_bytes,dict) or not isinstance(pins,dict):raise AdmissionError('inventory inputs missing')
    if hashlib.sha256(p5_bytes).hexdigest()!=pins.get('p5_sha256') or hashlib.sha256(inventory_bytes).hexdigest()!=pins.get('inventory_sha256'):raise AdmissionError('inventory input hash differs')
    try:p5=json.loads(p5_bytes);inv=json.loads(inventory_bytes)
    except (ValueError,UnicodeDecodeError) as e:raise AdmissionError('inventory JSON malformed') from e
    old=p5.get('source_tarballs');candidates=inv.get('candidate_archives');quarantine=inv.get('quarantined_archive_metadata')
    if not isinstance(old,list) or len(old)!=pins.get('p5_count') or not isinstance(candidates,list) or len(candidates)!=pins.get('candidate_count') or not isinstance(quarantine,list) or len(quarantine)!=pins.get('quarantine_count'):raise AdmissionError('inventory count differs')
    old_names=set();old_hashes=set()
    for row in old:
        name=row.get('name');sha=row.get('sha256')
        if not isinstance(name,str) or not name or not _sha(sha) or name in old_names or sha in old_hashes:raise AdmissionError('old archive duplicate or malformed')
        old_names.add(name);old_hashes.add(sha)
    qnames={x.get('archive_name') for x in quarantine}
    if None in qnames or len(qnames)!=len(quarantine):raise AdmissionError('quarantine inventory malformed')
    seen=set();hashes=set();validated=[]
    if set(receipt_bytes)!={x.get('archive_name') for x in candidates}:raise AdmissionError('candidate receipt names differ')
    expected_receipts=pins.get('receipt_sha256')
    if not isinstance(expected_receipts,dict) or set(expected_receipts)!=set(receipt_bytes):raise AdmissionError('receipt pins missing')
    for row in candidates:
        name=row.get('archive_name');sha=row.get('archive_sha256');chunk=row.get('chunk_index');seed=row.get('seed');base=row.get('seed_base');producer=row.get('producer_job_name')
        if not isinstance(name,str) or not name or name in seen or name in qnames or not _sha(sha) or sha in hashes or type(chunk) is not int or type(seed) is not int or type(base) is not int or seed!=base+chunk or not isinstance(producer,str) or not producer:raise AdmissionError('candidate identity or quarantine differs')
        raw=receipt_bytes[name]
        if not isinstance(raw,bytes) or hashlib.sha256(raw).hexdigest()!=expected_receipts[name]:raise AdmissionError('candidate receipt hash differs')
        try:receipt=json.loads(raw)
        except (ValueError,UnicodeDecodeError) as e:raise AdmissionError('candidate receipt malformed') from e
        if receipt.get('archive_basename')!=name or receipt.get('job_name')!=producer or receipt.get('local_closed_file_sha256')!=sha or receipt.get('local_closed_file_bytes')!=row.get('archive_size_bytes'):raise AdmissionError('candidate receipt lineage differs')
        seen.add(name);hashes.add(sha);validated.append({'name':name,'sha256':sha,'producer':producer,'chunk':chunk,'seed':seed})
    return {'status':'metadata_only_not_admission','old_archive_count':len(old),'candidate_count':len(validated),'quarantine_count':len(quarantine),'candidate_inventory_digest':digest(sorted(validated,key=lambda x:x['name']))}
