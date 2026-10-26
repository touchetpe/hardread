#!/usr/bin/env python3
"""Chance-only luck per turn ("hax meter"), AIVAT-style -- v1, SUPERSEDED by luck.py.

v1 valued each chance branch with an MCTS rooted at the branch. poke-engine scores MCTS leaves relative
to the root's own static eval, so those values were not comparable across branches, and v1's summed luck
did not predict outcomes (r = 0.02). luck.py (v2) values branches on an absolute scale (value.py).
The helpers below (branch features, realised-branch selection) are reused by luck.py.

For each turn N where the user chose an action at the start of the turn:
  1. Take the reconstructed decision state (the user's view at the start of turn N).
  2. Sample K worlds for the opponent's hidden information with Foul Play's own sampler
     (mode.prepare_battles). The luck is therefore *belief-averaged*: it is computed in
     the worlds the user could not tell apart, not in the true world.
  3. In each world, enumerate every chance branch of the actual action pair with
     poke-engine's generate_instructions (crits, accuracy, secondary effects, speed ties,
     and the damage-roll split where a roll decides a KO; poke-engine averages other rolls).
  4. Value each branch's end-of-turn state with a short MCTS (fixed iterations) and map
     the value to a win probability with the calibrator (calibrate.py).
  5. Identify the realised branch from the log: the branch whose resulting public state
     (HP fractions, faints, statuses, active boosts of every Pokemon seen in both) is
     closest to the reconstructed state at the next decision point; ties are pooled.
  6. luck_N = mean over worlds of [ WP(realised) - sum_b p_b * WP(b) ].

Sum of luck_N over turns = the chance-only correction term (zero-mean under the model by
construction). Decision quality is measured separately (evaluate.py).
"""
import json
import math
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpenv  # noqa: E402

fpenv.configure()

import poke_engine as pe  # noqa: E402
from fp.search.poke_engine_helpers import battle_to_poke_engine_state  # noqa: E402

from calibrate import load_calibrator  # noqa: E402
from evaluate import _seed_for, sample_worlds  # noqa: E402


def value_state(state_str, iterations):
    """Short MCTS root value (side one) of a poke-engine state string."""
    st = pe.State.from_string(state_str)
    res = pe.monte_carlo_tree_search(st, 0, iterations=iterations)
    visits = sum(o.visits for o in res.side_one) or 1
    return sum(o.total_score for o in res.side_one) / visits


def engine_move(action, battle_side_active=None):
    """Foul Play choice string -> poke-engine move string."""
    if action is None:
        return None
    if action.startswith("switch "):
        return action[len("switch "):]
    return action


def _feat_engine(state):
    out = {}
    for sd, side in (("u", state.side_one), ("o", state.side_two)):
        act = int(str(side.active_index))
        boosts = (side.attack_boost, side.defense_boost, side.special_attack_boost, side.special_defense_boost,
                  side.speed_boost)
        for i, p in enumerate(side.pokemon):
            if str(p.id).lower() in ("none", "pikachu") and p.hp == 0:
                continue
            frac = p.hp / p.maxhp if p.maxhp else 0.0
            st = str(p.status).lower()
            out[(sd, str(p.id).lower())] = (frac, "none" if st in ("none", "") else st, i == act,
                                    boosts if i == act else None)
    return out


STATUS_MAP = {"psn": "poison", "tox": "toxic", "brn": "burn", "par": "paralyze", "slp": "sleep", "frz": "freeze"}


def _feat_battle(battle):
    out = {}
    for sd, side in (("u", battle.user), ("o", battle.opponent)):
        mons = ([side.active] if side.active else []) + list(side.reserve)
        for p in mons:
            if p is None:
                continue
            frac = (p.hp / p.max_hp) if p.max_hp else 0.0
            st = STATUS_MAP.get(p.status, "none") if p.status else "none"
            b = None
            if p is side.active:
                bb = p.boosts
                b = (bb["attack"], bb["defense"], bb["special-attack"], bb["special-defense"], bb["speed"])
            out[(sd, p.name)] = (frac, st, p is side.active, b)
    return out


