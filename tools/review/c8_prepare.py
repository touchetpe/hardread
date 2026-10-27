"""Private E9 gamewise A2 source preparation. No fit is performed here."""
import argparse
import gzip
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next((p for p in HERE.parents if (p / 'tools/belief/roster_hash.py').exists()), HERE.parents[1])
sys.path.insert(0, str(ROOT / 'tools/belief'))
from roster_hash import canonical_roster_key, roster_digest  # noqa: E402
from targets import hr_val_roster_digests, reserved_roster_digests, excluded_rosters  # noqa: E402

COHORTS = ('lt1300', '1300-1499', '1500-1699', '1700-1899', '1900plus', 'top50')
SOURCE_FILES = {**{c:'run_b_'+c+'.jsonl' for c in COHORTS if c!='top50'}, 'top50':'run_b_top.jsonl'}
EXPECTED = {'lt1300': 200, '1300-1499': 200, '1500-1699': 200, '1700-1899': 200,
            '1900plus': 182, 'top50': 513}

class PrepareError(ValueError):
    pass


def sha_bytes(data): return hashlib.sha256(data).hexdigest()
def sha_file(path): return sha_bytes(Path(path).read_bytes())
def jsonbytes(obj): return (json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))+'\n').encode()


def read_checked(path, expected):
    raw = Path(path).read_bytes()
    if not isinstance(expected, str) or len(expected) != 64 or sha_bytes(raw) != expected:
        raise PrepareError('input hash mismatch')
    return raw


def jsonl(raw):
    try:
        if raw[:2] == b'\x1f\x8b': raw = gzip.decompress(raw)
        lines = raw.splitlines()
        if any(not line.strip() for line in lines):
            raise PrepareError('blank line in source JSONL')
        rows = [json.loads(line) for line in lines]
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        raise PrepareError('malformed source JSONL') from exc
    if any(not isinstance(r, dict) for r in rows): raise PrepareError('source row is not an object')
    return rows


def wilson(success, total):
    if not total: return None
    z=1.959963984540054; p=success/total; den=1+z*z/total
    center=(p+z*z/(2*total))/den; radius=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return [max(0,center-radius), min(1,center+radius)]


def membership_map(rows, memberships, expected_counts=EXPECTED):
    records={}
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('uid'),str) or not row['uid'] or row.get('side') not in ('p1','p2'):
            raise PrepareError('invalid merged record identity')
        key=(row['uid'],row['side'])
        if key in records: raise PrepareError('merged records must have unique game-side identities')
        if type(row.get('outcome')) is not int or row['outcome'] not in (0,1):
            raise PrepareError('record outcome is missing or invalid')
        if 'error' not in row and (not isinstance(row.get('luck'),list) or not isinstance(row.get('decisions'),list)
                or not isinstance(row.get('meta'),dict)):
            raise PrepareError('successful record lacks outcome, luck, decisions or metadata')
        records[key]=row
    labels={}
    if not isinstance(memberships,list): raise PrepareError('membership must be a list')
    for item in memberships:
        if not isinstance(item,dict): raise PrepareError('membership entry malformed')
        key=(item.get('uid'),item.get('side')); cohorts=item.get('cohorts')
        if key in labels or not isinstance(cohorts,list) or not cohorts or len(set(cohorts))!=len(cohorts) or any(c not in COHORTS for c in cohorts):
            raise PrepareError('invalid or duplicate membership')
        labels[key]=tuple(sorted(cohorts))
    if set(records)!=set(labels): raise PrepareError('membership and merged identities differ')
    uids={uid for uid,_side in records}
    for uid in uids:
        if (uid,'p1') not in records or (uid,'p2') not in records or labels[(uid,'p1')]!=labels[(uid,'p2')]:
            raise PrepareError('both sides and consistent cohort membership required')
        a,b=records[(uid,'p1')],records[(uid,'p2')]
        if 'error' not in a and 'error' not in b and a['outcome']+b['outcome']!=1:
            raise PrepareError('game sides have conflicting outcomes')
    counts={c:sum(c in labels[(uid,'p1')] for uid in uids) for c in COHORTS}
    if counts != expected_counts: raise PrepareError('cohort game membership counts differ from frozen selection')
    return records,labels,uids


