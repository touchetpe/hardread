#!/usr/bin/env python3
"""Pre-submission v2 data/proof readback; no model or remote calls."""
import argparse
from pathlib import Path
import json
import run_sft as R

class _Pins:
    def __init__(self,proof):
        self.tokenizer_sha256=proof['tokenizer_sha256'];self.template_sha256=proof['template_sha256']

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',required=True);p.add_argument('--proof',required=True);p.add_argument('--serializer',required=True)
    args=p.parse_args(argv); data=Path(args.data); proof_path=Path(args.proof)
    if proof_path.is_symlink() or not proof_path.is_file():raise ValueError('proof path invalid')
    proof_sha=R.sha256(proof_path);proof=R.strict_json(proof_path.read_bytes())
    if R.sha256(proof_path)!=proof_sha:raise ValueError('proof changed')
    man=R.strict_json((data/'manifest.json').read_bytes())
    if man.get('schema') not in (R.V2_FULL,R.V2_SMOKE):raise ValueError('v2 schema required')
    summary=R.validate_sft_manifest(man,data,_Pins(proof),proof,serializer_path=args.serializer)
    print(json.dumps({'schema':summary['manifest_schema'],'proof_sha256':proof_sha,
                      'manifest_sha256':R.sha256(data/'manifest.json'),'data_counts':summary['data_counts']},sort_keys=True))

if __name__=='__main__':main()
