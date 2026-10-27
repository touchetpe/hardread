"""Synthetic C8 source and metric regression tests; no E9 results are read."""
import copy
import contextlib
import io
import json
import tempfile
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ROOT=next(p for p in HERE.parents if (p/'tools/review/bridge.py').is_file())
sys.path[:0]=[str(HERE),str(ROOT/'tools/review'),str(ROOT/'tools/belief')]

def module(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    obj=importlib.util.module_from_spec(spec);sys.modules[name]=obj;spec.loader.exec_module(obj);return obj
P=module('c8_prepare');C=module('calib_b')


def records_and_memberships():
    rows=[]; memberships=[]; grouped={x:[] for x in P.COHORTS}; sources=[]
    for i,cohort in enumerate(P.COHORTS):
        uid='synthetic-'+str(i); labels=[cohort]+(['top50'] if i==0 else [])
        for side in ('p1','p2'):
            row={'uid':uid,'side':side,'outcome':0 if side=='p1' else 1,
                 'luck':[{'k':0,'turn':1,'K':1,'worlds':[{'draw':0,'A_dec':-2 if side=='p1' else 2}]}],
                 'decisions':[],'meta':{'rating_band':cohort}}
            rows.append(row); memberships.append({'uid':uid,'side':side,'cohorts':labels})
            for label in labels: grouped[label].append(copy.deepcopy(row))
            sources.extend({'uid':uid,'player':side,'species_preview':f'species{i}{side}{n}'} for n in range(6))
    counts={x:1 for x in P.COHORTS};counts['top50']=2
    rows.sort(key=lambda r:(P.SOURCE_FILES[r['meta']['rating_band']],r['side']))
    return rows,memberships,grouped,sources,counts


class A2PreparationTests(unittest.TestCase):
    def setUp(self): self.rows,self.members,self.cohorts,self.species,self.counts=records_and_memberships()
    def prepare(self,**kw):
        return P.prepare(self.rows,self.members,[self.species],kw.get('val',set()),kw.get('reserved',set()),
                         cohort_rows=self.cohorts,expected_counts=self.counts)

    def test_predeclared_selectors_bind_input_triples_and_expected_population(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);spec={'sources':{},'selector_code':{}}
            def create(path):
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic');return {'path':str(path),'sha256':P.sha_file(path)}
            for source in ('top','broad'):
                directory=root/source
                spec['sources'][source]={'games':create(directory/'games.jsonl.gz'),
                    'sets':create(directory/'sets.jsonl.gz'),'profiles':create(directory/'species_profiles.json')}
            spec['full_sets']=create(root/'foul-play/fp/data/pkmn_sets_cache/gen9ou/pokemon_full_sets.json')
            for path in ('tools/review/human_eval.py','tools/human/reconstruct_check.py'):
                spec['selector_code'][path]=create(root/path)['sha256']
            calls=[]
            def selector(**kwargs):
                calls.append(kwargs);cohort=kwargs['band'] or 'top50';n=P.EXPECTED[cohort]
                return [{'uid':cohort+'-'+str(i),'side':side,'log':['synthetic'], 'team':'synthetic',
                         'outcome':0 if side=='p1' else 1, 'worlds':4,'iterations':20000,
                         'luck_worlds':4,'luck_iterations':10000} for i in range(n) for side in ('p1','p2')]
            selected,bindings,outcomes=P.reconstruct_membership(spec,root=root,jobs_fn=selector)
            self.assertEqual(len(selected),2990);self.assertEqual(len(calls),6)
            self.assertEqual(calls[4]['n'],200);self.assertEqual(calls[4]['seed'],11)
            self.assertEqual(calls[5]['n'],513);self.assertEqual(calls[5]['seed'],1)
            def inconsistent(**kwargs):
                rows=selector(**kwargs)
                if kwargs['band'] in (None,'lt1300'):
                    for r in rows[:2]:
                        r['uid']='shared-game'; r['team']='top-team' if kwargs['band'] is None else 'broad-team'
                return rows
            with self.assertRaisesRegex(P.PrepareError,'different source inputs'):
                P.reconstruct_membership(spec,root=root,jobs_fn=inconsistent)
            Path(spec['sources']['top']['sets']['path']).write_text('changed')
            with self.assertRaises(P.PrepareError):P.reconstruct_membership(spec,root=root,jobs_fn=selector)

    def test_second_side_roster_excludes_entire_overlapping_game(self):
        team=[r['species_preview'] for r in self.species if r['uid']=='synthetic-0' and r['player']=='p2']
        clean,labels,_uid_audit,_duplicate_audit,report=self.prepare(val={P.roster_digest(team)})
        self.assertFalse(any(r['uid']=='synthetic-0' for r in clean));self.assertFalse(any(r['uid']=='synthetic-0' for r in labels))
        self.assertEqual(report['excluded_games'],1);self.assertEqual(report['cohorts']['lt1300']['excluded_games'],1)
        self.assertEqual(report['cohorts']['top50']['excluded_games'],1)
        self.assertEqual(report['cohorts']['unique_pooled']['attempted_games'],6)
        self.assertEqual(report['cohorts']['cohort_incidence']['attempted_games'],7)
        self.assertTrue(report['strict_190_high_band'].startswith('unmet'))

    def test_missing_error_outcome_and_extra_merge_file_fail(self):
        with self.assertRaisesRegex(P.PrepareError,'outcome'):
            P.verify_selector_outcomes([{'uid':'u','side':'p1','error':'synthetic'}],{('u','p1'):0})
        bad={'uid':'synthetic-0','side':'p1','error':'synthetic'}
        self.cohorts['lt1300'].append(bad)
        with self.assertRaisesRegex(P.PrepareError,'outcomes'): self.prepare()
        with self.assertRaises(P.PrepareError): P.jsonl(b'{}\n\n{}\n')
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);files=[]
            for cohort in P.COHORTS:
                path=folder/P.SOURCE_FILES[cohort];path.write_text('')
                files.append((cohort,str(path),P.sha_file(path)))
            P.verify_cohort_files(files)
            (folder/'run_b_extra.jsonl').write_text('')
            with self.assertRaisesRegex(P.PrepareError,'extra'):P.verify_cohort_files(files)

    def test_a2_excludes_either_side_and_records_both_exclusion_sources(self):
        for side in ('p1','p2'):
            species=[r['species_preview'] for r in self.species if r['uid']=='synthetic-1' and r['player']==side]
            h=P.roster_digest(species)
            clean,_,audit,_duplicate_audit,report=self.prepare(val={h},reserved={h})
            self.assertFalse(any(r['uid']=='synthetic-1' for r in clean))
            uid=next(r for r in audit if r['uid']=='synthetic-1')
            self.assertTrue(uid['hr_val_match']);self.assertTrue(uid['reserved_match'])
            self.assertEqual(report['matches']['both'],1)

    def test_input_hash_and_conflicting_error_outcomes_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'input';path.write_bytes(b'synthetic')
            self.assertEqual(P.read_checked(path,P.sha_file(path)),b'synthetic')
            with self.assertRaises(P.PrepareError):P.read_checked(path,'0'*64)
        duplicate={'uid':'synthetic-0','side':'p1','outcome':1,'error':'synthetic'}
        self.cohorts['lt1300'].append(duplicate)
        with self.assertRaisesRegex(P.PrepareError,'conflicting outcomes'):self.prepare()

    def test_cohort_success_uses_its_own_attempts_even_when_overlap_merged_success(self):
        original=self.cohorts['top50'][0]
        self.cohorts['top50'][0]={'uid':original['uid'],'side':original['side'],'outcome':original['outcome'],
                                  'error':'synthetic'}
        _,_,_,_,report=self.prepare()
        self.assertEqual(report['cohorts']['lt1300']['both_side_success'],1)
        self.assertEqual(report['cohorts']['top50']['both_side_success'],1)
        self.assertEqual(report['cohorts']['unique_pooled']['both_side_success'],6)

    def test_invalid_missing_duplicate_or_conflicting_rosters_fail(self):
        self.species.pop()
        with self.assertRaises(P.PrepareError): self.prepare()
        self.species.append(copy.deepcopy(self.species[-1]))
        with self.assertRaises(P.PrepareError): self.prepare()
        self.rows,self.members,self.cohorts,self.species,self.counts=records_and_memberships()
        conflict=copy.deepcopy(self.species[:12]);conflict[0]['species_preview']='different'
        with self.assertRaises(P.PrepareError):
            P.prepare(self.rows,self.members,[self.species,conflict],set(),set(),cohort_rows=self.cohorts,expected_counts=self.counts)

    def test_noisy_duplicate_is_audited_and_exact_first_success_is_required(self):
        conflict=copy.deepcopy(self.cohorts['top50'][0]);conflict['luck'][0]['worlds'][0]['A_dec']=77
        idx=next(i for i,r in enumerate(self.rows) if (r['uid'],r['side'])==(conflict['uid'],conflict['side']))
        self.cohorts['top50'].append(conflict)
        _,_,_,duplicates,_=self.prepare()
        self.assertEqual(len(duplicates),1)
        self.assertNotEqual(duplicates[0]['chosen_payload_sha256'],duplicates[0]['other_payload_sha256'])
        self.rows[idx]=copy.deepcopy(conflict)
        with self.assertRaisesRegex(P.PrepareError,'first-success precedence'): self.prepare()
        self.rows,self.members,self.cohorts,self.species,self.counts=records_and_memberships()
        self.cohorts['top50'].pop()
        with self.assertRaisesRegex(P.PrepareError,'missing selected'): self.prepare()

    def test_wrong_merged_row_order_fails_exact_merge(self):
        self.rows[0],self.rows[1]=self.rows[1],self.rows[0]
        with self.assertRaisesRegex(P.PrepareError,'merged row order'):self.prepare()

    def test_first_error_then_success_precedence_across_sorted_files(self):
        idx=next(i for i,r in enumerate(self.rows) if r['uid']=='synthetic-0' and r['side']=='p1')
        key=self.rows[idx]; noisy=copy.deepcopy(key);noisy['luck'][0]['worlds'][0]['A_dec']=11
        self.cohorts['lt1300'][0]={'uid':key['uid'],'side':key['side'],'outcome':key['outcome'],'error':'synthetic'}
        self.cohorts['top50'][0]=noisy;self.rows[idx]=copy.deepcopy(noisy)
        self.prepare()
        self.rows[idx]=copy.deepcopy(key)
        with self.assertRaisesRegex(P.PrepareError,'first-success precedence'):self.prepare()

    def test_error_record_and_invalid_memberships_fail_closed(self):
        self.members[0]['cohorts']=['unknown']
        with self.assertRaises(P.PrepareError): self.prepare()
        self.members[0]['cohorts']=['lt1300','top50'];self.rows[0]['outcome']=1
        with self.assertRaises(P.PrepareError): self.prepare()


