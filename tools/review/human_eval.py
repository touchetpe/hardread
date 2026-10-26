#!/usr/bin/env python3
"""Review engine on human games (and the same pipeline on farm games for the noise floor).

Per game-side (one process each; Foul Play's datasets are process-global):
  1. Reconstruct the side's decisions from the public replay log. The user's team is DRAFTED from the replay
     (tools/human/reconstruct_check.draft_team, without nicknames): species/forme from team preview, the
     items/abilities/tera/moves that player revealed during the whole game, defaults elsewhere (Leftovers,
     first ability, 85 EVs, Serious). Only the user's own side is drafted; the opponent stays belief-based
     (Foul Play's sampler), exactly as in a real review.
  2. Search every decision with an iteration-based version of the review search (W worlds x N iterations,
     Foul Play's sampler + poke-engine MCTS) and keep per-option absolute values A = eval + logit(v)/K.
  3. Luck v2 records per turn (luck.py, hidden-action mixture, damage-conditioned worlds).

  python tools/review/human_eval.py run --n 600 --workers 12 --out scratch_ds/review/human/run.jsonl
  python tools/review/human_eval.py farm scratch_ds/review/lab.jsonl.gz --n 10 --out .../farm_noise.jsonl
  python tools/review/human_eval.py analyze scratch_ds/review/human/run.jsonl [--farm-noise ...]
"""
import argparse
import gzip
import json
import math
import random
import signal
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARDREAD = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HARDREAD / "tools" / "human"))
HUMAN = HARDREAD / "scratch_ds" / "human"


def search_iters(battle, worlds, iterations, seed):
    """The review search (evaluate.Evaluator.search) with fixed MCTS iterations, in-process."""
    import value as V
    from evaluate import aggregate, sample_worlds
    from fp.search.poke_engine_helpers import battle_to_poke_engine_state
    from poke_engine import monte_carlo_tree_search
    _, sampled = sample_worlds(battle, worlds, seed)
    results, evals = [], []
    for b, chance in sampled:
        st = battle_to_poke_engine_state(b)
        evals.append(V.evaluate(st))
        results.append((monte_carlo_tree_search(st, 0, iterations=iterations), chance))
    options, root, opp, root_abs = aggregate(results, evals)
    best = max(options, key=lambda m: (options[m]["share"], options[m]["value"] or 0)) if options else None
    return {"options": {m: {"A": o.get("A"), "share": o["share"]} for m, o in options.items()},
            "root_A": root_abs, "best": best, "opp_policy": opp}


class Timeout(Exception):
    pass


def _alarm(sig, frm):
    raise Timeout()


def job_run(job):
    import fpenv
    fpenv.configure()
    import contextlib
    import io
    import reconstruct as R
    from evaluate import _seed_for
    from luck import LuckMeter
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(job.get("timeout", 900))
    t0 = time.time()
    out = {k: job[k] for k in ("uid", "side", "outcome", "meta")}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            decs, info = R.reconstruct(job["log"], job["side"], job["team"], compute_legal=False)
            truth = job.get("truth")  # farm noise mode: the bot's logged choices
            evals, rows = {}, []
            t1 = time.time()
            for d in decs:
                e = search_iters(d.battle, job["worlds"], job["iterations"], _seed_for(("he", d.idx, d.turn, d.k)))
                evals[d.idx] = {"options": {m: {"share": o["share"]} for m, o in e["options"].items()},
                                "opp_policy": e["opp_policy"]}
                act = d.action
                if truth is not None:
                    act = truth.get(f"{d.turn}:{d.k}", act)
                rows.append({"turn": d.turn, "k": d.k, "kind": d.kind, "action": act, "source": d.action_source,
                             "root_A": e["root_A"], "best": e["best"], "options": e["options"]})
            t_eval = time.time() - t1
            t2 = time.time()
            lm = LuckMeter(worlds=job["luck_worlds"], iterations=job["luck_iterations"], processes=0, hidden_mix=True)
            luck = lm.game_records(decs, info, evals) if job.get("luck", True) else []
        out.update({"decisions": rows, "luck": luck, "own_hp_mode": info["own_hp_mode"],
                    "seconds": {"reconstruct": round(t1 - t0, 1), "eval": round(t_eval, 1),
                                "luck": round(time.time() - t2, 1)}})
    except Timeout:
        out["error"] = "timeout"
    except BaseException as e:  # noqa: BLE001
        import traceback
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        out["trace"] = traceback.format_exc()[-1500:]
    finally:
        signal.alarm(0)
    return out


