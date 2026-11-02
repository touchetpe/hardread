"""Bridge numbers for the UI: Start -> Luck -> Choices (both players) -> Result.

Source of truth, in order:
  1. the "bridge" block that tools/review/review.py writes into its output (tools/review/bridge.py);
  2. tools/review/bridge.from_review() on an older review JSON (with the app's end reason filled in);
  3. a stub with the same definitions (research/27 4.2), only if bridge.py cannot be imported.

Definitions: S = start win probability, L = total luck, R = 1 win / 0 loss, C = R - S - L;
displayed s = round(100 S), l = round(100 L), c = 100 R - s - l (they always add up).
Shown as signed bars from Start to Result, never as a running total.
"""
import copy
import math
import sys
from pathlib import Path

HARDREAD = Path(__file__).resolve().parents[1]
_REVIEW = HARDREAD / "tools" / "review"

try:
    if str(_REVIEW) not in sys.path:
        sys.path.insert(0, str(_REVIEW))
    import bridge as _B
except Exception:  # noqa: BLE001
    _B = None


def _rnd(x):
    return int(math.floor(x + 0.5))


def _stub(review, end_reason=None):
    s = review["summary"]
    res = s.get("result")
    if res == "tie":
        return {"result": "tie"}
    S, L = s.get("start_win_prob"), s.get("total_luck") or 0.0
    if res not in ("win", "loss") or S is None:
        return None
    R = 1.0 if res == "win" else 0.0
    C = R - S - L
    si, li = _rnd(100 * S), _rnd(100 * L)
    tag = None
    curve = s.get("win_prob_curve") or []
    if end_reason in ("forfeit", "timeout") and curve:
        t, wp = curve[-1]
        tag = {"reason": end_reason, "turn": t, "win_prob_then": wp,
               "text": f"Ended by {end_reason} on turn {t} (your win chance then: {_rnd(100 * wp)}%)"}
    return {"S": S, "L": L, "R": R, "C": C, "s": si, "l": li, "c": _rnd(100 * R) - si - li,
            "result": res, "swingy": abs(L) > 0.5 or abs(C) > 1, "end_tag": tag}


def compute(review, end_reason=None):
    """-> the UI's bridge dict: S L C R s l c r, result, available, tie, tag (text), swingy, source."""
    blk, source = review.get("bridge"), "review.py (tools/review/bridge.py)"
    if not blk and _B is not None:
        rv = copy.copy(review)
        rv["meta"] = dict(review.get("meta") or {})
        if end_reason and not rv["meta"].get("end_reason"):
            rv["meta"]["end_reason"] = end_reason
        blk, source = _B.from_review(rv), "tools/review/bridge.py"
    if not blk:
        blk, source = _stub(review, end_reason), "app stub (research/27 4.2)"
    if not blk:   # review.py leaves a tie's result empty and records it in meta.end_reason
        return {"available": False, "tie": (review.get("meta") or {}).get("end_reason") == "tie", "source": source}
    if blk.get("result") == "tie":
        return {"available": False, "tie": True, "source": source}
    tag = blk.get("end_tag")
    out = {k: blk.get(k) for k in ("S", "L", "C", "R", "s", "l", "c")}
    out.update({"r": _rnd(100 * blk["R"]), "available": True, "tie": False, "result": blk.get("result"),
                "swingy": bool(blk.get("swingy")), "tag": tag.get("text") if isinstance(tag, dict) else tag,
                "tag_wp": tag.get("win_prob_then") if isinstance(tag, dict) else None,
                "labels": blk.get("labels"), "source": source})
    return out
