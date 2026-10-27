import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'azure/sft/run_sft.py'
spec = importlib.util.spec_from_file_location('sft_repair', SOURCE)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def args():
    return SimpleNamespace(snapshot='/snapshot', data='/data', max_steps=None, micro_batch=4, grad_accum=2,
                           lr=None, save_steps=200, epochs=1, lora_r=None, lora_alpha=None,
                           warmup_steps=None, resume_from=None, loss='weighted', train_timeout=100)


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); M.OUT = self.root / 'out'; M.OUT.mkdir()
        M.STATE = {'stages': {}}; M.SFT = ROOT / 'azure/sft'

    def local_tree(self):
        d = self.root / 'local'; d.mkdir()
        for name in ('adapter_model.safetensors', 'adapter_config.json', 'tokenizer.json', 'tokenizer_config.json'):
            (d / name).write_bytes(('data-' + name).encode())
        ck = d / 'checkpoint-200'; ck.mkdir(); (ck / 'optimizer.pt').write_bytes(b'optimizer')
        return d

    def test_resolved_recipe_is_weighted_and_rank_local(self):
        import yaml
        cfgs = []
        with patch.dict(os.environ, {'WORLD_SIZE': '6'}), patch.object(M, 'sh'):
            for rank in (0, 2):
                M.RANK = rank; M.SUFFIX = '' if rank == 0 else '-rank2'
                M.LOCAL_OUTPUT = self.root / f'local-{rank}'
                M._train(args(), {'HRSFT_LOSS': 'weighted'})
                cfgs.append(yaml.safe_load((M.OUT / f'config.resolved{M.SUFFIX}.yaml').read_text()))
        self.assertNotEqual(cfgs[0]['output_dir'], cfgs[1]['output_dir'])
        for c in cfgs:
            self.assertEqual((c['micro_batch_size'], c['gradient_accumulation_steps'], c['save_steps'], c['num_epochs']), (4,2,200,1))
            self.assertEqual(c['save_total_limit'], 3)
            self.assertFalse(c['cut_cross_entropy']); self.assertFalse(c['auto_resume_from_checkpoints'])
            self.assertIn('sft_plugin.HardReadSFTPlugin', c['plugins'])

    def test_persistence_verifies_all_files_and_marker_last(self):
        d = self.local_tree(); result = M.persist_local_output(d)
        marker = result / 'verified-persistence.json'
        self.assertTrue(marker.is_file()); data = json.loads(marker.read_text())
        self.assertEqual(len(data['files']), 5)
        self.assertEqual((result / 'adapter_model.safetensors').read_bytes(), (d / 'adapter_model.safetensors').read_bytes())
        self.assertEqual(M.STATE['verified_persisted_adapter'], str(result))
        self.assertEqual(M.STATE['local_output_bytes'], sum(x['bytes'] for x in data['files']))
        self.assertGreater(M.STATE['local_free_bytes_at_persistence'], 0)

    def test_real_train_stage_records_eio(self):
        M.RANK=2; M.SUFFIX='-rank2'; M.LOCAL_OUTPUT=self.root/'local'
        with patch.dict(os.environ, {'WORLD_SIZE':'6'}), patch.object(M, 'sh', side_effect=OSError(5, 'synthetic EIO')):
            self.assertIsNone(M.train(args(), {'HRSFT_LOSS':'weighted'}))
        self.assertEqual(M.STATE['stages']['train']['status'], 'failed')
        self.assertIn('EIO', M.STATE['stages']['train']['error'])

    def test_partial_copy_and_corruption_never_mark_verified(self):
        d = self.local_tree(); original = M.shutil.copyfile; calls = []
        def interrupted(src, dst):
            calls.append(src)
            if len(calls) == 2: raise OSError('synthetic EIO')
            return original(src, dst)
        with patch.object(M.shutil, 'copyfile', side_effect=interrupted):
            with self.assertRaises(OSError): M.persist_local_output(d)
        self.assertEqual(list(M.OUT.rglob('verified-persistence.json')), [])
        with patch.object(M.shutil, 'copyfile', side_effect=lambda src,dst: Path(dst).write_bytes(b'bad')):
            with self.assertRaises(OSError): M.persist_local_output(d)
        self.assertEqual(list(M.OUT.rglob('verified-persistence.json')), [])

    def test_checkpoint_only_adapter_cannot_replace_missing_final_adapter(self):
        d = self.local_tree()
        for name in ('adapter_model.safetensors', 'adapter_config.json'):
            (d / name).rename(d / 'checkpoint-200' / name)
        self.assertEqual(M.find_adapter(d), d / 'checkpoint-200')  # legacy single-node discovery
        with self.assertRaises(FileNotFoundError): M.persist_local_output(d)
        self.assertEqual(list(M.OUT.rglob('verified-persistence.json')), [])

    def test_more_than_three_checkpoints_and_missing_tokenizer_fail(self):
        d = self.local_tree()
        for n in (400, 600, 800): (d / f'checkpoint-{n}').mkdir()
        with self.assertRaisesRegex(ValueError, 'too many'): M.persist_local_output(d)
        (d / 'checkpoint-800').rmdir(); (d / 'tokenizer.json').unlink()
        with self.assertRaises(FileNotFoundError): M.persist_local_output(d)

    def test_wait_success_failure_corrupt_and_timeout(self):
        M.STATE = {'started_unix': 100, 'args': {'job_timeout': 200}}
        with patch.object(M.time, 'time', return_value=110):
            marker = M.OUT / 'rank0-terminal.json'
            for status, expected in [('success', True), ('failure', False)]:
                marker.write_text(json.dumps({'status':status})); self.assertEqual(M.wait_for_rank0(), expected)
            marker.write_text('{'); self.assertFalse(M.wait_for_rank0())
        marker.unlink()
        with patch.object(M.time, 'time', return_value=180): self.assertFalse(M.wait_for_rank0())

    def test_rank2_train_failure_no_retry_or_wait(self):
        M.RANK=2; M.SUFFIX='-rank2'; a=['run_sft.py','--snapshot','/snapshot','--data','/data','--out',str(M.OUT),'--skip-serve']
        def failed(*_): M.STATE['stages']['train']={'status':'failed','error':'OSError: EIO'}
        with patch.dict(os.environ, {'WORLD_SIZE':'6'}), patch.object(sys,'argv',a), \
             patch.object(M,'preflight',return_value={'ok':True}), patch.object(M,'prepare_local_output',return_value=self.root/'local'), \
             patch.object(M,'train',side_effect=failed), patch.object(M,'train_retry_plain') as plain, \
             patch.object(M,'train_retry_mb1') as mb, patch.object(M,'wait_for_rank0') as wait, \
             patch.object(M,'run_post') as post:
            with self.assertRaises(SystemExit) as e: M.main()
            self.assertNotEqual(e.exception.code,0); plain.assert_not_called(); mb.assert_not_called(); wait.assert_not_called(); post.assert_not_called()

    def test_rank0_failure_or_persistence_failure_skips_eval(self):
        M.RANK=0; M.SUFFIX=''; a=['run_sft.py','--snapshot','/snapshot','--data','/data','--out',str(M.OUT),'--skip-serve']
        for train_status in ('failed','ok'):
            M.STATE={'stages':{}}
            def train(*_): M.STATE['stages']['train']={'status':train_status}
            with patch.dict(os.environ, {'WORLD_SIZE':'6'}), patch.object(sys,'argv',a), \
                 patch.object(M,'preflight',return_value={'ok':True}), patch.object(M,'prepare_local_output',return_value=self.root/'local'), \
                 patch.object(M,'train',side_effect=train), patch.object(M,'persist_local_output',side_effect=OSError('EIO')), \
                 patch.object(M,'run_post') as post:
                with self.assertRaises(SystemExit) as e: M.main()
                self.assertNotEqual(e.exception.code,0); post.assert_not_called()

    def test_rank0_persists_before_eval_and_post_failure_is_nonzero(self):
        M.RANK=0; M.SUFFIX=''; a=['run_sft.py','--snapshot','/snapshot','--data','/data','--out',str(M.OUT),'--skip-serve']
        events=[]
        def train(*_): M.STATE['stages']['train']={'status':'ok'}
        def persist(_): events.append('persist'); return self.root/'persisted'
        def post(*_): events.append('eval'); M.STATE['stages']['eval_teacher']={'status':'ok'}
        with patch.dict(os.environ, {'WORLD_SIZE':'6'}), patch.object(sys,'argv',a), \
             patch.object(M,'preflight',return_value={'ok':True}), patch.object(M,'prepare_local_output',return_value=self.root/'local'), \
             patch.object(M,'train',side_effect=train), patch.object(M,'persist_local_output',side_effect=persist), \
             patch.object(M,'run_post',side_effect=post):
            M.main(); self.assertEqual(events,['persist','eval'])
        M.STATE={'stages':{}}
        def failed_post(*_): M.STATE['stages']['eval_teacher']={'status':'failed'}
        with patch.dict(os.environ, {'WORLD_SIZE':'6'}), patch.object(sys,'argv',a), \
             patch.object(M,'preflight',return_value={'ok':True}), patch.object(M,'prepare_local_output',return_value=self.root/'local'), \
             patch.object(M,'train',side_effect=train), patch.object(M,'persist_local_output',return_value=self.root/'persisted'), \
             patch.object(M,'run_post',side_effect=failed_post):
            with self.assertRaises(SystemExit) as e: M.main()
            self.assertNotEqual(e.exception.code,0)

    def test_successful_nonzero_rank_propagates_terminal_wait(self):
        M.RANK=2; M.SUFFIX='-rank2'; a=['run_sft.py','--snapshot','/snapshot','--data','/data','--out',str(M.OUT),'--skip-serve']
        for terminal in (True, False):
            M.STATE={'stages':{}}
            def train(*_): M.STATE['stages']['train']={'status':'ok'}
            with patch.dict(os.environ, {'WORLD_SIZE':'6'}), patch.object(sys,'argv',a), \
                 patch.object(M,'preflight',return_value={'ok':True}), patch.object(M,'prepare_local_output',return_value=self.root/'local'), \
                 patch.object(M,'train',side_effect=train), patch.object(M,'wait_for_rank0',return_value=terminal), \
                 patch.object(M,'run_post') as post:
                with self.assertRaises(SystemExit) as e: M.main()
                self.assertEqual(e.exception.code, 0 if terminal else 4); post.assert_not_called()

    def test_local_output_rejects_mount_and_low_disk(self):
        M.RANK=2
        with patch.object(M.subprocess,'run',return_value=SimpleNamespace(stdout='nfs\n')):
            with self.assertRaisesRegex(RuntimeError,'filesystem'): M.prepare_local_output(self.root)
        with patch.object(M.subprocess,'run',return_value=SimpleNamespace(stdout='ext4\n')), \
             patch.object(M.shutil,'disk_usage',return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(RuntimeError,'free'): M.prepare_local_output(self.root)
        with patch.object(M.subprocess,'run',return_value=SimpleNamespace(stdout='ext4\n')), \
             patch.object(M.shutil,'disk_usage',return_value=SimpleNamespace(free=M.MIN_LOCAL_FREE)):
            a=M.prepare_local_output(self.root); b=M.prepare_local_output(self.root)
            self.assertNotEqual(a,b); self.assertFalse(list(a.iterdir()))

    def test_single_process_keeps_weighted_to_plain_retry(self):
        M.RANK=0; M.SUFFIX=''; a=['run_sft.py','--snapshot','/snapshot','--data','/data','--out',str(M.OUT),'--skip-serve']
        def train(*_): M.STATE['stages']['train']={'status':'failed'}
        def plain(*_): M.STATE['stages']['train_retry_plain']={'status':'ok'}
        with patch.dict(os.environ, {'WORLD_SIZE':'1'}), patch.object(sys,'argv',a), \
             patch.object(M,'preflight',return_value={'ok':True}), patch.object(M,'train',side_effect=train), \
             patch.object(M,'train_retry_plain',side_effect=plain) as retry, patch.object(M,'train_retry_mb1') as mb, \
             patch.object(M,'find_adapter',return_value=self.root), patch.object(M,'run_post'):
            M.main(); retry.assert_called_once(); mb.assert_not_called()
            self.assertEqual(M.STATE['loss_fallback'],'weighted -> plain')

    def test_terminal_entrypoint_maps_failure_and_success(self):
        M.RANK=0
        with patch.dict(os.environ, {'WORLD_SIZE':'6'}):
            with patch.object(M,'main',side_effect=SystemExit(3)):
                with self.assertRaises(SystemExit): M.entrypoint()
                self.assertEqual(json.loads((M.OUT/'rank0-terminal.json').read_text())['status'],'failure')
            with patch.object(M,'main',return_value=None): M.entrypoint()
            self.assertEqual(json.loads((M.OUT/'rank0-terminal.json').read_text())['status'],'success')

if __name__ == '__main__': unittest.main()
