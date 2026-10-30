"""Project and attach root-admitted native views; never import a pipeline.

Builder inputs: four exact byte streams per successful alias (review, view,
offered, run receipt), plus a root-approved binding object. Binding keys:
projection_sha256, mapping_sha256, batch_receipt_sha256, builder_sha256,
games. Each ordered game binding has alias, status ('ok' or 'unavailable'),
reason (null or safe enum), review_sha256, view_sha256, offered_sha256,
run_receipt_sha256, human_fit, percent_hp_approx. For unavailable entries all
four output digests are null. The root-approved binding itself is the separate
lineage/coverage attestation: native run receipts do not contain output hashes.
The caller supplies bytes directly; no path in the packet is ever followed.
"""
import copy
import hashlib
import json
import math
import re
from pathlib import Path

SCHEMA='own-ladder-postgame-v1'
HASH=re.compile(r'[a-f0-9]{64}\Z')
ACTION=re.compile(r"[A-Za-z][A-Za-z0-9 .'-]{0,63}\Z")
REASONS={'cache_missing','native_runtime','native_failure','review_unavailable','malformed'}
BRIDGE=('available','tie','S','L','C','R','s','l','c','r','result','swingy')
CARD=('turn','k','kind','chosen','best','gap','gap_pts','wp_before','wp_before_pct','chosen_wp','best_wp')


def digest(raw): return hashlib.sha256(raw).hexdigest()
def own_hash(): return digest(Path(__file__).read_bytes())
def _keys(obj, keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys): raise ValueError('invalid packet structure')
def _hash(v):
    if not isinstance(v,str) or not HASH.fullmatch(v): raise ValueError('invalid digest')
    return v

def read_json(raw):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d: raise ValueError('duplicate field')
            d[k]=v
        return d
    if not isinstance(raw,bytes) or len(raw)>12_000_000: raise ValueError('invalid source bytes')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))

def _number(v, *, integer=False, probability=False, optional=False):
    if v is None and optional: return None
    if type(v) not in ((int,) if integer else (int,float)): raise ValueError('invalid number')
    try:
        if not math.isfinite(v): raise ValueError('invalid number')
    except OverflowError as exc:
        raise ValueError('invalid number') from exc
    if probability and not 0<=v<=1: raise ValueError('invalid probability')
    return v

def _label(v):
    if not isinstance(v,str) or not ACTION.fullmatch(v): raise ValueError('invalid action')
    return v

def _measure(cites, path, value, view_digest):
    if value is not None: cites.append({'pointer':path,'value':value,'view_sha256':view_digest})
    return value

