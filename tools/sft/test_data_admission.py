import hashlib, json, math, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import data_admission as A
from roster_hash import roster_digest
import serialize as S
import build_sft as B

H=lambda x:hashlib.sha256(x.encode()).hexdigest()

def game(i,rosters=None):
    rosters=rosters or [[f'a{i}{x}' for x in range(6)],[f'b{i}{x}' for x in range(6)]]
    log=['|start|','|turn|1','|win|synthetic'];uid=f'run{i}/g0001'
    raw=json.dumps({'p1':'player-a','p2':'player-b','log':log,'p1team':[{'species':s} for s in rosters[0]],'p2team':[{'species':s} for s in rosters[1]]}).encode()
    return {'key':f'key{i}','uid':uid,'joined_uid':uid,'run_id':f'run{i}','game':1,'archive_sha256':H('archive'+str(i)),
            'producer':'job','chunk':i,'seed':100+i,'line_index':0,'line_sha256':H('line'+str(i)),
            'raw_replay_sha256':H('raw'+str(i)),'normalized_replay_sha256':H('normal'+str(i)),
            'log':log,'source_log_bytes':raw,'log_sha256':hashlib.sha256(raw).hexdigest(),'player_a':'p1','player_names':['player-a','player-b'],'source_player_a':'player-a',
            'team_names':[f'gen9/hr_train/team{i}a',f'gen9/hr_train/team{i}b'],
            'joined_team_names':[f'gen9/hr_train/team{i}a',f'gen9/hr_train/team{i}b'],
            'joined_rosters':rosters,'roster_digests':[roster_digest(r) for r in rosters], 'source_kind':'P5','rc_a':0,'rc_b':0,'source_status':'ok','decided':True,'p5_incomplete_game':False,'p5_nonzero_process_rc':False,'decision_count':1}

def make_index(gs):
    teams={name:{'split':'dev','roster_digest':digest} for g in gs for name,digest in zip(g['team_names'],g['roster_digests'])}
    sources={g['archive_sha256']:{k:g[k] for k in ('producer','chunk','seed')} for g in gs}
    return A.index_games(gs,teams,sources)

def row(g):
    return {'uid':g['uid']+'/A/000','run_id':g['run_id'],'game':g['game'],'side':'A','player':'p1','turn':1,'k':0,
            'cfg':'C1t1','legal':['a','b'],'choice':'a','policy':{'a':.2,'b':.8},'worlds':[{'chance':1,'side_one':[{'m':'a','v':2,'s':0},{'m':'b','v':8,'s':0}]}],'history_end':2,'view':{},'own_team':[{'species':s} for s in g['joined_rosters'][0]],'opp_team':[{'species':s} for s in g['joined_rosters'][1]]}

def projection(g):
    return {'example_id':g['uid']+'/A/000/action','game_key':g['key'],'archive_sha256':g['archive_sha256'],'roster_digests':g['roster_digests']}

class Tests(unittest.TestCase):
 def test_identity_alias_conflict_side_swap_and_no_roster_union(self):
    g1=game(1);g2=game(2,[g1['joined_rosters'][1],game(2)['joined_rosters'][1]])
    idx=make_index([g1,g2]);self.assertNotEqual(idx['components'][g1['key']],idx['components'][g2['key']])
    bad=game(3);bad['normalized_replay_sha256']=g1['normalized_replay_sha256']
    with self.assertRaisesRegex(A.AdmissionError,'identity'):make_index([g1,bad])
    bad=game(4);bad['joined_team_names'].reverse()
    with self.assertRaisesRegex(A.AdmissionError,'team join'):make_index([g1,bad])
    common=H('same');tokens=A.game_tokens({'uid':common,'raw_replay_sha256':common,'normalized_replay_sha256':common})
    self.assertEqual(len(tokens),3);self.assertTrue(all(t.split(':')[0] in ('uid','raw_replay','normalized_replay') for t in tokens))
 def test_projection_requires_actual_membership_and_one_roster_exclusion(self):
    g1=game(1);g2=game(2);g3=game(3,[g1['joined_rosters'][0],game(3)['joined_rosters'][1]])
    idx=make_index([g1,g2,g3]);ex={'v1':[projection(g1)],'fixed400':[projection(g2)]}
    refs={g1['uid']+'/A/000':g1['key'],g2['uid']+'/A/000':g2['key']}
    with self.assertRaisesRegex(A.AdmissionError,'link missing'):A.project_holdouts(idx,ex,{'v1':1,'fixed400':1},{})
    hold=A.project_holdouts(idx,ex,{'v1':1,'fixed400':1},refs)
    kept,counts=A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')},expected_heldout=1)
    self.assertNotIn(g3['key'],kept);self.assertEqual(counts['holdout_roster'],1)
    bad=dict(projection(g1),quality_score=1)
    with self.assertRaisesRegex(A.AdmissionError,'identity-only'):A.project_holdouts(idx,{'v1':[bad],'fixed400':ex['fixed400']},{'v1':1,'fixed400':1},refs)
    with self.assertRaisesRegex(A.AdmissionError,'manifest differs'):A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')})
 def test_new_validation_excludes_own_roster(self):
    chosen=next(game(i) for i in range(10000) if A.is_new_val(game(i)));g2=game(12000,[chosen['joined_rosters'][0],game(12000)['joined_rosters'][1]]);g3=game(13000)
    idx=make_index([chosen,g2,g3]);ex={'v1':[projection(g3)],'fixed400':[projection(g3)]};ref={g3['uid']+'/A/000':g3['key']}
    hold=A.project_holdouts(idx,ex,{'v1':1,'fixed400':1},ref)
    kept,_=A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')},expected_heldout=1)
    self.assertNotIn(chosen['key'],kept);self.assertNotIn(g2['key'],kept)
 def test_policy_empty_nonfinite_unsupported_and_duplicates_block(self):
    g=game(1);r=row(g)
    self.assertEqual(A.teacher_row(r,g,set(),set()),('b',None))
    for policy in ({},{'a':float('nan')},{'a':float('inf')},{'a':-1},{'missing':1},{'a':0}):
        with self.assertRaises(A.AdmissionError):A.teacher_row(dict(r,policy=policy),g,set(),set())
    seen=set();order=set();A.teacher_row(r,g,seen,order)
    with self.assertRaisesRegex(A.AdmissionError,'duplicate'):A.teacher_row(r,g,seen,order)
    for changed in (dict(r,history_end=True),dict(r,history_end=4),dict(r,player='p2'),dict(r,legal=['a','a'])):
        with self.assertRaises(A.AdmissionError):A.teacher_row(changed,g,set(),set())
 def test_token_alignment_and_duplicate_content(self):
    g=game(1);r=row(g);seen=set();contents=set()
    example={'messages':[],'tools':[],'id_map':{'act_01':'a','act_02':'b'},'species':[]}
    enc={'input_ids':[10,11],'labels':[-100,11],'n_prompt':1,'target_text':'synthetic'}
    info={'choice_pos':1,'hard_tok':11,'soft_tok':[11,12],'soft_p':[.8,.2],'multi_token_ids':False}
    class Fake:
        def encode(self,*args):return dict(enc)
    with patch.object(S,'build_example',return_value=example),patch.object(S,'target_message',return_value={}),patch.object(B,'h01',return_value=.5),patch.object(B,'choice_token_info',return_value=info):
        records,_=A.serialize_tasks(r,g,'b',Fake(),seen,contents);self.assertEqual(len(records),1)
        with self.assertRaisesRegex(A.AdmissionError,'duplicate example'):A.serialize_tasks(r,g,'b',Fake(),seen,contents)
        with patch.object(B,'choice_token_info',return_value=dict(info,hard_tok=12)):
            with self.assertRaisesRegex(A.AdmissionError,'misaligned'):A.serialize_tasks(r,g,'b',Fake(),set(),set())
        with patch.object(B,'choice_token_info',return_value=dict(info,soft_p=[.2,.2])):
            with self.assertRaisesRegex(A.AdmissionError,'soft target'):A.serialize_tasks(r,g,'b',Fake(),set(),set())
 def test_frozen_bytes_and_execution_switch(self):
    v=b'synthetic-val-bytes';e=b'synthetic-eval-bytes'
    actual=A.verify_frozen_bytes(v,e,hashlib.sha256(v).hexdigest(),hashlib.sha256(e).hexdigest());self.assertEqual(actual,(v,e))
    with tempfile.TemporaryDirectory() as d:
        A.copy_frozen_synthetic(d,v,e,hashlib.sha256(v).hexdigest(),hashlib.sha256(e).hexdigest())
        self.assertEqual((Path(d)/'val.jsonl.gz').read_bytes(),v);self.assertEqual((Path(d)/'eval.jsonl.gz').read_bytes(),e)
    with self.assertRaises(A.AdmissionError):A.verify_frozen_bytes(v+b'x',e,hashlib.sha256(v).hexdigest(),hashlib.sha256(e).hexdigest())
    self.assertFalse(A.DATA_EXECUTION_ENABLED)
    with self.assertRaisesRegex(A.AdmissionError,'disabled'):A.real_source_scan(".",{}, {}, {})
    with self.assertRaisesRegex(A.AdmissionError,'disabled'):A.real_dataset_build(".",{}, {}, {}, {}, {})

