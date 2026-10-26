#!/usr/bin/env python3
"""Evaluate reconstructed decisions with Foul Play's own search.

For each decision this runs exactly what fp.search.main.find_best_move runs (deepcopy,
team-preview actives, mode.prepare_battles -> battle_to_poke_engine_state ->
get_result_from_mcts in a process pool), but keeps the full per-world statistics:

  options[m] = {"value": chance-weighted mean MCTS score of option m (side one = the user),
                "share": chance-weighted visit share of m (Foul Play's selection policy)}
  best       = the option Foul Play would rank first (highest visit share)
  cost       = max(0, value[best] - value[chosen])   ("decision cost", raw MCTS score units)
  root_value = chance-weighted mean root score (same definition as the extractor's root_value)

Default: 4 worlds x 1000 ms, 1 thread per world (configurable). Unlike live Foul Play,
the number of worlds is fixed (no doubling while few opponent moves are known), so that
review cost is predictable.
"""
import argparse
import hashlib
import json
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpenv  # noqa: E402

fpenv.configure()

from fp.search.main import get_result_from_mcts  # noqa: E402
from fp.search.poke_engine_helpers import battle_to_poke_engine_state  # noqa: E402


def _seed_for(tag):
    return int(hashlib.sha256(str(tag).encode()).hexdigest()[:12], 16)


def sample_worlds(battle, worlds, seed=None):
    """Foul Play's own belief sample: the find_best_move copy, then mode.prepare_battles."""
    b = deepcopy(battle)
    if b.team_preview:
        b.user.active = b.user.reserve.pop(0)
        b.opponent.active = b.opponent.reserve.pop(0)
    if seed is not None:
        random.seed(seed)
    return b, b.mode.prepare_battles(b, worlds)


def aggregate(results, evals=None):
    """results: list of (MctsResult, chance). Returns options, root_value, opponent policy.

    evals (optional, one static eval per world) adds absolute values on poke-engine's common scale:
    A = eval(world) + logit(score)/K (see value.py; raw MCTS scores are relative to each root's eval).
    """
    import value as V
    total_chance = sum(c for _, c in results) or 1.0
    opts, opp = {}, {}
    root = 0.0
    root_abs = 0.0
    for i, (res, c) in enumerate(results):
        w = c / total_chance
        visits = sum(o.visits for o in res.side_one) or 1
        rv = sum(o.total_score for o in res.side_one) / visits
        root += w * rv
        if evals is not None:
            root_abs += w * (evals[i] + V.logit(rv) / V.K)
        for o in res.side_one:
            e = opts.setdefault(o.move_choice, {"value_num": 0.0, "value_den": 0.0, "share": 0.0, "visits": 0,
                                                "A_num": 0.0})
            e["share"] += w * o.visits / visits
            e["visits"] += o.visits
            if o.visits:
                e["value_num"] += w * o.total_score / o.visits
                e["value_den"] += w
                if evals is not None:
                    e["A_num"] += w * (evals[i] + V.logit(o.total_score / o.visits) / V.K)
        v2 = sum(o.visits for o in res.side_two) or 1
        for o in res.side_two:
            opp[o.move_choice] = opp.get(o.move_choice, 0.0) + w * o.visits / v2
    out = {}
    for m, e in opts.items():
        out[m] = {"value": round(e["value_num"] / e["value_den"], 4) if e["value_den"] else None,
                  "share": round(e["share"], 4), "visits": e["visits"]}
        if evals is not None:
            out[m]["A"] = round(e["A_num"] / e["value_den"], 2) if e["value_den"] else None
    opp_sorted = {m: round(p, 4) for m, p in sorted(opp.items(), key=lambda kv: -kv[1])}
    if evals is not None:
        return out, round(root, 4), opp_sorted, round(root_abs, 2)
    return out, round(root, 4), opp_sorted