def turn_events(segment, side):
    """Explicit chance events in a turn's log lines, by the side that suffered them ('u' user, 'o' opponent)."""
    ev = {"crit": {"u": 0, "o": 0}, "miss_by": {"u": 0, "o": 0}}
    for l in segment:
        p = l.split("|")
        if len(p) < 3:
            continue
        who = "u" if p[2].startswith(side) else "o"
        if p[1] == "-crit":
            ev["crit"][who] += 1
        elif p[1] == "-miss":
            ev["miss_by"][who] += 1
    return ev


def _damage_to(state_before, state_after):
    out = {}
    for sd, b, a in (("u", state_before.side_one, state_after.side_one), ("o", state_before.side_two, state_after.side_two)):
        tot = 0.0
        for pb, pa in zip(b.pokemon, a.pokemon):
            if pb.maxhp:
                tot += (pb.hp - pa.hp) / pb.maxhp
        out[sd] = round(tot, 4)
    return out


def _discrete(fe):
    return {k: (frac <= 0, st, b) for k, (frac, st, act, b) in fe.items()}


def select_realised(rows, fo, events, tie_tol):
    """Pick the realised branch(es): discrete outcome first (faints, statuses, boosts), then the
    log's crit flags (a crit is the larger-damage twin of an otherwise identical branch), then HP."""
    dfo = _discrete(fo)

    def ddist(r):
        d = 0.0
        for k, (fnt, st, b) in _discrete(r["fe"]).items():
            o = dfo.get(k)
            if o is None:
                continue
            d += 1.0 * (fnt != o[0]) + 0.5 * (st != o[1] and not o[0]) + \
                (0.1 * sum(abs(x - y) for x, y in zip(b, o[2])) if b is not None and o[2] is not None else 0.0)
        return d
    for r in rows:
        r["ddist"] = ddist(r)
    dmin = min(r["ddist"] for r in rows)
    cand = [r for r in rows if r["ddist"] <= dmin + 1e-9]
    for sd in ("u", "o"):
        levels = sorted({r["dmg"][sd] for r in cand})
        if len(levels) < 2:
            continue
        top = [r for r in cand if r["dmg"][sd] >= levels[-1] - 1e-4]
        if events["crit"][sd]:
            cand = top
        else:
            mass_top = sum(r["p"] for r in top)
            if mass_top < 0.5 * sum(r["p"] for r in cand):
                cand = [r for r in cand if r not in top]
    hmin = min(r["dist"] for r in cand)
    return [r for r in cand if r["dist"] <= hmin + tie_tol], dmin


def distance(fe, fo):
    d = 0.0
    for k, (frac, st, act, b) in fe.items():
        o = fo.get(k)
        if o is None:
            continue
        ofrac, ost, oact, ob = o
        d += abs(frac - ofrac)
        if (frac <= 0) != (ofrac <= 0):
            d += 1.0
        if st != ost and ofrac > 0:
            d += 0.5
        if b is not None and ob is not None:
            d += 0.1 * sum(abs(x - y) for x, y in zip(b, ob))
    return d


def condition_on_revealed_move(pkmn, move):
    """The opponent revealed `move` this turn: make the sampled set contain it (replace an unrevealed slot)."""
    from fp.data import all_move_json
    if pkmn is None or move is None:
        return
    mv = move.removesuffix("-tera")
    if mv not in all_move_json or pkmn.get_move(mv) is not None:
        return
    if len(pkmn.moves) >= 4:
        pkmn.moves.pop(-1)
    pkmn.add_move(mv)


def describe(instr):
    """Short label of what chance did in a branch (for display)."""
    txt = " ".join(str(i) for i in instr.instruction_list)
    return txt