def draft_team_smogon(sets_rows, profiles, gender_by_species):
    """User-side draft: what the player revealed in the replay (item, ability, tera, moves), and for everything
    else the most common Smogon set (Foul Play's gen9ou full-set cache) consistent with those reveals:
    nature, EVs, unrevealed item/ability/tera, remaining moves. Falls back to the default draft per Pokemon."""
    import re
    import reconstruct_check as C
    full = _full_sets()
    out = []
    for r in sets_rows:
        sp = r["species_preview"]
        key = re.sub(r"[^a-z0-9]", "", sp.lower())
        cands = full.get(key) or full.get(re.sub(r"[^a-z0-9]", "", sp.split("-")[0].lower())) or {}
        rev_moves = {re.sub(r"[^a-z0-9]", "", m.lower()) for m in r["moves"]}
        nz = lambda x: re.sub(r"[^a-z0-9]", "", (x or "").lower())  # noqa: E731
        best, best_score = None, -1.0
        for k, cnt in cands.items():
            tera, abil, item, nature, evs, *mv = k.split("|")
            sc = cnt * (1 + 2 * len(rev_moves & set(mv)))
            if r["item"] and nz(r["item"]) != item:
                sc *= 0.2
            if r["ability"] and nz(r["ability"]) != abil:
                sc *= 0.3
            if r["tera_type"] and nz(r["tera_type"]) != tera:
                sc *= 0.5
            if sc > best_score:
                best, best_score = k, sc
        if best is None:
            out.append(C.draft_team([r], profiles, gender_by_species).strip())
            continue
        tera, abil, item, nature, evs, *mv = best.split("|")
        moves = list(r["moves"])
        for m in mv:
            if len(moves) >= 4:
                break
            if m not in {nz(x) for x in moves}:
                moves.append(m)
        ev = [int(x) for x in evs.split(",")]
        g = gender_by_species.get(sp, "")
        head = sp + (f" ({g})" if g else "") + f" @ {r['item'] or item}"
        lines = [head, f"Ability: {r['ability'] or abil}", f"Tera Type: {(r['tera_type'] or tera).capitalize()}",
                 "EVs: " + " / ".join(f"{v} {lbl}" for v, lbl in zip(ev, ("HP", "Atk", "Def", "SpA", "SpD", "Spe"))),
                 f"{nature.capitalize()} Nature"] + [f"- {m}" for m in moves[:4]]
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


_FULL = None


def _full_sets():
    global _FULL
    if _FULL is None:
        p = HARDREAD / "foul-play/fp/data/pkmn_sets_cache/gen9ou/pokemon_full_sets.json"
        _FULL = json.loads(p.read_text()) if p.exists() else {}
    return _FULL


def human_jobs(n, seed, worlds, iterations, luck_worlds, luck_iterations, min_turns=3, draft="default"):
    import reconstruct_check as C
    games = [g for g in C.load_jsonl(HUMAN / "games.jsonl.gz") if g["turns"] >= min_turns and g.get("winner")]
    random.Random(seed).shuffle(games)
    games = games[:n]
    ids = {g["uid"] for g in games}
    sets = defaultdict(list)
    for r in C.load_jsonl(HUMAN / "sets.jsonl.gz"):
        if r["uid"] in ids:
            sets[(r["uid"], r["player"])].append(r)
    profiles = json.loads((HUMAN / "species_profiles.json").read_text())
    jobs = []
    for g in games:
        genders = {}
        for l in g["log"]:
            p = l.split("|")
            if len(p) > 3 and p[1] == "poke":
                sp = p[3].split(",")[0].strip()
                gd = p[3].split(",")[1].strip() if "," in p[3] else ""
                genders[(p[2], sp)] = gd if gd in ("M", "F") else ""
        for side in ("p1", "p2"):
            gmap = {sp: gd for (s, sp), gd in genders.items() if s == side}
            if draft == "smogon":
                team = draft_team_smogon(sets[(g["uid"], side)], profiles, gmap)
            else:
                team = C.draft_team(sets[(g["uid"], side)], profiles, gmap, None)  # no nicknames
            won = int(g["winner_player"] == side) if g.get("winner_player") else int((g["winner"] == "A") == (side == "p1"))
            r = g.get("ratings") or {}
            jobs.append({"uid": g["uid"], "side": side, "log": g["log"], "team": team, "outcome": won,
                         "meta": {"end_reason": g.get("end_reason"), "turns": g["turns"],
                                  "rating": (r.get("pre") or r.get("pre_game") or r).get(side) if isinstance(r, dict) else None,
                                  "ratings_raw": r, "top50": g.get("top50"),
                                  "chance_net": (g.get("chance_net") or {}).get(side)},
                         "worlds": worlds, "iterations": iterations, "luck_worlds": luck_worlds,
                         "luck_iterations": luck_iterations})
    return jobs


