"""Complete synthetic constructor; review subprocess and all pins are test fakes."""
import contextlib,hashlib,io,json,os,stat,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
ROOT=next(p for p in HERE.parents if (p/'tools/review/calib_b.py').is_file())
sys.path[:0]=[str(HERE),str(ROOT/'tools/review'),str(ROOT)]
import build_eval_manifest as M
import c9_farm_source as F
import construct_eval_c9 as C
import human_eval
sys.path.append(str(ROOT/'tools/agent'))
from llm import GlimmerClient
from app import pipeline
from test_c9_farm_source import fixture,put,extract,tarbytes,digest
from test_c9_proposed_manifest import raw

class ConstructorTest(unittest.TestCase):
 def test_synthetic_constructor_and_rank_proof(self):
  with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as st:
   root=Path(tmp);audit,ready,pool=fixture(root)
   games,cover,mapping,counts=F._derived(root,audit,ready,pool,expected=(79,1580,3156,1577,2,1),extract_module=extract)
   outputs=(b''.join(map(F.canonical,games)),b''.join(map(F.canonical,cover)),F.canonical({'games':mapping,'counts':counts}))
   crawl=[];n=0
   while len(crawl)<12:
    uid=f'crawl-{n}';n+=1
    if M.calib_b.half(uid)==1:crawl.append({'uid':uid,'winner_player':'p1','log':['|turn|10']})
   pins={}
   for role,path in M.EXPECTED_CRAWL_SOURCES.items():
    data=b''.join(map(raw,crawl)) if role=='games' else b'' if role=='sets' else raw({})
    pins[role]=put(root,path,data)
   put(root,'tools/review/calibrator.json',raw({'absolute_human':{'a':1}}));cache=put(root,M.DRAFT_CACHE_PATH,b'cache')
   for path in M.REVIEW_SOURCE_PATHS:put(root,path,((HERE/Path(path).name) if path.startswith('tools/agent/') else (ROOT/path)).read_bytes())
   ctx={'mode':'export','side':'p1','end_reason':'ko','turns':10,'winner':'p1','names':{},'draft_rows':[], 'active':{},'opp_active':{},'private':True,'own_species':[],'opp_species':[]}
   review={'meta':{'side':'p1','end_reason':'ko'},'bridge':{'version':1,'S':.5,'L':0,'R':1,'C':.5,'s':50,'l':0,'c':50,'result':'win'},'summary':{'result':'win','start_win_prob':.5,'total_luck':0,'decisions':0},'turns':[],'decisions':[]}
   def fake_review(cmd,**kw):
    output=Path(cmd[cmd.index('--out')+1]);side=cmd[cmd.index('--side')+1];output.write_bytes(raw(dict(review,meta={'side':side,'end_reason':'ko'})))
    return SimpleNamespace(returncode=0,stderr=b'')
   st.enter_context(patch.object(C,'CRAWL',pins));st.enter_context(patch.object(M,'CRAWL_PINS',pins))
   st.enter_context(patch.object(M,'DRAFT_CACHE_SHA',cache));st.enter_context(patch.object(M,'git_head_for',return_value='commit'));st.enter_context(patch.object(M,'git_repo_head',return_value='fork-commit'))
   st.enter_context(patch.object(F,'checked',side_effect=lambda root,path,digest:Path(root)/path))
   st.enter_context(patch.object(F,'output_bytes',return_value=outputs));st.enter_context(patch.object(F,'export_bytes',return_value=b'team'))
   st.enter_context(patch.object(human_eval,'draft_team_smogon',return_value='team'))
   st.enter_context(patch.object(pipeline,'prepare',side_effect=lambda rep,side,team_text=None:(['|turn|10'],dict(ctx,side=side),{'genders':{}})))
   st.enter_context(patch.object(C.subprocess,'run',side_effect=fake_review))
   manifest_path=C.construct(root,'scratch_ds/evals/fixture',root/'fake-python')
   a1=json.loads((root/'scratch_ds/evals/fixture/a1_items.json').read_bytes());a0=json.loads((root/'scratch_ds/evals/fixture/a0_items.json').read_bytes())
   self.assertEqual((len(a1['items']),len(a0['items'])),(60,30))
   manifest=json.loads((root/manifest_path).read_bytes());self.assertEqual(len(manifest['rows']),20)
   self.assertEqual(len({(r['pool'],r['uid']) for r in manifest['rows']}),20)
   M.validate_items_document(a1,'a1',root=root)
   M.validate_items_document(a0,'a0',root=root)
   calls=[]
   def fail_review(cmd,**kw): calls.append(cmd);return SimpleNamespace(returncode=7,stderr=b'private traceback')
   stdout=io.StringIO()
   with patch.object(C.subprocess,'run',side_effect=fail_review),contextlib.redirect_stdout(stdout), \
        patch.object(GlimmerClient,'health',side_effect=AssertionError('health called')) as health, \
        patch.object(GlimmerClient,'chat',side_effect=AssertionError('chat called')) as chat:
    with self.assertRaises(M.ManifestError):C.construct(root,'scratch_ds/evals/failure',root/'fake-python')
    health.assert_not_called();chat.assert_not_called()
   failure_dir=root/'scratch_ds/evals/failure';receipts=list(failure_dir.glob('*-failure.json'))
   self.assertEqual((len(calls),len(receipts)),(1,1))
   receipt=json.loads(receipts[0].read_bytes());self.assertEqual(receipt['status'],'candidate_exception_provenance_pending')
   artifact=receipt['failure_artifact'];stderr_path=root/artifact['path'];fake_stderr=b'private traceback'
   self.assertEqual(stderr_path.parent,failure_dir);self.assertEqual(stderr_path.read_bytes(),fake_stderr)
   self.assertEqual(artifact,{'path':str(stderr_path.relative_to(root)),'sha256':hashlib.sha256(fake_stderr).hexdigest(),
                              'bytes':len(fake_stderr),'exit_code':7,'kind':'worker_stderr'})
   self.assertEqual(stat.S_IMODE(stderr_path.stat().st_mode),0o600)
   with self.assertRaises(FileExistsError): C.write_new(stderr_path,b'overwrite')
   self.assertEqual(stderr_path.read_bytes(),fake_stderr)
   self.assertEqual(stat.S_IMODE(receipts[0].stat().st_mode),0o600)
   self.assertEqual(stdout.getvalue(),'');self.assertNotIn('private traceback',receipts[0].read_text())
   self.assertFalse((failure_dir/'selection_manifest.json').exists());self.assertEqual(len(list(failure_dir.glob('*-stderr.bin'))),1)
   self.assertNotIn('GlimmerClient',vars(C))
   launch_calls=[]
   def fail_launch(cmd,**kw):
    launch_calls.append(cmd);raise OSError('private exception detail')
   with patch.object(C.subprocess,'run',side_effect=fail_launch),contextlib.redirect_stdout(io.StringIO()):
    with self.assertRaises(M.ManifestError):C.construct(root,'scratch_ds/evals/launch-failure',root/'fake-python')
   launch_dir=root/'scratch_ds/evals/launch-failure';launch_receipts=list(launch_dir.glob('*-failure.json'))
   self.assertEqual((len(launch_calls),len(launch_receipts)),(1,1));launch_receipt=json.loads(launch_receipts[0].read_bytes())
   diagnostic=launch_receipt['failure_artifact'];diag_path=root/diagnostic['path'];data=diag_path.read_bytes()
   self.assertEqual(diagnostic,{'path':str(diag_path.relative_to(root)),'sha256':hashlib.sha256(data).hexdigest(),
                                'bytes':len(data),'exit_code':None,'kind':'sanitized_diagnostic'})
   self.assertLess(len(data),200);self.assertNotIn(b'private exception detail',data)
   self.assertEqual(stat.S_IMODE(diag_path.stat().st_mode),0o600)
   self.assertEqual(stat.S_IMODE(launch_receipts[0].stat().st_mode),0o600)
   self.assertFalse((launch_dir/'selection_manifest.json').exists())
   with patch.object(pipeline,'prepare',side_effect=RuntimeError('context')), patch.object(C.subprocess,'run',side_effect=AssertionError('review called')):
    with self.assertRaisesRegex(M.ManifestError,'context/team preparation'):
     C.construct(root,'scratch_ds/evals/context-failure',root/'fake-python')
   self.assertEqual(list((root/'scratch_ds/evals/context-failure').glob('*-failure.json')),[])