def project_view(alias,view,offered,view_digest,receipt_digest,human_fit,percent_hp_approx):
    _hash(view_digest); _hash(receipt_digest)
    if alias not in ('G1','G2','G3') or type(human_fit) is not bool or type(percent_hp_approx) is not bool or not isinstance(view,dict): raise ValueError('invalid source')
    if view.get('mode')!='export' or view.get('result') not in ('win','loss','tie',None) or view.get('end_reason') not in ('ko','forfeit','timeout','disconnect','tie',None): raise ValueError('invalid metadata')
    if not isinstance(offered,list) or len(offered)>3 or not isinstance(view.get('disagreements'),list) or len(view['disagreements'])!=len(offered): raise ValueError('offered unavailable')
    cites=[]; out={'status':'ok','reason':None,'result':view['result'],'end_reason':view.get('end_reason'),
                   'turns':_measure(cites,'turns',_number(view.get('turns'),integer=True,optional=True),view_digest),
                   'bridge':None,'wp':[],'disagreements':[], 'citations':cites,'view_sha256':view_digest,'run_receipt_sha256':receipt_digest,
                   'human_fit':human_fit,'percent_hp_approx':percent_hp_approx}
    b=view.get('bridge')
    if not isinstance(b,dict) or b.get('source')!='review.py (tools/review/bridge.py)' or type(b.get('available')) is not bool or type(b.get('tie')) is not bool: raise ValueError('native bridge missing')
    if b['available']:
        clean={'available':True,'tie':False}
        for key in ('S','L','C','R'):
            clean[key]=_measure(cites,'bridge.'+key,_number(b.get(key),probability=key in ('S','R')),view_digest)
        for key in ('s','l','c','r'):
            clean[key]=_measure(cites,'bridge.'+key,_number(b.get(key),integer=True),view_digest)
        if clean['R'] not in (0,1) or clean['r']!=100*clean['R'] or clean['s']+clean['l']+clean['c']!=clean['r'] or b.get('result') not in ('win','loss') or type(b.get('swingy')) is not bool: raise ValueError('invalid bridge identity')
        clean.update(result=b['result'],swingy=b['swingy'])
    else:
        clean={'available':False,'tie':b['tie'],'S':None,'L':None,'C':None,'R':None,'s':None,'l':None,'c':None,'r':None,'result':None,'swingy':False}
    out['bridge']=clean
    points=view.get('wp')
    if not isinstance(points,list) or len(points)>10000: raise ValueError('curve unavailable')
    for i,p in enumerate(points):
        if not isinstance(p,dict): raise ValueError('invalid point')
        q={'turn':_measure(cites,f'wp[{i}].turn',_number(p.get('turn'),integer=True),view_digest),
           'wp':_measure(cites,f'wp[{i}].wp',_number(p.get('wp'),probability=True,optional=True),view_digest), 'luck':None,'luck_pts':None}
        if (p.get('luck') is None)!=(p.get('luck_pts') is None): raise ValueError('incomplete luck')
        if p.get('luck') is not None:
            q['luck']=_measure(cites,f'wp[{i}].luck',_number(p['luck']),view_digest)
            q['luck_pts']=_measure(cites,f'wp[{i}].luck_pts',_number(p['luck_pts'],integer=True),view_digest)
        out['wp'].append(q)
    for i,(card,source) in enumerate(zip(view['disagreements'],offered)):
        if not isinstance(card,dict) or not isinstance(source,dict) or (card.get('turn'),card.get('k'))!=(source.get('turn'),source.get('k')): raise ValueError('offered membership differs')
        if card.get('kind') not in ('teampreview','move','switch'): raise ValueError('invalid card kind')
        for visible, original in (('gap','decision_cost'),('wp_before','win_prob'),('chosen_wp','chosen_win_prob'),('best_wp','engine_best_win_prob')):
            if original in source and card.get(visible)!=source[original]: raise ValueError('offered measurement differs')
        c={'kind':card['kind'],'chosen':_measure(cites,f'disagreements[{i}].chosen',_label(card.get('chosen')),view_digest),
           'best':_measure(cites,f'disagreements[{i}].best',_label(card.get('best')),view_digest)}
        for key in ('turn','k','gap_pts','wp_before_pct'):
            c[key]=_measure(cites,f'disagreements[{i}].{key}',_number(card.get(key),integer=True),view_digest)
        for key in ('gap','wp_before','chosen_wp','best_wp'):
            c[key]=_measure(cites,f'disagreements[{i}].{key}',_number(card.get(key),probability=key!='gap'),view_digest)
        out['disagreements'].append(c)
    return out


def build_sidecar(binding, sources):
    _keys(binding,('projection_sha256','mapping_sha256','batch_receipt_sha256','builder_sha256','games'))
    for key in ('projection_sha256','mapping_sha256','batch_receipt_sha256','builder_sha256'): _hash(binding[key])
    if binding['builder_sha256']!=own_hash() or not isinstance(binding['games'],list) or len(binding['games'])!=3: raise ValueError('builder binding differs')
    packet={'schema':SCHEMA,**{k:binding[k] for k in ('projection_sha256','mapping_sha256','batch_receipt_sha256','builder_sha256')},'games':[]}
    for alias,b in zip(('G1','G2','G3'),binding['games']):
        _keys(b,('alias','status','reason','review_sha256','view_sha256','offered_sha256','run_receipt_sha256','human_fit','percent_hp_approx'))
        if b['alias']!=alias or type(b['human_fit']) is not bool or type(b['percent_hp_approx']) is not bool: raise ValueError('alias or flags differ')
        if b['status']=='unavailable':
            if b['reason'] not in REASONS or any(b[k] is not None for k in ('review_sha256','view_sha256','offered_sha256','run_receipt_sha256')): raise ValueError('invalid unavailable slot')
            packet['games'].append({'alias':alias,'status':'unavailable','reason':b['reason']}); continue
        if b['status']!='ok' or b['reason'] is not None: raise ValueError('invalid status')
        data=sources.get(alias); _keys(data,('review','view','offered','run_receipt'))
        for name in data:
            if digest(data[name])!=_hash(b[name+'_sha256']): raise ValueError('source binding differs')
        receipt=read_json(data['run_receipt'])
        if not isinstance(receipt,dict) or receipt.get('alias')!=alias or receipt.get('native_status')!='ok' or receipt.get('extraction_status')!='ok': raise ValueError('run not terminal')
        review=read_json(data['review'])
        try:
            native_view=read_json(data['view']); native_offered=read_json(data['offered'])
            raw_bridge=review.get('bridge') if isinstance(review,dict) else None
            if not isinstance(raw_bridge,dict): raise ValueError('native bridge missing')
            displayed=native_view.get('bridge',{})
            if displayed.get('available') and any(raw_bridge.get(k)!=displayed.get(k) for k in ('S','L','C','R','s','l','c','result')):
                raise ValueError('bridge source mismatch')
            pane=project_view(alias,native_view,native_offered,b['view_sha256'],b['run_receipt_sha256'],b['human_fit'],b['percent_hp_approx'])
        except (ValueError, TypeError, KeyError):
            packet['games'].append({'alias':alias,'status':'unavailable','reason':'malformed'})
            continue
        packet['games'].append({'alias':alias,'status':'ok','pane':pane,'review_sha256':b['review_sha256'],'offered_sha256':b['offered_sha256']})
    return packet


