"""Inert T2 candidate. Pure helpers accept synthetic rows only; no archive opens on import."""
import hashlib
import json
import math
from decimal import Decimal, localcontext

BUILD_ENABLED = False
MAX_PAIRS = 100_000

class PairError(ValueError):
    pass

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def select_pair(worlds, legal):
    """Select values from all positive-weight worlds, using the public menu order."""
    if not isinstance(legal, list) or len(legal) < 2 or any(not isinstance(a, str) or not a for a in legal) or len(set(legal)) != len(legal):
        raise PairError('invalid legal menu')
    if not isinstance(worlds, list):
        raise PairError('missing worlds')
    valid = []
    for world in worlds:
        if not isinstance(world, dict):
            raise PairError('malformed world')
        chance = world.get('chance')
        if type(chance) not in (int, float) or not math.isfinite(chance) or chance < 0:
            raise PairError('invalid chance')
        if chance == 0:
            continue
        opts = world.get('side_one')
        if not isinstance(opts, list):
            raise PairError('missing options')
        seen = {}
        for o in opts:
            if not isinstance(o, dict) or not isinstance(o.get('m'), str) or not o['m'] or o['m'] in seen:
                raise PairError('invalid or duplicate option')
            seen[o['m']] = o
        if set(seen) != set(legal):
            raise PairError('world menu differs')
        values = {}
        for action in legal:
            v, s = seen[action].get('v'), seen[action].get('s')
            if type(v) not in (int, float) or type(s) not in (int, float) or not math.isfinite(v) or not math.isfinite(s) or v <= 0:
                raise PairError('invalid score or visits')
            value = s
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise PairError('score outside raw win-score range')
            values[action] = value
        valid.append((Decimal(str(chance)), {a: Decimal(str(seen[a]['s'])) for a in legal}))
    if len(valid) < 3:
        return None
    weights = sum(w for w, _ in valid)
    if not weights.is_finite() or weights <= 0:
        raise PairError('invalid total chance')
    with localcontext() as ctx:
        ctx.prec = 50
        averages = {a: sum(w * vals[a] for w, vals in valid) / weights for a in legal}
    best = max(legal, key=lambda a: averages[a])
    candidates = []
    for a in legal:
        if a == best:
            continue
        gap = averages[best] - averages[a]
        agree = sum(vals[best] > vals[a] for _, vals in valid)
        if gap >= Decimal('0.05') and agree >= 3:
            candidates.append((a, gap, agree))
    if not candidates:
        return None
    alt, gap, agree = max(candidates, key=lambda item: averages[item[0]])
    return {'chosen': best, 'rejected': alt, 'chosen_value': float(averages[best]),
            'rejected_value': float(averages[alt]), 'value_gap': float(gap), 'agreeing_worlds': agree,
            'positive_worlds': len(valid)}

