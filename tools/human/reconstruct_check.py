#!/usr/bin/env python3
"""Reconstruction check on human replays: run tools/review/reconstruct.py (read-only use) from both players'
perspectives with a team drafted from what the replay reveals, and cross-check it against parse_replays.py.

The real teams are unknown, so each side's team is DRAFTED from the replay (same idea as the README of
tools/review for its public replay): species and forme from team preview, revealed item / ability / tera type /
moves from sets.jsonl.gz; unrevealed fields are filled with a default (item Leftovers, first listed ability, first
type as tera type, moves padded from the species' most common revealed moves, neutral nature, 85 EVs everywhere).
The draft uses the whole game, so it is only valid for checking state tracking and the decision reading, not for
search quality.

Per decision "clean" means: reconstruct produced it, its legal set is non-empty, and (when the log shows the
action) the action is in the legal set and equals the action read by parse_replays.py for the same (turn, k).
Hidden actions (user fainted / was immobilised before moving) are counted separately; they are not errors.

Usage (from hardread/):
  ../environments/foulplay/bin/python tools/human/reconstruct_check.py --n 50 --workers 4
"""
import argparse
import gzip
import json
import multiprocessing as mp
import random
import re
import signal
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARDREAD = HERE.parent.parent
sys.path.insert(0, str(HARDREAD / "tools" / "review"))
DEFAULT_OUT = HARDREAD / "scratch_ds" / "human"
STATS = ("hp", "atk", "def", "spa", "spd", "spe")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def load_jsonl(p):
    with gzip.open(p, "rt") as f:
        for l in f:
            yield json.loads(l)


def draft_team(sets_rows, profiles, gender_by_species, nick_by_species=None):
    """sets_rows: the player's six rows from sets.jsonl.gz. Returns Showdown export text."""
    from fp.data import pokedex
    out = []
    for r in sets_rows:
        sp = r["species_preview"]
        pd = pokedex.get(norm(sp)) or pokedex.get(norm(sp.split("-")[0])) or {}
        ability = r["ability"] or (pd.get("abilities") or {}).get("0") or ""
        ability = re.sub(r" \(.*\)$", "", ability)   # 'Embody Aspect (Wellspring)' -> 'Embody Aspect'
        item = r["item"] or "Leftovers"
        tera = r["tera_type"] or ((pd.get("types") or ["normal"])[0]).capitalize()
        moves = list(r["moves"])
        prof = (profiles.get(sp) or {}).get("moves", {})
        for m in prof:
            if len(moves) >= 4:
                break
            if m not in moves:
                moves.append(m)
        while len(moves) < 4:
            moves.append("Protect" if "Protect" not in moves else "Substitute" if "Substitute" not in moves else "Rest")
        g = gender_by_species.get(sp, "")
        nick = (nick_by_species or {}).get(sp)
        if nick and nick != sp:
            head = f"{nick} ({sp})"
        else:
            head = sp
        head += (f" ({g})" if g else "") + f" @ {item}"
        lines = [head, f"Ability: {ability}", f"Tera Type: {tera}",
                 "EVs: " + " / ".join(f"85 {l}" for l in ("HP", "Atk", "Def", "SpA", "SpD", "Spe")),
                 "Serious Nature"] + [f"- {m}" for m in moves[:4]]
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


class Timeout(Exception):
    pass


def _alarm(sig, frm):
    raise Timeout()


def worker(job):
    """One (game, side) in a fresh process (Foul Play's datasets are process-global)."""
    import fpenv
    fpenv.configure()
    import reconstruct as R
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(job.get("timeout", 400))
    t0 = time.time()
    res = {"uid": job["uid"], "side": job["side"], "turns": job["turns"], "error": None, "decisions": []}
    try:
        decs, info = R.reconstruct(job["log"], job["side"], job["team"], compute_legal=True)
        res["own_hp_mode"] = info["own_hp_mode"]
        for d in decs:
            res["decisions"].append({
                "turn": d.turn, "k": d.k, "kind": d.kind, "action": d.action, "source": d.action_source,
                "view": d.view, "legal": d.legal, "legal_error": getattr(d, "legal_error", None), "tera": d.tera,
                "candidates": d.candidates if hasattr(d, "candidates") else None})
    except Timeout:
        res["error"] = "Timeout: > %ds" % job.get("timeout", 400)
    except BaseException as e:  # noqa: BLE001
        tb = traceback.extract_tb(e.__traceback__)
        where = f"{Path(tb[-1].filename).name}:{tb[-1].lineno}" if tb else ""
        res["error"] = f"{type(e).__name__}: {str(e)[:160]} @ {where}"
    finally:
        signal.alarm(0)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def expected_action(row):
    at, a = row["action_type"], row["action"]
    if a is None:
        return None
    if at in ("switch", "lead"):
        return "switch " + norm(a)
    if at == "move":
        return norm(a)
    if at == "tera_move":
        return norm(a) + "-tera"
    return None


