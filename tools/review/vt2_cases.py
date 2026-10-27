#!/usr/bin/env python3
"""VT2 / VT2b case builder: planted misreads and planted misplays (research/28 §0.2 V4, E8; S3.3).

Foul Play venv. Source: hr_val farm decisions with log v3 (`tools/dataset/extract.py` output: per decision the
side's own team and the opponent's TRUE team, `opp_team`). Evaluation use only (hr_val is never trained on).

For a user decision (kind move, turn >= 1) whose opponent active still hides its item:
  T = the opponent active's true set (item, ability, tera, moves) as a belief;
  W = T with the item replaced by the most used Smogon item for that species that differs from the true item and is
      not ruled out by Foul Play's inference (impossible_items).
Selection (seeds 21-23, the verdict's depth): verdict.run_decision with U = W and R = T on common worlds gives
WP_W,s and WP_T,s for every option. b_T = argmax mean_s WP_T, b_W = argmax mean_s WP_W.
  VT2  keep if b_W != b_T and Delta_T,s = WP_T,s(b_T) - WP_T,s(b_W) has mean >= 0.08 and is > 0 for all 3 seeds.
       Case: chosen = b_W, X = b_T, belief = W (U), truth = T (R) -> expected verdict Misread.
  VT2b a legal option m with WP_T,s(b_T) - WP_T,s(m) >= 0.15 for all 3 seeds (the qualifying m closest to 0.15).
       Case: chosen = m, X = b_T, belief = T (U), truth = T (R) -> expected verdict Misplay.
At most one VT2 and one VT2b case per game-side. Verdicts then run with the verdict seeds (11-13):
  verdict.py batch --cases vt2_cases.jsonl --stated case --reference truth --out scratch_ds/verdict/vt2.json

  ../environments/foulplay/bin/python tools/review/vt2_cases.py scratch_ds/val/val-n*-c0[2-3]?.jsonl.gz \
      --n 30 --workers 12 --out-dir scratch_ds/verdict
"""
import argparse
import gzip
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SEL_SEEDS = (21, 22, 23)


def _set_belief(s):
    nz = lambda x: "".join(ch for ch in str(x or "").lower() if ch.isalnum())  # noqa: E731
    f = {"item": nz(s.get("item")) or None, "ability": nz(s.get("ability")) or None,
         "tera": nz(s.get("tera_type")) or None, "moves_has": [nz(m) for m in s.get("moves") or []]}
    return {k: v for k, v in f.items() if v}


def job_side(job):
    import fpenv
    fpenv.configure()
    import contextlib
    import io
    import bridge as B
    import reconstruct as R
    import verdict as VD
    out = {"uid": job["uid"], "side": job["side"], "vt2": None, "vt2b": None, "tried": 0, "log": []}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            decs, info = R.reconstruct(job["log"], job["side"], job["team"], compute_legal=False)
            cal = B.load_cal(job["cal"])
            rng = random.Random(job["seed"])
            cands = [d for d in decs if d.kind == "move" and d.turn >= 1 and d.battle.opponent.active is not None]
            rng.shuffle(cands)
            truth_by = {VD.nz(s["species"]): s for s in job["opp_team"]}
            for d in cands[:job["max_decisions"]]:
                if out["vt2"] and out["vt2b"]:
                    break
                act = d.battle.opponent.active
                k = VD.known_state(act)
                ts = truth_by.get(act.name) or truth_by.get(act.base_name) or next(
                    (s for n, s in truth_by.items() if n.startswith(act.base_name)), None)
                if ts is None or k["item_state"] != "unknown":
                    continue
                T = {"sets": {act.name: _set_belief(ts)}}
                true_item = T["sets"][act.name].get("item")
                try:
                    usage = d.battle.mode.smogon_sets.get_pkmn_by_name_in_dict(
                        act, d.battle.mode.smogon_sets.raw_pkmn_sets).get("items", [])
                except Exception:  # noqa: BLE001
                    usage = []
                alt = next((it for it, _ in sorted(usage, key=lambda kv: -kv[1])
                            if it and it != true_item and it not in act.impossible_items and it != "unknownitem"), None)
                if alt is None or true_item is None:
                    continue
                W = {"sets": {act.name: dict(T["sets"][act.name], item=alt)}}
                out["tried"] += 1
                opts = None
                res = VD.run_decision(d, info, chosen="__none__", x="__none__", belief=W, reference="truth", truth=T,
                                      seeds=SEL_SEEDS, iterations=job["iterations"], cal=cal,
                                      case_key=f"vt2sel|{job['uid']}|{job['side']}|{d.turn}|{d.k}")
                runs = res["runs"]
                opts = set.intersection(*[set(r["U"]["wp"]) & set(r["R"]["wp"]) for r in runs])
                if len(opts) < 2:
                    continue
                mT = {o: sum(r["R"]["wp"][o] for r in runs) / len(runs) for o in opts}
                mW = {o: sum(r["U"]["wp"][o] for r in runs) / len(runs) for o in opts}
                bT, bW = max(mT, key=mT.get), max(mW, key=mW.get)
                base = {"uid": job["uid"], "side": job["side"], "turn": d.turn, "k": d.k, "kind": d.kind,
                        "log": job["log"], "team": job["team"], "truth": T,
                        "opp_active": act.name, "true_item": true_item, "selection_seeds": list(SEL_SEEDS),
                        "selection_iterations": job["iterations"]}
                if out["vt2"] is None and bT != bW:
                    dT = [r["R"]["wp"][bT] - r["R"]["wp"][bW] for r in runs]
                    if sum(dT) / 3 >= 0.08 and all(x > 0 for x in dT):
                        out["vt2"] = dict(base, case_id=f"vt2|{job['uid']}|{job['side']}|{d.turn}|{d.k}",
                                          belief=W, planted_item=alt, chosen=bW, X=bT,
                                          selection_delta_T=[round(x, 4) for x in dT], expected="Misread")
                if out["vt2b"] is None:
                    q = []
                    for m in opts - {bT}:
                        dm = [r["R"]["wp"][bT] - r["R"]["wp"][m] for r in runs]
                        if all(x >= 0.15 for x in dm):
                            q.append((sum(dm) / 3, m, dm))
                    if q:
                        _, m, dm = min(q)
                        out["vt2b"] = dict(base, case_id=f"vt2b|{job['uid']}|{job['side']}|{d.turn}|{d.k}",
                                           belief=T, chosen=m, X=bT, selection_delta_T=[round(x, 4) for x in dm],
                                           expected="Misplay")
    except BaseException as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