class IntegratedFixtureTests(unittest.TestCase):
 def test_synthetic_pipeline_excludes_holdouts_and_uses_pinned_builder_helpers(self):
    gs=[game(i) for i in (101,102,103)]
    teams={n:{'split':'dev','roster_digest':d} for g in gs for n,d in zip(g['team_names'],g['roster_digests'])}
    archives={g['archive_sha256']:{k:g[k] for k in ('producer','chunk','seed')} for g in gs}
    examples={'v1':[projection(gs[0])],'fixed400':[projection(gs[1])]}
    ex={'messages':[],'tools':[],'id_map':{'act_01':'a','act_02':'b'},'species':[]}
    enc={'input_ids':[10,11],'labels':[-100,11],'n_prompt':1,'target_text':'synthetic'}
    info={'choice_pos':1,'hard_tok':11,'soft_tok':[11,12],'soft_p':[.8,.2],'multi_token_ids':False}
    class Fake:
        def encode(self,*args):return dict(enc)
    with patch.object(S,'build_example',return_value=ex),patch.object(S,'target_message',return_value={}),patch.object(B,'h01',return_value=.5),patch.object(B,'choice_token_info',return_value=info):
        result=A.evaluate_synthetic_fixture(gs,teams,archives,[(g['key'],row(g)) for g in gs],examples,{'v1':1,'fixed400':1},{H('held')},{'reserved'},{H('reserved')},Fake(),expected_heldout=1)
    self.assertEqual(result['status'],'synthetic_only_not_admission');self.assertEqual(len(result['rows']),1)
    self.assertEqual(result['train_game_count'],1);self.assertFalse(any(result['overlap_counts'].values()))

class MoreSyntheticTests(unittest.TestCase):
 def test_valid_side_swap_preserves_roster_and_player_assignment(self):
    g=game(50);g['player_a']='p2';g['player_names']=['player-b','player-a']
    raw=json.dumps({'p1':'player-b','p2':'player-a','log':g['log'],'p1team':[{'species':x} for x in g['joined_rosters'][1]],'p2team':[{'species':x} for x in g['joined_rosters'][0]]}).encode()
    g['source_log_bytes']=raw;g['log_sha256']=hashlib.sha256(raw).hexdigest()
    make_index([g]);r=dict(row(g),player='p2')
    self.assertEqual(A.teacher_row(r,g,set(),set()),('b',None))
    with self.assertRaises(A.AdmissionError):A.teacher_row(dict(r,player='p1'),g,set(),set())
 def test_source_log_tamper_and_roster_side_tamper_block(self):
    g=game(55);bad=dict(g,source_log_bytes=g['source_log_bytes']+b'x')
    with self.assertRaisesRegex(A.AdmissionError,'log hash'):make_index([bad])
    r=row(g);r['own_team'],r['opp_team']=r['opp_team'],r['own_team']
    with self.assertRaisesRegex(A.AdmissionError,'roster side'):A.teacher_row(r,g,set(),set())
 def test_distinct_id_domains_and_zero_proof(self):
    g1=game(61);g2=game(62);g3=game(63);idx=make_index([g1,g2,g3])
    refs={g['uid']+'/A/000':g['key'] for g in (g1,g2)}
    hold=A.project_holdouts(idx,{'v1':[projection(g1)],'fixed400':[projection(g2)]},{'v1':1,'fixed400':1},refs)
    decision=g1['uid']+'/A/000';example=decision+'/action'
    with self.assertRaisesRegex(A.AdmissionError,'nonzero overlap'):A.verify_zero_overlap(idx,[g3['key']],hold,{H('held')},{'reserved'},{H('reserved')},{decision},set())
    with self.assertRaisesRegex(A.AdmissionError,'nonzero overlap'):A.verify_zero_overlap(idx,[g3['key']],hold,{H('held')},{'reserved'},{H('reserved')},set(),{example})

