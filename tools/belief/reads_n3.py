"""N3 reads preparation and scoring primitives. Real source entry stays disabled."""
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict

PREP_ENABLED=os.environ.get('HARDREAD_N3_PREP_ENABLED')=='1'
SCORE_ENABLED=os.environ.get('HARDREAD_N3_SCORE_ENABLED')=='1'
DOMAIN='glimmer-reads-n3/v1|'
ATTRS=('item','ability','ability_nt','tera','moves')

class N3Error(ValueError): pass

def canonical(x):
    return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def digest(x): return hashlib.sha256(canonical(x)).hexdigest()

def toid(value):
    from roster_hash import toid as normalize
    return normalize(value)

def select_examples(rows, *, expected_games=440):
    """Select one decision per game; preserve every selected row's original order."""
    if not isinstance(rows,list) or not rows: raise N3Error('empty population')
    game_decisions=defaultdict(set);uids=set();owners={}
    for e in rows:
        if not isinstance(e,dict): raise N3Error('malformed example')
        game=e.get('game');duid=e.get('duid');uid=e.get('uid');slot=e.get('slot')
        if any(not isinstance(x,str) or not x for x in (game,duid,uid)) or type(slot) is not int or slot<0 or uid!=f'{duid}/{slot}' or uid in uids:
            raise N3Error('duplicate or malformed example identity')
        if duid in owners and owners[duid]!=game: raise N3Error('decision belongs to multiple games')
        owners[duid]=game;uids.add(uid);game_decisions[game].add(duid)
    if len(game_decisions)!=expected_games: raise N3Error('game population differs')
    selected={g:min(ds,key=lambda d:(hashlib.sha256((DOMAIN+g+'|'+d).encode()).hexdigest(),d)) for g,ds in game_decisions.items()}
    result=[e for e in rows if selected[e['game']]==e['duid']]
    if {e['game'] for e in result}!=set(game_decisions): raise N3Error('selected game missing')
    return result,selected

def project_baselines(selected, rows):
    """Require each selected UID exactly once and in the same order as selected."""
    ids=[e['uid'] for e in selected];wanted=set(ids);found=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('uid'),str): raise N3Error('baseline row malformed')
        uid=row['uid']
        if uid not in wanted: continue
        if uid in seen: raise N3Error('duplicate baseline prediction')
        seen.add(uid);found.append(row)
    if [r['uid'] for r in found]!=ids: raise N3Error('baseline IDs or order differ')
    for row in found:
        for field in ('item','ability','tera','moves'):
            d=row.get(field)
            if not isinstance(d,dict): raise N3Error('baseline distribution missing')
            for key,value in d.items():
                if not isinstance(key,str) or not key or type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
                    raise N3Error('invalid baseline probability')
            # Historical belief output omits low-probability and reserved classes.
            # Six-decimal rounding and float32 softmax can slightly exceed one.
            # Preserve stored mass and probabilities exactly for benchmark scoring.
            if field!='moves' and math.fsum(d.values())>1 + len(d)*0.5e-6 + 1e-6:
                raise N3Error('categorical probability mass exceeds producer bound')
    return found

def _public_view(view):
    if not isinstance(view,dict): raise N3Error('source public view missing')
    # Keep only the extractor's public view fields; strip private keys recursively.
    def clean(x):
        if isinstance(x,dict): return {k:clean(v) for k,v in x.items() if k not in ('y','hidden','opp_team','worlds','played')}
        if isinstance(x,list): return [clean(v) for v in x]
        return x
    return {key:clean(view.get(key)) for key in ('user','opponent','weather','field','trick_room')}

