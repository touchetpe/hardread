#!/usr/bin/env python3
"""Tests for tools/review/bridge.py (research/28 S1.5).

  python3 tools/review/test_bridge.py        # from hardread/; also runs under pytest

1. Identity on 50 random reviewed game-sides (human_eval records): S + L + C = R and act + drift + end = C to 1e-9;
   L equals luck.py's 'branch_cond' total (reference copy of its arithmetic; luck.py itself when importable).
2. Integer rounding: s + l + c = 100 R always; |c - round(100 C)| <= 1; half-up = JavaScript Math.round.
3. UI JSON: every review.py output with a 'bridge' block has (s, l, c) = bridge.from_review(review)'s; and the
   review path (rounded summary fields, what the UI shows) agrees with the exact path within 1 point.
4. end_info / end_tag on synthetic logs.
"""
import glob
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # hardread/
sys.path.insert(0, str(HERE))
import bridge as B  # noqa: E402

HUMAN_FILES = [ROOT / "scratch_ds/review/human/paired_smogon.jsonl", ROOT / "scratch_ds/review/human/run520.jsonl"]
REVIEW_GLOB = str(ROOT / "scratch_ds/review/games/*.json")
N_SIDES, SEED = 50, 20261027


def _sample_sides(n=N_SIDES, seed=SEED):
    lines = []
    for f in HUMAN_FILES:
        if f.exists():
            with open(f) as fh:
                lines += [ln for ln in fh if '"error"' not in ln[:200]]
    if not lines:
        return []
    rng = random.Random(seed)
    out = []
    for ln in rng.sample(lines, min(len(lines), 4 * n)):
        r = json.loads(ln)
        if "error" in r or r.get("outcome") not in (0, 1):
            continue
        out.append(r)
        if len(out) == n:
            break
    return out


# reference copy of luck.py's arithmetic (world_luck / turn_luck 'branch_cond'), kept literal on purpose
def _ref_world_luck(w, cal):
    tot = sum(c["p"] for c in w["classes"]) or 1.0
    preal = sum(c["p_real"] for c in w["classes"])
    if preal <= 0:
        return None
    wp_real = sum(c["p_real"] * cal(c["A"]) for c in w["classes"]) / preal
    wp_exp = sum(c["p"] * cal(c["A"]) for c in w["classes"]) / tot
    return wp_real - wp_exp


