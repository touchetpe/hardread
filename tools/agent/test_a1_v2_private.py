import copy,json,tempfile,unittest
from unittest.mock import patch
import build_eval_manifest as M
import platform
from pathlib import Path
import a1_v2_capture as C
import audit_a1_v2 as A
import eval_a1_v2 as E
from llm import assistant_message
from tools import all_tool_schemas
from explain import SYSTEM_PROMPT

def call(cid,name,args):return {'id':cid,'name':name,'arguments':args,'parse_error':None}
def response(calls):
 return {'message':{'content':'','tool_calls':[{'id':c['id'],'function':{'name':c['name'],'arguments':json.dumps(c['arguments'])}} for c in calls]},'tool_calls':calls}
def fixture():
 calls=[call('a','get_summary',{}),call('b','list_key_moments',{'kind':'luck'}),call('c','get_turn',{'turn':1}),call('d','get_luck_events',{'turn':1}),call('e','unknown',{}),call('f','get_turn',{'turn':9})]
 resp=response(calls);final=response([call('z','submit_explanation',{})])
 outputs=[{'id':'#1','luck_pts':5},{'ranked':[]},{'turn':1},{'events':[]},{'error':"unknown tool 'unknown'"},{'error':'tool budget used; call submit_explanation now'}]
 msgs=[{'role':'system','content':SYSTEM_PROMPT},{'role':'user','content':'Question: What happened?'}]
 toolmsgs=[{'role':'tool','tool_call_id':c['id'],'name':c['name'],'content':json.dumps(o)} for c,o in zip(calls,outputs)]
 events=[{'messages':msgs,'tools':all_tool_schemas(),'tool_choice':'required','max_tokens':None,'response':resp},{'messages':msgs+[assistant_message(resp)]+toolmsgs,'tools':all_tool_schemas(),'tool_choice':'required','max_tokens':None,'response':final}]
 steps=[{'tool_calls':[{k:c[k] for k in ('name','arguments','parse_error')} for c in r['tool_calls']]} for r in (resp,final)]
 citation={'slot':'x','ref':'#1.luck_pts','tool':'get_summary','field':'luck_pts','kind':'pts','value':5,'shown':'+5 points'}
 result={'question':'What happened?','mode':'loop','final':'glimmer_raw','steps':steps,'trace':[{'name':'get_summary','args':{},'output':outputs[0]},{'name':'list_key_moments','args':{'kind':'luck'},'output':outputs[1]},{'name':'get_turn','args':{'turn':1},'output':outputs[2]},{'name':'get_luck_events','args':{'turn':1},'output':outputs[3]}], 'rendered':{'sentences':[{'i':0,'text':'Luck was +5 points.','template':'Luck was {x}.','slots':{'x':'#1.luck_pts'},'citations':[citation]}]}}
 return {'i':0,'question':'What happened?','final':'glimmer_raw','result':result,'capture':events,'trace_binding':{'items_sha256':'a'*64,'manifest_sha256':'b'*64,'config_sha256':'c'*64,'source_sha256':'d'*64}}