class InventoryTests(unittest.TestCase):
 def test_exact_metadata_and_quarantine_are_checked_without_opening_archives(self):
    old={'source_tarballs':[{'name':'old.tar.gz','sha256':H('old')}]};candidate={'archive_name':'new.tar.gz','archive_sha256':H('new'),'archive_size_bytes':12,'producer_job_name':'job','chunk_index':3,'seed_base':100,'seed':103}
    inv={'candidate_archives':[candidate],'quarantined_archive_metadata':[{'archive_name':'bad.tar.gz'}]}
    receipt={'archive_basename':'new.tar.gz','job_name':'job','local_closed_file_sha256':H('new'),'local_closed_file_bytes':12}
    p5=A.canonical(old);ib=A.canonical(inv);rb=A.canonical(receipt)
    pins={'p5_sha256':hashlib.sha256(p5).hexdigest(),'inventory_sha256':hashlib.sha256(ib).hexdigest(),'p5_count':1,'candidate_count':1,'quarantine_count':1,'receipt_sha256':{'new.tar.gz':hashlib.sha256(rb).hexdigest()}}
    self.assertEqual(A.validate_metadata_inventory(p5,ib,{'new.tar.gz':rb},pins)['status'],'metadata_only_not_admission')
    with self.assertRaisesRegex(A.AdmissionError,'receipt hash'):A.validate_metadata_inventory(p5,ib,{'new.tar.gz':rb+b' '},pins)
    with self.assertRaisesRegex(A.AdmissionError,'receipt names'):A.validate_metadata_inventory(p5,ib,{},pins)
    bad=dict(candidate,seed=104);ib2=A.canonical(dict(inv,candidate_archives=[bad]));pins2=dict(pins,inventory_sha256=hashlib.sha256(ib2).hexdigest())
    with self.assertRaisesRegex(A.AdmissionError,'candidate identity'):A.validate_metadata_inventory(p5,ib2,{'new.tar.gz':rb},pins2)

class ArchiveAdapterTests(unittest.TestCase):
 def test_synthetic_archive_join_and_unsafe_member_rejection(self):
    import io, tarfile
    import data_admission_runtime as R
    import extract
    with tempfile.TemporaryDirectory() as d:
        path=Path(d)/'sample.tar.gz';g=game(70);rosters=g['joined_rosters'];
        game_row={'game':1,'a':'player-a','b':'player-b','team_a':g['team_names'][0],'team_b':g['team_names'][1],'winner':'A','status':'ok','cfg_a':'C1t1','cfg_b':'C1t1','rc_a':0,'rc_b':0}
        replay=json.loads(g['source_log_bytes']);dec=[]
        for side,choice in (('a','a'),('b','b')):
            drow={'v':2,'turn':1,'choice':choice,'worlds':[{'chance':1,'side_one':[{'m':'a','v':4,'s':0},{'m':'b','v':6,'s':0}]}]}
            dec.append((f'g0001_{side}.decisions.jsonl',json.dumps(drow).encode()+b'\n'))
        def write(bad=False,malformed=False,rc_a=0,rc_b=0):
            game_row.update(rc_a=rc_a,rc_b=rc_b)
            with tarfile.open(path,'w:gz') as tar:
                contents=[('games.jsonl',json.dumps(game_row).encode()+b'\n'),('summary.json',json.dumps({'run_id':'sample','seed':100,'games':1,'completed':1,'team_pool':'gen9/hr_train','cfg_a':'C1t1','cfg_b':'C1t1'}).encode()),('battlelogs/g.log.json',json.dumps(replay).encode())]+dec
                if malformed:
                    contents=[(name,(json.dumps(dict(json.loads(body),worlds=[{'chance':1,'side_one':[{'m':'a'}]}])).encode()+b'\n') if name.endswith('.decisions.jsonl') else body) for name,body in contents]
                if bad:contents.append(('../escape',b'bad'))
                for name,body in contents:
                    info=tarfile.TarInfo('sample/'+name);info.size=len(body);tar.addfile(info,io.BytesIO(body))
        write();digest=hashlib.sha256(path.read_bytes()).hexdigest()
        source={'kind':'P5','run_id':'sample','sha256':digest,'producer':'job','chunk':0,'seed':100,'game_count':1,'decision_log_versions':[2]}
        teams={n:{'split':'dev','roster_digest':r} for n,r in zip(g['team_names'],g['roster_digests'])}
        identity=lambda uid,log,sanitizer:{'replay_sha256':H('raw'),'normalized_replay_sha256':H('normal')}
        games,rows,hashes,tombs=R.extract_joined_archive(path,digest,source,teams,None,extract,identity)
        self.assertEqual((len(games),len(rows),len(hashes)),(1,2,2));self.assertEqual(rows[0]['uid'],'sample/g0001/A/000')
        write(malformed=True);digest=hashlib.sha256(path.read_bytes()).hexdigest();source['sha256']=digest
        with patch.object(extract,'history_cuts',return_value=[None]):
            indexed,bad_rows,_,tombs=R.extract_joined_archive(path,digest,source,teams,None,extract,identity)
        self.assertIsNone(bad_rows[0]['history_end']);self.assertEqual(bad_rows[0]['worlds'][0]['side_one'],[{'m':'a'}])
        with self.assertRaisesRegex(A.AdmissionError,'history cut'):A.teacher_row(bad_rows[0],indexed[0],set(),set())
        for rc_a,rc_b in ((0,1),(3,2)):
            write(rc_a=rc_a,rc_b=rc_b);digest=hashlib.sha256(path.read_bytes()).hexdigest();source['sha256']=digest
            indexed,bad_rows,_,tombs=R.extract_joined_archive(path,digest,source,teams,None,extract,identity)
            self.assertTrue(indexed[0]['p5_nonzero_process_rc']);self.assertEqual(indexed[0]['decision_count'],2);self.assertEqual(len(bad_rows),2)
        for rc_a,rc_b in ((None,0),(True,0),('0',0)):
            write(rc_a=rc_a,rc_b=rc_b);digest=hashlib.sha256(path.read_bytes()).hexdigest();source['sha256']=digest
            joined,_,_,_=R.extract_joined_archive(path,digest,source,teams,None,extract,identity)
            self.assertTrue(joined[0]['p5_incomplete_game'])
        write(rc_a=0,rc_b=1);digest=hashlib.sha256(path.read_bytes()).hexdigest();source['sha256']=digest;source['kind']='P6'
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'P6 source health'):R.extract_joined_archive(path,digest,source,teams,None,extract,identity)
        source['kind']='P5'
        write(True);digest=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'unsafe'):R.read_archive(path,digest)

