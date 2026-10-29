import hashlib, io, json, os, pathlib, subprocess, sys, tarfile, tempfile, time, unittest
from unittest import mock
import acquire, vendor, clean_cli as driver
class Response:
    def __init__(self, data, length=None):
        self.stream=io.BytesIO(data); self.headers={} if length is None else {'Content-Length':str(length)}
    def read(self,n): return self.stream.read(min(n,3))
    def __enter__(self): return self
    def __exit__(self,*args): pass
class Opener:
    def __init__(self, response): self.response=response
    def open(self,*args,**kwargs): return self.response
def archive(entries):
    bio=io.BytesIO()
    with tarfile.open(fileobj=bio,mode='w:gz') as tar:
        for name,kind,body in entries:
            info=tarfile.TarInfo(name)
            if kind=='link': info.type=tarfile.SYMTYPE; info.linkname=body; tar.addfile(info)
            else: info.size=len(body); tar.addfile(info,io.BytesIO(body))
    return bio.getvalue()
class Tests(unittest.TestCase):
    def test_artifact_inventory_and_feeds(self):
        art=json.loads(pathlib.Path('mac-cp313-arm64.artifacts.json').read_text())
        wheels=art['runtime_wheel_candidates']['wheels']+[art['build_tool_candidate']['candidate']]
        crates=art['registry_packages']
        self.assertEqual((len(wheels),len(crates)),(9,55))
        self.assertEqual(sum(x['size_bytes'] for x in wheels)+sum(x['crate_size_bytes'] for x in crates),32411866)
        self.assertTrue(all(len(x['sha256'])==64 for x in wheels))
        self.assertTrue(all(len(x['lock_checksum'])==64 for x in crates))
        pins=json.loads(pathlib.Path('mac-cp313-arm64.data.json').read_text())['data_inputs']['pins']
        self.assertEqual(len(pins),4); self.assertEqual(len({x['cache_path'] for x in pins}),4)
    def test_chunked_exact_and_hash(self):
        data=b'abcde'; got=acquire.fetch_exact(Opener(Response(data)),'https://files.pythonhosted.org/a',len(data),hashlib.sha256(data).hexdigest()); self.assertEqual(got,data)
    def test_cap_and_hash_mismatch(self):
        with self.assertRaises(RuntimeError): acquire.bounded_read(Response(b'12345'),4)
        with self.assertRaises(RuntimeError): acquire.fetch_exact(Opener(Response(b'ab')),'https://files.pythonhosted.org/a',2,'0'*64)
        with self.assertRaises(RuntimeError): acquire.bounded_read(Response(b'abc',5),6)
    def test_safe_tar_and_duplicate(self):
        with tempfile.TemporaryDirectory() as t:
            vendor.extract_exact(archive([('pkg/a','file',b'abc')]),'pkg',pathlib.Path(t)/'ok')
            self.assertEqual((pathlib.Path(t)/'ok/a').read_bytes(),b'abc')
            for i,entries in enumerate([[('pkg/../x','file',b'x')],[('/pkg/x','file',b'x')],[('pkg/x','link','/etc')],[('pkg/x','file',b'x'),('pkg/x','file',b'y')]]):
                with self.assertRaises(RuntimeError): vendor.extract_exact(archive(entries),'pkg',pathlib.Path(t)/str(i))
    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t)/'file'; acquire.write_new(p,b'one')
            with self.assertRaises(RuntimeError): acquire.write_new(p,b'two')
            self.assertEqual(p.read_bytes(),b'one')
    def test_wall_timeout_and_process_cleanup(self):
        with tempfile.TemporaryDirectory() as t:
            t=pathlib.Path(t)
            with self.assertRaises(subprocess.TimeoutExpired): driver.run_stage('test',[sys.executable,'-c','import time; time.sleep(10)'],t,os.environ,time.monotonic()+0.15,t)
    def test_signal_during_spawn_mask_restore_cleans_owned_group(self):
        class FakeProc:
            pid=12345
            def wait(self, timeout=None): return 0
        calls=[]
        def mask(how, sigs):
            calls.append(how)
            if len(calls)==2: raise TimeoutError('pending signal')
            return set()
        with tempfile.TemporaryDirectory() as t, mock.patch.object(driver.signal,'pthread_sigmask',side_effect=mask), mock.patch.object(driver.subprocess,'Popen',return_value=FakeProc()), mock.patch.object(driver.os,'killpg') as kill:
            t=pathlib.Path(t)
            with self.assertRaises(TimeoutError): driver.run_stage('race',['fake'],t,os.environ,time.monotonic()+4,t)
            kill.assert_called_once_with(12345,driver.signal.SIGKILL)
    def test_failed_stage_kills_descendant(self):
        with tempfile.TemporaryDirectory() as t:
            t=pathlib.Path(t); marker=t/'escaped'
            child="import time,pathlib;time.sleep(0.4);pathlib.Path(%r).write_text('bad')" % str(marker)
            parent="import subprocess,sys;subprocess.Popen([sys.executable,'-c',%r]);sys.exit(2)" % child
            with self.assertRaises(RuntimeError): driver.run_stage('failed',[sys.executable,'-c',parent],t,os.environ,time.monotonic()+4,t)
            time.sleep(0.6); self.assertFalse(marker.exists())
    def test_offline_socket(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t)/'exercise.py'; p.write_text('import socket\nsocket.create_connection(("example.com",80))\n')
            r=subprocess.run([sys.executable,'offline_review.py',str(p)],capture_output=True)
            self.assertNotEqual(r.returncode,0); self.assertIn(b'offline review attempted network',r.stderr)
if __name__=='__main__': unittest.main()
