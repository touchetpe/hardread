"""Freeze source-bound C9 reviews in a separately authorised local review window.

No model client. Real execution requires a reviewed commit and the explicit switch.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import build_eval_manifest as M
import c9_farm_source as F

CRAWL={
 'games':'338d1255fd4055fff86cf7ef6275a0feba856b7113d28f117f3953ce893a8750',
 'sets':'e2ab9a263c38cdf88dddaebde98ac60a278012895a467bd9c115c2d2a047fa1e',
 'species_profiles':'0584a121307abe129dc72b803f0d7771366414b21cf56f6aa7ca98f7dd55a878'}
CALIBRATOR='d4a8b2580ca101bc6f55a8900cb716db59d04d78e0636c6843ed135d6b5642a9'

def write_new(path, data):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 os.fchmod(fd,0o600)
 with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
 return hashlib.sha256(data).hexdigest()


def read_jsonl(path):
 raw=Path(path).read_bytes();raw=gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw
 return [json.loads(line) for line in raw.splitlines() if line.strip()]

def construct(root, outrel, fp_python):
 root=Path(root).resolve(); out=(root/outrel).resolve(); evalroot=(root/'scratch_ds/evals').resolve()
 if not evalroot.is_relative_to(root) or out.parent != evalroot:
  raise M.ManifestError('output must be a new direct eval subdirectory')
 evalroot.mkdir(mode=0o700,parents=True,exist_ok=True)
 out.mkdir(mode=0o700,parents=False,exist_ok=False)
 def rel(p): return p.relative_to(root).as_posix()
 def save(name,b):
  path=out/name;return {'path':rel(path),'sha256':write_new(path,b)}
 # All source pins are checked before producing a single review.
 for role,path in M.EXPECTED_CRAWL_SOURCES.items(): F.checked(root,path,CRAWL[role])
 F.checked(root,'tools/review/calibrator.json',CALIBRATOR)
 F.checked(root,M.DRAFT_CACHE_PATH,M.DRAFT_CACHE_SHA)
 cal=json.loads((root/'tools/review/calibrator.json').read_bytes())
 if not isinstance(cal.get('absolute_human'),dict) or not cal['absolute_human']:
  raise M.ManifestError('active app calibrator missing')
 derived=F.output_bytes(root)
 farm_map=json.loads(derived[2])['games']
 refs={key:save(filename,data) for key,filename,data in zip(('games','decisions','source_map'),('farm-games.jsonl','farm-coverage.jsonl','farm-source-map.json'),derived)}
 identity={'pool':'hr_val',**refs}
 sources=[{'pool':'crawl','role':role,'path':path,'sha256':CRAWL[role]} for role,path in M.EXPECTED_CRAWL_SOURCES.items()]
 sources += [{'pool':'hr_val','role':role,**refs[role]} for role in ('games','decisions')]
 census={pool:M.enumerate_source_candidates(pool,sources,root=root) for pool in M.POOLS}
 # The prior implementation incorrectly required decision coverage. Its correction
 # may not alter this frozen eligible rank population beyond already excluded games.
 changed=[c for c in census['hr_val'] if c['exclusion_reason'] is None and c['decision_coverage']!=['p1','p2']]
 after=[c for c in census['hr_val'] if c['exclusion_reason'] is None]
 before=[c for c in after if c['decision_coverage']==['p1','p2']]
 rank_digest=lambda rows: M.digest_json(sorted(c['rank_hash'] for c in rows))
 post_counts=Counter(c['exclusion_reason'] or 'eligible' for c in census['hr_val'])
 pre_counts=post_counts.copy();pre_counts['eligible']-=len(changed);pre_counts['farm_seats_ineligible']+=len(changed)
 diff={'eligible_rank_changes':len(changed),'candidate_sides':len(census['hr_val']),
       'before_eligible_rank_sha256':rank_digest(before),'after_eligible_rank_sha256':rank_digest(after),
       'before_exclusion_counts':dict(sorted(pre_counts.items())),
       'after_exclusion_counts':dict(sorted(post_counts.items())),
       'source_map_sha256':refs['source_map']['sha256']}
 save('eligibility-diff.json',M.canonical_json(diff).encode()+b'\n')
 if changed: raise M.ManifestError('eligibility/rank drift requires Sol decision')
 if len(census['hr_val'])!=3160: raise M.ManifestError('farm side census is incomplete')
 code_commit=M.git_head_for('tools/review',root)
 fork_commit=M.git_repo_head(root/'foul-play')
 code_hashes={p:M.digest_file(root/p) for p in M.REVIEW_SOURCE_PATHS}
 active=f'tools/review/calibrator.json#absolute_human@{CALIBRATOR}'
 rows_by_pool={}; evidence={}; source_rows={}
 for pool in M.POOLS:
  source=next(s for s in sources if s['pool']==pool and s['role']=='games')
  source_rows[pool]={idx:row for idx,row,_hash in M._read_source_jsonl(source['path'],root=root)}
 from app import pipeline
 sys.path.insert(0,str(root/'tools/review'))
 import human_eval
 sets=defaultdict(list)
 for r in read_jsonl(root/M.EXPECTED_CRAWL_SOURCES['sets']): sets[(r.get('uid'),r.get('player'))].append(r)
 profiles=json.loads((root/M.EXPECTED_CRAWL_SOURCES['species_profiles']).read_bytes())
 for pool in M.POOLS:
  cache={}
  def review_fn(candidate):
   record=source_rows[pool][candidate['source_record_index']]; side=candidate['side']
   log=record.get('log'); log='\n'.join(log) if isinstance(log,list) else log
   rep={'log':log,'formatid':'gen9ou','private':True}
   tag=f'{pool}-{candidate["rank_hash"]}'; replay=out/(tag+'-replay.json'); teamfile=out/(tag+'-team.txt'); reviewfile=out/(tag+'-worker-review.json')
   try:
    if pool=='hr_val':
     team=F.export_bytes(root,candidate['team_exports'][side]).decode('utf-8')
     lines,ctx,_job=pipeline.prepare(rep,side,team_text=team)
    else:
     lines,ctx,job=pipeline.prepare(rep,side)
     team=human_eval.draft_team_smogon(sets[(candidate['uid'],side)],profiles,job['genders'])
   except Exception as exc:
    raise M.ManifestError('app context/team preparation failed; selection stopped') from exc
   write_new(teamfile,team.encode('utf-8'));write_new(replay,M.canonical_json({'log':'\n'.join(lines)}).encode()+b'\n')
   cmd=[str(fp_python),str(root/'tools/review/review.py'),str(replay),'--team',str(teamfile),'--side',side,'--out',str(reviewfile)]
   started=datetime.now(timezone.utc).isoformat(); result=None
   try:
    result=subprocess.run(cmd,cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    if result.returncode or not reviewfile.is_file(): raise RuntimeError('review process failed')
    review=json.loads(reviewfile.read_bytes())
    if not isinstance(review,dict): raise ValueError('review output is not an object')
   except Exception as exc:
    ended=datetime.now(timezone.utc).isoformat()
    source_map=(farm_map[candidate['source_record_index']] if pool=='hr_val' else None)
    error_class=re.sub(r'[^A-Za-z0-9_]', '', type(exc).__name__)[:80] or 'Exception'
    if result is not None and isinstance(result.stderr, bytes):
     evidence_bytes=result.stderr
     evidence_ref=save(tag+'-stderr.bin',evidence_bytes)
     evidence_kind='worker_stderr'
    else:
     evidence_bytes=f'{error_class}: worker failed before captured stderr was available\n'.encode('ascii')
     evidence_ref=save(tag+'-diagnostic.txt',evidence_bytes)
     evidence_kind='sanitized_diagnostic'
    evidence_ref.update(bytes=len(evidence_bytes),exit_code=result.returncode if result is not None else None,
                        kind=evidence_kind)
    failure={'pool':pool,'uid':candidate['uid'],'side':side,'rank_hash':candidate['rank_hash'],
             'exit_class':f'exit_{result.returncode}' if result is not None else error_class,
             'status':'candidate_exception_provenance_pending','started_utc':started,'ended_utc':ended,
             'source_path':candidate['source_path'],'source_sha256':candidate['source_sha256'],
             'source_record_index':candidate['source_record_index'],
             'source_record_sha256':candidate['source_record_sha256'],
             'raw_archive_sha256':source_map.get('archive_sha256') if source_map else None,
             'raw_game_line_sha256':source_map.get('games_line_sha256') if source_map else None,
             'raw_log_sha256':source_map.get('log_sha256') if source_map else None,
             'export_reference':candidate.get('team_exports',{}).get(side) if pool=='hr_val' else None,
             'team_sha256':M.digest_file(teamfile),'replay_sha256':M.digest_file(replay),
             'review_code_commit':code_commit,'review_source_sha256':code_hashes,
             'sanitized_exit_summary':f'{error_class}; exit code {result.returncode if result is not None else "unavailable"}',
             'failure_artifact':evidence_ref}
    save(tag+'-failure.json',M.canonical_json(failure).encode()+b'\n')
    raise M.ManifestError('review exception; selection stopped pending separately reviewed receipt') from None
   cache[(candidate['uid'],side)]=(ctx,replay,teamfile)
   return review
  def context_fn(candidate,review): return cache[(candidate['uid'],candidate['side'])][0]
  selection=M.select_candidates(census[pool],pool,review_fn,context_fn,
        farm_source_identity=identity if pool=='hr_val' else None,
        review_code_commit=code_commit,review_source_sha256=code_hashes,root=root)
  if len(selection['selected'])!=10: raise M.ManifestError('fewer than ten reviewed unique games')
  rows=[]
  for item in selection['selected']:
   tag=f'{pool}-{item["rank_hash"]}'; replay=cache[(item['uid'],item['side'])][1]; teamfile=cache[(item['uid'],item['side'])][2]
   row=M.build_artifact_row(item,source_path=item['source_path'],source_sha256=item['source_sha256'],
      replay_path=rel(replay),replay_sha256=M.digest_file(replay),review_path=rel(out/(tag+'-review.json')),
      ctx_path=rel(out/(tag+'-ctx.json')),offered_path=rel(out/(tag+'-offered.json')),
      review_code_commit=code_commit,active_calibrator=active,root=root)
   row['team_path']=rel(teamfile);row['team_sha256']=M.digest_file(teamfile)
   rows.append(row)
  rows_by_pool[pool]=rows;evidence[pool]=selection['attempt_evidence']
 manifest={'schema':'c9-selection-manifest-v1','sources':sources,'farm_source_identity':identity,
           'candidate_census':census,'rows':rows_by_pool['crawl']+rows_by_pool['hr_val'],
           'selection_evidence':evidence,'review_code_commit':code_commit,
           'review_source_sha256':code_hashes,'active_calibrator':active,'fork_commit':fork_commit}
 M.validate_manifest(manifest,root=root,require_evals_paths=True)
 mp=out/'selection_manifest.json';write_new(mp,M.canonical_json(manifest).encode()+b'\n')
 a1,a0=M._manifest_items(rel(mp),manifest)
 write_new(out/'a1_items.json',M.canonical_json(a1).encode()+b'\n')
 write_new(out/'a0_items.json',M.canonical_json(a0).encode()+b'\n')
 return rel(mp)

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--output',required=True,help='new directory under scratch_ds/evals')
 ap.add_argument('--execute-local-reviews',action='store_true')
 ap.add_argument('--foulplay-python',default=str(ROOT.parent/'environments/foulplay/bin/python'))
 args=ap.parse_args()
 if not args.execute_local_reviews: ap.error('review window and explicit --execute-local-reviews are required')
 print(construct(ROOT,args.output,Path(args.foulplay_python)))
if __name__=='__main__':main()
