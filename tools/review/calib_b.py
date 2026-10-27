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


def metrics(p, y, bins=10):
    n = len(y)
    if not n:
        return {"n": 0}
    brier = sum((a - b) ** 2 for a, b in zip(p, y)) / n
    ll = -sum(b * math.log(min(1 - 1e-6, max(1e-6, a))) + (1 - b) * math.log(min(1 - 1e-6, max(1e-6, 1 - a)))
              for a, b in zip(p, y)) / n
    tab = defaultdict(lambda: [0, 0.0, 0.0])
    for a, b in zip(p, y):
        k = min(bins - 1, int(a * bins))
        tab[k][0] += 1
        tab[k][1] += a
        tab[k][2] += b
    ece = sum(abs(t[1] - t[2]) for t in tab.values()) / n
    return {"n": n, "brier": round(brier, 4), "log_loss": round(ll, 4), "ece": round(ece, 4),
            "reliability": [(f"{k / bins:.1f}", t[0], round(t[1] / t[0], 3), round(t[2] / t[0], 3))
                            for k, t in sorted(tab.items())]}


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--name", default="absolute_human_b")
    ap.add_argument("--out-dir", default="scratch_ds/review/human_b")
    ap.add_argument("--write-calibrator", action="store_true")
    ap.add_argument("--force", action="store_true", help="write the calibrator even if the acceptance fails")
    a = ap.parse_args()
    recs, dup, err = load(a.inputs)
    by_band = defaultdict(list)
    for r in recs:
        by_band[band_of(r)].append(r)
    fit = [row for r in recs if half(r["uid"]) == 0 for row in dec_rows(r)]
    ca, cb = fit_abs([x for x, _ in fit], [y for _, y in fit])
    cal = B.Cal(ca, cb, a.name)
    ref = {k: B.load_cal(k) for k in ("absolute_human", "absolute")}
    test_rows = {band: [row for r in rs if half(r["uid"]) == 1 for row in dec_rows(r)] for band, rs in by_band.items()}
    pooled = [row for rows in test_rows.values() for row in rows]

    def rep(rows):
        out = {"new": metrics([cal(x) for x, _ in rows], [y for _, y in rows])}
        for k, c in ref.items():
            m = metrics([c(x) for x, _ in rows], [y for _, y in rows])
            m.pop("reliability", None)
            out[k] = m
        return out

    report = {"tool": "tools/review/calib_b.py", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "inputs": a.inputs, "name": a.name, "calibrator": {"a": round(ca, 4), "b": round(cb, 4)},
              "game_sides": len(recs), "duplicates_dropped": dup, "error_sides": err,
              "games": len({r["uid"] for r in recs}),
              "fit": {"games": len({r["uid"] for r in recs if half(r["uid"]) == 0}), "rows": len(fit)},
              "test_pooled": rep(pooled),
              "test_by_band": {b: dict(rep(rows), games=len({r["uid"] for r in by_band[b] if half(r["uid"]) == 1}))
                               for b, rows in sorted(test_rows.items())}}
    pooled_ece = report["test_pooled"]["new"]["ece"]
    band_ece = {b: v["new"].get("ece") for b, v in report["test_by_band"].items()}
    report["acceptance"] = {"pooled_ece": pooled_ece, "pooled_ok": pooled_ece is not None and pooled_ece <= 0.04,
                            "band_ece": band_ece,
                            "bands_ok": all(e is not None and e <= 0.06 for e in band_ece.values()),
                            "rule": "pooled test ECE <= 0.04 and every band <= 0.06"}
    report["pass"] = report["acceptance"]["pooled_ok"] and report["acceptance"]["bands_ok"]
    od = Path(a.out_dir)
    od.mkdir(parents=True, exist_ok=True)
    (od / "calib_by_band.json").write_text(json.dumps(report, indent=1) + "\n")
    # luck reference for the card: total luck per game-side under the new calibrator
    Ls = defaultdict(list)
    for r in recs:
        b = B.from_record(r, cal, with_split=False)
        if b is not None:
            Ls[band_of(r)].append(b["L"])
            Ls["all"].append(b["L"])
    qs = [i / 100 for i in range(101)]
    lref = {"calibrator": a.name, "a": round(ca, 4), "b": round(cb, 4), "unit": "total luck L per game-side (WP points / 100)",
            "quantiles_0_to_100": {k: quantiles(v, qs) for k, v in sorted(Ls.items())},
            "n": {k: len(v) for k, v in sorted(Ls.items())},
            "usage": "percentile of a review's bridge.L = share of reference sides with L below it (interpolate the list)"}
    (od / "luck_reference_b.json").write_text(json.dumps(lref) + "\n")
    if a.write_calibrator and (report["pass"] or a.force):
        cp = HERE / "calibrator.json"
        d = json.loads(cp.read_text())
        d[a.name] = {"a": round(ca, 4), "b": round(cb, 4), "kind": "absolute: P(win) = sigmoid(a * 0.0125 * A + b)",
                     "fit_on": f"Draft B (Smogon sets) human_eval runs {', '.join(Path(p).name for p in a.inputs)}: "
                               f"{report['fit']['games']} games (sha256(uid) % 2 == 0); test on the other "
                               f"{len({r['uid'] for r in recs if half(r['uid']) == 1})}: pooled ECE {pooled_ece}, "
                               f"by band {band_ece}",
                     "pass": report["pass"]}
        cp.write_text(json.dumps(d, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("calibrator", "game_sides", "games", "duplicates_dropped", "acceptance",
                                             "pass")}, indent=1))
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