class HaxMeter:
    def __init__(self, worlds=4, iterations=20000, processes=4, calibrator=None, tie_tol=0.02):
        self.worlds, self.iterations, self.tie_tol = worlds, iterations, tie_tol
        self.cal = calibrator or load_calibrator()
        self.side = None
        self.pool = ProcessPoolExecutor(max_workers=processes) if processes else _InlinePool()

    def close(self):
        self.pool.shutdown()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def turn_luck(self, d, observed_battle, user_action=None, opp_action=None, opp_policy=None, segment=None):
        """Luck of the chance events of decision d's turn, from the user's side.

        Hidden actions: the user's unrevealed move falls back to `user_action` (e.g. the
        evaluator's preferred candidate) or the first candidate; the opponent's hidden
        action falls back to the opponent's policy at our search root (opp_policy), tried in
        order until one is valid in the sampled world, else 'none'.
        """
        t0 = time.time()
        from reconstruct import guess_unrevealed_move
        u = d.action or user_action or ((d.candidates or [None])[0]) or guess_unrevealed_move(d.battle)
        s1 = engine_move(u)
        opp_known = d.opp_action is not None or opp_action is not None
        s2_list = [engine_move(opp_action or d.opp_action)] if opp_known else \
            [engine_move(m) for m in (opp_policy or {})] + ["none"]
        if s1 is None:
            return {"turn": d.turn, "skipped": "user action unknown"}
        _, sampled = sample_worlds(d.battle, self.worlds, seed=_seed_for(("hax", d.idx, d.turn)))
        fo = _feat_battle(observed_battle)
        jobs = []
        per_world = []
        for wi, (b, chance) in enumerate(sampled):
            branches, err = None, None
            if opp_known:
                condition_on_revealed_move(b.opponent.active, s2_list[0])
            st = battle_to_poke_engine_state(b)
            for s2 in s2_list:
                try:
                    branches = pe.generate_instructions(st, s1, s2)
                    break
                except Exception as e:  # noqa: BLE001  (the move is not valid in this world)
                    err = repr(e)[:120]
            if branches is None:
                per_world.append({"error": err})
                continue
            base = st.to_string()
            rows = []
            for bi, br in enumerate(branches):
                s_after = pe.State.from_string(base).apply_instructions(br)
                fe = _feat_engine(s_after)
                fut = self.pool.submit(value_state, s_after.to_string(), self.iterations)
                rows.append({"p": br.percentage / 100.0, "dist": distance(fe, fo), "fe": fe,
                             "dmg": _damage_to(st, s_after), "fut": fut})
            per_world.append({"rows": rows, "chance": chance})
        events = turn_events(segment or [], self.side or "p1")
        lucks, n_branches, realised_p = [], 0, []
        for w in per_world:
            if "rows" not in w or not w["rows"]:
                continue
            rows = w["rows"]
            for r in rows:
                r["v"] = r.pop("fut").result()
                r["wp"] = self.cal(r["v"])
            real, dmin = select_realised(rows, fo, events, self.tie_tol)
            p_real = sum(r["p"] for r in real) or 1e-9
            wp_real = sum(r["p"] * r["wp"] for r in real) / p_real
            wp_exp = sum(r["p"] * r["wp"] for r in rows) / (sum(r["p"] for r in rows) or 1)
            lucks.append(wp_real - wp_exp)
            n_branches += len(rows)
            realised_p.append(p_real)
            w["match_dist"] = dmin + min(r["dist"] for r in real)
        if not lucks:
            return {"turn": d.turn, "skipped": "no world could replay the actions",
                    "errors": [w.get("error") for w in per_world if "error" in w][:2]}
        return {"turn": d.turn, "k": d.k, "luck": round(sum(lucks) / len(lucks), 4),
                "user_action": u, "user_action_known": d.action is not None,
                "opp_action": opp_action or d.opp_action, "opp_action_known": opp_known,
                "luck_by_world": [round(x, 4) for x in lucks], "branches": n_branches,
                "realised_prob": round(sum(realised_p) / len(realised_p), 4),
                "match_dist": round(sum(w.get("match_dist", 0) for w in per_world if "rows" in w) / len(lucks), 4),
                "worlds_used": len(lucks), "seconds": round(time.time() - t0, 3)}

    def game_luck(self, decisions, info, evals=None):
        """Per-turn luck for every turn-start decision of a reconstructed game.

        evals: optional {decision idx: evaluate.py result} for the hidden-action fallbacks.
        """
        out = []
        self.side = info["side"]
        lines = info.get("lines") or []
        for i, d in enumerate(decisions):
            if d.kind != "move":
                continue
            nxt = decisions[i + 1].battle if i + 1 < len(decisions) else info["final_battle"]
            end = decisions[i + 1].log_index if i + 1 < len(decisions) else len(lines)
            seg = lines[d.log_index:end]
            ev = (evals or {}).get(d.idx) or {}
            ua = None
            if d.action is None and ev.get("options") and d.candidates:
                ua = max(d.candidates, key=lambda m: (ev["options"].get(m) or {}).get("share", 0))
            res = self.turn_luck(d, nxt, user_action=ua, opp_policy=ev.get("opp_policy"), segment=seg)
            res["events"] = turn_events(seg, self.side)
            out.append(res)
        return out


