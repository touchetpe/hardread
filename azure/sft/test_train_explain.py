import hashlib,tempfile,unittest
from pathlib import Path
import train_explain as T

class Tests(unittest.TestCase):
 def test_unrelated_dataset_rejected_before_subprocess(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'train.jsonl.gz').write_bytes(b't');(root/'val.jsonl.gz').write_bytes(b'v')
   h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
   train=h(root/'train.jsonl.gz');val=h(root/'val.jsonl.gz')
   manifest={'status':'committed_final_overlap_verified','task':'explain','train_sha256':train,'val_sha256':val,'decision_count':2000,'verified_items':1500,'a1_sha256':'a'*64,'a0_sha256':'b'*64,'training_lineage_sha256':'f'*64,'training_run_id':'test'}
   with self.assertRaises(T.TrainingError):T.build_command(runner='run',snapshot=root,data=root,out='out',epochs=2,world_size=4,manifest=manifest,train_sha256=train,val_sha256=val,lineage={},root=root,blind_key=b'x')
 def test_missing_final_overlap_or_invalid_node_count(self):
  with self.assertRaises(T.TrainingError):T.build_command(runner='r',snapshot='s',data='d',out='o',epochs=1,world_size=1,manifest={},train_sha256='a',val_sha256='b',lineage={},root='.',blind_key=b'x')
 def test_training_package_requires_all_committed_live_bytes_and_runner(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);hashes={};selected={}
   for name in T.REQUIRED_TRAIN_CODE:
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(name);raw=p.read_bytes();hashes[name]=hashlib.sha256(raw).hexdigest();selected[('config',name)]=raw
   manifest={'training_code_sha256':hashes};runner=root/'azure/sft/run_sft.py'
   self.assertTrue(T.verify_training_package(manifest,selected,root,runner))
   with self.assertRaises(T.TrainingError):T.verify_training_package(manifest,selected,root,root/'other.py')
   (root/'azure/sft/sft_plugin.py').write_text('changed')
   with self.assertRaises(T.TrainingError):T.verify_training_package(manifest,selected,root,runner)
   (root/'azure/sft/sft_plugin.py').write_bytes(selected[('config','azure/sft/sft_plugin.py')]);selected.pop(('config','tools/sft/glimmer_chat.py'))
   with self.assertRaises(T.TrainingError):T.verify_training_package(manifest,selected,root,runner)
if __name__=='__main__':unittest.main()
