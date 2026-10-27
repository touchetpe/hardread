#!/usr/bin/env python3
"""Verdict v1: was a decision a misplay, a misread, or defensible? (research/27 §4.4 as fixed by research/28 §0.2)

Runs in the Foul Play venv (../environments/foulplay/bin/python). Post-kickoff code; it does not use the
evaluation oracle (fp/hardread_oracle.py) and sets no environment variable. One decision per process.

Inputs per decision: the replay log, the user's team export, side, (turn, k); the user's stated read (belief);
X = the review's engine-best option (picked by the review search with its own seed, so the verdict seeds below
are not selected on); chosen = the user's move.

Belief schema (from the note parser / chips):
  {"sets": {"<species>": {"item": ..., "ability": ..., "tera": ..., "moves_has": [...], "moves_not": [...]}},
   "expects_action": {...}, "confidence": "sure|think|guess"}        (expects_action: read check only, not here)

Conditions (merge semantics per sampled world, research/27 §4.4):
  U  "your read": the stated fields merged into each sampled world;
  R  the reference: T "revealed later" (fields the replay reveals after this decision, same merge) when any field
     of a stated species was revealed later; else E "the engine's estimate" (Foul Play's sampler unchanged).
  Item/ability/tera: a stated value replaces the sampled one (the item is not set if it was already removed or
  knocked off; tera is not set after Terastallizing). moves_has: added if missing, replacing the unrevealed sampled
  move with the lowest usage rate (never a revealed move; never more than 4). moves_not: removed only if unrevealed,
  then refilled with the most used unrevealed move not excluded. Spreads/EVs: not used.
  A stated field contradicted by what was revealed BEFORE the decision is not a verdict: it is reported as
  "Read contradicted on turn N" and dropped; the verdict runs on the remaining fields (§0.2 V5).

Search: for each seed s in (11, 12, 13): one base sample of 4 worlds (Foul Play's sampler, Python RNG seeded from
(s, decision)); U and R are applied to copies of the same worlds (common random numbers on worlds only: poke-engine's
MCTS seeds itself from the OS, so MCTS noise is independent across seeds and across U/R); 20,000 MCTS iterations
per world; WP(option) = cal(absolute value A) (value.py; calibrator absolute_human by default).

Statistic (signed paired contrast, §0.2 V2): D_K,s = WP_K,s(X) - WP_K,s(chosen).
  K BAD: mean_s D >= m (0.05) and D > 0 for all 3 seeds;   K OK: mean_s D <= 0.02.
  Misplay = U BAD; Misread = U OK and R BAD; Defensible = U OK and R OK; Within noise = anything else.
  Info only: under U, a third option Y beating X by >= m with all seeds > 0.
Wording: "3 independent runs on the same sampled worlds" (never "deterministic").

  verdict.py batch --cases vt1_candidates.jsonl --stated none --reference engine --seeds 11,12,13 \
      --iterations 20000 --workers 12 --out scratch_ds/verdict/vt1.json          (VT1, E7 step 2)
  verdict.py one REPLAY --team TEAM.txt --side p1 --turn 14 --k 0 --x knockoff [--belief belief.json] [--out v.json]
  verdict.py rescore scratch_ds/verdict/vt1.json --margin 0.08                   (verdicts at another margin)
"""
import argparse
import hashlib
import json
import math
import re
import sys
import time
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARDREAD = HERE.parents[1]
sys.path.insert(0, str(HERE))

VERSION = 1
SEEDS = (11, 12, 13)
WORLDS = 4
ITERATIONS = 20000
M_BAD = 0.05
M_OK = 0.02
CACHE_DIR = HARDREAD / "scratch_ds" / "verdict" / "cache"
FIELDS = ("item", "ability", "tera", "moves_has", "moves_not")
UNKNOWN_ITEM = "unknownitem"  # fp.constants.UNKNOWN_ITEM