class Tests(unittest.TestCase):
 def test_complete_scope_and_structural_citation(self):
  p=A.project(fixture())[0]
  self.assertEqual(len(p['data_tool_receipts']),6)
  self.assertEqual(p['data_tool_receipts'][1]['output'],{'ranked':[]})
  self.assertEqual(p['data_tool_receipts'][4]['output']['error'],"unknown tool 'unknown'")
  self.assertIn('budget',p['data_tool_receipts'][5]['output']['error'])
  self.assertEqual(p['cited_receipts'][0]['record']['luck_pts'],5)
 def test_malformed_or_incomplete_scope_fails(self):
  for name in ('drop','reorder','value','field','duplicate','unsupported','shown'):
   row=fixture()
   if name=='drop':row['capture'][1]['messages'].pop(3)
   elif name=='reorder':row['capture'][1]['messages'][3:5]=reversed(row['capture'][1]['messages'][3:5])
   elif name=='value':row['result']['rendered']['sentences'][0]['citations'][0]['value']=6
   elif name=='field':row['result']['rendered']['sentences'][0]['citations'][0]['field']='swing_pts'
   elif name=='shown':row['result']['rendered']['sentences'][0]['citations'][0]['shown']='5 points'
   elif name=='unsupported':row['capture'][1]['messages'][3]['content']='123'
   else:
    row['capture'][1]['messages'][4]['content']=json.dumps({'id':'#1','luck_pts':5})
    row['result']['trace'][1]['output']={'id':'#1','luck_pts':5}
   with self.subTest(name=name),self.assertRaises(A.AuditError):A.project(row)
 def test_sampling_binding_and_labels(self):
  rows=[dict(copy.deepcopy(fixture()),i=i) for i in range(60)]
  pins=dict(item_hash='a'*64,manifest_hash='b'*64,config_hash='c'*64,source_hash='d'*64,blind_key=b'k'*32)
  frozen=A.derive(rows,**pins);self.assertEqual(len(frozen['packets']),60)
  self.assertEqual(sum(p['owner_spot_check'] for p in frozen['packets']),15)
  self.assertTrue(all(set(p)=={'question','sentence','citations','cited_receipts','data_tool_receipts','audit_id','owner_spot_check'} for p in frozen['packets']))
  labs=[{'audit_id':p['audit_id'],'misleading':False,'category':None} for p in frozen['packets']]
  owners=[lab for lab,p in zip(labs,frozen['packets']) if p['owner_spot_check']]
  self.assertEqual(A._score_derived(rows,frozen,labs,owners,**pins)['owner_agree'],15)
  for name in ('packet','arm','owner','source','label'):
   altered=copy.deepcopy(frozen);changed=copy.deepcopy(rows);labels=copy.deepcopy(labs)
   if name=='packet':altered['packets'][0]['data_tool_receipts'].reverse()
   if name=='arm':altered['packets'][0]['arm']='base'
   if name=='owner':altered['packets'][0]['owner_spot_check']=not altered['packets'][0]['owner_spot_check']
   if name=='source':changed[0]['trace_binding']['source_sha256']='e'*64
   if name=='label':labels.pop()
   with self.subTest(name=name),self.assertRaises(A.AuditError):A._score_derived(changed,altered,labels,owners,**pins)
 def test_bound_trace_detects_rehashed_source_and_trace_mutation(self):
  import a1_v2_frozen_loader as L
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();source=root/'source.py';source.write_text('synthetic')
   config={'source_sha256':{'source.py':E.digest(source)}};cfg=root/'config.json';cfg.write_text(json.dumps(config))
   source_hash=A.sha(config['source_sha256']);config_hash=E.digest(cfg)
   rows=[dict(copy.deepcopy(fixture()),i=i) for i in range(60)];prepared=[]
   for i,row in enumerate(rows):
    item={'row_index':i,'pool':'synthetic','uid':str(i),'side':'p1','rank_hash':str(i),'q':1,'review':'synthetic-review','question':row['question']};prepared.append(item)
    row.update(review=item['review'],q=1);row['trace_binding'].update(expected_items=60,items_path=str(root/L.ITEM_PATH),items_sha256=L.ITEM_SHA,manifest_sha256='b'*64,config_sha256=config_hash,source_sha256=source_hash,item={k:item[k] for k in ('row_index','pool','uid','side','rank_hash','q')})
   trace=root/'trace.jsonl';trace.write_text(''.join(json.dumps(r)+'\n' for r in rows))
   with patch.object(L,'load_registered_a1_v2',return_value=({},'b'*64,prepared)),patch.object(E,'verify_commits',return_value=True):
    frozen=A.bound_derive(trace,root=root,config_path=cfg,source_sha256=source_hash,blind_key=b'k'*32)
    labs=[{'audit_id':p['audit_id'],'misleading':False,'category':None} for p in frozen['packets']]
    owners=[lab for lab,p in zip(labs,frozen['packets']) if p['owner_spot_check']]
    self.assertEqual(A.bound_score(trace,frozen,labs,owners,root=root,config_path=cfg,source_sha256=source_hash,blind_key=b'k'*32)['n'],60)
    trace.write_text(trace.read_text()+'\n')
    with self.assertRaises(A.AuditError):A.bound_score(trace,frozen,labs,owners,root=root,config_path=cfg,source_sha256=source_hash,blind_key=b'k'*32)
    # A template is excluded from the sentence sample but remains subject to
    # the same structural validation as every other recorded answer.
    bad=copy.deepcopy(rows);bad[0]['final']='template';bad[0]['result']['final']='template'
    bad[0]['result']['rendered']['sentences']=[{'i':'invalid','text':None}]
    for sentence in ({'i':'invalid','text':None},{'i':0,'text':' '},{'i':True,'text':'x'},{'i':-1,'text':'x'}):
     bad[0]['result']['rendered']['sentences']=[sentence]
     trace.write_text(''.join(json.dumps(r)+'\n' for r in bad))
     with self.assertRaisesRegex(A.AuditError,'result structure'):A.bound_derive(trace,root=root,config_path=cfg,source_sha256=source_hash,blind_key=b'k'*32)
    trace.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    source.write_text('changed')
    with self.assertRaises(A.AuditError):A.bound_derive(trace,root=root,config_path=cfg,source_sha256=source_hash,blind_key=b'k'*32)
 def test_client_wrapper_captures_exact_outbound_history(self):
  reference=fixture();events=reference['capture'];result=reference['result']
  class Fake:
   def __init__(self):self.i=0
   def chat(self,messages,tools=None,tool_choice='required',max_tokens=None):
    event=events[self.i];self.i+=1
    assert messages==event['messages'] and tools==event['tools']
    return copy.deepcopy(event['response'])
  def run(review,question,client,**kwargs):
   for e in events:client.chat(e['messages'],tools=e['tools'])
   return copy.deepcopy(result)
  item={'i':0,'question':'What happened?','review':'synthetic','q':1,'review_data':{},'visible_verdicts':{},'offered_data':[]}
  row=E.record_item(item,Fake(),run=run,wording_guard=lambda *args:{'final':'glimmer_raw','policy_rejected':False},binding=reference['trace_binding'])
  self.assertEqual(row['capture'],events)
  self.assertEqual(A.project(row)[0]['data_tool_receipts'][1]['output'],{'ranked':[]})
 def test_owned_server_binding_and_memory(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);exe=root/'server';model=root/'model';exe.write_bytes(b'exe');model.write_bytes(b'model')
   argv=[str(exe),'--model',str(model),'--host','127.0.0.1','--port','18222','--jinja','--swa-full','--ctx-size','32768','--reasoning-format','deepseek','--gpu-layers','99','--parallel','1']
   proc={'pid':123,'argv':argv,'executable_path':str(exe),'executable_sha256':E.TRUSTED_EXECUTABLE_SHA256,'started_utc':'2026-10-28T00:00:00Z'}
   proof=dict(proc,schema='hardread.a1.v2.owned-server.v1',owned=True,host=platform.node(),url='http://127.0.0.1:18222/v1/chat/completions',model_path=str(model),gguf_sha256=E.TRUSTED_MODEL_SHA256,build=E.TRUSTED_BUILD,verified_epoch=1000,memory={'status':'pass','available_bytes':100,'observed_epoch':1000},quiescence={'source':'app_queue_inspection_v1','server_pid':123,'server_started_utc':proc['started_utc'],'url':'http://127.0.0.1:18222/v1/chat/completions','app_pid':456,'app_started_utc':'2026-10-28T00:01:00Z','app_executable_sha256':'f'*64,'inflight_llm_calls':0,'queue_checked':True,'observed_epoch':1000})
   config={'server':{'url':proof['url'],'argv':argv,'executable_sha256':proof['executable_sha256'],'gguf_sha256':proof['gguf_sha256'],'build':proof['build'],'min_available_bytes':50}}
   app={'pid':456,'started_utc':'2026-10-28T00:01:00Z','executable_sha256':'f'*64}
   real_digest=E.digest
   with patch.object(E,'digest',side_effect=lambda p:E.TRUSTED_MODEL_SHA256 if Path(p)==model else E.TRUSTED_EXECUTABLE_SHA256 if Path(p)==exe else real_digest(p)),patch.object(M,'_mac_process_snapshot',side_effect=lambda pid:proc if pid==123 else app),patch.object(M,'_live_executable_version',return_value=proof['build']),patch.object(E,'_available_memory',return_value=100):
    self.assertTrue(E.verify_server(config,proof,now=1000))
    for key,value in (('pid',9794),('owned',False),('verified_epoch',0),('gguf_sha256','0'*64),('quiescence',{})):
     with self.subTest(key=key),self.assertRaises(E.RecordError):E.verify_server(config,dict(proof,**{key:value}),now=1000)
    with patch.object(E,'_available_memory',return_value=49),self.assertRaises(E.RecordError):E.verify_server(config,proof,now=1000)
    for badargv in (argv+['--unknown'],argv+['--jinja'],[x for x in argv if x!='--swa-full'],[('16384' if x=='32768' else x) for x in argv],argv+['--lora','adapter']):
     changed=dict(proof,argv=badargv);changed_config={'server':dict(config['server'],argv=badargv)}
     with self.subTest(argv=badargv),self.assertRaises(E.RecordError):E.verify_server(changed_config,changed,now=1000)
    with self.assertRaises(E.RecordError):E.verify_server({'server':dict(config['server'],build='version: 2 (abcdef0)')},dict(proof,build='version: 2 (abcdef0)'),now=1000)
 def test_fresh_quiescence_before_and_after_lock(self):
  import urllib.request
  from types import SimpleNamespace
  import io
  proof={'pid':123,'url':'http://127.0.0.1:18222/v1/chat/completions','quiescence':{'app_pid':456,'app_started_utc':'start','app_executable_sha256':'f'*64,'app_url':'http://127.0.0.1:18051'}}
  app={'pid':456,'started_utc':'start','executable_sha256':'f'*64}
  class Reply(io.BytesIO):
   status=200
   def __enter__(self):return self
   def __exit__(self,*args):self.close()
  state={'locked':False,'busy':False,'wrong_listener':False}
  def lsof(*args):
   if '-sTCP:LISTEN' in args:
    pid='456' if '-iTCP:18051' in args else '123';return SimpleNamespace(returncode=0,stdout=('999' if state['wrong_listener'] else pid)+'\n',stderr='')
   return SimpleNamespace(returncode=0 if state['busy'] else 1,stdout='p777\n' if state['busy'] else '',stderr='')
  def get(url,timeout):
   self.assertEqual(url,'http://127.0.0.1:18051/api/health')
   return Reply(json.dumps({'ok':True,'queue':0,'running':0,'eval_lock':state['locked'],'llm_url':'http://127.0.0.1:18131'}).encode())
  with patch.object(M,'_mac_process_snapshot',return_value=app),patch.object(E,'_lsof',side_effect=lsof),patch.object(urllib.request,'urlopen',side_effect=get):
   self.assertFalse(E.verify_quiescence(proof,locked=False)['lock']);state['locked']=True
   self.assertTrue(E.verify_quiescence(proof,locked=True)['lock'])
   state['busy']=True
   with self.assertRaises(E.RecordError):E.verify_quiescence(proof,locked=True)
   state['busy']=False;state['wrong_listener']=True
   with self.assertRaises(E.RecordError):E.verify_quiescence(proof,locked=True)
   state['wrong_listener']=False;state['locked']=False
   with self.assertRaises(E.RecordError):E.verify_quiescence(proof,locked=True)
 def test_listener_rejects_multiple_duplicate_and_malformed_pids(self):
  from types import SimpleNamespace
  for out in ('123\n456\n','123\n123\n','p123\nf7\n',' 123\n','123 garbage\n','\n',''):
   with self.subTest(output=out),patch.object(E,'_lsof',return_value=SimpleNamespace(returncode=0,stdout=out,stderr='')):
    with self.assertRaises(E.RecordError):E._listener(123,18222)
  for code,err in ((1,''),(0,'warning')):
   with self.subTest(code=code,err=err),patch.object(E,'_lsof',return_value=SimpleNamespace(returncode=code,stdout='123\n',stderr=err)):
    with self.assertRaises(E.RecordError):E._listener(123,18222)
  with patch.object(E,'_lsof',return_value=SimpleNamespace(returncode=0,stdout='123\n',stderr='')) as called:
   E._listener(123,18222);called.assert_called_once_with('-iTCP:18222','-sTCP:LISTEN','-t')
 def test_private_audit_paths_and_exclusive_creation(self):
  import os
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();base=root/'scratch_ds/evals';base.mkdir(parents=True,mode=0o700);os.chmod(root/'scratch_ds',0o700)
   frozen={'schema':'x','population':0,'packets':[],'key':[],'binding':{},'instructions':'synthetic'}
   paths=[base/'packet-v2.json',base/'key-v2.json',base/'meta-v2.json']
   A.write_packet(frozen,*paths,root=root)
   self.assertTrue(all(p.exists() and p.stat().st_mode&0o777==0o600 for p in paths))
   with self.assertRaises(A.AuditError):A.write_packet(frozen,*paths,root=root)
   with self.assertRaises(A.AuditError):A.write_packet(frozen,root/'outside-v2.json',base/'k-v2',base/'m-v2',root=root)
   (base/'link').symlink_to(root,target_is_directory=True)
   with self.assertRaises(A.AuditError):A.write_packet(frozen,base/'link/a-v2',base/'k-v2',base/'m-v2',root=root)
 def test_exclusive_output_and_disabled_recorder(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a';p.write_text('preserve')
   with self.assertRaises(FileExistsError):A.reserve([p])
   self.assertEqual(p.read_text(),'preserve')
  with self.assertRaises(E.RecordError):E.validate_config({})
