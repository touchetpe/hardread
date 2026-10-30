import gzip, io, json, os, pathlib, signal, socket, subprocess, sys, tempfile, time, unittest
from unittest import mock
import fetch_saved_increment as m
class Clock:
    def __init__(self): self.now=0
    def clock(self): return self.now
    def sleep(self,n): self.now+=n
class Response:
    def __init__(self,status,body=b'',headers=None): self.status=status; self.body=io.BytesIO(body); self.headers=headers or {}
    def read(self,n): return self.body.read(n)
    def __enter__(self): return self
    def __exit__(self,*args): pass
class Opener:
    def __init__(self,clock,responses): self.clock=clock; self.responses=iter(responses); self.times=[]
    def open(self,req,timeout):
        self.times.append(self.clock.clock()); response=next(self.responses)
        if isinstance(response,Exception): raise response
        return response
def make_http(responses,**kwargs):
    clock=Clock(); opener=Opener(clock,responses)
    obj=m.BoundedHttp(100,pathlib.Path('/not/a/real/stop/file'),opener=opener,clock=clock.clock,sleeper=clock.sleep,**kwargs)
    return obj,opener
class Tests(unittest.TestCase):
    def test_network_denied(self):
        with mock.patch.object(socket.socket,'connect',side_effect=AssertionError('no socket')):
            http,_=make_http([Response(200,b'{}')]); self.assertEqual(http.get('gen9ou-123'),b'{}')
    def test_spacing_retry_and_404(self):
        http,op=make_http([Response(500),Response(500),Response(404)])
        self.assertIsNone(http.get('gen9ou-1')); self.assertEqual(http.attempts,3)
        self.assertTrue(all(b-a>=1.2 for a,b in zip(op.times,op.times[1:])))
        self.assertEqual(http.codes,{'500':2,'404':1})
    def test_immediate_policy_stop(self):
        for code in (403,429,451):
            http,op=make_http([Response(code),Response(200,b'{}')])
            with self.assertRaises(m.Stop): http.get('gen9ou-1')
            self.assertEqual(len(op.times),1)
    def test_chunked_and_wire_exact(self):
        http,_=make_http([Response(200,b'abc')],per_wire=3)
        with self.assertRaises(m.Stop): http.get('gen9ou-1')
        http,_=make_http([Response(200,b'abc',{'Content-Length':'3'})],per_wire=3,max_wire=3,max_decoded=3)
        self.assertEqual(http.get('gen9ou-1'),b'abc'); self.assertEqual((http.wire,http.decoded),(3,3))
        http,_=make_http([Response(200,b'abc')],per_wire=4); self.assertEqual(http.get('gen9ou-1'),b'abc')
    def test_decoded_gzip_exact_cap(self):
        encoded=gzip.compress(b'x'*10)
        http,_=make_http([Response(200,encoded,{'Content-Encoding':'gzip','Content-Length':str(len(encoded))})],per_decoded=10,max_decoded=10)
        self.assertEqual(http.get('gen9ou-1'),b'x'*10)
    def test_decoded_gzip_limit(self):
        http,_=make_http([Response(200,gzip.compress(b'x'*100),{'Content-Encoding':'gzip'})],per_decoded=10)
        with self.assertRaises(m.Stop): http.get('gen9ou-1')
        self.assertEqual(http.decoded,10)
    def test_cumulative_and_attempt_limit(self):
        http,_=make_http([Response(200,b'abc',{'Content-Length':'3'})],max_wire=3,max_decoded=3,max_requests=1)
        self.assertEqual(http.get('gen9ou-1'),b'abc')
        with self.assertRaises(m.Stop): http.get('gen9ou-2')
    def test_exhausted_budget_never_opens(self):
        for kind in ('wire','decoded','requests'):
            http,op=make_http([Response(200,b'{}')])
            if kind=='wire': http.wire=http.max_wire
            if kind=='decoded': http.decoded=http.max_decoded
            if kind=='requests': http.attempts=http.max_requests
            with self.assertRaises(m.Stop): http.get('gen9ou-1')
            self.assertEqual(op.times,[])
    def test_retry_stops_after_cumulative_wire_exhaustion(self):
        http,op=make_http([Response(500,b'abc',{'Content-Length':'3'}),Response(200,b'{}')],max_wire=3)
        with self.assertRaises(m.Stop): http.get('gen9ou-1')
        self.assertEqual((len(op.times),http.attempts,http.wire),(1,1,3))
    def test_deadline_in_retry(self):
        http,op=make_http([Response(500),Response(200,b'{}')]); http.deadline=.5
        with self.assertRaises(m.Stop): http.get('gen9ou-1')
        self.assertEqual(len(op.times),1)
    def test_response_filters_and_hash_identity(self):
        row={'id':'gen9ou-1','formatid':'gen9ou','uploadtime':m.SINCE,'log':'test-log','players':['known','human']}
        def check(changes,expected,accounts=()):
            d=dict(row,**changes); obj,code=m.accept_body(json.dumps(d).encode(),'gen9ou-1','top500',m.SINCE+1,set(),set(),set(accounts)); self.assertEqual(code,expected); return obj
        check({'private':True},'private');check({'password':'x'},'private');check({'formatid':'other'},'format');check({'id':'gen9ou-2'},'id_mismatch');check({'uploadtime':m.SINCE-1},'date')
        check({},'known_account',[m.account_hash('known')]);self.assertEqual(check({},'accepted')['participant_type'],'unverified')
        _,code=m.accept_body(json.dumps(row).encode(),'gen9ou-1','global',m.SINCE+1,set(),{m.sha(b'test-log')},set());self.assertEqual(code,'duplicate_log')
    def test_atomic_output_cap_and_conflict(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as t:
            root=pathlib.Path(t); row={'replay':{'id':'gen9ou-1'}}
            with mock.patch.object(m,'MAX_OUTPUT',2097152):
                with self.assertRaises(m.Stop): m.commit_record(root,1,row)
                self.assertEqual(list(root.iterdir()),[])
            m.commit_record(root,1,row)
            with self.assertRaises((FileExistsError,m.Stop)): m.commit_record(root,1,row)
    def test_notice(self):
        with self.assertRaises(m.Stop): m.accept_body(b'{"error":"private details"}','gen9ou-1','global',m.SINCE+1,set(),set(),set())
    def test_cap_status_code_accounting(self):
        http,_=make_http([Response(500,b'xyz'),Response(404,b'xy')])
        self.assertIsNone(http.get('gen9ou-1')); self.assertEqual((http.wire,http.decoded),(5,5))
class OfflineTests(unittest.TestCase):
    def fixture(self,base):
        top=base/'top'; broad=base/'broad'; top.mkdir(); broad.mkdir(); (broad/'search_top500').mkdir()
        users=[{'userid':'person'+str(i)} for i in range(500)]
        (top/'ladder_top50.json').write_text(json.dumps({'top50':users[:50]}))
        (broad/'ladder_top500.json').write_text(json.dumps({'toplist':users}))
        (broad/'meta.json').write_text(json.dumps({'t_end':m.SINCE+100,'since':m.SINCE,'n_slots':2400}))
        baseline={'id':'gen9ou-1','rating':1500,'log':'historic'}
        for d in (top,broad):
            with gzip.open(d/'replays-0000.jsonl.gz','wt') as f: f.write(json.dumps(baseline)+'\n')
        (broad/'index.jsonl').write_text(json.dumps({'id':'gen9ou-1'})+'\n')
        for u in users[50:]:
            rows=[] if u!=users[50] else [{'id':'gen9ou-2','uploadtime':m.SINCE+1,'rating':1100,'private':False,'password':False}]
            (broad/'search_top500'/(u['userid']+'.json')).write_text(json.dumps({'userid':u['userid'],'results':rows}))
        pages=[{'slot':i,'rows':([] if i else [{'id':'gen9ou-3','uploadtime':m.SINCE+1,'rating':1100,'private':False,'password':False}])} for i in range(2400)]
        (broad/'global_pages.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in pages))
        accounts=base/'accounts.json'; accounts.write_text(json.dumps({'schema':'hardread-known-player-exclusion-hashes/1','domain':'hardread-player-exclusion-v1','normalizer':'ShowdownASCIIalnumlower','digests':['a'*64,'b'*64]}))
        own=base/'own.jsonl'; own.write_text(json.dumps({'battle_id':'battle-gen9ou-2'})+'\n')
        return top,broad,accounts,own
    def test_verify_never_calls_network_and_preserves_baselines(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as t, mock.patch.object(socket.socket,'connect',side_effect=AssertionError('network')):
            top,broad,accounts,own=self.fixture(pathlib.Path(t)); before={str(p):m.streamhash(p) for p in m.source_files(top,broad,accounts,own)}
            result=m.verify(top,broad,accounts,own)
            self.assertEqual(result['counts']['top_candidates'],0); self.assertEqual(result['counts']['global_candidates_upper'],1)
            self.assertEqual(before,{str(p):m.streamhash(p) for p in m.source_files(top,broad,accounts,own)})
            encoded=json.dumps(result); self.assertNotIn('person50',encoded); self.assertNotIn('gen9ou-2',encoded)
            (broad/'search_top500'/'person50.json').unlink()
            with self.assertRaises(ValueError): m.verify(top,broad,accounts,own)
    def test_stdout_and_exception_redaction(self):
        import subprocess,sys
        with tempfile.TemporaryDirectory(dir='/private/tmp',prefix='private-player-') as t:
            bad=pathlib.Path(t)/'https-private-identity'; root=pathlib.Path(t)
            r=subprocess.run([sys.executable,'fetch_saved_increment.py','verify-offline','--baseline-top',str(bad),'--baseline-broad',str(bad),'--accounts',str(bad),'--own-index',str(bad),'--out-dir',str(bad),'--input-manifest',str(root/'manifest')],capture_output=True,text=True)
            self.assertNotIn(str(bad),r.stdout+r.stderr); self.assertNotIn('https-private-identity',r.stdout+r.stderr)
            r=subprocess.run([sys.executable,'fetch_saved_increment.py','https://example.invalid/private-player'],capture_output=True,text=True)
            self.assertNotIn('private-player',r.stdout+r.stderr);self.assertNotIn('https://',r.stdout+r.stderr)
    def test_resume_reconstruct_and_partial_hold(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as t:
            out=pathlib.Path(t); manifest_hash='c'*64
            (out/'session.json').write_text(json.dumps({'input_manifest_sha256':manifest_hash,'start_unix':1}))
            row={'replay':{'id':'gen9ou-4','log':'resume-log'},'participant_type':'unverified','route':'global'}
            m.commit_record(out,1,row); file=out/'000001.json.gz'
            m.write_receipt(out,{'status':'stopped','inputs_unchanged':True,'counts':{'accepted':1},'records':[{'ordinal':1,'bytes':file.stat().st_size,'sha256':m.streamhash(file)[1]}],'attempts':5,'wire_bytes':10,'decoded_bytes':10,'codes':{}})
            session,receipt,records=m.scan_existing(out,manifest_hash); self.assertEqual((receipt['attempts'],len(records)),(5,1))
            receipt['reason']='policy_status'; (out/'receipt-0001.json').write_text(json.dumps(receipt))
            with self.assertRaises(m.Stop):m.scan_existing(out,manifest_hash)
            receipt['reason']='complete'; (out/'receipt-0001.json').write_text(json.dumps(receipt))
            (out/'000002.part').write_bytes(b'partial')
            with self.assertRaises(m.Stop):m.scan_existing(out,manifest_hash)
    def test_policy_worker_nonzero_with_numeric_receipt(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as t:
            base=pathlib.Path(t); top,broad,accounts,own=self.fixture(base)
            own.write_text(json.dumps({'battle_id':'battle-gen9ou-9'})+'\n');out=base/'out';out.mkdir();(out/'session.json').write_text('{}')
            class FakeHttp:
                def __init__(self,*args): self.attempts=1;self.wire=0;self.decoded=0;self.codes={'403':1}
                def check(self): pass
                def get(self,rid): raise m.Stop('policy_status')
            class Args: pass
            a=Args();a.baseline_top=top;a.baseline_broad=broad;a.accounts=accounts;a.own_index=own;a.out_dir=out;a.stop_file=base/'stop';a.input_manifest=base/'manifest'
            verified=m.verify(top,broad,accounts,own);a.input_manifest.write_text(json.dumps(verified))
            with mock.patch.object(m,'BoundedHttp',FakeHttp): self.assertEqual(m.worker(a,100,verified,{},[]),2)
            receipt=json.loads((out/'receipt-0001.json').read_text());self.assertEqual(receipt['reason'],'policy_status');self.assertEqual(receipt['codes'],{'403':1})
    def test_policy_subprocess_exit_is_nonzero_with_reason(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as t:
            base=pathlib.Path(t);top,broad,accounts,own=self.fixture(base)
            own.write_text(json.dumps({'battle_id':'battle-gen9ou-9'})+'\n');out=base/'out';manifest=base/'manifest'
            manifest.write_text(json.dumps(m.verify(top,broad,accounts,own),sort_keys=True)+'\n')
            program="""import pathlib,sys
import fetch_saved_increment as m
class Http:
 def __init__(self,*a): self.attempts=0;self.wire=0;self.decoded=0;self.codes={}
 def check(self): pass
 def get(self,rid): self.attempts+=1;self.codes['403']=1;raise m.Stop('policy_status')
m.BoundedHttp=Http
class Args: pass
a=Args();a.baseline_top=pathlib.Path(sys.argv[1]);a.baseline_broad=pathlib.Path(sys.argv[2]);a.accounts=pathlib.Path(sys.argv[3]);a.own_index=pathlib.Path(sys.argv[4]);a.out_dir=pathlib.Path(sys.argv[5]);a.input_manifest=pathlib.Path(sys.argv[6]);a.stop_file=pathlib.Path(sys.argv[7]);a.resume=False
sys.exit(m.run(a))
"""
            proc=subprocess.run([sys.executable,'-c',program,str(top),str(broad),str(accounts),str(own),str(out),str(manifest),str(base/'stop')],capture_output=True,text=True,timeout=5)
            self.assertNotEqual(proc.returncode,0); receipt=json.loads((out/'receipt-0001.json').read_text())
            self.assertEqual((receipt['reason'],receipt['attempts']),('policy_status',1));self.assertEqual(receipt['codes'],{'403':1})
            with self.assertRaises(m.Stop):m.scan_existing(out,m.streamhash(manifest)[1])
    def test_worker_deduplicates_logs_and_keeps_baselines(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as t:
            base=pathlib.Path(t); top,broad,accounts,own=self.fixture(base)
            own.write_text(json.dumps({'battle_id':'battle-gen9ou-9'})+'\n')
            out=base/'out';out.mkdir(); (out/'session.json').write_text('{}')
            class FakeHttp:
                def __init__(self,*args): self.attempts=0;self.wire=0;self.decoded=0;self.codes={}
                def check(self): pass
                def get(self,rid):
                    self.attempts+=1
                    row={'id':rid,'formatid':'gen9ou','uploadtime':m.SINCE+1,'players':['a','b'],'rating':1100,'log':'historic' if rid=='gen9ou-2' else 'fresh'}
                    return json.dumps(row).encode()
            class Args: pass
            a=Args();a.baseline_top=top;a.baseline_broad=broad;a.accounts=accounts;a.own_index=own;a.out_dir=out;a.stop_file=base/'stop'
            before=m.verify(top,broad,accounts,own); a.input_manifest=base/'manifest'; a.input_manifest.write_text(json.dumps(before))
            with mock.patch.object(m,'BoundedHttp',FakeHttp),mock.patch.object(socket.socket,'connect',side_effect=AssertionError('network')):
                self.assertEqual(m.worker(a,100,before,{},[]),0)
            receipt=json.loads((out/'receipt-0001.json').read_text())
            self.assertEqual(receipt['counts']['accepted'],1); self.assertEqual(receipt['counts']['duplicate_log'],1);self.assertTrue(receipt['inputs_unchanged'])
            self.assertEqual(len(list(out.glob('*.json.gz'))),1)
    def test_supervisor_grace_and_forced_kill_are_nonzero(self):
        for mode in ('cooperative','forced'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir='/private/tmp') as t:
                base=pathlib.Path(t); top,broad,accounts,own=self.fixture(base)
                own.write_text(json.dumps({'battle_id':'battle-gen9ou-9'})+'\n')
                manifest=base/'manifest'; manifest.write_text(json.dumps(m.verify(top,broad,accounts,own),sort_keys=True)+'\n')
                out=base/'out'; marker=base/'worker-pid'
                program="""import os,pathlib,time,sys
import fetch_saved_increment as m
m.TERM_GRACE=.3
class Http:
 def __init__(self,*a): self.attempts=0;self.wire=0;self.decoded=0;self.codes={}
 def check(self): pass
 def get(self,rid):
  self.attempts+=1;self.wire=13;self.decoded=9;self.codes['200']=1;pathlib.Path(sys.argv[7]).write_text(str(os.getpid()))
  time.sleep(.1 if sys.argv[9]=='cooperative' else 30)
  return None
m.BoundedHttp=Http
class Args: pass
a=Args();a.baseline_top=pathlib.Path(sys.argv[1]);a.baseline_broad=pathlib.Path(sys.argv[2]);a.accounts=pathlib.Path(sys.argv[3]);a.own_index=pathlib.Path(sys.argv[4]);a.out_dir=pathlib.Path(sys.argv[5]);a.input_manifest=pathlib.Path(sys.argv[6]);a.stop_file=pathlib.Path(sys.argv[8]);a.resume=False
sys.exit(m.run(a))
"""
                proc=subprocess.Popen([sys.executable,'-c',program,str(top),str(broad),str(accounts),str(own),str(out),str(manifest),str(marker),str(base/'stop'),mode],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:
                    end=time.monotonic()+5
                    while not marker.exists() and time.monotonic()<end: time.sleep(.01)
                    self.assertTrue(marker.exists()); pid=int(marker.read_text())
                    os.kill(proc.pid,signal.SIGTERM); self.assertNotEqual(proc.wait(timeout=5),0)
                    with self.assertRaises(ProcessLookupError): os.kill(pid,0)
                    receipt=json.loads(sorted(out.glob('receipt-*.json'))[-1].read_text())
                    if mode=='cooperative':
                        self.assertEqual(receipt['reason'],'external_stop');self.assertEqual((receipt['attempts'],receipt['wire_bytes'],receipt['decoded_bytes']),(1,13,9));self.assertEqual(receipt['codes'],{'200':1})
                    else:
                        self.assertEqual(receipt['reason'],'forced_kill');self.assertEqual(receipt['accounting'],'unknown');self.assertNotIn('attempts',receipt)
                finally:
                    if proc.poll() is None: proc.kill();proc.wait()
    def test_existing_output_fails_closed_without_network(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as t:
            top,broad,accounts,own=self.fixture(pathlib.Path(t)); out=pathlib.Path(t)/'out';out.mkdir(); (out/'000001.part').write_text('partial')
            class Args: pass
            a=Args(); a.baseline_top=top;a.baseline_broad=broad;a.out_dir=out;a.resume=False
            with mock.patch.object(socket.socket,'connect',side_effect=AssertionError('network')):
                with self.assertRaises(m.Stop):m.run(a)
if __name__=='__main__': unittest.main()