def nz(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


# ------------------------------------------------------------------ pure verdict rule (VT3-testable, no Foul Play)

def k_bad(ds, m=M_BAD):
    return bool(ds) and all(d is not None for d in ds) and sum(ds) / len(ds) >= m and all(d > 0 for d in ds)


def k_ok(ds, m_ok=M_OK):
    return bool(ds) and all(d is not None for d in ds) and sum(ds) / len(ds) <= m_ok


def decide(d_u, d_r, m=M_BAD, m_ok=M_OK):
    """d_u, d_r: per-seed signed contrasts WP(X) - WP(chosen) under U and R."""
    if k_bad(d_u, m):
        return "Misplay"
    if k_ok(d_u, m_ok) and k_bad(d_r, m):
        return "Misread"
    if k_ok(d_u, m_ok) and k_ok(d_r, m_ok):
        return "Defensible"
    return "Within noise"


def card_copy(verdict, x, d_u, d_r, reference):
    fmt = lambda ds: ", ".join("n/a" if d is None else f"{100 * d:+.1f}" for d in ds)  # noqa: E731
    mean = lambda ds: sum(ds) / len(ds) if ds and all(d is not None for d in ds) else None  # noqa: E731
    ref = "revealed later" if reference == "later" else ("the true sets" if reference == "truth"
                                                        else "the engine's estimate")
    if verdict == "Misplay":
        return f"Even if your read were right, {x} was better by {100 * mean(d_u):.0f} points (3 runs: {fmt(d_u)})."
    if verdict == "Misread":
        why = "wrong (revealed later)" if reference == "later" else (
            "wrong" if reference == "truth" else "unlikely (engine's estimate)")
        return (f"Your move fit your read; the read was {why}: under {ref}, {x} was better by "
                f"{100 * mean(d_r):.0f} points (3 runs: {fmt(d_r)}).")
    if verdict == "Defensible":
        return "Your move holds up under your read and under the reference."
    return f"Within noise. {x} minus your move, under your read: {fmt(d_u)}; under {ref}: {fmt(d_r)} (points, 3 runs)."


def wilson(k, n, z=1.959964):
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - h), min(1.0, c + h)]


# ------------------------------------------------------------------ merge semantics (Foul Play objects)

def _all_opp(side):
    return ([side.active] if side.active is not None else []) + list(side.reserve)


def find_pkmn(side, species):
    s = nz(species)
    for p in _all_opp(side):
        if p is None:
            continue
        names = {p.name, p.base_name}
        try:
            names.add(p.get_species())
        except Exception:  # noqa: BLE001
            pass
        if s in names:
            return p
    return None


def known_state(pkmn):
    """What the log revealed about an opponent Pokemon at decision time (the state Foul Play tracks)."""
    item = pkmn.item
    if item is None or item == "":
        item_state = "removed"
    elif item == UNKNOWN_ITEM:
        item_state = "unknown"
    else:
        item_state = "inferred" if getattr(pkmn, "item_inferred", False) else "revealed"
    return {"item": None if item in (None, "", UNKNOWN_ITEM) else item, "item_state": item_state,
            "removed_item": getattr(pkmn, "removed_item", None),
            "ability": pkmn.ability, "tera": pkmn.tera_type if pkmn.terastallized else None,
            "terastallized": bool(pkmn.terastallized), "moves": [m.name for m in pkmn.moves]}


def contradictions(belief, battle):
    """Split a belief into (kept belief, contradictions) against what was revealed before the decision."""
    kept, bad = {"sets": {}}, []
    for sp, f in (belief.get("sets") or {}).items():
        p = find_pkmn(battle.opponent, sp)
        if p is None:
            kept["sets"][sp] = dict(f)
            continue
        k = known_state(p)
        out = {}
        for fld in FIELDS:
            v = f.get(fld)
            if v in (None, "", []):
                continue
            if fld == "item" and k["item_state"] == "revealed" and nz(v) != k["item"]:
                bad.append({"species": sp, "field": "item", "stated": v, "revealed": k["item"]})
            elif fld == "ability" and k["ability"] and nz(v) != k["ability"]:
                bad.append({"species": sp, "field": "ability", "stated": v, "revealed": k["ability"]})
            elif fld == "tera" and k["terastallized"] and nz(v) != nz(k["tera"]):
                bad.append({"species": sp, "field": "tera", "stated": v, "revealed": k["tera"]})
            elif fld == "moves_not" and any(nz(m) in k["moves"] for m in v):
                hit = [m for m in v if nz(m) in k["moves"]]
                bad.append({"species": sp, "field": "moves_not", "stated": hit, "revealed": k["moves"]})
                rest = [m for m in v if nz(m) not in k["moves"]]
                if rest:
                    out["moves_not"] = rest
            elif fld == "moves_has" and len(k["moves"]) >= 4 and any(nz(m) not in k["moves"] for m in v):
                hit = [m for m in v if nz(m) not in k["moves"]]
                bad.append({"species": sp, "field": "moves_has", "stated": hit, "revealed": k["moves"]})
            else:
                out[fld] = v
        if out:
            kept["sets"][sp] = out
    return kept, bad