# ---------------------------------------------------------------- farm sanity check

def _sanity_game(job):
    """Reconstruct one farm game from side A's view and sum its luck (runs in a worker)."""
    import reconstruct as R
    g, recs, worlds, iterations = job
    player = recs[0]["player"]
    try:
        decs, info = R.reconstruct(g["log"], player, R._team_from_norm_sets(recs[0]["own_team"]), compute_legal=False)
        hm = HaxMeter(worlds=worlds, iterations=iterations, processes=0)  # in-process: one worker per game
        turns = hm.game_luck(decs, info)
    except Exception as e:  # noqa: BLE001
        import traceback
        return {"game": g["uid"], "error": repr(e), "trace": traceback.format_exc()[-1500:]}
    cal = load_calibrator()
    first = next((r for r in recs if (r["turn"] or 0) >= 1), recs[0])
    return {"game": g["uid"], "side": recs[0]["side"], "outcome": recs[0]["outcome"],
            "v0_raw": first["root_value"], "v0": cal(first["root_value"]),
            "v_preview": cal(recs[0]["root_value"]),
            "total_luck": sum(t.get("luck", 0.0) for t in turns), "turns": turns,
            "skipped": sum(1 for t in turns if "skipped" in t), "n_turns": len(turns),
            "seconds": sum(t.get("seconds", 0) for t in turns)}


class _Done:
    def __init__(self, v):
        self.v = v

    def result(self):
        return self.v


class _InlinePool:
    def submit(self, fn, *a):
        return _Done(fn(*a))

    def shutdown(self):
        pass


