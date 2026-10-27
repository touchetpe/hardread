#!/usr/bin/env python3
"""Validate the bridge Start -> Luck -> Choices (both players) -> Result (research/27 §4.2, research/28 S1.5, E6).

Read-only; system python3 is enough. From hardread/:

  python3 tools/review/validate_bridge.py scratch_ds/review/human/run520.jsonl \
      scratch_ds/review/human/paired_default.jsonl scratch_ds/review/human/paired_smogon.jsonl \
      --cal absolute_human --gate-file paired_smogon --json scratch_ds/review/bridge_validation.json

Inputs are raw review records (human_eval.py run/farm output: decisions with root_A, luck worlds, outcome,
meta.end_reason). Every file is measured; only the gate file (matched by file stem, e.g. 'paired_smogon'
or 'run_b') is gated. Exit 0 iff every acceptance row passes on the gate file; 1 otherwise; 2 on usage errors.

The formulas are tools/review/bridge.py's (one implementation for product and validation).

Acceptance rows (research/28 S1.5), on the gate file:
  V1  max |S + L + C - R| <= 1e-9; displayed integers add up (s + l + c = 100 R); max |c - round(100 C)| <= 1
  V2  share |C| > 1 <= 2%
  V3  mean L winners - losers: game-bootstrap 95% CI lower bound > 0; P(L>0 | win) >= 0.65; P(L>0 | loss) <= 0.50
  V4  |mean L| <= 0.03 (luck bias per game-side)
  V5  corr(L_p1, L_p2) <= -0.40; corr(C_p1, C_p2) <= -0.80 (games with both seats reviewed)
  V6  corr(sum act, C) >= 0.50 (the 'choices' bar is mostly the action pairs played)
Everything else is informational (Draft-A rows are reported, never gated: Draft A fails V4 by design).
"""
import argparse
import json
import math
import random
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge as B  # noqa: E402

BOOT_N, BOOT_SEED = 1000, 7

# (key, description, comparison, threshold) evaluated on the gate file's metrics
ACCEPTANCE = [
    ("V1_max_abs_identity_error", "max |S + L + C - R|", "<=", 1e-9),
    ("V1_display_adds_up", "displayed integers add up (s + l + c = 100 R) on every side", "==", True),
    ("V1_display_choices_vs_exact_max_pts", "max |c - round(100 C)| (points)", "<=", 1),
    ("V2_share_abs_C_gt_1", "share of sides with |C| > 1", "<=", 0.02),
    ("V3_diff_win_minus_loss_ci_low", "mean L winners - losers, 95% CI lower bound", ">", 0.0),
    ("V3_P_L_pos_win", "P(L > 0 | win)", ">=", 0.65),
    ("V3_P_L_pos_loss", "P(L > 0 | loss)", "<=", 0.50),
    ("V4_abs_mean_L", "|mean L| over all sides (luck bias)", "<=", 0.03),
    ("V5_corr_L1_L2", "corr(L_p1, L_p2)", "<=", -0.40),
    ("V5_corr_C1_C2", "corr(C_p1, C_p2)", "<=", -0.80),
    ("V6_corr_act_vs_C", "corr(sum act, C)", ">=", 0.50),
]


def _cmp(v, op, thr):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    return {"<=": v <= thr, ">=": v >= thr, "<": v < thr, ">": v > thr, "==": v == thr}[op]


def corr(xs, ys):
    if len(xs) < 3:
        return float("nan")
    mx, my = st.mean(xs), st.mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else float("nan")


def boot_ci(rows, f, n=BOOT_N, seed=BOOT_SEED):
    """95% percentile CI, resampling games (uid) with replacement."""
    rng = random.Random(seed)
    keys = sorted({x["uid"] for x in rows})
    by = defaultdict(list)
    for x in rows:
        by[x["uid"]].append(x)
    vals = []
    for _ in range(n):
        smp = [y for k in (rng.choice(keys) for _ in keys) for y in by[k]]
        try:
            vals.append(f(smp))
        except (ZeroDivisionError, st.StatisticsError):
            pass
    vals.sort()
    return [vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]]


def load_rows(path, cal):
    rows, skipped = [], {"error": 0, "no_preview": 0, "tie": 0}
    for line in open(path):
        r = json.loads(line)
        if "error" in r:
            skipped["error"] += 1
            continue
        if r.get("outcome") not in (0, 1):
            skipped["tie"] += 1
            continue
        b = B.from_record(r, cal)
        if b is None:
            skipped["no_preview"] += 1
            continue
        o = r.get("decisions") or []
        cost, n_big = 0.0, 0
        for d in o:  # the r1 waterfall input (sum of per-decision regrets), reported for comparison only
            op = d["options"]
            if d.get("action") in op and d.get("best") in op and op[d["action"]].get("A") is not None \
                    and op[d["best"]].get("A") is not None:
                c = max(0.0, cal(op[d["best"]]["A"]) - cal(op[d["action"]]["A"]))
                cost += c
                n_big += c > 0.05
        rows.append({"uid": r["uid"], "side": r["side"], "S": b["S"], "L": b["L"], "R": b["R"], "C": b["C"],
                     "s": b["s"], "l": b["l"], "c": b["c"], **{k: b["split"][k] for k in ("act", "drift", "end")},
                     "sum_cost": cost, "n_cost_gt_05": n_big, "end_reason": (r.get("meta") or {}).get("end_reason")})
    return rows, skipped


