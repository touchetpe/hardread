#!/usr/bin/env python3
"""Which decisions the product offers a verdict on ("biggest disagreements with the engine", research/27 §4.3),
and the VT1 population built with exactly that rule (research/28 §0.2 V3, S1.21, E7 step 1).

The product rule (one implementation; the app should import `offered_from_review`):
  * a user decision with a known action (hidden actions are never offered);
  * win probability before the decision in [0.20, 0.80] (calibrated root value);
  * cost = WP(engine-best) - WP(chosen) >= 0.05 on the review search (engine-best = Foul Play's ranking, the
    option with the highest visit share);
  * Draft mode: neither the chosen nor the engine-best option is 'assumed' (uses an unrevealed own move or item);
  * at most 3 per game-side, by cost (descending). Never summed.

VT1 population (Foul Play venv; reads human_eval.py farm output and the label-farm decisions it was built from):
  ../environments/foulplay/bin/python tools/review/select_offered.py scratch_ds/verdict/vt1_review.jsonl \
      --decisions <label-farm decisions .jsonl.gz> --n 150 --seed 7 --out scratch_ds/verdict/vt1_candidates.jsonl
--n/--seed must be the ones given to `human_eval.py farm` (they rebuild the same jobs, to attach each case's log
and team export). Each output line is a verdict case: chosen = the bot's own C1t1 move, X = the review's engine-best.
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bridge as B  # noqa: E402  (calibrators)

WP_LO, WP_HI, MIN_COST, MAX_PER_SIDE = 0.20, 0.80, 0.05, 3


def is_offered(wp_before, cost, wp_lo=WP_LO, wp_hi=WP_HI, min_cost=MIN_COST):
    return wp_before is not None and cost is not None and wp_lo <= wp_before <= wp_hi and cost >= min_cost


def offered_from_review(review, max_per_side=MAX_PER_SIDE, assumed=None):
    """review.py output -> the decisions offered as 'disagreements' (dicts from review['decisions']), by cost.
    assumed: optional callable(decision) -> True if the chosen or engine-best option is assumed (Draft mode)."""
    c = [d for d in review.get("decisions") or []
         if d.get("action") and is_offered(d.get("win_prob"), d.get("decision_cost"))
         and not (assumed and assumed(d))]
    return sorted(c, key=lambda d: -d["decision_cost"])[:max_per_side]


def offered_from_record(record, cal, max_per_side=MAX_PER_SIDE, wp_lo=WP_LO, wp_hi=WP_HI, min_cost=MIN_COST):
    """A raw review record (human_eval.py run/farm) -> (offered decisions with wp/cost, per-side counters)."""
    out, cnt = [], Counter()
    for d in record.get("decisions") or []:
        cnt["decisions"] += 1
        o = d.get("options") or {}
        act, best = d.get("action"), d.get("best")
        if not act or act not in o or not best or best not in o or o[act].get("A") is None \
                or o[best].get("A") is None or d.get("root_A") is None:
            cnt["no_known_action_or_value"] += 1
            continue
        wp = cal(d["root_A"])
        wp_best, wp_act = cal(o[best]["A"]), cal(o[act]["A"])
        cost = max(0.0, wp_best - wp_act)
        if wp_lo <= wp <= wp_hi:
            cnt["balanced"] += 1
        if is_offered(wp, cost, wp_lo, wp_hi, min_cost):
            out.append({"turn": d["turn"], "k": d["k"], "kind": d["kind"], "chosen": act, "X": best,
                        "wp_before": wp, "cost": cost, "wp_X": wp_best, "wp_chosen": wp_act})
    out.sort(key=lambda x: -x["cost"])
    cnt["offered_before_cap"] = len(out)
    cnt["dropped_by_cap"] = max(0, len(out) - max_per_side)
    out = out[:max_per_side]
    for i, x in enumerate(out):
        x["rank_in_side"] = i + 1
    cnt["offered"] = len(out)
    return out, cnt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review", help="human_eval.py farm (or run) output .jsonl")
    ap.add_argument("--decisions", default=None,
                    help="the label-farm decisions .jsonl.gz given to human_eval.py farm (attaches log + team)")
    ap.add_argument("--n", type=int, default=None, help="--n given to human_eval.py farm")
    ap.add_argument("--seed", type=int, default=None, help="--seed given to human_eval.py farm")
    ap.add_argument("--cal", default="absolute_human", help="product calibrator (calibrator.json key)")
    ap.add_argument("--max-per-side", type=int, default=MAX_PER_SIDE)
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", default=None, help="summary JSON (default: <out>.summary.json)")
    a = ap.parse_args()
    cal = B.load_cal(a.cal)
    jobs = {}
    if a.decisions:
        if a.n is None or a.seed is None:
            ap.error("--decisions needs the --n and --seed used for human_eval.py farm")
        import fpenv
        fpenv.configure()
        import human_eval as H
        for j in H.farm_jobs(a.decisions, a.n, a.seed, 4, 20000):
            jobs[(j["uid"], j["side"])] = j
    tot, kinds, cases, errors = Counter(), Counter(), [], 0
    per_side_offered = []
    for line in open(a.review):
        r = json.loads(line)
        if "error" in r:
            errors += 1
            continue
        tot["sides"] += 1
        sel, cnt = offered_from_record(r, cal, a.max_per_side)
        tot.update(cnt)
        per_side_offered.append(len(sel))
        for x in sel:
            kinds[x["kind"]] += 1
            case = {"case_id": f"{r['uid']}|{r['side']}|{x['turn']}|{x['k']}", "uid": r["uid"], "side": r["side"],
                    "farm_side": (r.get("meta") or {}).get("farm_side"), "outcome": r.get("outcome"), **x,
                    "selection": {"cal": a.cal, "wp_range": [WP_LO, WP_HI], "min_cost": MIN_COST,
                                  "max_per_side": a.max_per_side, "review_search": "4 worlds x 20k iterations, "
                                  "world seed _seed_for(('he', idx, turn, k)) (human_eval.py)"}}
            j = jobs.get((r["uid"], r["side"]))
            if a.decisions:
                if j is None:
                    case["error"] = "job not found in --decisions (check --n/--seed)"
                else:
                    case["log"], case["team"] = j["log"], j["team"]
                    tc = j["truth"].get(f"{x['turn']}:{x['k']}")
                    case["truth_check"] = tc == x["chosen"]
            cases.append(case)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    summ = {"tool": "tools/review/select_offered.py", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "review": a.review, "decisions": a.decisions, "cal": a.cal, "sides": tot["sides"], "error_sides": errors,
            "decisions_total": tot["decisions"], "decisions_balanced": tot["balanced"],
            "offered_before_cap": tot["offered_before_cap"], "dropped_by_cap": tot["dropped_by_cap"],
            "offered": len(cases), "share_of_balanced_before_cap": round(tot["offered_before_cap"] / max(1, tot["balanced"]), 4),
            "by_kind": dict(kinds), "sides_with_any": sum(1 for n in per_side_offered if n),
            "missing_log_or_team": sum(1 for c in cases if "error" in c),
            "truth_mismatch": sum(1 for c in cases if c.get("truth_check") is False),
            "accept_ge_100": len(cases) >= 100}
    sp = a.summary or (str(Path(a.out).with_suffix("")) + ".summary.json")
    Path(sp).write_text(json.dumps(summ, indent=1) + "\n")
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
