#!/usr/bin/env python3
"""Calibrate Foul Play's root value (MCTS score) into a win probability.

Input: extractor output (decisions JSONL with root_value, outcome, turn, uid). Games are
split into K folds by a hash of the game id (no game is in two folds); every metric is
computed on out-of-fold predictions (each game scored by a calibrator that never saw it).
Candidates:
  * isotonic (pool-adjacent-violators, monotone non-decreasing, interpolated), and
  * Platt (logistic on logit(root_value)),
  * the raw root value itself (identity),
and reports out of fold: Brier, log loss, ECE (10 equal-width bins) and the reliability
table, overall and by turn bucket, plus a game-bootstrap 95% interval of the Brier
difference vs the raw value. The best out-of-fold candidate is refit on all games and
saved as JSON (pure Python, no numpy needed).

Usage:
  python calibrate.py scratch_ds/review/lab.jsonl.gz --out tools/review/calibrator.json
"""
import argparse
import bisect
import gzip
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

TURN_BUCKETS = [(0, 0, "preview"), (1, 5, "1-5"), (6, 15, "6-15"), (16, 30, "16-30"), (31, 10 ** 6, "31+")]


def load(paths, include_preview=True):
    rows = []
    for p in paths:
        op = gzip.open if str(p).endswith(".gz") else open
        with op(p, "rt") as f:
            for l in f:
                r = json.loads(l)
                if r.get("outcome") is None or r.get("root_value") is None:
                    continue
                if r.get("team_preview") and not include_preview:
                    continue
                game = r["uid"].rsplit("/", 2)[0]
                rows.append({"game": game, "x": float(r["root_value"]), "y": int(r["outcome"]),
                             "turn": int(r.get("turn") or 0), "cfg": r.get("cfg"), "side": r.get("side")})
    return rows


def is_test(game, frac=0.3):
    return int(hashlib.sha256(game.encode()).hexdigest()[:8], 16) / 16 ** 8 < frac


# ---------------------------------------------------------------- calibrators

class Isotonic:
    kind = "isotonic"

    def __init__(self, xs=None, ys=None):
        self.xs, self.ys = xs or [], ys or []

    def fit(self, x, y):
        pts = sorted(zip(x, y))
        blocks = []  # [sum_y, n, x_min, x_max]
        for xi, yi in pts:
            blocks.append([yi, 1, xi, xi])
            while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
                s, n, lo, hi = blocks.pop()
                blocks[-1][0] += s
                blocks[-1][1] += n
                blocks[-1][3] = hi
        # knots at block centres, values = block means, clipped away from 0/1
        self.xs = [(b[2] + b[3]) / 2 for b in blocks]
        self.ys = [min(0.995, max(0.005, b[0] / b[1])) for b in blocks]
        return self

    def __call__(self, x):
        xs, ys = self.xs, self.ys
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = bisect.bisect_right(xs, x)
        x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y1

    def to_json(self):
        return {"kind": self.kind, "xs": [round(v, 6) for v in self.xs], "ys": [round(v, 6) for v in self.ys]}


def _logit(p):
    p = min(1 - 1e-4, max(1e-4, p))
    return math.log(p / (1 - p))


class Platt:
    kind = "platt"

    def __init__(self, a=1.0, b=0.0):
        self.a, self.b = a, b

    def fit(self, x, y, iters=60):
        z = [_logit(v) for v in x]
        a, b = 1.0, 0.0
        for _ in range(iters):  # Newton on the 2-parameter logistic log likelihood
            ga = gb = haa = hab = hbb = 0.0
            for zi, yi in zip(z, y):
                p = 1 / (1 + math.exp(-(a * zi + b)))
                g = p - yi
                w = p * (1 - p)
                ga += g * zi
                gb += g
                haa += w * zi * zi
                hab += w * zi
                hbb += w
            det = haa * hbb - hab * hab
            if det <= 1e-12:
                break
            da = (hbb * ga - hab * gb) / det
            db = (haa * gb - hab * ga) / det
            a, b = a - da, b - db
            if abs(da) + abs(db) < 1e-9:
                break
        self.a, self.b = a, b
        return self

    def __call__(self, x):
        return 1 / (1 + math.exp(-(self.a * _logit(x) + self.b)))

    def to_json(self):
        return {"kind": self.kind, "a": self.a, "b": self.b}


class Identity:
    kind = "identity"

    def __call__(self, x):
        return min(0.995, max(0.005, x))

    def to_json(self):
        return {"kind": self.kind}


def load_calibrator(path=None):
    """Load a saved calibrator (default: tools/review/calibrator.json); identity if missing."""
    path = Path(path) if path else Path(__file__).resolve().parent / "calibrator.json"
    if not path.exists():
        return Identity()
    d = json.loads(path.read_text())
    d = d.get("calibrator", d)
    if d["kind"] == "isotonic":
        return Isotonic(d["xs"], d["ys"])
    if d["kind"] == "platt":
        return Platt(d["a"], d["b"])
    return Identity()


# ---------------------------------------------------------------- metrics