def _ref_turn_luck(rec, cal):
    ws = rec.get("worlds") or []
    if not ws:
        return 0.0
    acc = [w for w in ws if w["accepted"]]
    ws = acc or ws
    vals = [x for x in (_ref_world_luck(w, cal) for w in ws) if x is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _luck_module():
    try:
        import luck  # needs the Foul Play venv
        return luck
    except Exception:
        return None


def _review_like(r, cal):
    """What review.py would output for this record's summary (its rounding), without rerunning the search."""
    S = B.start_prob(r, cal)
    tl = [round(_ref_turn_luck(rec, cal), 4) for rec in r.get("luck") or [] if rec.get("worlds")]
    moves = [d for d in r["decisions"] if d.get("kind") == "move" and d.get("root_A") is not None]
    return {"meta": {"end_reason": (r.get("meta") or {}).get("end_reason"), "end_turn": (r.get("meta") or {}).get("turns")},
            "summary": {"result": "win" if r["outcome"] == 1 else "loss", "start_win_prob": round(S, 3),
                        "total_luck": round(sum(tl), 3),
                        "win_prob_curve": [[d["turn"], round(cal(d["root_A"]), 3)] for d in moves]}}


def test_identity_50_random_sides():
    cal = B.load_cal("absolute_human")
    sides = _sample_sides()
    assert len(sides) == N_SIDES, f"need {N_SIDES} reviewed sides in {[str(f) for f in HUMAN_FILES]}, got {len(sides)}"
    lk = _luck_module()
    for r in sides:
        b = B.from_record(r, cal)
        assert b is not None, r["uid"]
        assert abs(b["S"] + b["L"] + b["C"] - b["R"]) <= 1e-9
        sp = b["split"]
        assert abs(sp["act"] + sp["drift"] + sp["end"] - b["C"]) <= 1e-9
        ref_L = sum(_ref_turn_luck(rec, cal) for rec in r.get("luck") or [])
        assert abs(ref_L - b["L"]) <= 1e-12, (r["uid"], ref_L, b["L"])
        if lk is not None:
            lcal = lk.AbsCal(cal.a, cal.b)
            assert abs(sum(lk.turn_luck(rec, lcal, "branch_cond") for rec in r.get("luck") or []) - b["L"]) <= 1e-9
        assert b["s"] + b["l"] + b["c"] == 100 * int(b["R"])
        assert abs(b["c"] - B.rnd(100 * b["C"])) <= 1
        assert b["result"] == ("win" if r["outcome"] == 1 else "loss")
    return "luck.py cross-check: " + ("yes" if lk is not None else "no (system python; reference copy used)")


def test_integer_rounding():
    # JavaScript Math.round parity (half-up, including negatives)
    for x, js in [(50.5, 51), (-18.5, -18), (0.5, 1), (-0.5, 0), (52.49, 52), (-34.51, -35), (99.5, 100)]:
        assert B.rnd(x) == js, (x, B.rnd(x), js)
    rng = random.Random(1)
    grid = [0.005 * i for i in range(201)]
    cases = [(S, L, R) for S in grid[::7] for L in [-1 + 0.005 * i for i in range(0, 401, 3)] for R in (0, 1)]
    cases += [(rng.random(), rng.uniform(-1.2, 1.2), rng.choice((0, 1))) for _ in range(20000)]
    for S, L, R in cases:
        b = B.compose(S, L, R)
        assert b["s"] + b["l"] + b["c"] == 100 * R
        assert abs(b["c"] - B.rnd(100 * b["C"])) <= 1
        assert b["swingy"] == (abs(L) > 0.5 or abs(R - S - L) > 1)
    b = B.compose(0.52, -0.18, 0)
    assert (b["s"], b["l"], b["c"]) == (52, -18, -34)
    assert b["labels"]["start"] == "Start 52%"
    assert b["labels"]["luck"].endswith(": −18") and b["labels"]["choices"].endswith(": −34")
    assert b["labels"]["result"] == "Result: loss"
    try:
        B.compose(0.5, 0.0, 0.5)
        raise AssertionError("a tie must not get a bridge")
    except ValueError:
        pass


def test_ui_json_equals_bridge():
    cal = B.load_cal("absolute_human")
    # (a) real review.py outputs that carry the bridge block (the UI's JSON)
    n_real = 0
    for f in sorted(glob.glob(REVIEW_GLOB)):
        try:
            rv = json.loads(Path(f).read_text())
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(rv, dict) or "bridge" not in rv or rv["bridge"] is None:
            continue
        ui, ref = rv["bridge"], B.from_review(rv)
        assert (ui["s"], ui["l"], ui["c"]) == (ref["s"], ref["l"], ref["c"]), f
        assert ui["s"] + ui["l"] + ui["c"] == 100 * int(ui["R"]), f
        assert ui["s"] == B.rnd(100 * rv["summary"]["start_win_prob"]) and ui["l"] == B.rnd(100 * rv["summary"]["total_luck"])
        n_real += 1
    if n_real == 0:
        print("  note: no review.py output with a 'bridge' block under scratch_ds/review/games/ (run review.py once); "
              "checking the synthetic review path only")
    # (b) the review path (rounded summary fields) vs the exact path, on the same 50 sides
    for r in _sample_sides():
        exact = B.from_record(r, cal)
        ui = B.from_review(_review_like(r, cal))
        assert ui["s"] + ui["l"] + ui["c"] == 100 * int(ui["R"])
        assert abs(ui["s"] - exact["s"]) <= 1 and abs(ui["l"] - exact["l"]) <= 1 and abs(ui["c"] - exact["c"]) <= 2
        assert (ui["end_tag"] is None) == (exact["end_tag"] is None)
    return n_real


def test_end_info_and_tag():
    base = ["|player|p1|a|", "|player|p2|b|", "|turn|1", "|move|p1a: X|Tackle|p2a: Y", "|turn|2", "|turn|3"]
    assert B.end_info(base + ["|-message|b forfeited.", "|win|a"]) == ("forfeit", 3)
    assert B.end_info(base + ["|-message|b lost due to inactivity.", "|win|a"]) == ("timeout", 3)
    assert B.end_info(base + ["|faint|p2a: Y", "|win|a"]) == ("ko", 3)
    assert B.end_info(base + ["|tie"]) == ("tie", 3)
    assert B.end_info(base) == (None, 3)
    t = B.end_tag("forfeit", 14, 0.123)
    assert t["text"] == "Ended by forfeit on turn 14 (your win chance then: 12%)"
    assert B.end_tag("ko", 14, 0.1) is None
    b = B.from_review({"meta": {"end_reason": "timeout", "end_turn": 9},
                       "summary": {"result": "win", "start_win_prob": 0.48, "total_luck": 0.21,
                                   "win_prob_curve": [[8, 0.7], [9, 0.81]]}})
    assert (b["s"], b["l"], b["c"]) == (48, 21, 31) and b["end_tag"]["text"].endswith("(your win chance then: 81%)")
    assert B.from_review({"summary": {"result": "tie", "start_win_prob": 0.5}})["result"] == "tie"
    assert B.from_review({"summary": {"result": None}}) is None


def main():
    tests = [test_identity_50_random_sides, test_integer_rounding, test_ui_json_equals_bridge, test_end_info_and_tag]
    failed = 0
    for t in tests:
        try:
            extra = t()
            note = f" ({extra} review.py outputs checked)" if isinstance(extra, int) else (f" ({extra})" if extra else "")
            print(f"ok    {t.__name__}{note}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
