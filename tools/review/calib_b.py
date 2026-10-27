#!/usr/bin/env python3
"""Draft-B human calibrator fit, ECE by rating band, and the luck reference for the card (research/28 S2.2, E9).

System python3 is enough (no Foul Play import). From hardread/:

  python3 tools/review/calib_b.py scratch_ds/review/human_b/run_b.jsonl --name absolute_human_b \
      --out-dir scratch_ds/review/human_b [--write-calibrator]

Same method as the existing `absolute_human` calibrator (human_eval.py analyze):
  * rows = turn-start decisions (k = 0, turn >= 1) of every reviewed game-side, x = mean A_dec over the luck run's
    unconditioned first-K worlds (eval + 10k-iteration MCTS correction), y = the side's result;
  * P(win) = sigmoid(a * 0.0125 * x + b), fitted by Newton's method on the games with sha256(uid) % 2 == 0,
    tested on the other half (no game in both);
  * ECE = 10 equal-width bins, |mean p - mean y| weighted by bin size.
Band = the game's `rating_band` (broad crawl), or 'top50' for the top-50 ladder set (no band field).
Duplicate (uid, side) records (a game in two runs) are kept once.

Acceptance (S2.2): pooled test ECE <= 0.04 and every band's test ECE <= 0.06.
Outputs: <out-dir>/calib_by_band.json; <out-dir>/luck_reference_b.json (total-luck quantiles per band and pooled,
from bridge.from_record with the new calibrator: the card's percentile; Ag-App copies it into app/data/);
--write-calibrator adds <name> to tools/review/calibrator.json (only if the acceptance passes, unless --force).
"""
import argparse
import hashlib
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bridge as B  # noqa: E402

K = B.K


def half(uid):
    return int(hashlib.sha256(uid.encode()).hexdigest()[:8], 16) % 2


def fit_abs(xs, ys, iters=50):
    """Newton's method for P = sigmoid(a * K * x + b) (luck.AbsCal.fit, copied: no Foul Play import)."""
    z = [K * x for x in xs]
    a, b = 1.0, 0.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for zi, yi in zip(z, ys):
            p = B.sigmoid(a * zi + b)
            g, w = p - yi, p * (1 - p)
            ga += g * zi
            gb += g
            haa += w * zi * zi
            hab += w * zi
            hbb += w
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


def metrics_raw(p, y, bins=10):
    if len(p) != len(y):
        raise ValueError('probability and outcome counts differ')
    n = len(y)
    if not n:
        return {'n': 0, 'ece': None, 'brier': None, 'log_loss': None, 'reliability': []}
    if any(not isinstance(x, (float, int)) or not math.isfinite(x) or x < 0 or x > 1 for x in p):
        raise ValueError('invalid predicted probability')
    if any(type(x) is not int or x not in (0, 1) for x in y):
        raise ValueError('invalid binary outcome')
    brier = sum((a-b)**2 for a,b in zip(p,y))/n
    ll = -sum(b*math.log(min(1-1e-6,max(1e-6,a))) + (1-b)*math.log(min(1-1e-6,max(1e-6,1-a)))
              for a,b in zip(p,y))/n
    tab = defaultdict(lambda:[0,0.0,0.0])
    for a,b in zip(p,y):
        k=min(bins-1,int(a*bins)); tab[k][0]+=1; tab[k][1]+=a; tab[k][2]+=b
    return {'n':n, 'brier':brier, 'log_loss':ll, 'ece':sum(abs(t[1]-t[2]) for t in tab.values())/n,
            'reliability':[(f'{k/bins:.1f}',t[0],round(t[1]/t[0],3),round(t[2]/t[0],3))
                           for k,t in sorted(tab.items())]}


def metrics(p, y, bins=10):
    out=metrics_raw(p,y,bins)
    return {k:(round(v,4) if k in ('brier','log_loss','ece') and v is not None else v) for k,v in out.items()}


