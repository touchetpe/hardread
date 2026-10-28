"""Synthetic source-replayed ProductMaxTurns selection and rejection tests."""
import copy, hashlib, json, unittest
from unittest.mock import patch
from test_c9_proposed_manifest import ManifestTest, M, pipeline, put, raw


def valid_log(turns):
    ours=['Pikachu','Charizard','Bulbasaur','Squirtle','Eevee','Snorlax']
    theirs=['Gengar','Gyarados','Dragonite','Mew','Ditto','Mewtwo']
    return (['|player|p1|Alice','|player|p2|Bob','|gametype|singles']
            + [f'|poke|p1|{x}, L100' for x in ours]
            + [f'|poke|p2|{x}, L100' for x in theirs]
            + ['|teampreview','|turn|1',f'|turn|{turns}','|win|Alice'])

class TurnExceptionTest(ManifestTest):
 def _replace_first(self, turns=113, log=None):
  source=next(s for s in self.src if s['pool']=='crawl' and s['role']=='games')
  records=[json.loads(x) for x in (self.root/source['path']).read_bytes().splitlines()]
  first=min(records,key=lambda row:min(M.rank_hash(row['uid'],s) for s in ('p1','p2')))
  first['log']=valid_log(turns) if log is None else log
  source['sha256']=put(self.root,source['path'],b''.join(map(raw,records)))
  M.CRAWL_PINS['games']=source['sha256']
  return first['uid']

 def test_real_shipped_precheck_records_first_rank_and_full_documents(self):
  uid=self._replace_first(113);manifest=self.build();evidence=manifest['selection_evidence']['crawl']
  first=evidence['attempts'][0];self.assertEqual(first['uid'],uid)
  self.assertEqual((first['outcome'],first['exception_type'],first['raw_turn_count'],first['parsed_turn_count'],first['product_max_turns']),
                   ('review_exception','ProductMaxTurns',113,113,100))
  self.assertEqual(evidence['review_exceptions'],sum(a['outcome']=='review_exception' for a in evidence['attempts']))
  self.assertEqual(evidence['attempts_sha256'],M.digest_json(evidence['attempts']))
  self.assertEqual(len([r for r in manifest['rows'] if r['pool']=='crawl']),10)
  M.validate_manifest(manifest,root=self.root)
  path='scratch_ds/evals/turn-manifest.json';put(self.root,path,raw(manifest));a1,a0=M._manifest_items(path,manifest)
  self.assertEqual((len(a1['items']),len(a0['items'])),(60,30))
  M.validate_items_document(a1,'a1',root=self.root);M.validate_items_document(a0,'a0',root=self.root)
  # Rank order is invariant to filesystem/input order and uses the same source-bound witness.
  census=manifest['candidate_census']['crawl'];written=[]
  def save(w):
   path='scratch_ds/evals/permutation-'+w['rank_hash'];b=raw(w);written.append(w)
   return {'path':path,'sha256':put(self.root,path,b)}
  result=M.select_candidates(list(reversed(census)),'crawl',lambda c:dict(self.review,meta={'side':c['side'],'end_reason':'ko'}),
   lambda c,r:dict(self.ctx,side=c['side']),review_code_commit='commit',review_source_sha256=self.codehashes,root=self.root,exception_witness_fn=save)
  self.assertEqual([x['rank_hash'] for x in result['selected']], [r['rank_hash'] for r in manifest['rows'] if r['pool']=='crawl'])
  self.assertEqual(result['attempt_evidence']['attempts'][0]['witness_sha256'],first['witness_sha256'])

 def test_mutations_cannot_forge_or_suppress_exception(self):
  self._replace_first(101);manifest=self.build();M.validate_manifest(manifest,root=self.root)
  self.assertEqual(manifest['selection_evidence']['crawl']['attempts'][0]['raw_turn_count'],101)
  def check(mut):
   bad=copy.deepcopy(manifest);ev=bad['selection_evidence']['crawl'];mut(bad,ev['attempts'][0]);ev['attempts_sha256']=M.digest_json(ev['attempts'])
   with self.assertRaises((M.ManifestError,ValueError,OSError)):M.validate_manifest(bad,root=self.root)
  for key,value in [('raw_turn_count',102),('parsed_turn_count',102),('product_max_turns',99),
                    ('source_record_index',999),('source_record_sha256','0'*64),('rank_hash','0'*64),
                    ('candidate_sha256','0'*64),('exception_type','RuntimeError'),('outcome','selected'),
                    ('witness_sha256','0'*64),('review_code_commit','wrong')]:
   with self.subTest(field=key):check(lambda _m,a,k=key,v=value:a.__setitem__(k,v))
  check(lambda _m,a:a['review_source_sha256'].__setitem__('app/pipeline.py','0'*64))
  check(lambda m,a:m['selection_evidence']['crawl'].__setitem__('review_exceptions',0))
  with patch.object(pipeline,'MAX_TURNS',101):
   with self.assertRaises(M.ManifestError):M.validate_manifest(manifest,root=self.root)
  attempt=manifest['selection_evidence']['crawl']['attempts'][0];path=self.root/attempt['witness_path'];original=path.read_bytes()
  try:
   forged=json.loads(original);forged['parsed_turn_count']=102;new=raw(forged);path.write_bytes(new)
   bad=copy.deepcopy(manifest);ev=bad['selection_evidence']['crawl'];ev['attempts'][0]['witness_file_sha256']=hashlib.sha256(new).hexdigest();ev['attempts'][0]['witness_sha256']=M.digest_json(forged);ev['attempts_sha256']=M.digest_json(ev['attempts'])
   with self.assertRaises(M.ManifestError):M.validate_manifest(bad,root=self.root)
  finally:path.write_bytes(original)
  source=next(x for x in manifest['sources'] if x['pool']=='crawl' and x['role']=='games');path=self.root/source['path'];original=path.read_bytes()
  try:
   rows=[json.loads(x) for x in original.splitlines()];rows[attempt['source_record_index']]['log']=valid_log(100);changed=b''.join(map(raw,rows));path.write_bytes(changed)
   bad=copy.deepcopy(manifest);new_hash=hashlib.sha256(changed).hexdigest();next(x for x in bad['sources'] if x['pool']=='crawl' and x['role']=='games')['sha256']=new_hash;M.CRAWL_PINS['games']=new_hash
   with self.assertRaises(M.ManifestError):M.validate_manifest(bad,root=self.root)
  finally:path.write_bytes(original);M.CRAWL_PINS['games']=source['sha256']

 def test_boundary_and_other_input_error_fail_closed(self):
  uid=self._replace_first(100);census=M.enumerate_source_candidates('crawl',self.src,root=self.root);seen=[]
  target=next(c for c in census if c['uid']==uid and c['side']=='p1')
  result=M.select_candidates([target],'crawl',lambda c:(seen.append(c['uid']) or self.review),lambda c,r:self.ctx,
    offered_fn=lambda *_:[],review_code_commit='commit',review_source_sha256=self.codehashes,root=self.root,limit=1)
  self.assertEqual(seen,[uid]);self.assertEqual(result['review_exceptions'],0)
  short=dict(target,turns=9);self.assertEqual(M._candidate_exclusion_reason(short,'crawl'),'short')
  self._replace_first(101,log=['|turn|101','|win|Alice'])
  candidate=min(M.enumerate_source_candidates('crawl',self.src,root=self.root),key=lambda c:c['rank_hash'])
  with self.assertRaises(M.ManifestError):
   M.select_candidates([candidate],'crawl',lambda c:self.review,lambda c,r:self.ctx,offered_fn=lambda *_:[],review_code_commit='commit',review_source_sha256=self.codehashes,root=self.root,limit=1,exception_witness_fn=lambda _:self.fail('should not save'))

if __name__=='__main__':unittest.main()