def audit_attempts(records, labels, cohort_rows):
    """Reconstruct committed sorted-file first-success precedence exactly."""
    if not isinstance(cohort_rows,dict) or set(cohort_rows)!=set(COHORTS):
        raise PrepareError('all six original cohort files are required')
    all_attempts=defaultdict(list); counts={}; selected={}; differences=[]
    for cohort in sorted(COHORTS,key=lambda c:SOURCE_FILES[c]):
        attempts=cohort_rows[cohort]; seen=set(); errors=0
        for line_no,row in enumerate(attempts,1):
            key=(row.get('uid'),row.get('side')) if isinstance(row,dict) else (None,None)
            if key not in records or cohort not in labels[key]:
                raise PrepareError('cohort contains an unselected game-side')
            seen.add(key); all_attempts[key].append((cohort,line_no,row)); errors += 'error' in row
            if key not in selected or ('error' in selected[key][2] and 'error' not in row):
                selected[key]=(cohort,line_no,row)
        expected={key for key in labels if cohort in labels[key]}
        if seen!=expected: raise PrepareError('cohort is missing selected game-sides')
        counts[cohort]={'raw_attempts':len(attempts),'effective_sides':len(seen),
                        'duplicate_attempts':len(attempts)-len(seen),'error_attempts':errors}
    if list(records)!=list(selected):
        raise PrepareError('merged row order differs from sorted-file first-success merge')
    for key,attempts in all_attempts.items():
        outcomes=[row.get('outcome') for _,_,row in attempts]
        if any(type(o) is not int or o not in (0,1) for o in outcomes) or len(set(outcomes))>1:
            raise PrepareError('duplicate attempts have invalid or conflicting outcomes')
        cohort,line,chosen=selected[key]
        if records[key]!=chosen:
            raise PrepareError('merged row differs from sorted-file first-success precedence')
        successes=[(c,n,r) for c,n,r in attempts if 'error' not in r]
        if len(successes)>1:
            baseline=sha_bytes(jsonbytes({'luck':chosen.get('luck'),'decisions':chosen.get('decisions')}))
            for other_cohort,other_line,row in successes:
                other=sha_bytes(jsonbytes({'luck':row.get('luck'),'decisions':row.get('decisions')}))
                if other!=baseline:
                    differences.append({'uid':key[0],'side':key[1],
                        'chosen_file':SOURCE_FILES[cohort],'chosen_line':line,'chosen_payload_sha256':baseline,
                        'other_file':SOURCE_FILES[other_cohort],'other_line':other_line,'other_payload_sha256':other})
    return counts,differences