class FixedSelectionTests(unittest.TestCase):
 def test_fixed_selection_and_mutation(self):
    import data_admission_runtime as R
    ids=[f'run/g0001/A/{i:04d}/action' for i in range(1066)]
    selected=[ids[int(i*(len(ids)/400))] for i in range(400)]
    expected=hashlib.sha256('\n'.join(selected).encode()).hexdigest()
    self.assertEqual(R.select_fixed400_ids(list(reversed(ids)),expected),selected)
    ids[0]='different/action'
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'digest differs'):R.select_fixed400_ids(ids,expected)


class IdentityProjectionTests(unittest.TestCase):
 def test_identity_only_parser_ignores_nested_fields_and_rejects_duplicates(self):
    import data_admission_runtime as R
    raw='{"labels":[1,{"example_id":"fake","task":"reads"}],"example_id":"r/g0001/A/001/action","task":"action","score":{"x":1}}'
    self.assertEqual(R.identity_fields(raw),{'example_id':'r/g0001/A/001/action','task':'action'})
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'duplicate'):R.identity_fields(raw[:-1]+',"task":"action"}')
    with self.assertRaises(R.RuntimeErrorClosed):R.identity_fields('{"example_id":"x/action","task":"action","x":[1}')


class ExecutionGateTests(unittest.TestCase):
 def test_real_entrypoints_gate_before_any_source_read(self):
    from unittest.mock import patch
    import data_admission_runtime as R
    with patch.object(R,'_read_ref',side_effect=AssertionError('opened')), patch.object(R,'derive_index',side_effect=AssertionError('derived')):
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'disabled'):R.scan('.',{}, {}, {})
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'disabled'):R.build('.',{}, {}, {},{}, {})
 def test_index_review_is_required_before_rederivation(self):
    from unittest.mock import patch
    import data_admission_runtime as R
    with patch.object(R,'authorize',return_value={}), patch.object(R,'derive_index',side_effect=AssertionError('derived')):
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'review missing'):R.build('.',{}, {}, {},{}, {},execute=True)


class ProductionBuilderTests(unittest.TestCase):
 def test_reviewed_synthetic_build_and_tampered_index_block(self):
    import data_admission_runtime as R
    import glimmer_chat
    gs=[game(201),game(202),game(203)]
    for g in gs:g['key']=g['uid']
    gs[0].update(rc_b=1,p5_nonzero_process_rc=True)
    idx=make_index(gs);ids={gs[1]['uid']+'/A/000':gs[1]['uid']}
    hold=A.project_holdouts(idx,{'v1':[projection(gs[1])],'fixed400':[projection(gs[1])]},{'v1':1,'fixed400':1},ids)
    kept,reasons=A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')},expected_heldout=1)
    self.assertNotIn(gs[0]['uid'],kept);self.assertEqual(reasons['p5_nonzero_process_rc'],1);self.assertIn(gs[2]['uid'],kept)
    excluded_bad=dict(row(gs[0]),history_end=None,worlds=[{'chance':1,'side_one':[{'m':'a'}]}])
    state={'index':idx,'rows':[excluded_bad,row(gs[2])],'kept':[gs[2]['uid']],'hold':hold,'heldout':{H('held')},'heldout_names':set(),'reserved_names':{'reserved'},'reserved_rosters':{H('reserved')},'frozen':{}}
    receipt={'archive_records':[{'archive_sha256':gs[2]['archive_sha256'],'source':'P5','decision_count':2,'game_count':2,'reconciliation':{'admitted_raw_decisions':1}}],'excluded_game_counts':{},'exclusion_reconciliation':{'admitted_raw_decisions':1}}
    data=A.canonical(receipt)+b'\n';spec_sha=H('spec');index_sha=hashlib.sha256(data).hexdigest()
    review={'status':'accepted','index_sha256':index_sha,'spec_sha256':spec_sha,'card_sha256':H('card'),'reviewer':'independent'}
    example={'messages':[],'tools':[],'id_map':{'act_01':'a','act_02':'b'},'species':[]}
    enc={'input_ids':[10,11],'labels':[-100,11],'n_prompt':1,'target_text':'synthetic'}
    info={'choice_pos':1,'hard_tok':11,'soft_tok':[11,12],'soft_p':[.8,.2],'multi_token_ids':False}
    class Fake:
        tokenizer_sha256=H('tok');template_sha256=H('tpl')
        def __init__(self,*a,**k):pass
        def encode(self,*a):return dict(enc)
    with tempfile.TemporaryDirectory() as d:
        d=Path(d);v=d/'val';e=d/'eval';v.write_bytes(b'val');e.write_bytes(b'eval')
        state['frozen']={'v1_val':{'path':str(v),'sha256':hashlib.sha256(b'val').hexdigest()},'v1_eval':{'path':str(e),'sha256':hashlib.sha256(b'eval').hexdigest()}}
        root=str(Path(__file__).resolve().parents[2]);spec={'output_dir':'unused','tokenizer_dir':'unused'}
        args=(root,{'sha256':spec_sha},{'sha256':H('card')},{},{'sha256':index_sha},{'commit':'x','path':'x','sha256':'x'})
        with patch.object(R,'authorize',return_value=spec),patch.object(R,'_committed',return_value=json.dumps(review).encode()),patch.object(R,'_read_ref',return_value=data),patch.object(R,'derive_index',return_value=(receipt,state)),patch.object(R,'iter_verified_batches',side_effect=lambda root,state,receipt:[(gs,state['rows'])]),patch.object(R,'_private_output',return_value=d/'out'),patch.object(glimmer_chat,'GlimmerChat',Fake),patch.object(S,'build_example',return_value=example),patch.object(S,'target_message',return_value={}),patch.object(B,'h01',return_value=.5),patch.object(B,'choice_token_info',return_value=info):
            result=R.build(*args,execute=True)
            self.assertEqual(result['train_rows'],1);self.assertEqual((d/'out/val.jsonl.gz').read_bytes(),b'val');self.assertEqual((d/'out/eval.jsonl.gz').read_bytes(),b'eval')
            manifest=json.loads((d/'out/manifest.json').read_text());self.assertEqual(manifest['counts']['source_raw_decisions'],2);self.assertEqual(manifest['counts']['admitted_raw_decisions'],1)
            per=manifest['per_source'][gs[2]['archive_sha256']];self.assertEqual(per['source_raw_decisions'],2);self.assertEqual(per['admitted_raw_decisions'],1)
        bad_state=dict(state,rows=[dict(row(gs[2]),worlds=[{'chance':1,'side_one':[{'m':'a'}]}])])
        with patch.object(R,'authorize',return_value=spec),patch.object(R,'_committed',return_value=json.dumps(review).encode()),patch.object(R,'_read_ref',return_value=data),patch.object(R,'derive_index',return_value=(receipt,bad_state)),patch.object(R,'iter_verified_batches',side_effect=lambda root,state,receipt:[(gs,state['rows'])]),patch.object(R,'_private_output',return_value=d/'other'),patch.object(glimmer_chat,'GlimmerChat',Fake):
            with self.assertRaisesRegex(R.RuntimeErrorClosed,'malformed admitted teacher'):R.build(*args,execute=True)
        bad_state=dict(state,rows=[dict(row(gs[2]),worlds=[])])
        with patch.object(R,'authorize',return_value=spec),patch.object(R,'_committed',return_value=json.dumps(review).encode()),patch.object(R,'_read_ref',return_value=data),patch.object(R,'derive_index',return_value=(receipt,bad_state)),patch.object(R,'iter_verified_batches',side_effect=lambda root,state,receipt:[(gs,state['rows'])]),patch.object(R,'_private_output',return_value=d/'other'),patch.object(glimmer_chat,'GlimmerChat',Fake):
            with self.assertRaisesRegex(R.RuntimeErrorClosed,'empty admitted teacher policy'):R.build(*args,execute=True)
        bad_state=dict(state,rows=[dict(row(gs[2]),history_end=None)])
        with patch.object(R,'authorize',return_value=spec),patch.object(R,'_committed',return_value=json.dumps(review).encode()),patch.object(R,'_read_ref',return_value=data),patch.object(R,'derive_index',return_value=(receipt,bad_state)),patch.object(R,'iter_verified_batches',side_effect=lambda root,state,receipt:[(gs,state['rows'])]),patch.object(R,'_private_output',return_value=d/'other'),patch.object(glimmer_chat,'GlimmerChat',Fake):
            with self.assertRaisesRegex(A.AdmissionError,'history cut'):R.build(*args,execute=True)
        with patch.object(R,'authorize',return_value=spec),patch.object(R,'_committed',return_value=json.dumps(review).encode()),patch.object(R,'_read_ref',return_value=data+b'tampered'),patch.object(R,'derive_index',return_value=(receipt,state)):
            with self.assertRaisesRegex(R.RuntimeErrorClosed,'recomputation differs'):R.build(*args,execute=True)