def metrics(rows):
    W = [x for x in rows if x["R"] == 1]
    Lo = [x for x in rows if x["R"] == 0]
    q = lambda xs, p: sorted(xs)[int(p * (len(xs) - 1))]
    m = {"game_sides": len(rows), "wins": len(W), "losses": len(Lo), "games": len({x["uid"] for x in rows})}
    # V1 additivity, exact and displayed
    m["V1_max_abs_identity_error"] = max(abs(x["S"] + x["L"] + x["C"] - x["R"]) for x in rows)
    m["V1_max_abs_split_identity_error"] = max(abs(x["act"] + x["drift"] + x["end"] - x["C"]) for x in rows)
    m["V1_display_adds_up"] = all(x["s"] + x["l"] + x["c"] == B.rnd(100 * x["R"]) for x in rows)
    m["V1_display_choices_vs_exact_max_pts"] = max(abs(x["c"] - B.rnd(100 * x["C"])) for x in rows)
    # V2 ranges
    m["V2_share_abs_L_gt_0.5"] = sum(abs(x["L"]) > 0.5 for x in rows) / len(rows)
    m["V2_share_abs_C_gt_1"] = sum(abs(x["C"]) > 1 for x in rows) / len(rows)
    m["V2_share_swingy"] = sum(abs(x["L"]) > 0.5 or abs(x["C"]) > 1 for x in rows) / len(rows)
    m["V2_share_running_total_S_plus_L_outside_0_1"] = sum(not (0 <= x["S"] + x["L"] <= 1) for x in rows) / len(rows)
    # V3 luck direction
    m["V3_mean_L_win"] = st.mean(x["L"] for x in W)
    m["V3_mean_L_loss"] = st.mean(x["L"] for x in Lo)
    m["V3_diff_win_minus_loss"] = m["V3_mean_L_win"] - m["V3_mean_L_loss"]
    ci = boot_ci(rows, lambda s: st.mean(x["L"] for x in s if x["R"] == 1) - st.mean(x["L"] for x in s if x["R"] == 0))
    m["V3_diff_win_minus_loss_ci"] = ci
    m["V3_diff_win_minus_loss_ci_low"] = ci[0]
    m["V3_P_L_pos_win"] = sum(x["L"] > 0 for x in W) / len(W)
    m["V3_P_L_pos_loss"] = sum(x["L"] > 0 for x in Lo) / len(Lo)
    # V4 bias
    m["V4_mean_L"] = st.mean(x["L"] for x in rows)
    m["V4_abs_mean_L"] = abs(m["V4_mean_L"])
    m["V4_mean_L_ci"] = boot_ci(rows, lambda s: st.mean(x["L"] for x in s))
    m["V4b_P_C_pos_win"] = sum(x["C"] > 0 for x in W) / len(W)
    m["V4b_P_C_neg_loss"] = sum(x["C"] < 0 for x in Lo) / len(Lo)
    m["V4b_median_C_win_loss"] = [st.median(x["C"] for x in W), st.median(x["C"] for x in Lo)]
    # V5 mirror within a game
    by = defaultdict(dict)
    for x in rows:
        by[x["uid"]][x["side"]] = x
    pairs = [(d["p1"], d["p2"]) for d in by.values() if "p1" in d and "p2" in d]
    m["V5_pairs"] = len(pairs)
    m["V5_corr_L1_L2"] = corr([a["L"] for a, b in pairs], [b["L"] for a, b in pairs])
    m["V5_corr_C1_C2"] = corr([a["C"] for a, b in pairs], [b["C"] for a, b in pairs])
    m["V5_mean_abs_S1_plus_S2_minus_1"] = st.mean(abs(a["S"] + b["S"] - 1) for a, b in pairs) if pairs else None
    m["V5_mean_abs_C1_plus_C2"] = st.mean(abs(a["C"] + b["C"]) for a, b in pairs) if pairs else None
    # V6 what 'choices' is made of
    m["V6_corr_act_vs_C"] = corr([x["act"] for x in rows], [x["C"] for x in rows])
    gap = [(b["sum_cost"] - a["sum_cost"], a["C"]) for a, b in pairs] + [(a["sum_cost"] - b["sum_cost"], b["C"]) for a, b in pairs]
    m["V6_corr_C_vs_regret_gap_opp_minus_user"] = corr([g for g, c in gap], [c for g, c in gap])
    # V7 decomposition of C
    vC = st.pvariance([x["C"] for x in rows])
    for nm in ("act", "drift", "end"):
        m[f"V7_mean_abs_{nm}"] = st.mean(abs(x[nm]) for x in rows)
        m[f"V7_var_share_{nm}"] = st.pvariance([x[nm] for x in rows]) / vC if vC else None
    for er in ("ko", "forfeit", "timeout"):
        sub = [x for x in rows if x["end_reason"] == er]
        if sub:
            m[f"V7_{er}_n_mean_abs_end"] = [len(sub), st.mean(abs(x["end"]) for x in sub)]
    # the r1 waterfall (invalid) for comparison
    m["r1_sum_cost_median_win_loss"] = [st.median(x["sum_cost"] for x in W), st.median(x["sum_cost"] for x in Lo)]
    m["r1_share_S_minus_sumcost_below_0"] = sum(x["S"] - x["sum_cost"] < 0 for x in rows) / len(rows)
    m["dist_S_p10_p50_p90"] = [q([x["S"] for x in rows], p) for p in (0.1, 0.5, 0.9)]
    m["dist_L_p10_p50_p90"] = [q([x["L"] for x in rows], p) for p in (0.1, 0.5, 0.9)]
    m["dist_C_win_p10_p50_p90"] = [q([x["C"] for x in W], p) for p in (0.1, 0.5, 0.9)]
    m["dist_C_loss_p10_p50_p90"] = [q([x["C"] for x in Lo], p) for p in (0.1, 0.5, 0.9)]
    return m


