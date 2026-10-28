"""Plain-loss T1 explain LoRA training command. Execution is disabled."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

TRAINING_ENABLED=False
class TrainingError(ValueError):pass

def build_command(*,runner,snapshot,data,out,epochs,world_size,manifest,train_sha256,val_sha256,lineage,root,blind_key):
    if type(epochs) is not int or epochs not in (1,2) or type(world_size) is not int or world_size not in (2,3,4):
        raise TrainingError('one or two epochs and two to four nodes required')
    if not isinstance(manifest,dict) or manifest.get('status')!='committed_final_overlap_verified' or manifest.get('task')!='explain':
        raise TrainingError('committed explain manifest and final overlap required')
    if not isinstance(manifest.get('training_run_id'),str) or not manifest['training_run_id'].strip():raise TrainingError('frozen training run identity required')
    if (type(manifest.get('decision_count')) is not int or manifest['decision_count']<2000
            or type(manifest.get('verified_items')) is not int or manifest['verified_items']<1500
            or any(not isinstance(manifest.get(key),str) or len(manifest[key])!=64 for key in ('a1_sha256','a0_sha256'))):
        raise TrainingError('full population, verifier threshold and final A1/A0 hashes required')
    if manifest.get('train_sha256')!=train_sha256 or manifest.get('val_sha256')!=val_sha256:
        raise TrainingError('train or dev data binding differs')
    for file,expected in ((Path(data)/'train.jsonl.gz',train_sha256),(Path(data)/'val.jsonl.gz',val_sha256)):
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=expected:raise TrainingError('training bytes changed')
    from t1_training_lineage import verify_dataset,digest
    if (manifest.get('training_lineage_sha256')!=digest(lineage)
            or manifest.get('train_sha256')!=lineage.get('train_sha256') or manifest.get('val_sha256')!=lineage.get('val_sha256')):
        raise TrainingError('committed target and dataset lineage differs')
    try:verified=verify_dataset(lineage,root,snapshot,data,blind_key=blind_key)
    except (ValueError,OSError,KeyError,TypeError) as exc:raise TrainingError('deterministic source and dataset rederivation failed') from exc
    if verified['decision_count']!=manifest['decision_count'] or verified['verified_items']!=manifest['verified_items']:
        raise TrainingError('derived acceptance counts differ')
    return [sys.executable,str(runner),'--snapshot',str(snapshot),'--data',str(data),'--out',str(out),
            '--epochs',str(epochs),'--loss','plain','--skip-teacher','--skip-serve','--no-mb-fallback']

REQUIRED_TRAIN_CODE=frozenset({
    'azure/sft/train_explain.py','azure/sft/run_sft.py','azure/sft/sft_plugin.py',
    'azure/sft/qlora_sft.yaml','azure/sft/eval_sft.py','azure/sft/eval_explain_dev.py',
    'tools/sft/glimmer_chat.py','tools/sft/dex.json','azure/sft/t1_checkpoint_inventory.py','tools/sft/t1_training_lineage.py','tools/sft/build_explain_sft.py',
    'tools/agent/t1_full_sampler.py','tools/agent/t1_full_population.py','tools/agent/t1_full_targets.py','tools/agent/t1_template_trace.py',
    'tools/agent/t1_bf16_client.py','tools/agent/t1_git_proof.py','tools/agent/build_explain_targets.py','tools/agent/build_explain_train_manifest.py',
    'tools/agent/judge_explain.py','tools/agent/sample_explain.py','tools/agent/explain.py','tools/agent/templates.py',
    'tools/agent/tools.py','tools/agent/verifier.py','tools/agent/render.py','tools/agent/llm.py','tools/agent/names.py','tools/agent/lexicon.py',
    'app/server.py','app/pipeline.py','app/card.py','tools/human/parse_replays.py','tools/review/verdict_identity.py','tools/review/select_offered.py','tools/review/bridge.py','tools/review/calibrator.json'})

def verify_runtime_packages(manifest):
    import importlib.metadata
    required={'fastapi','starlette','tokenizers','jinja2','torch','transformers','peft','axolotl'}
    versions=manifest.get('runtime_packages')
    if not isinstance(versions,dict) or not required.issubset(versions):raise TrainingError('reviewed runtime package versions required')
    try:
        if any(importlib.metadata.version(name)!=version for name,version in versions.items()):raise TrainingError('runtime package version differs')
    except importlib.metadata.PackageNotFoundError as exc:raise TrainingError('required runtime package missing') from exc
    return True

def verify_training_package(manifest, selected, root, runner):
    root=Path(root).resolve()
    codes=manifest.get('training_code_sha256')
    if not isinstance(codes,dict) or set(codes)!=REQUIRED_TRAIN_CODE:
        raise TrainingError('exact reviewed training package required')
    for name,expected in codes.items():
        path=root/name
        if any((root/Path(*Path(name).parts[:i])).is_symlink() for i in range(1,len(Path(name).parts)+1)):
            raise TrainingError('training package symlink')
        try: raw=path.read_bytes()
        except OSError as exc: raise TrainingError('training package missing') from exc
        if (not isinstance(expected,str) or hashlib.sha256(raw).hexdigest()!=expected
                or selected.get(('config',name))!=raw):
            raise TrainingError('training source not pinned by selected proof')
    if Path(runner).resolve() != root/'azure/sft/run_sft.py':
        raise TrainingError('runner is outside reviewed training package')
    return True

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--lineage-root',required=True);p.add_argument('--payload-root',required=True);p.add_argument('--bundle-manifest',required=True);p.add_argument('--privacy-report',required=True);p.add_argument('--identifier-coverage',required=True);p.add_argument('--control-scan',required=True);p.add_argument('--control-scan-sha256',required=True);p.add_argument('--runner',required=True);p.add_argument('--snapshot',required=True)
    p.add_argument('--data',required=True);p.add_argument('--out',required=True);p.add_argument('--manifest',required=True)
    p.add_argument('--manifest-sha256',required=True);p.add_argument('--lineage',required=True);p.add_argument('--lineage-path',required=True);p.add_argument('--lineage-sha256',required=True);p.add_argument('--blind-key-file',required=True);p.add_argument('--epochs',type=int,required=True)
    p.add_argument('--checkpoint-inventory-out',required=True);p.add_argument('--world-size',type=int,required=True);p.add_argument('--job-timeout',type=int,required=True)
    p.add_argument('--manifest-path',required=True);p.add_argument('--config-commit',required=True);p.add_argument('--protocol-commit',required=True)
    p.add_argument('--git-proof',required=True);p.add_argument('--git-proof-sha256',required=True);p.add_argument('--ancestry',required=True);p.add_argument('--ancestry-sha256',required=True);args=p.parse_args()
    if not TRAINING_ENABLED:raise TrainingError('T1 training disabled pending root review')
    from t1_git_proof import verify
    raw=Path(args.manifest).read_bytes()
    proof=Path(args.git_proof).read_bytes()
    selected=verify(proof,expected_sha256=args.git_proof_sha256,expected_config_commit=args.config_commit,expected_protocol_commit=args.protocol_commit)
    if selected.get(('config',args.manifest_path))!=raw:raise TrainingError('manifest differs from selected committed blob')
    ancestry_raw=Path(args.ancestry).read_bytes()
    if hashlib.sha256(ancestry_raw).hexdigest()!=args.ancestry_sha256:raise TrainingError('root ancestry attestation differs')
    ancestry=json.loads(ancestry_raw)
    if (ancestry.get('status')!='pass' or ancestry.get('config_commit')!=args.config_commit
            or ancestry.get('protocol_commit')!=args.protocol_commit or ancestry.get('git_proof_sha256')!=args.git_proof_sha256
            or ancestry.get('post_c3_ancestry_verified') is not True):
        raise TrainingError('root post-C3 ancestry proof missing')
    if hashlib.sha256(raw).hexdigest()!=args.manifest_sha256:raise TrainingError('manifest bytes changed')
    manifest=json.loads(raw)
    lineage_raw=Path(args.lineage).read_bytes()
    if hashlib.sha256(lineage_raw).hexdigest()!=args.lineage_sha256 or selected.get(('config',args.lineage_path))!=lineage_raw:
        raise TrainingError('training lineage is not the selected committed blob')
    lineage=json.loads(lineage_raw)
    key=Path(args.blind_key_file).read_bytes()
    if hashlib.sha256(key).hexdigest()!=lineage.get('blind_key_sha256'):
        raise TrainingError('blind key differs from committed lineage')
    if Path(__file__).resolve() != (Path(args.root).resolve()/'azure/sft/train_explain.py'):
        raise TrainingError('executing wrapper is outside reviewed package')
    verify_training_package(manifest,selected,args.root,args.runner)
    verify_runtime_packages(manifest)
    if Path(args.root).resolve()!=Path(args.payload_root).resolve():raise TrainingError('source root must be scanned payload root')
    from t1_training_lineage import verify_training_stage
    refs=lineage['inputs'];work=Path(args.lineage_root)
    controls={'config':args.manifest,'proof':args.git_proof,'ancestry':args.ancestry,
        'overlap':work/refs['overlap']['path'],'source':work/refs['source_attestation']['path'],
        'privacy':args.privacy_report,'manifest':args.bundle_manifest,'coverage':args.identifier_coverage,
        'lineage':args.lineage,'sampling_config':work/refs['sampling_config']['path'],
        'sampling_proof':work/lineage['sampling_anchor']['git_proof']['path'],'blind_key':args.blind_key_file}
    verify_training_stage(lineage,work,args.payload_root,args.data,controls=controls,control_scan=args.control_scan,
        control_scan_sha256=args.control_scan_sha256,source_paths=manifest['training_code_sha256'])
    if int(os.environ.get('WORLD_SIZE','1'))!=args.world_size:raise TrainingError('actual node count differs from approved job')
    cmd=build_command(runner=args.runner,snapshot=args.snapshot,data=args.data,out=args.out,epochs=args.epochs,
                      world_size=args.world_size,manifest=manifest,train_sha256=manifest['train_sha256'],val_sha256=manifest['val_sha256'],
                      lineage=lineage,root=args.lineage_root,blind_key=key)
    if args.job_timeout<=0:raise TrainingError('job timeout required')
    inventory_path=Path(args.checkpoint_inventory_out)
    if inventory_path.exists() or inventory_path.is_symlink() or not inventory_path.parent.is_dir():
        raise TrainingError('fresh checkpoint inventory output required')
    cmd += ['--job-timeout',str(args.job_timeout)]
    rank=os.environ.get('RANK')
    if rank is None or not rank.isdigit():raise TrainingError('distributed rank is required for checkpoint capture')
    result=subprocess.run(cmd,check=False)
    if result.returncode:raise SystemExit(result.returncode)
    if int(rank)!=0:return
    from t1_checkpoint_inventory import capture
    inventory=capture(args.out,lineage_sha256=args.lineage_sha256,training_manifest_sha256=args.manifest_sha256,
        base_tree_sha256=lineage['base']['model_tree_sha256'],dev_sha256=lineage['val_sha256'],run_id=manifest['training_run_id'])
    fd=os.open(args.checkpoint_inventory_out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(inventory,f,sort_keys=True);f.write('\n')
if __name__=='__main__':main()
