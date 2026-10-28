"""Prospective A1 v2 audit projection. Only recorded scoped evidence is used."""
import copy,hashlib,hmac,json,os,random,re,stat
from pathlib import Path
from a1_v2_capture import visible_receipts,canonical,CaptureError
from render import render_sentence

class AuditError(ValueError): pass
SEED=20261027
INSTRUCTIONS=('Assess only the recorded scope. luck_pts is attributed luck; swing_pts is a counterfactual swing. '
              'An empty query does not establish facts outside that query. A ranking is limited to its shown scope. '
              'Use categories a–f: attribution, direction, unsupported causality, unsupported verdict, certainty, association.')

def sha(obj): return hashlib.sha256(canonical(obj).encode()).hexdigest()
def _records(obj):
    if isinstance(obj,dict):
        if 'id' in obj:
            if not isinstance(obj['id'],str) or not re.fullmatch(r'#[0-9]+',obj['id']): raise AuditError('invalid record id')
            yield obj
        for v in obj.values(): yield from _records(v)
    elif isinstance(obj,list):
        for v in obj: yield from _records(v)
    elif not isinstance(obj,(str,int,float,bool,type(None))): raise AuditError('unsupported receipt type')

def _reject_metadata(value):
    forbidden={'arm','raw_pass','after_repair_pass','raw_repaired_status','verifier_result','item_index','source_path','original_label'}
    if isinstance(value,dict):
        if forbidden.intersection(value): raise AuditError('blinded packet metadata leak')
        for x in value.values(): _reject_metadata(x)
    elif isinstance(value,list):
        for x in value: _reject_metadata(x)

def project(row):
    result=row.get('result')
    try: receipts=visible_receipts(result,row.get('capture'))
    except (CaptureError,TypeError,KeyError) as exc: raise AuditError('unproven model-visible trace') from exc
    index={}
    for i,r in enumerate(receipts):
        for rec in _records(r['output']):
            if rec['id'] in index: raise AuditError('duplicate record ID')
            index[rec['id']]=(i,rec)
    # Re-run the production renderer on the exact visible records, binding shown formatting.
    class Session:
        calls=[{'name':r['tool']} for r in receipts]
        record_call={rid:pair[0] for rid,pair in index.items()}
        def resolve(self,ref):
            if not isinstance(ref,str) or '.' not in ref: raise KeyError(ref)
            rid,field=ref.split('.',1);rec=index[rid][1]
            return rec,field,rec[field]
    sentences=(result.get('rendered') or {}).get('sentences')
    if not isinstance(sentences,list): raise AuditError('rendered sentences missing')
    out=[];seen=set()
    for s in sentences:
        if not isinstance(s,dict) or type(s.get('i')) is not int or s['i'] in seen or not isinstance(s.get('text'),str) or not s['text'].strip(): raise AuditError('sentence identity missing')
        seen.add(s['i']);cites=s.get('citations')
        if not isinstance(cites,list) or not isinstance(s.get('template'),str) or not isinstance(s.get('slots'),dict): raise AuditError('sentence structure missing')
        try: text,derived=render_sentence(s['template'],s['slots'],Session())
        except (KeyError,TypeError,ValueError,IndexError) as exc: raise AuditError('renderer replay failed') from exc
        if text!=s['text'] or canonical(derived)!=canonical(cites): raise AuditError('citation value or shown text differs')
        cited=[];seen_refs=set()
        for c in cites:
            if not isinstance(c,dict) or not isinstance(c.get('ref'),str) or '.' not in c['ref']: raise AuditError('citation malformed')
            if c['ref'] in seen_refs:raise AuditError('duplicate citation reference')
            seen_refs.add(c['ref'])
            rid,field=c['ref'].split('.',1)
            if rid not in index: raise AuditError('unresolved citation')
            i,rec=index[rid]
            if c.get('tool')!=receipts[i]['tool'] or c.get('field')!=field or field not in rec or canonical(c.get('value'))!=canonical(rec[field]): raise AuditError('citation differs from exact return')
            cited.append({'citation':copy.deepcopy(c),'receipt':copy.deepcopy(receipts[i]),'record':copy.deepcopy(rec)})
        out.append({'question':row['question'],'sentence':s['text'],'citations':copy.deepcopy(cites),
                    'cited_receipts':cited,'data_tool_receipts':copy.deepcopy(receipts),'_sentence':s['i']})
    for packet in out: _reject_metadata(packet)
    return out

