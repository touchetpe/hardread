#!/usr/bin/env python3
"""Offline check of the belief lever (foul-play/fp/hardread_belief.py, lever A) on belief examples.

For each example (decision × opponent Pokemon), rebuild the Pokemon from its public view (baselines.fp_pokemon),
draw a pool of M sets with Foul Play's sampler (offline, so WITHOUT the in-game damage pruning), score them with the
exported belief and resample them like the lever. Reports the mean probability of the true value under
  pool    the pool itself (what vanilla Foul Play's worlds look like: uniform over its own draws),
  lever   the lever's resampling weights (what the treated bot's worlds look like),
  belief  the belief's own marginal (the target the lever approaches as M grows),
for item / tera / spread class / hidden moves, on scored rows only (hidden attributes).
Needs the Foul Play env (Python 3.13) and numpy (user site-packages is used if the venv lacks it):

  ../environments/foulplay/bin/python tools/belief/lever_offline.py --examples scratch_ds/belief/v1data/val.examples.jsonl.gz \
      --npz scratch_ds/belief/export/belief_v1.npz --groups 40 --m 16
"""
import argparse
import json
import math
import random
import sys
import time
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import baselines as BL  # noqa: E402
from targets import iter_jsonl, toid  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--examples", required=True)
    ap.add_argument("--npz", required=True)
    ap.add_argument("--groups", type=int, default=40, help="number of (game, side) groups")
    ap.add_argument("--m", type=int, default=16)
    ap.add_argument("--lam", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--fp-input", action="store_true", help="feed the pool histogram to a --worlds model (FP_INPUT)")
    a = ap.parse_args()
    fp = BL.fp_setup()
    from fp import hardread_belief as HB
    model = HB.BeliefModel(a.npz)
    groups = defaultdict(list)
    for e in iter_jsonl([a.examples]):
        k = (e["game"], e["side"])
        if k not in groups and len(groups) >= a.groups:
            continue
        groups[k].append(e)
    acc = defaultdict(lambda: defaultdict(float))
    cnt = defaultdict(int)
    random.seed(a.seed)
    t0 = time.time()
    for k, exs in groups.items():
        mode = BL.fp_mode(BL.group_names(exs))
        for e in exs:
            v = e["view"]
            p = BL.fp_pokemon(v)
            pool = []
            for _ in range(a.m):
                q = deepcopy(p)
                fp["sample"](q, mode)
                pool.append(q)
            wd = HB._pool_hist(pool) if (a.fp_input and model.worlds) else None
            row = HB.view_row(v, e["ctx"], e["turn"], e["team_preview"], e["active"], world_dists=wd)
            lp = model.forward([row])
            hid = HB._hidden(v)
            keys = [HB._set_key(q) for q in pool]
            count = defaultdict(int)
            for kk in keys:
                count[kk] += 1
            u, n = len(count), len(pool)
            logw = [a.lam * (HB._score(model, lp, 0, hid, q) - math.log((count[kk] + HB.Q_SMOOTH) / (n + HB.Q_SMOOTH * u)))
                    for q, kk in zip(pool, keys)]
            mx = max(logw)
            w = [math.exp(x - mx) for x in logw]
            tot = sum(w)
            w = [x / tot for x in w]
            y, h = e["y"], e["hidden"]

            def mass(fn, true, weights):
                return sum(wi for q, wi in zip(pool, weights) if fn(q) == true)

            uni = [1.0 / n] * n
            checks = []
            if h["item"]:
                checks.append(("item", lambda q: "none" if q.item in (None, "", "None") else toid(q.item), y["item"],
                               math.exp(lp["item"][0, model.idx("item", y["item"])])))
            if h["tera"]:
                checks.append(("tera", lambda q: toid(q.tera_type) if q.tera_type else "?", y["tera"],
                               math.exp(lp["tera"][0, model.idx("tera", y["tera"])])))
            checks.append(("spread", lambda q: HB.spread_class(q.nature, q.evs or [0] * 6), y["spread"],
                           math.exp(lp["spread"][0, model.idx("spread", y["spread"])])))
            for name, fn, true, pb in checks:
                acc[name]["pool"] += mass(fn, true, uni)
                acc[name]["lever"] += mass(fn, true, w)
                acc[name]["belief"] += pb
                cnt[name] += 1
            hm = h["moves"]
            if hm:
                for label, weights in (("pool", uni), ("lever", w)):
                    acc["moves"][label] += sum(sum(wi for q, wi in zip(pool, weights) if mv in {toid(x.name) for x in q.moves})
                                               for mv in hm) / len(hm)
                acc["moves"]["belief"] += sum(1 / (1 + math.exp(-lp["moves_logit"][0, model.idx("move", mv)])) for mv in hm) / len(hm)
                cnt["moves"] += 1
            acc["ess"]["lever"] += 1.0 / sum(x * x for x in w)
            cnt["ess"] += 1
    res = {name: {lab: round(val / cnt[name], 4) for lab, val in d.items()} for name, d in acc.items()}
    res["rows"] = dict(cnt)
    res["groups"] = len(groups)
    res["m"] = a.m
    res["lambda"] = a.lam
    res["npz"] = a.npz
    res["fp_input"] = bool(a.fp_input)
    res["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(res, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
