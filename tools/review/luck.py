#!/usr/bin/env python3
"""Chance-only luck on an absolute value scale (v2 of the hax meter).

What changed from hax.py (v1), and why:
  * v1 valued each chance branch with an MCTS rooted at that branch. poke-engine scores MCTS leaves
    relative to the root's own static eval, so values of different roots were not comparable (a KO's
    immediate gain was subtracted out). v2 uses A(s) = eval(s) + logit(V_mcts(s))/K (value.py), one
    absolute scale for every branch and every decision point.
  * Branches with the same outcome class (faints, statuses, boosts, damage dealt to 3%) are valued
    once (a representative state) and weighted by the class's total probability.
  * Belief conditioning: a sampled world is *rejected* if no branch reproduces the HP the log shows
    for the Pokemon involved this turn (sum |dHP| > cond_tol); extra worlds are drawn to replace it.
  * Everything raw is stored (per world: accepted, match distance, per class: p, A, realised share),
    so calibration and the estimators are computed afterwards (analyze()).

Estimators (all in win-probability units after calibrating A on farm outcomes, cross-fitted by game):
  branch      sum over turns of mean over worlds [P(A_realised) - sum_c p_c P(A_c)]   (all worlds)
  branch_cond the same over accepted worlds only (falls back to all worlds when every world is rejected)
  event       branch_cond restricted to turns where the log shows a chance event (crit, miss, full
              paralysis, sleep/freeze skip, flinch, secondary status); other turns count 0
"""
import argparse
import gzip
import hashlib
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpenv  # noqa: E402

fpenv.configure()

import poke_engine as pe  # noqa: E402
from fp.search.poke_engine_helpers import battle_to_poke_engine_state  # noqa: E402

import value as V  # noqa: E402
from evaluate import _seed_for, sample_worlds  # noqa: E402
from hax import (_InlinePool, _damage_to, _feat_battle, _feat_engine, condition_on_revealed_move,  # noqa: E402
                 distance, engine_move, select_realised, value_state)

CHANCE_CANT = ("par", "slp", "frz", "flinch")


def visible_events(segment, side):
    """Chance events the log shows this turn, by sufferer ('u' user, 'o' opponent)."""
    ev = {"crit": {"u": 0, "o": 0}, "miss_by": {"u": 0, "o": 0}, "cant": {"u": 0, "o": 0},
          "secondary_status": {"u": 0, "o": 0}}
    last_move_damaging = False
    for l in segment:
        p = l.split("|")
        if len(p) < 3:
            continue
        who = "u" if p[2].startswith(side) else "o"
        if p[1] == "move":
            from fp.data import all_move_json
            from fp.battle.helpers import normalize_name
            last_move_damaging = all_move_json.get(normalize_name(p[3]) if len(p) > 3 else "", {}).get("category") \
                in ("physical", "special")
        elif p[1] == "-crit":
            ev["crit"][who] += 1
        elif p[1] == "-miss":
            ev["miss_by"][who] += 1
        elif p[1] == "cant" and len(p) > 3 and p[3] in CHANCE_CANT:
            ev["cant"][who] += 1
        elif p[1] == "-status" and "[from]" not in l and last_move_damaging:
            ev["secondary_status"][who] += 1
    return ev


def has_event(ev):
    return any(v["u"] or v["o"] for v in ev.values())


# ---------------------------------------------------------------- student evaluator (optional)

STUDENT_DIR = Path(__file__).resolve().parents[1] / "student"
ENGINE_STATUS = {"burn": "brn", "paralyze": "par", "poison": "psn", "toxic": "tox", "sleep": "slp", "freeze": "frz"}
ENGINE_WEATHER = {"rain": "raindance", "sun": "sunnyday", "sand": "sandstorm", "hail": "hail", "snow": "snowscape",
                  "harshsun": "desolateland", "heavyrain": "primordialsea"}
SIDE_KEYS = {"aurora_veil": "auroraveil", "light_screen": "lightscreen", "reflect": "reflect", "safeguard": "safeguard",
             "spikes": "spikes", "stealth_rock": "stealthrock", "sticky_web": "stickyweb", "tailwind": "tailwind",
             "toxic_spikes": "toxicspikes", "mist": "mist", "healing_wish": "healingwish", "toxic_count": "toxic_count"}
