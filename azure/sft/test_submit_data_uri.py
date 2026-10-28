import hashlib, os, shutil, subprocess, tempfile, unittest
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[2]
FILES=('train.jsonl.gz','val.jsonl.gz','eval.jsonl.gz','manifest.json')
URI='azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/synthetic/data/'

class SubmitDataTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'repo';self.root.mkdir()
  for rel in ('azure/sft','tools/sft'):
   shutil.copytree(ROOT/rel,self.root/rel,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
  self.data=Path(self.tmp.name)/'data';self.data.mkdir()
  for name in FILES:(self.data/name).write_bytes(('synthetic '+name).encode())
  self.bin=Path(self.tmp.name)/'bin';self.bin.mkdir()
  for name in ('az','python'):
   p=self.bin/name;p.write_text('#!/bin/bash\nprintf "%s\\n" "$*" > "$SYNTHETIC_CALLED"\nexit 0\n');p.chmod(0o755)
  self.env=dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ['PATH'],DRY_RUN='1',HR_AZ_SUBSCRIPTION='synthetic',HR_AZ_RG='synthetic',HR_AZ_WS='synthetic',HR_AZ_CLUSTER='synthetic',SYNTHETIC_CALLED=str(Path(self.tmp.name)/'called'))
  for key in ('DATA_URI','CKPT_URI','CKPT_ARG','NODES','V2_PROOF_FILE'):self.env.pop(key,None)
 def tearDown(self):self.tmp.cleanup()
 def stage(self,name,**env):
  return subprocess.run(['bash',str(self.root/'azure/sft/submit_sft.sh'),name,str(self.data),'300','synthetic','--max-steps','1'],cwd=self.root,env=dict(self.env,**env),text=True,capture_output=True)
 def test_legacy_copy_and_manifest(self):
  r=self.stage('hrsft-legacy');self.assertEqual(r.returncode,0,r.stderr)
  a=self.root/'azure/attempts/hrsft-legacy';job=yaml.safe_load((a/'job.yml').read_text())
  self.assertIn('--data data',job['command']);self.assertEqual(job['inputs'].keys(),{'model_snapshot'})
  for name in FILES:self.assertEqual((a/'code/data'/name).read_bytes(),(self.data/name).read_bytes())
  self.assertEqual(subprocess.run(['shasum','-a','256','-c',str(a/'code-manifest.sha256')],cwd=a/'code',capture_output=True).returncode,0)
  self.assertFalse(Path(self.env['SYNTHETIC_CALLED']).exists())
 def test_mount_snapshot_yaml_checkpoint_ddp_and_hashes(self):
  r=self.stage('hrsft-mount',DATA_URI=URI,CKPT_URI='azureml://synthetic/checkpoint/',CKPT_ARG='--adapter',NODES='2');self.assertEqual(r.returncode,0,r.stderr)
  a=self.root/'azure/attempts/hrsft-mount';job=yaml.safe_load((a/'job.yml').read_text())
  self.assertEqual(job['inputs']['sft_data'],{'type':'uri_folder','path':URI,'mode':'ro_mount'})
  self.assertEqual(job['inputs']['ckpt']['mode'],'ro_mount');self.assertIn('--adapter ${{inputs.ckpt}}',job['command'])
  self.assertIn('bash azure/sft/verify_sft_data.sh "${{inputs.sft_data}}"',job['command']);self.assertIn('--data "${{inputs.sft_data}}"',job['command'])
  self.assertEqual(job['resources']['instance_count'],2);self.assertEqual(job['distribution']['process_count_per_instance'],1)
  self.assertFalse((a/'code/data').exists())
  self.assertLess(sum(f.stat().st_size for f in (a/'code').rglob('*') if f.is_file()),2_000_000)
  for f in (a/'code').rglob('*'):
   if f.is_file():
    for name in FILES:self.assertNotIn((self.data/name).read_bytes(),f.read_bytes())
  pins=(a/'code/data.sha256').read_text().splitlines();self.assertEqual(len(pins),4)
  for name,line in zip(FILES,pins):self.assertEqual(line,hashlib.sha256((self.data/name).read_bytes()).hexdigest()+'  '+name)
  self.assertEqual(subprocess.run(['shasum','-a','256','-c',str(a/'code-manifest.sha256')],cwd=a/'code',capture_output=True).returncode,0)
  self.assertFalse(Path(self.env['SYNTHETIC_CALLED']).exists())
 def test_v2_proof_is_staged_and_pinned_without_dataset_bytes(self):
  proof=Path(self.tmp.name)/'proof.json';proof.write_text('{"schema":"synthetic"}')
  (self.root/'azure/sft/validate_v2_data.py').write_text('import sys\nassert "--data" in sys.argv and "--proof" in sys.argv and "--serializer" in sys.argv\nprint("synthetic")\n')
  r=self.stage('hrsft-v2-proof',DATA_URI=URI,V2_PROOF_FILE=str(proof));self.assertEqual(r.returncode,0,r.stderr)
  a=self.root/'azure/attempts/hrsft-v2-proof';job=yaml.safe_load((a/'job.yml').read_text())
  proof_sha=hashlib.sha256(proof.read_bytes()).hexdigest()
  self.assertIn('--proof-binding v2-proof.json --proof-binding-sha256 '+proof_sha,job['command'])
  self.assertEqual((a/'code/v2-proof.json').read_bytes(),proof.read_bytes());self.assertFalse((a/'code/data').exists())
  for f in (a/'code').rglob('*'):
   if f.is_file():
    for name in FILES:self.assertNotIn((self.data/name).read_bytes(),f.read_bytes())
  self.assertEqual(job['inputs']['sft_data']['mode'],'ro_mount')
  self.assertEqual(subprocess.run(['shasum','-a','256','-c',str(a/'code-manifest.sha256')],cwd=a/'code',capture_output=True).returncode,0)
  self.assertFalse(Path(self.env['SYNTHETIC_CALLED']).exists())
 def test_failed_v2_readback_prevents_staging(self):
  proof=Path(self.tmp.name)/'proof.json';proof.write_text('{"schema":"synthetic"}')
  (self.root/'azure/sft/validate_v2_data.py').write_text('raise SystemExit(2)\n')
  r=self.stage('hrsft-v2-rejected',DATA_URI=URI,V2_PROOF_FILE=str(proof))
  self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'azure/attempts/hrsft-v2-rejected').exists())
 def test_direct_proof_override_rejected(self):
  r=subprocess.run(['bash',str(self.root/'azure/sft/submit_sft.sh'),'hrsft-override',str(self.data),'300','synthetic','--proof-binding','override'],cwd=self.root,env=self.env,text=True,capture_output=True)
  self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'azure/attempts/hrsft-override').exists())
 def test_mount_resume_and_twelve_node_yaml_are_preserved(self):
  r=self.stage('hrsft-mount-resume',DATA_URI=URI,CKPT_URI='azureml://synthetic/checkpoint/',CKPT_ARG='--resume-from',NODES='12');self.assertEqual(r.returncode,0,r.stderr)
  job=yaml.safe_load((self.root/'azure/attempts/hrsft-mount-resume/job.yml').read_text())
  self.assertEqual(job['resources']['instance_count'],12);self.assertIn('--resume-from ${{inputs.ckpt}}',job['command'])
  self.assertEqual(job['inputs']['sft_data']['mode'],'ro_mount');self.assertEqual(job['inputs']['ckpt']['mode'],'ro_mount')
 def test_invalid_uri_or_missing_file_fails_before_staging(self):
  bad=['','https://example/x/','azureml://datastores/other/paths/pokemon-rl-research/a/',URI+'extra',URI+'../',URI+'x/../../',URI+'x y/',URI+'x\ny/','azureml://datastores/workspaceblobstore/paths/pokemon-rl-research//']
  for i,uri in enumerate(bad):
   name='hrsft-bad'+str(i);r=self.stage(name,DATA_URI=uri)
   self.assertNotEqual(r.returncode,0,repr(uri));self.assertFalse((self.root/'azure/attempts'/name).exists())
  (self.data/'eval.jsonl.gz').unlink();r=self.stage('hrsft-missing',DATA_URI=URI)
  self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'azure/attempts/hrsft-missing').exists());self.assertFalse(Path(self.env['SYNTHETIC_CALLED']).exists())
 def test_helper_checks_four_files_before_python(self):
  r=self.stage('hrsft-verify',DATA_URI=URI);self.assertEqual(r.returncode,0,r.stderr)
  code=self.root/'azure/attempts/hrsft-verify/code';cmd=['bash','azure/sft/verify_sft_data.sh',str(self.data),'--data',str(self.data)]
  def invoke():return subprocess.run(cmd,cwd=code,env=self.env,capture_output=True,text=True)
  marker=Path(self.env['SYNTHETIC_CALLED'])
  for rank in ('0','1'):
   self.env['RANK']=rank;self.assertEqual(invoke().returncode,0);self.assertTrue(marker.exists());marker.unlink()
  for name in FILES:
   p=self.data/name;raw=p.read_bytes();p.write_bytes(raw+b'!');self.assertNotEqual(invoke().returncode,0,name);self.assertFalse(marker.exists());p.write_bytes(raw)
   p.unlink();self.assertNotEqual(invoke().returncode,0,name);self.assertFalse(marker.exists());p.write_bytes(raw)
  for args in (['--data','/different'],['--data',str(self.data),'--data','/different'],['--data='+str(self.data)]):
   rejected=subprocess.run(['bash','azure/sft/verify_sft_data.sh',str(self.data),*args],cwd=code,env=self.env,capture_output=True)
   self.assertNotEqual(rejected.returncode,0);self.assertFalse(marker.exists())
  pin=code/'data.sha256';pin.write_text(pin.read_text()+'garbage\n');self.assertNotEqual(invoke().returncode,0);self.assertFalse(marker.exists())
if __name__=='__main__':unittest.main()
