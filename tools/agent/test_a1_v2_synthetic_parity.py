"""Twenty synthetic reviews exercise copied v2 manifest checks; no frozen inputs."""
from unittest.mock import patch
from test_eval_c9 import SyntheticWorkspace,manifest_tools as M
import a1_v2_frozen_loader as L
import app.pipeline as pipeline

class Tests(SyntheticWorkspace):
 def test_twenty_review_parity_and_input_tampering(self):
  manifest,document,_=self.build_manifest()
  with patch.object(L,'_require_farm_identity',M._require_farm_identity),patch.object(L,'_validate_selected_context',M._validate_selected_context),patch.object(L,'git_repo_head',M.git_repo_head),patch.object(L,'CRAWL_PINS',M.CRAWL_PINS),patch.object(L,'DRAFT_CACHE_SHA',M.DRAFT_CACHE_SHA):
   def validate():return L.validate_manifest_v2(manifest,root=self.root,app_offered_fn=lambda r,c:pipeline.build_view_with_offered(r,c)[1],require_evals_paths=True)
   result=validate();self.assertEqual([len(result[x]) for x in M.POOLS],[10,10]);self.assertEqual(len(document['items']),60)
   for name in ('review_path','ctx_path','replay_path','offered_path','source_path'):
    path=self.root/manifest['rows'][0][name];before=path.read_bytes();path.write_bytes(b'[]' if name=='offered_path' and before!=b'[]' else b'[{}]' if name=='offered_path' else before+b' ')
    with self.subTest(name=name),self.assertRaises((M.ManifestError,ValueError)):validate()
    path.write_bytes(before)
   row=manifest['rows'][0];team=self.root/row['team_path'];before=team.read_bytes();team.write_bytes(before+b' ')
   with self.assertRaises(M.ManifestError):M._verify_file(row['team_path'],row['team_sha256'],self.root)
   team.write_bytes(before)
   altered=dict(document);altered['items']=list(document['items']);altered['items'][0],altered['items'][1]=altered['items'][1],altered['items'][0]
   self.assertNotEqual(M.canonical_json(document),M.canonical_json(altered))