BOOST_KEYS = {"attack_boost": "attack", "defense_boost": "defense", "special_attack_boost": "special-attack",
              "special_defense_boost": "special-defense", "speed_boost": "speed"}


class StudentEval:
    """The 25M student's calibrated P(win) (tools/student/infer.py, numpy backend)."""

    def __init__(self, ckpt=None):
        if str(STUDENT_DIR) not in sys.path:
            sys.path.insert(0, str(STUDENT_DIR))
        try:
            import numpy  # noqa: F401
        except ImportError:  # the Foul Play venv has no numpy: borrow the same-ABI (3.13) user site, appended
            import site
            sys.path.append(site.getusersitepackages() if hasattr(site, "getusersitepackages") else "")
            sys.path.append(str(Path.home() / "Library/Python/3.13/lib/python/site-packages"))
        import infer
        import encode as SE
        self.SE = SE
        self.s = infer.load(str(ckpt or (Path(__file__).resolve().parents[2] / "scratch_ds/student/runs/final/frac1/best.npz")))

    def history(self, lines, player):
        return self.SE.canonical_history(lines, player)

    def value(self, view, legal, turn, k, team_preview, history, player):
        _, v = self.s.policy_value(view, legal, turn, k, team_preview, history, player)
        return float(v)


def branch_view(view, state):
    """Decision-logger view of a post-chance branch: the decision's public view with the dynamic fields
    (HP, status, active, boosts, tera, consumed own items, side conditions, weather, terrain, trick room)
    taken from the poke-engine branch state. Static public knowledge (revealed moves/items/abilities, Foul
    Play's inferences) is kept from the decision view; this turn's new reveals are not added."""
    import copy
    v = copy.deepcopy(view)
    for key, side in (("user", state.side_one), ("opponent", state.side_two)):
        sv = v[key]
        mons = ([sv["active"]] if sv.get("active") else []) + list(sv.get("reserve") or [])
        eng = {str(p.id).lower(): p for p in side.pokemon}
        for m in mons:
            p = eng.get(m["name"])
            if p is None:
                continue
            frac = p.hp / p.maxhp if p.maxhp else 0.0
            if key == "user":
                m["hp"] = p.hp
                if p.hp <= 0:
                    m["max_hp"] = 0
                if str(p.item).lower() in ("none", "") and m.get("item"):
                    m["removed_item"], m["item"] = m["item"], None
            else:
                m["hp"] = round(frac * (m.get("max_hp") or 0), 2)
            st = str(p.status).lower()
            m["status"] = ENGINE_STATUS.get(st) if p.hp > 0 else None
            if p.terastallized and not m.get("terastallized"):
                m["terastallized"] = True
                m["tera_type"] = m.get("tera_type") or str(p.tera_type).lower()
            m["boosts"] = {}
        act_name = str(side.pokemon[int(str(side.active_index))].id).lower()
        act = next((m for m in mons if m["name"] == act_name), None)
        if act is not None:
            act["boosts"] = {BOOST_KEYS[k2]: getattr(side, k2) for k2 in BOOST_KEYS if getattr(side, k2)}
            sv["active"] = act
            sv["reserve"] = [m for m in mons if m is not act]
        sc = side.side_conditions
        sv["side_conditions"] = {SIDE_KEYS[k2]: getattr(sc, k2) for k2 in SIDE_KEYS if getattr(sc, k2, 0)}
    w = str(state.weather).lower()
    v["weather"] = ENGINE_WEATHER.get(w)
    t = str(state.terrain).lower()
    v["field"] = None if t in ("none", "") else t
    v["trick_room"] = bool(state.trick_room)
    return v


def branch_legal(bview):
    """Approximate legal menu at the next decision point of a branch view."""
    u = bview["user"]
    act = u.get("active")
    sw = ["switch " + m["name"] for m in u.get("reserve") or [] if (m.get("hp") or 0) > 0]
    if act is None or (act.get("hp") or 0) <= 0:
        return sw, True
    used_tera = any(m.get("terastallized") for m in [act] + list(u.get("reserve") or []))
    mv = list(act.get("moves") or [])
    out = mv + ([m + "-tera" for m in mv] if act.get("can_terastallize") and not used_tera else []) + sw
    return out or sw, False