def build_prompt_records(selected, decisions, games, serializer, chat):
    """Synthetic/verified join primitive; upstream archive authentication is required."""
    groups=defaultdict(list)
    for row in selected: groups[row['duid']].append(row)
    if set(decisions)!=set(groups): raise N3Error('decision join is missing or extra')
    prompts=[]
    for duid,rows in groups.items():
        r=decisions[duid]
        if not isinstance(r,dict) or r.get('uid')!=duid or type(r.get('log_version')) is not int or r['log_version']!=3:
            raise N3Error('source decision identity/version differs')
        game_key=rows[0]['game'];game=games.get(game_key)
        if not isinstance(game,dict) or game.get('uid')!=game_key or r.get('run_id')!=game.get('run_id') or r.get('game')!=game.get('game') or game_key!=f"{r['run_id']}/g{r['game']:04d}":
            raise N3Error('game join differs')
        if r.get('side') not in ('A','B') or game.get('player_a') not in ('p1','p2') or not re.fullmatch(re.escape(game_key)+'/'+str(r.get('side'))+r'/\d{3,}',duid) or type(r.get('turn')) is not int or r['turn']<0 or type(r.get('k')) is not int or r['k']<0:
            raise N3Error('source side or decision order differs')
        expected_player=game['player_a'] if r['side']=='A' else ('p2' if game['player_a']=='p1' else 'p1')
        if r.get('player')!=expected_player: raise N3Error('source player side differs')
        log=game.get('log');cut=r.get('history_end')
        if not isinstance(log,list) or any(not isinstance(x,str) for x in log) or type(cut) is not int or not 0<=cut<=len(log):
            raise N3Error('invalid public history cut')
        view=_public_view(r.get('view')); opp=view.get('opponent') or {}
        if not isinstance(opp,dict): raise N3Error('opponent view malformed')
        slots=([opp['active']] if opp.get('active') else [])+list(opp.get('reserve') or [])
        for e in rows:
            if any(e.get(k)!=r.get(k) for k in ('run_id','side','turn','k','cfg')) or e.get('game')!=game_key or bool(e.get('team_preview'))!=bool(r.get('team_preview')):
                raise N3Error('frozen row decision fields differ')
            slot=e['slot']
            if slot>=len(slots) or e.get('view')!=slots[slot] or e.get('species')!=toid(slots[slot].get('name')):
                raise N3Error('frozen row slot or public view differs')
        ex=serializer.build_example(view,log[:cut],[],r['player'],r['turn'],r['k'],bool(r.get('team_preview')),task='reads')
        names=ex.get('species')
        if not isinstance(names,list) or not names: raise N3Error('displayed species missing')
        keys=[toid(n) for n in names]
        if any(not x for x in keys) or len(set(keys))!=len(keys): raise N3Error('ambiguous displayed species')
        row_species={e['uid']:toid(serializer.species_name(e['view']['name'])) for e in rows}
        if any(v not in keys for v in row_species.values()) or len(set(row_species.values()))!=len(row_species): raise N3Error('species display join is ambiguous')
        prompt=chat.render(ex['messages'],ex['tools'],add_generation_prompt=True)
        ids=chat.ids(prompt)
        if not isinstance(prompt,str) or not isinstance(ids,list) or not ids or len(ids)>2048:
            raise N3Error('prompt invalid or too long; do not replace the selected decision')
        prompts.append({'game':game_key,'duid':duid,'row_uids':[e['uid'] for e in rows],
                        'row_species':row_species,'displayed_species':keys,'prompt':prompt,'tools':ex['tools'],'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()})
    labels=[{k:e[k] for k in ('uid','duid','game','species','view','hidden','y','scored','ymask') if k in e} for e in selected]
    return prompts,labels

def materialize_synthetic(rows, decisions, games, usage, belief, serializer, chat, destination, *, expected_games):
    """Fixture-only materialization: never accepts or certifies real source claims."""
    import os
    from pathlib import Path
    target=Path(destination)
    if target.exists() or not target.parent.is_dir() or target.parent.stat().st_mode&0o077:
        raise N3Error('fixture output parent invalid')
    selected,choices=select_examples(rows,expected_games=expected_games)
    projected_usage=project_baselines(selected,usage);projected_belief=project_baselines(selected,belief)
    selected_decisions={d:decisions[d] for d in set(choices.values()) if d in decisions}
    prompts,labels=build_prompt_records(selected,selected_decisions,games,serializer,chat)
    target.mkdir(mode=0o700)
    try:
        hashes={}
        for name,data in (('prompts.jsonl',prompts),('labels.jsonl',labels),('usage.jsonl',projected_usage),('belief.jsonl',projected_belief)):
            body=b''.join(canonical(row)+b'\n' for row in data)
            fd=os.open(target/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as out:out.write(body);out.flush();os.fsync(out.fileno())
            hashes[name]=hashlib.sha256(body).hexdigest()
        manifest={'schema':'hardread.reads-n3.synthetic-fixture/v1','games':len(choices),'decisions':len(prompts),
                  'selected':sorted([e['game'],e['duid'],e['uid']] for e in selected),'prompt_sha256':hashes['prompts.jsonl'],
                  'label_sha256':hashes['labels.jsonl'],'usage_sha256':hashes['usage.jsonl'],'belief_sha256':hashes['belief.jsonl']}
        fd=os.open(target/'manifest.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:out.write(canonical(manifest)+b'\n');out.flush();os.fsync(out.fileno())
        return manifest
    except BaseException:
        import shutil
        shutil.rmtree(target,ignore_errors=True);raise

def prepare_real(*args,**kwargs):
    if not PREP_ENABLED:raise N3Error('real N3 preparation disabled pending combined review and commit')
    import n3_prepare
    return n3_prepare.prepare(*args,**kwargs)


def strict_json(value):
    def object_pairs(pairs):
        result={}
        for key,val in pairs:
            if key in result: raise N3Error('duplicate JSON key')
            result[key]=val
        return result
    def bad(x): raise N3Error('nonfinite JSON')
    return json.loads(value,object_pairs_hook=object_pairs,parse_constant=bad)

def parse_reads(raw, tools, parse_tool_call, expected_species):
    """Parse the first tool frame only; duplicate keys/parameters fail closed."""
    import re
    p=parse_tool_call(raw,tools)
    if not p.get('ok') or p.get('name')!='submit_reads': return None,'malformed_tool'
    matches=re.findall(r'<atem:parameter name="([^"]*)">(.*?)</atem:parameter>',raw,re.DOTALL)
    if len(matches)!=1 or matches[0][0]!='reads' or raw.count('<atem:invoke name="submit_reads">')!=1:
        return None,'malformed_tool'
    try: parsed=strict_json(matches[0][1])
    except (ValueError,TypeError): return None,'malformed_tool'
    if not isinstance(parsed,dict) or (p.get('arguments') or {}).get('reads')!=parsed: return None,'malformed_tool'
    expected=set(expected_species);mapped={}
    for name,value in parsed.items():
        key=toid(name)
        if not key or key not in expected or key in mapped or not isinstance(value,dict): return None,'ambiguous_species'
        mapped[key]=value
    return mapped,None

def score_rows(labels, prompt_records, outputs, usage, belief, benchmark, parse_tool_call):
    """Use identical S-A masks; absent/malformed model predictions are wrong."""
    usage=project_baselines(labels,usage);belief=project_baselines(labels,belief)
    if len(labels)!=len(usage) or len(labels)!=len(belief): raise N3Error('baseline inventory differs')
    ids=[e['uid'] for e in labels]
    if len(set(ids))!=len(ids) or [p['uid'] for p in usage]!=ids or [p['uid'] for p in belief]!=ids:
        raise N3Error('baseline order or identity differs')
    prompts={p['duid']:p for p in prompt_records}
    if len(prompts)!=len(prompt_records): raise N3Error('duplicate prompt decision')
    byout={}
    for o in outputs:
        if not isinstance(o,dict) or o.get('duid') in byout: raise N3Error('duplicate output decision')
        byout[o.get('duid')]=o
    if set(byout)!=set(prompts): raise N3Error('output decision inventory differs')
    if len({o.get('selection_sha256') for o in outputs})!=1: raise N3Error('mixed model selection')
    decoded={};failures=Counter()
    for duid,prompt in prompts.items():
        output=byout[duid]
        if not isinstance(output.get('raw'),str) or output.get('prompt_sha256')!=prompt['prompt_sha256'] or type(output.get('generated_tokens')) is not int or not 0<=output['generated_tokens']<=512 or not isinstance(output.get('selection_sha256'),str): raise N3Error('first output or provenance differs')
        reads,error=parse_reads(output['raw'],prompt['tools'],parse_tool_call,prompt['displayed_species'])
        expected='ok' if error is None else error
        if output.get('parse_status')!=expected: raise N3Error('saved parse status differs')
        decoded[duid]=reads
        if error: failures[error]+=1
    scores=[]
    for e,up,bp in zip(labels,usage,belief):
        prompt=prompts.get(e['duid']);output=byout.get(e['duid'])
        if prompt is None or e['uid'] not in prompt['row_species']: raise N3Error('label prompt join missing')
        predictions=decoded[e['duid']]
        species=prompt['row_species'][e['uid']]; prediction=predictions.get(species) if isinstance(predictions,dict) else None
        if not isinstance(prediction,dict): prediction={};failures['missing_or_invalid_species']+=1
        required={'item','ability','tera','moves','spread'}
        missing=[field for field in ('item','ability','tera','spread') if not isinstance(prediction.get(field),str) or not prediction[field].strip()]
        moves=prediction.get('moves')
        normal=[toid(x) for x in moves] if isinstance(moves,list) and all(isinstance(x,str) and x.strip() for x in moves) else []
        valid_moves=len(normal)==4 and all(normal) and len(set(normal))==4
        normalized_fields=all(toid(prediction[field]) for field in ('item','ability','tera') if field not in missing)
        row_valid=set(prediction)==required and not missing and normalized_fields and valid_moves
        if not row_valid:failures['invalid_species_row']+=1
        cats,hidden_moves=benchmark.scored_attrs(e)
        cats=[x for x in cats if x[0] in ('item','ability','ability_nt','tera')]
        scores_row={'uid':e['uid'],'game':e['game'],'metrics':{}}
        for attr,base,true in cats:
            pred=prediction.get(base);pred_id=toid(pred) if isinstance(pred,str) and pred.strip() else None
            item={'model':float(row_valid and pred_id==true),'model_nll':None}
            for name,baseline in (('usage',up),('belief_v1',bp)):
                sc=benchmark.score_cat(baseline[base],true,1e-4)
                item[name]=sc['top1'];item[name+'_nll']=sc['nll'];item[name+'_true_in_support']=true in baseline[base]
            if pred_id is None:failures['missing_'+base]+=1
            scores_row['metrics'][attr]=item
        if hidden_moves:
            revealed={toid(x) for x in (e['view'].get('moves') or [])}
            hidden=set(hidden_moves)
            item={'model':float(row_valid and set(normal)-revealed==hidden),'model_nll':None}
            for name,baseline in (('usage',up),('belief_v1',bp)):
                item[name]=benchmark.score_moves(baseline['moves'],revealed,hidden_moves)['set'];item[name+'_nll']=None
            if not valid_moves:failures['invalid_moves']+=1
            scores_row['metrics']['moves']=item
        scores.append(scores_row)
    return scores,dict(failures)

def bootstrap(scores, *, iterations=2000, seed=0):
    """Paired resampling by game; retain unrounded rates and differences."""
    import numpy as np
    games=sorted({r['game'] for r in scores}); grouped=defaultdict(list)
    for row in scores:grouped[row['game']].append(row)
    if len(games)<2 or iterations!=2000 or seed!=0: raise N3Error('bootstrap contract differs')
    rng=np.random.default_rng(seed);indices=rng.integers(0,len(games),size=(iterations,len(games)))
    result={}
    for attr in ATTRS:
        counts=np.array([sum(attr in r['metrics'] for r in grouped[g]) for g in games],dtype=np.int64)
        sums={arm:np.array([sum(r['metrics'][attr][arm] for r in grouped[g] if attr in r['metrics']) for g in games],dtype=np.float64) for arm in ('model','usage','belief_v1')}
        n=int(counts.sum())
        if not n: result[attr]={'n':0};continue
        sampled=counts[indices].sum(axis=1);valid=sampled>0;entry={'n':n,'bootstrap_defined':int(valid.sum()),'arms':{}}
        for arm in sums:
            total=sums[arm][indices].sum(axis=1);rates=total[valid]/sampled[valid]
            entry['arms'][arm]={'correct':int(sums[arm].sum()),'rate':float(sums[arm].sum()/n),'ci':np.percentile(rates,[2.5,97.5]).tolist()}
        for ref in ('usage','belief_v1'):
            dif=(sums['model'][indices].sum(axis=1)-sums[ref][indices].sum(axis=1))[valid]/sampled[valid]
            entry['arms']['model']['diff_vs_'+ref]={'percentage_points':float(100*(sums['model'].sum()-sums[ref].sum())/n),'ci_percentage_points':(100*np.percentile(dif,[2.5,97.5])).tolist()}
        entry['nll']={'model':None}
        for arm in ('usage','belief_v1'):
            vals=[r['metrics'][attr][arm+'_nll'] for r in scores if attr in r['metrics']]
            entry['nll'][arm]=None if attr=='moves' else float(sum(vals)/len(vals))
        if attr!='moves':
            entry['baseline_log_score_definition']='mean -ln(max(stored_true_class_probability,1e-4)); omitted classes use the floor; no renormalization'
            entry['true_class_support']={}
            for arm in ('usage','belief_v1'):
                supported=sum(r['metrics'][attr][arm+'_true_in_support'] for r in scores if attr in r['metrics'])
                entry['true_class_support'][arm]={'count':supported,'denominator':n,'rate':supported/n}
        result[attr]=entry
    return result

FROZEN_SOURCES={
    'examples':'34baa58c45c4be4aead4f85f654abf5afe2a1390f644cf635acf321d0596d9b7',
    'usage':'2bd961e6edb2679e048ea185f22dca1ad16f858594fa5a06c4be166e451841ae',
    'belief_v1':'f33d6e9fa10c8db83c12ad96347272ea976fcc255ebb0d08d2dca72802ff2fb7',
    'benchmark':'463145f7965f740017a7c6d05e14a12658f76768bde9a53661b211ab37ff7558',
}

def file_sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def score_real(*,cohort_path,cohort_sha,review_path,review_sha,prompt_path,label_path,usage_path,belief_path,
               output_path,terminal_path,terminal_sha,destination,benchmark,parse_tool_call,source_root):
    """Offline scoring only after the cohort and completed model output are frozen."""
    if not SCORE_ENABLED:raise N3Error('N3 scoring disabled pending source and operational review')
    import os
    from pathlib import Path
    cohort_path=Path(cohort_path);review_path=Path(review_path);destination=Path(destination)
    if file_sha(cohort_path)!=cohort_sha or file_sha(review_path)!=review_sha:raise N3Error('cohort/review bytes differ')
    cohort=strict_json(cohort_path.read_bytes());review=strict_json(review_path.read_bytes())
    if review.get('status')!='accepted' or review.get('config_sha256')!=cohort.get('config_sha256') or review.get('source_map_sha256')!=cohort.get('source_map_sha256') or review.get('source_audit_sha256')!=cohort.get('source_audit_sha256') or review_sha!=cohort.get('source_review_sha256') or cohort.get('games')!=440 or cohort.get('decisions')!=440:
        raise N3Error('accepted frozen 440-game cohort required')
    if cohort.get('frozen_source_sha256')!=FROZEN_SOURCES or file_sha(benchmark.__file__)!=FROZEN_SOURCES['benchmark']:
        raise N3Error('frozen source or benchmark differs')
    source_root=Path(source_root).resolve();source_pins=cohort.get('source_code_sha256')
    required={'tools/belief/reads_n3.py','tools/belief/benchmark.py','tools/belief/targets.py',
              'tools/belief/roster_hash.py','tools/sft/serialize.py','tools/sft/glimmer_chat.py',
              'tools/sft/dex.json','foul-play/fp/data/pokedex.json'}
    if not isinstance(source_pins,dict) or not required.issubset(source_pins):raise N3Error('scoring source closure missing')
    for name,expected in source_pins.items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or not isinstance(expected,str) or len(expected)!=64:raise N3Error('source pin malformed')
        current=source_root
        for part in path.parts:
            current=current/part
            if current.is_symlink():raise N3Error('scoring source symlink')
        if not current.is_file() or file_sha(current)!=expected:raise N3Error('scoring source bytes differ')
    if Path(__file__).resolve()!=(source_root/'tools/belief/reads_n3.py').resolve() or Path(benchmark.__file__).resolve()!=(source_root/'tools/belief/benchmark.py').resolve():raise N3Error('loaded scorer source differs')
    import sys
    parse_module=sys.modules.get(parse_tool_call.__module__)
    if parse_module is None or Path(parse_module.__file__).resolve()!=(source_root/'tools/sft/glimmer_chat.py').resolve():raise N3Error('loaded parser source differs')
    for mod,rel in (('targets','tools/belief/targets.py'),('roster_hash','tools/belief/roster_hash.py')):
        loaded=sys.modules.get(mod)
        if loaded is None or Path(loaded.__file__).resolve()!=(source_root/rel).resolve():raise N3Error('loaded scoring dependency differs')
    for path,key in ((prompt_path,'prompt_sha256'),(label_path,'label_sha256'),(usage_path,'usage_sha256'),(belief_path,'belief_sha256')):
        if file_sha(path)!=cohort.get(key):raise N3Error('cohort artifact differs')
    if file_sha(terminal_path)!=terminal_sha:raise N3Error('terminal receipt bytes differ')
    terminal=strict_json(Path(terminal_path).read_bytes())
    if terminal.get('arm') not in ('xl6','selected_v2') or terminal.get('status')!='complete' or terminal.get('completed')!=440 or terminal.get('cohort_manifest_sha256')!=cohort_sha or terminal.get('prompt_sha256')!=cohort['prompt_sha256'] or terminal.get('outputs_sha256')!=file_sha(output_path):
        raise N3Error('model terminal proof differs')
    def rows(path):
        with open(path,encoding='utf-8') as f:return [strict_json(line) for line in f]
    prompts=rows(prompt_path);labels=rows(label_path);usage=rows(usage_path);belief=rows(belief_path);outputs=rows(output_path)
    if len(prompts)!=440 or len({x['game'] for x in labels})!=440 or len({x['duid'] for x in labels})!=440:
        raise N3Error('cohort count differs')
    actual=sorted([e['game'],e['duid'],e['uid']] for e in labels)
    if actual!=cohort.get('selected') or len({tuple(x) for x in actual})!=len(actual):raise N3Error('selected row membership differs')
    if any(o.get('selection_sha256')!=terminal.get('selection_sha256') or o.get('config_sha256')!=terminal.get('config_sha256') for o in outputs):raise N3Error('mixed model provenance')
    scores,failures=score_rows(labels,prompts,outputs,usage,belief,benchmark,parse_tool_call)
    metrics=bootstrap(scores)
    result={'schema':'hardread.reads-n3.exploratory-result/v1','arm':terminal.get('arm'),'games':440,
            'decisions':440,'pokemon_rows':len(labels),'cohort_manifest_sha256':cohort_sha,
            'model_selection_sha256':terminal['selection_sha256'],'output_sha256':terminal['outputs_sha256'],
            'parse_and_field_failures':failures,'metrics':metrics,'bootstrap_games':2000,'bootstrap_seed':0,
            'model_categorical_nll':None,'moves_nll':None}
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir() or destination.parent.stat().st_mode&0o077:
        raise N3Error('score output must be new and private')
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(canonical(result)+b'\n');f.flush();os.fsync(f.fileno())
    return {'result_sha256':file_sha(destination),'games':440}


def main(argv=None):
    import argparse
    import sys
    from pathlib import Path
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('cohort','cohort-sha','review','review-sha','prompts','labels','usage','belief','outputs','terminal','terminal-sha','out','source-root'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--execute',action='store_true');a=p.parse_args(argv)
    if not SCORE_ENABLED or not a.execute:raise SystemExit('N3 scoring disabled pending source and result review')
    sys.path[:0]=[str(Path(a.source_root)/'tools/belief'),str(Path(a.source_root)/'tools/sft')]
    import benchmark
    from glimmer_chat import parse_tool_call
    result=score_real(cohort_path=a.cohort,cohort_sha=a.cohort_sha,review_path=a.review,review_sha=a.review_sha,
        prompt_path=a.prompts,label_path=a.labels,usage_path=a.usage,belief_path=a.belief,output_path=a.outputs,
        terminal_path=a.terminal,terminal_sha=a.terminal_sha,destination=a.out,benchmark=benchmark,
        parse_tool_call=parse_tool_call,source_root=a.source_root)
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