def reconstruct_membership(source_spec, *, root=ROOT, jobs_fn=None):
    """Re-run the predeclared selectors against the pinned top/broad source triples."""
    sources=source_spec.get('sources') if isinstance(source_spec,dict) else None
    if not isinstance(sources,dict) or set(sources)!={'top','broad'}:
        raise PrepareError('both pinned selection source triples required')
    for source in sources.values():
        if not isinstance(source,dict) or set(source)!={'games','sets','profiles'}:
            raise PrepareError('selection source triple malformed')
        for ref in source.values(): read_checked(ref['path'],ref['sha256'])
        games=Path(source['games']['path']).resolve()
        if (Path(source['sets']['path']).resolve()!=games.parent/'sets.jsonl.gz'
                or Path(source['profiles']['path']).resolve()!=games.parent/'species_profiles.json'):
            raise PrepareError('selector source triple is not the actual sibling inputs')
    cache=source_spec.get('full_sets') or {}
    expected_cache=(Path(root)/'foul-play/fp/data/pkmn_sets_cache/gen9ou/pokemon_full_sets.json').resolve()
    if Path(cache.get('path','')).resolve()!=expected_cache:
        raise PrepareError('selector full-set cache path differs from the actual runtime input')
    read_checked(cache['path'],cache['sha256'])
    pinned=source_spec.get('selector_code')
    required={'tools/review/human_eval.py','tools/human/reconstruct_check.py'}
    if not isinstance(pinned,dict) or set(pinned)!=required:
        raise PrepareError('selector code hash pins are incomplete')
    for path,h in pinned.items(): read_checked(Path(root)/path,h)
    if jobs_fn is None:
        import human_eval
        if human_eval._FULL is not None: raise PrepareError('selector cache already populated')
        jobs_fn=human_eval.human_jobs
    by_key=defaultdict(set); input_bindings={}; outcomes={}
    for cohort in COHORTS:
        source='top' if cohort=='top50' else 'broad'
        jobs=jobs_fn(n=513 if source=='top' else 200,seed=1 if source=='top' else 11,
                     worlds=4,iterations=20000,luck_worlds=4,luck_iterations=10000,
                     draft='smogon',games_path=sources[source]['games']['path'],
                     band=None if source=='top' else cohort)
        seen=set()
        for job in jobs:
            key=(job.get('uid'),job.get('side'))
            if not isinstance(key[0],str) or not key[0] or key[1] not in ('p1','p2') or key in seen:
                raise PrepareError('selector produced invalid or duplicate game-side')
            fields=('uid','side','log','team','outcome','worlds','iterations','luck_worlds','luck_iterations')
            if any(field not in job for field in fields) or type(job['outcome']) is not int or job['outcome'] not in (0,1):
                raise PrepareError('selector input or outcome identity is incomplete')
            binding=sha_bytes(jsonbytes({field:job[field] for field in fields}))
            if key in input_bindings and input_bindings[key]!=binding:
                raise PrepareError('overlapping selectors produced different source inputs')
            input_bindings[key]=binding;outcomes[key]=job['outcome']
            seen.add(key);by_key[key].add(cohort)
        if len(seen)!=2*EXPECTED[cohort]:
            raise PrepareError('selector count differs from predeclared cohort count')
    for uid in {key[0] for key in by_key}:
        if (uid,'p1') not in by_key or (uid,'p2') not in by_key or by_key[(uid,'p1')]!=by_key[(uid,'p2')]:
            raise PrepareError('selector game sides do not agree')
    # The files can change between their first hash and selector completion.
    for source in sources.values():
        for ref in source.values(): read_checked(ref['path'],ref['sha256'])
    read_checked(cache['path'],cache['sha256'])
    return by_key,input_bindings,outcomes


def verify_cohort_files(files):
    if len(files)!=len(COHORTS) or {x[0] for x in files}!=set(COHORTS):
        raise PrepareError('exactly one file for each of the six cohorts required')
    dirs=set()
    for name,path,_sha in files:
        p=Path(path).resolve(); dirs.add(p.parent)
        if p.name!=SOURCE_FILES[name]: raise PrepareError('cohort basename differs from merge source name')
    if len(dirs)!=1: raise PrepareError('cohort files must share one snapshot directory')
    parent=next(iter(dirs))
    if {p.name for p in parent.glob('run_b_*.jsonl')}!=set(SOURCE_FILES.values()):
        raise PrepareError('snapshot has missing or extra merge source files')


def verify_selector_outcomes(rows, selector_outcomes):
    for row in rows:
        key=(row.get('uid'),row.get('side'))
        if key not in selector_outcomes or type(row.get('outcome')) is not int or row['outcome']!=selector_outcomes[key]:
            raise PrepareError('attempt outcome differs from frozen selector input')


def roster_index(uid_set, set_sources):
    # Each source is a complete pinned set metadata archive. Compare duplicates across sources.
    index={}
    for rows in set_sources:
        grouped=defaultdict(list)
        for row in rows:
            uid,side=row.get('uid'),row.get('player')
            if uid in uid_set:
                if side not in ('p1','p2'): raise PrepareError('invalid roster side')
                grouped[(uid,side)].append(row.get('species_preview'))
        for key,species in grouped.items():
            try: canonical=canonical_roster_key(species)
            except (ValueError,TypeError) as exc: raise PrepareError('missing, malformed or duplicate species preview') from exc
            if key in index and index[key]!=canonical: raise PrepareError('contradictory cohort source rosters')
            index[key]=canonical
    if set(index)!={(uid,side) for uid in uid_set for side in ('p1','p2')}:
        raise PrepareError('missing side roster')
    return index