def metrics(p, y, bins=10):
    n = len(y)
    if n == 0:
        return {"n": 0}
    brier = sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / n
    ll = -sum(yi * math.log(min(1 - 1e-6, max(1e-6, pi))) + (1 - yi) * math.log(min(1 - 1e-6, max(1e-6, 1 - pi)))
              for pi, yi in zip(p, y)) / n
    table = defaultdict(lambda: [0, 0.0, 0.0])
    for pi, yi in zip(p, y):
        b = min(bins - 1, int(pi * bins))
        table[b][0] += 1
        table[b][1] += pi
        table[b][2] += yi
    ece = sum(abs(t[1] - t[2]) for t in table.values()) / n
    rel = [{"bin": f"{b / bins:.1f}-{(b + 1) / bins:.1f}", "n": t[0], "mean_pred": round(t[1] / t[0], 3),
            "win_rate": round(t[2] / t[0], 3)} for b, t in sorted(table.items())]
    base = sum(y) / n
    return {"n": n, "brier": round(brier, 4), "log_loss": round(ll, 4), "ece": round(ece, 4),
            "brier_skill_vs_base_rate": round(1 - brier / (base * (1 - base)), 4) if 0 < base < 1 else None,
            "reliability": rel}


def bucket(turn):
    for lo, hi, name in TURN_BUCKETS:
        if lo <= turn <= hi:
            return name
    return "?"


def report(rows, cal_fns):
    out = {}
    for name, fn in cal_fns.items():
        p = [fn(r["x"]) for r in rows]
        y = [r["y"] for r in rows]
        res = {"all": metrics(p, y)}
        for _, _, b in TURN_BUCKETS:
            idx = [i for i, r in enumerate(rows) if bucket(r["turn"]) == b]
            m = metrics([p[i] for i in idx], [y[i] for i in idx])
            m.pop("reliability", None)
            res[b] = m
        out[name] = res
    return out


def fold_of(game, k):
    return int(hashlib.sha256(game.encode()).hexdigest()[:8], 16) % k


def game_bootstrap_delta(rows, pa, pb, reps=500, seed=0):
    """95% interval of Brier(a) - Brier(b), resampling games."""
    import random
    rnd = random.Random(seed)
    by_game = defaultdict(list)
    for i, r in enumerate(rows):
        by_game[r["game"]].append(i)
    games = list(by_game)
    deltas = []
    for _ in range(reps):
        idx = [i for g in (rnd.choice(games) for _ in games) for i in by_game[g]]
        deltas.append(sum((pa[i] - rows[i]["y"]) ** 2 - (pb[i] - rows[i]["y"]) ** 2 for i in idx) / len(idx))
    deltas.sort()
    return [round(deltas[int(0.025 * reps)], 4), round(deltas[int(0.975 * reps) - 1], 4)]


def load_pairs(paths):
    """Matched simultaneous decisions: (game, turn) at k=0 (and team preview), both seats present."""
    by = defaultdict(dict)
    for p in paths:
        op = gzip.open if str(p).endswith(".gz") else open
        with op(p, "rt") as f:
            for l in f:
                r = json.loads(l)
                if r.get("root_value") is None or r.get("outcome") is None or r.get("k") != 0:
                    continue
                game = r["uid"].rsplit("/", 2)[0]
                by[(game, int(r.get("turn") or 0))][r["side"]] = (float(r["root_value"]), int(r["outcome"]))
    pairs = []
    for (game, turn), sides in by.items():
        if "A" in sides and "B" in sides:
            pairs.append({"game": game, "turn": turn, "xa": sides["A"][0], "xb": sides["B"][0],
                          "ya": sides["A"][1], "yb": sides["B"][1]})
    return pairs


def _gap_stats(g):
    g = sorted(g)
    n = len(g)
    if not n:
        return {"n": 0}
    q = lambda f: round(g[min(n - 1, int(f * n))], 4)  # noqa: E731
    return {"n": n, "mean": round(sum(g) / n, 4), "median": q(0.5), "p75": q(0.75), "p90": q(0.9), "p99": q(0.99),
            "share_gt_0.05": round(sum(x > 0.05 for x in g) / n, 3), "share_gt_0.10": round(sum(x > 0.10 for x in g) / n, 3)}