def _usable_moves(side, d):
    """Uniform mixture over the active's usable moves (engine move strings), at most 4."""
    act = side.pokemon[int(str(side.active_index))]
    mv = [str(m.id).lower() for m in act.moves if str(m.id).lower() != "none" and not m.disabled and m.pp > 0]
    if d is not None and getattr(d, "candidates", None):
        mv = list(d.candidates) or mv  # engine strings, incl. "-tera" when the log showed tera
    mv = mv[:4] or ["none"]
    return [(m, 1.0 / len(mv)) for m in mv]


def class_key(fe, dmg):
    disc = tuple(sorted((k[0], k[1], frac <= 0, st, b) for k, (frac, st, act, b) in fe.items()))
    return disc, round(dmg["u"] / 0.03), round(dmg["o"] / 0.03)


def involved_distance(fe, fo, keys):
    d = 0.0
    for k in keys:
        a, b = fe.get(k), fo.get(k)
        if a is None or b is None:
            continue
        d += abs(a[0] - b[0])
    return d


class LuckMeter:
    def __init__(self, worlds=4, iterations=10000, cond_tol=0.12, max_draw_factor=3, processes=0, hidden_mix=False,
                 student=None):
        self.worlds, self.iterations, self.cond_tol = worlds, iterations, cond_tol
        self.student = student  # optional StudentEval: stores S (student P(win)) next to A for every class
        self.hidden_mix = hidden_mix  # hidden action = uniform mixture over the Pokemon's usable moves
        self.max_draw_factor = max_draw_factor
        if processes:
            from concurrent.futures import ProcessPoolExecutor
            self.pool = ProcessPoolExecutor(max_workers=processes)
        else:
            self.pool = _InlinePool()

    def close(self):
        self.pool.shutdown()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _abs(self, state):
        over = V.battle_over(state)
        if over is not None:
            return None, (1000.0 if over == 1 else -1000.0)
        return self.pool.submit(value_state, state.to_string(), self.iterations), V.evaluate(state)

    def turn_record(self, d, observed_battle, segment, side, user_action=None, opp_policy=None, history=None):
        """Raw per-world data for decision d's turn (user perspective = side one)."""
        from reconstruct import guess_unrevealed_move
        t0 = time.time()
        u = d.action or user_action or ((d.candidates or [None])[0]) or guess_unrevealed_move(d.battle)
        s1 = engine_move(u)
        opp_known = d.opp_action is not None
        s2_list = [engine_move(d.opp_action)] if opp_known else [engine_move(m) for m in (opp_policy or {})] + ["none"]
        rec = {"K": self.worlds, "turn": d.turn, "k": d.k, "user_action": u, "user_action_known": d.action is not None,
               "opp_action": d.opp_action, "opp_action_known": opp_known,
               "events": visible_events(segment, side), "worlds": []}
        if s1 is None:
            rec["skipped"] = "user action unknown"
            return rec
        fo = _feat_battle(observed_battle)
        n_draw = self.worlds * self.max_draw_factor
        _, sampled = sample_worlds(d.battle, n_draw, seed=_seed_for(("luck", d.idx, d.turn)))
        pending = []
        accepted = 0
        for wi, (b, _chance) in enumerate(sampled):
            if accepted >= self.worlds and len(pending) >= self.worlds:
                break
            if opp_known:
                condition_on_revealed_move(b.opponent.active, s2_list[0])
            st = battle_to_poke_engine_state(b)
            if self.hidden_mix and (d.action is None or not opp_known):
                s1s = [(s1, 1.0)] if d.action is not None else _usable_moves(st.side_one, d)
                s2s = [(s2_list[0], 1.0)] if opp_known else _usable_moves(st.side_two, None)
                branches = []
                for a, wa in s1s:
                    for o, wo in s2s:
                        try:
                            for br in pe.generate_instructions(st, a, o):
                                branches.append((br, wa * wo))
                        except Exception:  # noqa: BLE001
                            continue
                tot_w = sum(w for _, w in branches) and sum(
                    w * br.percentage for br, w in branches) / 100.0
                if not branches:
                    continue
                branches = [(br, w / tot_w) for br, w in branches]
            else:
                branches = None
                for s2 in s2_list:
                    try:
                        branches = [(br, 1.0) for br in pe.generate_instructions(st, s1, s2)]
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if branches is None:
                    continue
            base = st.to_string()
            pre_act = {("u", str(st.side_one.pokemon[int(str(st.side_one.active_index))].id).lower()),
                       ("o", str(st.side_two.pokemon[int(str(st.side_two.active_index))].id).lower())}
            rows = []
            for br, wt in branches:
                s_after = pe.State.from_string(base).apply_instructions(br)
                fe = _feat_engine(s_after)
                keys = pre_act | {k for k, v in fe.items() if v[2]}
                rows.append({"p": wt * br.percentage / 100.0, "dist": distance(fe, fo), "fe": fe,
                             "dmg": _damage_to(st, s_after), "state": s_after, "inv": involved_distance(fe, fo, keys)})
            real, dmin = select_realised(rows, fo, {"crit": rec["events"]["crit"]}, 0.02)
            real_ids = {id(r) for r in real}
            match = min(r["inv"] for r in real)
            ok = match <= self.cond_tol
            if not ok and accepted >= self.worlds:
                continue  # enough accepted worlds; don't spend search on a rejected extra
            accepted += ok
            # outcome classes: value one representative per class
            classes = {}
            for r in rows:
                key = class_key(r["fe"], r["dmg"])
                c = classes.setdefault(key, {"p": 0.0, "p_real": 0.0, "rep": r})
                c["p"] += r["p"]
                if id(r) in real_ids:
                    c["p_real"] += r["p"]
                if r["p"] > c["rep"]["p"]:
                    c["rep"] = r
            dec_fut, dec_eval = self._abs(st)
            cl = []
            for c in classes.values():
                fut, ev = self._abs(c["rep"]["state"])
                entry = {"p": c["p"], "p_real": c["p_real"], "fut": fut, "eval": ev}
                if self.student is not None:
                    bst = c["rep"]["state"]
                    over = V.battle_over(bst)
                    if over is not None:
                        entry["S"] = float(over)
                    else:
                        bv = branch_view(d.view, bst)
                        legal, forced = branch_legal(bv)
                        entry["S"] = round(self.student.value(bv, legal, d.turn if forced else d.turn + 1,
                                                              1 if forced else 0, False, history, side), 5)
                cl.append(entry)
            pending.append({"draw": wi, "accepted": ok, "match": round(match, 4), "ddist": round(dmin, 3), "dec": (dec_fut, dec_eval),
                            "classes": cl, "n_branches": len(rows)})
        for w in pending:
            fut, ev = w.pop("dec")
            w["A_dec"] = round(ev + V.logit(fut.result()) / V.K, 2) if fut is not None else ev
            for c in w["classes"]:
                fut = c.pop("fut")
                c["A"] = round(c.pop("eval") + V.logit(fut.result()) / V.K, 2) if fut is not None else c.pop("eval")
                c["p"], c["p_real"] = round(c["p"], 5), round(c["p_real"], 5)
            rec["worlds"].append(w)
        if self.student is not None:
            legal = d.legal or branch_legal(d.view)[0]
            rec["S_dec"] = round(self.student.value(d.view, legal, d.turn, d.k, False, history, side), 5)
        rec["draws"] = len(sampled)
        rec["seconds"] = round(time.time() - t0, 3)
        return rec

    def game_records(self, decisions, info, evals=None):
        out = []
        lines = info.get("lines") or []
        for i, d in enumerate(decisions):
            if d.kind != "move":
                continue
            nxt = decisions[i + 1].battle if i + 1 < len(decisions) else info["final_battle"]
            end = decisions[i + 1].log_index if i + 1 < len(decisions) else len(lines)
            ev = (evals or {}).get(d.idx) or {}
            ua = None
            if d.action is None and ev.get("options") and d.candidates:
                ua = max(d.candidates, key=lambda m: (ev["options"].get(m) or {}).get("share", 0))
            hist = self.student.history(lines[:d.log_index], info["side"]) if self.student is not None else None
            out.append(self.turn_record(d, nxt, lines[d.log_index:end], info["side"], user_action=ua,
                                        opp_policy=ev.get("opp_policy"), history=hist))
        return out