def derive(rows,*,item_hash,manifest_hash,config_hash,source_hash,blind_key):
    if not isinstance(blind_key,bytes) or len(blind_key)<32: raise AuditError('private audit key required')
    if not all(isinstance(x,str) and re.fullmatch('[0-9a-f]{64}',x) for x in (item_hash,manifest_hash,config_hash,source_hash)): raise AuditError('missing source pins')
    if not isinstance(rows,list) or len(rows)!=60 or [r.get('i') for r in rows]!=list(range(60)): raise AuditError('exact ordered 60-item trace required')
    population=[]
    for row in rows:
        b=row.get('trace_binding') or {}
        if b.get('items_sha256')!=item_hash or b.get('manifest_sha256')!=manifest_hash or b.get('config_sha256')!=config_hash or b.get('source_sha256')!=source_hash: raise AuditError('trace source binding differs')
        result=row.get('result') or {}
        if row.get('final')!=result.get('final') or result.get('final') not in ('glimmer_raw','glimmer_repaired','template'): raise AuditError('invalid final outcome')
        # Even template traces must have fully proven model requests and returns.
        if result['final']=='template':
            try: visible_receipts(result,row.get('capture'))
            except (CaptureError,TypeError,KeyError) as exc: raise AuditError('unproven template trace') from exc
            continue
        projected=project(row)
        if result['final']!='template':
            for p in projected: p['_item']=row['i'];population.append(p)
    sample=population if len(population)<=60 else random.Random(SEED).sample(population,60)
    owner=set(random.Random(15).sample(range(len(sample)),min(15,len(sample))))
    packets=[];key=[]
    for i,p in enumerate(sample):
        source={'item':p.pop('_item'),'sentence':p.pop('_sentence')}
        aid=hmac.new(blind_key,canonical({'item':source['item'],'sentence':source['sentence'],'config':config_hash}).encode(),hashlib.sha256).hexdigest()
        packets.append(dict(p,audit_id=aid,owner_spot_check=i in owner));key.append(dict(source,audit_id=aid))
    if len({r['audit_id'] for r in packets})!=len(packets): raise AuditError('duplicate audit identity')
    return {'schema':'hardread.a1.audit-v2','population':len(population),'packets':packets,'key':key,'instructions':INSTRUCTIONS,
            'binding':{'trace_sha256':sha(rows),'items_sha256':item_hash,'manifest_sha256':manifest_hash,'config_sha256':config_hash,'source_sha256':source_hash,'seed':SEED,'owner_seed':15,'packet_sha256':sha(packets),'key_sha256':sha(key)}}

def _score_derived(rows, frozen, labels, owner_labels, **pins):
    rebuilt=derive(rows,**pins)
    if canonical(rebuilt)!=canonical(frozen): raise AuditError('packet, source or receipt changed')
    packets=rebuilt['packets'];required={r['audit_id'] for r in packets};owners={r['audit_id'] for r in packets if r['owner_spot_check']}
    def validate(values,expected):
        if not isinstance(values,list) or len(values)!=len(expected): raise AuditError('incomplete labels')
        by={}
        for row in values:
            if not isinstance(row,dict) or set(row)-{'audit_id','misleading','category','note'} or row.get('audit_id') in by or row.get('audit_id') not in expected or type(row.get('misleading')) is not bool or (row['misleading'] and row.get('category') not in tuple('abcdef')) or (not row['misleading'] and row.get('category') not in (None,*'abcdef')): raise AuditError('invalid or duplicate label')
            by[row['audit_id']]=row
        if set(by)!=expected: raise AuditError('missing label')
        return by
    labs=validate(labels,required);own=validate(owner_labels,owners);n=len(packets);k=sum(r['misleading'] for r in labs.values())
    if len(owners)<15: raise AuditError('owner subset incomplete')
    from audit_a1 import wilson
    return {'status':'complete','n':n,'misleading':k,'rate':k/n if n else None,'ci95':wilson(k,n),'owner_n':len(owners),'owner_agree':sum(labs[i]['misleading']==own[i]['misleading'] for i in owners),'labels_sha256':sha(labels),'owner_labels_sha256':sha(owner_labels),'binding':rebuilt['binding']}