def _usage(pkmn, mode):
    try:
        return {nz(m): r for m, r in mode.smogon_sets.move_usage_rates(pkmn)}
    except Exception:  # noqa: BLE001
        return {}


def merge_world(world, belief, base_battle, mode):
    """Apply a belief to one sampled world (a Battle copy) in place. Returns the list of changes."""
    changes = []
    for sp, f in (belief.get("sets") or {}).items():
        p = find_pkmn(world.opponent, sp)
        base = find_pkmn(base_battle.opponent, sp)
        if p is None or base is None:
            changes.append({"species": sp, "skipped": "not in the opponent's team at this decision"})
            continue
        k = known_state(base)
        if f.get("item"):
            if k["item_state"] == "removed":
                changes.append({"species": sp, "item": "not set (already removed)"})
            elif p.item != nz(f["item"]):
                changes.append({"species": sp, "item": [p.item, nz(f["item"])]})
                p.item = nz(f["item"])
        if f.get("ability") and p.ability != nz(f["ability"]):
            changes.append({"species": sp, "ability": [p.ability, nz(f["ability"])]})
            p.ability = nz(f["ability"])
        if f.get("tera"):
            if p.terastallized:
                changes.append({"species": sp, "tera": "not set (already Terastallized)"})
            elif nz(p.tera_type) != nz(f["tera"]):
                changes.append({"species": sp, "tera": [p.tera_type, nz(f["tera"])]})
                p.tera_type = nz(f["tera"])
        revealed = set(k["moves"])
        use = _usage(p, mode)
        excluded = {nz(m) for m in f.get("moves_not") or []}
        stated = {nz(m) for m in f.get("moves_has") or []}
        for m in f.get("moves_has") or []:
            m = nz(m)
            if p.get_move(m) is not None:
                continue
            if len(p.moves) >= 4:
                # never drop a revealed move or another stated move
                cand = [mv for mv in p.moves if mv.name not in revealed and mv.name not in stated]
                if not cand:
                    changes.append({"species": sp, "moves_has": m, "skipped": "4 revealed moves"})
                    continue
                drop = min(cand, key=lambda mv: (use.get(mv.name, 0.0), mv.name))
                p.remove_move(drop.name)
                changes.append({"species": sp, "move_replaced": [drop.name, m]})
            else:
                changes.append({"species": sp, "move_added": m})
            if p.add_move(m) is None:
                changes.append({"species": sp, "move_unknown": m})
        for m in excluded:
            if p.get_move(m) is None or m in revealed:
                continue
            p.remove_move(m)
            have = {mv.name for mv in p.moves}
            refill = next((mv for mv, _ in sorted(use.items(), key=lambda kv: -kv[1])
                           if mv not in have and mv not in excluded), None)
            if refill:
                p.add_move(refill)
            changes.append({"species": sp, "move_removed": m, "refill": refill})
    # re-derive the opponent's move locks for the merged sets (as prepare_battles does after sampling)
    if world.opponent.active is not None:
        for mv in world.opponent.active.moves:
            mv.disabled = False
    world.opponent.lock_moves()
    return changes