# ---------------------------------------------------------------- estimators

def sigmoid(x):
    return 1 / (1 + math.exp(-max(-60, min(60, x))))


class AbsCal:
    """P(win) = sigmoid(a * K * A + b), fitted on decision-point A values."""

    def __init__(self, a=1.0, b=0.0):
        self.a, self.b = a, b

    def __call__(self, A):
        return sigmoid(self.a * V.K * A + self.b)

    def fit(self, xs, ys, iters=50):
        from calibrate import Platt
        z = [V.K * x for x in xs]
        a, b = 1.0, 0.0
        for _ in range(iters):
            ga = gb = haa = hab = hbb = 0.0
            for zi, yi in zip(z, ys):
                p = sigmoid(a * zi + b)
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
        self.a, self.b = a, b
        return self


def _wp(c, cal):
    return c["S"] if cal == "student" else cal(c["A"])


def world_luck(w, cal):
    tot = sum(c["p"] for c in w["classes"]) or 1.0
    preal = sum(c["p_real"] for c in w["classes"])
    if preal <= 0 or (cal == "student" and any("S" not in c for c in w["classes"])):
        return None
    wp_real = sum(c["p_real"] * _wp(c, cal) for c in w["classes"]) / preal
    wp_exp = sum(c["p"] * _wp(c, cal) for c in w["classes"]) / tot
    return wp_real - wp_exp