def serialize_pair(row, game, legal, selection, gc):
    import serialize as S
    import build_sft as B
    cut = row.get('history_end')
    if type(cut) is not int or not 0 <= cut <= len(game['log']):
        raise PairError('invalid history cut')
    ex = S.build_example(row['view'], game['log'][:cut], legal, row['player'], row['turn'], row['k'], bool(row.get('team_preview')), task='action')
    mapping = ex.get('id_map')
    if not isinstance(mapping, dict) or list(mapping.values()) != legal or len(set(mapping.values())) != len(legal):
        raise PairError('menu mapping mismatch')
    inverse = {action: cid for cid, action in mapping.items()}
    encoded = []
    for action in (selection['chosen'], selection['rejected']):
        if action not in inverse:
            raise PairError('target outside menu')
        e = gc.encode(ex['messages'], ex['tools'], S.target_message(action, mapping))
        ids, labels, n = e.get('input_ids'), e.get('labels'), e.get('n_prompt')
        if not isinstance(ids, list) or not isinstance(labels, list) or type(n) is not int or not 0 < n < len(ids) <= 2048 or len(ids) != len(labels) or any(type(x) is not int or x < 0 for x in ids) or labels != [-100]*n + ids[n:] or ids[-1] != gc.eot_id:
            raise PairError('invalid target encoding')
        try:
            info = B.choice_token_info(gc, e, mapping, inverse[action], {inverse[action]: 1.0})
        except (ValueError, KeyError, IndexError, StopIteration) as exc:
            raise PairError('choice token unresolved') from exc
        if info.get('multi_token_ids') or type(info.get('choice_pos')) is not int or not n <= info['choice_pos'] < len(ids) or ids[info['choice_pos']] != info.get('hard_tok') or len(set(info.get('soft_tok', []))) != len(mapping):
            # choice_token_info collapses identical token IDs; all IDs must be distinct.
            raise PairError('choice IDs are not one-token distinguishable')
        mark=B.ID_MARK.format(inverse[action]); text=e.get('target_text')
        if not isinstance(text,str) or text.count(mark)!=1:
            raise PairError('choice marker missing')
        response=ids[n:]
        for cid in mapping:
            variant=gc.ids(text.replace(mark,B.ID_MARK.format(cid)))
            if len(variant)!=len(response): raise PairError('multi-token choice')
            differences=[i for i,(x,y) in enumerate(zip(response,variant)) if x!=y]
            if cid==inverse[action]:
                if differences: raise PairError('target tokenization differs')
            elif differences!=[info['choice_pos']-n]:
                raise PairError('choice is not independently one-token')
        encoded.append((e, info))
    a, b = encoded[0][0], encoded[1][0]
    if a['n_prompt'] != b['n_prompt'] or a['input_ids'][:a['n_prompt']] != b['input_ids'][:b['n_prompt']]:
        raise PairError('prompt prefix mismatch')
    ac, bc = encoded[0][1]['choice_pos'], encoded[1][1]['choice_pos']
    if ac != bc or len(a['input_ids']) != len(b['input_ids']) or sum(x != y for x, y in zip(a['input_ids'], b['input_ids'])) != 1 or a['input_ids'][ac] == b['input_ids'][bc]:
        raise PairError('target choice differs by more than one token')
    return {'chosen_id': inverse[selection['chosen']], 'rejected_id': inverse[selection['rejected']],
            'prompt_sha256': digest(a['input_ids'][:a['n_prompt']]),
            'chosen_response_sha256': digest(a['input_ids'][a['n_prompt']:]),
            'rejected_response_sha256': digest(b['input_ids'][b['n_prompt']:]),
            'chosen': {'input_ids': a['input_ids'], 'labels': a['labels']},
            'rejected': {'input_ids': b['input_ids'], 'labels': b['labels']}}

class _ReverseLex:
    def __init__(self,value): self.value=value
    def __lt__(self,other): return self.value>other.value
    def __eq__(self,other): return self.value==other.value

def deterministic_cap(records, limit=MAX_PAIRS):
    if type(limit) is not int or limit < 0:
        raise PairError('invalid cap')
    ids, pairs = set(), set()
    for r in records:
        uid = r.get('decision_uid'); pair_id = r.get('pair_id'); source = r.get('archive_sha256')
        if not isinstance(uid, str) or not uid or not isinstance(source, str) or len(source) != 64 or any(c not in '0123456789abcdef' for c in source) or not isinstance(pair_id, str) or len(pair_id) != 64:
            raise PairError('invalid source-bound identity')
        if uid in ids or pair_id in pairs:
            raise PairError('duplicate decision or pair')
        ids.add(uid); pairs.add(pair_id)
    return sorted(records, key=lambda r: (hashlib.sha256((r['archive_sha256'] + '\0' + r['decision_uid']).encode()).hexdigest(), r['decision_uid']))[:limit]

