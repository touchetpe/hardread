"""Synthetic pre-action evidence; never reads app state or any real games."""
import copy
import importlib.util
from pathlib import Path
import unittest
import tempfile
import contextlib
import time
import own_ladder_selection as selection


def cite(v, field): return {'value':v, 'field':field, 'ref':'a'*64}

def game(alias):
    return {'alias':alias, 'conflicts_unavailable':False, 'decisions':[
        {'turn':cite(i,'turn'),'chosen':cite('Move A','chosen'),
         'duration':{'value':cite('0.12345678901234567890123456789','duration'), 'unit':cite('seconds','unit')},
         'visits':[{'value':cite(str(100+j),'visits.count'),'scope':cite('world '+str(j+1),'visits.scope')} for j in range(3)]}
        for i in (1,2,3)]}

def response(refs):
    return {'finish_reason':'tool_calls', 'tool_calls':[{'name':'select_evidence','arguments':{'refs':refs},'parse_error':None}]}

def refs(reg): return [next(r for r,f in reg.facts.items() if f['turn_index']==i and f['kind']=='search_duration') for i in range(3)]

class SelectionTests(unittest.TestCase):
    def test_registry_order_exact_values_and_no_private_citations_in_prompt(self):
        reg=selection.Registry(game('G1'))
        self.assertEqual(len(reg.facts),12)
        self.assertEqual(list(reg.facts)[:4],['#1.choice','#2.search_duration','#3.search_visits','#4.search_visits'])
        prompt=str(reg.prompt())
        self.assertIn('0.12345678901234567890123456789',prompt)
        self.assertIn('world 2',prompt)
        self.assertNotIn('world 3',prompt)
        self.assertNotIn('a'*64,prompt)
        self.assertNotIn('visits.count',prompt)
        self.assertEqual(selection.validate_completion(reg,response(refs(reg))),refs(reg))

    def test_missing_optional_fields_offer_only_recorded_choice(self):
        g=game('G1')
        for decision in g['decisions']:
            decision['duration']=None; decision['visits']=[]
        reg=selection.Registry(g)
        self.assertEqual(list(reg.facts),['#1.choice','#2.choice','#3.choice'])
        self.assertEqual(reg.validate({'refs':list(reg.facts)}),list(reg.facts))

    def test_reject_injection_invalid_response_and_partial(self):
        bad=game('G1'); bad['decisions'][0]['result']='win'
        with self.assertRaises(ValueError): selection.Registry(bad)
        bad=game('G1'); bad['decisions'][0]['turn']['field']='future.reveal'
        with self.assertRaises(ValueError): selection.Registry(bad)
        bad=game('G1'); bad['decisions'].pop()
        with self.assertRaises(ValueError): selection.Registry(bad)
        reg=selection.Registry(game('G1')); rr=refs(reg)
        cases=[{'refs':[rr[0],rr[0],rr[2]]},{'refs':[rr[0],rr[1],'#99.choice']},
               {'refs':[rr[0],rr[1],rr[2]],'extra':'x'}, {'refs':[rr[0], '#1.choice',rr[2]]}]
        for case in cases:
            with self.assertRaises(ValueError): reg.validate(case)
        bad=response(rr); bad['tool_calls'].append(bad['tool_calls'][0])
        with self.assertRaises(ValueError): selection.validate_completion(reg,bad)
        bad=response(rr); bad['finish_reason']='length'
        with self.assertRaises(ValueError): selection.validate_completion(reg,bad)

    def test_sidecar_binding_and_selected_companions(self):
        games=[game(a) for a in ('G1','G2','G3')]
        view={'presentation':'recorded-facts','games':games,'mode':'template-only'}
        entries=[{'alias':a,'status':'fallback','refs':[]} for a in ('G1','G2','G3')]
        reg=selection.Registry(games[0]); entries[0]={'alias':'G1','status':'success','refs':refs(reg)}
        side={'schema':'own-ladder-selection-v1','projection_sha256':'1'*64,'selector_sha256':'2'*64,'receipt_sha256':'3'*64,'games':entries}
        out=selection.apply_sidecar(view,'1'*64,'2'*64,'3'*64,side)
        self.assertEqual(out['games'][0]['selection_mode'],'glimmer-selected')
        self.assertEqual(out['games'][1]['selection_mode'],'template-only')
        self.assertEqual(out['games'][0]['decisions'][0]['duration']['unit']['value'],'seconds')
        self.assertEqual(out['games'][0]['decisions'][0]['visits'],[])
        self.assertEqual(len(view['games'][0]['decisions'][0]['visits']),3)
        with self.assertRaises(ValueError): selection.apply_sidecar(view,'4'*64,'2'*64,'3'*64,side)
        with self.assertRaises(ValueError): selection.apply_sidecar(view,'1'*64,'2'*64,'4'*64,side)

    def test_fake_batch_no_repair_and_transport_stop(self):
        path=Path(__file__).resolve().parents[1]/'tools/agent/run_own_ladder_selector.py'
        spec=importlib.util.spec_from_file_location('fake_selector_batch',path)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        games=[game(a) for a in ('G1','G2','G3')]; attempts=[]
        class Fake:
            def chat(self,prompt,schema):
                if prompt['alias']=='G1': return response(['#1.choice','#5.choice','#9.choice'])
                if prompt['alias']=='G2': return response(['#1.choice','#1.choice','#9.choice'])
                raise TimeoutError()
        out=module.synthetic_batch(games,Fake(),attempts.append,lambda _:True,lambda:0)
        self.assertEqual(attempts,['G1','G2','G3'])
        self.assertEqual([x['status'] for x in out],['success','fallback','fallback'])
        attempts.clear()
        out=module.synthetic_batch(games,Fake(),attempts.append,lambda _:False,lambda:0)
        self.assertEqual(attempts,[])
        self.assertTrue(all(g['status']=='fallback' for g in out))

class CallerTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'tools/agent/run_own_ladder_selector.py'
        spec=importlib.util.spec_from_file_location('selector_caller',path)
        self.caller=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.caller)
        self.games=[game(a) for a in ('G1','G2','G3')]
        self.view={'games':self.games}
        self.admission={'projection_sha256':'1'*64,'selector_sha256':'2'*64,'rule_sha256':'3'*64,
                        'runtime':{'model_path':'/synthetic/model','template_sha256':selection.digest_bytes(b'template')}}
        self.received=[]
        @contextlib.contextmanager
        def lock(*args): yield
        self.caller.eval_lock=lock
    def fake_call(self, fn, args, timeout):
        if fn is self.caller.complete:
            alias=__import__('json').loads(args[0][1]['content'])['alias']
            if alias=='G2': return dict(response(['#1.choice','#1.choice','#9.choice']),completion_tokens=5)
            return dict(response(['#1.choice','#5.choice','#9.choice']),completion_tokens=5)
        endpoint,body=args; self.received.append((endpoint,body))
        if endpoint=='/health': return {'status':'ok'}
        if endpoint=='/props': return {'total_slots':1,'model_path':'/synthetic/model','chat_template':'template','default_generation_settings':{'n_ctx':8192}}
        if endpoint=='/apply-template': return {'prompt':'synthetic template'}
        if endpoint=='/tokenize': return {'tokens':[1,2,3]}
        raise AssertionError('unexpected endpoint')
    def test_one_shot_preflight_and_invalid_selection_no_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            out=self.caller.run(self.admission,'4'*64,self.view,path,self.fake_call,lambda _:None)
            self.assertEqual(out['completion_attempts'],3)
            self.assertEqual(len(self.received),8)
            template_bodies=[body for ep,body in self.received if ep=='/apply-template']
            self.assertEqual(len(template_bodies),3)
            self.assertEqual(template_bodies[0]['tool_choice'],'required')
            self.assertTrue(template_bodies[0]['tools'])
            self.assertEqual(template_bodies[0]['chat_template_kwargs'],{'reasoning_strength':'low'})
            for alias in ('G1','G2','G3'): self.assertTrue((path/alias/'attempt.private.json').exists())
            with self.assertRaises(FileExistsError): self.caller.run(self.admission,'4'*64,self.view,path,self.fake_call,lambda _:None)
    def test_admission_rejects_mismatched_projection_rule_before_runtime(self):
        c=self.caller
        wrong='0'*64
        if wrong == c.sha_file(c.ROOT/'app/own_ladder_review.py'):
            wrong='1'*64
        admission={'schema':'own-ladder-selector-admission-v1', 'projection_path':'/synthetic/projection',
                   'projection_sha256':'a'*64, 'rule_sha256':wrong,
                   'runner_sha256':c.sha_file(Path(c.__file__).resolve()),
                   'selector_sha256':c.sha_file(c.SELECTOR), 'runtime':{}}
        calls=[]
        with self.assertRaisesRegex(ValueError, 'source differs'):
            c.validate_admission(admission, process_check=lambda value: calls.append(value))
        self.assertEqual(calls, [])

    def test_deadline_is_independent_of_client(self):
        with self.assertRaises(TimeoutError): self.caller.bounded_call(time.sleep,(0.5,),0.05)
    def test_lock_busy_and_runtime_mismatch_prevent_requests(self):
        @contextlib.contextmanager
        def locked(*args): raise RuntimeError('busy'); yield
        self.caller.eval_lock=locked
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError): self.caller.run(self.admission,'4'*64,self.view,Path(folder),self.fake_call,lambda _:None)
            self.assertEqual(self.received,[])
        @contextlib.contextmanager
        def unlocked(*args): yield
        self.caller.eval_lock=unlocked
        def mismatch(_): raise ValueError('runtime mismatch')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError): self.caller.run(self.admission,'4'*64,self.view,Path(folder),self.fake_call,mismatch)
            self.assertEqual(self.received,[])
    def test_unknown_usage_stops_remaining_attempts(self):
        def no_usage(fn,args,timeout):
            if fn is self.caller.complete: return response(['#1.choice','#5.choice','#9.choice'])
            return self.fake_call(fn,args,timeout)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            out=self.caller.run(self.admission,'4'*64,self.view,path,no_usage,lambda _:None)
            self.assertEqual(out['completion_attempts'],1)
            self.assertFalse((path/'G2').exists())

    def test_transport_error_stops_remaining_attempts(self):
        def blocked(fn,args,timeout):
            if fn is self.caller.complete: raise TimeoutError()
            return self.fake_call(fn,args,timeout)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            out=self.caller.run(self.admission,'4'*64,self.view,path,blocked,lambda _:None)
            self.assertEqual(out['completion_attempts'],1)
            self.assertTrue((path/'G1'/'attempt.private.json').exists())
            self.assertFalse((path/'G2').exists())

if __name__=='__main__': unittest.main()
