"""Deterministic verifier for submit_explanation payloads (research/27 §4.6, with research/28 §0.1 fix).

An answer passes only if every rule holds (plus the structural rule 0):
  0. structure: 1-4 sentences, each {"text": str, "slots": {name: "#id.field"}}; every {placeholder} has a slot.
  1. every slot reference resolves to an existing scalar field of a tool output from this session;
  2. the slot's key type is allowed for its phrase class (lexicon.PHRASE_CLASSES: luck words -> *luck*/*swing*
     points; "chance" -> p/*_prob; "win chance"/"start" -> *_wp_pct; "prefers/disagrees" -> gap_pts; "choices"
     -> choices_pts; "turn {t}" -> a turn); every numeric slot needs a phrase that says what it is;
     choices_pts is never "your choices" (it is both players');
  3. the free text has no digits and no number words, EXCEPT digits inside a species/move name that appears in
     a cited tool output (e.g. "Porygon2"; research/28 §0.1 row §4.6); a unit right after a placeholder
     ("{p} percent") is part of the slot, not a number word;
  4. polarity words ("toward you", "against you", "helped", "cost", ...) match the sign of the nearest bound
     signed value (pts; gap_pts counts as a cost); a direction claim needs a signed slot in its sentence;
  5. every species or move named in free text appears in a cited tool output (an output containing a record
     the answer cites), not just in the public log;
  6. verdict words and their negations appear only through a {verdict} slot bound to get_verdict;
  7. a probability below one half (prob fields; win chances below 50%) is never stated with certainty words;
     its sentence carries a hedge ("chance", "likely", "might", "only", ...).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lexicon import (CERTAIN_RE, DIGIT_RE, HEDGE_RE, LEXICON_VERSION, NUMBER_WORD_RE, NUMERIC_KINDS,  # noqa: E402
                     ONE_COUNT_RE, PHRASE_RE, PLACEHOLDER_RE, TURN_ADJ_RE, UNIT_AFTER_PLACEHOLDER_RE, VERDICT_RE,
                     WEAK_CERTAIN_RE, YOUR_CHOICES_RE, nearest_slot, polarity_spans)
from names import find_entities, to_id  # noqa: E402
from tools import field_kind, pts_subkind  # noqa: E402

VERIFIER_VERSION = "agent-verifier-v2 (" + LEXICON_VERSION + ")"
MAX_SENTENCES = 4
MAX_CHARS = 300


def _fail(fails, rule, i, msg, slot=None):
    fails.append({"rule": rule, "sentence": i, "slot": slot, "message": msg})


def _neutral(text):
    """Template text with placeholders replaced by a neutral token of the same length (keeps positions)."""
    return PLACEHOLDER_RE.sub(lambda m: "X" * len(m.group(0)), text)


def _free_text(text):
    """Text without placeholders; a unit right after a placeholder ("{p} percent") goes with it."""
    return PLACEHOLDER_RE.sub(" ", UNIT_AFTER_PLACEHOLDER_RE.sub(r"\1", text))


def _sign(kind, sub, value):
    if kind != "pts" or value is None:
        return None
    if sub == "gap":
        return -1 if value > 0 else 0  # a gap is a cost to the player
    return (value > 0) - (value < 0)


def _entity_ok(eid, cited):
    return any(c == eid or c.startswith(eid) or eid.startswith(c) for c in cited)


def verify(payload, session):
    """-> {"pass": bool, "failures": [...], "n_sentences", "version"}. `payload` is the parsed
    submit_explanation arguments ({"sentences": [...]})."""
    fails = []
    sents = payload.get("sentences") if isinstance(payload, dict) else None
    if not isinstance(sents, list) or not sents:
        _fail(fails, 0, None, "arguments must be {\"sentences\": [ {\"text\": ..., \"slots\": {...}} ]} with at "
                              "least one sentence")
        return {"pass": False, "failures": fails, "n_sentences": 0, "version": VERIFIER_VERSION}
    if len(sents) > MAX_SENTENCES:
        _fail(fails, 0, None, f"at most {MAX_SENTENCES} sentences")
    cited_ids = set()
    resolved = []  # per sentence: {name: (kind, sub, value, rid, tool)}
    for i, s in enumerate(sents):
        res = {}
        if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not s["text"].strip():
            _fail(fails, 0, i, "each sentence needs a non-empty \"text\" string")
            resolved.append(res)
            continue
        slots = s.get("slots")
        if slots is None:
            slots = {}
        if not isinstance(slots, dict):
            _fail(fails, 0, i, "\"slots\" must be an object mapping placeholder names to \"#id.field\"")
            slots = {}
        if len(s["text"]) > MAX_CHARS:
            _fail(fails, 0, i, f"sentence longer than {MAX_CHARS} characters")
        for name in PLACEHOLDER_RE.findall(s["text"]):
            if name in res:
                continue
            if name not in slots:
                _fail(fails, 1, i, f"placeholder {{{name}}} has no slot", name)
                continue
            try:
                rec, field, value = session.resolve(slots[name])
            except KeyError as e:
                _fail(fails, 1, i, f"slot {name}: {e.args[0]}", name)
                continue
            kind = field_kind(field)
            tool = session.calls[session.record_call[rec["id"]]]["name"]
            res[name] = (kind, pts_subkind(field) if kind == "pts" else None, value, rec["id"], tool, field)
            cited_ids.add(rec["id"])
        resolved.append(res)
    cited_ents = session.cited_entities(cited_ids)

    for i, s in enumerate(sents):
        if not isinstance(s, dict) or not isinstance(s.get("text"), str):
            continue
        text = s["text"]
        res = resolved[i]
        neutral = _neutral(text)
        phs = [(m.group(1), m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)]

        # ---- rule 2: phrase classes
        triggers = []
        for cname, rx, allowed in PHRASE_RE:
            for m in rx.finditer(neutral):
                triggers.append((cname, m.start(), m.end(), allowed))
        # a "win chance" match also matches "chance": drop chance triggers inside win_chance spans
        wc = [(a, b) for c, a, b, _ in triggers if c == "win_chance"]
        triggers = [t for t in triggers if not (t[0] == "chance" and any(a <= t[1] < b for a, b in wc))]
        for name, a, b in phs:
            if name not in res:
                continue
            kind, sub, value, rid, tool, field = res[name]
            if TURN_ADJ_RE.search(neutral[:a]):
                if kind != "turn":
                    _fail(fails, 2, i, f"{{{name}}} follows 'turn' but cites {field} ({kind}), not a turn number", name)
                continue
            if kind == "turn":
                _fail(fails, 2, i, f"{{{name}}} cites a turn number; write it as 'turn {{{name}}}'", name)
                continue
            if kind not in NUMERIC_KINDS:
                continue
            best = None
            for cname, ta, tb, allowed in triggers:
                d = a - tb if tb <= a else (ta - b if ta >= b else 0)
                key = (d, 0 if tb <= a else 1)
                if best is None or key < best[0]:
                    best = (key, cname, allowed)
            if best is None:
                _fail(fails, 2, i, f"{{{name}}} ({field}) has no words saying what the number is (e.g. 'luck', "
                                   f"'win chance', 'chance', 'preferred by')", name)
                continue
            _, cname, allowed = best
            if (kind, sub) not in allowed and (kind, None) not in allowed:
                _fail(fails, 2, i, f"{{{name}}} cites {field} but is phrased as '{cname}'; that phrase needs "
                                   f"{sorted(k + ('_' + s_ if s_ else '') for k, s_ in allowed)}", name)
            if kind == "pts" and sub == "choices" and YOUR_CHOICES_RE.search(neutral):
                _fail(fails, 2, i, "choices_pts is both players' choices together; never call it 'your choices'",
                      name)

        # ---- rule 3: no digits / number words, except digits inside cited species/move names
        free = _free_text(text)
        stripped = free
        for nm, kind_, eid, start in find_entities(free):
            if DIGIT_RE.search(nm) and _entity_ok(eid, cited_ents):
                stripped = stripped.replace(nm, " ")
        if DIGIT_RE.search(stripped):
            _fail(fails, 3, i, "free text contains a digit; put every number in a {placeholder}")
        nw = NUMBER_WORD_RE.search(stripped) or ONE_COUNT_RE.search(stripped)
        if nw:
            _fail(fails, 3, i, f"free text contains the number word '{nw.group(0)}'")

        # ---- rule 4: polarity
        signed = [(name, a, b, _sign(res[name][0], res[name][1], res[name][2])) for name, a, b in phs
                  if name in res and res[name][0] == "pts"]
        for sgn, strong, pa, pb, phrase in polarity_spans(neutral):
            if not signed:
                if strong:
                    _fail(fails, 4, i, f"'{phrase}' claims a direction but the sentence binds no signed "
                                       f"points value (luck_pts, swing_pts, choices_pts, gap_pts)")
                continue
            near = nearest_slot(pa, pb, signed)
            if near[3] != sgn:
                val = res[near[0]][2]
                _fail(fails, 4, i, f"'{phrase}' does not match the sign of {{{near[0]}}} "
                                   f"({res[near[0]][5]} = {val})", near[0])

        # ---- rule 5: species/move names in free text must be in a cited output
        for nm, kind_, eid, start in find_entities(_neutral(text)):
            if not _entity_ok(eid, cited_ents):
                _fail(fails, 5, i, f"'{nm}' ({kind_}) is not in any tool output this answer cites; cite the record "
                                   f"that names it or use a name slot")

        # ---- rule 6: verdict words only via {verdict} bound to get_verdict
        vw = VERDICT_RE.search(free)
        if vw:
            _fail(fails, 6, i, f"verdict word '{vw.group(0)}' in free text; verdicts appear only through a "
                               f"{{verdict}} slot bound to get_verdict")
        for name in res:
            kind, sub, value, rid, tool, field = res[name]
            if kind == "verdict" and tool != "get_verdict":
                _fail(fails, 6, i, f"{{{name}}} is a verdict not bound to get_verdict", name)

        # ---- rule 7: probabilities < 0.5 only with hedges
        low = [name for name in res if (res[name][0] == "prob" and res[name][2] is not None and res[name][2] < 0.5)
               or (res[name][0] == "wp" and res[name][2] is not None and res[name][2] < 50)]
        if low:
            if CERTAIN_RE.search(free):
                _fail(fails, 7, i, f"certainty word '{CERTAIN_RE.search(free).group(0)}' with a probability below "
                                   f"one half ({', '.join('{' + n + '}' for n in low)})", low[0])
            elif WEAK_CERTAIN_RE.search(free) and not HEDGE_RE.search(free):
                _fail(fails, 7, i, f"probability below one half stated as a fact; hedge it (e.g. 'had only a "
                                   f"{{{low[0]}}} chance')", low[0])
    return {"pass": not fails, "failures": fails, "n_sentences": len(sents), "version": VERIFIER_VERSION,
            "cited_records": sorted(cited_ids, key=lambda r: int(r[1:]))}


def failure_text(report):
    """Repair-turn message listing the failures (one line each)."""
    lines = []
    for f in report["failures"]:
        where = "" if f["sentence"] is None else f"sentence {f['sentence'] + 1}: "
        lines.append(f"- rule {f['rule']}: {where}{f['message']}")
    return "\n".join(lines)


def to_id_safe(x):
    return to_id(x)
