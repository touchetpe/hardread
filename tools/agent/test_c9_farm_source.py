"""Synthetic farm lineage and source-map tests; no real inputs or model."""
import gzip, hashlib, io, json, sys, tarfile, tempfile, unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=next(p for p in HERE.parents if (p/'tools/dataset/extract.py').is_file())
sys.path[:0]=[str(HERE),str(ROOT)]
import c9_farm_source as farm
import importlib.util
spec=importlib.util.spec_from_file_location('synthetic_extract', ROOT/'tools/dataset/extract.py')
extract=importlib.util.module_from_spec(spec);spec.loader.exec_module(extract)
from c9_farm_source import code_hash

def digest(b): return hashlib.sha256(b).hexdigest()
def put(root, rel, b):
 p=root/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b); return digest(b)
def tarbytes(members):
 f=io.BytesIO()
 with tarfile.open(fileobj=f,mode='w:gz') as tar:
  for name,data in members.items():
   info=tarfile.TarInfo(name); info.size=len(data); tar.addfile(info,io.BytesIO(data))
 return f.getvalue()
def jsonline(obj): return (json.dumps(obj)+'\n').encode()

def fixture(root, count=79, bad_label=False, duplicate=False):
 pooldata=tarbytes({'gen9/hr_val/a':b'A', 'gen9/hr_val/b':b'B'})
 poolsha=put(root,'azure/pools/teams_pools.tar.gz',pooldata)
 h=hashlib.sha256(); h.update(b'a\0A');h.update(b'b\0B')
 manifest={'teams':{'hr_val':2},'content_sha256':{'hr_val':h.hexdigest()},'tarball_sha256':poolsha}
 code='azure/attempts/job/code'; put(root,code+'/script',b'code'); put(root,code+'/pools/teams_pools.tar.gz',pooldata)
 tree,nfiles=code_hash(root/code)
 job='azure/attempts/job/job-create.json'; yml='azure/attempts/job/job.yml'
 jobsha=put(root,job,json.dumps({'name':'hrval-test','command':'run --team-pool gen9/hr_val --log-decisions'}).encode()); ysha=put(root,yml,b'job')
 producer={'seed_base':710000,'job_name':'hrval-test','job_create_name':'hrval-test','job_name_matches_create':True,'side_swap':False,'seed_rule':'seed_base + chunk_number','pool':'gen9/hr_val','job_create_path':job,'job_create_sha256':jobsha,'yaml_path':yml,'yaml_sha256':ysha,'code_path':code,'code_tree_sha256':tree,'code_file_count':nfiles}
 entries=[]; gs=[]; ds=[]; archives=[]
 for chunk in range(count):
  run=f'val-n1-c{chunk:03d}'; members={}; raw=[]
  for n in range(20):
   i=chunk*20+n; a=f'a{i}'; b=f'b{i}'
   game={'game':n,'a':a,'b':b,'winner':None if i==2 else 'A','status':'finished','team_a':'hr_train/a' if bad_label and i==1 else 'a','team_b':'b'}
   if duplicate and i==1: game['game']=0
   raw.append(jsonline(game))
   if i != 0:
    log={'p1':b if i==1 else a,'p2':a if i==1 else b,'log':['|turn|1','|turn|9' if i==1 else '|turn|10'],'p1team':[],'p2team':[]}
    members[f'{run}/battlelogs/{n}.log.json']=json.dumps(log).encode()
    for side in ('a','b'):
     if i in (1,2) and side=='b': continue
     members[f'{run}/g{n:04d}_{side}.decisions.jsonl']=jsonline({'turn':1,'worlds':[]})
  members[f'{run}/games.jsonl']=b''.join(raw)
  archive=f'scratch_ds/chunks/val/{run}.tar.gz'; ash=put(root,archive,tarbytes(members))
  local={k[len(run)+1:]:(lambda d=v:d) for k,v in members.items()};o=io.StringIO();g=io.StringIO()
  extract.extract_run(run,local,o,g,__import__('collections').defaultdict(int))
  gp=f'scratch_ds/val/{run}.games.jsonl.gz';dp=f'scratch_ds/val/{run}.jsonl.gz'
  gh=put(root,gp,gzip.compress(g.getvalue().encode()));dh=put(root,dp,gzip.compress(o.getvalue().encode()))
  entries.append({'source_path':archive,'source_sha256':ash,'source_role':'hr_val','producer_match':True,'producer':producer,'chunk_number':chunk,'records':20})
  archives.append({'sha256':ash,'path':archive,'pool':'gen9/hr_val','chunk_number':chunk,'records':20,'seed_base':710000,'code_tree_sha256':tree,'job_yaml_sha256':ysha,'job_create_sha256':jobsha,'producer_job_name_sha256':digest(b'hrval-test')})
  meta={'source_archive_sha256':ash,'source_pool':'gen9/hr_val','source_code_tree_sha256':tree,'source_job_yaml_sha256':ysha,'source_archive_chunk_number':chunk}; gs.append(dict(meta,path=gp,sha256=gh));ds.append(dict(meta,path=dp,sha256=dh))
 audit={'source_catalog':[{'role':'hr_val','archives':entries}]}
 ready={'hr_val_provenance':{'game_file_inventory':gs,'decision_file_inventory':ds,'raw_archives':archives}}
 return audit,ready,manifest

class FarmTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name);cls.audit,cls.ready,cls.pool=fixture(cls.root)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def build(self):return farm._derived(self.root,self.audit,self.ready,self.pool,expected=(79,1580,3156,1577,2,1),extract_module=extract)
 def test_census_and_order(self):
  games,cov,mapping,counts=self.build();self.assertEqual((len(games),len(cov),len(mapping)),(1580,1580,1580))
  self.assertEqual(counts,{'missing_log':1,'zero':1,'partial':2,'both':1577})
  self.assertEqual(games[1]['winner_player'],'p2');self.assertEqual(cov[1]['sides'],{'p2':1})
  self.audit['source_catalog'][0]['archives'].reverse()
  try:
   shuffled=self.build();self.assertEqual(shuffled,(games,cov,mapping,counts))
   rank=lambda rows:sorted(hashlib.sha256(('a1/'+r['uid']+'/'+side).encode()).hexdigest() for r in rows for side in ('p1','p2'))
   self.assertEqual(rank(shuffled[0]),rank(games))
  finally:self.audit['source_catalog'][0]['archives'].reverse()
 def test_changed_input_refused(self):
  path=self.root/self.audit['source_catalog'][0]['archives'][0]['source_path'];orig=path.read_bytes()
  try:
   path.write_bytes(orig+b'x')
   with self.assertRaises(farm.SourceError):self.build()
  finally:path.write_bytes(orig)
 def test_wrong_pool(self):
  producer=self.audit['source_catalog'][0]['archives'][0]['producer'];old=producer['pool'];producer['pool']='gen9/hr_train'
  try:
   with self.assertRaises(farm.SourceError):self.build()
  finally:producer['pool']=old


class CandidateTest(unittest.TestCase):
 def test_decision_coverage_is_diagnostic(self):
  import build_eval_manifest as M
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);ref=lambda c:{'member':'gen9/hr_val/'+c,'sha256':hashlib.sha256(c.encode()).hexdigest()}
   records=[];coverage=[]
   for i in range(6):
    record={'uid':f'g{i}','winner_player':'p1','log':['|turn|10'],'team_exports':{'p1':ref('a'),'p2':ref('b')}}
    if i==2:record['team_exports']=None
    if i==3:record['winner_player']=None
    if i==4:record['log']=['|turn|9']
    if i==5:record.update(winner_player=None,winner_recorded='A',log=None,team_exports=None)
    records.append(record);coverage.append({'uid':f'g{i}','sides':({'p1':1} if i==0 else {})})
   def source(role,items):
    path=f'{role}.jsonl';raw=b''.join(farm.canonical(x) for x in items);(root/path).write_bytes(raw)
    return {'path':path,'sha256':digest(raw),'role':role,'pool':'hr_val'}
   census=M.enumerate_source_candidates('hr_val',[source('games',records),source('decisions',coverage)],root=root)
   grouped={f'g{i}':[c for c in census if c['uid']==f'g{i}'] for i in range(6)}
   for i in (0,1): self.assertEqual([c['exclusion_reason'] for c in grouped[f'g{i}']],[None,None])
   self.assertEqual([c['decision_coverage'] for c in grouped['g0']],[['p1'],['p1']])
   self.assertEqual([c['decision_coverage'] for c in grouped['g1']],[[],[]])
   for i,reason in ((2,'farm_seats_ineligible'),(3,'undecided'),(4,'short'),(5,'turn_count_unavailable')):
    self.assertEqual([c['exclusion_reason'] for c in grouped[f'g{i}']],[reason,reason])
 def test_mutable_verified_flag_and_changed_derived_refused(self):
  import build_eval_manifest as M
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   with self.assertRaises(M.SourceUnresolvedError):M._require_farm_identity({'pool':'hr_val','verified':True},root=root)
   data=(b'games',b'coverage',b'map');identity={'pool':'hr_val'}
   for key,b in zip(('games','decisions','source_map'),data):
    path=root/'scratch_ds/evals'/key;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b);identity[key]={'path':str(path.relative_to(root)),'sha256':digest(b)}
   with patch.object(farm,'output_bytes',return_value=data):
    M._require_farm_identity(identity,root=root)
    (root/'scratch_ds/evals/games').write_bytes(b'changed')
    with self.assertRaises(M.SourceUnresolvedError):M._require_farm_identity(identity,root=root)


