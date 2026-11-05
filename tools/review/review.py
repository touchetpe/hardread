#!/usr/bin/env python3
"""Hax or You: review one Showdown game for one player.

  python tools/review/review.py REPLAY --team TEAM.txt --side p1 --out review.json

REPLAY is a battle log: a Showdown replay .json ({"log": "..."}), a farm battle-log .json,
or a plain text file of protocol lines. TEAM is the player's own team export.

Output JSON:
  meta       players, side, result, engine settings, timings, caveats
  decisions  every decision point: turn, k, kind, action read from the log, calibrated win
             probability, the engine's preferred option, decision cost (win-probability
             points lost vs the engine's preferred option), top alternatives
  turns      per turn: win probability at the start of the turn, luck (chance-only, in
             win-probability points, belief-averaged), decision cost, top alternative
  summary    total luck, total decision cost, biggest luck swing, turning point (biggest
             decision cost), the win-probability curve
  bridge     Start -> Luck -> Choices (both players) -> Result, integer points that add up and
             the forfeit/timeout tag (tools/review/bridge.py; validated by validate_bridge.py)

Engine: Foul Play's own battle-update code, sampler and MCTS (poke-engine), offline.
"""
import argparse
import contextlib
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _round(x, n=3):
    return None if x is None else round(x, n)