def build(root, spec_ref, card_ref, approval_ref, index_ref, index_review_ref, destination, *, dpo_spec_ref=None, dpo_review_ref=None, dpo_approval_ref=None, execute=False):
    """Revalidate the accepted index before using its authenticated archive stream."""
    if not BUILD_ENABLED or execute is not True:
        raise PairError('DPO build disabled')
    import os
    import heapq
    import shutil
    import tempfile
    from collections import Counter
    from pathlib import Path
    import data_admission as A
    import data_admission_runtime as R
    import serialize as S
    import sys
    import build_sft as B
    import glimmer_chat as G
    from glimmer_chat import GlimmerChat

    for module,expected_path in ((S,'tools/sft/serialize.py'),(B,'tools/sft/build_sft.py'),(G,'tools/sft/glimmer_chat.py')):
        if Path(module.__file__).resolve()!=(Path(root)/expected_path).resolve(): raise PairError('loaded serializer source differs')

    # Bind the DPO-specific implementation and output to its independently reviewed
    # prospective specification; the SFT index authorization alone is insufficient.
    dpo_spec_raw = R._read_ref(root, dpo_spec_ref)
    dpo_spec = json.loads(dpo_spec_raw)
    if not isinstance(dpo_review_ref,dict) or set(dpo_review_ref)!={'commit','path','sha256'}:
        raise PairError('DPO source review missing')
    reviewed = json.loads(R._committed(root,dpo_review_ref['commit'],dpo_review_ref['path'],dpo_review_ref['sha256']))
    if not isinstance(reviewed,dict) or reviewed.get('status')!='accepted' or reviewed.get('spec_sha256')!=dpo_spec_ref['sha256'] or not isinstance(reviewed.get('reviewer'),str) or not reviewed['reviewer'].strip():
        raise PairError('DPO source review differs')
    if not isinstance(dpo_approval_ref,dict) or set(dpo_approval_ref)!={'commit','path','sha256'}:
        raise PairError('DPO build authorization missing')
    approval=json.loads(R._committed(root,dpo_approval_ref['commit'],dpo_approval_ref['path'],dpo_approval_ref['sha256']))
    if not isinstance(approval,dict) or approval.get('status')!='approved' or approval.get('purpose')!='dpo_build' or approval.get('spec_sha256')!=dpo_spec_ref['sha256'] or approval.get('source_review_sha256')!=dpo_review_ref['sha256'] or not isinstance(approval.get('owner'),str) or not approval['owner'].strip():
        raise PairError('DPO build authorization differs')
    expected_plan='9d6d411405d1120165597b9e57b2d6ec9b934411534e9afa4d729331287880c4'
    if not isinstance(dpo_spec,dict) or dpo_spec.get('status')!='FROZEN_FOR_REVIEW' or dpo_spec.get('plan_sha256')!=expected_plan or dpo_spec.get('source_index_sha256')!=index_ref.get('sha256') or dpo_spec.get('output_dir')!=destination or dpo_spec.get('max_pairs')!=MAX_PAIRS:
        raise PairError('DPO prospective specification differs')
    code=dpo_spec.get('code_sha256')
    required={'tools/sft/build_dpo.py','tools/sft/data_admission.py','tools/sft/data_admission_runtime.py','tools/sft/build_sft.py','tools/sft/serialize.py','tools/sft/glimmer_chat.py','tools/sft/dex.json','tools/dataset/extract.py','tools/belief/roster_hash.py','tools/human/parse_replays.py','azure/student/p6_trainonly_inspect.py'}
    if not isinstance(code,dict) or not required.issubset(code) or Path(__file__).resolve()!=(Path(root)/'tools/sft/build_dpo.py').resolve():
        raise PairError('DPO code closure missing')
    for name, pin in code.items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or not A._sha(pin): raise PairError('bad source pin')
        path=Path(root)/path
        if path.is_symlink() or not path.is_file() or R.file_sha(path)!=pin: raise PairError('source bytes differ')
    spec = R.authorize(root, spec_ref, card_ref, approval_ref, 'build', execute=True)
    if not isinstance(index_review_ref, dict) or set(index_review_ref) != {'commit', 'path', 'sha256'}:
        raise PairError('independent index review missing')
    review = json.loads(R._committed(root, index_review_ref['commit'], index_review_ref['path'], index_review_ref['sha256']))
    if not isinstance(review, dict) or review.get('status') != 'accepted' or review.get('index_sha256') != index_ref.get('sha256') or review.get('spec_sha256') != spec_ref['sha256'] or review.get('card_sha256') != card_ref['sha256'] or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        raise PairError('independent index review differs')
    receipt, state = R.derive_index(root, spec, spec_ref['sha256'])
    if A.canonical(receipt) + b'\n' != R._read_ref(root, index_ref):
        raise PairError('reviewed index rederivation differs')
    R.authorize(root, spec_ref, card_ref, approval_ref, 'build', execute=True)
    dest = R._private_output(root, destination)
    tokenizer = Path(spec['tokenizer_dir'])
    gc = GlimmerChat(tokenizer if tokenizer.is_absolute() else Path(root) / tokenizer, verify=True)
    tmp = Path(tempfile.mkdtemp(prefix='.dpo-build-', dir=dest.parent)); os.chmod(tmp, 0o700)
    spool=None
    try:
        spool=R.RecordSpool(tmp/'records.spool')
        kept = set(state['kept']); seen_ids = set(); seen_order = set(); example_ids = set()
        heap = []; seen_pair_ids=set(); seen_pair_content=set(); drops = Counter(); admitted = 0; qualified=0
        for batch, rows in R.iter_verified_batches(root, state, receipt):
            games = {g['key']: g for g in batch}
            for row in rows:
                key = row['uid'].rsplit('/', 2)[0]
                if key not in kept:
                    continue
                admitted += 1
                game = games[key]
                A.decision_identity(row, game, seen_ids, seen_order)
                if row.get('cfg') != 'C1t1':
                    drops['cfg'] += 1; continue
                if type(row.get('log_version')) is not int or row['log_version'] not in (2,3):
                    drops['log_version'] += 1; continue
                worlds = row.get('worlds')
                try:
                    if not isinstance(worlds, list):
                        raise PairError('missing worlds')
                    union = set()
                    for w in worlds:
                        if not isinstance(w, dict):
                            raise PairError('malformed world')
                        if w.get('chance') == 0 and type(w.get('chance')) in (int, float):
                            continue
                        if not isinstance(w.get('side_one'), list):
                            raise PairError('malformed world')
                        for o in w['side_one']:
                            if not isinstance(o, dict) or not isinstance(o.get('m'), str) or not o['m']:
                                raise PairError('malformed option')
                            union.add(o['m'])
                    legal = S.ordered_legal(row['view'], sorted(union), bool(row.get('team_preview')))
                    if len(legal) < 2:
                        drops['min_legal'] += 1; continue
                    selection = select_pair(worlds, legal)
                    if selection is None:
                        drops['no_qualifying_pair'] += 1; continue
                    enc = serialize_pair(row, game, legal, selection, gc)
                except (PairError, ValueError, KeyError, TypeError, OverflowError, IndexError):
                    drops['invalid_pair_or_encoding'] += 1; continue
                eid = row['uid'] + '/action'; example_ids.add(eid)
                prov = {'archive_sha256': game['archive_sha256'], 'decision_uid': row['uid'], 'game_key': key,
                        'component': state['index']['components'][key], 'chosen_action': selection['chosen'],
                        'rejected_action': selection['rejected'], 'value_gap': selection['value_gap'],
                        'agreeing_worlds': selection['agreeing_worlds'], 'positive_worlds': selection['positive_worlds']}
                record=dict(prov, pair_id=digest(prov), **enc)
                pair_content=digest({'chosen':enc['chosen'],'rejected':enc['rejected']})
                if record['pair_id'] in seen_pair_ids or pair_content in seen_pair_content:
                    drops['duplicate_pair']+=1; continue
                seen_pair_ids.add(record['pair_id']); seen_pair_content.add(pair_content); qualified+=1
                rank=hashlib.sha256((record['archive_sha256']+'\0'+record['decision_uid']).encode()).hexdigest()
                item=(-int(rank,16),_ReverseLex(record['decision_uid']))
                if len(heap)<MAX_PAIRS or item>heap[0][:2]:
                    offset=spool.file.tell(); spool.append(record)
                    entry=(*item,offset)
                    if len(heap)<MAX_PAIRS: heapq.heappush(heap,entry)
                    else: heapq.heapreplace(heap,entry)
        if admitted != receipt['exclusion_reconciliation']['admitted_raw_decisions']:
            raise PairError('admitted decision count differs from index')
        overlap = A.verify_zero_overlap(state['index'], kept, state['hold'], state['heldout'], state['reserved_names'], state['reserved_rosters'], seen_ids, example_ids, heldout_names=state['heldout_names'])
        if not heap:
            raise PairError('no qualifying pairs')
        spool.file.flush(); os.fsync(spool.file.fileno())
        output_hash=hashlib.sha256()
        fd=os.open(tmp/'pairs.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:
            for _,_,offset in sorted(heap,key=lambda entry:(-entry[0],entry[1].value)):
                spool.file.seek(offset); header=spool.file.read(8)
                if len(header)!=8: raise PairError('spool offset truncated')
                size=int.from_bytes(header,'big'); data=spool.file.read(size)
                if len(data)!=size: raise PairError('spool record truncated')
                out.write(data); output_hash.update(data)
            out.flush(); os.fsync(out.fileno())
        spool.close(); spool=None; (tmp/'records.spool').unlink()
        pair_sha=output_hash.hexdigest()
        if R.file_sha(tmp/'pairs.jsonl')!=pair_sha: raise PairError('pair readback differs')
        manifest = {'schema':'hardread.dpo.pairs/v1','index_sha256':index_ref['sha256'],
                    'spec_sha256':spec_ref['sha256'],'dpo_spec_sha256':dpo_spec_ref['sha256'],
                    'dpo_source_review_sha256':dpo_review_ref['sha256'],'dpo_approval_sha256':dpo_approval_ref['sha256'],
                    'tokenizer_sha256':gc.tokenizer_sha256,'template_sha256':gc.template_sha256,
                    'qualified_count':qualified,'count':len(heap),'drops':dict(drops),
                    'overlap':overlap,'pairs_sha256':pair_sha}
        fd=os.open(tmp/'manifest.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:
            out.write(canonical(manifest)+b'\n'); out.flush(); os.fsync(out.fileno())
        R.authorize(root,spec_ref,card_ref,approval_ref,'build',execute=True)
        if R._read_ref(root,dpo_spec_ref)!=dpo_spec_raw: raise PairError('DPO specification changed')
        for name,pin in code.items():
            if R.file_sha(Path(root)/name)!=pin: raise PairError('source changed during build')
        if dest.exists(): raise PairError('output already exists')
        os.rename(tmp,dest)
    except BaseException:
        if spool is not None: spool.close()
        shutil.rmtree(tmp,ignore_errors=True); raise
    return {'output': str(dest), 'pair_count': len(heap), 'manifest_sha256': R.file_sha(dest / 'manifest.json')}


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('root','spec-ref','card-ref','approval-ref','index-ref','index-review-ref','destination','dpo-spec-ref','dpo-review-ref','dpo-approval-ref'):
        parser.add_argument('--'+key,required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    refs=[json.loads(getattr(args,k)) for k in ('spec_ref','card_ref','approval_ref','index_ref','index_review_ref')]
    result=build(args.root,*refs,args.destination,dpo_spec_ref=json.loads(args.dpo_spec_ref),dpo_review_ref=json.loads(args.dpo_review_ref),dpo_approval_ref=json.loads(args.dpo_approval_ref),execute=args.execute)
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__': main()