def bootstrap_games(game_rows, cal, *, seed=7, replicates=1000):
    """Non-gating percentile intervals; resample original games, keeping both sides together."""
    games=sorted(game_rows)
    if not games: return {'games':0,'replicates':replicates,'seed':seed,'ece_ci95':None,'brier_ci95':None}
    rng=random.Random(seed); eces=[]; briers=[]
    for _ in range(replicates):
        selected=[rng.choice(games) for _ in games]
        rows=[row for uid in selected for row in game_rows[uid]]
        m=metrics_raw([cal(x) for x,_ in rows],[y for _,y in rows])
        if m['n']:
            eces.append(m['ece']); briers.append(m['brier'])
    def interval(values):
        if len(values)!=replicates: return None
        values.sort(); return [values[int(.025*(replicates-1))], values[int(.975*(replicates-1))]]
    return {'games':len(games),'replicates':replicates,'seed':seed,'ece_ci95':interval(eces),
            'brier_ci95':interval(briers),'non_gating':True}


def band_of(r):
    m = r.get("meta") or {}
    return m.get("rating_band") or "top50"


def load(paths):
    recs, seen, dup, err = [], set(), 0, 0
    for p in paths:
        for line in open(p):
            r = json.loads(line)
            if "error" in r:
                err += 1
                continue
            key = (r["uid"], r["side"])
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            recs.append(r)
    return recs, dup, err


def dec_rows(r):
    out = []
    for t in r.get("luck") or []:
        ws = [w for w in t.get("worlds") or [] if w["draw"] < t["K"] and w.get("A_dec") is not None]
        if t["k"] == 0 and t["turn"] >= 1 and ws:
            out.append((sum(w["A_dec"] for w in ws) / len(ws), r["outcome"]))
    return out


def quantiles(xs, qs):
    s = sorted(xs)
    return [round(s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))], 4) for q in qs] if s else []


COHORTS = ('lt1300','1300-1499','1500-1699','1700-1899','1900plus','top50')


def load_membership(rows, entries):
    records={}
    for row in rows:
        key=(row.get('uid'),row.get('side'))
        if not isinstance(key[0],str) or not key[0] or key[1] not in ('p1','p2') or key in records:
            raise ValueError('clean input identity is missing or duplicated')
        if 'error' not in row and (type(row.get('outcome')) is not int or row['outcome'] not in (0,1)):
            raise ValueError('invalid outcome in clean record')
        records[key]=row
    memberships={}
    for entry in entries:
        key=(entry.get('uid'),entry.get('side')); labels=entry.get('cohorts')
        if (key in memberships or not isinstance(labels,list) or not labels or len(labels)!=len(set(labels))
                or any(label not in COHORTS for label in labels)):
            raise ValueError('membership labels are missing, duplicated or unknown')
        memberships[key]=tuple(sorted(labels))
    if set(records)!=set(memberships): raise ValueError('membership/input keys differ')
    for uid in {k[0] for k in records}:
        if (uid,'p1') not in records or (uid,'p2') not in records or memberships[(uid,'p1')]!=memberships[(uid,'p2')]:
            raise ValueError('missing side or inconsistent game membership')
        a,b=records[(uid,'p1')],records[(uid,'p2')]
        if 'error' not in a and 'error' not in b and a['outcome']+b['outcome']!=1:
            raise ValueError('inconsistent game outcomes')
    return records,memberships


