#!/usr/bin/env python3
"""The bridge: Start -> Luck -> Choices (both players) -> Result (research/27 §4.2, research/28 S1.5).

Pure Python (no Foul Play import): runs on system python3 (3.9, validation, app) and in the Foul Play
venv (review.py). One implementation for the product and for its validation.

Per reviewed game-side, with cal = an absolute-scale calibrator, P(win) = sigmoid(a * 0.0125 * A + b):
  S  start win probability          = cal(root_A at team preview)        (review.py summary.start_win_prob)
  L  total luck                     = sum over turns of luck v2 'branch_cond' (review.py summary.total_luck)
  R  result                         = 1 win, 0 loss (a tie gets no bridge)
  C  choices, yours and the opponent's = R - S - L                       (identity, by construction)

Displayed integers (always add up): s = round(100 S), l = round(100 L), c = 100 R - s - l.
'round' is half-up (floor(x + 0.5)), the same as JavaScript's Math.round, so a front end that
re-derives the numbers gets the same integers; the UI should read s/l/c from the review JSON anyway.
|c - round(100 C)| <= 1 always.

Diagnostic split of C (validation and receipts only, never headline bars), same worlds as luck:
  act_t  = mean_w [ sum_c p_c WP(c) / sum_c p_c - WP(A_dec_w) ]   effect of the action pair played, before dice
  drift  = sum_t [ WP_dec(t) - WP_real(t-1) ], WP_real(0) = S      information revealed, pivots, re-estimation
  end    = R - WP_real(last)                                       forfeit/timeout jump, or terminal error
  C == sum_t act_t + drift + end (exact telescoping identity)

Entry points:
  compose(S, L, R, ...)        the bridge from three numbers (+ optional end tag and split)
  from_review(review_json)     the product path: review.py output -> bridge block (review.py stores it as "bridge")
  from_record(record, cal)     the validation path: a human_eval.py / luck-farm record with raw luck worlds
  end_info(log_lines)          (end_reason, last_turn) from a Showdown protocol log
"""
import json
import math
from pathlib import Path

K = 0.0125  # poke-engine's sigmoid slope (value.py)
VERSION = 1
CAL_PATH = Path(__file__).resolve().parent / "calibrator.json"
LUCK_LABEL = "Luck (crits, misses, rolls, speed ties)"
CHOICES_LABEL = "Choices, yours and your opponent's"
CHOICES_FOOTNOTE = ("everything that wasn't dice: both players' moves and switches, what each side revealed, "
                    "and resigning")
SWINGY_TEXT = "unusually swingy game"


def sigmoid(x):
    """Same clamp as luck.py, so luck values match the review's to the last bit."""
    return 1 / (1 + math.exp(-max(-60, min(60, x))))


class Cal:
    """P(win) = sigmoid(a * K * A + b) on the absolute value scale (value.py)."""

    def __init__(self, a, b, name=None):
        self.a, self.b, self.name = float(a), float(b), name

    def __call__(self, A):
        return sigmoid(self.a * K * A + self.b)

    def __repr__(self):
        return f"Cal({self.name}: a={self.a}, b={self.b})"


def load_cal(name="absolute_human", path=None):
    """A calibrator by key in calibrator.json ('absolute', 'absolute_human', 'absolute_human_b', ...),
    or inline 'a,b'."""
    if "," in name:
        a, b = name.split(",", 1)
        return Cal(a, b, name)
    d = json.loads(Path(path or CAL_PATH).read_text())
    if name not in d or "a" not in d[name]:
        raise KeyError(f"calibrator {name!r} not in {path or CAL_PATH} (keys: {sorted(d)})")
    return Cal(d[name]["a"], d[name]["b"], name)


def rnd(x):
    """Half-up rounding to an integer (JavaScript Math.round)."""
    return int(math.floor(x + 0.5))


def display(S, L, R):
    """Integer points that always add up: s + l + c == 100 R."""
    s, l = rnd(100 * S), rnd(100 * L)
    return s, l, rnd(100 * R) - s - l