class SourceProofTests(unittest.TestCase):
 def test_independent_p6_inspector_must_match_full_join(self):
    import data_admission_runtime as R
    g=game(400);r=row(g);h=H('decision')
    independent={'uid':g['uid'],'game_line_index':g['line_index'],'game_line_sha256':g['line_sha256'],'log_sha256':g['log_sha256'],'teams':tuple(g['team_names']),'log':json.loads(g['source_log_bytes']),'decisions':[(r['uid'],h)]}
    R.compare_p6_inspection([independent],[g],[r],[h])
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'join differs'):R.compare_p6_inspection([dict(independent,teams=tuple(reversed(g['team_names'])))],[g],[r],[h])
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'decision inventory'):R.compare_p6_inspection([independent],[g],[r],[H('other')])
 def test_frozen_gzip_ids_reject_file_and_identity_mutation(self):
    import gzip
    import data_admission_runtime as R
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/'ids.gz'
        with gzip.open(p,'wt') as stream:stream.write('{"example_id":"r/g0001/A/000/action","task":"action","score":123}\n')
        expected=hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertEqual(len(R.read_identity_file(p,expected)),1)
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'differs'):R.read_identity_file(p,H('wrong'))


class HeldoutNameTests(unittest.TestCase):
 def test_public_heldout_name_is_an_independent_domain(self):
    gs=[game(501),game(502),game(503)];idx=make_index(gs)
    refs={g['uid']+'/A/000':g['key'] for g in gs[:2]}
    hold=A.project_holdouts(idx,{'v1':[projection(gs[0])],'fixed400':[projection(gs[1])]},{'v1':1,'fixed400':1},refs)
    name=gs[2]['team_names'][0]
    kept,counts=A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')},heldout_names={name},expected_heldout=1)
    self.assertNotIn(gs[2]['key'],kept);self.assertEqual(counts['holdout_team_name'],1)
    with self.assertRaisesRegex(A.AdmissionError,'nonzero overlap'):A.verify_zero_overlap(idx,[gs[2]['key']],hold,{H('held')},{'reserved'},{H('reserved')},set(),set(),heldout_names={name})


class ReviewedAuthorizationTests(unittest.TestCase):
 def test_full_code_binding_and_reviewed_commit_are_required(self):
    import data_admission_runtime as R
    root=Path(__file__).resolve().parents[2]
    required=['tools/sft/data_admission.py','tools/sft/data_admission_runtime.py','tools/sft/build_sft.py','tools/sft/serialize.py','tools/sft/dex.json','tools/sft/glimmer_chat.py','tools/dataset/extract.py','tools/belief/roster_hash.py','tools/human/parse_replays.py','azure/student/p6_trainonly_inspect.py']
    code={f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in required}
    spec={'status':'FROZEN_FOR_REVIEW','contract_sha256':'8547147630b68a0dd78689b3222515bdf48b5469f13d8f49af0eaeec8e1a2b33','amendment_sha256':'77b82491c07e5d212bfa81403b88a73c8f3f84783f6ca5befec1fdbfe1ebd2e7','source_health_amendment_sha256':'4a06e7f14df2dad26d8f1472691a3cc9af23e049f8497d685a29ec56a4d02326','code_sha256':code}
    raw=json.dumps(spec).encode();sref={'path':'spec','sha256':hashlib.sha256(raw).hexdigest()}
    card={'commit':'a'*40,'path':'card','sha256':H('card')};approval={'commit':'b'*40,'path':'approval','sha256':H('approval')}
    approved={'status':'approved','purpose':'index','card_commit':card['commit'],'card_sha256':card['sha256'],'spec_sha256':sref['sha256'],'owner':'owner'}
    def committed(root,commit,path,expected):return sref['sha256'].encode() if path=='card' else json.dumps(approved).encode()
    with patch.object(R,'_read_ref',return_value=raw),patch.object(R,'_committed',side_effect=committed):
        self.assertEqual(R.authorize(root,sref,card,approval,'index',execute=True),spec)
        approved['purpose']='build'
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'authorization differs'):R.authorize(root,sref,card,approval,'index',execute=True)
        approved['purpose']='index'
        with patch.object(R,'file_sha',return_value=H('tampered')):
            with self.assertRaisesRegex(R.RuntimeErrorClosed,'source bytes differ'):R.authorize(root,sref,card,approval,'index',execute=True)


