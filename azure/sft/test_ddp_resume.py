import hashlib,importlib.util,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
D=Path(__file__).parent
spec=importlib.util.spec_from_file_location('sft_resume',D/'run_sft.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
spec=importlib.util.spec_from_file_location('sft_plugin_resume',D/'sft_plugin.py');P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
class ResumeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='/private/tmp');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.ck=self.root/'checkpoint-3497';self.ck.mkdir();self.data=self.root/'data';self.data.mkdir()
  for n in ('adapter_model.safetensors','adapter_config.json','optimizer.pt','scheduler.pt','rng_state_0.pth'):(self.ck/n).write_bytes(('synthetic '+n).encode())
  (self.ck/'trainer_state.json').write_text(json.dumps({'global_step':3497,'epoch':1.0}));(self.data/'train.jsonl.gz').write_bytes(b'fake train');(self.data/'manifest.json').write_bytes(b'fake manifest');self.proof=self.root/'proof';self.proof.write_bytes(b'fake proof');self.marker=self.root/'marker';self.repin()
  self.args=SimpleNamespace(resume_from=str(self.ck),eval_only=False,epochs=2,micro_batch=4,grad_accum=2,lr=2e-4,lora_r=32,lora_alpha=16,save_steps=200,loss='weighted',choice_weight=10.,soft_alpha=.7,max_steps=None,warmup_steps=None,no_mb_fallback=True,skip_teacher=True,skip_serve=True,resume_marker=str(self.marker),proof_binding=str(self.proof),proof_binding_sha256=sha(self.proof),data=str(self.data))
 def repin(self):
  rows=[{'path':'checkpoint-3497/'+p.name,'bytes':p.stat().st_size,'sha256':sha(p)} for p in self.ck.iterdir()];self.marker.write_text(json.dumps({'files':rows}))
 def check(self):return M.validate_v2_second_epoch(self.args,marker_digest=sha(self.marker),proof_digest=sha(self.proof),train_digest=sha(self.data/'train.jsonl.gz'),manifest_digest=sha(self.data/'manifest.json'))
 def test_valid(self):
  with patch.dict(os.environ,{'WORLD_SIZE':'12'}):
   r=self.check();self.assertEqual(r['global_step'],3497);self.assertFalse(r['bitwise_replay'])
 def test_missing_or_mutated(self):
  with patch.dict(os.environ,{'WORLD_SIZE':'12'}):
   for name in ('adapter_model.safetensors','adapter_config.json','optimizer.pt','scheduler.pt','trainer_state.json','rng_state_0.pth'):
    p=self.ck/name;old=p.read_bytes();p.unlink()
    with self.subTest(name=name),self.assertRaises(ValueError):self.check()
    p.write_bytes(old)
   (self.ck/'optimizer.pt').write_bytes(b'changed')
   with self.assertRaises(ValueError):self.check()
 def test_position_recipe_world_and_proof(self):
  with patch.dict(os.environ,{'WORLD_SIZE':'12'}):
   for field,value in [('epochs',1),('lr',1e-4),('skip_teacher',False),('skip_serve',False),('grad_accum',4),('resume_from',None)]:
    old=getattr(self.args,field);setattr(self.args,field,value)
    with self.subTest(field=field),self.assertRaises(ValueError):self.check()
    setattr(self.args,field,old)
   for state in ({'global_step':3496,'epoch':1.0},{'global_step':3497,'epoch':.9}):
    (self.ck/'trainer_state.json').write_text(json.dumps(state));self.repin()
    with self.assertRaisesRegex(ValueError,'position differs'):self.check()
  with patch.dict(os.environ,{'WORLD_SIZE':'11'}),self.assertRaises(ValueError):self.check()
 def test_marker(self):
  with patch.dict(os.environ,{'WORLD_SIZE':'12'}),self.assertRaisesRegex(ValueError,'marker differs'):M.validate_v2_second_epoch(self.args,marker_digest='0'*64)
 def test_ordinary_ddp_resume_stays_disabled(self):
  import sys
  argv=['run_sft.py','--snapshot','/snapshot','--data',str(self.data),'--out',str(self.root/'out'),'--resume-from',str(self.ck)]
  with patch.dict(os.environ,{'WORLD_SIZE':'12'}),patch.object(sys,'argv',argv),patch.object(M,'preflight') as preflight:
   with self.assertRaisesRegex(RuntimeError,'reviewed second-epoch mode'):M.main()
   preflight.assert_not_called()
 def test_optimizer_and_scheduler_are_compared_to_saved_state(self):
  import torch
  parameter=torch.nn.Parameter(torch.tensor(0.));optimizer=torch.optim.AdamW([parameter],lr=.001);parameter.grad=torch.ones(());optimizer.step()
  scheduler=torch.optim.lr_scheduler.LambdaLR(optimizer,lambda _:1.0)
  torch.save(optimizer.state_dict(),self.ck/'optimizer.pt');torch.save(scheduler.state_dict(),self.ck/'scheduler.pt')
  result=P.compare_resume_state(optimizer,scheduler,self.ck);self.assertEqual(result['optimizer_step_counters_verified'],1)
  optimizer.state[parameter]['step']+=1
  with self.assertRaisesRegex(RuntimeError,'optimizer counter differs'):P.compare_resume_state(optimizer,scheduler,self.ck)
  optimizer.state[parameter]['step']-=1;optimizer.state[parameter]['exp_avg']+=1
  with self.assertRaisesRegex(RuntimeError,'optimizer tensor state differs'):P.compare_resume_state(optimizer,scheduler,self.ck)
 def test_loaded_optimizer_scheduler_receipt(self):
  class O:
   param_groups=[{'lr':0.0}]
   def state_dict(self):return {'state':{1:{'step':3497}}}
  t=SimpleNamespace(optimizer=O(),lr_scheduler=SimpleNamespace(last_epoch=3497),_hrsft_adapter_loaded=True,_hrsft_resume_comparison={'optimizer_step_counters_verified':1});state=SimpleNamespace(global_step=3497)
  with patch.dict(os.environ,{'RANK':'2'}):
   cb=P.make_resume_callback(object,t,self.root,3497);cb.on_train_begin(None,state,None);state.global_step=3498;t.optimizer.param_groups[0]['lr']=.0001;cb.on_step_end(None,state,None)
   self.assertEqual(json.loads((self.root/'resume-rank2.json').read_text())['steps'][0]['global_step'],3498)
   t._hrsft_adapter_loaded=False
   with self.assertRaises(RuntimeError):cb.on_train_begin(None,SimpleNamespace(global_step=3497),None)
   t._hrsft_adapter_loaded=True;t.optimizer.state_dict=lambda:{'state':{}}
   with self.assertRaises(RuntimeError):cb.on_train_begin(None,SimpleNamespace(global_step=3497),None)
if __name__=='__main__':unittest.main()
