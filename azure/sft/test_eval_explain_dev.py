import hashlib,json,unittest
import eval_explain_dev as E
class Tests(unittest.TestCase):
 def test_token_weighted_dev_loss_and_no_action_metrics(self):
  got=E.summarize([{'tokens':2,'nll_sum':4.0},{'tokens':1,'nll_sum':5.0}]);self.assertEqual(got['cross_entropy'],3.0);self.assertNotIn('top1',got)
 def test_checkpoint_choice_requires_complete_matching_run(self):
  inv={'status':'reviewed','checkpoints':[{'id':'a','tree_sha256':'a'*64},{'id':'b','tree_sha256':'b'*64}], 'training_manifest_sha256':'m','lineage_sha256':'l','base_tree_sha256':'base','dev_sha256':'dev','run_id':'run'}
  rows=[{'checkpoint_id':i,'checkpoint_tree_sha256':h,'checkpoint_inventory_sha256':'inventory','training_manifest_sha256':'m','training_lineage_sha256':'l','base_tree_sha256':'base','dev_sha256':'dev','run_id':'run','metric':'explain_teacher_token_cross_entropy','cross_entropy':1.0} for i,h in (('b','b'*64),('a','a'*64))]
  raw=json.dumps(inv).encode();sha=hashlib.sha256(raw).hexdigest()
  for row in rows:row['checkpoint_inventory_sha256']=sha
  kw={'selected':{('config','inventory.json'):raw},'inventory_path':'inventory.json'}
  self.assertEqual(E.choose_checkpoint(rows,raw,sha,**kw)['checkpoint_id'],'a')
  with self.assertRaises(E.EvaluationError):E.choose_checkpoint(rows[:1],raw,sha,**kw)
  rows[0]['run_id']='other'
  with self.assertRaises(E.EvaluationError):E.choose_checkpoint(rows,raw,sha,**kw)
 def test_invalid_loss_fails(self):
  with self.assertRaises(E.EvaluationError):E.summarize([{'tokens':1,'nll_sum':float('nan')}])