class BoundedMemoryTests(unittest.TestCase):
 def test_compact_index_never_retains_teacher_payloads(self):
    import data_admission_runtime as R
    g=game(601);g['key']=g['uid'];r=row(g);payload='synthetic-heavy-'+('x'*2000000);r['worlds']=[{'payload':payload}];r['view']={'payload':payload}
    teams={name:{'split':'dev','roster_digest':digest} for name,digest in zip(g['team_names'],g['roster_digests'])}
    source={'sha256':g['archive_sha256'],**{k:g[k] for k in ('producer','chunk','seed')}}
    index={'games':{},'components':{},'token_owner':{}};decisions={};content=set()
    R.append_compact_batch(index,decisions,content,[g],[r],[H('content')],teams,source)
    self.assertEqual(decisions,{r['uid']:g['uid']});self.assertNotIn('worlds',repr(index));self.assertNotIn('synthetic-heavy',repr(index));self.assertNotIn('source_log_bytes',index['games'][g['uid']]);self.assertNotIn('log',index['games'][g['uid']])
 def test_derive_index_state_has_no_teacher_rows(self):
    import data_admission_runtime as R
    g=game(605);g['key']=g['uid'];r=row(g);r['worlds']=[{'large':'x'*2000000}];r['view']={'large':'x'*2000000}
    source={'path':'synthetic','run_id':g['run_id'],'kind':'P5','sha256':g['archive_sha256'],'producer':g['producer'],'chunk':g['chunk'],'seed':g['seed']}
    teams={n:{'split':'dev','roster_digest':d} for n,d in zip(g['team_names'],g['roster_digests'])}
    frozen={'v1_val':{'sha256':'aed05c91f3140fbffb4b37b429baa4d456a8ba75e0da85888c4c0c3081cb384e'},'v1_eval':{'sha256':'7ea29968fe36c61bd7283f8f72b770a5eeb0074d4829f04cd2fa2550160abb10'},'fixed_v0':{'sha256':'3b0ba8fa2e2db60b9149d4ac03f1ae5bcbcb152745c4fa8fe1bf54fcc5e06276'}}
    spec={'source_refs':{k:{} for k in ('public_pool','sealed_metadata','team_split','validated_teams')},'frozen_refs':frozen}
    hold={k:set() for k in ('components','tokens','rosters','team_names','example_ids','decision_ids')}
    with patch.object(R,'_load_runtime_modules',return_value=(None,None,None)),patch.object(R,'authenticated_catalog',return_value=[source]),patch.object(R,'_read_ref',return_value=b'{}'),patch.object(R,'roster_metadata',return_value=(teams,set(),set(),set(),set())),patch.object(R,'verified_archive_batch',return_value=([g],[r],[H('content')],[])),patch.object(R,'verify_source_census'),patch.object(R,'_path',return_value='unused'),patch.object(R,'read_identity_file',return_value=[]),patch.object(R,'holdout_projection',return_value=hold),patch.object(R,'select_fixed400_ids',return_value=[]),patch.object(A,'partition_games',return_value=([g['uid']],{})):
        receipt,state=R.derive_index('.',spec,H('spec'))
    self.assertEqual(receipt['decision_count'],1);self.assertNotIn('rows',state);self.assertNotIn('worlds',repr(state));self.assertNotIn('x'*1000,repr(state));self.assertNotIn('log',state['index']['games'][g['uid']])
 def test_spool_exactly_matches_legacy_shuffle(self):
    import io, random
    import data_admission_runtime as R
    items=[{'example_id':str(i),'x':i*i} for i in range(257)];legacy=items[:];random.Random(0).shuffle(legacy)
    with tempfile.TemporaryDirectory() as d:
        spool=R.RecordSpool(Path(d)/'spool');[spool.append(x) for x in items];actual=io.BytesIO();spool.replay(actual);spool.close()
        expected=b''.join(A.canonical(x)+b'\n' for x in legacy)
        self.assertEqual(actual.getvalue(),expected);self.assertEqual(hashlib.sha256(actual.getvalue()).hexdigest(),hashlib.sha256(expected).hexdigest())
 def test_second_pass_tamper_blocks_before_yield(self):
    import data_admission_runtime as R
    g=game(602);g['key']=g['uid'];r=row(g);source={'sha256':g['archive_sha256']};record={'archive_sha256':source['sha256'],'game_count':1,'decision_count':1,'decision_ids_sha256':A.digest([r['uid']]),'decision_content_sha256':A.digest([H('expected')]),'tombstone_sha256':A.digest([])}
    state={'catalog':[source],'teams':{},'index':{'games':{g['uid']:R.compact_game(g)}}};receipt={'archive_records':[record]}
    with patch.object(R,'_load_runtime_modules',return_value=(None,None,None)),patch.object(R,'verified_archive_batch',return_value=([g],[r],[H('tampered')],[])):
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'second-pass archive decisions differ'):list(R.iter_verified_batches('.',state,receipt))