class Evaluator:
    def __init__(self, worlds=4, search_ms=1000, threads=1, processes=None):
        self.worlds, self.search_ms, self.threads = worlds, search_ms, threads
        self.pool = ProcessPoolExecutor(max_workers=processes or worlds)

    def close(self):
        self.pool.shutdown()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def search(self, battle, seed=None):
        t0 = time.time()
        _, sampled = sample_worlds(battle, self.worlds, seed)
        import value as V
        futs, evals = [], []
        for i, (b, chance) in enumerate(sampled):
            st_obj = battle_to_poke_engine_state(b)
            evals.append(V.evaluate(st_obj))
            futs.append((self.pool.submit(get_result_from_mcts, st_obj.to_string(), self.search_ms, i, self.threads),
                         chance))
        results = [(f.result(), c) for f, c in futs]
        options, root, opp, root_abs = aggregate(results, evals)
        return {"options": options, "root_value": root, "root_A": root_abs, "opp_policy": opp,
                "worlds": len(sampled), "seconds": round(time.time() - t0, 3)}

    def evaluate_decision(self, d, seed=None):
        r = self.search(d.battle, seed=seed if seed is not None else _seed_for((d.idx, d.turn, d.k)))
        opts = r["options"]
        best = max(opts, key=lambda m: (opts[m]["share"], opts[m]["value"] or 0)) if opts else None
        best_value = max((o["value"] for o in opts.values() if o["value"] is not None), default=None)
        chosen = d.action
        cost = None
        if chosen is not None and chosen in opts and opts[chosen]["value"] is not None and best is not None:
            cost = round(max(0.0, opts[best]["value"] - opts[chosen]["value"]), 4)
        elif chosen is None and getattr(d, "candidates", None):
            # unrevealed move: the cost is bounded by the candidates' range
            vals = [opts[m]["value"] for m in d.candidates if m in opts and opts[m]["value"] is not None]
            if vals and best is not None:
                r["cost_range"] = [round(max(0.0, opts[best]["value"] - max(vals)), 4),
                                   round(max(0.0, opts[best]["value"] - min(vals)), 4)]
        r.update({"best": best, "best_value_any": best_value, "chosen": chosen, "cost": cost,
                  "chosen_in_options": chosen in opts if chosen else None})
        return r