def revealed_later(decision_battle, final_battle, species=None):
    """Belief with the fields the replay reveals after this decision (T), from Foul Play's end-of-log state.
    species: restrict to these (the stated species); None = all."""
    out = {"sets": {}}
    for fp_ in _all_opp(final_battle.opponent):
        if fp_ is None:
            continue
        if species is not None and nz(fp_.name) not in {nz(s) for s in species} and \
                nz(fp_.base_name) not in {nz(s) for s in species}:
            continue
        base = find_pkmn(decision_battle.opponent, fp_.name)
        if base is None:
            continue
        kb, kf = known_state(base), known_state(fp_)
        f = {}
        # the item held at decision time: the first item removed later (knocked off, consumed, tricked away),
        # else the item shown at the end; unless it was already known or removed at the decision
        item_later = kf["removed_item"] or (kf["item"] if kf["item_state"] == "revealed" else None)
        if item_later and kb["item_state"] in ("unknown", "inferred") and item_later != kb["item"]:
            f["item"] = item_later
        if kf["ability"] and not kb["ability"]:
            f["ability"] = kf["ability"]
        if kf["terastallized"] and not kb["terastallized"] and kf["tera"]:
            f["tera"] = kf["tera"]
        new_moves = [m for m in kf["moves"] if m not in kb["moves"]]
        if new_moves:
            f["moves_has"] = new_moves
        if f:
            out["sets"][fp_.name] = f
    return out


# ------------------------------------------------------------------ search

def search_condition(worlds, iterations):
    import value as V
    from evaluate import aggregate
    from fp.search.poke_engine_helpers import battle_to_poke_engine_state
    from poke_engine import monte_carlo_tree_search
    results, evals = [], []
    for b, chance in worlds:
        st = battle_to_poke_engine_state(b)
        evals.append(V.evaluate(st))
        results.append((monte_carlo_tree_search(st, 0, iterations=iterations), chance))
    options, _root, _opp, root_abs = aggregate(results, evals)
    return options, root_abs


def run_decision(d, info, chosen, x, belief, reference="auto", truth=None, seeds=SEEDS, iterations=ITERATIONS,
                 worlds=WORLDS, cal=None, case_key="", m=M_BAD, m_ok=M_OK):
    """The verdict for one reconstructed decision d (reconstruct.Decision)."""
    from evaluate import _seed_for, sample_worlds
    t0 = time.time()
    belief = belief or {"sets": {}}
    kept, contra = contradictions(belief, d.battle)
    stated_species = list(kept["sets"])
    # reference
    if reference == "truth":
        ref_belief, ref_kind = truth or {"sets": {}}, "truth"
    elif reference == "engine":
        ref_belief, ref_kind = {"sets": {}}, "engine"
    else:  # auto / later
        later = revealed_later(d.battle, info["final_battle"], stated_species or None)
        if later["sets"] and (reference == "later" or stated_species):
            ref_belief, ref_kind = later, "later"
        else:
            ref_belief, ref_kind = {"sets": {}}, "engine"
    runs = []
    for s in seeds:
        base, sampled = sample_worlds(d.battle, worlds, _seed_for(("verdict", VERSION, s, case_key, d.turn, d.k)))
        run = {"seed": s}
        for cond, bel in (("U", kept), ("R", ref_belief)):
            ws = [(deepcopy(b), c) for b, c in sampled]
            ch = []
            for b, _ in ws:
                ch.append(merge_world(b, bel, base, d.battle.mode) if bel["sets"] else [])
            opts, root_abs = search_condition(ws, iterations)
            wp = {mv: cal(o["A"]) for mv, o in opts.items() if o.get("A") is not None}
            delta = (wp[x] - wp[chosen]) if (x in wp and chosen in wp) else None
            run[cond] = {"delta": delta, "wp": {mv: round(v, 4) for mv, v in wp.items()},
                         "root_wp": round(cal(root_abs), 4), "missing": [o for o in (x, chosen) if o not in wp],
                         "changes_world0": ch[0] if ch else []}
        runs.append(run)
    d_u = [r["U"]["delta"] for r in runs]
    d_r = [r["R"]["delta"] for r in runs]
    verdict = decide(d_u, d_r, m, m_ok)
    # info: a third option Y beating X under U by >= m in every seed
    info_y = None
    common = set.intersection(*[set(r["U"]["wp"]) for r in runs]) if runs else set()
    for y in sorted(common - {x, chosen}):
        dy = [r["U"]["wp"][y] - r["U"]["wp"].get(x, float("nan")) for r in runs]
        if all(v > 0 for v in dy) and sum(dy) / len(dy) >= m:
            if info_y is None or sum(dy) > sum(info_y["deltas"]):
                info_y = {"option": y, "deltas": [round(v, 4) for v in dy],
                          "text": f"under your read, {y} looked even better"}
    out = {"version": VERSION, "turn": d.turn, "k": d.k, "kind": d.kind, "chosen": chosen, "X": x,
           "stated": kept, "contradictions": [dict(c, text=_contra_text(c, info, d)) for c in contra],
           "reference": ref_kind, "reference_fields": ref_belief if ref_kind != "engine" else None,
           "seeds": list(seeds), "iterations": iterations, "worlds": worlds,
           "delta_U": [None if v is None else round(v, 4) for v in d_u],
           "delta_R": [None if v is None else round(v, 4) for v in d_r],
           "mean_delta_U": _mean(d_u), "mean_delta_R": _mean(d_r),
           "margin": m, "ok_margin": m_ok, "verdict": verdict, "copy": card_copy(verdict, x, d_u, d_r, ref_kind),
           "info_third_option": info_y, "runs": runs, "seconds": round(time.time() - t0, 1),
           "wording": "3 independent runs on the same sampled worlds"}
    return out