def farm_jobs(decisions_path, n, seed, worlds, iterations):
    import reconstruct as R
    games = {}
    with gzip.open(decisions_path.replace(".jsonl", ".games.jsonl"), "rt") as f:
        for l in f:
            g = json.loads(l)
            games[g["uid"]] = g
    by = defaultdict(list)
    with gzip.open(decisions_path, "rt") as f:
        for l in f:
            r = json.loads(l)
            by[(f"{r['run_id']}/g{r['game']:04d}", r["side"])].append(r)
    uids = sorted({u for u, _ in by})
    random.Random(seed).shuffle(uids)
    jobs = []
    for u in uids[:n]:
        for s in ("A", "B"):
            recs = by.get((u, s))
            if not recs:
                continue
            jobs.append({"uid": u, "side": recs[0]["player"], "log": games[u]["log"],
                         "team": R.sets_to_export(R.normalize_sets(R._team_from_norm_sets(recs[0]["own_team"]))),
                         "outcome": recs[0]["outcome"], "meta": {"farm_side": s},
                         "truth": {f"{r['turn'] or 0}:{r['k']}": r["choice"] for r in recs},
                         "worlds": worlds, "iterations": iterations, "luck": False,
                         "luck_worlds": 4, "luck_iterations": 10000})
    return jobs


def run_jobs(jobs, workers, out_path):
    from multiprocessing import get_context
    done = set()
    if Path(out_path).exists():
        for l in open(out_path):
            r = json.loads(l)
            if "error" not in r:
                done.add((r["uid"], r["side"]))
    jobs = [j for j in jobs if (j["uid"], j["side"]) not in done]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with get_context("spawn").Pool(workers, maxtasksperchild=4) as pool, open(out_path, "a") as f:
        for r in pool.imap_unordered(job_run, jobs, chunksize=1):
            f.write(json.dumps(r) + "\n")
            f.flush()


# ---------------------------------------------------------------- analysis

TURN_BUCKETS = [(1, 5, "1-5"), (6, 15, "6-15"), (16, 30, "16-30"), (31, 10 ** 6, "31+")]


def _metrics(p, y, bins=10):
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
    rel = [(f"{k / bins:.1f}", t[0], round(t[1] / t[0], 3), round(t[2] / t[0], 3)) for k, t in sorted(tab.items())]
    return {"n": n, "brier": round(brier, 4), "log_loss": round(ll, 4), "ece": round(ece, 4), "reliability": rel}


def _bucket(t):
    for lo, hi, name in TURN_BUCKETS:
        if lo <= t <= hi:
            return name
    return None


def calib_report(rows, cal_fn):
    """rows: (game, turn, A, y). Per-decision P = cal(A)."""
    p = [cal_fn(a) for _, _, a, _ in rows]
    y = [r[3] for r in rows]
    out = {"all": _metrics(p, y)}
    for _, _, name in TURN_BUCKETS:
        idx = [i for i, r in enumerate(rows) if _bucket(r[1]) == name]
        m = _metrics([p[i] for i in idx], [y[i] for i in idx])
        m.pop("reliability", None)
        out[name] = m
    return out