def farm_noise(decisions_path, games_path, n_games, worlds, ms, processes, out_path, seed=11):
    """Decision cost of the searching bot's own choices (should be ~0): the review's noise floor.

    Uses the logged choice (ground truth) as the chosen action, and contrasts it with the
    expected cost of a uniformly random *other* legal option.
    """
    import gzip
    import reconstruct as R
    from calibrate import load_calibrator
    cal = load_calibrator()
    games = {}
    with gzip.open(games_path, "rt") as f:
        for l in f:
            g = json.loads(l)
            games[g["uid"]] = g
    by = {}
    with gzip.open(decisions_path, "rt") as f:
        for l in f:
            r = json.loads(l)
            if r["side"] == "A":
                by.setdefault(f"{r['run_id']}/g{r['game']:04d}", []).append(r)
    uids = sorted(u for u in by if u in games)
    random.Random(seed).shuffle(uids)
    rows = []
    out = open(out_path, "w")
    with Evaluator(worlds, ms, processes=processes) as ev:
        for u in uids[:n_games]:
            recs = by[u]
            decs, info = R.reconstruct(games[u]["log"], recs[0]["player"], R._team_from_norm_sets(recs[0]["own_team"]),
                                       compute_legal=False)
            truth = {(r["turn"] or 0, r["k"]): r for r in recs}
            for d in decs:
                r = truth.get((d.turn, d.k))
                if r is None:
                    continue
                d.action = r["choice"]
                e = ev.evaluate_decision(d)
                opts = {m: o for m, o in e["options"].items() if o["value"] is not None}
                if d.action not in opts or e["best"] not in opts:
                    continue
                wp = {m: cal(o["value"]) for m, o in opts.items()}
                cost = max(0.0, wp[e["best"]] - wp[d.action])
                others = [max(0.0, wp[e["best"]] - wp[m]) for m in opts if m != d.action]
                row = {"uid": r["uid"], "turn": d.turn, "k": d.k, "kind": d.kind, "chosen": d.action, "best": e["best"],
                       "agree": d.action == e["best"], "cost": round(cost, 4),
                       "random_other_cost": round(sum(others) / len(others), 4) if others else None,
                       "n_options": len(opts), "root_eval": e["root_value"], "root_logged": r["root_value"],
                       "seconds": e["seconds"]}
                rows.append(row)
                out.write(json.dumps(row) + "\n")
                out.flush()
    out.close()
    multi = [x for x in rows if x["n_options"] > 1]
    costs = sorted(x["cost"] for x in multi)
    n = max(1, len(costs))
    rc = [x["random_other_cost"] for x in multi if x["random_other_cost"] is not None]
    re_, rl = [x["root_eval"] for x in rows], [x["root_logged"] for x in rows]
    mx, my = sum(re_) / len(re_), sum(rl) / len(rl)
    cov = sum((a - mx) * (b - my) for a, b in zip(re_, rl))
    corr = cov / ((sum((a - mx) ** 2 for a in re_) * sum((b - my) ** 2 for b in rl)) ** 0.5)
    return {"games": min(n_games, len(uids)), "decisions": len(rows), "decisions_with_choice": len(multi),
            "search": f"{worlds} worlds x {ms} ms", "mean_cost_bot": round(sum(costs) / n, 4),
            "median_cost_bot": costs[n // 2] if costs else None, "p90_cost_bot": costs[int(0.9 * n)] if costs else None,
            "share_cost_gt_0.02": round(sum(c > 0.02 for c in costs) / n, 3),
            "share_cost_gt_0.05": round(sum(c > 0.05 for c in costs) / n, 3),
            "agree_with_review_best": round(sum(x["agree"] for x in multi) / n, 3),
            "mean_cost_random_other_option": round(sum(rc) / max(1, len(rc)), 4),
            "root_value_corr_review_vs_logged": round(corr, 3),
            "mean_abs_root_diff": round(sum(abs(a - b) for a, b in zip(re_, rl)) / len(rows), 4),
            "mean_seconds_per_decision": round(sum(x["seconds"] for x in rows) / len(rows), 2)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("game", help="evaluate every decision of one log")
    g.add_argument("log")
    g.add_argument("--side", required=True, choices=["p1", "p2"])
    g.add_argument("--team", required=True)
    g.add_argument("--worlds", type=int, default=4)
    g.add_argument("--ms", type=int, default=1000)
    g.add_argument("--out", default="-")
    f = sub.add_parser("noise", help="decision cost of the farm bot's own choices (noise floor)")
    f.add_argument("decisions")
    f.add_argument("--games", default=None)
    f.add_argument("--n", type=int, default=10)
    f.add_argument("--worlds", type=int, default=4)
    f.add_argument("--ms", type=int, default=1000)
    f.add_argument("--processes", type=int, default=4)
    f.add_argument("--out", required=True)
    f.add_argument("--summary", default=None)
    args = ap.parse_args()
    import reconstruct as R
    if args.cmd == "noise":
        games = args.games or args.decisions.replace(".jsonl", ".games.jsonl")
        summ = farm_noise(args.decisions, games, args.n, args.worlds, args.ms, args.processes, args.out)
        txt = json.dumps(summ, indent=1)
        print(txt)
        if args.summary:
            Path(args.summary).write_text(txt + "\n")
        return
    lines, _ = R.load_log(args.log)
    decs, info = R.reconstruct(lines, args.side, Path(args.team).read_text(), compute_legal=False)
    out = sys.stdout if args.out == "-" else open(args.out, "w")
    with Evaluator(args.worlds, args.ms) as ev:
        for d in decs:
            r = ev.evaluate_decision(d)
            out.write(json.dumps({"turn": d.turn, "k": d.k, "action": d.action, **r}) + "\n")


if __name__ == "__main__":
    main()