def _r(v):
    if isinstance(v, float):
        return v if (abs(v) < 1e-6 and v != 0) or math.isnan(v) else round(v, 4)
    if isinstance(v, list):
        return [_r(x) for x in v]
    return v


def gate(m):
    rows = []
    for key, desc, op, thr in ACCEPTANCE:
        v = m.get(key)
        rows.append({"row": key, "check": desc, "value": _r(v), "rule": f"{op} {thr}", "pass": _cmp(v, op, thr)})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="raw review record files (.jsonl)")
    ap.add_argument("--cal", default="absolute_human", help="calibrator key in calibrator.json, or 'a,b'")
    ap.add_argument("--gate-file", required=True, help="stem of the input that is gated (e.g. paired_smogon, run_b)")
    ap.add_argument("--json", default=None, help="write the full report here")
    a = ap.parse_args()
    stems = {Path(p).stem: p for p in a.inputs}
    if a.gate_file not in stems:
        print(f"--gate-file {a.gate_file!r} is not the stem of any input ({sorted(stems)})", file=sys.stderr)
        sys.exit(2)
    cal = B.load_cal(a.cal)
    report = {"tool": "tools/review/validate_bridge.py", "bridge_version": B.VERSION,
              "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "calibrator": {"name": a.cal, "a": cal.a, "b": cal.b}, "gate_file": a.gate_file,
              "bootstrap": {"n": BOOT_N, "seed": BOOT_SEED, "unit": "game (uid)"}, "files": {}}
    for stem, path in stems.items():
        rows, skipped = load_rows(path, cal)
        if not rows:
            report["files"][stem] = {"path": path, "skipped": skipped, "error": "no usable game-sides"}
            continue
        m = metrics(rows)
        report["files"][stem] = {"path": path, "skipped": skipped, "gated": stem == a.gate_file,
                                 "metrics": {k: _r(v) for k, v in m.items()}, "acceptance": gate(m)}
    g = report["files"][a.gate_file]
    acc = g.get("acceptance") or []
    report["pass"] = bool(acc) and all(r["pass"] for r in acc)
    report["failed_rows"] = [r["row"] for r in acc if not r["pass"]] if acc else ["no usable game-sides"]
    report["informational_notes"] = [
        "Only the gate file is gated; other files (e.g. Draft A run520/paired_default) are reported.",
        "V5_mean_abs_S1_plus_S2_minus_1 was listed as <= 0.06 in research/27 §4.2 on Draft A only; research/28 S1.5 "
        "does not gate it. It is reported here for every file.",
        "V2_share_running_total_S_plus_L_outside_0_1 is why the UI shows signed bars, never a running total.",
    ]
    txt = json.dumps(report, indent=1)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(txt + "\n")
    # console summary
    for stem, f in report["files"].items():
        tag = "GATE" if f.get("gated") else "info"
        if "acceptance" not in f:
            print(f"[{tag}] {stem}: {f.get('error')}")
            continue
        npass = sum(r["pass"] for r in f["acceptance"])
        print(f"[{tag}] {stem}: {f['metrics']['game_sides']} sides, {f['metrics']['V5_pairs']} paired games; "
              f"rows passing {npass}/{len(f['acceptance'])}")
        for r in f["acceptance"]:
            print(f"    {'ok  ' if r['pass'] else 'FAIL'} {r['row']:<40} {r['value']!s:<24} {r['rule']}")
    print(f"pass: {report['pass']}" + ("" if report["pass"] else f" (failed: {', '.join(report['failed_rows'])})"))
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