def reserve(files):
    """Reserve all outputs with exclusive creation; never overwrite a prior artifact."""
    fds=[]
    try:
        for path in files:
            fd=os.open(Path(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);fds.append(fd)
    except Exception:
        for fd in fds:os.close(fd)
        raise
    return fds


def bound_derive(trace_path,*,root,config_path,source_sha256,blind_key):
    """Validate the physical frozen 60 items before any audit packet is made."""
    import audit_a1 as v1
    raw,rows,errors=v1._read_trace(trace_path)
    if errors:raise AuditError('trace parse failed')
    config_raw=Path(config_path).read_bytes()
    config_hash=hashlib.sha256(config_raw).hexdigest()
    config=json.loads(config_raw)
    from eval_a1_v2 import verify_commits
    verify_commits(config,config_path,root)
    from a1_v2_frozen_loader import load_registered_a1_v2,ITEM_SHA
    manifest,manifest_sha,prepared=load_registered_a1_v2(config,root=root)
    if len(rows)!=60 or len(prepared)!=60:raise AuditError('complete frozen trace required')
    for i,(row,item) in enumerate(zip(rows,prepared)):
        binding=row.get('trace_binding');result=row.get('result')
        if not isinstance(binding,dict) or not isinstance(result,dict):raise AuditError('frozen trace result missing')
        rendered=result.get('rendered')
        if (result.get('final') not in ('glimmer_raw','glimmer_repaired','template') or row.get('final')!=result.get('final')
                or not isinstance(result.get('trace'),list) or not isinstance(rendered,dict) or not isinstance(rendered.get('sentences'),list)
                or any(not isinstance(sentence,dict) or not isinstance(sentence.get('text'),str) or not sentence['text'].strip()
                       or type(sentence.get('i')) is not int or sentence['i']<0 for sentence in rendered['sentences'])):
            raise AuditError('frozen trace result structure differs')
        expected={key:item.get(key) for key in ('row_index','pool','uid','side','rank_hash','q')}
        if (row.get('i')!=i or row.get('review')!=item.get('review') or row.get('q')!=item.get('q') or row.get('question')!=item.get('question')
                or binding.get('expected_items')!=60 or Path(binding.get('items_path','')).resolve()!=(Path(root)/'scratch_ds/evals/c9-freeze-fb10153-v1/a1_items.json').resolve()
                or binding.get('items_sha256')!=ITEM_SHA or binding.get('manifest_sha256')!=manifest_sha or binding.get('item')!=expected
                or not isinstance(result.get('trace'),list) or not isinstance(rendered.get('sentences'),list)):
            raise AuditError('frozen trace binding differs')
    integrity={'items_sha256':ITEM_SHA,'manifest_sha256':manifest_sha}
    for name,expected in config['source_sha256'].items():
        path=Path(root)/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected: raise AuditError('source bytes differ')
    if sha(config['source_sha256'])!=source_sha256: raise AuditError('source closure hash differs')
    frozen=derive(rows,item_hash=integrity['items_sha256'],manifest_hash=integrity['manifest_sha256'],config_hash=config_hash,source_hash=source_sha256,blind_key=blind_key)
    frozen['binding']['trace_bytes_sha256']=hashlib.sha256(raw).hexdigest()
    return frozen


def bound_score(trace_path,frozen,labels,owner_labels,*,root,config_path,source_sha256,blind_key):
    rebuilt=bound_derive(trace_path,root=root,config_path=config_path,source_sha256=source_sha256,blind_key=blind_key)
    if canonical(rebuilt)!=canonical(frozen):raise AuditError('physical trace or packet binding differs')
    plain=copy.deepcopy(rebuilt);plain['binding'].pop('trace_bytes_sha256')
    import audit_a1 as v1
    _,rows,errors=v1._read_trace(trace_path)
    if errors:raise AuditError('malformed trace')
    binding=plain['binding']
    result=_score_derived(rows,plain,labels,owner_labels,item_hash=binding['items_sha256'],manifest_hash=binding['manifest_sha256'],config_hash=binding['config_sha256'],source_hash=source_sha256,blind_key=blind_key)
    result['binding']=rebuilt['binding']
    return result

def _private_path(path,root):
    root=Path(root).resolve();base=root/'scratch_ds/evals';target=Path(path)
    if not target.is_absolute():target=root/target
    try:rel=target.relative_to(base)
    except ValueError as exc:raise AuditError('audit output outside private eval directory') from exc
    if not rel.parts or '..' in rel.parts or 'v2' not in target.name.lower():raise AuditError('private v2 output path required')
    if (root/'scratch_ds').is_symlink():raise AuditError('private directory ancestor is symlink')
    for parent in [base,*[base.joinpath(*rel.parts[:i]) for i in range(1,len(rel.parts))]]:
        if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid!=os.getuid() or stat.S_IMODE(parent.stat().st_mode)&0o077:raise AuditError('private non-symlink directory required')
    if target.exists() or target.is_symlink():raise AuditError('audit output already exists')
    return target

def write_packet(frozen,packet_path,key_path,meta_path,*,root=None):
    """Write only the blinded sheet to packet_path; source links stay private."""
    root=Path(root) if root is not None else Path(__file__).resolve().parents[2]
    packet_path,key_path,meta_path=[_private_path(p,root) for p in (packet_path,key_path,meta_path)]
    if len({packet_path,key_path,meta_path})!=3:raise AuditError('distinct audit output paths required')
    packets=frozen['packets'];key=frozen['key'];binding=frozen['binding']
    content=[canonical({'schema':'hardread.a1.audit-packet-v2','instructions':frozen['instructions'],'packets':packets})+'\n',canonical(key)+'\n',canonical({'schema':frozen['schema'],'population':frozen['population'],'binding':binding})+'\n']
    fds=reserve([packet_path,key_path,meta_path])
    for fd,data in zip(fds,content):
        with os.fdopen(fd,'w',encoding='utf8') as f:f.write(data);f.flush();os.fsync(f.fileno())
