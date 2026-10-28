"""Synthetic-only tests of frozen witness and compatibility boundaries."""
import ast,hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
import a1_v2_frozen_loader as L
import build_eval_manifest as M

class Tests(unittest.TestCase):
 def test_frozen_witness_is_authenticated_and_not_regenerated(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();folder=root/'scratch_ds/evals';folder.mkdir(parents=True)
   raw=(json.dumps({'uid':'synthetic-game','log':['|turn|101']})+'\n').encode();source=folder/'source.jsonl';source.write_bytes(raw)
   candidate={'pool':'crawl','uid':'synthetic-game','side':'p1','source_path':'scratch_ds/evals/source.jsonl','source_sha256':hashlib.sha256(raw).hexdigest(),'source_record_index':0,'source_record_sha256':hashlib.sha256(raw).hexdigest(),'turns':101}
   commit='c'*40
   witness={'schema':'c9-product-max-turns-witness-v1','exception_type':'ProductMaxTurns','pool':'crawl','rank_hash':M.rank_hash(candidate['uid'],candidate['side']),'candidate_sha256':M.digest_json(M._source_identity(candidate)),'source_path':candidate['source_path'],'source_sha256':candidate['source_sha256'],'source_record_index':0,'source_record_sha256':candidate['source_record_sha256'],'raw_turn_count':101,'parsed_turn_count':101,'product_max_turns':100,'app_pipeline_sha256':L.OLD_PIPELINE,'review_code_commit':commit,'observed_error_class':'InputError'}
   witness_path=folder/'witness.json';witness_path.write_text(json.dumps(witness));attempt={'witness_path':'scratch_ds/evals/witness.json','witness_file_sha256':M.digest_file(witness_path)}
   self.assertEqual(L._frozen_witness_v2(candidate,attempt,commit,{'app/pipeline.py':L.OLD_PIPELINE},root=root),witness)
   witness_path.write_text(witness_path.read_text()+' ')
   with self.assertRaises(M.ManifestError):L._frozen_witness_v2(candidate,attempt,commit,{'app/pipeline.py':L.OLD_PIPELINE},root=root)
   witness_path.write_text(json.dumps(witness));source.write_bytes(raw+b' ')
   with self.assertRaises(M.ManifestError):L._frozen_witness_v2(candidate,attempt,commit,{'app/pipeline.py':L.OLD_PIPELINE},root=root)
 def test_raw_offered_pins_cover_exactly_20_and_detect_tampering(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();rows=[];pins={}
   for i in range(20):
    path=root/('offered-%d.json'%i);path.write_text('[]\n');name=path.relative_to(root).as_posix();rows.append({'offered_path':name});pins[name]=M.digest_file(path)
   L._check_offered_pins(rows,pins,root=root)
   with self.assertRaises(M.ManifestError):L._check_offered_pins(rows,pins|{'extra':'a'*64},root=root)
   (root/'offered-10.json').write_text('[1]\n')
   with self.assertRaises(M.ManifestError):L._check_offered_pins(rows,pins,root=root)
 def test_exact_frozen_item_rejection_before_any_load(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();path=root/L.ITEM_PATH;path.parent.mkdir(parents=True);path.write_text('{}')
   with self.assertRaises(M.ManifestError):L.load_registered_a1_v2({},root=root)
 def test_attempt_order_and_row_identity_are_bound(self):
  import copy
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();source_hashes={}
   for name in M.REVIEW_SOURCE_PATHS:
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('synthetic '+name);source_hashes[name]=M.digest_file(p)
   commit='synthetic';census=[]
   for i in range(10):
    c={'pool':'hr_val','uid':'game-%d'%i,'side':'p1','source_record_index':i,'source_record_sha256':str(i)*64,'result':'win','turns':12,'both_seats_eligible':True,'exclusion_reason':None}
    c['rank_hash']=M.rank_hash(c['uid'],c['side']);census.append(c)
   census.sort(key=lambda c:(c['rank_hash'],c['uid'],c['side']));rows=[];attempts=[]
   for c in census:
    selected='a'*64;rows.append({'pool':'hr_val','uid':c['uid'],'side':'p1','selection_sha256':selected,'source_record_index':c['source_record_index'],'source_record_sha256':c['source_record_sha256'],'eligibility':{'decided':True,'result':'win','turns':12,'half':None,'both_seats_eligible':True}})
    attempts.append({'pool':'hr_val','uid':c['uid'],'side':'p1','rank_hash':c['rank_hash'],'candidate_sha256':M.digest_json(M._source_identity(c)),'review_code_commit':commit,'review_source_sha256':source_hashes,'outcome':'selected','selected_row_sha256':selected})
   evidence={'attempts':attempts,'attempts_sha256':M.digest_json(attempts),'review_code_commit':commit,'review_source_sha256':source_hashes,'review_exceptions':0};manifest={'review_code_commit':commit,'review_source_sha256':source_hashes}
   L._validate_attempt_evidence_v2('hr_val',evidence,census,rows,manifest,root=root)
   bad=copy.deepcopy(evidence);bad['attempts'][0],bad['attempts'][1]=bad['attempts'][1],bad['attempts'][0];bad['attempts_sha256']=M.digest_json(bad['attempts'])
   with self.assertRaises(M.ManifestError):L._validate_attempt_evidence_v2('hr_val',bad,census,rows,manifest,root=root)
   rows[0]['source_record_sha256']='f'*64
   with self.assertRaises(M.ManifestError):L._validate_attempt_evidence_v2('hr_val',evidence,census,rows,manifest,root=root)
 def test_pipeline_ast_changes_are_limited_to_display_functions(self):
  root=Path(M.ROOT);old=subprocess.run(['git','-C',str(root),'show',M.FROZEN_C9_V1_REVIEW_CODE_COMMIT+':app/pipeline.py'],check=True,capture_output=True,text=True).stdout
  new=(root/'app/pipeline.py').read_text();old_tree=ast.parse(old);new_tree=ast.parse(new)
  def filtered(tree):
   return ast.dump(ast.Module(body=[n for n in tree.body if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) or n.name not in ('verdict_card','_stated_hypotheses','read_check')],type_ignores=[]),include_attributes=False)
  self.assertEqual(filtered(old_tree),filtered(new_tree))
  self.assertEqual(M.digest_file(root/'app/pipeline.py'),L.CURRENT_PIPELINE)
 def test_unmodified_v1_loader_remains_separate(self):
  self.assertIsNot(L.validate_manifest_v2,M.validate_manifest)
  self.assertIn('_verify_frozen_c9_v1_source_binding',M._validate_attempt_evidence.__code__.co_names)