def _r2(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    r = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0
    return r, r * r


def _ols2(x1, x2, y):
    """R^2 of y ~ 1 + x1 + x2 (pure Python normal equations)."""
    n = len(y)
    m1, m2, my = sum(x1) / n, sum(x2) / n, sum(y) / n
    a = [v - m1 for v in x1]
    b = [v - m2 for v in x2]
    c = [v - my for v in y]
    s11 = sum(v * v for v in a)
    s22 = sum(v * v for v in b)
    s12 = sum(u * v for u, v in zip(a, b))
    s1y = sum(u * v for u, v in zip(a, c))
    s2y = sum(u * v for u, v in zip(b, c))
    det = s11 * s22 - s12 * s12
    if det <= 0:
        return 0.0, (0.0, 0.0)
    b1 = (s22 * s1y - s12 * s2y) / det
    b2 = (s11 * s2y - s12 * s1y) / det
    ss_res = sum((ci - b1 * ai - b2 * bi) ** 2 for ai, bi, ci in zip(a, b, c))
    ss_tot = sum(ci * ci for ci in c)
    return 1 - ss_res / ss_tot, (b1, b2)


def sanity(decisions_path, games_path, n_games, workers, worlds, iterations, out_path, side="A"):
    import gzip
    from collections import defaultdict
    games = {}
    with gzip.open(games_path, "rt") as f:
        for l in f:
            g = json.loads(l)
            games[g["uid"]] = g
    by = defaultdict(list)
    with gzip.open(decisions_path, "rt") as f:
        for l in f:
            r = json.loads(l)
            if r["side"] == side:
                by[f"{r['run_id']}/g{r['game']:04d}"].append(r)
    uids = sorted(u for u in by if u in games and games[u].get("status") == "ok" and by[u][0]["outcome"] is not None)
    rnd = random.Random(7)
    rnd.shuffle(uids)
    uids = uids[:n_games]
    jobs = [(games[u], by[u], worlds, iterations) for u in uids]
    from multiprocessing import get_context
    t0 = time.time()
    res = []
    with get_context("spawn").Pool(workers) as pool, open(out_path, "w") as f:
        for r in pool.imap_unordered(_sanity_game, jobs, chunksize=1):  # incremental: partial results survive
            res.append(r)
            f.write(json.dumps(r) + "\n")
            f.flush()
    wall = time.time() - t0
    ok = [r for r in res if "error" not in r]
    return summarise(ok, len(res) - len(ok), worlds, iterations, wall)


def summarise(ok, errors, worlds, iterations, wall):
    y = [r["outcome"] for r in ok]
    L = [r["total_luck"] for r in ok]
    v0 = [r["v0"] for r in ok]
    r_l, r2_l = _r2(L, y)
    r_v, r2_v = _r2(v0, y)
    r2_both, coefs = _ols2(v0, L, y)
    # luck measured from the winner's side should be positive on average
    won = [r["total_luck"] for r in ok if r["outcome"] == 1]
    lost = [r["total_luck"] for r in ok if r["outcome"] == 0]
    turns = [t for r in ok for t in r["turns"] if "luck" in t]
    summary = {"games": len(ok), "errors": errors, "worlds": worlds, "iterations": iterations,
               "wall_seconds": round(wall, 1) if wall else None, "cpu_seconds_per_game": round(sum(r["seconds"] for r in ok) / max(1, len(ok)), 2),
               "corr_luck_outcome": round(r_l, 3), "r2_luck": round(r2_l, 3),
               "corr_v0_outcome": round(r_v, 3), "r2_v0": round(r2_v, 3),
               "r2_v0_plus_luck": round(r2_both, 3), "ols_coefs_v0_luck": [round(c, 3) for c in coefs],
               "mean_luck_winners": round(sum(won) / max(1, len(won)), 4),
               "mean_luck_losers": round(sum(lost) / max(1, len(lost)), 4),
               "mean_total_luck": round(sum(L) / max(1, len(L)), 4),
               "sd_total_luck": round((sum((x - sum(L) / len(L)) ** 2 for x in L) / max(1, len(L) - 1)) ** 0.5, 4) if L else None,
               "turns_scored": len(turns), "turns_skipped": sum(r["skipped"] for r in ok),
               "turns_user_action_hidden": sum(1 for t in turns if not t.get("user_action_known")),
               "turns_opp_action_hidden": sum(1 for t in turns if not t.get("opp_action_known")),
               "mean_turn_luck": round(sum(t["luck"] for t in turns) / max(1, len(turns)), 5),
               "mean_abs_turn_luck": round(sum(abs(t["luck"]) for t in turns) / max(1, len(turns)), 4)}
    return summary


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sanity", help="luck vs outcome on farm self-play")
    s.add_argument("decisions")
    s.add_argument("--games", default=None)
    s.add_argument("--n", type=int, default=40)
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--worlds", type=int, default=4)
    s.add_argument("--iterations", type=int, default=20000)
    s.add_argument("--side", default="A")
    s.add_argument("--out", required=True)
    s.add_argument("--summary", default=None)
    s2 = sub.add_parser("summarise", help="summarise a (possibly partial) sanity output file")
    s2.add_argument("out")
    s2.add_argument("--worlds", type=int, default=None)
    s2.add_argument("--iterations", type=int, default=None)
    args = ap.parse_args()
    if args.cmd == "summarise":
        rows = [json.loads(l) for l in open(args.out)]
        ok = [r for r in rows if "error" not in r]
        print(json.dumps(summarise(ok, len(rows) - len(ok), args.worlds, args.iterations, None), indent=1))
        return
    games = args.games or args.decisions.replace(".jsonl", ".games.jsonl")
    summ = sanity(args.decisions, games, args.n, args.workers, args.worlds, args.iterations, args.out, args.side)
    txt = json.dumps(summ, indent=1)
    print(txt)
    if args.summary:
        Path(args.summary).write_text(txt + "\n")


if __name__ == "__main__":
    main()
