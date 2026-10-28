"""Synthetic Git, frozen-item and mocked-owned-server integration; no real calls."""
import copy,hashlib,json,platform,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import build_eval_manifest as M
import evallock,explain,llm
import app.server as server
import eval_a1_v2 as E
from test_a1_v2_private import fixture

class Tests(unittest.TestCase):
 def test_commits_60_items_server_lock_outputs_and_failures(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   def git(*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout.decode().strip()
   git('init','-q');git('config','user.name','Synthetic');git('config','user.email','synthetic@example.invalid')
   for name in E.REQUIRED:
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('synthetic source '+name+'\n')
   git('add','--',*sorted(E.REQUIRED));git('commit','-qm','synthetic source');source_commit=git('rev-parse','HEAD')
   protocol=root/'EVAL_PROTOCOL_AGENT.md';protocol.write_text('synthetic prior protocol\n\n'+E.PROTOCOL_SECTION+'\n\n'+E.COMPAT_SECTION+'\n');git('add','--','EVAL_PROTOCOL_AGENT.md');git('commit','-qm','synthetic protocol');protocol_commit=git('rev-parse','HEAD')
   source_pins={p:E.digest(root/p) for p in E.REQUIRED}
   data=root/'data';data.mkdir(); review=data/'review.json';ctx=data/'ctx.json';offered=data/'offered.json'
   review.write_text('{}');ctx.write_text('{}');offered.write_text('[]')
   rows=[]
   for i in range(20):
    rows.append({'pool':'crawl' if i<10 else 'hr_val','uid':'synthetic-%d'%i,'side':'p1','rank_hash':str(i),'review_path':'data/review.json','review_sha256':E.digest(review),'ctx_path':'data/ctx.json','ctx_sha256':E.digest(ctx),'offered_path':'data/offered.json','offered_sha256':E.digest(offered),'ordered_offered_keys':[],'source_path':'data/source','source_sha256':'a'*64,'replay_path':'data/replay','replay_sha256':'b'*64})
   manifest={'rows':rows};(data/'manifest.json').write_text(json.dumps(manifest));items,_=M._manifest_items('data/manifest.json',manifest)
   items_path=data/'items.json';items_path.write_text(json.dumps(items));self.assertEqual(len(items['items']),60)
   exe=root/'server';model=root/'model';exe.write_bytes(b'synthetic executable');model.write_bytes(b'synthetic weights')
   url='http://127.0.0.1:18222/v1/chat/completions';argv=[str(exe),'--model',str(model),'--host','127.0.0.1','--port','18222','--jinja','--swa-full','--ctx-size','32768','--reasoning-format','deepseek','--gpu-layers','99','--parallel','1'];now=time.time()
   proc={'pid':123,'argv':argv,'executable_path':str(exe),'executable_sha256':E.TRUSTED_EXECUTABLE_SHA256,'started_utc':'2026-10-28T00:00:00Z'};app={'pid':456,'started_utc':'2026-10-28T00:01:00Z','executable_sha256':'f'*64}
   proof=dict(proc,schema='hardread.a1.v2.owned-server.v1',owned=True,host=platform.node(),url=url,model_path=str(model),gguf_sha256=E.TRUSTED_MODEL_SHA256,build=E.TRUSTED_BUILD,verified_epoch=now,memory={'status':'pass','available_bytes':100,'observed_epoch':now},quiescence={'source':'app_queue_inspection_v1','server_pid':123,'server_started_utc':proc['started_utc'],'url':url,'app_pid':456,'app_started_utc':app['started_utc'],'app_executable_sha256':app['executable_sha256'],'inflight_llm_calls':0,'queue_checked':True,'observed_epoch':now})
   proof_path=root/'proof.json';proof_path.write_text(json.dumps(proof))
   request={'temperature':0,'top_k':40,'top_p':1,'seed':42,'reasoning':'low','max_tokens':2048,'tool_choice':'required','mode':'loop','max_data_calls':4,'max_repairs':1}
   config={'schema':'hardread.a1.base-v2.config.v1','arm':'base','request':request,'source_sha256':source_pins,'source_commit':source_commit,'protocol_commit':protocol_commit,'protocol_path':'EVAL_PROTOCOL_AGENT.md','protocol_sha256':E.digest(protocol),'items_sha256':E.digest(items_path),'manifest_sha256':M.manifest_digest(manifest),'server':{'url':url,'argv':argv,'executable_sha256':E.TRUSTED_EXECUTABLE_SHA256,'gguf_sha256':E.TRUSTED_MODEL_SHA256,'build':proof['build'],'min_available_bytes':50}}
   cfg=root/'config.json';cfg.write_text(json.dumps(config));git('add','--','config.json');git('commit','-qm','synthetic committed config')
   self.assertTrue(E.verify_commits(config,cfg,root))
   with self.assertRaises(E.RecordError):E.validate_config(dict(config,protocol_path='other.md'))
   original_load=M.load_validated_a1;original_paths=M.validate_record_output_paths;original_lock=evallock.eval_lock
   calls=[]
   class FakeClient:
    def __init__(self,*args,**kwargs):self.i=0
    def health(self):return True
    def chat(self,messages,tools=None,tool_choice='required',max_tokens=None):
     f=fixture();response=f['capture'][self.i%2]['response'];self.i+=1;calls.append('fake');return copy.deepcopy(response)
   def synthetic_run(review,question,client,**kw):
    f=fixture();f['result']['question']=question
    for e in f['capture']:
     msgs=copy.deepcopy(e['messages']);msgs[1]['content']='Question: '+question
     client.chat(msgs,tools=e['tools'])
    result=f['result'];result.update(raw_pass=True,after_repair_pass=True,latency_s=.1,errors=[],attempts=[])
    return result
   outdir=root/'scratch_ds/evals';outdir.mkdir(parents=True);out=outdir/'synthetic-v2.json';lock=root/'lock'
   argv_cli=['eval_a1_v2.py','--config',str(cfg),'--proof',str(proof_path),'--items',str(items_path),'--out',str(out)]
   real_digest=E.digest
   with patch.object(E,'digest',side_effect=lambda p:E.TRUSTED_MODEL_SHA256 if Path(p)==model else E.TRUSTED_EXECUTABLE_SHA256 if Path(p)==exe else real_digest(p)),patch.object(E,'ROOT',root),patch.object(M,'validate_manifest',return_value={'crawl':10,'hr_val':10}),patch.object(M,'load_validated_a1',side_effect=lambda doc:original_load(doc,root=root,visible_verdicts_fn=lambda *_:{})),patch.object(M,'validate_record_output_paths',side_effect=lambda a,companion:original_paths(a,root=root,companion=companion)),patch.object(M,'_mac_process_snapshot',side_effect=lambda pid:proc if pid==123 else app),patch.object(M,'_live_executable_version',return_value=proof['build']),patch.object(E,'_available_memory',return_value=100),patch.object(evallock,'eval_lock',side_effect=lambda what,seconds:original_lock(what,seconds,lock_path=lock)),patch.object(llm,'GlimmerClient',FakeClient),patch.object(explain,'run',side_effect=synthetic_run),patch.object(server,'wording_guard',return_value={'final':'glimmer_raw','policy_rejected':False}),patch.object(sys,'argv',argv_cli):
    # An otherwise internally consistent synthetic 60-item cohort cannot pass
    # production identity pins; no constants or identity checks are stubbed.
    with self.assertRaises(E.RecordError):E.main()
    self.assertEqual(calls,[]);self.assertFalse(lock.exists());self.assertFalse(out.exists())
    # The exact registered path and claimed pins still cannot make synthetic
    # item bytes pass the immutable production identity boundary.
    registered=root/E.TRUSTED_ITEMS;registered.parent.mkdir(parents=True,exist_ok=True);registered.write_bytes(items_path.read_bytes())
    forged=dict(config,items_sha256=E.TRUSTED_ITEMS_SHA256,manifest_sha256=E.TRUSTED_MANIFEST)
    cfg.write_text(json.dumps(forged));git('add','--','config.json');git('commit','-qm','synthetic forged cohort claim')
    argv_forged=['eval_a1_v2.py','--config',str(cfg),'--proof',str(proof_path),'--items',str(registered),'--out',str(out)]
    with patch.object(sys,'argv',argv_forged),patch.object(M,'load_validated_a1',side_effect=AssertionError('loader reached')):
     with self.assertRaises(E.RecordError):E.main()
    self.assertEqual(calls,[])
    cfg.write_text(json.dumps(config));git('add','--','config.json');git('commit','-qm','restore synthetic config')
    changed=copy.deepcopy(items);changed['items'][0],changed['items'][1]=changed['items'][1],changed['items'][0]
    with self.assertRaises(M.ManifestError):original_load(changed,root=root,visible_verdicts_fn=lambda *_:{})
    cfg.write_text(cfg.read_text()+' ')
    with self.assertRaises(E.RecordError):E.verify_commits(config,cfg,root)
    cfg.write_text(json.dumps(config));(root/next(iter(E.REQUIRED))).write_text('changed')
    with self.assertRaises(E.RecordError):E.verify_commits(config,cfg,root)