def _mean(ds):
    return None if not ds or any(v is None for v in ds) else round(sum(ds) / len(ds), 4)


def _contra_text(c, info, d):
    n = reveal_turn(info.get("lines") or [], info.get("opponent"), c, d.log_index)
    return f"Read contradicted on turn {n}" if n is not None else "Read contradicted by an earlier log line"


def reveal_turn(lines, opp, c, upto):
    """Turn of the first log line (before the decision) that revealed the contradicting field."""
    turn = 0
    want = {"item": ("-item", "-enditem", "item:"), "ability": ("-ability", "ability:"), "tera": ("-terastallize",),
            "moves_not": ("move",), "moves_has": ("move",)}[c["field"]]
    vals = c["stated"] if isinstance(c["stated"], list) else [c["revealed"]]
    for ln in lines[:upto]:
        p = ln.split("|")
        if len(p) > 2 and p[1] == "turn":
            try:
                turn = int(p[2])
            except ValueError:
                pass
            continue
        if not opp or f"{opp}a:" not in ln:
            continue
        tag = p[1] if len(p) > 1 else ""
        if c["field"] in ("moves_not", "moves_has"):
            if tag == "move" and len(p) > 3 and p[2].startswith(f"{opp}a") and any(nz(p[3]) == nz(v) for v in vals):
                return turn
        elif tag in want or any(w in ln for w in want if w.endswith(":")):
            if any(nz(v) in nz(ln) for v in vals if v):
                return turn
    return None


# ------------------------------------------------------------------ cases, cache, workers

def _cache_key(case, cfg):
    h = hashlib.sha256()
    for part in (json.dumps(case.get("log"), sort_keys=True), case.get("team"), case["side"], case["turn"], case["k"],
                 case["chosen"], case["X"], json.dumps(case.get("belief"), sort_keys=True),
                 json.dumps(case.get("truth"), sort_keys=True), json.dumps(cfg, sort_keys=True), VERSION):
        h.update(str(part).encode())
        h.update(b"\0")
    return h.hexdigest()[:24]


def job_case(job):
    """One case per process (Foul Play datasets are process-global)."""
    import fpenv
    fpenv.configure()
    import contextlib
    import io
    import bridge as B
    import reconstruct as R
    case, cfg = job["case"], job["cfg"]
    key = _cache_key(case, cfg)
    cp = Path(cfg["cache_dir"]) / f"{key}.json" if cfg.get("cache_dir") else None
    if cp is not None and cp.exists():
        out = json.loads(cp.read_text())
        out["cached"] = True
        return out
    base = {"case_id": case.get("case_id"), "uid": case.get("uid"), "side": case["side"], "cache_key": key}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            log = case["log"] if isinstance(case["log"], list) else case["log"].split("\n")
            decs, info = R.reconstruct(log, case["side"], case["team"], compute_legal=False)
            d = next((x for x in decs if x.turn == case["turn"] and x.k == case["k"]), None)
            if d is None:
                return dict(base, error=f"decision turn {case['turn']} k {case['k']} not found")
            belief = case.get("belief") if cfg["stated"] == "case" else {"sets": {}}
            res = run_decision(d, info, case["chosen"], case["X"], belief, reference=cfg["reference"],
                               truth=case.get("truth"), seeds=tuple(cfg["seeds"]), iterations=cfg["iterations"],
                               worlds=cfg["worlds"], cal=B.load_cal(cfg["cal"]), case_key=case.get("case_id") or key,
                               m=cfg["margin"])
        out = dict(base, **res)
        out["selection"] = {k: case.get(k) for k in ("wp_before", "cost", "wp_X", "wp_chosen", "rank_in_side")}
        if cp is not None:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(out) + "\n")
        return out
    except BaseException as e:  # noqa: BLE001
        import traceback
        return dict(base, error=f"{type(e).__name__}: {str(e)[:200]}", trace=traceback.format_exc()[-1500:])


