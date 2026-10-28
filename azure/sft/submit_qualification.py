#!/usr/bin/env python3
"""Stage pinned code and YAML only; root controls any separately admitted cloud creation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

SUBMIT_ENABLED = False

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','config','config-sha256','attempt','compute','environment','pairs-uri','base-uri','xl6-uri','approval-uri','selection-uri','output-uri','resource-group','workspace'):
        p.add_argument('--'+n,required=True)
    p.add_argument('--hard-end-utc',required=True)
    p.add_argument('--approval',required=True)
    p.add_argument('--approval-sha256',required=True)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args(argv)
    if not a.execute or os.environ.get('HARDREAD_DPO_QUALIFY_SUBMIT')!='1':
        raise SystemExit('DPO submission disabled until source and operational admission')
    root=Path(a.root).resolve()
    config_raw=Path(a.config).read_bytes()
    if hashlib.sha256(config_raw).hexdigest()!=a.config_sha256: raise SystemExit('config hash differs')
    preliminary=json.loads(config_raw)
    trainer=root/'azure/sft/train_dpo.py'
    if trainer.is_symlink() or not trainer.is_file() or hashlib.sha256(trainer.read_bytes()).hexdigest()!=preliminary.get('code_files',{}).get('azure/sft/train_dpo.py'): raise SystemExit('trainer source differs')
    sys.path.insert(0,str(root/'azure/sft'))
    import train_dpo as T
    cfg=T.runtime_config(preliminary,config_sha=a.config_sha256,qualification=True)
    import qualify_dpo as Q
    approval=Q.check_approval(a.approval,a.approval_sha256,a.config_sha256,T.fingerprint(cfg['code_files']))
    from datetime import datetime, timezone
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',a.hard_end_utc) or approval.get('hard_end_utc')!=a.hard_end_utc: raise SystemExit('hard end not bound by approval')
    try: hard_end=datetime.strptime(a.hard_end_utc,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError: raise SystemExit('invalid hard end')
    remaining=(hard_end-datetime.now(timezone.utc)).total_seconds()
    if not 1200<remaining<=cfg['max_seconds']: raise SystemExit('hard end outside qualification window')
    T.verify_file_pins(root,cfg['code_files'])
    if a.environment!=cfg['environment_id']: raise SystemExit('environment identity differs')
    if any(getattr(a,k)!=v for k,v in cfg['job_resources'].items()): raise SystemExit('job resource pins differ')
    if a.output_uri.rstrip('/').split('/')[-1]!=cfg['run_name']: raise SystemExit('output name differs')
    uris=(a.pairs_uri,a.base_uri,a.xl6_uri,a.approval_uri,a.selection_uri,a.output_uri)
    for uri in uris:
        if not re.fullmatch(r'azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/[A-Za-z0-9._~/-]+',uri) or any(x in ('','.','..') for x in uri.split('/')[3:]):
            raise SystemExit('unsafe Azure URI')
    if not re.fullmatch(r'azureml:[A-Za-z0-9_-]+',a.compute) or not re.fullmatch(r'azureml:/subscriptions/[A-Za-z0-9-]+/resourceGroups/[A-Za-z0-9._()-]+/providers/Microsoft.MachineLearningServices/workspaces/[A-Za-z0-9_-]+/environments/[A-Za-z0-9_-]+/versions/[A-Za-z0-9._-]+',a.environment): raise SystemExit('invalid compute or environment')
    if not all(re.fullmatch(r'[A-Za-z0-9._()-]+',v) for v in (a.resource_group,a.workspace)): raise SystemExit('invalid workspace')
    attempt=Path(a.attempt)
    attempt.mkdir(mode=0o700)  # exclusive, unique reviewed attempt
    code=attempt/'code'; code.mkdir(mode=0o700)
    for rel in cfg['code_files']:
        dst=code/rel; dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/rel,dst)
    T.verify_file_pins(code,cfg['code_files'])
    shutil.copyfile(a.config,code/'job-config.json')
    if T.file_sha(code/'job-config.json')!=a.config_sha256: raise SystemExit('staged config differs')
    template=(root/'azure/sft/qualification_job.yml.template').read_text()
    substitutions={'NAME':cfg['run_name'],'CONFIG_SHA':a.config_sha256,'COMPUTE':a.compute,
                   'ENVIRONMENT':a.environment,'PAIRS_URI':a.pairs_uri,'BASE_URI':a.base_uri,
                   'XL6_URI':a.xl6_uri,'APPROVAL_URI':a.approval_uri,'APPROVAL_SHA':a.approval_sha256,
                   'SELECTION_URI':a.selection_uri,'OUTPUT_URI':a.output_uri,'TIMEOUT':str(cfg['max_seconds']),'HARD_END':a.hard_end_utc}
    for key,value in substitutions.items(): template=template.replace('__'+key+'__',value)
    if re.search(r'__[A-Z_]+__',template): raise SystemExit('unresolved job template')
    job=attempt/'job.yml'; T.exclusive_bytes(job,template.encode())
    print(str(job))  # Staging only. Root owns watchdog admission and manual creation.

if __name__=='__main__': main()
