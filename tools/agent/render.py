"""Renderer: fill {placeholders} from cited tool fields, formatted by key type (research/27 §4.6).

Numbers are copied by construction: the text the model writes has no digits (verifier rule 3); every number
shown comes from `ToolSession.resolve(ref)` and is formatted here.

Formats:
  turn        "11"
  pts         signed "+17 points" / "−61 points" (U+2212, as bridge.py labels), EXCEPT the slot a polarity phrase
              refers to ("swung {x} against you": the words give the sign, so "17 points"); gap_pts always
              unsigned; "1 point". The phrase-to-slot association is lexicon.nearest_slot, the same one the
              verifier checks (rule 4), so a sign is never dropped from a slot the words do not describe.
  wp          "44%"                      (win chance, already an integer percent)
  prob        "10%", "4.2%", "<1%"       (0..1 probability)
  percentile  "83rd percentile"
  name/text/result/verdict  the string as stored
Units are not doubled: "{x} points" / "{x} luck points" get no extra "points"; "{p}%" / "{p} percent" -> "4.2%".
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lexicon import PLACEHOLDER_RE, nearest_slot, polarity_spans  # noqa: E402
from tools import field_kind, pts_subkind  # noqa: E402

RENDER_VERSION = "agent-render-v2"
MINUS = "−"


def fmt_value(value, kind, polar=False, sub=None, unit=True):
    if value is None:
        return "?"
    if kind == "turn":
        return str(int(value))
    if kind == "pts":
        v = int(value)
        mag = abs(v)
        u = (" point" if mag == 1 else " points") if unit else ""
        if polar or v == 0 or sub == "gap":  # a gap is a magnitude (how much higher the engine rated its option)
            return f"{mag}{u}"
        return f"{'+' if v > 0 else MINUS}{mag}{u}"
    if kind == "wp":
        return f"{int(value)}%"
    if kind == "prob":
        p = float(value)
        if p >= 0.995:
            return "100%"
        if p >= 0.1:
            return f"{int(round(100 * p))}%"
        if p >= 0.01:
            return f"{100 * p:.1f}%"
        return "<1%" if p > 0 else "0%"
    if kind == "percentile":
        n = int(value)
        suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suf} percentile"
    return str(value)


_PCT_AFTER = re.compile(r"^\s*(?:%|percent\b|per\s+cent\b)", re.I)
_PTS_SOON = re.compile(r"^\s*(?:[A-Za-z'\-]+\s+){0,2}points?\b", re.I)
_PCTL_AFTER = re.compile(r"^\s*percentile\b", re.I)


def _resolve(session, ref):
    try:
        rec, field, value = session.resolve(ref)
        kind = field_kind(field)
        tool = session.calls[session.record_call[rec["id"]]]["name"]
        return rec, field, value, kind, tool
    except (KeyError, TypeError):
        return None, None, None, None, None


def polar_slots(text, slots, session):
    """Names of the signed slots that a polarity phrase in this sentence refers to."""
    neutral = PLACEHOLDER_RE.sub(lambda m: "X" * len(m.group(0)), text)
    signed = []
    for m in PLACEHOLDER_RE.finditer(text):
        rec, field, value, kind, tool = _resolve(session, (slots or {}).get(m.group(1)))
        if kind == "pts":
            signed.append((m.group(1), m.start(), m.end()))
    out = set()
    for sgn, strong, a, b, phrase in polarity_spans(neutral):
        near = nearest_slot(a, b, signed)
        if near:
            out.add(near[0])
    return out


def render_sentence(text, slots, session):
    """-> (rendered text, citations [{slot, ref, tool, field, kind, value, shown}]). Unresolvable slots
    render as '?' (the verifier fails them before anything is shown)."""
    polar = polar_slots(text, slots, session)
    out, cites, pos = [], [], 0
    for m in PLACEHOLDER_RE.finditer(text):
        name = m.group(1)
        out.append(text[pos:m.start()])
        ref = (slots or {}).get(name)
        rec, field, value, kind, tool = _resolve(session, ref)
        rest = text[m.end():]
        pos = m.end()
        if rec is None:
            shown = "?"
        elif kind == "pts":
            shown = fmt_value(value, kind, name in polar, pts_subkind(field), unit=not _PTS_SOON.match(rest))
        else:
            shown = fmt_value(value, kind)
            um = _PCT_AFTER.match(rest) if kind in ("wp", "prob") else (
                _PCTL_AFTER.match(rest) if kind == "percentile" else None)
            if um:
                pos = m.end() + um.end()
        out.append(shown)
        cites.append({"slot": name, "ref": ref, "tool": tool, "field": field, "kind": kind, "value": value,
                      "shown": shown})
    out.append(text[pos:])
    s = "".join(out)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    return s, cites


def render(sentences, session, mode=None):
    """Render a submit_explanation payload ({"sentences": [...]} or the list). -> {"text", "sentences", "mode"}."""
    if isinstance(sentences, dict):
        sentences = sentences.get("sentences") or []
    rs = []
    for i, snt in enumerate(sentences or []):
        if not isinstance(snt, dict):
            continue
        sl = snt.get("slots") if isinstance(snt.get("slots"), dict) else {}
        txt, cites = render_sentence(snt.get("text", "") or "", sl, session)
        rs.append({"i": i, "text": txt, "template": snt.get("text", ""), "slots": snt.get("slots") or {},
                   "citations": cites})
    return {"text": " ".join(r["text"] for r in rs), "sentences": rs, "mode": mode, "render_version": RENDER_VERSION}