def summarize(results, m=M_BAD, m_ok=M_OK, label="VT1"):
    ok = [r for r in results if "error" not in r]
    counts = {}
    for r in ok:
        v = decide(r["delta_U"], r["delta_R"], m, m_ok)
        counts[v] = counts.get(v, 0) + 1
    n = len(ok)
    fd = counts.get("Misplay", 0) + counts.get("Misread", 0)
    lo, hi = wilson(fd, n)
    out = {"label": label, "margin": m, "ok_margin": m_ok, "n": n, "errors": len(results) - n, "counts": counts,
           "false_decisive": fd, "false_decisive_rate": round(fd / n, 4) if n else None,
           "wilson95": [round(lo, 4), round(hi, 4)]}
    if label == "VT1":
        out["pass_rule"] = "n >= 100 and (Misplay + Misread) / n <= 0.10 and Wilson 95% upper <= 0.18"
        out["pass"] = bool(n >= 100 and n and fd / n <= 0.10 and hi <= 0.18)
    return out


def batch(args):
    from multiprocessing import get_context
    cfg = {"stated": args.stated, "reference": args.reference, "seeds": [int(s) for s in args.seeds.split(",")],
           "iterations": args.iterations, "worlds": args.worlds, "cal": args.cal, "margin": args.margin,
           "cache_dir": None if args.no_cache else str(CACHE_DIR)}
    cases = [json.loads(l) for l in open(args.cases)]
    cases = [c for c in cases if "error" not in c]
    if args.limit:
        cases = cases[:args.limit]
    stream = Path(str(Path(args.out).with_suffix("")) + ".cases.jsonl")
    stream.parent.mkdir(parents=True, exist_ok=True)
    done = {}
    if stream.exists():
        for l in open(stream):
            r = json.loads(l)
            if "error" not in r:
                done[r.get("case_id")] = r
    todo = [c for c in cases if c.get("case_id") not in done]
    t0 = time.time()
    results = list(done.values())
    with get_context("spawn").Pool(args.workers, maxtasksperchild=4) as pool, open(stream, "a") as f:
        for i, r in enumerate(pool.imap_unordered(job_case, [{"case": c, "cfg": cfg} for c in todo], chunksize=1)):
            f.write(json.dumps(r) + "\n")
            f.flush()
            results.append(r)
            if args.progress:
                print(f"{i + 1}/{len(todo)} {r.get('verdict', r.get('error'))}", file=sys.stderr)
    label = "VT1" if (args.stated == "none" and args.reference == "engine") else "batch"
    summ = summarize(results, args.margin, M_OK, label)
    sens = {str(mm): summarize(results, mm, M_OK, label) for mm in sorted({args.margin, 0.05, 0.08})}
    out = {"tool": "tools/review/verdict.py", "version": VERSION,
           "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cases_file": args.cases, "config": cfg,
           "wall_seconds": round(time.time() - t0, 1), "summary": summ, "by_margin": sens,
           "chosen_definition": "case['chosen'] (VT1: the bot's own C1t1 move)",
           "X_definition": "case['X'] (the review search's engine-best, its own seed)",
           "cases": [{k: r.get(k) for k in ("case_id", "turn", "k", "kind", "chosen", "X", "verdict", "delta_U",
                                            "delta_R", "mean_delta_U", "mean_delta_R", "reference", "contradictions",
                                            "info_third_option", "selection", "seconds", "error")} for r in results]}
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(summ, indent=1))
    print(json.dumps({k: v["counts"] for k, v in sens.items()}))