def _signed(n):
    return f"+{n}" if n > 0 else (f"−{-n}" if n < 0 else "0")


def end_tag(end_reason, end_turn=None, last_wp=None):
    """'Ended by forfeit on turn N (your win chance then: X%)' for forfeits and timeouts, else None."""
    if end_reason not in ("forfeit", "timeout"):
        return None
    word = "forfeit" if end_reason == "forfeit" else "timeout"
    txt = f"Ended by {word}"
    if end_turn is not None:
        txt += f" on turn {end_turn}"
    if last_wp is not None:
        txt += f" (your win chance then: {rnd(100 * last_wp)}%)"
    return {"reason": end_reason, "turn": end_turn,
            "win_prob_then": None if last_wp is None else round(last_wp, 4), "text": txt}


def compose(S, L, R, end_reason=None, end_turn=None, last_wp=None, split=None):
    """The bridge block. R must be 0 or 1 (ties have no bridge: use tie_block())."""
    if R not in (0, 1, 0.0, 1.0):
        raise ValueError(f"R must be 0 or 1, got {R!r}")
    R = float(R)
    C = R - S - L
    s, l, c = display(S, L, R)
    out = {
        "version": VERSION,
        "S": S, "L": L, "R": R, "C": C,
        "s": s, "l": l, "c": c,
        "result": "win" if R == 1 else "loss",
        "swingy": abs(L) > 0.5 or abs(C) > 1,
        "end_tag": end_tag(end_reason, end_turn, last_wp),
        "labels": {
            "start": f"Start {s}%",
            "luck": f"{LUCK_LABEL}: {_signed(l)}",
            "choices": f"{CHOICES_LABEL}: {_signed(c)}",
            "choices_footnote": CHOICES_FOOTNOTE,
            "result": f"Result: {'win' if R == 1 else 'loss'}",
        },
    }
    if out["swingy"]:
        out["labels"]["swingy"] = SWINGY_TEXT
    if split is not None:
        out["split"] = split
    return out


def tie_block():
    return {"version": VERSION, "result": "tie", "labels": {"result": "Result: tie"}, "note": "no bridge for a tie"}


# ------------------------------------------------------------------ product path (review.py output)

def end_info(log_lines):
    """(end_reason, last_turn) from protocol lines. end_reason: forfeit / timeout / disconnect (same rule as
    tools/human/parse_replays.py), 'ko' if the log has |win| without one of those, 'tie', or None (unfinished)."""
    reason, last_turn, won, tie = None, None, False, False
    for ln in log_lines:
        p = ln.split("|")
        if len(p) < 2:
            continue
        t = p[1]
        if t == "turn" and len(p) > 2:
            try:
                last_turn = int(p[2])
            except ValueError:
                pass
        elif t == "-message" and reason is None:
            txt = p[2] if len(p) > 2 else ""
            if "forfeited" in txt:
                reason = "forfeit"
            elif "inactivity" in txt or "timer" in txt.lower():
                reason = "timeout"
            elif "disconnect" in txt.lower():
                reason = "disconnect"
        elif t == "win":
            won = True
        elif t == "tie":
            tie = True
    if reason is None:
        reason = "ko" if won else ("tie" if tie else None)
    return reason, last_turn


def from_review(review):
    """Bridge block from review.py output (uses only what the review shows: summary + meta).
    Returns None when the review has no result or no start probability."""
    s = review.get("summary") or {}
    meta = review.get("meta") or {}
    res = s.get("result")
    if res == "tie":
        return tie_block()
    if res not in ("win", "loss") or s.get("start_win_prob") is None:
        return None
    curve = s.get("win_prob_curve") or []
    last_wp = curve[-1][1] if curve else None
    end_turn = meta.get("end_turn") or (curve[-1][0] if curve else None)
    return compose(float(s["start_win_prob"]), float(s.get("total_luck") or 0.0), 1.0 if res == "win" else 0.0,
                   end_reason=meta.get("end_reason"), end_turn=end_turn, last_wp=last_wp)


