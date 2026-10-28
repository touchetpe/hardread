"""Synthetic end-to-end selection and source-bound manifest checks."""
import contextlib,copy,hashlib,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
ROOT=next(p for p in HERE.parents if (p/'tools/review/calib_b.py').is_file())
sys.path[:0]=[str(HERE),str(ROOT/'tools/review'),str(ROOT)]
import build_eval_manifest as M
import c9_farm_source as F
import human_eval
from app import pipeline

def put(root,path,data):
 p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return hashlib.sha256(data).hexdigest()
def raw(obj):return (json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode()

class ManifestTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
  self.stack.enter_context(patch.object(M,'_require_farm_identity'))
  self.stack.enter_context(patch.object(M,'git_repo_head',return_value='fork-commit'))
  self.stack.enter_context(patch.object(F,'checked',side_effect=lambda root,path,digest: Path(root)/path))
  self.stack.enter_context(patch.object(F,'export_bytes',return_value=b'team'))
  self.stack.enter_context(patch.object(human_eval,'draft_team_smogon',return_value='team'))
  self.ctx={'mode':'export','side':'p1','end_reason':'ko','turns':10,'winner':'p1','names':{},'draft_rows':[], 'active':{},'opp_active':{},'private':True,'own_species':[],'opp_species':[]}
  self.stack.enter_context(patch.object(pipeline,'prepare',side_effect=lambda rep,side,team_text=None: (['|turn|10'],dict(self.ctx,side=side),{'genders':{}})))
  self.review={'meta':{'side':'p1','end_reason':'ko'},'bridge':{'version':1,'S':.5,'L':0,'R':1,'C':.5,'s':50,'l':0,'c':50,'result':'win'},'summary':{'result':'win','start_win_prob':.5,'total_luck':0,'decisions':0},'turns':[],'decisions':[]}
  self.src=[]; pins={}
  crawl=[];n=0
  while len(crawl)<12:
   uid=f'crawl-{n}';n+=1
   if M.calib_b.half(uid)==1:crawl.append({'uid':uid,'winner_player':'p1','log':['|turn|10']})
  farm=[{'uid':f'farm-{i}','winner_player':'p1','log':['|turn|10'],'team_exports':{s:{'member':'gen9/hr_val/'+s,'sha256':'a'*64} for s in ('p1','p2')}} for i in range(12)]
  for role in ('games','sets','species_profiles'):
   path=M.EXPECTED_CRAWL_SOURCES[role];data=b''.join(map(raw,crawl)) if role=='games' else b'' if role=='sets' else raw({})
   pins[role]=put(self.root,path,data);self.src.append({'pool':'crawl','role':role,'path':path,'sha256':pins[role]})
  self.stack.enter_context(patch.object(M,'CRAWL_PINS',pins))
  cachehash=put(self.root,M.DRAFT_CACHE_PATH,b'cache');self.stack.enter_context(patch.object(M,'DRAFT_CACHE_SHA',cachehash))
  put(self.root,'tools/review/calibrator.json',raw({'absolute_human':{'a':1}}))
  for role,records in [('games',farm),('decisions',[{'uid':g['uid'],'sides':{'p1':1}} for g in farm])]:
   path='farm-'+role;sha=put(self.root,path,b''.join(map(raw,records)));self.src.append({'pool':'hr_val','role':role,'path':path,'sha256':sha})
  self.identity={'pool':'hr_val',**{role:next(s for s in self.src if s['pool']=='hr_val' and s['role']==role) for role in ('games','decisions')}}
  self.codehashes={path:put(self.root,path,((HERE/Path(path).name) if path.startswith('tools/agent/') else (ROOT/path)).read_bytes()) for path in M.REVIEW_SOURCE_PATHS}
  self.cal='tools/review/calibrator.json#absolute_human@d4a8b2580ca101bc6f55a8900cb716db59d04d78e0636c6843ed135d6b5642a9'
 def build(self):
  census={p:M.enumerate_source_candidates(p,self.src,root=self.root) for p in M.POOLS}
  rows=[];evidence={}
  for pool in M.POOLS:
   selection=M.select_candidates(census[pool],pool,lambda c:dict(self.review,meta={'side':c['side'],'end_reason':'ko'}),lambda c,r:dict(self.ctx,side=c['side']),farm_source_identity=self.identity if pool=='hr_val' else None,review_code_commit='commit',review_source_sha256=self.codehashes,root=self.root)
   evidence[pool]=selection['attempt_evidence']
   for i,c in enumerate(selection['selected']):
    prefix=f'scratch_ds/evals/{pool}-{i}';replay=prefix+'-replay';team=prefix+'-team'
    rh=put(self.root,replay,raw({'log':'|turn|10'}));th=put(self.root,team,b'team')
    row=M.build_artifact_row(c,source_path=c['source_path'],source_sha256=c['source_sha256'],replay_path=replay,replay_sha256=rh,review_path=prefix+'-review',ctx_path=prefix+'-ctx',offered_path=prefix+'-offered',review_code_commit='commit',active_calibrator=self.cal,root=self.root)
    row.update(team_path=team,team_sha256=th);rows.append(row)
  manifest={'schema':'c9-selection-manifest-v1','sources':self.src,'farm_source_identity':self.identity,'candidate_census':census,'rows':rows,'selection_evidence':evidence,'review_code_commit':'commit','review_source_sha256':self.codehashes,'active_calibrator':self.cal,'fork_commit':'fork-commit'}
  return manifest
 def test_fixed_documents_and_mutations(self):
  manifest=self.build();M.validate_manifest(manifest,root=self.root)
  path='scratch_ds/evals/manifest.json';put(self.root,path,raw(manifest));a1,a0=M._manifest_items(path,manifest)
  self.assertEqual((len(a1['items']),len(a0['items'])),(60,30))
  self.assertEqual(M.load_validated_a0(a0,root=self.root)[2][0]['log'],manifest['rows'][0]['replay_path'])
  for doc,kind in ((a1,'a1'),(a0,'a0')):
   bad=copy.deepcopy(doc);bad['items'].pop()
   with self.assertRaises(M.ManifestError):M.validate_items_document(bad,kind,root=self.root)
  bad=copy.deepcopy(manifest);bad['rows'][1]=copy.deepcopy(bad['rows'][0])
  with self.assertRaises(M.ManifestError):M.validate_manifest(bad,root=self.root)
  bad=copy.deepcopy(manifest);bad['selection_evidence']['crawl']['attempts'].pop(0);bad['selection_evidence']['crawl']['attempts_sha256']=M.digest_json(bad['selection_evidence']['crawl']['attempts'])
  with self.assertRaises(M.ManifestError):M.validate_manifest(bad,root=self.root)
  offered=self.root/manifest['rows'][0]['offered_path'];self.assertEqual(json.loads(offered.read_text()),[]);offered.write_bytes(raw([{'turn':1}]))
  with self.assertRaises(M.ManifestError):M.validate_manifest(manifest,root=self.root)
 def test_exception_stops_at_first_rank(self):
  census=M.enumerate_source_candidates('crawl',self.src,root=self.root);calls=[]
  def failure(c):calls.append(c['rank_hash']);raise ValueError('failure')
  with self.assertRaises(M.ManifestError):M.select_candidates(census,'crawl',failure,lambda *_:{},offered_fn=lambda *_:[])
  self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main()
