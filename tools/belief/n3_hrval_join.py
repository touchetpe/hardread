"""Source-only join primitives for N3; no real-data entry or authorization.

Caller must independently authenticate the reviewed catalog, producer evidence,
source map and selected rows before invoking these functions. This module is not
an admission receipt or a replacement for that missing operational entry.
"""
import hashlib
import io
import json
from collections import defaultdict

class JoinError(ValueError): pass

def sha(b): return hashlib.sha256(b).hexdigest()

def join_one(archive, archive_sha, selections, read_archive, extractor, normalized_hash, *, expected_seed, expected_game_count=20):
    """Authenticate member bytes and join selected game/decision identities.

    selections is a mapping keyed by game UID with game-record index, line hash,
    replay hashes and exact chosen decision UID. The caller must bind this mapping
    to an independently accepted source map before reading any real archive.
    """
    run, members = read_archive(archive, archive_sha)
    if not selections or 'games.jsonl' not in members or 'summary.json' not in members: raise JoinError('games or summary missing')
    summary=json.loads(members['summary.json'])
    if not isinstance(summary,dict) or summary.get('run_id')!=run or type(summary.get('seed')) is not int or summary['seed']!=expected_seed or summary.get('team_pool')!='gen9/hr_val' or summary.get('cfg_a')!='C1t1' or summary.get('cfg_b')!='C1t1':raise JoinError('archive summary lineage differs')
    lines=members['games.jsonl'].splitlines(keepends=True)
    if len(lines)!=expected_game_count or len(selections)!=expected_game_count or any(not line.strip() for line in lines):raise JoinError('archive game population differs')
    if any(not isinstance(g,str) or not g.startswith(run+'/g') for g in selections):
        raise JoinError('game run mismatch')
    logs={}
    for name, raw in members.items():
        if name.startswith('battlelogs/') and name.endswith('.log.json'):
            d=json.loads(raw)
            p=(d.get('p1'),d.get('p2'))
            if not all(isinstance(x,str) and x for x in p) or p[0]==p[1] or p in logs or p[::-1] in logs:
                raise JoinError('ambiguous replay players')
            logs[p]=d
    safe_games=[]; chosen=set(); expected=set()
    for uid, spec in selections.items():
        index=spec['source_record_index']
        if type(index) is not int or not 0<=index<len(lines) or sha(lines[index])!=spec['source_record_sha256']:
            raise JoinError('source game line mismatch')
        game=json.loads(lines[index]); number=game.get('game')
        if game.get('cfg_a')!='C1t1' or game.get('cfg_b')!='C1t1':raise JoinError('game config differs')
        if type(number) is not int or uid!=f'{run}/g{number:04d}' or uid in chosen:
            raise JoinError('source game identity mismatch')
        chosen.add(uid)
        pair=(game.get('a'),game.get('b'))
        replay=logs.get(pair) or logs.get(pair[::-1])
        if not replay or not isinstance(replay.get('log'),list) or not all(isinstance(x,str) for x in replay['log']):
            raise JoinError('selected replay missing')
        text='\n'.join(replay['log'])
        if not text or sha(text.encode())!=spec['replay_sha256'] or normalized_hash(text,(replay['p1'],replay['p2']))!=spec['normalized_replay_sha256']:
            raise JoinError('selected replay identity mismatch')
        decision=spec['duid']
        if not isinstance(decision,str) or not decision.startswith(uid+'/') or decision in expected: raise JoinError('selected decision mismatch')
        expected.add(decision)
        safe=dict(game);safe['winner']=None;safe_games.append(safe)
    # Preserve decision ordering and choice for history cuts; remove worlds and
    # outcome fields before extractor sees them. Only selected games are passed.
    safe_members={k:v for k,v in members.items() if k.startswith('battlelogs/')}
    safe_members['games.jsonl']=b''.join(json.dumps(g).encode()+b'\n' for g in safe_games)
    for game in safe_games:
        for side in ('a','b'):
            name=f"g{game['game']:04d}_{side}.decisions.jsonl"
            if name in members:
                rows=[]
                for line in members[name].splitlines():
                    if line.strip():
                        row=json.loads(line);row['worlds']=[];rows.append(json.dumps(row).encode()+b'\n')
                safe_members[name]=b''.join(rows)
    loaders={k:(lambda x=v:x) for k,v in safe_members.items()}
    out=io.StringIO(); games_out=io.StringIO(); stats=defaultdict(int)
    extractor.extract_run(run,loaders,out,games_out,stats)
    decisions={}; games={}
    for line in games_out.getvalue().splitlines():
        g=json.loads(line);uid=g['uid']
        if uid in games: raise JoinError('duplicate game output')
        games[uid]=g
    for line in out.getvalue().splitlines():
        d=json.loads(line);uid=d['uid']
        if uid in expected:
            if uid in decisions: raise JoinError('duplicate decision output')
            decisions[uid]=d
    if set(games)!=chosen or set(decisions)!=expected: raise JoinError('incomplete source join')
    return decisions,games

MAP_SHA='400ae6ea6020c583ecc59f8c389423454a6c8d9952d8822e1d558509fd5e1b78'

def map_selections(source_map, archive, selected):
    """Check projection consistency, after the caller authenticates map bytes.

    The independently reviewed producer proof is still required separately.
    """
    if source_map.get('schema')!='hardread.n3.primary-selected-source-map/v2' or source_map.get('status')!='prospective_source_only_no_quality':
        raise JoinError('source map schema differs')
    entries=source_map.get('archives');games=source_map.get('games')
    if not isinstance(entries,list) or not isinstance(games,list) or len(entries)!=22 or len(games)!=440 or source_map.get('game_count')!=440 or source_map.get('selected_archive_count')!=22 or source_map.get('selected_row_count')!=1954:
        raise JoinError('source map cardinality differs')
    bypath={e['source_path']:e for e in entries}
    bygame={g['game_uid']:g for g in games}
    if len(bypath)!=22 or len(bygame)!=440:raise JoinError('duplicate source map identity')
    per_archive=defaultdict(list)
    for g in games:per_archive[g.get('source_path')].append(g.get('source_record_index'))
    if set(per_archive)!=set(bypath) or any(len(v)!=20 or set(v)!=set(range(20)) for v in per_archive.values()):raise JoinError('source archive game membership differs')
    grouped=defaultdict(list)
    for row in selected:grouped[row['game']].append(row)
    if set(grouped)!=set(bygame) or sum(map(len,grouped.values()))!=1954:raise JoinError('selected population mismatch')
    for uid,rows in grouped.items():
        g=bygame[uid];entry=bypath.get(g.get('source_path'))
        if not entry or entry.get('producer_pool')!='gen9/hr_val' or g.get('source_sha256')!=entry.get('source_sha256') or g.get('seed')!=entry.get('expected_seed') or g.get('run_id')!=uid.rsplit('/g',1)[0] or [r['uid'] for r in rows]!=g.get('example_uids') or any(r['duid']!=g.get('decision_uid') for r in rows):
            raise JoinError('selected projection differs')
    if archive not in bypath:raise JoinError('archive not selected')
    return {uid:{'source_record_index':g['source_record_index'], 'source_record_sha256':g['source_record_sha256'], 'replay_sha256':g['replay_sha256'], 'normalized_replay_sha256':g['normalized_replay_sha256'], 'duid':g['decision_uid']} for uid,g in bygame.items() if g['source_path']==archive}
