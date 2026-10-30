"""Synthetic-only native pane projection tests."""
import copy
import json
import subprocess
from pathlib import Path
import unittest
import own_ladder_postgame as p
import own_ladder_selection as selection


def raw(x): return json.dumps(x,separators=(',',':')).encode()
def view():
    return {'mode':'export','result':'win','end_reason':'ko','turns':32,
            'bridge':{'source':'review.py (tools/review/bridge.py)','available':True,'tie':False,
                      'S':0.5,'L':0.1,'C':0.4,'R':1,'s':50,'l':10,'c':40,'r':100,'result':'win','swingy':False,
                      'tag':'private text'},
            'wp':[{'turn':1,'wp':0.5,'luck':None,'luck_pts':None,'action':'private'}, {'turn':2,'wp':0.6,'luck':0.1,'luck_pts':10}],
            'disagreements':[], 'summary_text':'private', 'opponent_name':'private'}

def fixture():
    data={}; bindings=[]
    for alias in ('G1','G2','G3'):
        vals={'review':raw({'bridge':{'S':0.5,'L':0.1,'C':0.4,'R':1,'s':50,'l':10,'c':40,'result':'win'}}),'view':raw(view()),'offered':raw([]),
              'run_receipt':raw({'alias':alias,'native_status':'ok','extraction_status':'ok'})}
        data[alias]=vals
        bindings.append({'alias':alias,'status':'ok','reason':None,**{k+'_sha256':p.digest(v) for k,v in vals.items()},
                         'human_fit':True,'percent_hp_approx':False})
    binding={'projection_sha256':'1'*64,'mapping_sha256':'2'*64,'batch_receipt_sha256':'3'*64,'builder_sha256':p.own_hash(),'games':bindings}
    return binding,data

def recorded(): return {'presentation':'recorded-facts','games':[{'alias':a,'decisions':[]} for a in ('G1','G2','G3')]}

class PostgameTests(unittest.TestCase):
    def test_projection_whitelist_and_exact_citations_empty_offered(self):
        binding,data=fixture(); packet=p.build_sidecar(binding,data); pane=packet['games'][0]['pane']
        self.assertEqual(pane['disagreements'],[])
        self.assertNotIn('summary_text',str(pane)); self.assertNotIn('private',str(pane))
        self.assertIn({'pointer':'bridge.s','value':50,'view_sha256':binding['games'][0]['view_sha256']},pane['citations'])
        self.assertIn({'pointer':'wp[1].wp','value':0.6,'view_sha256':binding['games'][0]['view_sha256']},pane['citations'])
        out=p.apply_sidecar(recorded(),packet,'1'*64,'2'*64,'3'*64,p.own_hash())
        self.assertEqual(out['games'][0]['postgame']['bridge']['s'],50)
        self.assertNotIn('postgame',recorded()['games'][0])
        with self.assertRaises(ValueError): selection.Registry(out['games'][0])

    def test_offered_order_and_missing_option_are_not_invented(self):
        v=view(); card={'turn':2,'k':0,'kind':'move','chosen':'Move A','best':'Move B', 'gap':0.1,'gap_pts':10,
                        'wp_before':0.5,'wp_before_pct':50,'chosen_wp':None,'best_wp':0.6}
        v['disagreements']=[card]
        with self.assertRaises(ValueError): p.project_view('G1',v,[{'turn':2,'k':0}],'a'*64,'b'*64,False,False)
        card['chosen_wp']=0.5
        with self.assertRaises(ValueError): p.project_view('G1',v,[{'turn':2,'k':1}],'a'*64,'b'*64,False,False)
        out=p.project_view('G1',v,[{'turn':2,'k':0}],'a'*64,'b'*64,False,False)
        self.assertEqual(out['disagreements'][0]['chosen_wp'],0.5)

    def test_bad_bridge_becomes_unavailable_and_null_curve_preserved(self):
        binding,data=fixture(); v=view(); v['bridge']['c']=39; data['G1']['view']=raw(v)
        binding['games'][0]['view_sha256']=p.digest(data['G1']['view'])
        packet=p.build_sidecar(binding,data)
        self.assertEqual(packet['games'][0],{'alias':'G1','status':'unavailable','reason':'malformed'})
        v=view(); v['wp'][0]['wp']=None
        self.assertIsNone(p.project_view('G1',v,[],'a'*64,'b'*64,False,False)['wp'][0]['wp'])

    def test_stale_alias_swaps_and_untrusted_fields_rejected(self):
        binding,data=fixture(); packet=p.build_sidecar(binding,data)
        for proj,mapping,batch in [('4'*64,'2'*64,'3'*64),('1'*64,'4'*64,'3'*64),('1'*64,'2'*64,'4'*64)]:
            with self.assertRaises(ValueError): p.apply_sidecar(recorded(),packet,proj,mapping,batch,p.own_hash())
        bad=copy.deepcopy(packet); bad['games'][0],bad['games'][1]=bad['games'][1],bad['games'][0]
        with self.assertRaises(ValueError): p.apply_sidecar(recorded(),bad,'1'*64,'2'*64,'3'*64,p.own_hash())
        bad=copy.deepcopy(packet); bad['games'][0]['pane']['opponent_name']='private'
        with self.assertRaises(ValueError): p.apply_sidecar(recorded(),bad,'1'*64,'2'*64,'3'*64,p.own_hash())
        bad=copy.deepcopy(packet); bad['games'][0]['pane']['citations'][0]['value']=999
        with self.assertRaises(ValueError): p.apply_sidecar(recorded(),bad,'1'*64,'2'*64,'3'*64,p.own_hash())
        with self.assertRaises(ValueError): p.read_json(b'{"a":1,"a":2}')
        data['G1']['view']=b'{}'
        with self.assertRaises(ValueError): p.build_sidecar(binding,data)

