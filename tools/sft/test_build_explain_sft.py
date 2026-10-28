import unittest
import build_explain_sft as B

class Tokenizer:
    def encode(self,messages,tools,message):return {'input_ids':[1,2,3,4],'labels':[-100,-100,3,4],'n_prompt':2}
def fixture():
    items=[{'decision_id':'d1','game_id':'g1','split':'train'},{'decision_id':'d2','game_id':'g2','split':'dev'}]
    call={'request':{'messages':[{'role':'user','content':'question'}],'tools':[]},'response':{'message':{'role':'assistant','content':'ok'}}}
    targets={'d1':{'type':'model','candidate_id':'c1'},'d2':{'type':'model','candidate_id':'c2'}}
    return items,targets,{'c1':{'calls':[call]},'c2':{'calls':[call]}}

class Tests(unittest.TestCase):
    def test_assistant_only_labels_and_game_split(self):
        items,targets,attempts=fixture()
        got=B.build_examples(items,targets,attempts,tokenizer=Tokenizer(),max_tokens=10,trace_validator=lambda *_:True,privacy_literals=['private-name'])
        self.assertEqual(got['splits']['train'][0]['labels'],[-100,-100,3,4])
        self.assertEqual(got['status'],'quarantined_not_training_approval')
    def test_multiturn_context_is_preserved_and_long_turn_is_dropped(self):
        items,targets,attempts=fixture();first=attempts['c1']['calls'][0]
        first['response']['message']={'role':'assistant','tool_calls':[{'id':'c','function':{'name':'lookup','arguments':{}}}]}
        history=first['request']['messages']+[first['response']['message'],{'role':'tool','name':'lookup','tool_call_id':'c','content':'cited result'}]
        attempts['c1']['calls'].append({'request':{'messages':history,'tools':[]},'response':{'message':{'role':'assistant','content':'final'}}})
        seen=[]
        class RecordingTokenizer:
            def encode(self,messages,tools,message):
                seen.append((messages,message))
                if len(messages)>1:return {'input_ids':[1,2,3,4,5,6],'labels':[-100,-100,3,4,5,6],'n_prompt':2}
                return {'input_ids':[1,2,3,4],'labels':[-100,-100,3,4],'n_prompt':2}
        got=B.build_examples(items,targets,attempts,tokenizer=RecordingTokenizer(),max_tokens=4,trace_validator=lambda *_:True,privacy_literals=['private'])
        self.assertEqual(seen[1][0],history);self.assertEqual(got['drops']['too_long'],1)
        self.assertEqual(len(got['splits']['train']),1)
    def test_game_leak_and_unverified_template_are_rejected(self):
        items,targets,attempts=fixture();items[1]['game_id']=items[0]['game_id']
        with self.assertRaises(B.ExplainDataError):B.build_examples(items,targets,attempts,tokenizer=Tokenizer(),max_tokens=10,trace_validator=lambda *_:True,privacy_literals=['private'])
        items[1]['game_id']='g2';targets['d2']={'type':'template_fallback'}
        with self.assertRaises(B.ExplainDataError):B.build_examples(items,targets,attempts,tokenizer=Tokenizer(),max_tokens=10,trace_validator=lambda *_:True,privacy_literals=['private'])
    def test_deterministic_private_dataset_is_not_promoted(self):
        import tempfile
        from pathlib import Path
        items,targets,attempts=fixture()
        result=B.build_examples(items,targets,attempts,tokenizer=Tokenizer(),max_tokens=10,trace_validator=lambda *_:True,privacy_literals=['private'])
        with tempfile.TemporaryDirectory() as d:
            a=B.write_dataset(result,Path(d)/'a',tokenizer_sha256='a'*64,template_sha256='b'*64)
            b=B.write_dataset(result,Path(d)/'b',tokenizer_sha256='a'*64,template_sha256='b'*64)
            self.assertEqual(a['train_sha256'],b['train_sha256'])
            self.assertEqual(a['status'],'quarantined_not_training_approval')
            with self.assertRaises(B.ExplainDataError):B.write_dataset(result,Path(d)/'a',tokenizer_sha256='a'*64,template_sha256='b'*64)
    def test_rejected_trace_and_private_text_fail(self):
        items,targets,attempts=fixture()
        with self.assertRaises(B.ExplainDataError):B.build_examples(items,targets,attempts,tokenizer=Tokenizer(),max_tokens=10,trace_validator=lambda *_:False,privacy_literals=['private'])
        attempts['c1']['calls'][0]['request']['messages'][0]['content']='private name'
        with self.assertRaises(B.ExplainDataError):B.build_examples(items,targets,attempts,tokenizer=Tokenizer(),max_tokens=10,trace_validator=lambda *_:True,privacy_literals=['private'])
if __name__=='__main__':unittest.main()
