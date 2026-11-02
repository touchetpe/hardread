"""Deterministic template explanations (the fallback after one failed repair; research/27 §4.6).

Templates are submit_explanation payloads with slots over the same tool records, so they go through the same
renderer and verifier ("Glimmer selects; templates phrase"): when the model already fetched key moments in
its session, the templates phrase those; otherwise they fetch the defaults themselves.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

Q_KINDS = ("luck_or_choices", "biggest_luck", "disagreement", "turn", "turning_point")


def question_kind(question):
    q = (question or "").lower()
    if "turning point" in q:  # checked first: its instructions also mention the engine's preference
        return "turning_point", None
    m = re.search(r"\bturn\s+(\d+)", q)
    if m:
        return "turn", int(m.group(1))
    if "disagree" in q or "engine prefer" in q or "should i have" in q or "better move" in q:
        return "disagreement", None
    if "biggest" in q and ("luck" in q or "hax" in q):
        return "biggest_luck", None
    return "luck_or_choices", None


def _records(session, tool, kind=None):
    """Records already fetched by the model in this session (its selection), newest call first."""
    out = []
    for c in reversed(session.calls):
        if c["name"] != tool or "error" in (c["output"] or {}):
            continue
        if kind is not None and (c["args"] or {}).get("kind") != kind:
            continue
        out.append(c["output"])
    return out


def _summary(session):
    got = _records(session, "get_summary")
    return got[0] if got else session.call("get_summary", {})


def _moments(session, kind):
    got = _records(session, "list_key_moments", kind)
    out = got[0] if got else session.call("list_key_moments", {"kind": kind, "k": 2})
    return out.get("moments") or []


def s_summary(rec):
    out = []
    rid = rec["id"]
    if "start_wp_pct" in rec:
        out.append({"text": "You started this game with a {s} win chance.", "slots": {"s": f"{rid}.start_wp_pct"}})
    if "luck_pts" in rec and "choices_pts" in rec:
        out.append({"text": "Luck: {l}. Choices, yours and your opponent's together: {c}.",
                    "slots": {"l": f"{rid}.luck_pts", "c": f"{rid}.choices_pts"}})
    elif "luck_pts" in rec:
        out.append({"text": "Luck over the whole game: {l}.", "slots": {"l": f"{rid}.luck_pts"}})
    return out


def s_luck_moment(m):
    rid = m["id"]
    out = []
    if m.get("luck_pts"):
        word = "toward you" if m["luck_pts"] > 0 else "against you"
        out.append({"text": "On turn {t}, luck swung {x} " + word + ".",
                    "slots": {"t": f"{rid}.turn", "x": f"{rid}.luck_pts"}})
    if m.get("realised_prob") is not None:
        out.append({"text": "What happened on that turn had a {p} chance.", "slots": {"p": f"{rid}.realised_prob"}})
    return out


def s_disagreement(m):
    rid = m["id"]
    out = [{"text": "On turn {t}, you chose {chosen}; the engine preferred {best} by {g}.",
            "slots": {"t": f"{rid}.turn", "chosen": f"{rid}.chosen", "best": f"{rid}.engine_best",
                      "g": f"{rid}.gap_pts"}}]
    if "engine_best_wp_pct" in m and "chosen_wp_pct" in m:
        out.append({"text": "It rated {best} at a {a} win chance and {chosen} at a {b} win chance.",
                    "slots": {"best": f"{rid}.engine_best", "a": f"{rid}.engine_best_wp_pct",
                              "chosen": f"{rid}.chosen", "b": f"{rid}.chosen_wp_pct"}})
    return out


def s_turn(rec):
    rid = rec["id"]
    out = []
    if "your_action" in rec and "opp_action" in rec:
        out.append({"text": "On turn {t}, you used {a} and your opponent used {o}.",
                    "slots": {"t": f"{rid}.turn", "a": f"{rid}.your_action", "o": f"{rid}.opp_action"}})
    if "wp_before_pct" in rec and "wp_after_pct" in rec:
        out.append({"text": "Your win chance went from {w0} to {w1}.",
                    "slots": {"w0": f"{rid}.wp_before_pct", "w1": f"{rid}.wp_after_pct"}})
    if rec.get("luck_pts"):
        word = "toward you" if rec["luck_pts"] > 0 else "against you"
        out.append({"text": "Luck on that turn swung {x} " + word + ".", "slots": {"x": f"{rid}.luck_pts"}})
    return out


def s_turning_point(m, gradual=False):
    """The largest win-chance move, what made it, and the engine's preference if the page displays one."""
    rid = m["id"]
    out = []
    lead = "No single turn stands out in this game; the largest move toward the final result came on turn {t}" \
        if gradual else "The turning point was turn {t}"
    slots = {"t": f"{rid}.turn", "a": f"{rid}.wp_before_pct", "b": f"{rid}.wp_after_pct"}
    if m.get("what_happened"):
        lead += ", when {w}"
        slots["w"] = f"{rid}.what_happened"
    out.append({"text": lead + ": your win chance went from {a} to {b}.", "slots": slots})
    if m.get("main_cause") == "luck":
        if m.get("event"):
            out.append({"text": "Luck on that turn was {l}, the main cause of that move: {e}.",
                        "slots": {"l": f"{rid}.luck_pts", "e": f"{rid}.event"}})
        else:
            out.append({"text": "Luck on that turn was {l}, the main cause of that move.",
                        "slots": {"l": f"{rid}.luck_pts"}})
    elif "luck_pts" in m:
        out.append({"text": "Luck on that turn was {l}; most of the move came from choices, yours and your "
                            "opponent's ({c}).", "slots": {"l": f"{rid}.luck_pts", "c": f"{rid}.choices_pts"}})
    elif "choices_pts" in m:
        out.append({"text": "That move came from choices, yours and your opponent's ({c}).",
                    "slots": {"c": f"{rid}.choices_pts"}})
    if "engine_best" in m and "gap_pts" in m:
        out.append({"text": "Instead of {chosen}, the engine preferred {best} by {g}.",
                    "slots": {"chosen": f"{rid}.chosen", "best": f"{rid}.engine_best", "g": f"{rid}.gap_pts"}})
    return out


