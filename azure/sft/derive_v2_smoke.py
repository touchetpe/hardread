#!/usr/bin/env python3
"""Build an explicit 2,000-row smoke descendant from an accepted full dataset.

Nothing runs on import. The caller must separately approve and review the source,
parent proof, row-order digest, output receipt and cloud mount before any job.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import run_sft as R

class _Pins:
    def __init__(self, tokenizer, template):
        self.tokenizer_sha256=tokenizer; self.template_sha256=template

def derive(parent, parent_proof, proof_sha, serializer_path, destination, *, frozen=R.V2_FROZEN):
    parent=Path(parent); parent_proof=Path(parent_proof); destination=Path(destination)
    if not R._hex64(proof_sha) or parent_proof.is_symlink() or R.sha256(parent_proof)!=proof_sha:
        raise ValueError('parent proof bytes differ')
    proof=R.strict_json(parent_proof.read_bytes()); man=R.strict_json((parent/'manifest.json').read_bytes())
    if man.get('schema')!=R.V2_FULL: raise ValueError('smoke parent must be full v2')
    pins=_Pins(proof.get('tokenizer_sha256'),proof.get('template_sha256'))
    R.validate_sft_manifest(man,parent,pins,proof,serializer_path=serializer_path,frozen=frozen)
    if destination.exists() or destination.is_symlink() or destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError('smoke destination must be new')
    if destination.parent.stat().st_mode & 0o077 or destination.resolve().is_relative_to(parent.resolve()):
        raise ValueError('smoke output parent must be private and separate from source')
    temp=Path(tempfile.mkdtemp(prefix='.smoke-v2-',dir=destination.parent));os.chmod(temp,0o700)
    try:
        counts={'action':0,'reads':0};ids=set();ordered=hashlib.sha256()
        with gzip.open(parent/'train.jsonl.gz','rb') as source:
            fd=os.open(temp/'train.jsonl.gz',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as raw:
                with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as out:
                    for line in source:
                        if len(ids)==2000: break
                        if len(line)>8*1024*1024: raise ValueError('source row exceeds bound')
                        row=R.strict_json(line);uid=row.get('example_id');task=row.get('task')
                        if not isinstance(uid,str) or not uid or uid in ids or task not in counts or not uid.endswith('/'+task):
                            raise ValueError('smoke row identity differs')
                        ids.add(uid);counts[task]+=1;ordered.update(uid.encode('utf-8')+b'\n');out.write(line)
                raw.flush();os.fsync(raw.fileno())
        if len(ids)!=2000: raise ValueError('fewer than 2000 parent rows')
        for name in ('val.jsonl.gz','eval.jsonl.gz'):
            shutil.copyfile(parent/name,temp/name)
            if R.sha256(temp/name)!=frozen[name] or R.sha256(parent/name)!=frozen[name]: raise ValueError('frozen data copy differs')
        if R.sha256(parent/'manifest.json')!=proof['manifest_sha256'] or R.sha256(parent/'train.jsonl.gz')!=proof['parent_train_sha256']:
            raise ValueError('parent changed during derivation')
        files={name:{'sha256':R.sha256(temp/name),'bytes':(temp/name).stat().st_size} for name in R.DATA_FILES}
        manifest={'schema':R.V2_SMOKE,'tokenizer_sha256':man['tokenizer_sha256'],'template_sha256':man['template_sha256'],
                  'source_index_sha256':man['source_index_sha256'],'spec_sha256':man['spec_sha256'],
                  'parent_train_games':man['train_games'],'parent_train_rosters':man['train_rosters'],
                  'counts':{'action_post_tokenization':counts['action'],'reads_post_tokenization':counts['reads']},
                  'overlap_counts':man['overlap_counts'],'files':files,
                  'parent_full_manifest_sha256':proof['manifest_sha256'],'parent_train_sha256':proof['parent_train_sha256'],
                  'overlap_receipt_sha256':proof['overlap_receipt_sha256'],'smoke_ordered_ids_sha256':ordered.hexdigest()}
        (temp/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n')
        smoke_proof=dict(proof,manifest_sha256=R.sha256(temp/'manifest.json'),
                         parent_full_manifest_sha256=proof['manifest_sha256'],
                         smoke_ordered_ids_sha256=ordered.hexdigest())
        # This candidate is review input; its existence is not an admission decision.
        (temp/'proof-candidate.json').write_text(json.dumps(smoke_proof,sort_keys=True,separators=(',',':'))+'\n')
        if R.validate_sft_manifest(manifest,temp,pins,smoke_proof,serializer_path=serializer_path,frozen=frozen)['manifest_schema']!=R.V2_SMOKE:
            raise ValueError('derived smoke readback failed')
        receipt={'schema':'hardread.glimmer-v2.smoke-derivation-receipt/v1','status':'pending_review',
                 'parent_full_manifest_sha256':proof['manifest_sha256'],'parent_train_sha256':proof['parent_train_sha256'],
                 'manifest_sha256':R.sha256(temp/'manifest.json'),'proof_candidate_sha256':R.sha256(temp/'proof-candidate.json'),
                 'ordered_ids_sha256':ordered.hexdigest(),'counts':manifest['counts'],'files':files}
        (temp/'derivation-receipt.json').write_text(json.dumps(receipt,sort_keys=True,separators=(',',':'))+'\n')
        for p in temp.iterdir():os.chmod(p,0o600)
        if destination.exists(): raise ValueError('smoke destination already exists')
        os.rename(temp,destination)
    except BaseException:
        shutil.rmtree(temp,ignore_errors=True);raise
    return {'rows':2000,'manifest_sha256':R.sha256(destination/'manifest.json'),
            'proof_candidate_sha256':R.sha256(destination/'proof-candidate.json'),
            'ordered_ids_sha256':ordered.hexdigest(),'derivation_receipt_sha256':R.sha256(destination/'derivation-receipt.json')}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('parent','parent-proof','proof-sha256','serializer','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--execute',action='store_true');a=p.parse_args(argv)
    if not a.execute:raise SystemExit('derivation requires explicit --execute and prior authorization')
    print(json.dumps(derive(a.parent,a.parent_proof,a.proof_sha256,a.serializer,a.out),sort_keys=True))

if __name__=='__main__':main()
