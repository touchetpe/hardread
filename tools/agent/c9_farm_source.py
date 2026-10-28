"""Source-bound hr_val adapter. Never opens sealed members or invokes a model."""
import gzip
import hashlib
import io
import json
import re
import tarfile
from collections import Counter, defaultdict
from pathlib import Path


AUDIT = ('scratch_ds/codex/training-roadmap/source-audit/source-audit.private.json',
         '6882cf46977bcefe71ae5bab36f0a1e3e37628814497ede4ac4e9dc8ac69f110')
READINESS = ('scratch_ds/codex/explanation-catchup-20261028/source-readiness-20261027T235740Z/source-readiness.json',
             'c698cdf824c37ec1567f1d5e62f6be9d247208d778ed3650eee379d7201fcafc')
POOL_MANIFEST = ('data/pools-v1.json', '9f3900fa06c4e91138f420d7328f0651c40aa52c0178fafd9b2b551a5c032843')
EXTRACTOR = ('tools/dataset/extract.py', '938cb32b6943a5364e47aafba37391fe05c6317602731a83b025455dc0fc8192')
POOL = 'azure/pools/teams_pools.tar.gz'

class SourceError(ValueError):
    pass

def sha(data): return hashlib.sha256(data).hexdigest()
def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()
def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode() + b'\n'
def checked(root, path, digest):
    p = (Path(root) / path).resolve()
    if not p.is_relative_to(Path(root).resolve()) or not p.is_file() or file_sha(p) != digest:
        raise SourceError('source file missing or changed: ' + str(path))
    return p

def lines(data):
    result = []
    for index, line in enumerate(data.splitlines(keepends=True)):
        if not line.strip(): raise SourceError('blank source row')
        try: value = json.loads(line)
        except ValueError as e: raise SourceError('invalid source row') from e
        if not isinstance(value, dict): raise SourceError('non-object source row')
        result.append((index, value, sha(line)))
    return result

def pool_members(root, manifest, pool_path=POOL):
    path = checked(root, pool_path, manifest['tarball_sha256'])
    members = {}; content = hashlib.sha256()
    with tarfile.open(path, 'r:gz') as tar:
        entries = [m for m in tar if m.isfile() and m.name.startswith('gen9/hr_val/')]
        for m in sorted(entries, key=lambda x: x.name):
            label = m.name.removeprefix('gen9/hr_val/')
            if not label or '/' in label or label in members: raise SourceError('invalid or duplicate val export')
            data = tar.extractfile(m).read()
            if not data: raise SourceError('empty export')
            content.update(label.encode() + b'\0' + data)
            members[label] = {'member': m.name, 'sha256': sha(data)}
    if len(members) != manifest['teams']['hr_val'] or content.hexdigest() != manifest['content_sha256']['hr_val']:
        raise SourceError('pool content mismatch')
    return members

def _pool_ref_for_raw_label(raw, pool):
    """Bind the producer's exact prefixed label to a verified tar member."""
    prefix = 'gen9/hr_val/'
    if type(raw) is not str or not raw.startswith(prefix):
        raise SourceError('invalid raw team label')
    bare = raw[len(prefix):]
    if not bare or bare in ('.', '..') or '/' in bare or '\\' in bare or '\0' in bare:
        raise SourceError('invalid raw team label')
    ref = pool.get(bare)
    if not isinstance(ref, dict) or ref.get('member') != raw:
        raise SourceError('team label outside pinned val pool')
    return ref

def code_hash(path):
    path=Path(path)
    if not path.is_dir(): raise SourceError('producer snapshot missing')
    h=hashlib.sha256(); count=0
    for item in sorted(path.rglob('*')):
        if item.is_symlink(): raise SourceError('producer snapshot symlink forbidden')
        if not item.is_file(): continue
        rel=item.relative_to(path).as_posix().encode()
        if rel==b'pools/teams_pools.tar.gz': continue
        h.update(len(rel).to_bytes(4,'big'));h.update(rel);h.update(bytes.fromhex(file_sha(item)));count+=1
    return h.hexdigest(),count

def _replay_pair(g, logs):
    pair = (g.get('a'), g.get('b'))
    if not all(isinstance(x, str) and x for x in pair) or pair[0] == pair[1]:
        raise SourceError('invalid players')
    direct, reverse = logs.get(pair), logs.get(pair[::-1])
    if direct and reverse: raise SourceError('ambiguous replay pair')
    return direct or reverse