def _turning_point(session):
    got = _records(session, "list_key_moments", "win_chance")
    out = got[0] if got else session.call("list_key_moments", {"kind": "win_chance", "k": 3})
    ms = out.get("moments") or []
    return s_turning_point(ms[0], gradual=bool(out.get("note"))) if ms else []


def build(session, question):
    """-> submit_explanation payload {"sentences": [...]} (at most 4 sentences)."""
    kind, turn = question_kind(question)
    sents = []
    if kind == "turning_point":
        sents = _turning_point(session)
    elif kind == "turn":
        got = [o for o in _records(session, "get_turn") if o.get("turn") == turn]
        rec = got[0] if got else session.call("get_turn", {"turn": turn})
        if "error" not in rec:
            sents = s_turn(rec)
    elif kind == "disagreement":
        ms = _moments(session, "disagreement")
        if ms:
            sents = s_disagreement(ms[0])
        else:
            empty_text = ("This review shows no disagreements that met its display rules."
                          if session.offered is not None else
                          "In balanced positions, the engine did not prefer another option by a clear margin "
                          "in this game.")
            sents = [{"text": empty_text, "slots": {}}]
    elif kind == "biggest_luck":
        ms = _moments(session, "luck")
        sents = s_luck_moment(ms[0]) if ms else [{"text": "No single turn had a clear luck swing in this game.",
                                                  "slots": {}}]
    else:
        sents = s_summary(_summary(session))
        ms = _moments(session, "luck")
        if ms:
            sents += s_luck_moment(ms[0])[:1]
    if not sents:
        sents = s_summary(_summary(session))
    return {"sentences": sents[:4]}