def prepare(rows,memberships,set_sources,hr_val,reserved,*,cohort_rows,expected_counts=EXPECTED):
    records,labels,uids=membership_map(rows,memberships,expected_counts)
    attempt_counts,duplicate_audit=audit_attempts(records,labels,cohort_rows)
    rosters=roster_index(uids,set_sources)
    excluded={}; match_counts={'hr_val':0,'reserved':0,'both':0}
    for uid in sorted(uids):
        hashes={roster_digest(rosters[(uid,side)]) for side in ('p1','p2')}
        hits=(bool(hashes & hr_val),bool(hashes & reserved))
        if any(hits):
            excluded[uid]=hits
            if hits[0]: match_counts['hr_val']+=1
            if hits[1]: match_counts['reserved']+=1
            if all(hits): match_counts['both']+=1
    clean=[r for r in rows if r['uid'] not in excluded]
    clean_labels=[m for m in memberships if m['uid'] not in excluded]
    expected_keys={key for key in records if key[0] not in excluded}
    if {(r['uid'],r['side']) for r in clean}!=expected_keys: raise PrepareError('A2 changed more than excluded game identities')
    groups={c:{uid for uid in uids if c in labels[(uid,'p1')]} for c in COHORTS}
    groups['unique_pooled']=uids
    groups['cohort_incidence']=None
    report={}
    for name,group in groups.items():
        if group is None: continue
        if name=='unique_pooled':
            successful_sides={key for key,row in records.items() if 'error' not in row}
        else:
            successful_sides={(row['uid'],row['side']) for row in cohort_rows[name] if 'error' not in row}
        success=sum((uid,'p1') in successful_sides and (uid,'p2') in successful_sides for uid in group)
        report[name]={'attempted_games':len(group),'both_side_success':success,'wilson_95':wilson(success,len(group)),
                      'success_ge_95pct': bool(group) and success/len(group)>=.95,
                      'excluded_games':sum(uid in excluded for uid in group)}
    attempts=sum(report[c]['attempted_games'] for c in COHORTS)
    success=sum(report[c]['both_side_success'] for c in COHORTS)
    report['cohort_incidence']={'attempted_games':attempts,'both_side_success':success,
                                'success_ge_95pct':bool(attempts) and success/attempts>=.95,
                                'interval':'not computed: cohort memberships can overlap'}
    uid_audit=[{'uid':uid,'cohorts':list(labels[(uid,'p1')]),'included':uid not in excluded,
                'hr_val_match':excluded.get(uid,(False,False))[0],
                'reserved_match':excluded.get(uid,(False,False))[1]} for uid in sorted(uids)]
    return clean,clean_labels,uid_audit,duplicate_audit,{'cohorts':report,'attempts':attempt_counts,'matches':match_counts,'excluded_games':len(excluded),
        'strict_190_high_band':'unmet: 182 selected',
        'high_band_fallback':{'attempted_games':len(groups['1900plus']),
            'both_side_success':report['1900plus']['both_side_success'],
            'at_least_100_attempted':len(groups['1900plus'])>=100,
            'at_least_100_both_side_success':report['1900plus']['both_side_success']>=100,
            'status':'fallback only; strict S1.22 unmet'}}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--merged',required=True); ap.add_argument('--merged-sha256',required=True)
    ap.add_argument('--membership',required=True); ap.add_argument('--membership-sha256',required=True)
    ap.add_argument('--selection-spec',required=True); ap.add_argument('--selection-spec-sha256',required=True)
    ap.add_argument('--cohort',action='append',nargs=3,metavar=('NAME','PATH','SHA256'),required=True)
    ap.add_argument('--sets',action='append',nargs=2,metavar=('PATH','SHA256'),required=True)
    ap.add_argument('--pool-manifest',required=True); ap.add_argument('--pool-sha256',required=True)
    ap.add_argument('--split-metadata',required=True); ap.add_argument('--split-sha256',required=True)
    ap.add_argument('--species-metadata',required=True); ap.add_argument('--species-sha256',required=True)
    ap.add_argument('--out-dir',required=True); a=ap.parse_args()
    merged=read_checked(a.merged,a.merged_sha256); members=read_checked(a.membership,a.membership_sha256)
    selection=json.loads(read_checked(a.selection_spec,a.selection_spec_sha256))
    selected,input_bindings,selector_outcomes=reconstruct_membership(selection)
    member_rows=json.loads(members)
    reconstructed={(m['uid'],m['side']):set(m['cohorts']) for m in member_rows}
    if len(reconstructed)!=len(member_rows) or reconstructed!=selected:
        raise PrepareError('membership is not the predeclared selector output')
    sets=[jsonl(read_checked(p,h)) for p,h in a.sets]
    expected_sets={(selection['sources'][source]['sets']['path'],selection['sources'][source]['sets']['sha256'])
                   for source in ('top','broad')}
    if set(map(tuple,a.sets))!=expected_sets or len(a.sets)!=len(expected_sets):
        raise PrepareError('roster metadata differs from the pinned selected sources')
    verify_cohort_files(a.cohort)
    original={name:jsonl(read_checked(path,sha)) for name,path,sha in a.cohort}
    for p,h in ((a.pool_manifest,a.pool_sha256),(a.split_metadata,a.split_sha256),(a.species_metadata,a.species_sha256)):
        read_checked(p,h)
    val=hr_val_roster_digests(a.pool_manifest); reserved=reserved_roster_digests(a.split_metadata,a.species_metadata)
    if excluded_rosters(a.pool_manifest,a.split_metadata,a.species_metadata) != val | reserved:
        raise PrepareError('A2 exclusion union is inconsistent')
    input_rows=jsonl(merged)
    verify_selector_outcomes(input_rows,selector_outcomes)
    for cohort_rows in original.values():
        verify_selector_outcomes(cohort_rows,selector_outcomes)
    clean,clean_labels,uid_audit,duplicate_audit,report=prepare(input_rows,json.loads(members),sets,val,reserved,cohort_rows=original)
    od=Path(a.out_dir).resolve()
    try: od.relative_to((ROOT/'scratch_ds/codex/c8').resolve())
    except ValueError as exc: raise PrepareError('output must remain under private C8 directory') from exc
    od.mkdir(parents=True,mode=0o700,exist_ok=False)
    data=b''.join(jsonbytes(r) for r in clean); membership=jsonbytes(clean_labels); uid_data=jsonbytes(uid_audit)
    duplicate_data=jsonbytes(duplicate_audit)
    selector_data=jsonbytes([{'uid':key[0],'side':key[1],'input_sha256':h} for key,h in sorted(input_bindings.items())])
    report.update({'merged_sha256':a.merged_sha256,'input_membership_sha256':a.membership_sha256,
                   'clean_sha256':sha_bytes(data),'clean_membership_sha256':sha_bytes(membership),
                   'uid_audit_sha256':sha_bytes(uid_data),'duplicate_audit_sha256':sha_bytes(duplicate_data),
                   'selector_inputs_sha256':sha_bytes(selector_data),
                   'selection_spec_sha256':a.selection_spec_sha256,
                   'sets_sources':[{'path':p,'sha256':h} for p,h in a.sets],
                   'cohort_sources':[{'cohort':name,'path':path,'sha256':h} for name,path,h in a.cohort],
                   'pool_sha256':a.pool_sha256,'split_sha256':a.split_sha256,'species_sha256':a.species_sha256})
    for name,content in (('clean.jsonl',data),('membership.json',membership),('uid-audit.json',uid_data),
                         ('duplicate-audit.json',duplicate_data),('selector-inputs.json',selector_data),
                         ('filter-report.json',jsonbytes(report))):
        fd=os.open(od/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f: f.write(content); f.flush(); os.fsync(f.fileno())
    print(json.dumps({'clean_sha256':report['clean_sha256'],'membership_sha256':report['clean_membership_sha256']}))

if __name__=='__main__': main()