def review(log_lines, side, team_text, worlds=4, search_ms=1000, hax_worlds=4, hax_iterations=10000,
           processes=4, calibrator_path=None, skip_hax=False, progress=None, luck_estimator="branch_cond",
           human=True):
    import reconstruct as R
    from calibrate import load_calibrator
    from evaluate import Evaluator
    from luck import LuckMeter, load_abs_calibrator, no_world_matched, turn_summary

    cal_root = load_calibrator(calibrator_path)
    cal_abs = load_abs_calibrator(calibrator_path, human=human)  # reviews are of human games by default
    # Win probability on the absolute scale (eval + search correction) when an absolute calibrator exists;
    # the raw MCTS root value is relative to each root's own eval (see value.py).
    if cal_abs is not None:
        cal, key_root, key_opt = cal_abs, "root_A", "A"
    else:
        cal, key_root, key_opt = cal_root, "root_value", "value"
    t0 = time.time()
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):  # Foul Play prints a few diagnostics to stdout
        decs, info = R.reconstruct(log_lines, side, team_text, compute_legal=False)
    t_rec = time.time() - t0

    evals = {}
    t1 = time.time()
    with Evaluator(worlds, search_ms, processes=processes) as ev, contextlib.redirect_stdout(quiet):
        for d in decs:
            evals[d.idx] = ev.evaluate_decision(d)
            if progress:
                progress(f"eval {d.idx + 1}/{len(decs)}")
    t_eval = time.time() - t1

    t2 = time.time()
    luck = []
    if not skip_hax:
        with LuckMeter(hax_worlds, hax_iterations, processes=processes, hidden_mix=True) as lm, \
                contextlib.redirect_stdout(quiet):
            recs = lm.game_records(decs, info, evals)
        luck = [dict(turn_summary(r, cal_abs or cal, luck_estimator), turn=r["turn"]) for r in recs]
    t_hax = time.time() - t2
    luck_by_turn = {t["turn"]: t for t in luck}

    decisions = []
    for d in decs:
        e = evals[d.idx]
        opts = e["options"]
        wp = cal(e[key_root])
        ranked = sorted(opts.items(), key=lambda kv: (-kv[1]["share"], -(kv[1]["value"] or 0)))
        best = e["best"]
        chosen_wp = cal(opts[d.action][key_opt]) if d.action in opts and opts[d.action].get(key_opt) is not None else None
        best_wp = cal(opts[best][key_opt]) if best and opts[best].get(key_opt) is not None else None
        cost = max(0.0, best_wp - chosen_wp) if chosen_wp is not None and best_wp is not None else None
        cost_range = None
        if d.action is None and d.candidates and best_wp is not None:
            vals = [cal(opts[m][key_opt]) for m in d.candidates if m in opts and opts[m].get(key_opt) is not None]
            if vals:
                cost_range = [_round(max(0.0, best_wp - max(vals))), _round(max(0.0, best_wp - min(vals)))]
        decisions.append({
            "turn": d.turn, "k": d.k, "kind": d.kind, "action": d.action, "action_source": d.action_source,
            "candidates_if_hidden": d.candidates if d.action is None else None,
            "win_prob": _round(wp), "root_value_raw": e["root_value"], "root_A": e.get("root_A"),
            "engine_best": best, "engine_best_win_prob": _round(best_wp), "chosen_win_prob": _round(chosen_wp),
            "decision_cost": _round(cost), "decision_cost_range_if_hidden": cost_range,
            "top_alternatives": [{"action": m, "win_prob": _round(cal(o[key_opt])) if o.get(key_opt) is not None else None,
                                  "share": o["share"]} for m, o in ranked[:3]],
            "opponent_policy_top": list(e["opp_policy"].items())[:3],
            "search_seconds": e["seconds"]})

    turns = []
    for dd in decisions:
        if dd["kind"] not in ("move",):
            continue
        t = dd["turn"]
        lk = luck_by_turn.get(t, {})
        extra = [x for x in decisions if x["turn"] == t and x["kind"] == "switch"]
        tc = (dd["decision_cost"] or 0.0) + sum(x["decision_cost"] or 0.0 for x in extra)
        turns.append({"turn": t, "win_prob": dd["win_prob"], "action": dd["action"],
                      "opponent_action": lk.get("opp_action"), "luck": lk.get("luck"),
                      "luck_events": lk.get("visible_events"), "luck_realised_prob": lk.get("realised_prob"),
                      "chance_swing_vs_counterfactual": lk.get("counterfactual_swing"),
                      "luck_note": lk.get("skipped"), "luck_no_match": no_world_matched(lk),
                      "decision_cost": _round(tc), "engine_best": dd["engine_best"],
                      "forced_switches": [{"action": x["action"], "decision_cost": x["decision_cost"],
                                           "engine_best": x["engine_best"]} for x in extra]})

    scored = [t for t in turns if t["luck"] is not None]
    big_luck = max(scored, key=lambda t: abs(t["luck"])) if scored else None
    costed = [x for x in decisions if x["decision_cost"] is not None]
    big_dec = max(costed, key=lambda x: x["decision_cost"]) if costed else None
    preview = next((x for x in decisions if x["kind"] == "teampreview"), None)
    summary = {
        "result": None if info["user_won"] is None else ("win" if info["user_won"] else "loss"),
        "start_win_prob": preview["win_prob"] if preview else None,
        "total_luck": _round(sum(t["luck"] for t in scored)),
        "total_decision_cost": _round(sum(x["decision_cost"] for x in costed)),
        "decisions": len(decisions), "decisions_with_hidden_action": sum(1 for x in decisions if x["action"] is None),
        "biggest_luck_swing": None if not big_luck else {
            "turn": big_luck["turn"], "luck": big_luck["luck"], "events": big_luck["luck_events"],
            "realised_branch_prob": big_luck["luck_realised_prob"],
            "actions": [big_luck["action"], big_luck["opponent_action"]]},
        "turning_point": None if not big_dec else {
            "turn": big_dec["turn"], "k": big_dec["k"], "chosen": big_dec["action"], "engine_best": big_dec["engine_best"],
            "decision_cost": big_dec["decision_cost"], "win_prob_before": big_dec["win_prob"]},
        "win_prob_curve": [[t["turn"], t["win_prob"]] for t in turns],
    }
    from bridge import end_info, from_review
    end_reason, end_turn = end_info(log_lines)
    meta = {
        "players": info["players"], "side": side, "opponent_side": info["opponent"], "own_hp": info["own_hp_mode"],
        "end_reason": end_reason, "end_turn": end_turn,
        "engine": {"search": f"{worlds} worlds x {search_ms} ms (Foul Play sampler + poke-engine MCTS)",
                   "luck": f"luck v2 ({luck_estimator}): up to {hax_worlds} damage-consistent sampled worlds x every "
                           f"chance outcome class, absolute values (eval + {hax_iterations}-iteration MCTS correction)",
                   "calibrator": "absolute (eval + search)" if cal_abs is not None else getattr(cal, "kind", "identity")},
        "seconds": {"reconstruct": round(t_rec, 1), "evaluate": round(t_eval, 1), "luck": round(t_hax, 1),
                    "total": round(time.time() - t0, 1)},
        "caveats": [
            "Luck is chance-only (crits, misses, secondary effects, speed ties, KO damage rolls, random targets, "
            "consecutive Protect) and belief-averaged: computed in opponent sets sampled from what you knew and "
            "kept only if they reproduce the damage the replay shows. Checked on bot self-play (499 paired games, after "
            "the 2 November fix, with test settings that differ from the page's search): the two seats' luck mirrors "
            "at r = −0.82, and subtracting it removes about 22% of outcome variance (tools/review/README.md). "
            "Damage-roll luck is only counted where a roll decides a KO (poke-engine averages other rolls).",
            "Win probabilities are absolute values (poke-engine eval + search correction) mapped by a calibrator fit "
            "on top-50 ladder games (flatter than the self-play one: human games swing back more often).",
            "Decision cost compares your choice with the engine's preferred option at the same search budget; "
            "small costs (below ~0.02) are within search noise.",
            "When the replay hides your move (fainted first, full paralysis, sleep, flinch), the action is unknown; "
            "the cost is then given as a range over the possible moves."],
    }
    out = {"meta": meta, "summary": summary, "turns": turns, "decisions": decisions}
    out["bridge"] = from_review(out)  # Start -> Luck -> Choices -> Result; the UI reads s/l/c from here (bridge.py)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("replay")
    ap.add_argument("--team", required=True, help="team export text file")
    ap.add_argument("--side", required=True, choices=["p1", "p2"])
    ap.add_argument("--out", default="-")
    ap.add_argument("--worlds", type=int, default=4)
    ap.add_argument("--ms", type=int, default=1000)
    ap.add_argument("--hax-worlds", type=int, default=4)
    ap.add_argument("--hax-iterations", type=int, default=20000)
    ap.add_argument("--processes", type=int, default=4)
    ap.add_argument("--no-hax", action="store_true")
    ap.add_argument("--calibrator", default=None)
    args = ap.parse_args()
    import reconstruct as R
    lines, _ = R.load_log(args.replay)
    res = review(lines, args.side, Path(args.team).read_text(), args.worlds, args.ms, args.hax_worlds,
                 args.hax_iterations, args.processes, args.calibrator, args.no_hax,
                 progress=lambda m: print(m, file=sys.stderr, end="\r"))
    txt = json.dumps(res, indent=1)
    if args.out == "-":
        print(txt)
    else:
        Path(args.out).write_text(txt + "\n")
        s = res["summary"]
        print(json.dumps({k: s[k] for k in ("result", "start_win_prob", "total_luck", "total_decision_cost",
                                            "biggest_luck_swing", "turning_point")}, indent=1), file=sys.stderr)
        print(json.dumps(res["meta"]["seconds"]), file=sys.stderr)


if __name__ == "__main__":
    main()