class P5ReturnCodeAmendmentTests(unittest.TestCase):
 def test_bad_games_remain_in_identity_holdouts_and_reconcile(self):
    import data_admission_runtime as R
    gs=[game(i) for i in (701,702,703,704)]
    for g in gs:g['key']=g['uid']
    gs[0].update(rc_a=1,p5_nonzero_process_rc=True,decision_count=3)
    gs[1].update(rc_a=1,rc_b=2,p5_nonzero_process_rc=True,decision_count=4)
    gs[2]['decision_count']=5;gs[3]['decision_count']=6
    idx=make_index(gs);refs={g['uid']+'/A/000':g['uid'] for g in (gs[0],gs[3])}
    hold=A.project_holdouts(idx,{'v1':[projection(gs[0])],'fixed400':[projection(gs[3])]},{'v1':1,'fixed400':1},refs)
    kept,counts=A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')},expected_heldout=1)
    self.assertIn(gs[0]['uid'],idx['games']);self.assertIn(idx['components'][gs[0]['uid']],hold['components'])
    self.assertNotIn(gs[0]['uid'],kept);self.assertNotIn(gs[1]['uid'],kept);self.assertEqual(counts['p5_nonzero_process_rc'],2)
    records=[{'archive_sha256':g['archive_sha256'],'game_count':1,'decision_count':g['decision_count'],'raw_game_lines':1,'missing_log_tombstones':0,'tombstone_side_lines':0} for g in gs]
    total,flags=R.reconcile_exclusions(idx,kept,hold,{H('held')},{'reserved'},{H('reserved')},set(),records)
    self.assertEqual(total['source_games'],4);self.assertEqual(total['source_raw_decisions'],18);self.assertEqual(total['primary_decision_counts']['p5_nonzero_process_rc'],7)
    self.assertTrue(flags[gs[0]['uid']]['flags']['holdout_game']);self.assertEqual(flags[gs[0]['uid']]['primary'],'p5_nonzero_process_rc')
    self.assertEqual(total['source_raw_decisions'],total['admitted_raw_decisions']+total['excluded_raw_decisions'])
    broken=[dict(x) for x in records];broken[0]['decision_count']+=1
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'per-archive exclusion arithmetic'):R.reconcile_exclusions(idx,kept,hold,{H('held')},{'reserved'},{H('reserved')},set(),broken)
    with self.assertRaisesRegex(A.AdmissionError,'nonzero overlap'):A.verify_zero_overlap(idx,[gs[0]['uid']],hold,{H('held')},{'reserved'},{H('reserved')},set(),set())
 def test_healthy_p5_p6_and_strict_p6_rejection(self):
    gs=[game(i) for i in (711,712,713)]
    gs[1]['source_kind']='P6';idx=make_index(gs)
    refs={gs[2]['uid']+'/A/000':gs[2]['key']};hold=A.project_holdouts(idx,{'v1':[projection(gs[2])],'fixed400':[projection(gs[2])]},{'v1':1,'fixed400':1},refs)
    kept,_=A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')},expected_heldout=1)
    self.assertIn(gs[0]['key'],kept);self.assertIn(gs[1]['key'],kept)
    idx['games'][gs[1]['key']]['rc_a']=1
    with self.assertRaisesRegex(A.AdmissionError,'health identity'):A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')},expected_heldout=1)
 def test_second_pass_rc_tamper_blocks_before_yield(self):
    import data_admission_runtime as R
    g=game(705);g['key']=g['uid'];r=row(g);source={'sha256':g['archive_sha256']};record={'archive_sha256':source['sha256'],'game_count':1,'decision_count':1,'decision_ids_sha256':A.digest([r['uid']]),'decision_content_sha256':A.digest([H('content')]),'tombstone_sha256':A.digest([])}
    state={'catalog':[source],'teams':{},'index':{'games':{g['uid']:R.compact_game(g)}}};tampered=dict(g,rc_b=1,p5_nonzero_process_rc=True)
    with patch.object(R,'_load_runtime_modules',return_value=(None,None,None)),patch.object(R,'verified_archive_batch',return_value=([tampered],[r],[H('content')],[])):
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'game identity differs'):list(R.iter_verified_batches('.',state,{'archive_records':[record]}))


class P5IncompleteTombstoneTests(unittest.TestCase):
 def test_joined_timeout_and_missing_log_tombstone(self):
    import io, tarfile
    import data_admission_runtime as R
    import extract
    g=game(801);replay=json.loads(g['source_log_bytes']);names=g['team_names']
    good={'game':1,'a':'player-a','b':'player-b','team_a':names[0],'team_b':names[1],'winner':'A','status':'ok','cfg_a':'C1t1','cfg_b':'C1t1','rc_a':0,'rc_b':0}
    missing={'game':2,'a':'missing-a','b':'missing-b','team_a':names[0],'team_b':names[1],'winner':None,'status':'timeout','cfg_a':'C1t1','cfg_b':'C1t1','rc_a':None,'rc_b':None}
    dec={'v':2,'turn':1,'choice':'a','worlds':[{'chance':1,'side_one':[{'m':'a','v':4,'s':0},{'m':'b','v':6,'s':0}]}]}
    with tempfile.TemporaryDirectory() as d:
        path=Path(d)/'sample.tar.gz'
        def write(missing_record,include_log=False):
            with tarfile.open(path,'w:gz') as tar:
                summary={'run_id':'sample','seed':100,'games':2,'completed':1,'team_pool':'gen9/hr_train','cfg_a':'C1t1','cfg_b':'C1t1'}
                members={'games.jsonl':(json.dumps(good)+'\n'+json.dumps(missing_record)+'\n').encode(),'summary.json':json.dumps(summary).encode(),'battlelogs/g1.log.json':json.dumps(replay).encode(),'g0001_a.decisions.jsonl':json.dumps(dec).encode()+b'\n','g0001_b.decisions.jsonl':json.dumps(dict(dec,choice='b')).encode()+b'\n','g0002_a.decisions.jsonl':b'partial-quarantined-side-line\n'}
                if include_log:
                    missing_log=dict(replay,p1='missing-a',p2='missing-b',log=['|different|','|turn|1'])
                    members['battlelogs/g2.log.json']=json.dumps(missing_log).encode()
                    members['g0002_a.decisions.jsonl']=json.dumps(dict(dec,turn=2)).encode()+b'\n'
                for name,body in members.items():
                    info=tarfile.TarInfo('sample/'+name);info.size=len(body);tar.addfile(info,io.BytesIO(body))
        teams={n:{'split':'dev','roster_digest':v} for n,v in zip(names,g['roster_digests'])}
        source={'kind':'P5','run_id':'sample','producer':'job','chunk':0,'seed':100,'decision_log_versions':[2]}
        identity=lambda uid,log,sanitizer:{'replay_sha256':H('raw'+uid),'normalized_replay_sha256':H('norm'+uid)}
        write(missing);digest=hashlib.sha256(path.read_bytes()).hexdigest();source['sha256']=digest
        joined,rows,hashes,tombs=R.extract_joined_archive(path,digest,source,teams,None,extract,identity)
        self.assertEqual((len(joined),len(rows),len(tombs)),(1,2,1));self.assertEqual(tombs[0]['uid'],'sample/g0002');self.assertEqual(len(tombs[0]['side_ids']),1);self.assertNotIn('raw_replay_sha256',tombs[0]);self.assertEqual(tombs[0]['roster_digests'],g['roster_digests'])
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'holdout references'):R.verify_tombstone_holdout({tombs[0]['uid']},['sample/g0002/A/000/action'])
        bad_teams=dict(teams);del bad_teams[names[0]]
        with self.assertRaisesRegex(R.RuntimeErrorClosed,'roster missing'):R.extract_joined_archive(path,digest,source,bad_teams,None,extract,identity)
        write(missing,include_log=True);digest=hashlib.sha256(path.read_bytes()).hexdigest();source['sha256']=digest
        joined,rows,hashes,tombs=R.extract_joined_archive(path,digest,source,teams,None,extract,identity)
        self.assertEqual((len(joined),len(tombs)),(2,0));self.assertTrue(joined[1]['p5_incomplete_game']);self.assertEqual(joined[1]['decision_count'],1)
 def test_tombstone_uid_and_content_aliases_block(self):
    import data_admission_runtime as R
    tomb={'uid':'run/g0002','side_ids':['run/g0002/A/000'],'side_content_hashes':[H('side')]}
    ids=set();index={'games':{}};decisions={};content=set()
    R.add_tombstone(tomb,ids,index,decisions,content)
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'identity conflicts'):R.add_tombstone(tomb,ids,index,decisions,content)
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'identity conflicts'):R.add_tombstone(dict(tomb,uid='new/g0001'),set(),index,decisions,content)
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'identity conflicts'):R.add_tombstone(tomb,set(),{'games':{tomb['uid']:{}}},decisions,set())
 def test_tombstone_name_roster_barrier_and_25_24_census(self):
    import data_admission_runtime as R
    gs=[game(i) for i in range(900,924)];idx=make_index(gs)
    refs={gs[0]['uid']+'/A/000':gs[0]['key']};hold=A.project_holdouts(idx,{'v1':[projection(gs[0])],'fixed400':[projection(gs[0])]},{'v1':1,'fixed400':1},refs)
    hold['tombstone_names']={gs[1]['team_names'][0]};hold['tombstone_rosters']={gs[2]['roster_digests'][0]}
    kept,counts=A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')},expected_heldout=1)
    self.assertNotIn(gs[1]['key'],kept);self.assertNotIn(gs[2]['key'],kept);self.assertEqual(counts['tombstone_team_name'],1);self.assertEqual(counts['tombstone_roster'],1)
    for g in gs:g['archive_sha256']=H('one-archive')
    records=[{'archive_sha256':H('one-archive'),'game_count':24,'decision_count':24,'raw_game_lines':25,'missing_log_tombstones':1,'tombstone_side_lines':2}]
    total,_=R.reconcile_exclusions(idx,kept,hold,{H('held')},{'reserved'},{H('reserved')},set(),records)
    self.assertEqual((total['raw_archive_game_lines'],total['source_games'],total['missing_log_tombstones'],total['tombstone_side_lines']),(25,24,1,2))
    bad=[dict(records[0],raw_game_lines=26)]
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'raw versus joined'):R.reconcile_exclusions(idx,kept,hold,{H('held')},{'reserved'},{H('reserved')},set(),bad)