def turn_luck(rec, cal, estimator):
    ws = rec.get("worlds") or []
    if not ws:
        return 0.0
    if estimator == "branch":
        ws = [w for w in ws if w["draw"] < rec["K"]]  # the first K draws, unconditioned
    if estimator in ("branch_cond", "event"):
        acc = [w for w in ws if w["accepted"]]
        ws = acc or ws
    if estimator == "event" and not has_event(rec["events"]):
        return 0.0
    vals = [x for x in (world_luck(w, cal) for w in ws) if x is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _first_k(rec):
    return [w for w in rec.get("worlds") or [] if w["draw"] < rec["K"]]


def turn_summary(rec, cal, estimator="branch_cond"):
    """Product view of one turn: luck, realised probability, counterfactual swing, visible events."""
    ws = rec.get("worlds") or []
    acc = [w for w in ws if w["accepted"]] or ws
    p_real, swing = [], []
    for w in acc:
        tot = sum(c["p"] for c in w["classes"]) or 1.0
        pr = sum(c["p_real"] for c in w["classes"])
        if pr <= 0:
            continue
        wp_real = sum(c["p_real"] * cal(c["A"]) for c in w["classes"]) / pr
        rest = [(c["p"] - c["p_real"], cal(c["A"])) for c in w["classes"] if c["p"] - c["p_real"] > 1e-9]
        p_real.append(pr / tot)
        if rest:
            swing.append(wp_real - sum(p * v for p, v in rest) / sum(p for p, _ in rest))
    return {"luck": round(turn_luck(rec, cal, estimator), 4) if ws else None,
            "realised_prob": round(sum(p_real) / len(p_real), 4) if p_real else None,
            "counterfactual_swing": round(sum(swing) / len(swing), 4) if swing else 0.0,
            "visible_events": {k: v for k, v in rec["events"].items() if v["u"] or v["o"]},
            "worlds_accepted": sum(w["accepted"] for w in ws), "worlds_used": len(acc),
            "user_action": rec.get("user_action"), "opp_action": rec.get("opp_action"), "skipped": rec.get("skipped")}


def load_abs_calibrator(path=None):
    p = Path(path) if path else Path(__file__).resolve().parent / "calibrator.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text()).get("absolute")
    return AbsCal(d["a"], d["b"]) if d else None


def decision_value(rec, cal):
    ws = _first_k(rec)  # unconditioned draws only: accepted worlds were chosen using the turn's outcome
    if not ws:
        return None
    return sum(cal(w["A_dec"]) for w in ws) / len(ws)


# ---------------------------------------------------------------- farm runs

def _farm_game(job):
    import reconstruct as R
    g, recs, worlds, iterations, cond_tol, hidden_mix, use_student = job
    try:
        decs, info = R.reconstruct(g["log"], recs[0]["player"], R._team_from_norm_sets(recs[0]["own_team"]),
                                   compute_legal=False)
        lm = LuckMeter(worlds=worlds, iterations=iterations, cond_tol=cond_tol, processes=0, hidden_mix=hidden_mix,
                       student=StudentEval() if use_student else None)
        t0 = time.time()
        turns = lm.game_records(decs, info)
        return {"game": g["uid"], "side": recs[0]["side"], "outcome": recs[0]["outcome"], "turns": turns,
                "seconds_luck": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        import traceback
        return {"game": g["uid"], "side": recs[0]["side"], "error": repr(e), "trace": traceback.format_exc()[-1500:]}


def farm_run(decisions_path, games_path, n_games, sides, workers, worlds, iterations, cond_tol, out_path, seed=7,
             hidden_mix=False, use_student=False, only_games=None):
    games = {}
    with gzip.open(games_path, "rt") as f:
        for l in f:
            g = json.loads(l)
            games[g["uid"]] = g
    by = defaultdict(list)
    with gzip.open(decisions_path, "rt") as f:
        for l in f:
            r = json.loads(l)
            by[(f"{r['run_id']}/g{r['game']:04d}", r["side"])].append(r)
    uids = sorted({u for (u, s) in by if u in games and games[u].get("status") == "ok"})
    random.Random(seed).shuffle(uids)
    if only_games is not None:
        uids = [u for u in uids if u in only_games]
    uids = uids[:n_games]
    done = set()
    if Path(out_path).exists():  # resume
        for l in open(out_path):
            r = json.loads(l)
            if "error" not in r:
                done.add((r["game"], r["side"]))
    jobs = [(games[u], by[(u, s)], worlds, iterations, cond_tol, hidden_mix, use_student) for u in uids for s in sides
            if (u, s) in by and (u, s) not in done and by[(u, s)][0]["outcome"] is not None]
    from multiprocessing import get_context
    with get_context("spawn").Pool(workers) as pool, open(out_path, "a") as f:
        for r in pool.imap_unordered(_farm_game, jobs, chunksize=1):
            f.write(json.dumps(r) + "\n")
            f.flush()


# ---------------------------------------------------------------- analysis (the four checks)

def _corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def _var(x):
    m = sum(x) / len(x)
    return sum((v - m) ** 2 for v in x) / (len(x) - 1)


def _boot(games, stat, reps=1000, seed=0):
    rnd = random.Random(seed)
    vals = []
    for _ in range(reps):
        s = [games[rnd.randrange(len(games))] for _ in games]
        try:
            vals.append(stat(s))
        except (ZeroDivisionError, ValueError):
            continue
    vals.sort()
    return [round(vals[int(0.025 * len(vals))], 4), round(vals[int(0.975 * len(vals)) - 1], 4)]


def analyze(paths, folds=5, event_baseline=None):
    rows = []
    for p in paths:
        for l in open(p):
            r = json.loads(l)
            if "error" not in r and r.get("outcome") is not None:
                rows.append(r)
    fold = lambda g: int(hashlib.sha256(g.encode()).hexdigest()[:8], 16) % folds  # noqa: E731
    # cross-fitted calibration of A at decision points
    cals = {}
    for f in range(folds):
        xs, ys = [], []
        for r in rows:
            if fold(r["game"]) == f:
                continue
            for t in r["turns"]:
                for w in _first_k(t):
                    xs.append(w["A_dec"])
                    ys.append(r["outcome"])
        cals[f] = AbsCal().fit(xs, ys)
    full = AbsCal().fit([w["A_dec"] for r in rows for t in r["turns"] for w in _first_k(t)],
                        [r["outcome"] for r in rows for t in r["turns"] for w in _first_k(t)])
    # calibration quality of the absolute decision value vs outcome (out of fold)
    pp, yy = [], []
    for r in rows:
        cal = cals[fold(r["game"])]
        for t in r["turns"]:
            v = decision_value(t, cal)
            if v is not None:
                pp.append(v)
                yy.append(r["outcome"])
    brier = sum((a - b) ** 2 for a, b in zip(pp, yy)) / len(pp)
    out = {"game_sides": len(rows), "games": len({r["game"] for r in rows}),
           "turns": sum(len(r["turns"]) for r in rows),
           "calibration_absolute": {"a": round(full.a, 4), "b": round(full.b, 4), "brier_oof": round(brier, 4),
                                    "n": len(pp)}}
    worlds_all = [w for r in rows for t in r["turns"] for w in t.get("worlds") or []]
    out["worlds_rejected_share"] = round(sum(not w["accepted"] for w in worlds_all) / max(1, len(worlds_all)), 3)
    turns_all = [t for r in rows for t in r["turns"] if t.get("worlds")]
    out["turns_all_worlds_rejected_share"] = round(sum(all(not w["accepted"] for w in t["worlds"]) for t in turns_all)
                                                   / max(1, len(turns_all)), 3)
    out["mean_classes_per_world"] = round(sum(len(w["classes"]) for w in worlds_all) / max(1, len(worlds_all)), 2)
    has_student = all(all("S_dec" in t for t in r["turns"] if t.get("worlds")) for r in rows) and rows
    ests = [("branch", "branch", None), ("branch_cond", "branch_cond", None), ("event", "event", None)]
    if has_student:
        ests += [("student_branch_cond", "branch_cond", "student"), ("student_event", "event", "student")]
    for name, est, mode in ests:
        per = []
        turn_l = []
        for r in rows:
            cal = "student" if mode == "student" else cals[fold(r["game"])]
            ls = [turn_luck(t, cal, est) for t in r["turns"]]
            turn_l.append(ls)
            if mode == "student":
                v0 = next((t["S_dec"] for t in r["turns"] if "S_dec" in t), 0.5)
            else:
                v0 = decision_value(r["turns"][0], cal) if r["turns"] else 0.5
            per.append({"game": r["game"], "side": r["side"], "y": r["outcome"], "L": sum(ls), "v0": v0 or 0.5})
        res = {}
        for side in ("A", "B"):
            g = [x for x in per if x["side"] == side]
            if len(g) < 10:
                continue
            vr = lambda s: _var([x["y"] - x["L"] for x in s]) / _var([x["y"] for x in s])  # noqa: E731
            vr0 = lambda s: _var([x["y"] - x["v0"] - x["L"] for x in s]) / _var([x["y"] - x["v0"] for x in s])  # noqa: E731
            cr = lambda s: _corr([x["L"] for x in s], [x["y"] for x in s])  # noqa: E731
            res[side] = {"n": len(g), "var_ratio": round(vr(g), 4), "var_ratio_ci": _boot(g, vr),
                         "var_ratio_after_v0": round(vr0(g), 4), "var_ratio_after_v0_ci": _boot(g, vr0),
                         "corr_luck_outcome": round(cr(g), 4), "corr_ci": _boot(g, cr),
                         "sd_total_luck": round(_var([x["L"] for x in g]) ** 0.5, 4)}
        # mirror
        a = {x["game"]: x["L"] for x in per if x["side"] == "A"}
        b = {x["game"]: x["L"] for x in per if x["side"] == "B"}
        both = [(a[k], b[k]) for k in a if k in b]
        if len(both) >= 10:
            mc = lambda s: _corr([x[0] for x in s], [x[1] for x in s])  # noqa: E731
            res["mirror"] = {"n": len(both), "corr_LA_LB": round(mc(both), 4), "corr_ci": _boot(both, mc),
                             "mean_abs_LA_plus_LB": round(sum(abs(x + y) for x, y in both) / len(both), 4),
                             "mean_abs_LA": round(sum(abs(x) for x, _ in both) / len(both), 4)}
            # seat-symmetrised luck for seat A's outcome: (L_A - L_B) / 2
            ya = {x["game"]: x["y"] for x in per if x["side"] == "A"}
            sym = [{"y": ya[k], "L": (a[k] - b[k]) / 2} for k in a if k in b]
            vr = lambda s: _var([x["y"] - x["L"] for x in s]) / _var([x["y"] for x in s])  # noqa: E731
            cr = lambda s: _corr([x["L"] for x in s], [x["y"] for x in s])  # noqa: E731
            res["symmetrised_A"] = {"n": len(sym), "var_ratio": round(vr(sym), 4), "var_ratio_ci": _boot(sym, vr),
                                    "corr_luck_outcome": round(cr(sym), 4), "corr_ci": _boot(sym, cr)}
        # bias per decision (clustered by game)
        flat_by_game = [ls for ls in turn_l if ls]
        mb = lambda s: sum(sum(ls) for ls in s) / sum(len(ls) for ls in s)  # noqa: E731
        res["mean_luck_per_decision"] = round(mb(flat_by_game), 5)
        res["mean_luck_per_decision_ci"] = _boot(flat_by_game, mb)
        res["mean_abs_luck_per_decision"] = round(sum(abs(v) for ls in flat_by_game for v in ls)
                                                  / sum(len(ls) for ls in flat_by_game), 4)
        out[name] = res
    if has_student:
        pp = [t["S_dec"] for r in rows for t in r["turns"] if "S_dec" in t]
        yy = [r["outcome"] for r in rows for t in r["turns"] if "S_dec" in t]
        out["student_decision_brier"] = round(sum((a - b) ** 2 for a, b in zip(pp, yy)) / max(1, len(pp)), 4)
    if event_baseline:
        out["event_count_baseline"] = event_baseline
    return out


def event_count_baseline(games_path, sides=("A",), game_filter=None):
    """corr(net visible chance events for - against, outcome) per game-side (the floor to beat)."""
    xs, ys = [], []
    with gzip.open(games_path, "rt") as f:
        for l in f:
            g = json.loads(l)
            if game_filter is not None and g["uid"] not in game_filter:
                continue
            for s in sides:
                me = g["player_a"] if s == "A" else ("p2" if g["player_a"] == "p1" else "p1")
                ev = visible_events(g["log"], me)
                net = sum(v["o"] - v["u"] for k, v in ev.items() if k in ("crit", "miss_by", "cant"))
                xs.append(net)
                ys.append(int((g["winner"] == "A") == (s == "A")))
    games = list(zip(xs, ys))
    c = lambda s: _corr([x for x, _ in s], [y for _, y in s])  # noqa: E731
    return {"n": len(xs), "corr": round(c(games), 4), "corr_ci": _boot(games, c)}


def student_test_games(decisions_path, permille=70):
    """Games in the student's held-out test split (tools/student/train.py: either roster crc32 % 1000 < 70)."""
    import re
    import zlib
    normalize_name = lambda x: re.sub(r"[^a-z0-9]", "", str(x).lower()) if x else ""  # noqa: E731 (student to_id)
    out = set()
    with gzip.open(decisions_path, "rt") as f:
        for l in f:
            r = json.loads(l)
            for team in (r.get("own_team"), r.get("opp_team")):
                ro = "|".join(sorted(normalize_name(s.get("species") or "") for s in team or []))
                if zlib.crc32(ro.encode()) % 1000 < permille:
                    out.add(f"{r['run_id']}/g{r['game']:04d}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("farm")
    f.add_argument("decisions")
    f.add_argument("--games", default=None)
    f.add_argument("--n", type=int, default=200)
    f.add_argument("--sides", default="A,B")
    f.add_argument("--workers", type=int, default=8)
    f.add_argument("--worlds", type=int, default=4)
    f.add_argument("--iterations", type=int, default=10000)
    f.add_argument("--cond-tol", type=float, default=0.12)
    f.add_argument("--out", required=True)
    f.add_argument("--hidden-mix", action="store_true")
    f.add_argument("--student", action="store_true", help="also store the student's P(win) per class")
    f.add_argument("--only-student-test", action="store_true",
                   help="only games in the student's held-out test split (roster crc32 %% 1000 < 70)")
    a = sub.add_parser("analyze")
    a.add_argument("records", nargs="+")
    a.add_argument("--games", default=None, help="games file for the event-count baseline")
    a.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.cmd == "farm":
        games = args.games or args.decisions.replace(".jsonl", ".games.jsonl")
        only = student_test_games(args.decisions) if args.only_student_test else None
        farm_run(args.decisions, games, args.n, args.sides.split(","), args.workers, args.worlds, args.iterations,
                 args.cond_tol, args.out, hidden_mix=args.hidden_mix, use_student=args.student, only_games=only)
        return
    base = None
    if args.games:
        uids = {json.loads(l)["game"] for p in args.records for l in open(p)}
        base = event_count_baseline(args.games, ("A", "B"), uids)
    res = analyze(args.records, event_baseline=base)
    txt = json.dumps(res, indent=1)
    print(txt)
    if args.out:
        Path(args.out).write_text(txt + "\n")


if __name__ == "__main__":
    main()