class BridgeSubjectTests(unittest.TestCase):
    def test_bot_and_ordinary_review_render_distinct_subjects(self):
        script = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  function bridgeWidget(v, options) {');
const end = source.indexOf('  function legendKey(', start);
if (start < 0 || end < 0) throw Error('bridge function missing');
function h(tag, attrs, ...children) { return {children, appendChild(x){this.children.push(x);}}; }
function s(tag, attrs) { return h(tag, attrs, attrs && attrs.text || ''); }
function signedBar() { return h('svg', null); }
function legendKey(kind, text) { return h('span', null, text); }
function glyph(n) { return n >= 0 ? '+' : '-'; }
function favour(n) { return n > 0 ? 'in your favour' : n < 0 ? 'against you' : 'neutral'; }
function signed(n) { return String(n); }
function pct(n) { return Math.round(100*n) + '%'; }
const MINUS='-';
const bridgeWidget = eval('(' + source.slice(start,end).trim() + ')');
function text(x) { if (x == null) return ''; if (Array.isArray(x)) return x.map(text).join(' '); if (typeof x === 'object') return text(x.children); return String(x); }
const v={bridge:{available:true,tie:false,s:50,l:60,c:-10,r:100,L:.6,result:'win',swingy:true},summary_text:''};
const bot=text(bridgeWidget(v,{subject:'HardReadBot'}));
const human=text(bridgeWidget(v));
for (const phrase of ['HardReadBot and its opponent','in HardReadBot’s favour','against HardReadBot','HardReadBot’s play alone','HardReadBot’s win chance']) if (!bot.includes(phrase)) throw Error('missing bot text: '+phrase);
for (const phrase of ['your opponent', 'your favour', 'against you', 'your play alone', 'your win chance']) if (bot.includes(phrase)) throw Error('human copy leaked: '+phrase);
for (const phrase of ["yours and your opponent's",'in your favour','against you','your play alone','your win chance']) if (!human.includes(phrase)) throw Error('ordinary copy changed: '+phrase);
"""
        path=Path(__file__).resolve().parent/'static/app.js'
        result=subprocess.run(['node','-e',script,str(path)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__': unittest.main()