def _uid(run, n):
    if not isinstance(run, str) or not run or type(n) is not int or n < 0: raise SourceError('invalid game id')
    return f'{run}/g{n:04d}'

def _derived(root, audit, readiness, pool_manifest, *, expected=(79, 1580, 124611, 1577, 2, 1), pool_path=POOL, extract_module=None):
    """Private core; all caller-provided control metadata must itself be pinned externally."""
    if extract_module is None: raise SourceError('pinned extractor required')
    group = [g for g in audit['source_catalog'] if g.get('role') == 'hr_val']
    if len(group) != 1: raise SourceError('hr_val catalog is not unique')
    entries = group[0]['archives']; prov = readiness['hr_val_provenance']
    narchives, ngames, ndecisions, nboth, npartial, nmissing = expected
    if len(entries) != narchives or len({e['source_path'] for e in entries}) != narchives:
        raise SourceError('missing or duplicate archive')
    actual = {p.relative_to(root).as_posix() for p in (Path(root) / 'scratch_ds/chunks/val').glob('val-*.tar.gz')}
    if actual != {e['source_path'] for e in entries}: raise SourceError('missing or extra archive')
    archives = {e['sha256']: e for e in prov['raw_archives']}
    if len(archives) != narchives: raise SourceError('readiness archive inventory incomplete')
    invg = {e['source_archive_sha256']: e for e in prov['game_file_inventory']}
    invd = {e['source_archive_sha256']: e for e in prov['decision_file_inventory']}
    if len(invg) != narchives or len(invd) != narchives: raise SourceError('extracted inventory incomplete')
    pool = pool_members(root, pool_manifest, pool_path)
    checked_jobs = set(); derived_games=[]; derived_coverage=[]; mapping=[]
    counts = Counter(); seen_games=set(); seen_decisions=0
    for entry in sorted(entries, key=lambda e: (e['producer']['job_name'], e['chunk_number'])):
        p = entry['producer']; arch_sha = entry['source_sha256']
        if (entry['source_role'] != 'hr_val' or p.get('pool') != 'gen9/hr_val' or entry.get('producer_match') is not True
                or p.get('side_swap') is not False or p.get('seed_rule') != 'seed_base + chunk_number'
                or p.get('job_name_matches_create') is not True or p.get('job_name') != p.get('job_create_name')):
            raise SourceError('unexpected producer or pool')
        if arch_sha not in invg or arch_sha not in invd or arch_sha not in archives: raise SourceError('extracted inventory mismatch')
        prior=archives[arch_sha]
        if (prior.get('path') != entry['source_path'] or prior.get('pool') != 'gen9/hr_val'
                or prior.get('chunk_number') != entry['chunk_number'] or prior.get('records') != entry['records']
                or prior.get('seed_base') != p.get('seed_base') or prior.get('code_tree_sha256') != p['code_tree_sha256']
                or prior.get('job_yaml_sha256') != p['yaml_sha256'] or prior.get('job_create_sha256') != p['job_create_sha256']
                or prior.get('producer_job_name_sha256') != sha(p['job_name'].encode())):
            raise SourceError('archive/producer differs from readiness proof')
        for item in (invg[arch_sha], invd[arch_sha]):
            if (item.get('source_pool') != 'gen9/hr_val' or item.get('source_code_tree_sha256') != p['code_tree_sha256']
                    or item.get('source_job_yaml_sha256') != p['yaml_sha256']
                    or item.get('source_archive_chunk_number') != entry['chunk_number']):
                raise SourceError('extracted file producer binding mismatch')
        jobkey = p['job_create_path']
        if jobkey not in checked_jobs:
            job = json.loads(checked(root, p['job_create_path'], p['job_create_sha256']).read_bytes())
            checked(root, p['yaml_path'], p['yaml_sha256'])
            command = job.get('command', '')
            if job.get('name') != p['job_name']: raise SourceError('job identity mismatch')
            if not re.search(r'(?:^|\s)--team-pool\s+gen9/hr_val(?:\s|$)', command): raise SourceError('job pool mismatch')
            code = (Path(root) / p['code_path']).resolve()
            if not code.is_relative_to(Path(root).resolve()) or code_hash(code) != (p['code_tree_sha256'], p['code_file_count']):
                raise SourceError('producer snapshot changed')
            checked(root, str(Path(p['code_path']) / 'pools/teams_pools.tar.gz'), pool_manifest['tarball_sha256'])
            checked_jobs.add(jobkey)
        archive = checked(root, entry['source_path'], arch_sha)
        game_file = checked(root, invg[arch_sha]['path'], invg[arch_sha]['sha256'])
        decision_file = checked(root, invd[arch_sha]['path'], invd[arch_sha]['sha256'])
        raw_members={}
        with tarfile.open(archive, 'r:gz') as tar:
            for m in tar:
                if not m.isfile() or Path(m.name).name.startswith('._'): continue
                if m.name in raw_members: raise SourceError('duplicate archive member')
                raw_members[m.name] = tar.extractfile(m).read()
        names = [x for x in raw_members if x.endswith('/games.jsonl')]
        if len(names) != 1: raise SourceError('missing or duplicate games member')
        games_member = names[0]; run = games_member[:-len('/games.jsonl')]
        if '/' in run or not run: raise SourceError('invalid run root')
        local = {k[len(run)+1:]: (lambda d=v: d) for k,v in raw_members.items() if k.startswith(run+'/')}
        if len(local) != len(raw_members): raise SourceError('unexpected archive run root')
        out=io.StringIO(); gout=io.StringIO(); stats=defaultdict(int)
        extract_module.extract_run(run, local, out, gout, stats)
        extracted_game_bytes = gzip.decompress(game_file.read_bytes())
        extracted_decision_bytes = gzip.decompress(decision_file.read_bytes())
        if gout.getvalue().encode() != extracted_game_bytes or out.getvalue().encode() != extracted_decision_bytes:
            raise SourceError('extracted source differs from raw producer bytes')
        extracted_games = {(r['run_id'], r['game']): (idx,r,h) for idx,r,h in lines(extracted_game_bytes)}
        if len(extracted_games) != stats['games']: raise SourceError('duplicate extracted game')
        decisions = defaultdict(list)
        for idx,d,h in lines(extracted_decision_bytes):
            key = (d.get('run_id'),d.get('game')); decisions[key].append((idx,d,h)); seen_decisions += 1
        logs={}
        for name,data in raw_members.items():
            if '/battlelogs/' in name and name.endswith('.log.json'):
                log=json.loads(data); pair=(log.get('p1'),log.get('p2'))
                if not all(isinstance(x,str) and x for x in pair) or pair[0]==pair[1] or pair in logs or pair[::-1] in logs: raise SourceError('duplicate or invalid log pair')
                logs[pair]=(name,log,sha(data))
        raw_games=lines(raw_members[games_member])
        if len(raw_games) != entry['records']: raise SourceError('archive game count changed')
        used_logs=set(); game_pairs=set()
        for raw_idx,g,raw_hash in raw_games:
            uid=_uid(run,g.get('game')); key=(run,g['game'])
            if key in seen_games: raise SourceError('duplicate game identity')
            seen_games.add(key)
            ref_a=_pool_ref_for_raw_label(g.get('team_a'),pool)
            ref_b=_pool_ref_for_raw_label(g.get('team_b'),pool)
            pair=(g.get('a'),g.get('b'))
            if pair in game_pairs or pair[::-1] in game_pairs: raise SourceError('duplicate game player pair')
            game_pairs.add(pair)
            replay=_replay_pair(g,logs); found=extracted_games.get(key)
            if replay: used_logs.add(replay[0])
            if bool(replay) != bool(found): raise SourceError('replay/extracted game mismatch')
            if replay:
                logname, log, loghash=replay; extidx,ex,exhash=found
                if any(ex.get(k) != g.get(k) for k in ('game','winner','status','team_a','team_b')): raise SourceError('game metadata mismatch')
                if ex['log'] != log.get('log') or ex['player_a'] != ('p1' if log['p1']==g['a'] else 'p2'):
                    raise SourceError('replay or seat mapping mismatch')
                a=ex['player_a']; b='p2' if a=='p1' else 'p1'; loglines=ex['log']
                if not isinstance(loglines,list) or not all(isinstance(s,str) for s in loglines): raise SourceError('bad replay')
                exports={a:ref_a, b:ref_b}
                winner={'A':a,'B':b}.get(g.get('winner'))
                row={'uid':uid,'run_id':run,'game':g['game'],'winner_player':winner,'log':loglines,'team_exports':exports}
            else:
                counts['missing_log']+=1; a=None; logname=None; loghash=None; extidx=None; exhash=None
                row={'uid':uid,'run_id':run,'game':g['game'],'winner_player':None,'winner_recorded':g.get('winner'),'log':None,'team_exports':None}
            coverage=Counter(); refs=[]; side_indices=Counter(); raw_decision_lines={}
            for idx,d,dh in decisions.pop(key,[]):
                if a is None: raise SourceError('decision without replay')
                side=d.get('side'); expected_player={'A':a,'B':'p2' if a=='p1' else 'p1'}.get(side)
                if expected_player is None or d.get('player') != expected_player: raise SourceError('decision mapping mismatch')
                j=side_indices[side]; side_indices[side]+=1
                rawname=f'{run}/g{g["game"]:04d}_{side.lower()}.decisions.jsonl'
                if rawname not in raw_decision_lines: raw_decision_lines[rawname] = lines(raw_members[rawname])
                rawlines=raw_decision_lines[rawname]
                if j >= len(rawlines): raise SourceError('decision raw index missing')
                rawline=rawlines[j]; coverage[expected_player]+=1
                refs.append({'extracted_line_index':idx,'extracted_line_sha256':dh,'raw_member':rawname,
                             'raw_line_index':rawline[0],'raw_line_sha256':rawline[2]})
            if len(coverage)==2: counts['both']+=1
            elif len(coverage)==1: counts['partial']+=1
            else: counts['zero']+=1
            c={'uid':uid,'sides':dict(sorted(coverage.items())),'row_count':sum(coverage.values())}
            derived_games.append(row); derived_coverage.append(c)
            mapping.append({'uid':uid,'derived_line_index':len(derived_games)-1,
                            'derived_game_line_sha256':sha(canonical(row)),
                            'derived_coverage_line_sha256':sha(canonical(c)),
                            'pool_tar_sha256':pool_manifest['tarball_sha256'], 'team_exports':row['team_exports'],
                            'archive_path':entry['source_path'],'archive_sha256':arch_sha,
                            'games_member':games_member,'games_line_index':raw_idx,'games_line_sha256':raw_hash,
                            'log_member':logname,'log_sha256':loghash,'extracted_games_path':invg[arch_sha]['path'],
                            'extracted_games_sha256':invg[arch_sha]['sha256'],'extracted_game_line_index':extidx,
                            'extracted_game_line_sha256':exhash,'extracted_decisions_path':invd[arch_sha]['path'],
                            'extracted_decisions_sha256':invd[arch_sha]['sha256'],'decision_lines':refs})
        if decisions: raise SourceError('decision refers to unknown game')
        if used_logs != {v[0] for v in logs.values()}: raise SourceError('unmatched battlelog')
        if file_sha(archive)!=arch_sha: raise SourceError('archive changed while reading')
    if (len(derived_games),seen_decisions,counts['both'],counts['partial'],counts['missing_log']) != (ngames,ndecisions,nboth,npartial,nmissing):
        raise SourceError('source census mismatch')
    return derived_games, derived_coverage, mapping, dict(counts)