def apply_sidecar(view,packet,projection_sha256,mapping_sha256,batch_receipt_sha256,builder_sha256):
    _keys(packet,('schema','projection_sha256','mapping_sha256','batch_receipt_sha256','builder_sha256','games'))
    for name,expected in (('projection_sha256',projection_sha256),('mapping_sha256',mapping_sha256),('batch_receipt_sha256',batch_receipt_sha256),('builder_sha256',builder_sha256)):
        _hash(expected)
        if packet[name]!=expected: raise ValueError('stale postgame binding')
    if packet['schema']!=SCHEMA or builder_sha256!=own_hash() or not isinstance(packet['games'],list) or len(packet['games'])!=3 or not isinstance(view,dict) or view.get('presentation')!='recorded-facts' or not isinstance(view.get('games'),list) or len(view['games'])!=3: raise ValueError('invalid packet')
    result=copy.deepcopy(view)
    for alias,game,entry in zip(('G1','G2','G3'),result['games'],packet['games']):
        if not isinstance(game,dict) or not isinstance(entry,dict) or game.get('alias')!=alias or entry.get('alias')!=alias: raise ValueError('alias mismatch')
        if entry.get('status')=='unavailable':
            _keys(entry,('alias','status','reason'))
            if entry['reason'] not in REASONS: raise ValueError('invalid reason')
            game['postgame']={'status':'unavailable','reason':entry['reason']}; continue
        _keys(entry,('alias','status','pane','review_sha256','offered_sha256'))
        if entry['status']!='ok': raise ValueError('invalid status')
        _hash(entry['review_sha256']); _hash(entry['offered_sha256'])
        pane=entry['pane']; _keys(pane,('status','reason','result','end_reason','turns','bridge','wp','disagreements','citations','view_sha256','run_receipt_sha256','human_fit','percent_hp_approx'))
        _hash(pane['view_sha256']); _hash(pane['run_receipt_sha256'])
        # Rebuild from the published whitelist and reject any noncanonical field,
        # numeric value, citation or order; never accept supplied free text.
        _keys(pane['bridge'],BRIDGE)
        synthetic={'mode':'export','result':pane['result'],'end_reason':pane['end_reason'],'turns':pane['turns'],
                   'bridge':dict(pane['bridge'],source='review.py (tools/review/bridge.py)'), 'wp':pane['wp'], 'disagreements':pane['disagreements']}
        offered=[{'turn':x.get('turn'),'k':x.get('k')} for x in pane['disagreements']]
        for p in pane['wp']: _keys(p,('turn','wp','luck','luck_pts'))
        for c in pane['disagreements']: _keys(c,CARD)
        rebuilt=project_view(alias,synthetic,offered,pane['view_sha256'],pane['run_receipt_sha256'],pane['human_fit'],pane['percent_hp_approx'])
        if rebuilt!=pane: raise ValueError('noncanonical pane')
        game['postgame']=pane
    return result