def rescore(args):
    d = json.loads(Path(args.result).read_text())
    res = [c for c in d["cases"]]
    print(json.dumps(summarize(res, args.margin, M_OK, d["summary"].get("label", "batch")), indent=1))


def one(args):
    import contextlib
    import io
    import fpenv
    fpenv.configure()
    import bridge as B
    import reconstruct as R
    lines, _ = R.load_log(args.replay)
    team = Path(args.team).read_text()
    belief = json.loads(Path(args.belief).read_text()) if args.belief else {"sets": {}}
    with contextlib.redirect_stdout(io.StringIO()):
        decs, info = R.reconstruct(lines, args.side, team, compute_legal=False)
    d = next((x for x in decs if x.turn == args.turn and x.k == args.k), None)
    if d is None:
        sys.exit(f"no decision at turn {args.turn} k {args.k}")
    chosen = args.chosen or d.action
    if not chosen:
        sys.exit("the log hides this action; pass --chosen")
    key = hashlib.sha256(json.dumps([lines, team, args.side, args.turn, args.k, chosen, args.x, belief,
                                     args.reference, args.seeds, args.iterations, args.cal, VERSION]).encode()).hexdigest()[:24]
    cp = CACHE_DIR / f"one-{key}.json"
    if cp.exists() and not args.no_cache:
        txt = cp.read_text()
    else:
        with contextlib.redirect_stdout(io.StringIO()):
            res = run_decision(d, info, chosen, args.x, belief, reference=args.reference,
                               seeds=tuple(int(s) for s in args.seeds.split(",")), iterations=args.iterations,
                               cal=B.load_cal(args.cal), case_key=key, m=args.margin)
        txt = json.dumps(res, indent=1)
        if not args.no_cache:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(txt + "\n")
    if args.out:
        Path(args.out).write_text(txt + "\n")
    r = json.loads(txt)
    print(json.dumps({k: r[k] for k in ("verdict", "copy", "delta_U", "delta_R", "reference", "contradictions",
                                        "info_third_option", "seconds")}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch", help="verdicts on a cases file (VT1/VT2)")
    b.add_argument("--cases", required=True)
    b.add_argument("--stated", default="none", choices=["none", "case"], help="none: U = the engine's estimate; "
                   "case: U = case['belief']")
    b.add_argument("--reference", default="engine", choices=["engine", "later", "truth", "auto"])
    b.add_argument("--seeds", default="11,12,13")
    b.add_argument("--iterations", type=int, default=ITERATIONS)
    b.add_argument("--worlds", type=int, default=WORLDS)
    b.add_argument("--cal", default="absolute_human")
    b.add_argument("--margin", type=float, default=M_BAD)
    b.add_argument("--workers", type=int, default=8)
    b.add_argument("--limit", type=int, default=None)
    b.add_argument("--no-cache", action="store_true")
    b.add_argument("--progress", action="store_true")
    b.add_argument("--out", required=True)
    o = sub.add_parser("one", help="one decision of a replay (product path)")
    o.add_argument("replay")
    o.add_argument("--team", required=True)
    o.add_argument("--side", required=True, choices=["p1", "p2"])
    o.add_argument("--turn", type=int, required=True)
    o.add_argument("--k", type=int, default=0)
    o.add_argument("--x", required=True, help="the review's engine-best option")
    o.add_argument("--chosen", default=None, help="default: the action read from the log")
    o.add_argument("--belief", default=None, help="belief JSON (schema in the module docstring)")
    o.add_argument("--reference", default="auto", choices=["auto", "later", "engine"])
    o.add_argument("--seeds", default="11,12,13")
    o.add_argument("--iterations", type=int, default=ITERATIONS)
    o.add_argument("--cal", default="absolute_human")
    o.add_argument("--margin", type=float, default=M_BAD)
    o.add_argument("--no-cache", action="store_true")
    o.add_argument("--out", default=None)
    rs = sub.add_parser("rescore", help="recompute verdict counts from a batch result at another margin")
    rs.add_argument("result")
    rs.add_argument("--margin", type=float, default=0.08)
    args = ap.parse_args()
    {"batch": batch, "one": one, "rescore": rescore}[args.cmd](args)


if __name__ == "__main__":
    main()