class CliHandoffTests(unittest.TestCase):
    def test_prepare_cli_artifacts_feed_calibrator_cli(self):
        # Both real CLI parsers run. All files, selectors and roster exclusions are synthetic.
        rows,members,_,_,counts=records_and_memberships()
        chosen={h:[f'fixture-{h}-{i}' for i in range(200) if C.half(f'fixture-{h}-{i}')==h][:6] for h in (0,1)}
        all_rows=[];all_members=[];cohorts={c:[] for c in P.COHORTS};species=[]
        for row,member in zip(rows,members):
            i=int(row['uid'].split('-')[-1])
            for h in (0,1):
                item=copy.deepcopy(row);item['uid']=chosen[h][i];all_rows.append(item)
                entry={'uid':item['uid'],'side':item['side'],'cohorts':member['cohorts']};all_members.append(entry)
                for cohort in entry['cohorts']: cohorts[cohort].append(copy.deepcopy(item))
                species.extend({'uid':item['uid'],'player':item['side'],
                                'species_preview':f'fixture{i}{h}{item["side"]}{n}'} for n in range(6))
        counts={k:v*2 for k,v in counts.items()}
        # Match sorted original cohort-file insertion order and first-success selection.
        merged={}
        for cohort in sorted(P.COHORTS,key=lambda c:P.SOURCE_FILES[c]):
            for row in cohorts[cohort]:
                key=(row['uid'],row['side'])
                if key not in merged or ('error' in merged[key] and 'error' not in row): merged[key]=row
        all_rows=list(merged.values())
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); snapshot=root/'snap';snapshot.mkdir()
            def write(path,obj):
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(P.jsonbytes(obj));return path
            def lines(path,rs):
                path.write_bytes(b''.join(P.jsonbytes(r) for r in rs));return path
            merged_path=lines(root/'merged.jsonl',all_rows); member_path=write(root/'members.json',all_members)
            top=lines(root/'top-sets.jsonl',species);broad=lines(root/'broad-sets.jsonl',[])
            spec=write(root/'selection.json',{'sources':{'top':{'sets':{'path':str(top),'sha256':P.sha_file(top)}},
                'broad':{'sets':{'path':str(broad),'sha256':P.sha_file(broad)}}}})
            dummy=[write(root/name,{'fixture':True}) for name in ('pool.json','split.json','species.json')]
            selected={(m['uid'],m['side']):set(m['cohorts']) for m in all_members}
            outcomes={(r['uid'],r['side']):r['outcome'] for r in all_rows}
            bindings={key:'synthetic-hash' for key in selected}
            args=['c8_prepare.py','--merged',str(merged_path),'--merged-sha256',P.sha_file(merged_path),
                  '--membership',str(member_path),'--membership-sha256',P.sha_file(member_path),
                  '--selection-spec',str(spec),'--selection-spec-sha256',P.sha_file(spec)]
            for cohort in P.COHORTS:
                path=lines(snapshot/P.SOURCE_FILES[cohort],cohorts[cohort]); args+=['--cohort',cohort,str(path),P.sha_file(path)]
            for path in (top,broad):args+=['--sets',str(path),P.sha_file(path)]
            for flag,path in zip(('pool','split','species'),dummy):args+=['--'+flag+'-metadata' if flag!='pool' else '--pool-manifest',str(path),'--'+flag+'-sha256',P.sha_file(path)]
            output=root/'scratch_ds/codex/c8/prepared';args+=['--out-dir',str(output)]
            excluded_uid=chosen[0][2]
            excluded_team=[r['species_preview'] for r in species if r['uid']==excluded_uid and r['player']=='p2']
            excluded_hash=P.roster_digest(excluded_team)
            actual_prepare=P.prepare
            def synthetic_prepare(*a,**kw):return actual_prepare(*a,**kw,expected_counts=counts)
            with patch.object(P,'ROOT',root),patch.object(P,'reconstruct_membership',return_value=(selected,bindings,outcomes)), \
                 patch.object(P,'hr_val_roster_digests',return_value={excluded_hash}),patch.object(P,'reserved_roster_digests',return_value=set()), \
                 patch.object(P,'excluded_rosters',return_value={excluded_hash}),patch.object(P,'prepare',side_effect=synthetic_prepare), \
                 patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):
                P.main()
            member_out=output/'membership.json'; clean=output/'clean.jsonl'; report=output/'filter-report.json'
            parsed=json.loads(member_out.read_bytes());prepared_report=json.loads(report.read_bytes())
            self.assertIsInstance(parsed,list)
            self.assertTrue(all(set(x)=={'uid','side','cohorts'} for x in parsed))
            self.assertEqual(P.sha_file(member_out),prepared_report['clean_membership_sha256'])
            self.assertEqual(P.sha_file(clean),prepared_report['clean_sha256'])
            self.assertEqual(len(C.load_membership(P.jsonl(clean.read_bytes()),parsed)[0]),len(all_rows)-2)
            self.assertFalse(any(r['uid']==excluded_uid for r in parsed))
            final=root/'scratch_ds/codex/c8/calibration'
            args=['calib_b.py',str(clean),'--input-sha256',P.sha_file(clean),'--membership',str(member_out),
                  '--membership-sha256',P.sha_file(member_out),'--filter-report',str(report),
                  '--filter-report-sha256',P.sha_file(report),'--out-dir',str(final)]
            with patch.object(P,'ROOT',root),patch.object(C.B,'load_cal',side_effect=lambda name:C.B.Cal(1,0,name)), \
                 patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as status:C.main()
            self.assertIn(status.exception.code,(0,1))
            result=json.loads((final/'calib_by_band.json').read_text())
            self.assertEqual(result['membership_sha256'],P.sha_file(member_out))
            self.assertEqual(result['game_sides'],len(all_rows)-2)
            self.assertEqual(result['test_by_band']['top50']['test']['sides'],4)