def base(n):
    n = norm(n)
    return n


def state_flags(view, row):
    """Compare reconstruct's public state at a turn-start decision with parse_replays' state for the same turn."""
    fl = []
    if view is None:
        return fl
    for who, sn, vs in (("self", row["self_active"], view["user"]), ("opp", row["opp_active"], view["opponent"])):
        va = vs.get("active") if vs else None
        if not sn or not va:
            continue
        a, b = base(sn["species"]), base(va["name"])
        if not (a.startswith(b) or b.startswith(a) or a[:6] == b[:6]):
            fl.append(f"{who}_active_species_mismatch")
            continue
        mx = va.get("max_hp") or 100
        hp = 100.0 * va["hp"] / mx if mx else None
        if sn["hp"] is not None and hp is not None and abs(hp - sn["hp"]) > (1.6 if who == "self" else 1.01):
            fl.append(f"{who}_hp_mismatch")
        if (sn["status"] or None) != (va.get("status") or None):
            fl.append(f"{who}_status_mismatch")
    return fl


def evaluate(res, rows):
    """Compare one reconstruction with parse_replays rows of the same player. Returns per-decision verdicts."""
    mine = {}
    for r in rows:
        key = (0, 0) if r["kind"] == "lead" else (r["turn"], r["k"])
        mine[key] = r
    out = []
    seen_keys = set()
    for d in res["decisions"]:
        key = (0, 0) if d["kind"] == "teampreview" else (d["turn"], d["k"])
        seen_keys.add(key)
        v = {"key": key, "kind": d["kind"], "flags": []}
        if d["legal"] is None or len(d["legal"]) == 0:
            v["flags"].append("legal_missing:" + (d["legal_error"] or "empty")[:60])
        act = d["action"]
        if act is None:
            v["hidden"] = True
        else:
            if d["legal"] and act not in d["legal"]:
                v["flags"].append("action_not_in_legal")
            r = mine.get(key)
            if r is None:
                v["flags"].append("no_matching_parser_row")
            else:
                ex = expected_action(r)
                if ex is not None and ex != act:
                    v["flags"].append("action_differs_from_parser")
        r = mine.get(key)
        if r is not None and d["kind"] == "move" and r["kind"] == "turn":
            v["flags"] += state_flags(d.get("view"), r)
            v["state_checked"] = True
        v["clean"] = not v["flags"]
        out.append(v)
    missing = [k for k in mine if k not in seen_keys and mine[k]["kind"] != "faint_replace_single"]
    return out, missing, mine


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(DEFAULT_OUT))
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--min-turns", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=400)
    ap.add_argument("--no-nicknames", action="store_true",
                    help="draft teams without the in-game nicknames (reproduces the nickname failure mode)")
    a = ap.parse_args()
    data = Path(a.data)
    out_dir = data / ("reconstruction_check_no_nicknames" if a.no_nicknames else "reconstruction_check")
    out_dir.mkdir(exist_ok=True)
    games = [g for g in load_jsonl(data / "games.jsonl.gz") if g["turns"] >= a.min_turns]
    random.Random(a.seed).shuffle(games)
    games = games[:a.n]
    ids = {g["uid"] for g in games}
    sets = defaultdict(list)
    for r in load_jsonl(data / "sets.jsonl.gz"):
        if r["uid"] in ids:
            sets[(r["uid"], r["player"])].append(r)
    rows = defaultdict(list)
    for r in load_jsonl(data / "actions.jsonl.gz"):
        if r["uid"] in ids:
            rows[(r["uid"], r["player"])].append(r)
    profiles = json.loads((data / "species_profiles.json").read_text())
    import fpenv
    fpenv.configure()
    jobs = []
    for g in games:
        genders = {}
        for l in g["log"]:
            p = l.split("|")
            if len(p) > 3 and p[1] == "poke":
                sp = p[3].split(",")[0].strip()
                gd = p[3].split(",")[1].strip() if "," in p[3] else ""
                genders[(p[2], sp)] = gd if gd in ("M", "F") else ""
        nicks = {}
        for l in g["log"]:
            p = l.split("|")
            if len(p) > 3 and p[1] in ("switch", "drag") and ": " in p[2]:
                nicks.setdefault((p[2][:2], p[3].split(",")[0].strip()), p[2].split(": ", 1)[1])
        for side in ("p1", "p2"):
            team = draft_team(sets[(g["uid"], side)], profiles,
                              {sp: gd for (s, sp), gd in genders.items() if s == side},
                              None if a.no_nicknames else {sp: n for (s, sp), n in nicks.items() if s == side})
            jobs.append({"uid": g["uid"], "side": side, "turns": g["turns"], "log": g["log"], "team": team,
                         "timeout": a.timeout})
    (out_dir / "drafted_team_example.txt").write_text(jobs[0]["team"])
    print(f"{len(games)} games, {len(jobs)} game-sides, {a.workers} workers", flush=True)
    results = []
    t0 = time.time()
    with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=1) as pool:
        for i, res in enumerate(pool.imap_unordered(worker, jobs)):
            results.append(res)
            print(f"[{i+1}/{len(jobs)}] {res['uid']} {res['side']} {res['seconds']}s "
                  f"{'ERR ' + res['error'] if res['error'] else str(len(res['decisions'])) + ' decisions'} "
                  f"({time.time()-t0:.0f}s)", flush=True)
    # evaluate
    tot = Counter()
    modes = Counter()
    errors = Counter()
    per_side = []
    details = []
    for res in results:
        r_rows = rows[(res["uid"], res["side"])]
        if res["error"]:
            tot["sides_failed"] += 1
            errors[re.sub(r"\d+", "N", res["error"])] += 1
            per_side.append({"uid": res["uid"], "side": res["side"], "error": res["error"]})
            n_exp = len(r_rows)
            tot["decisions_expected_in_failed_sides"] += n_exp
            continue
        tot["sides_ok"] += 1
        ver, missing, mine = evaluate(res, r_rows)
        tot["decisions"] += len(ver)
        tot["decisions_clean"] += sum(v["clean"] for v in ver)
        tot["state_checked"] += sum(1 for v in ver if v.get("state_checked"))
        tot["state_clean"] += sum(1 for v in ver if v.get("state_checked") and not any("mismatch" in f for f in v["flags"]))
        tot["hidden"] += sum(1 for v in ver if v.get("hidden"))
        tot["hidden_clean"] += sum(1 for v in ver if v.get("hidden") and v["clean"])
        shown = [v for v in ver if not v.get("hidden")]
        tot["shown"] += len(shown)
        tot["shown_clean"] += sum(v["clean"] for v in shown)
        tot["parser_rows_not_in_reconstruct"] += len(missing)
        tot["parser_rows"] += len(mine)
        for v in ver:
            for f in v["flags"]:
                modes[f.split(":")[0] if f.startswith("legal_missing") else f] += 1
                if f.startswith("legal_missing"):
                    errors["legal_missing: " + f.split(":", 1)[1]] += 1
            details.append({"uid": res["uid"], "side": res["side"], **{k: v[k] for k in ("key", "kind", "flags", "clean")},
                            "hidden": bool(v.get("hidden"))})
        miss_kinds = Counter(mine[k]["kind"] + (":single" if mine[k].get("single_option") else "") for k in missing)
        per_side.append({"uid": res["uid"], "side": res["side"], "decisions": len(ver),
                         "clean": sum(v["clean"] for v in ver), "missing_vs_parser": dict(miss_kinds),
                         "seconds": res["seconds"]})
        modes.update("missing_in_reconstruct:" + k for k in miss_kinds.elements())
    summ = {
        "games": len(games), "game_sides": len(jobs), "sides_without_exception": tot["sides_ok"],
        "sides_failed": tot["sides_failed"], "decisions_reconstructed": tot["decisions"],
        "decisions_clean": tot["decisions_clean"],
        "clean_rate_of_reconstructed": round(tot["decisions_clean"] / max(tot["decisions"], 1), 4),
        "state_checked_turn_start_decisions": tot["state_checked"], "state_clean": tot["state_clean"],
        "state_clean_rate": round(tot["state_clean"] / max(tot["state_checked"], 1), 4),
        "hidden_actions": tot["hidden"], "hidden_rate": round(tot["hidden"] / max(tot["decisions"], 1), 4),
        "action_shown_decisions": tot["shown"], "action_shown_clean": tot["shown_clean"],
        "clean_rate_action_shown": round(tot["shown_clean"] / max(tot["shown"], 1), 4),
        "parser_decision_rows": tot["parser_rows"], "parser_rows_missing_in_reconstruct": tot["parser_rows_not_in_reconstruct"],
        "failure_modes": dict(modes.most_common()), "errors": dict(errors.most_common(20)),
        "failed_sides_expected_decisions": tot["decisions_expected_in_failed_sides"],
        "wall_seconds": round(time.time() - t0, 1),
        "note": "Teams are drafted from the replay (not the players' real teams); legal sets come from poke-engine "
                "on the reconstructed state.",
    }
    (out_dir / "summary.json").write_text(json.dumps(summ, indent=1))
    (out_dir / "per_side.json").write_text(json.dumps(per_side, indent=1))
    with open(out_dir / "details.jsonl", "w") as f:
        for d in details:
            f.write(json.dumps(d) + "\n")
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    mp.freeze_support()
    main()
