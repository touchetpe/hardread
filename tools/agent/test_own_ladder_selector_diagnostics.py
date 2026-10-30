"""Fake-only diagnostic tests; no real process, lock, runtime, or HTTP access."""
import contextlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH=Path(__file__).with_name('run_own_ladder_selector.py')
spec=importlib.util.spec_from_file_location('selector_diagnostic_candidate',PATH)
c=importlib.util.module_from_spec(spec); spec.loader.exec_module(c)

class PipeEnd:
    def __init__(self, shared): self.shared=shared
    def close(self): pass
    def send_bytes(self, raw): self.shared.append(raw)
    def poll(self, timeout): return bool(self.shared)
    def recv_bytes(self, limit): return self.shared.pop(0)
class FakeProcess:
    def __init__(self,target): self.target=target
    def start(self): self.target()
    def is_alive(self): return False
    def terminate(self): raise AssertionError('not needed')
    def kill(self): raise AssertionError('not needed')
    def join(self, timeout): pass
class FakeContext:
    def Pipe(self, duplex=False):
        shared=[]; return PipeEnd(shared),PipeEnd(shared)
    def Process(self,target): return FakeProcess(target)

def private_failure(): raise ValueError('PRIVATE_PAYLOAD URL PATH TOKEN')

class DiagnosticsTests(unittest.TestCase):
    def test_fake_child_envelope_strips_exception_message(self):
        with patch.object(c.multiprocessing,'get_context',return_value=FakeContext()):
            with self.assertRaises(c.OwnedRequestError) as caught:
                c.bounded_call(private_failure,(),1)
        ctx=c.diagnostic_context(); ctx['stage']='health.request'; ctx['preflight_requests_counted']=1
        envelope=c.diagnostic_envelope(ctx,caught.exception)
        self.assertEqual((envelope['origin'],envelope['category'],envelope['exception_class']),('owned_request_child','validation','ValueError'))
        self.assertNotIn('PRIVATE',json.dumps(envelope)); self.assertNotIn('URL',json.dumps(envelope))
        self.assertEqual(envelope['completion_attempts_committed'],0)

    def test_fake_child_class_is_allowlisted_and_parent_stages_are_literal(self):
        class ArbitraryException(Exception): pass
        self.assertEqual(c.classify_exception(ArbitraryException('SECRET')),{'category':'unexpected','exception_class':'OtherException'})
        ctx=c.diagnostic_context(); ctx.update(stage='token_budget',alias='G2',preflight_requests_counted=4)
        result=c.diagnostic_envelope(ctx,TimeoutError('PRIVATE'))
        self.assertEqual((result['stage'],result['category'],result['origin']),('token_budget','timeout','parent'))
        ctx['stage']='PRIVATE'; ctx['alias']='PRIVATE'
        result=c.diagnostic_envelope(ctx,ValueError('PRIVATE'))
        self.assertEqual((result['stage'],result['alias']),('unknown',None))
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_companion_only_for_owned_marker_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d); (path/'batch-started.private.json').write_text('old')
            ctx=c.diagnostic_context(); c.write_failure(ctx,ValueError('PRIVATE'),path)
            self.assertFalse((path/'batch-failure-diagnostic.private.json').exists())
            self.assertFalse((path/'batch-failure.private.json').exists())
            ctx['marker_owned']=True; ctx['stage']='health.request'; ctx['preflight_requests_counted']=1
            c.write_failure(ctx,ValueError('PRIVATE'),path)
            companion=path/'batch-failure-diagnostic.private.json'; initial=companion.read_bytes()
            self.assertNotIn(b'PRIVATE',initial)
            c.write_failure(ctx,TimeoutError('PRIVATE'),path)
            self.assertEqual(companion.read_bytes(),initial)
            self.assertEqual(json.loads((path/'batch-failure.private.json').read_bytes()),{'status':'unavailable'})

    def test_diagnostic_write_failure_keeps_generic_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d); ctx=c.diagnostic_context(); ctx['marker_owned']=True
            original=c.write_new
            def fake_write(target,obj):
                if target.name=='batch-failure-diagnostic.private.json': raise OSError('PRIVATE')
                return original(target,obj)
            with patch.object(c,'write_new',side_effect=fake_write): c.write_failure(ctx,ValueError('PRIVATE'),path)
            self.assertEqual(json.loads((path/'batch-failure.private.json').read_bytes()),{'status':'unavailable'})

    def test_success_preserves_preflight_and_completion_call_limits(self):
        class Registry:
            def __init__(self,g): self.alias=g['alias']; self.facts={}; self.turns=[]
            def prompt(self): return {'alias':self.alias}
            def schema(self): return {'name':'synthetic'}
        @contextlib.contextmanager
        def fake_lock(*args): yield
        calls=[]
        def fake_call(fn,args,timeout):
            calls.append((fn,args,timeout))
            if fn is c.complete: return {'completion_tokens':2}
            endpoint=args[0]
            if endpoint=='/health': return {'status':'ok'}
            if endpoint=='/props': return {}
            if endpoint=='/apply-template': return {'prompt':'synthetic'}
            if endpoint=='/tokenize': return {'tokens':[1,2]}
            raise AssertionError('unexpected')
        ctx=c.diagnostic_context(); view={'games':[{'alias':a} for a in ('G1','G2','G3')]}
        admission={'runtime':{},'projection_sha256':'a'*64,'selector_sha256':'b'*64,'rule_sha256':'c'*64}
        with tempfile.TemporaryDirectory() as d, patch.object(c,'Registry',Registry), patch.object(c,'eval_lock',fake_lock), \
             patch.object(c,'verify_props',return_value=8192), patch.object(c,'validate_completion',return_value=['synthetic']):
            result=c.run(admission,'d'*64,view,Path(d),fake_call,lambda _:None,ctx)
            self.assertEqual(result['completion_attempts'],3)
            self.assertEqual(ctx['preflight_requests_counted'],8)
            self.assertEqual(ctx['completion_attempts_committed'],3)
            self.assertEqual(len(calls),11)
            self.assertTrue(all(0 < timeout <= (120 if fn is c.complete else 15) for fn,_,timeout in calls))

    def test_preflight_failure_zero_attempts_no_real_calls(self):
        class Registry:
            def __init__(self,g): self.alias=g['alias']
        @contextlib.contextmanager
        def fake_lock(*args): yield
        calls=[]
        def fake_call(fn,args,timeout): calls.append(args[0]); raise TimeoutError('PRIVATE')
        ctx=c.diagnostic_context(); view={'games':[{'alias':a} for a in ('G1','G2','G3')]}
        with tempfile.TemporaryDirectory() as d, patch.object(c,'Registry',Registry), patch.object(c,'eval_lock',fake_lock):
            with self.assertRaises(TimeoutError) as caught:
                c.run({'runtime':{}},'a'*64,view,Path(d),fake_call,lambda _:None,ctx)
            self.assertEqual(calls,['/health'])
            self.assertEqual(ctx['stage'],'health.request')
            self.assertEqual(ctx['preflight_requests_counted'],1)
            self.assertEqual(ctx['completion_attempts_committed'],0)
            c.write_failure(ctx,caught.exception,Path(d))
            result=json.loads((Path(d)/'batch-failure-diagnostic.private.json').read_text())
            self.assertEqual(result['category'],'timeout')
            self.assertNotIn('PRIVATE',json.dumps(result))

if __name__=='__main__': unittest.main()
