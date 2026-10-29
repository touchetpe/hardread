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
    def test_data_http_error_is_minimal_and_does_not_read_body(self):
        pins=json.loads(pathlib.Path('mac-cp313-arm64.data.json').read_text())['data_inputs']['pins']
        first_url=pins[0]['primary_source_url_derived_from_static_code']
        class UnreadBody:
            reads=0
            def read(self,*args):
                self.reads+=1
                raise AssertionError('HTTP error body must not be read')
            def close(self):
                pass
        body=UnreadBody()
        error=acquire.HTTPError(first_url,403,'Forbidden',{},body)
        class ErrorOpener:
            def __init__(self): self.calls=[]
            def open(self,request,timeout=20):
                self.calls.append(request.full_url)
                raise error
        opener=ErrorOpener()
        closure=json.loads(pathlib.Path('mac-cp313-arm64.data.json').read_text())
        with tempfile.TemporaryDirectory() as t:
            run=pathlib.Path(t)
            with mock.patch.object(acquire,'build_opener',return_value=opener):
                with self.assertRaises(RuntimeError) as raised:
                    acquire.acquire_data(run,closure)
            self.assertEqual(str(raised.exception),f'data source HTTP 403: {first_url}')
            self.assertIsNone(raised.exception.__cause__)
            self.assertEqual(opener.calls,[first_url])
            self.assertEqual(body.reads,0)
            failure=json.loads((run/'data-http-failure.private.json').read_text())
            self.assertEqual(failure,{'stage':'data','url':first_url,'status':403,'index':1})
            self.assertFalse((run/'data-inputs.private.json').exists())
            self.assertFalse(any((run/'hardread'/pin['cache_path']).exists() for pin in pins))


class OwnerLocalTests(unittest.TestCase):
    def make_bundle(self, base):
        bundle=base/'bundle'; bundle.mkdir()
        pins=[]; rows=[]
        for i in range(4):
            name=f'file{i}.json'; body=json.dumps({'v':i}).encode(); (bundle/name).write_bytes(body)
            path=f'foul-play/fp/data/cache{i}/item.json'; pins.append({'cache_path':path})
            rows.append({'cache_path':path,'relative_file':name,'size_bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'source':'synthetic','capture_date':'2026-10-29','permission_reference':'fake'})
        obj={'schema':'hardread-owner-cache-bundle-v1','provenance_record_id':'synthetic','snapshot_date':'2026-10-29','files':rows}
        manifest=bundle/'manifest.json'; auth=bundle/'auth.json'
        def update():
            manifest.write_text(json.dumps(obj))
            auth.write_text(json.dumps({'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'scope':'private-local-S6.4-test','decision_reference':'synthetic'}))
        update()
        return manifest,auth,{'data_inputs':{'pins':pins}},obj,update
    def test_cache_exact_copy_receipt_and_no_network(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as t:
            base=pathlib.Path(t).resolve(); manifest,auth,closure,obj,_=self.make_bundle(base); run=base/'run'; run.mkdir()
            with mock.patch.object(acquire,'build_opener',side_effect=AssertionError('network')):
                acquire.stage_owner_cache_files(run,manifest,auth,closure)
            for row in obj['files']:
                self.assertEqual((run/'hardread'/row['cache_path']).read_bytes(),(manifest.parent/row['relative_file']).read_bytes())
            receipt=json.loads((run/'owner-cache-receipt.private.json').read_text()); self.assertEqual(len(receipt['files']),4)
            self.assertEqual((run/'owner-cache-receipt.private.json').stat().st_mode & 0o777,0o600)
    def test_package_64_fake_files_and_no_network(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as t:
            base=pathlib.Path(t).resolve(); src=base/'src'; src.mkdir(); run=base/'run'; run.mkdir(); rows=[]
            for group,count in [('wheelhouse',9),('crate-archives',55)]:
                (src/group).mkdir()
                for i in range(count):
                    name=f'{i}.pkg'; body=bytes([i]); (src/group/name).write_bytes(body); rows.append((group,name,1,hashlib.sha256(body).hexdigest()))
            with mock.patch.object(acquire,'package_rows',return_value=rows), mock.patch.object(acquire,'build_opener',side_effect=AssertionError('network')):
                acquire.stage_local_packages(run,src,{})
                self.assertEqual(sum(1 for p in run.rglob('*.pkg')),64)
                (src/'wheelhouse'/'extra').write_bytes(b'x')
                with self.assertRaises(RuntimeError): acquire.stage_local_packages(base/'second',src,{})
    def test_cache_rejections(self):
        for scenario in ('duplicate','duplicate-file','missing','extra','size','hash','empty','malformed','traversal','symlink','destination','noauth','scope','oversize'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(dir='/private/tmp') as t:
                base=pathlib.Path(t).resolve(); manifest,auth,closure,obj,update=self.make_bundle(base); run=base/'run'; run.mkdir(); row=obj['files'][0]
                if scenario=='duplicate': obj['files'][1]['cache_path']=row['cache_path']
                if scenario=='duplicate-file': obj['files'][1]['relative_file']=row['relative_file']
                if scenario=='missing': obj['files'].pop()
                if scenario=='extra': obj['files'].append(dict(row))
                if scenario=='size': row['size_bytes']+=1
                if scenario=='hash': row['sha256']='0'*64
                if scenario=='empty':
                    body=b'{}'; (manifest.parent/row['relative_file']).write_bytes(body); row['size_bytes']=len(body); row['sha256']=hashlib.sha256(body).hexdigest()
                if scenario=='malformed':
                    body=b'{'; (manifest.parent/row['relative_file']).write_bytes(body); row['size_bytes']=len(body); row['sha256']=hashlib.sha256(body).hexdigest()
                if scenario=='traversal': row['relative_file']='../escape'
                if scenario=='symlink':
                    target=manifest.parent/row['relative_file']; target.unlink(); target.symlink_to(manifest.parent/'file1.json')
                if scenario=='destination':
                    dest=run/'hardread'/row['cache_path']; dest.parent.mkdir(parents=True); dest.write_bytes(b'existing')
                if scenario=='oversize': row['size_bytes']=4*1024*1024+1
                update()
                if scenario=='noauth': auth.unlink()
                if scenario=='scope': auth.write_text(json.dumps({'scope':'wrong','manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'decision_reference':'x'}))
                with mock.patch.object(acquire,'build_opener',side_effect=AssertionError('network')):
                    with self.assertRaises((RuntimeError,ValueError,OSError)): acquire.stage_owner_cache_files(run,manifest,auth,closure)
    def test_modes_reject_before_run(self):
        for flags in ([],['--input-mode','live','--owner-data-manifest','/tmp/a'],['--input-mode','owner-local']):
            with tempfile.TemporaryDirectory(dir='/private/tmp') as t:
                run=pathlib.Path(t).resolve()/'run'
                args=['clean_cli.py','--python','/x','--rust-bin','/x','--engine-sdist','/x','--foulplay-source','/x','--replay','/x','--team','/x','--side','p1','--run',str(run)]+flags
                with mock.patch.object(sys,'argv',args), self.assertRaises(SystemExit): driver.main()
                self.assertFalse(run.exists())

if __name__=='__main__': unittest.main()