class HistoricalCensusTests(unittest.TestCase):
 def test_reconciled_raw_joined_decisions_and_health_counts(self):
    import data_admission_runtime as R
    names=['lab-n3-c015','lab-n3-c037','lab-n3-c043']+[f'synthetic-{i}' for i in range(261)]
    records=[]
    for i,name in enumerate(names):
        tomb=int(i<3);records.append({'source':'P5','run_id':name,'raw_game_lines':25,'game_count':25-tomb,'missing_log_tombstones':tomb,'decision_count':551493 if i==0 else 0,'p5_health_counts':{'not_decided':13,'malformed_rc':9,'nonzero_rc':12,'status_not_ok':9,'status_ok_nonzero_undecided':4} if i==0 else {}})
    records.extend({'source':'P6','raw_game_lines':20,'game_count':20,'missing_log_tombstones':0,'decision_count':172815 if i==0 else 0} for i in range(100))
    R.verify_source_census(records)
    records[1]['game_count']+=1
    with self.assertRaisesRegex(R.RuntimeErrorClosed,'per-archive raw'):R.verify_source_census(records)


class HealthPrecedenceTests(unittest.TestCase):
 def test_nine_incomplete_and_four_status_ok_nonzero_reconcile(self):
    import data_admission_runtime as R
    gs=[game(i) for i in range(1100,1114)]
    for g in gs:g['key']=g['uid']
    for g in gs[:9]:g.update(source_status='timeout',decided=False,rc_a=None,rc_b=None,p5_incomplete_game=True,decision_count=2)
    for g in gs[9:13]:g.update(source_status='ok',decided=False,rc_a=0,rc_b=1,p5_incomplete_game=True,p5_nonzero_process_rc=True,decision_count=3)
    g=gs[13];idx=make_index(gs);ref={g['uid']+'/A/000':g['uid']}
    hold=A.project_holdouts(idx,{'v1':[projection(g)],'fixed400':[projection(g)]},{'v1':1,'fixed400':1},ref)
    kept,counts=A.partition_games(idx,hold,{H('held')},{'reserved'},{H('reserved')},expected_heldout=1)
    self.assertEqual(counts['p5_incomplete_game'],9);self.assertEqual(counts['p5_nonzero_process_rc'],4)
    records=[{'archive_sha256':x['archive_sha256'],'game_count':1,'decision_count':x['decision_count'],'raw_game_lines':1,'missing_log_tombstones':0,'tombstone_side_lines':0} for x in gs]
    total,flags=R.reconcile_exclusions(idx,kept,hold,{H('held')},{'reserved'},{H('reserved')},set(),records)
    self.assertEqual(total['primary_game_counts']['p5_incomplete_game'],9);self.assertEqual(total['primary_game_counts']['p5_nonzero_process_rc'],4)
    self.assertEqual(total['primary_decision_counts']['p5_incomplete_game'],18);self.assertEqual(total['primary_decision_counts']['p5_nonzero_process_rc'],12)
    self.assertTrue(flags[gs[9]['uid']]['flags']['p5_incomplete_game']);self.assertEqual(flags[gs[9]['uid']]['primary'],'p5_nonzero_process_rc')
    self.assertEqual(total['source_games'],total['admitted_games']+total['excluded_games']);self.assertEqual(total['source_raw_decisions'],total['admitted_raw_decisions']+total['excluded_raw_decisions'])
    timeout=dict(gs[9],source_status='timeout');f=A.game_exclusion_flags({'games':{timeout['uid']:timeout},'components':{timeout['uid']:H('component')}},timeout['uid'],hold,{H('held')},{'reserved'},{H('reserved')})
    self.assertEqual(A.primary_exclusion(timeout,f),'p5_incomplete_game')

if __name__=='__main__':unittest.main()