def mirror_report(paths, folds=5):
    """|P_A(win) + P_B(win) - 1| at matched (game, turn, k=0) pairs, raw vs calibrated.

    'pooled' = calibrator fit on both seats; 'seat_A_only' = fit on seat A's decisions only.
    Both are cross-fitted by game (each pair is scored by calibrators that never saw its game).
    Note sum-to-one is not guaranteed even for a perfect per-seat calibrator: each seat's value is
    conditioned on its own (different) private information and sampled beliefs.
    """
    pairs = load_pairs(paths)
    rows = load(paths)
    fns = {"raw": [], "pooled_platt": [], "pooled_isotonic": [], "seat_A_only_platt": [], "seat_A_only_isotonic": []}
    for f in range(folds):
        tr = [r for r in rows if fold_of(r["game"], folds) != f]
        tr_a = [r for r in tr if r.get("side") == "A"]
        cals = {"raw": Identity(),
                "pooled_platt": Platt().fit([r["x"] for r in tr], [r["y"] for r in tr]),
                "pooled_isotonic": Isotonic().fit([r["x"] for r in tr], [r["y"] for r in tr]),
                "seat_A_only_platt": Platt().fit([r["x"] for r in tr_a], [r["y"] for r in tr_a]),
                "seat_A_only_isotonic": Isotonic().fit([r["x"] for r in tr_a], [r["y"] for r in tr_a])}
        for pr in pairs:
            if fold_of(pr["game"], folds) == f:
                for name, c in cals.items():
                    fns[name].append((pr, abs(c(pr["xa"]) + c(pr["xb"]) - 1)))
    out = {"pairs": len(pairs), "games": len({p["game"] for p in pairs}), "folds": folds}
    for name, lst in fns.items():
        res = {"all": _gap_stats([g for _, g in lst])}
        for lo, hi, b in TURN_BUCKETS:
            res[b] = _gap_stats([g for pr, g in lst if lo <= pr["turn"] <= hi])
        out[name] = res
    # signed bias: do the two seats jointly over- or under-claim?
    out["mean_signed_sum_minus_1_raw"] = round(sum(p["xa"] + p["xb"] - 1 for p in pairs) / max(1, len(pairs)), 4)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "calibrator.json"))
    ap.add_argument("--report", default=None)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--mirror", default=None, help="write the mirror-consistency report (JSON) here and exit")
    args = ap.parse_args()
    if args.mirror:
        rep = mirror_report(args.inputs, args.folds)
        Path(args.mirror).write_text(json.dumps(rep, indent=1))
        print(json.dumps({k: (v["all"] if isinstance(v, dict) and "all" in v else v) for k, v in rep.items()}, indent=1))
        return
    rows = load(args.inputs)
    games = sorted({r["game"] for r in rows})
    k = args.folds
    oof = {"raw_root_value": [None] * len(rows), "isotonic": [None] * len(rows), "platt": [None] * len(rows),
           "constant_0.5": [0.5] * len(rows)}
    for f in range(k):
        tr = [r for r in rows if fold_of(r["game"], k) != f]
        iso = Isotonic().fit([r["x"] for r in tr], [r["y"] for r in tr])
        pl = Platt().fit([r["x"] for r in tr], [r["y"] for r in tr])
        for i, r in enumerate(rows):
            if fold_of(r["game"], k) == f:
                oof["raw_root_value"][i] = Identity()(r["x"])
                oof["isotonic"][i] = iso(r["x"])
                oof["platt"][i] = pl(r["x"])
    held = {}
    for name, p in oof.items():
        res = {"all": metrics(p, [r["y"] for r in rows])}
        for _, _, b in TURN_BUCKETS:
            idx = [i for i, r in enumerate(rows) if bucket(r["turn"]) == b]
            m = metrics([p[i] for i in idx], [rows[i]["y"] for i in idx])
            m.pop("reliability", None)
            res[b] = m
        held[name] = res
    # choose by out-of-fold Brier; the raw root value is a candidate (identity)
    best = min(("raw_root_value", "isotonic", "platt"), key=lambda n: held[n]["all"]["brier"])
    delta = {n: game_bootstrap_delta(rows, oof[n], oof["raw_root_value"]) for n in ("isotonic", "platt")}
    full_iso = Isotonic().fit([r["x"] for r in rows], [r["y"] for r in rows])
    full_platt = Platt().fit([r["x"] for r in rows], [r["y"] for r in rows])
    chosen = {"raw_root_value": Identity(), "isotonic": full_iso, "platt": full_platt}[best]
    rep = {"games": len(games), "decisions": len(rows), "folds": k, "cfgs": sorted({str(r['cfg']) for r in rows}),
           "out_of_fold": held, "chosen": best, "brier_delta_vs_raw_95ci": delta,
           "platt_full": full_platt.to_json(), "isotonic_knots": len(full_iso.xs)}
    Path(args.out).write_text(json.dumps({"calibrator": chosen.to_json(), "chosen": best, "fit_on": args.inputs,
                                          "games": len(games), "decisions": len(rows),
                                          "out_of_fold_all": {n: {kk: vv for kk, vv in v["all"].items() if kk != "reliability"}
                                                              for n, v in held.items()},
                                          "brier_delta_vs_raw_95ci": delta}, indent=1))
    if args.report:
        Path(args.report).write_text(json.dumps(rep, indent=1))
    summary = {n: {b: {m: v[b].get(m) for m in ("n", "brier", "ece")} for b in v} for n, v in held.items()}
    print(json.dumps({"games": len(games), "decisions": len(rows), "chosen": best, "brier_delta_vs_raw_95ci": delta,
                      "summary": summary,
                      "reliability_chosen": held[best]["all"]["reliability"]}, indent=1))


if __name__ == "__main__":
    main()
