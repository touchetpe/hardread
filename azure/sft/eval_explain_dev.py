"""Explain-only teacher dev loss; no action metrics. Execution is disabled."""
import argparse
import gzip
import hashlib
import json
import math
import os
import re
from pathlib import Path

EXPLAIN_EVAL_ENABLED=False
class EvaluationError(ValueError):pass

def summarize(rows):
    total_loss=0.0;total_tokens=0
    if not isinstance(rows,list) or not rows:raise EvaluationError('dev scores missing')
    for row in rows:
        if type(row.get('tokens')) is not int or row['tokens']<=0 or type(row.get('nll_sum')) not in (int,float) or not math.isfinite(row['nll_sum']) or row['nll_sum']<0:
            raise EvaluationError('invalid dev loss record')
        total_tokens+=row['tokens'];total_loss+=row['nll_sum']
    return {'metric':'explain_teacher_token_cross_entropy','examples':len(rows),'supervised_tokens':total_tokens,
            'nll_sum':total_loss,'cross_entropy':total_loss/total_tokens}

def choose_checkpoint(receipts,inventory_raw,inventory_sha256,*,selected,inventory_path):
    if (not isinstance(inventory_raw,bytes) or hashlib.sha256(inventory_raw).hexdigest()!=inventory_sha256
            or selected.get(('config',inventory_path))!=inventory_raw):raise EvaluationError('checkpoint inventory is not committed')
    inventory=json.loads(inventory_raw)
    if not isinstance(receipts,list) or not receipts or not isinstance(inventory,dict) or inventory.get('status')!='reviewed':raise EvaluationError('reviewed checkpoint inventory and dev receipts required')
    entries=inventory.get('checkpoints')
    if not isinstance(entries,list) or not entries or len({x.get('id') for x in entries})!=len(entries):raise EvaluationError('checkpoint inventory invalid')
    expected={x['id']:x['tree_sha256'] for x in entries}
    if len(receipts)!=len(expected) or {r.get('checkpoint_id') for r in receipts}!=set(expected):raise EvaluationError('exact checkpoint receipt set required')
    for r in receipts:
        if (r.get('metric')!='explain_teacher_token_cross_entropy' or r.get('checkpoint_tree_sha256')!=expected[r['checkpoint_id']]
                or r.get('checkpoint_inventory_sha256')!=inventory_sha256 or r.get('training_manifest_sha256')!=inventory.get('training_manifest_sha256')
                or r.get('training_lineage_sha256')!=inventory.get('lineage_sha256') or r.get('base_tree_sha256')!=inventory.get('base_tree_sha256')
                or r.get('dev_sha256')!=inventory.get('dev_sha256') or r.get('run_id')!=inventory.get('run_id')
                or type(r.get('cross_entropy')) not in (int,float) or not math.isfinite(r['cross_entropy'])):
            raise EvaluationError('checkpoint dev receipt provenance differs')
    return min(receipts,key=lambda r:(r['cross_entropy'],r['checkpoint_id']))

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--lineage-root',required=True);p.add_argument('--payload-root',required=True);p.add_argument('--bundle-manifest',required=True);p.add_argument('--privacy-report',required=True);p.add_argument('--identifier-coverage',required=True);p.add_argument('--control-scan',required=True);p.add_argument('--control-scan-sha256',required=True);p.add_argument('--runner',required=True);p.add_argument('--snapshot',required=True);p.add_argument('--adapter',required=True)
    p.add_argument('--blind-key-file',required=True);p.add_argument('--lineage',required=True);p.add_argument('--lineage-path',required=True);p.add_argument('--lineage-sha256',required=True);p.add_argument('--checkpoint-inventory',required=True);p.add_argument('--checkpoint-inventory-path',required=True);p.add_argument('--checkpoint-inventory-sha256',required=True);p.add_argument('--checkpoint-id',required=True);p.add_argument('--config-commit',required=True);p.add_argument('--protocol-commit',required=True);p.add_argument('--git-proof',required=True);p.add_argument('--git-proof-sha256',required=True);p.add_argument('--ancestry',required=True);p.add_argument('--ancestry-sha256',required=True);p.add_argument('--manifest-path',required=True);p.add_argument('--dev',required=True);p.add_argument('--dev-sha256',required=True);p.add_argument('--manifest',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--out',required=True)
    args=p.parse_args()
    if not EXPLAIN_EVAL_ENABLED:raise EvaluationError('explain dev inference disabled pending root review')
    from t1_git_proof import verify
    from t1_training_lineage import validate_base,digest
    from t1_checkpoint_inventory import verify_checkpoint
    selected=verify(Path(args.git_proof).read_bytes(),expected_sha256=args.git_proof_sha256,expected_config_commit=args.config_commit,expected_protocol_commit=args.protocol_commit)
    ancestry_raw=Path(args.ancestry).read_bytes()
    if hashlib.sha256(ancestry_raw).hexdigest()!=args.ancestry_sha256:raise EvaluationError('ancestry receipt changed')
    ancestry=json.loads(ancestry_raw)
    if (ancestry.get('status')!='pass' or ancestry.get('config_commit')!=args.config_commit
            or ancestry.get('protocol_commit')!=args.protocol_commit or ancestry.get('git_proof_sha256')!=args.git_proof_sha256
            or ancestry.get('post_c3_ancestry_verified') is not True):raise EvaluationError('reviewed ancestry missing')
    manifest_raw=Path(args.manifest).read_bytes()
    if hashlib.sha256(manifest_raw).hexdigest()!=args.manifest_sha256:raise EvaluationError('manifest bytes differ')
    if selected.get(('config',args.manifest_path))!=manifest_raw:raise EvaluationError('training manifest not committed')
    manifest=json.loads(manifest_raw)
    lineage_raw=Path(args.lineage).read_bytes();inventory_raw=Path(args.checkpoint_inventory).read_bytes()
    if (hashlib.sha256(lineage_raw).hexdigest()!=args.lineage_sha256 or selected.get(('config',args.lineage_path))!=lineage_raw
            or hashlib.sha256(inventory_raw).hexdigest()!=args.checkpoint_inventory_sha256
            or selected.get(('config',args.checkpoint_inventory_path))!=inventory_raw):raise EvaluationError('lineage or reviewed checkpoint inventory not committed')
    lineage=json.loads(lineage_raw);inventory=json.loads(inventory_raw)
    from train_explain import verify_training_package,verify_runtime_packages
    if Path(__file__).resolve()!=(Path(args.root).resolve()/'azure/sft/eval_explain_dev.py'):raise EvaluationError('executing evaluator differs')
    verify_training_package(manifest,selected,args.root,args.runner)
    verify_runtime_packages(manifest)
    if Path(args.root).resolve()!=Path(args.payload_root).resolve():raise EvaluationError('source root must be scanned payload')
    from t1_training_lineage import verify_training_stage
    work=Path(args.lineage_root);refs=lineage['inputs']
    controls={'config':args.manifest,'proof':args.git_proof,'ancestry':args.ancestry,
        'overlap':work/refs['overlap']['path'],'source':work/refs['source_attestation']['path'],
        'privacy':args.privacy_report,'manifest':args.bundle_manifest,'coverage':args.identifier_coverage,
        'lineage':args.lineage,'sampling_config':work/refs['sampling_config']['path'],
        'sampling_proof':work/lineage['sampling_anchor']['git_proof']['path'],'checkpoint_inventory':args.checkpoint_inventory,'blind_key':args.blind_key_file}
    verify_training_stage(lineage,work,args.payload_root,Path(args.dev).parent,controls=controls,control_scan=args.control_scan,
        control_scan_sha256=args.control_scan_sha256,source_paths=manifest['training_code_sha256'])
    if manifest.get('training_lineage_sha256')!=digest(lineage) or manifest.get('training_run_id')!=inventory.get('run_id'):
        raise EvaluationError('training lineage or run differs')
    validate_base(args.snapshot,lineage['base'])
    checkpoint=verify_checkpoint(inventory,args.adapter,args.checkpoint_id,lineage_sha256=args.lineage_sha256,
        training_manifest_sha256=args.manifest_sha256,base_tree_sha256=lineage['base']['model_tree_sha256'],dev_sha256=args.dev_sha256)
    if manifest.get('status')!='committed_final_overlap_verified' or manifest.get('task')!='explain' or manifest.get('val_sha256')!=args.dev_sha256:
        raise EvaluationError('committed training-only dev manifest required')
    raw=Path(args.dev).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=args.dev_sha256:raise EvaluationError('dev bytes differ')
    rows=[]
    with gzip.open(args.dev,'rt') as f:
        for line in f:
            row=json.loads(line)
            ids=row.get('input_ids');labels=row.get('labels')
            if row.get('task')!='explain' or not isinstance(ids,list) or not isinstance(labels,list) or len(ids)!=len(labels) or not any(x!=-100 for x in labels):
                raise EvaluationError('invalid explain-only dev row')
            rows.append(row)
    if not rows:raise EvaluationError('empty dev set')
    import torch
    from peft import PeftModel
    from transformers import AutoModelForImageTextToText
    from glimmer_chat import GlimmerChat
    gc=GlimmerChat(args.snapshot)
    if manifest.get('tokenizer_sha256')!=gc.tokenizer_sha256 or manifest.get('chat_template_sha256')!=gc.template_sha256:
        raise EvaluationError('dev tokenizer or template differs')
    base=AutoModelForImageTextToText.from_pretrained(args.snapshot,local_files_only=True,dtype=torch.bfloat16,
                                                        attn_implementation='flash_attention_2',device_map='auto')
    model=PeftModel.from_pretrained(base,args.adapter,is_trainable=False,local_files_only=True);model.eval()
    device=model.get_input_embeddings().weight.device;scores=[]
    for row in rows:
        x=torch.tensor([row['input_ids']],device=device);labels=torch.tensor([row['labels']],device=device)
        with torch.inference_mode():
            logits=model(input_ids=x).logits[:,:-1,:].float()
            targets=labels[:,1:]
            loss=torch.nn.functional.cross_entropy(logits.reshape(-1,logits.shape[-1]),targets.reshape(-1),ignore_index=-100,reduction='sum')
        scores.append({'tokens':int((targets!=-100).sum().item()),'nll_sum':float(loss.item())})
    result=summarize(scores);result['dev_sha256']=args.dev_sha256;result.update({'checkpoint_id':args.checkpoint_id,'checkpoint_tree_sha256':checkpoint['tree_sha256'],
        'checkpoint_inventory_sha256':args.checkpoint_inventory_sha256,'training_manifest_sha256':args.manifest_sha256,
        'training_lineage_sha256':args.lineage_sha256,'base_tree_sha256':lineage['base']['model_tree_sha256'],'run_id':inventory['run_id']})
    fd=os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,sort_keys=True);f.write('\n')
if __name__=='__main__':main()
