import copy
import contextlib
import io
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('run_sft',ROOT/'run_sft.py')
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
with patch.dict('sys.modules',{'run_sft':R}):
    spec=importlib.util.spec_from_file_location('derive_v2_smoke',ROOT/'derive_v2_smoke.py')
    D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)

class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.data=self.root/'full';self.data.mkdir()
        self.ser=self.root/'serialize.py';self.ser.write_text('SERIALIZER_VERSION = "hr-sft-v1.2"\n')
        self.gc=types.SimpleNamespace(tokenizer_sha256='a'*64,template_sha256='b'*64)
        rows=[{'example_id':f'game/A/{i:04d}/'+('action' if i%2==0 else 'reads'),'task':'action' if i%2==0 else 'reads','input_ids':[1,2],'labels':[-100,2]} for i in range(2002)]
        self.write_gz(self.data/'train.jsonl.gz',rows)
        self.write_gz(self.data/'val.jsonl.gz',[{'synthetic':'val'}]);self.write_gz(self.data/'eval.jsonl.gz',[{'synthetic':'eval'}])
        self.frozen={name:R.sha256(self.data/name) for name in ('val.jsonl.gz','eval.jsonl.gz')}
        self.files={name:{'sha256':R.sha256(self.data/name),'bytes':(self.data/name).stat().st_size} for name in R.DATA_FILES}
        self.man={'schema':R.V2_FULL,'tokenizer_sha256':self.gc.tokenizer_sha256,'template_sha256':self.gc.template_sha256,
                  'source_index_sha256':'c'*64,'spec_sha256':'d'*64,'train_games':4,'train_rosters':8,
                  'counts':{'action_post_tokenization':1001,'reads_post_tokenization':1001},
                  'overlap_counts':{x:0 for x in R.V2_DOMAINS},'files':copy.deepcopy(self.files)}
        self.write_manifest(self.man)
        msha=R.sha256(self.data/'manifest.json')
        self.proof={'schema':R.V2_PROOF,'manifest_sha256':msha,'parent_full_manifest_sha256':msha,
                    'parent_train_sha256':self.files['train.jsonl.gz']['sha256'],'source_index_sha256':'c'*64,
                    'spec_sha256':'d'*64,'tokenizer_sha256':'a'*64,'template_sha256':'b'*64,'serializer_sha256':R.sha256(self.ser),'serializer_version':'hr-sft-v1.2',
                    'val_sha256':self.frozen['val.jsonl.gz'],'eval_sha256':self.frozen['eval.jsonl.gz'],
                    'overlap_receipt_sha256':'e'*64,'admission_review_sha256':'f'*64}
    def tearDown(self):self.tmp.cleanup()
    def write_gz(self,path,rows):
        with gzip.open(path,'wt',encoding='utf-8') as f:
            for row in rows:f.write(json.dumps(row,separators=(',',':'))+'\n')
    def write_manifest(self,man): (self.data/'manifest.json').write_text(json.dumps(man,sort_keys=True))
    def validate(self,man=None,proof=None):
        return R.validate_sft_manifest(self.man if man is None else man,self.data,self.gc,self.proof if proof is None else proof,serializer_path=self.ser,frozen=self.frozen)
    def reject_mutation(self,fn):
        man=copy.deepcopy(self.man);proof=copy.deepcopy(self.proof);fn(man,proof);self.write_manifest(man)
        proof['manifest_sha256']=R.sha256(self.data/'manifest.json');proof['parent_full_manifest_sha256']=proof['manifest_sha256']
        with self.assertRaises((ValueError,KeyError,OSError,TypeError)):self.validate(man,proof)
        self.write_manifest(self.man)
    def test_legacy_is_unchanged_and_exact_v2_full(self):
        legacy={'schema':'hardread-sft-data/v1','tokenizer_sha256':'a'*64,'chat_template_sha256':'b'*64,'counts':{'train_rows':1},'serializer_version':'legacy'}
        self.assertEqual(R.validate_sft_manifest(legacy,self.data,self.gc)['serializer_version'],'legacy')
        self.assertEqual(self.validate()['manifest_schema'],R.V2_FULL)
        with self.assertRaises(ValueError):R.validate_sft_manifest({'schema':'unknown'},self.data,self.gc)
        with self.assertRaises(ValueError):R.validate_sft_manifest(self.man,self.data,self.gc,serializer_path=self.ser,frozen=self.frozen)
    def test_aliases_and_source_binding_rejected(self):
        for fn in (
            lambda m,p:m.update(chat_template_sha256=m['template_sha256']),
            lambda m,p:m.pop('template_sha256'),
            lambda m,p:p.pop('serializer_sha256'),
            lambda m,p:p.update(serializer_sha256='0'*64),
            lambda m,p:p.update(serializer_version='wrong'),
            lambda m,p:p.update(source_index_sha256='0'*64),
            lambda m,p:p.update(spec_sha256='0'*64),
            lambda m,p:p.update(tokenizer_sha256='0'*64),
            lambda m,p:p.update(template_sha256='0'*64),
            lambda m,p:p.update(parent_train_sha256='0'*64),
            lambda m,p:m.update(schema='unrecognized'),
        ):
            with self.subTest(fn=fn):self.reject_mutation(fn)
    def test_counts_and_overlap_rejected(self):
        for bad in (True,-1,1.5,'1001',None):
            self.reject_mutation(lambda m,p,b=bad:m['counts'].__setitem__('action_post_tokenization',b))
        self.reject_mutation(lambda m,p:m['counts'].__setitem__('action_post_tokenization',1000))
        self.reject_mutation(lambda m,p:m['counts'].__setitem__('train_rows',2002))
        self.reject_mutation(lambda m,p:m['overlap_counts'].__setitem__('canonical_game',1))
        self.reject_mutation(lambda m,p:m['overlap_counts'].__setitem__('decision_id',False))
        self.reject_mutation(lambda m,p:m['overlap_counts'].pop('p5_incomplete_game'))
        self.reject_mutation(lambda m,p:m.update(train_games=True))
    def test_files_proof_and_strict_json_fail_closed(self):
        self.reject_mutation(lambda m,p:m['files']['train.jsonl.gz'].__setitem__('sha256','0'*64))
        self.reject_mutation(lambda m,p:m['files']['val.jsonl.gz'].__setitem__('bytes',False))
        self.reject_mutation(lambda m,p:p.update(val_sha256='0'*64))
        self.reject_mutation(lambda m,p:p.update(eval_sha256='0'*64))
        p=self.data/'train.jsonl.gz';before=p.read_bytes();p.write_bytes(before+b'!')
        with self.assertRaises(ValueError):self.validate()
        p.write_bytes(before)
        bad=copy.deepcopy(self.proof);bad['manifest_sha256']='0'*64
        with self.assertRaises(ValueError):self.validate(proof=bad)
        with self.assertRaises(ValueError):R.strict_json('{"schema":"a","schema":"b"}')
        with self.assertRaises(ValueError):R.strict_json('{"score":NaN}')
    def test_missing_v2_proof_fails_before_gpu_diagnostics(self):
        fake=types.ModuleType('glimmer_chat');fake.GlimmerChat=lambda *a,**kw:self.gc
        args=types.SimpleNamespace(snapshot='synthetic',data=str(self.data),proof_binding=None,proof_binding_sha256=None)
        with patch.dict('sys.modules',{'glimmer_chat':fake}),patch.object(R.subprocess,'run') as diagnostic,patch.object(R,'log'),patch.object(R,'save_state'):
            self.assertIsNone(R.preflight(args))
            self.assertIn('reviewed v2 proof missing',R.STATE['stages']['preflight']['error'])
            diagnostic.assert_not_called()
    def test_presubmit_helper_runs_full_boundary(self):
        with patch.dict('sys.modules',{'run_sft':R}):
            spec=importlib.util.spec_from_file_location('validate_v2_data',ROOT/'validate_v2_data.py')
            helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        proof_file=self.root/'proof.json';proof_file.write_text(json.dumps(self.proof))
        original=R.validate_sft_manifest
        def synthetic_frozen(*args,**kwargs):
            kwargs['frozen']=self.frozen;return original(*args,**kwargs)
        with patch.object(R,'validate_sft_manifest',side_effect=synthetic_frozen),contextlib.redirect_stdout(io.StringIO()) as out:
            helper.main(['--data',str(self.data),'--proof',str(proof_file),'--serializer',str(self.ser)])
        receipt=json.loads(out.getvalue());self.assertEqual(receipt['manifest_sha256'],self.proof['manifest_sha256'])
        self.assertEqual(receipt['data_counts']['action_post_tokenization'],1001)
        p=self.data/'train.jsonl.gz';raw=p.read_bytes();p.write_bytes(raw+b'!')
        with patch.object(R,'validate_sft_manifest',side_effect=synthetic_frozen),self.assertRaises(ValueError):
            helper.main(['--data',str(self.data),'--proof',str(proof_file),'--serializer',str(self.ser)])
        p.write_bytes(raw)
    def test_smoke_is_truthful_derived_and_parent_bound(self):
        pp=self.root/'parent-proof.json';pp.write_text(json.dumps(self.proof));out=self.root/'smoke'
        result=D.derive(self.data,pp,R.sha256(pp),self.ser,out,frozen=self.frozen)
        self.assertEqual(result['rows'],2000)
        man=json.loads((out/'manifest.json').read_text());proof=json.loads((out/'proof-candidate.json').read_text())
        self.assertEqual(man['counts'],{'action_post_tokenization':1000,'reads_post_tokenization':1000})
        self.assertEqual((man['parent_train_games'],man['parent_train_rosters']),(4,8))
        self.assertNotIn('train_games',man);self.assertNotIn('train_rosters',man)
        self.assertNotEqual(man['files']['train.jsonl.gz']['sha256'],self.files['train.jsonl.gz']['sha256'])
        self.assertEqual(R.validate_sft_manifest(man,out,self.gc,proof,serializer_path=self.ser,frozen=self.frozen)['manifest_schema'],R.V2_SMOKE)
        for key in ('parent_full_manifest_sha256','parent_train_sha256','overlap_receipt_sha256','smoke_ordered_ids_sha256'):
            modified=copy.deepcopy(man);modified[key]='0'*64;(out/'manifest.json').write_text(json.dumps(modified,sort_keys=True))
            bound=copy.deepcopy(proof);bound['manifest_sha256']=R.sha256(out/'manifest.json')
            with self.subTest(key=key),self.assertRaises(ValueError):R.validate_sft_manifest(modified,out,self.gc,bound,serializer_path=self.ser,frozen=self.frozen)
        with self.assertRaises(ValueError):D.derive(self.data,pp,R.sha256(pp),self.ser,out,frozen=self.frozen)
        with self.assertRaises(ValueError):D.derive(self.data,pp,R.sha256(pp),self.ser,self.data/'nested',frozen=self.frozen)
        modified=copy.deepcopy(man);bound=copy.deepcopy(proof)
        modified['smoke_ordered_ids_sha256']='0'*64;bound['smoke_ordered_ids_sha256']='0'*64
        (out/'manifest.json').write_text(json.dumps(modified,sort_keys=True));bound['manifest_sha256']=R.sha256(out/'manifest.json')
        with self.assertRaises(ValueError):R.validate_sft_manifest(modified,out,self.gc,bound,serializer_path=self.ser,frozen=self.frozen)
        modified=copy.deepcopy(man);modified['counts']['action_post_tokenization']=999
        (out/'manifest.json').write_text(json.dumps(modified,sort_keys=True));bound=copy.deepcopy(proof);bound['manifest_sha256']=R.sha256(out/'manifest.json')
        with self.assertRaises(ValueError):R.validate_sft_manifest(modified,out,self.gc,bound,serializer_path=self.ser,frozen=self.frozen)

if __name__=='__main__':unittest.main()