# ------------------------------------------------------------------ validation path (raw luck records)

def _world_vals(w, cal):
    """(expected WP over chance classes, realised WP, WP at the decision) for one sampled world.
    Same arithmetic as luck.world_luck (luck = real - expected); None if no realised class."""
    cls = w["classes"]
    tot = sum(c["p"] for c in cls) or 1.0
    pr = sum(c["p_real"] for c in cls)
    if pr <= 0:
        return None
    exp_ = sum(c["p"] * cal(c["A"]) for c in cls) / tot
    real = sum(c["p_real"] * cal(c["A"]) for c in cls) / pr
    dec = cal(w["A_dec"]) if w.get("A_dec") is not None else None
    return exp_, real, dec


def turn_parts(rec, cal):
    """One turn's luck ('branch_cond': accepted worlds, all worlds if none accepted), action effect and
    decision WP. Luck equals luck.turn_luck(rec, cal, 'branch_cond'); a turn with no usable world counts 0."""
    ws = rec.get("worlds") or []
    acc = [w for w in ws if w.get("accepted")] or ws
    vals = [v for v in (_world_vals(w, cal) for w in acc) if v is not None]
    if not vals:
        return None
    luck = sum(r - e for e, r, d in vals) / len(vals)
    vd = [v for v in vals if v[2] is not None]
    act = sum(e - d for e, r, d in vd) / len(vd) if vd else 0.0
    dec = sum(d for e, r, d in vd) / len(vd) if vd else None
    return {"turn": rec["turn"], "k": rec.get("k", 0), "luck": luck, "act": act, "dec": dec}


def start_prob(record, cal):
    pre = next((d for d in record.get("decisions") or [] if d.get("kind") == "teampreview"), None)
    if pre is None or pre.get("root_A") is None:
        return None
    return cal(pre["root_A"])


def from_record(record, cal, with_split=True):
    """Bridge block from a raw review record (human_eval.py run/farm output: decisions + luck worlds +
    outcome + meta). Adds 'split' {act, drift, end, turns}. None if the record has no team-preview value."""
    if "error" in record:
        return None
    S = start_prob(record, cal)
    if S is None:
        return None
    parts = [p for p in (turn_parts(r, cal) for r in sorted(record.get("luck") or [],
                                                             key=lambda x: (x["turn"], x.get("k", 0))))
             if p is not None]
    L = sum(p["luck"] for p in parts)
    R = float(record["outcome"])
    act = sum(p["act"] for p in parts)
    drift, prev_real = 0.0, S
    for p in parts:
        if p["dec"] is not None:
            drift += p["dec"] - prev_real
            prev_real = p["dec"] + p["act"] + p["luck"]
        else:
            prev_real = prev_real + p["act"] + p["luck"]
    end = R - prev_real
    meta = record.get("meta") or {}
    split = {"act": act, "drift": drift, "end": end, "turns": len(parts)} if with_split else None
    # 'your win chance then' = the last turn-start win probability, as review.py's win_prob_curve
    moves = [d for d in record.get("decisions") or [] if d.get("kind") == "move" and d.get("root_A") is not None]
    last_wp = cal(moves[-1]["root_A"]) if moves else None
    return compose(S, L, R, end_reason=meta.get("end_reason"), end_turn=meta.get("turns"), last_wp=last_wp,
                   split=split)


def main():
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Print the bridge block of a review.py JSON (or of raw records).")
    ap.add_argument("path", help="review.py output JSON, or a .jsonl of raw review records")
    ap.add_argument("--cal", default="absolute_human", help="calibrator for raw records")
    a = ap.parse_args()
    if a.path.endswith(".jsonl"):
        cal = load_cal(a.cal)
        for line in open(a.path):
            r = json.loads(line)
            b = from_record(r, cal)
            print(json.dumps({"uid": r.get("uid"), "side": r.get("side"), "bridge": b}))
    else:
        json.dump(from_review(json.loads(Path(a.path).read_text())), sys.stdout, indent=1)
        print()


if __name__ == "__main__":
    main()