def analyze_clean(rows, entries, *, name='absolute_human_b', references=None):
    records,memberships=load_membership(rows,entries)
    successful=[r for r in records.values() if 'error' not in r]
    fit=[v for r in successful if half(r['uid'])==0 for v in dec_rows(r)]
    if not fit: raise ValueError('no fit rows')
    ca,cb=fit_abs([x for x,_ in fit],[y for _,y in fit]); cal=B.Cal(ca,cb,name)
    references=references if references is not None else {k:B.load_cal(k) for k in ('absolute_human','absolute')}
    pools={'all':successful,**{c:[r for r in successful if c in memberships[(r['uid'],r['side'])]] for c in COHORTS}}
    reports={}; raw_ece={}; luck={}
    for group,recs in pools.items():
        fit_recs=[r for r in recs if half(r['uid'])==0]; test_recs=[r for r in recs if half(r['uid'])==1]
        test=[v for r in test_recs for v in dec_rows(r)]
        raw=metrics_raw([cal(x) for x,_ in test],[y for _,y in test]); raw_ece[group]=raw['ece']
        report={'new':metrics([cal(x) for x,_ in test],[y for _,y in test]),
                'fit':{'games':len({r['uid'] for r in fit_recs}),'sides':len(fit_recs),
                       'rows':sum(len(dec_rows(r)) for r in fit_recs)},
                'test':{'games':len({r['uid'] for r in test_recs}),'sides':len(test_recs),'rows':len(test)},
                'errors':sum('error' in r for key,r in records.items() if group=='all' or group in memberships[key])}
        for key,ref in references.items():
            m=metrics([ref(x) for x,_ in test],[y for _,y in test]); m.pop('reliability',None); report[key]=m
        games=defaultdict(list)
        for r in test_recs:
            values=dec_rows(r)
            if values: games[r['uid']].extend(values)
        report['uncertainty']=bootstrap_games(dict(games),cal)
        reports[group]=report
        ls=[]
        for r in recs:
            bridge=B.from_record(r,cal,with_split=False)
            if bridge is not None: ls.append(bridge['L'])
        luck[group]=ls
    pooled_ok=raw_ece['all'] is not None and raw_ece['all']<=.04
    band_ok=all(raw_ece[c] is not None and raw_ece[c]<=.06 for c in COHORTS)
    report={'calibrator':{'a':round(ca,4),'b':round(cb,4)},'games':len({r['uid'] for r in successful}),
            'game_sides':len(successful),'test_pooled':reports['all'],
            'test_by_band':{c:reports[c] for c in COHORTS},
            'acceptance':{'pooled_ece_unrounded':raw_ece['all'],'band_ece_unrounded':{c:raw_ece[c] for c in COHORTS},
                          'pooled_ok':pooled_ok,'bands_ok':band_ok,
                          'rule':'unrounded pooled ECE <= 0.04 and each of six cohort ECE <= 0.06'},
            'pass':pooled_ok and band_ok}
    qs=[i/100 for i in range(101)]
    lref={'calibrator':name,'a':round(ca,4),'b':round(cb,4),
          'unit':'total luck L per game-side (WP points / 100)',
          'quantiles_0_to_100':{k:quantiles(v,qs) for k,v in luck.items()},
          'n':{k:len(v) for k,v in luck.items()},
          'usage':'percentile of a review bridge.L = share below it (interpolate the list)'}
    return report,lref


def main():
    import c8_prepare as P
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('input'); ap.add_argument('--input-sha256',required=True)
    ap.add_argument('--membership',required=True); ap.add_argument('--membership-sha256',required=True)
    ap.add_argument('--filter-report',required=True); ap.add_argument('--filter-report-sha256',required=True)
    ap.add_argument('--name',default='absolute_human_b'); ap.add_argument('--out-dir',required=True)
    a=ap.parse_args(); raw=P.read_checked(a.input,a.input_sha256); mr=P.read_checked(a.membership,a.membership_sha256)
    fr=json.loads(P.read_checked(a.filter_report,a.filter_report_sha256))
    if fr.get('clean_sha256')!=a.input_sha256 or fr.get('clean_membership_sha256')!=a.membership_sha256:
        raise ValueError('filter report does not bind clean inputs')
    report,lref=analyze_clean(P.jsonl(raw),json.loads(mr),name=a.name)
    report.update({'input_sha256':a.input_sha256,'membership_sha256':a.membership_sha256,
                   'filter_report_sha256':a.filter_report_sha256})
    lref.update({'input_sha256':a.input_sha256,'membership_sha256':a.membership_sha256,
                 'filter_report_sha256':a.filter_report_sha256})
    od=Path(a.out_dir).resolve()
    try: od.relative_to((P.ROOT/'scratch_ds/codex/c8').resolve())
    except ValueError as exc: raise ValueError('output must remain under private C8 directory') from exc
    od.mkdir(parents=True,mode=0o700,exist_ok=False)
    for filename,content in (('calib_by_band.json',report),('luck_reference_b.json',lref)):
        import os
        fd=os.open(od/filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f: f.write(P.jsonbytes(content)); f.flush(); os.fsync(f.fileno())
    print(json.dumps({'pass':report['pass'],'input_sha256':a.input_sha256}))
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__': main()