def derive(root):
    root=Path(root).resolve()
    audit=json.loads(checked(root,*AUDIT).read_bytes()); readiness=json.loads(checked(root,*READINESS).read_bytes())
    manifest=json.loads(checked(root,*POOL_MANIFEST).read_bytes()); extractor_path=checked(root,*EXTRACTOR)
    import importlib.util
    spec=importlib.util.spec_from_file_location('c9_pinned_extract',extractor_path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    checked(root,*EXTRACTOR)
    return _derived(root,audit,readiness,manifest,extract_module=module)

def output_bytes(root):
    games, coverage, mapping, counts=derive(root)
    return (b''.join(map(canonical,games)), b''.join(map(canonical,coverage)),
            canonical({'schema':'c9-farm-source-map-v1','games':mapping,'counts':counts,
                       'audit_sha256':AUDIT[1],'readiness_sha256':READINESS[1],
                       'pool_manifest_sha256':POOL_MANIFEST[1]}))


def export_bytes(root, ref):
    """Read only a named pinned hr_val member and validate its bytes."""
    if (not isinstance(ref,dict) or not re.fullmatch(r'gen9/hr_val/[^/]+',ref.get('member',''))
            or not re.fullmatch(r'[0-9a-f]{64}',ref.get('sha256',''))):
        raise SourceError('invalid export reference')
    manifest=json.loads(checked(root,*POOL_MANIFEST).read_bytes())
    path=checked(root,POOL,manifest['tarball_sha256'])
    with tarfile.open(path,'r:gz') as tar:
        found=[m for m in tar if m.isfile() and m.name==ref['member']]
        if len(found)!=1: raise SourceError('missing or duplicate export')
        data=tar.extractfile(found[0]).read()
    if sha(data)!=ref['sha256']: raise SourceError('export bytes changed')
    return data