def analyze(paths, farm_noise=None):
    import hashlib
    import luck as L
    rows = [json.loads(l) for p in paths for l in open(p)]
    ok = [r for r in rows if "error" not in r]
    res = {"game_sides": len(rows), "ok": len(ok), "errors": len(rows) - len(ok),
           "games": len({r["uid"] for r in ok}),
           "errors_by_type": dict(sorted(defaultdict(int, {}).items()))}
    errs = defaultdict(int)
    for r in rows:
        if "error" in r:
            errs[r["error"][:60]] += 1
    res["errors_by_type"] = dict(errs)
    farm_cal = L.load_abs_calibrator()
    # ---- calibration on turn-start decisions (k = 0, turn >= 1), root_A from the review search
    def dec_rows(sub):
        out = []
        for r in sub:
            for d in r["decisions"]:
                if d["kind"] == "move" and d["k"] == 0 and d["turn"] >= 1 and d["root_A"] is not None:
                    out.append((r["uid"], d["turn"], d["root_A"], r["outcome"]))
        return out
    def luck_dec_rows(sub):
        """Decision values from the luck run (same valuation as the farm calibrator's fit: eval + 10k-iteration
        MCTS correction, unconditioned first-K worlds), turn starts only."""
        out = []
        for r in sub:
            for t in r.get("luck") or []:
                ws = [w for w in t.get("worlds") or [] if w["draw"] < t["K"]]
                if t["k"] == 0 and t["turn"] >= 1 and ws:
                    out.append((r["uid"], t["turn"], sum(w["A_dec"] for w in ws) / len(ws), r["outcome"]))
        return out
    subsets = {"all_games (forfeits counted)": ok,
               "ko_only": [r for r in ok if (r["meta"].get("end_reason") == "ko")],
               "forfeit_or_timeout": [r for r in ok if r["meta"].get("end_reason") in ("forfeit", "timeout")]}
    res["calibration_farm_calibrator"] = {}
    res["calibration_review_search_values"] = {}
    for name, sub in subsets.items():
        dr = luck_dec_rows(sub)
        res["calibration_farm_calibrator"][name] = dict(calib_report(dr, farm_cal), games=len({x[0] for x in dr}))
        dr2 = dec_rows(sub)
        res["calibration_review_search_values"][name] = dict(calib_report(dr2, farm_cal), games=len({x[0] for x in dr2}))
    # human-specific calibrator: fit on half the games (by hash), test on the other half
    half = lambda g: int(hashlib.sha256(g.encode()).hexdigest()[:8], 16) % 2  # noqa: E731
    dr = luck_dec_rows(ok)
    fit = [x for x in dr if half(x[0]) == 0]
    test = [x for x in dr if half(x[0]) == 1]
    hc = L.AbsCal().fit([x[2] for x in fit], [x[3] for x in fit])
    res["human_calibrator"] = {"a": round(hc.a, 4), "b": round(hc.b, 4), "fit_games": len({x[0] for x in fit}),
                               "test_games": len({x[0] for x in test}),
                               "test_with_human_calibrator": calib_report(test, hc),
                               "test_with_farm_calibrator": calib_report(test, farm_cal)}
    # ---- luck on human games (P from the farm calibrator on the luck run's own A values)
    per = []
    turn_l = []
    for r in ok:
        if not r.get("luck"):
            continue
        ls = [L.turn_luck(t, farm_cal, "branch_cond") for t in r["luck"]]
        turn_l.append(ls)
        per.append({"game": r["uid"], "side": r["side"], "y": r["outcome"], "L": sum(ls),
                    "end": r["meta"].get("end_reason"), "chance_net": r["meta"].get("chance_net"),
                    "rating": r["meta"].get("rating")})
    def luck_block(sub):
        if len(sub) < 20:
            return {"n": len(sub)}
        vr = lambda s: L._var([x["y"] - x["L"] for x in s]) / L._var([x["y"] for x in s])  # noqa: E731
        cr = lambda s: L._corr([x["L"] for x in s], [x["y"] for x in s])  # noqa: E731
        return {"n": len(sub), "var_ratio": round(vr(sub), 4), "var_ratio_ci": L._boot(sub, vr),
                "corr_luck_outcome": round(cr(sub), 4), "corr_ci": L._boot(sub, cr),
                "sd_total_luck": round(L._var([x["L"] for x in sub]) ** 0.5, 4)}
    p1 = [x for x in per if x["side"] == "p1"]
    res["luck"] = {"p1_all": luck_block(p1), "p1_ko_only": luck_block([x for x in p1 if x["end"] == "ko"]),
                   "both_seats_all": luck_block(per)}
    a = {x["game"]: x["L"] for x in per if x["side"] == "p1"}
    b = {x["game"]: x["L"] for x in per if x["side"] == "p2"}
    both = [(a[k], b[k]) for k in a if k in b]
    mc = lambda s: L._corr([x[0] for x in s], [x[1] for x in s])  # noqa: E731
    res["luck"]["mirror"] = {"n": len(both), "corr_L1_L2": round(mc(both), 4), "corr_ci": L._boot(both, mc),
                             "mean_abs_sum": round(sum(abs(x + y) for x, y in both) / max(1, len(both)), 4)}
    flat = [v for ls in turn_l for v in ls]
    mb = lambda s: sum(sum(ls) for ls in s) / sum(len(ls) for ls in s)  # noqa: E731
    res["luck"]["mean_luck_per_decision"] = round(sum(flat) / max(1, len(flat)), 5)
    res["luck"]["mean_luck_per_decision_ci"] = L._boot([ls for ls in turn_l if ls], mb)
    # symmetrised luck for p1's outcome
    ya = {x["game"]: x["y"] for x in per if x["side"] == "p1"}
    sym = [{"y": ya[k], "L": (a[k] - b[k]) / 2} for k in a if k in b]
    res["luck"]["symmetrised_p1"] = luck_block(sym)
    # event-count baseline on the same games
    cn = [x for x in p1 if x["chance_net"] is not None]
    cc = lambda s: L._corr([x["chance_net"] for x in s], [x["y"] for x in s])  # noqa: E731
    res["luck"]["event_count_baseline_p1"] = {"n": len(cn), "corr": round(cc(cn), 4), "corr_ci": L._boot(cn, cc)}
    # ---- decision cost
    def costs(sub, cal):
        out = []
        for r in sub:
            for d in r["decisions"]:
                o = d["options"]
                if not d["action"] or d["action"] not in o or not d["best"] or len(o) < 2:
                    continue
                if o[d["action"]]["A"] is None or o[d["best"]]["A"] is None:
                    continue
                out.append({"uid": r["uid"], "side": r["side"], "cost": max(0.0, cal(o[d["best"]]["A"]) - cal(o[d["action"]]["A"])),
                            "agree": d["action"] == d["best"], "kind": d["kind"], "rating": r["meta"].get("rating"),
                            "top": _is_top(r)})
        return out
    def dist(c):
        if not c:
            return {"n": 0}
        v = sorted(x["cost"] for x in c)
        n = len(v)
        return {"n": n, "mean": round(sum(v) / n, 4), "median": round(v[n // 2], 4), "p75": round(v[int(.75 * n)], 4),
                "p90": round(v[int(.9 * n)], 4), "share_gt_0.02": round(sum(x > 0.02 for x in v) / n, 3),
                "share_gt_0.05": round(sum(x > 0.05 for x in v) / n, 3),
                "agree_with_engine": round(sum(x["agree"] for x in c) / n, 3)}
    hc_cost = costs(ok, farm_cal)
    res["decision_cost"] = {"all_human": dist(hc_cost),
                            "top50_players": dist([x for x in hc_cost if x["top"] is True]),
                            "their_opponents": dist([x for x in hc_cost if x["top"] is False])}
    # per player-game mean cost vs rating
    pg = defaultdict(list)
    for x in hc_cost:
        pg[(x["uid"], x["side"])].append(x["cost"])
    rating = {(r["uid"], r["side"]): r["meta"].get("rating") for r in ok}
    pts = [(sum(v) / len(v), rating[k]) for k, v in pg.items() if rating.get(k) and len(v) >= 5]
    if len(pts) > 20:
        res["decision_cost"]["corr_mean_cost_vs_rating"] = round(L._corr([p[0] for p in pts], [p[1] for p in pts]), 4)
        res["decision_cost"]["corr_ci"] = L._boot(pts, lambda s: L._corr([p[0] for p in s], [p[1] for p in s]))
        res["decision_cost"]["n_player_games"] = len(pts)
    # within-game: does the lower-cost player have the higher rating / win?
    pairs = []
    for uid in {k[0] for k in pg}:
        if (uid, "p1") in pg and (uid, "p2") in pg and len(pg[(uid, "p1")]) >= 5 and len(pg[(uid, "p2")]) >= 5:
            c1 = sum(pg[(uid, "p1")]) / len(pg[(uid, "p1")])
            c2 = sum(pg[(uid, "p2")]) / len(pg[(uid, "p2")])
            r1, r2 = rating.get((uid, "p1")), rating.get((uid, "p2"))
            y1 = next(r["outcome"] for r in ok if r["uid"] == uid and r["side"] == "p1")
            pairs.append((c1, c2, r1, r2, y1))
    if pairs:
        rr = [p for p in pairs if p[2] and p[3] and p[2] != p[3] and p[0] != p[1]]
        res["decision_cost"]["within_game_lower_cost_is_higher_rated"] = {
            "n": len(rr), "share": round(sum((p[0] < p[1]) == (p[2] > p[3]) for p in rr) / max(1, len(rr)), 3)}
        ww = [p for p in pairs if p[0] != p[1]]
        res["decision_cost"]["within_game_lower_cost_wins"] = {
            "n": len(ww), "share": round(sum((p[0] < p[1]) == (p[4] == 1) for p in ww) / max(1, len(ww)), 3)}
    # ---- self-play reference: the same calibration report on farm luck records (farm calibrator, 2 params)
    farm_luck = HARDREAD / "scratch_ds/review/luck2/lab_w4_i10k_mix250.jsonl"
    if farm_luck.exists():
        fl = [json.loads(l) for l in open(farm_luck)]
        fl = [r for r in fl if "error" not in r]
        fr = []
        for r in fl:
            for t in r["turns"]:
                ws = [w for w in t.get("worlds") or [] if w["draw"] < t["K"]]
                if t["k"] == 0 and t["turn"] >= 1 and ws:
                    fr.append((r["game"], t["turn"], sum(w["A_dec"] for w in ws) / len(ws), r["outcome"]))
        res["self_play_reference"] = dict(calib_report(fr, farm_cal), games=len({x[0] for x in fr}),
                                          note="in-sample for the 2-parameter calibrator (out-of-fold Brier 0.154)")
    # ---- noise floor (farm, bot's own choices, same search settings)
    if farm_noise:
        fr = [json.loads(l) for p in farm_noise for l in open(p)]
        fr = [r for r in fr if "error" not in r]
        res["noise_floor_farm_bot"] = dist(costs(fr, farm_cal))
    return res


def _is_top(r):
    t = r["meta"].get("top50")
    if isinstance(t, dict):
        return bool(t.get(r["side"]))
    if t == "both":
        return True
    if t in ("p1", "p2"):
        return t == r["side"]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--n", type=int, default=600)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--workers", type=int, default=12)
    r.add_argument("--worlds", type=int, default=4)
    r.add_argument("--iterations", type=int, default=20000)
    r.add_argument("--luck-worlds", type=int, default=4)
    r.add_argument("--luck-iterations", type=int, default=10000)
    r.add_argument("--out", required=True)
    r.add_argument("--draft", default="default", choices=["default", "smogon"])
    f = sub.add_parser("farm")
    f.add_argument("decisions")
    f.add_argument("--n", type=int, default=10)
    f.add_argument("--seed", type=int, default=3)
    f.add_argument("--workers", type=int, default=8)
    f.add_argument("--worlds", type=int, default=4)
    f.add_argument("--iterations", type=int, default=20000)
    f.add_argument("--out", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("runs", nargs="+")
    a.add_argument("--farm-noise", nargs="*", default=None)
    a.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.cmd == "run":
        import fpenv
        fpenv.configure()
        run_jobs(human_jobs(args.n, args.seed, args.worlds, args.iterations, args.luck_worlds, args.luck_iterations,
                            draft=args.draft), args.workers, args.out)
    elif args.cmd == "farm":
        import fpenv
        fpenv.configure()
        run_jobs(farm_jobs(args.decisions, args.n, args.seed, args.worlds, args.iterations), args.workers, args.out)
    else:
        import fpenv
        fpenv.configure()
        res = analyze(args.runs, args.farm_noise)
        txt = json.dumps(res, indent=1)
        print(txt)
        if args.out:
            Path(args.out).write_text(txt + "\n")


if __name__ == "__main__":
    main()
