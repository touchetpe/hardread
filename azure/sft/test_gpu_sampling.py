import importlib.util,os,signal,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
D=Path(__file__).parent
spec=importlib.util.spec_from_file_location('g1_gpu_sampling',D/'run_sft.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
class GpuTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory(dir='/private/tmp');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);M.OUT=self.root;M.RANK=3;M.STATE={}
 def good(self,*a,**kw):
  self.assertEqual(kw['timeout'],5);self.assertIn('utilization.gpu',a[0][1]);return subprocess.CompletedProcess(a[0],0,stdout='2026/10/29 13:00:00.000, 0, GPU-synthetic, 84, 512, 1024, 25.0\n',stderr='')
 def test_initial_sample_rank_path_timeout_and_cleanup(self):
  before=signal.getsignal(signal.SIGTERM)
  with patch.object(M.subprocess,'run',side_effect=self.good):
   with M.NativeGpuSampler(self.root,3) as sampler:self.assertEqual(sampler.samples,1);self.assertTrue((self.root/'gpu-rank3.csv').exists())
  self.assertEqual(signal.getsignal(signal.SIGTERM),before);self.assertTrue(M.STATE['gpu_sampling']['sampler_stopped']);self.assertEqual(M.STATE['gpu_sampling']['status'],'unmeasured');self.assertEqual(M.STATE['gpu_sampling']['interval_seconds'],60)
 def test_unavailable_does_not_mask_workload_error(self):
  with patch.object(M.subprocess,'run',side_effect=FileNotFoundError('synthetic')):
   with self.assertRaisesRegex(RuntimeError,'workload'):
    with M.NativeGpuSampler(self.root,3):raise RuntimeError('workload')
  self.assertEqual(M.STATE['gpu_sampling']['status'],'unmeasured');self.assertEqual(M.STATE['gpu_sampling']['error_type'],'FileNotFoundError');self.assertTrue(M.STATE['gpu_sampling']['sampler_stopped'])
 def test_signal_restores_handlers_and_stops_thread(self):
  before=signal.getsignal(signal.SIGTERM)
  with patch.object(M.subprocess,'run',side_effect=self.good):
   with self.assertRaises(SystemExit):
    with M.NativeGpuSampler(self.root,3) as sampler:sampler._signal(signal.SIGTERM,None)
  self.assertEqual(signal.getsignal(signal.SIGTERM),before);self.assertTrue(M.STATE['gpu_sampling']['sampler_stopped'])
 def test_bounded_file_and_periodic_interval(self):
  with patch.object(M.subprocess,'run',side_effect=self.good):
   sampler=M.NativeGpuSampler(self.root,3);sampler.MAX_BYTES=1
   with sampler:self.assertTrue(sampler.truncated);self.assertEqual(sampler.samples,0)
  self.assertEqual(M.STATE['gpu_sampling']['status'],'unmeasured')
  class E:
   def __init__(self):self.calls=[]
   def wait(self,interval):self.calls.append(interval);return len(self.calls)>1
  sampler=M.NativeGpuSampler(self.root,4);sampler.stop_event=E();samples=[];sampler.sample=lambda periodic=False:samples.append(periodic);sampler.loop();self.assertEqual(sampler.stop_event.calls,[60,60]);self.assertEqual(samples,[True])
 def test_unmeasured_initial_sample_does_not_prevent_training(self):
  from test_ddp_output import args
  a=args();a.resume_v2_second_epoch=True;M.SFT=D;M.LOCAL_OUTPUT=self.root/'local';M.SUFFIX='-rank3'
  with patch.dict(os.environ,{'WORLD_SIZE':'12'}),patch.object(M.subprocess,'run',side_effect=FileNotFoundError('synthetic')),patch.object(M,'sh') as train:
   M._train(a,{'HRSFT_LOSS':'weighted'});train.assert_called_once()
  self.assertEqual(M.STATE['gpu_sampling']['status'],'unmeasured')
 def test_power_unsupported_keeps_gpu_utilization(self):
  def fake(*args,**kwargs):return subprocess.CompletedProcess(args[0],0,stdout='2026/10/29 13:00:00, 0, GPU-synthetic, 84, 512, 1024, [Not Supported]\n',stderr='')
  with patch.object(M.subprocess,'run',side_effect=fake):
   with M.NativeGpuSampler(self.root,3) as sampler:self.assertEqual(sampler.samples,1)
  self.assertTrue((self.root/'gpu-rank3.csv').read_text().strip().endswith(',N/A'))
 def test_initial_only_and_later_sampling_failure_are_unmeasured(self):
  calls=[]
  def initial_then_failure(*args,**kwargs):
   calls.append(1)
   if len(calls)==1:return self.good(*args,**kwargs)
   raise subprocess.TimeoutExpired('nvidia-smi',5)
  with patch.object(M.subprocess,'run',side_effect=initial_then_failure):
   with M.NativeGpuSampler(self.root,3) as sampler:sampler.sample(periodic=True)
  self.assertEqual(M.STATE['gpu_sampling']['samples'],1);self.assertEqual(M.STATE['gpu_sampling']['initial_samples'],1);self.assertEqual(M.STATE['gpu_sampling']['periodic_samples'],0);self.assertEqual(M.STATE['gpu_sampling']['status'],'unmeasured')
 def test_successful_periodic_sample_is_measured(self):
  with patch.object(M.subprocess,'run',side_effect=self.good):
   with M.NativeGpuSampler(self.root,3) as sampler:sampler.sample(periodic=True)
  self.assertEqual(M.STATE['gpu_sampling']['periodic_samples'],1);self.assertEqual(M.STATE['gpu_sampling']['status'],'measured')
 def test_initial_only_training_succeeds_and_is_unmeasured(self):
  from test_ddp_output import args
  a=args();a.resume_v2_second_epoch=True;M.SFT=D;M.LOCAL_OUTPUT=self.root/'local';M.SUFFIX='-rank3'
  with patch.dict(os.environ,{'WORLD_SIZE':'12'}),patch.object(M.subprocess,'run',side_effect=self.good),patch.object(M,'sh'):
   result=M._train(a,{'HRSFT_LOSS':'weighted'});self.assertEqual(result['steps'],0)
  self.assertEqual(M.STATE['gpu_sampling']['status'],'unmeasured')
 def test_periodic_gaps_are_disclosed_as_partial(self):
  calls=[]
  def some_fail(*args,**kwargs):
   calls.append(1)
   if len(calls)==3:raise subprocess.TimeoutExpired('nvidia-smi',5)
   return self.good(*args,**kwargs)
  with patch.object(M.subprocess,'run',side_effect=some_fail):
   with M.NativeGpuSampler(self.root,3) as sampler:sampler.sample(periodic=True);sampler.sample(periodic=True)
  status=M.STATE['gpu_sampling'];self.assertEqual(status['status'],'partial');self.assertEqual((status['periodic_attempts'],status['periodic_failures'],status['periodic_samples']),(2,1,1))
 def test_timeout_is_unmeasured(self):
  with patch.object(M.subprocess,'run',side_effect=subprocess.TimeoutExpired('nvidia-smi',5)):
   with M.NativeGpuSampler(self.root,3):pass
  self.assertEqual(M.STATE['gpu_sampling']['error_type'],'TimeoutExpired');self.assertEqual(M.STATE['gpu_sampling']['status'],'unmeasured')
if __name__=='__main__':unittest.main()