class MutationTest(unittest.TestCase):
 def test_changed_lineage_inputs_fail_closed(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);audit,ready,pool=fixture(root)
   def build(): return farm._derived(root,audit,ready,pool,expected=(79,1580,3156,1577,2,1),extract_module=extract)
   entry=audit['source_catalog'][0]['archives'][0];p=entry['producer']
   paths=[entry['source_path'],ready['hr_val_provenance']['game_file_inventory'][0]['path'],
          ready['hr_val_provenance']['decision_file_inventory'][0]['path'],p['job_create_path'],p['yaml_path'],
          p['code_path']+'/script',p['code_path']+'/pools/teams_pools.tar.gz',farm.POOL]
   for name in paths:
    path=root/name;old=path.read_bytes()
    try:
     path.write_bytes(old+b'corruption')
     with self.subTest(name=name),self.assertRaises(farm.SourceError):build()
    finally:path.write_bytes(old)
   extra=root/'scratch_ds/chunks/val/val-extra.tar.gz';extra.write_bytes(b'x')
   try:
    with self.assertRaises(farm.SourceError):build()
   finally:extra.unlink()
   missing=root/entry['source_path'];oldbytes=missing.read_bytes();missing.unlink()
   try:
    with self.assertRaises(farm.SourceError):build()
   finally:missing.write_bytes(oldbytes)
   job=root/p['job_create_path'];oldbytes=job.read_bytes();oldsha=p['job_create_sha256']
   try:
    changed=json.loads(oldbytes);changed['command']='run --team-pool gen9/hr_train'
    payload=json.dumps(changed).encode();job.write_bytes(payload);p['job_create_sha256']=digest(payload)
    for a in ready['hr_val_provenance']['raw_archives']:a['job_create_sha256']=digest(payload)
    with self.assertRaises(farm.SourceError):build()
   finally:
    job.write_bytes(oldbytes);p['job_create_sha256']=oldsha
    for a in ready['hr_val_provenance']['raw_archives']:a['job_create_sha256']=oldsha
   dec=ready['hr_val_provenance']['decision_file_inventory'][0];dp=root/dec['path'];oldbytes=dp.read_bytes();oldsha=dec['sha256']
   try:
    rows=gzip.decompress(oldbytes).splitlines();value=json.loads(rows[0]);value['player']='p1' if value['player']=='p2' else 'p2'
    rows[0]=json.dumps(value).encode();payload=gzip.compress(b'\n'.join(rows)+b'\n');dp.write_bytes(payload);dec['sha256']=digest(payload)
    with self.assertRaises(farm.SourceError):build()
   finally:dp.write_bytes(oldbytes);dec['sha256']=oldsha
   old=pool['content_sha256']['hr_val'];pool['content_sha256']['hr_val']='0'*64
   try:
    with self.assertRaises(farm.SourceError):build()
   finally:pool['content_sha256']['hr_val']=old

 def test_wrong_export_and_duplicate_game_rejected(self):
  for change in ('bad_label','duplicate'):
   with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);audit,ready,pool=fixture(root,**{change:True})
    with self.assertRaises(farm.SourceError):
     farm._derived(root,audit,ready,pool,expected=(79,1580,3156,1577,2,1),extract_module=extract)

if __name__=='__main__': unittest.main()
