"""Source-bound, pre-action fact selection; pure transformations, no inference.

The caller supplies only the validated recorded-facts view. Registry references
are adapter roles, with original field/digest and companion citations retained
privately. Only the filtered prompt leaves this module's boundary.
"""
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / 'tools' / 'agent'
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))
from evidence_select import validate_selection

SHA = re.compile(r'[a-f0-9]{64}\Z')


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _keys(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ValueError('invalid recorded fact structure')


def _cite(obj, kind):
    _keys(obj, ('value', 'ref', 'field'))
    if not isinstance(obj['ref'], str) or not SHA.fullmatch(obj['ref']):
        raise ValueError('invalid citation')
    if not isinstance(obj['field'], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]+\]|\.[A-Za-z_][A-Za-z0-9_]*)*', obj['field']) or re.search(r'replay|opponent|player|url|log|future|reveal|outcome|result|winner|chat|identity', obj['field'], re.I):
        raise ValueError('invalid citation field')
    value = obj['value']
    if kind == 'turn' and (type(value) is not int or not 1 <= value <= 10000):
        raise ValueError('invalid turn')
    if kind == 'name' and (not isinstance(value,str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .'-]{0,47}",value)):
        raise ValueError('invalid friendly value')
    if kind == 'number' and (not isinstance(value,str) or len(value)>40 or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',value)):
        raise ValueError('invalid number')
    return obj


class Registry:
    def __init__(self, game):
        _keys(game, ('alias', 'decisions', 'conflicts_unavailable'))
        if game['alias'] not in ('G1','G2','G3') or type(game['conflicts_unavailable']) is not bool or not isinstance(game['decisions'],list) or len(game['decisions']) != 3:
            raise ValueError('three fixed decisions required')
        self.alias = game['alias']; self.facts = {}; self.turns = []
        def add(i, kind, value, companion=None, index=None):
            ref = '#%d.%s' % (len(self.facts)+1, kind)
            self.facts[ref] = {'turn_index':i,'kind':kind,'value':copy.deepcopy(value),'companion':copy.deepcopy(companion),'index':index}
        for i, d in enumerate(game['decisions']):
            _keys(d, ('turn','chosen','duration','visits'))
            turn, choice = _cite(d['turn'], 'turn'), _cite(d['chosen'], 'name')
            if self.turns and turn['value'] <= self.turns[-1]['turn']['value']:
                raise ValueError('turns must be distinct and chronological')
            self.turns.append({'turn':copy.deepcopy(turn), 'chosen':copy.deepcopy(choice)})
            add(i, 'choice', choice)
            duration = d['duration']
            if duration is not None:
                _keys(duration, ('value','unit'))
                _cite(duration['value'],'number'); _cite(duration['unit'],'name')
                if duration['unit']['value'] not in ('seconds','milliseconds','microseconds'):
                    raise ValueError('invalid duration unit')
                add(i, 'search_duration', duration['value'], duration['unit'])
            if not isinstance(d['visits'],list) or len(d['visits'])>100:
                raise ValueError('invalid visits')
            for j,v in enumerate(d['visits']):
                _keys(v, ('value','scope')); _cite(v['value'],'number'); _cite(v['scope'],'name')
                if not re.fullmatch(r'(?:0|[1-9][0-9]*)',v['value']['value']) or not re.fullmatch(r'world(?: [1-9][0-9]*)?',v['scope']['value']):
                    raise ValueError('invalid visit scope')
                if j < 2: add(i, 'search_visits', v['value'], v['scope'], j)
        if len(self.facts)>12:
            raise ValueError('too many facts')

    def resolve(self, ref):
        fact = self.facts[ref]
        return fact, fact['kind'], fact['value']['value']

    def prompt(self):
        slots = [{'turn':t['turn']['value'],'choice':t['chosen']['value'],'facts':[]} for t in self.turns]
        for ref, f in self.facts.items():
            fact = {'ref':ref,'kind':f['kind'],'exact_value':f['value']['value']}
            if f['companion'] is not None:
                fact['unit' if f['kind']=='search_duration' else 'scope'] = f['companion']['value']
            slots[f['turn_index']]['facts'].append(fact)
        return {'alias':self.alias,'turns':slots}

    def schema(self):
        return {'type':'function','function':{'name':'select_evidence','description':'Select exactly one recorded fact per turn.',
                'parameters':{'type':'object','properties':{'refs':{'type':'array','items':{'type':'string','enum':list(self.facts)},'minItems':3,'maxItems':3,'uniqueItems':True}},'required':['refs'],'additionalProperties':False}}}

    def validate(self, response):
        refs = validate_selection(self, list(self.facts), response)
        if len(refs)!=3 or {self.facts[r]['turn_index'] for r in refs} != {0,1,2}:
            raise ValueError('one reference per fixed turn required')
        return refs


def validate_completion(registry, response):
    if not isinstance(response,dict) or response.get('finish_reason') not in ('stop','tool_calls'):
        raise ValueError('completion incomplete')
    calls = response.get('tool_calls')
    if not isinstance(calls,list) or len(calls)!=1 or not isinstance(calls[0],dict) or calls[0].get('name')!='select_evidence' or calls[0].get('parse_error'):
        raise ValueError('invalid tool call')
    return registry.validate(calls[0].get('arguments'))


def apply_sidecar(view, projection_sha256, selector_sha256, receipt_sha256, sidecar):
    """Return a copy; any invalid/stale evidence raises for template fallback.

    Approval is external: caller must hash-bind the sidecar and supply the root
    approved receipt digest and this selector's source digest independently.
    """
    _keys(sidecar, ('schema','projection_sha256','selector_sha256','receipt_sha256','games'))
    if sidecar['schema']!='own-ladder-selection-v1' or any(not isinstance(v,str) or not SHA.fullmatch(v) for v in (projection_sha256,selector_sha256,receipt_sha256)):
        raise ValueError('invalid sidecar binding')
    if (sidecar['projection_sha256'],sidecar['selector_sha256'],sidecar['receipt_sha256']) != (projection_sha256,selector_sha256,receipt_sha256):
        raise ValueError('stale sidecar')
    if not isinstance(view,dict) or view.get('presentation')!='recorded-facts' or not isinstance(view.get('games'),list) or len(view['games'])!=3:
        raise ValueError('invalid view')
    games = sidecar['games']
    if not isinstance(games,list) or len(games)!=3:
        raise ValueError('invalid game receipts')
    out = copy.deepcopy(view)
    for alias, game, entry in zip(('G1','G2','G3'),out['games'],games):
        _keys(entry,('alias','status','refs'))
        if entry['alias']!=alias or game.get('alias')!=alias or entry['status'] not in ('success','fallback'):
            raise ValueError('invalid game receipt')
        if entry['status']=='fallback':
            if entry['refs'] != []: raise ValueError('fallback cannot select facts')
            game['selection_mode']='template-only'
            continue
        registry = Registry(game); refs = registry.validate({'refs':entry['refs']})
        for d in game['decisions']:
            d['duration']=None; d['visits']=[]
        for ref in refs:
            f = registry.facts[ref]; decision = game['decisions'][f['turn_index']]
            if f['kind']=='search_duration': decision['duration']={'value':f['value'],'unit':f['companion']}
            if f['kind']=='search_visits': decision['visits']=[{'value':f['value'],'scope':f['companion']}]
        game['selection_mode']='glimmer-selected'
    return out