class CalibrationTests(unittest.TestCase):
    def test_unrounded_ece_and_fixed_bin_definition(self):
        raw=C.metrics_raw([.04004,.95996],[0,1]); rounded=C.metrics([.04004,.95996],[0,1])
        self.assertGreater(raw['ece'],.04);self.assertEqual(rounded['ece'],.04)
        self.assertAlmostEqual(raw['brier'],.04004**2)
        self.assertEqual(sum(x[1] for x in raw['reliability']),2)

    def test_unique_pooled_and_overlapping_cohort_fanout_with_exact_gate(self):
        rows,members,_,_,_=records_and_memberships()
        # Create one synthetic fit game and one synthetic held-out game per cohort.
        chosen={}
        for h in (0,1):
            chosen[h]=[f'fake-{h}-{i}' for i in range(100) if C.half(f'fake-{h}-{i}')==h][:6]
        full=[];labels=[]
        for r,m in zip(rows,members):
            i=int(r['uid'].split('-')[-1])
            for h in (0,1):
                x=copy.deepcopy(r);x['uid']=chosen[h][i];full.append(x)
                labels.append({'uid':x['uid'],'side':x['side'],'cohorts':m['cohorts']})
        class FixedCal:
            def __init__(self,*_): pass
            def __call__(self,x): return .04004 if x<0 else .95996
        with patch.object(C.B,'Cal',FixedCal),patch.object(C.B,'from_record',return_value={'L':.1}):
            report,lref=C.analyze_clean(full,labels,references={})
        self.assertEqual(report['test_pooled']['test']['sides'],12)
        self.assertEqual(report['test_by_band']['top50']['test']['sides'],4)
        self.assertEqual(lref['n']['all'],24);self.assertEqual(lref['n']['top50'],8)
        self.assertEqual(report['test_pooled']['new']['ece'],.04)
        self.assertGreater(report['acceptance']['pooled_ece_unrounded'],.04)
        self.assertFalse(report['pass'])
        self.assertEqual(report['test_pooled']['uncertainty']['replicates'],1000)
        self.assertEqual(report['test_pooled']['uncertainty']['seed'],7)

    def test_original_game_half_and_bootstrap_are_reproducible(self):
        games={'a':[(-2,0),(2,1)],'b':[(-1,0),(1,1)]}
        cal=lambda x:.2 if x<0 else .8
        a=C.bootstrap_games(games,cal);b=C.bootstrap_games(dict(reversed(list(games.items()))),cal)
        self.assertEqual(a,b);self.assertTrue(a['non_gating'])
        self.assertEqual(C.half('same-game'),C.half('same-game'))


if __name__=='__main__': unittest.main()