class PrivateArtifactModeTest(unittest.TestCase):
 def test_exclusive_file_is_0600_even_with_restrictive_umask(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'private';previous=os.umask(0o777)
   try: C.write_new(path,b'synthetic')
   finally: os.umask(previous)
   self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
   with self.assertRaises(FileExistsError): C.write_new(path,b'overwrite')
   self.assertEqual(path.read_bytes(),b'synthetic')

class CliGuardTest(unittest.TestCase):
 def test_no_review_switch_stops_before_source_or_review(self):
  import io
  from contextlib import redirect_stderr
  with patch.object(sys,'argv',['construct_eval_c9.py','--output','scratch_ds/evals/unused']), \
       patch.object(F,'output_bytes',side_effect=AssertionError('source called')), redirect_stderr(io.StringIO()):
   with self.assertRaises(SystemExit) as error: C.main()
   self.assertEqual(error.exception.code,2)

class ExactAppTest(unittest.TestCase):
 def test_selected_synthetic_export_uses_exact_context_and_empty_offered(self):
  species1=['Pikachu','Charizard','Bulbasaur','Squirtle','Eevee','Snorlax']
  species2=['Gengar','Gyarados','Dragonite','Mew','Ditto','Mewtwo']
  lines=['|player|p1|Alice','|player|p2|Bob','|gametype|singles']
  lines += [f'|poke|p1|{s}, L100' for s in species1]
  lines += [f'|poke|p2|{s}, L100' for s in species2]
  lines += ['|teampreview','|turn|1','|turn|2','|turn|10','|win|Alice']
  team='\n\n'.join(f'{s} @ Leftovers\nAbility: Static\n- Tackle' for s in species1)
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);member='gen9/hr_val/selected-team.txt';pool=tarbytes({member:team.encode()})
   pool_sha=put(root,'pool.tar.gz',pool);manifest=raw({'tarball_sha256':pool_sha})
   manifest_sha=put(root,'pool.json',manifest)
   with patch.object(F,'POOL','pool.tar.gz'),patch.object(F,'POOL_MANIFEST',('pool.json',manifest_sha)):
    selected=F.export_bytes(root,{'member':member,'sha256':digest(team.encode())}).decode()
  out,ctx,job=pipeline.prepare({'log':'\n'.join(lines),'formatid':'gen9ou','private':True},'p1',team_text=selected)
  self.assertEqual(ctx['mode'],'export');self.assertEqual(job['team'],team)
  review={'meta':{'side':'p1','end_reason':'ko'},'bridge':{'version':1,'S':.5,'L':0,'R':1,'C':.5,'s':50,'l':0,'c':50,'result':'win'},'summary':{'result':'win','start_win_prob':.5,'total_luck':0,'decisions':0},'turns':[],'decisions':[]}
  self.assertEqual(pipeline.build_view_with_offered(review,ctx)[1],[])
  self.assertTrue(any('Player1' in line for line in out))

if __name__=='__main__':unittest.main()