def side_jobs(paths, seed, max_decisions, iterations, cal):
    import reconstruct as R
    games, recs = {}, defaultdict(list)
    for p in paths:
        with gzip.open(p.replace(".jsonl", ".games.jsonl"), "rt") as f:
            for line in f:
                g = json.loads(line)
                games[g["uid"]] = g
        with gzip.open(p, "rt") as f:
            for line in f:
                r = json.loads(line)
                recs[(f"{r['run_id']}/g{r['game']:04d}", r["side"])].append(r)
    keys = sorted(recs)
    random.Random(seed).shuffle(keys)
    jobs = []
    for u, s in keys:
        rr = recs[(u, s)]
        if u not in games or not rr[0].get("opp_team"):
            continue
        jobs.append({"uid": u, "side": rr[0]["player"], "log": games[u]["log"],
                     "team": R.sets_to_export(R.normalize_sets(R._team_from_norm_sets(rr[0]["own_team"]))),
                     "opp_team": rr[0]["opp_team"], "seed": seed, "max_decisions": max_decisions,
                     "iterations": iterations, "cal": cal})
    return jobs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("decisions", nargs="+", help="hr_val extract .jsonl.gz files (with .games.jsonl.gz siblings)")
    ap.add_argument("--n", type=int, default=30, help="cases wanted per test")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--max-decisions", type=int, default=8, help="candidate decisions tried per game-side")
    ap.add_argument("--iterations", type=int, default=20000, help="selection depth (use the verdict's)")
    ap.add_argument("--cal", default="absolute_human")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-sides", type=int, default=400)
    ap.add_argument("--out-dir", default="scratch_ds/verdict")
    a = ap.parse_args()
    import fpenv
    fpenv.configure()
    from multiprocessing import get_context
    jobs = side_jobs(a.decisions, a.seed, a.max_decisions, a.iterations, a.cal)[:a.max_sides]
    od = Path(a.out_dir)
    od.mkdir(parents=True, exist_ok=True)
    vt2, vt2b, tried, errs, sides = [], [], 0, 0, 0
    t0 = time.time()
    with get_context("spawn").Pool(a.workers, maxtasksperchild=4) as pool:
        for r in pool.imap_unordered(job_side, jobs, chunksize=1):
            sides += 1
            tried += r.get("tried", 0)
            errs += "error" in r
            if r.get("vt2") and len(vt2) < a.n:
                vt2.append(r["vt2"])
            if r.get("vt2b") and len(vt2b) < a.n:
                vt2b.append(r["vt2b"])
            print(f"{sides}/{len(jobs)} sides, tried {tried}, vt2 {len(vt2)}, vt2b {len(vt2b)}", file=sys.stderr)
            if len(vt2) >= a.n and len(vt2b) >= a.n:
                pool.terminate()
                break
    for name, cs in (("vt2_cases.jsonl", vt2), ("vt2b_cases.jsonl", vt2b)):
        with open(od / name, "w") as f:
            for c in cs:
                f.write(json.dumps(c) + "\n")
    summ = {"tool": "tools/review/vt2_cases.py", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sources": a.decisions, "sides_scanned": sides, "decisions_tried": tried, "error_sides": errs,
            "vt2_cases": len(vt2), "vt2b_cases": len(vt2b), "selection_seeds": list(SEL_SEEDS),
            "iterations": a.iterations, "wall_seconds": round(time.time() - t0, 1)}
    (od / "vt2_cases.summary.json").write_text(json.dumps(summ, indent=1) + "\n")
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
