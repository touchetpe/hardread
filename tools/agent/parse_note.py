#!/usr/bin/env python3
"""Note parser: the player's free-text note -> per-field belief with editable chips (research/28 S2.4).

  python3 tools/agent/parse_note.py --log REPLAY.json --side p1 --note "I thought Dragapult was Choice Specs" \
      [--review REVIEW.json --turn 11 [--k 0]] [--parser glimmer|keyword] [--url ...] [--out parsed.json]
  python3 tools/agent/parse_note.py --smoke [--url ...] --out scratch_ds/agent/parser_smoke.json

Glimmer (base) is called once, single turn, temperature 0, with one tool `submit_belief` whose species fields
are an enum of the 12 Pokémon in the game. Its output is then normalised against the dex and **grounded**: a
value is kept only if the note itself names it (its name, or an alias such as "scarf" or "sd"). Nothing numeric
is ever produced (the schema has no number fields; EVs/spreads are "not used" in v1, research/27 §4.4), so the
parser cannot invent numbers. The keyword baseline does the same matching without a model.

Belief schema (research/27 §4.4), produced from the chips:
  {"sets": {"<species>": {"item", "ability", "tera", "moves_has": [], "moves_not": []}},
   "expects_action": {"species", "kind": "switch|move|tera", "move"}, "confidence": "sure|think|guess"}
Read check (v1, no counterfactual): rendered by code from decisions[].opponent_policy_top + the log only.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from names import action_name, dex, move_name, species_name, to_id  # noqa: E402

PARSER_VERSION = "parse-note-v4"

ITEM_ALIASES = {
    "scarf": "choicescarf", "specs": "choicespecs", "band": "choiceband", "lefties": "leftovers",
    "lefty": "leftovers", "boots": "heavydutyboots", "hdb": "heavydutyboots", "av": "assaultvest",
    "vest": "assaultvest", "sash": "focussash", "lo": "lifeorb", "orb": "lifeorb", "booster": "boosterenergy",
    "zoom": "zoomlens", "rocky helmet": "rockyhelmet", "helmet": "rockyhelmet",
    "black sludge": "blacksludge", "sludge": "blacksludge", "sitrus": "sitrusberry",
    "banded": "choiceband", "scarfed": "choicescarf", "lum": "lumberry", "air balloon": "airballoon", "balloon": "airballoon", "eviolite": "eviolite",
    "loaded dice": "loadeddice", "dice": "loadeddice", "light clay": "lightclay", "clay": "lightclay",
    "weakness policy": "weaknesspolicy", "wp": "weaknesspolicy", "covert cloak": "covertcloak",
    "cloak": "covertcloak", "expert belt": "expertbelt", "white herb": "whiteherb", "mirror herb": "mirrorherb",
    "throat spray": "throatspray", "punching glove": "punchingglove", "red card": "redcard",
    "eject button": "ejectbutton", "eject pack": "ejectpack", "shed shell": "shedshell", "toxic orb": "toxicorb",
    "flame orb": "flameorb", "safety goggles": "safetygoggles", "goggles": "safetygoggles",
}
MOVE_ALIASES = {
    "sd": "swordsdance", "dd": "dragondance", "qd": "quiverdance", "cm": "calmmind", "np": "nastyplot",
    "eq": "earthquake", "cc": "closecombat", "sucker": "suckerpunch", "knock": "knockoff", "rocks": "stealthrock",
    "sr": "stealthrock", "tspikes": "toxicspikes", "wisp": "willowisp", "wow": "willowisp", "tbolt": "thunderbolt",
    "draco": "dracometeor", "mir": "makeitrain", "spin": "rapidspin", "volt": "voltswitch", "flip": "flipturn",
    "ice beam": "icebeam", "shadow ball": "shadowball", "focus blast": "focusblast",
    "sub": "substitute", "boomburst": "boomburst", "u-turn": "uturn",
    "uturn": "uturn", "trick room": "trickroom", "tr": "trickroom", "nasty plot": "nastyplot",
    "ivy": "ivycudgel", "bolt beak": "boltbeak", "fake out": "fakeout", "pain split": "painsplit",
}
TERA_RE = re.compile(r"\btera(?:stal(?:li[sz]ed?)?)?\s+(?:type\s+)?([A-Za-z]+)", re.I)
CONF_WORDS = [("sure", r"\b(?:sure|certain|definitely|knew|obvious(?:ly)?|100)\b"),
              ("guess", r"\b(?:guess(?:ed)?|maybe|might|hope(?:d)?|gamble|perhaps|could have been)\b"),
              ("think", r"\b(?:think|thought|expected|probably|likely|figured|read|assumed|predicted)\b")]
SWITCH_RE = re.compile(r"\b(?:switch(?:ing|ed)?(?:\s+out)?|go(?:ing)?\s+out|pivot(?:ing)?|bring(?:ing)?\s+in|"
                       r"come\s+in|sack)\b", re.I)
NOT_RE = r"(?:no|not|didn'?t|doesn'?t|don'?t|without|lacks?|lacked|never|wasn'?t|isn'?t)"
OWN_RE = r"(?:\bi\s+(?:used|clicked|went\s+for|chose|picked)|\bmy)"
SPREAD_RE = re.compile(r"\b(?:\d{1,3}\s*(?:hp|atk|def|spa|spd|spe)|evs?|ivs?|nature|jolly|adamant|timid|modest|"
                       r"bold|calm|careful|impish|max\s+speed|speed\s+tier|outspe\w+)\b", re.I)


# ------------------------------------------------------------------ game context

def species_from_log(lines, side):
    """(own six, opponent six) display names from |poke| lines (team preview)."""
    own, opp = [], []
    for ln in lines:
        p = ln.split("|")
        if len(p) > 3 and p[1] == "poke":
            name = p[3].split(",")[0].strip()
            (own if p[2] == side else opp).append(name)
    return own, opp


def load_log(path):
    txt = Path(path).read_text()
    try:
        d = json.loads(txt)
        log = d.get("log", d) if isinstance(d, dict) else d
    except json.JSONDecodeError:
        log = txt
    return log if isinstance(log, list) else str(log).split("\n")


# ------------------------------------------------------------------ grounding / normalisation

def _norm_note(note):
    """Lowercase; keep letters, digits, - and ' plus clause punctuation (attribution needs clause breaks)."""
    s = re.sub(r"([,.;:!?()])", r" \1 ", note.lower())
    return " " + re.sub(r"[^a-z0-9\-' ,.;:!?()]", " ", s) + " "


def _mentions(note_n, phrase):
    phrase = phrase.lower().strip()
    if not phrase:
        return None
    m = re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", note_n)
    return m.group(0).strip() if m else None


def species_evidence(note_n, display):
    """The note's span naming this species: full name, or a distinctive word/prefix/suffix of >= 4 letters
    ('Tusk' for Great Tusk, 'Zama' for Zamazenta, 'Gambit' for Kingambit)."""
    ev = _mentions(note_n, display.lower()) or _mentions(note_n, display.lower().replace("-", " "))
    if ev:
        return ev
    sid = to_id(display)
    base = to_id(display.split("-")[0])
    for tok in set(re.findall(r"[a-z0-9]{4,}", note_n)):
        if tok in ("iron", "great", "roaring", "walking", "scream", "brute", "slither", "sandy", "flutter",
                   "with", "that", "this", "they", "their", "have", "would", "could", "switch", "tera"):
            continue
        if base.startswith(tok) or base.endswith(tok) or sid == tok:
            return tok
        for w in display.lower().replace("-", " ").split():
            if len(w) >= 4 and w == tok and w not in ("iron", "great"):
                return tok
    return None


def item_evidence(note_n, item_id):
    disp = dex()["items"].get(item_id)
    if not disp:
        return None
    ev = _mentions(note_n, disp.lower())
    if ev:
        return ev
    for alias, iid in ITEM_ALIASES.items():
        if iid == item_id and _mentions(note_n, alias):
            return alias
    return None


def move_evidence(note_n, move_id):
    e = dex()["moves"].get(move_id)
    if not e:
        return None
    ev = _mentions(note_n, e["n"].lower()) or _mentions(note_n, e["n"].lower().replace("-", " "))
    if ev:
        return ev
    for alias, mid in MOVE_ALIASES.items():
        if mid == move_id and _mentions(note_n, alias):
            return alias
    return None


def ability_evidence(note_n, ab_id):
    disp = dex()["abilities"].get(ab_id)
    return _mentions(note_n, disp.lower()) if disp else None


def _resolve_item(v):
    i = to_id(v)
    if i in dex()["items"]:
        return i
    return ITEM_ALIASES.get((v or "").lower().strip())


def _resolve_move(v):
    i = to_id(v)
    if i in dex()["moves"]:
        return i
    return MOVE_ALIASES.get((v or "").lower().strip())


def _resolve_species(v, allowed):
    i = to_id(v)
    for s in allowed:
        if to_id(s) == i:
            return s
    return None


# ------------------------------------------------------------------ attribution (Ag-App regression, 2026-10-27)
# A move or item belongs to an opponent's set only if its own clause refers to that opponent (its name, or a
# pronoun after it was named) with an attribution cue, and it is not the player's own ("I used Crunch",
# "my Scarf", "so Crunch was safe"). Moves on the player's side of the review need the species name itself.
CLAUSE_BREAK_RE = re.compile(r"[,.;:!?()]|\b(?:so|because|since|then|but|while|whereas|although|though)\b")
OPP_PRONOUN_RE = re.compile(r"\b(?:it|its|it's|they|their|them|theirs|he|she|his|her|opponent|opp|foe)\b")
CUE_RE = re.compile(r"\b(?:has|had|have|having|with|running|runs|run|ran|carr\w*|hold\w*|held|use|used|uses|"
                    r"using|click\w*|go(?:es|ing)?\s+for|went\s+for|know\w*|pack\w*|lack\w*|no|not|never|without|"
                    r"would|will|could|might|was|were|is|be|as|on)\b|'d\b|n't\b")
# "it had X", "they run X", "she lacked X": a pronoun tightly bound to the move counts as naming the opponent
TIGHT_PRONOUN_RE = re.compile(r"\b(?:it|they|he|she)\s+(?:[\w']+\s+)?(?:has|had|have|runs|ran|run|carries|carried|"
                              r"holds|held|used|uses|knows|knew|packs|packed|lacks|lacked)\s+(?:no\s+|not\s+)?$")
OWN_BEFORE_RE = re.compile(r"(?:\bi\s+(?:[\w']+\s+){0,2}?(?:used|use|clicked|click|went\s+for|go\s+for|chose|picked|"
                           r"had|have|tried|kept)|\bmy|\bme|\bmine)\s+(?:[\w']+\s+)?$")


def clause_before(note_n, pos):
    """Text of the clause that ends at `pos` (from the last clause break)."""
    head = note_n[:pos]
    cut = 0
    for m in CLAUSE_BREAK_RE.finditer(head):
        cut = m.end()
    return head[cut:]


def attributed(note_n, ev, species, opp, own_moves=None, move_id=None):
    """(ok, reason): is this mention (evidence span `ev`) stated about the opponent's `species`?"""
    m = re.search(r"(?<![a-z0-9])" + re.escape(ev) + r"(?![a-z0-9])", note_n)
    if not m:
        return False, "not found in the note"
    pos = m.start()
    clause = clause_before(note_n, pos)
    own_m = OWN_BEFORE_RE.search(note_n[max(0, pos - 40):pos])
    # "I thought Ceruledge had X" / "I guess it had X": the opponent is the subject, so it is not the player's own
    if own_m and not (species_evidence(own_m.group(0), species) or OPP_PRONOUN_RE.search(own_m.group(0))
                      or any(species_evidence(own_m.group(0), o) for o in opp)):
        return False, "the player's own (\"I used\", \"my\")"
    named_here = species_evidence(clause, species) is not None
    if not named_here:
        tight = TIGHT_PRONOUN_RE.search(note_n[max(0, pos - 40):pos])
        if move_id and own_moves and move_id in own_moves and not tight:
            return False, "also on the player's side; the note does not name the opponent with it"
        if not OPP_PRONOUN_RE.search(clause):
            return False, "its clause does not refer to the opponent"
        prev = [(note_n.rfind(" " + e), sp) for sp in opp for e in [species_evidence(note_n[:pos], sp)] if e]
        prev = [x for x in prev if x[0] >= 0]
        if not prev or max(prev)[1] != species:
            return False, f"its clause refers to another Pokémon, not {species}"
    if not CUE_RE.search(clause):
        return False, "no wording links it to the opponent (has, with, used...)"
    return True, None


def own_moves_from_review(review):
    """Move ids on the player's side of a review (own actions and the engine's options for the player)."""
    out = set()
    for d in (review or {}).get("decisions") or []:
        acts = [d.get("action"), d.get("engine_best")] + [o.get("action") for o in d.get("top_alternatives") or []]
        for a in acts:
            if a and not str(a).startswith("switch "):
                out.add(to_id(str(a).replace("-tera", "")))
    return out


# ------------------------------------------------------------------ keyword baseline

def keyword_parse(note, own, opp, own_moves=None):
    """Deterministic baseline: every species/item/move/ability/tera named in the note, attached to the nearest
    preceding opponent species (else the only one named)."""
    note_n = _norm_note(note)
    named = []
    for sp in opp + own:
        ev = species_evidence(note_n, sp)
        if ev:
            named.append((note_n.find(" " + ev) if note_n.find(" " + ev) >= 0 else note_n.find(ev), sp, ev))
    named.sort()
    opp_named = [x for x in named if x[1] in opp]

    def owner(pos):
        prev = [x for x in opp_named if x[0] <= pos]
        if prev:
            return prev[-1][1]
        return opp_named[0][1] if opp_named else None

    sets = {}
    rejected = []  # every candidate the baseline does not keep, with its reason (reported in "dropped")

    def reject(sp, field, value, why):
        rejected.append({"field": field, "value": value, "species": sp, "reason": why})

    def put(sp, field, value, ev, neg=False):
        if sp is None:
            return
        d = sets.setdefault(sp, {})
        if field in ("moves_has", "moves_not"):
            d.setdefault(field, [])
            if value not in d[field]:
                d[field].append(value)
        else:
            d[field] = value
        d.setdefault("_evidence", {})[f"{field}:{value}"] = ev

    for iid, disp in dex()["items"].items():
        ev = item_evidence(note_n, iid)
        if ev:
            sp = owner(note_n.find(ev))
            ok, why = attributed(note_n, ev, sp, opp) if sp else (False, "no opponent Pokémon named before it")
            if ok:
                put(sp, "item", iid, ev)
            else:
                reject(sp, "item", disp, f"not attributed{' to ' + sp if sp else ''}: {why}")
    seen_moves = set()
    spans = []
    for mid, e in dex()["moves"].items():
        ev = move_evidence(note_n, mid)
        if ev:
            m = re.search(r"(?<![a-z0-9])" + re.escape(ev) + r"(?![a-z0-9])", note_n)
            spans.append((m.start(), m.end(), mid, ev))
    spans = [x for x in spans if not any(o[0] <= x[0] and x[1] <= o[1] and (o[1] - o[0]) > (x[1] - x[0])
                                         for o in spans)]  # "Thunder" inside "Thunder Wave"
    mentioned = []  # every move named and not the player's own: candidates for the expected action
    for pos, end, mid, ev in sorted(spans):
        before = note_n[max(0, pos - 40):pos]
        if re.search(OWN_RE + r"\s+(?:[\w']+\s+){0,1}$", before):
            continue  # the player's own move ("I used Defog")
        mentioned.append((mid, ev))
        sp = owner(pos)
        ok, why = attributed(note_n, ev, sp, opp, own_moves, mid) if sp else (False, "no opponent Pokémon named")
        if not ok:  # e.g. "so Crunch was safe": the player's own move, not the opponent's
            reject(sp, "moves", move_name(mid), f"not attributed{' to ' + sp if sp else ''}: {why}")
            continue
        seen_moves.add(mid)
        neg = re.search(r"\b" + NOT_RE + r"\s+(?:[\w']+\s+){0,4}$", before)
        put(sp, "moves_not" if neg else "moves_has", mid, ev)
    for aid in dex()["abilities"]:
        ev = ability_evidence(note_n, aid)
        if ev and aid not in ("noability",):
            put(owner(note_n.find(ev)), "ability", aid, ev)
    for m in TERA_RE.finditer(note):
        t = to_id(m.group(1))
        if t in dex()["types"]:
            put(owner(note_n.find(" tera")), "tera", t, m.group(0))
    exp = None
    sw = SWITCH_RE.search(note_n)
    if sw:
        after = [x for x in opp_named if sw.end() <= x[0] <= sw.end() + 30]  # "switch (out) to X": X is the target
        exp = {"kind": "switch", "species": after[0][1] if after else None}
    else:
        hm = [(mid, ev) for mid, ev in mentioned
              if re.search(r"\b(?:click|use|go\s+for|fire\s+off|throw\s+out|expected|predicted|thought)\b[^.]{0,30}"
                           + re.escape(ev), note_n)]
        if hm:
            exp = {"kind": "move", "move": hm[0][0], "species": owner(note_n.find(hm[0][1]))}
    conf = None
    for c, rx in CONF_WORDS:
        if re.search(rx, note, re.I):
            conf = c
            break
    return {"_rejected": rejected,
            "sets": [{"species": sp, **{k: v for k, v in d.items() if k != "_evidence"}} for sp, d in sets.items()],
            "expects_action": exp, "confidence": conf or "think"}


# ------------------------------------------------------------------ Glimmer submit_belief

def belief_tool(all_species):
    sp = {"type": "string", "enum": all_species}
    return {"type": "function", "function": {
        "name": "submit_belief",
        "description": "What the player's note says they believed about the opponent. Only fields the note states; "
                       "leave everything else out. Names as written in the game (items, moves, abilities, types).",
        "parameters": {"type": "object", "properties": {
            "sets": {"type": "array", "items": {"type": "object", "properties": {
                "species": sp, "item": {"type": "string"}, "ability": {"type": "string"},
                "tera": {"type": "string"},
                "moves_has": {"type": "array", "items": {"type": "string"}},
                "moves_not": {"type": "array", "items": {"type": "string"}}}, "required": ["species"]}},
            "expects_action": {"type": "object",
                               "description": "The opponent action the player expected. kind=switch: species is the "
                                              "opponent Pokémon expected to come IN (not the one leaving). "
                                              "kind=move: move is the move expected, species the user of it.",
                               "properties": {
                                   "species": sp, "kind": {"type": "string", "enum": ["switch", "move", "tera"]},
                                   "move": {"type": "string"}}, "required": ["kind"]},
            "confidence": {"type": "string", "enum": ["sure", "think", "guess"]}},
            "required": ["sets", "confidence"]}}}


PARSER_SYSTEM = ("You turn a competitive Pokémon player's note into the belief it states about the opponent's "
                 "Pokémon. Record only what the note says: items, abilities, Tera types, moves it has or lacks, and "
                 "the action the player expected from the opponent. Use full game names (\"scarf\" -> \"Choice "
                 "Scarf\", \"sd\" -> \"Swords Dance\"). Never add anything the note does not say. Call submit_belief.")


def glimmer_parse(note, own, opp, client, context=None):
    tool = belief_tool(own + opp)
    user = (f"Your team: {', '.join(own)}.\nOpponent's team: {', '.join(opp)}.\n" +
            (f"Context: {context}\n" if context else "") + f"Note: \"{note}\"")
    resp = client.chat([{"role": "system", "content": PARSER_SYSTEM}, {"role": "user", "content": user}],
                       tools=[tool], tool_choice="required", max_tokens=1024)
    call = next((c for c in resp["tool_calls"] if c["name"] == "submit_belief"), None)
    meta = {k: resp[k] for k in ("wall_s", "prompt_tokens", "completion_tokens", "finish_reason")}
    if call is None or call["arguments"] is None:
        return None, dict(meta, error=(call or {}).get("parse_error") or "no submit_belief call")
    return call["arguments"], meta


# ------------------------------------------------------------------ chips (normalise + ground) and belief

def to_chips(raw, note, own, opp, source, own_moves=None):
    """Normalise each field to a dex id and keep it only if the note names it and attributes it to that opponent
    (attributed()). -> (chips, dropped)."""
    note_n = _norm_note(note)
    chips, dropped = [], []

    def chip(sp, field, vid, disp, ev):
        chips.append({"species": sp, "field": field, "value": disp, "id": vid, "evidence": ev, "source": source,
                      "editable": True})

    for st in (raw or {}).get("sets") or []:
        if not isinstance(st, dict):
            dropped.append({"field": "sets", "value": st, "reason": "not an object"})
            continue
        sp = _resolve_species(st.get("species"), own + opp)
        if sp is None:
            dropped.append({"field": "species", "value": st.get("species"), "reason": "not one of the 12 in the game"})
            continue
        if sp in own:
            dropped.append({"field": "species", "value": sp, "reason": "your own Pokémon (its set comes from your "
                                                                       "team export)"})
            continue
        if not species_evidence(note_n, sp):
            dropped.append({"field": "species", "value": sp, "reason": "the note does not name it"})
            continue
        if st.get("item"):
            iid = _resolve_item(st["item"])
            ev = item_evidence(note_n, iid) if iid else None
            ok, why = attributed(note_n, ev, sp, opp) if ev else (False, None)
            if iid and ev and ok:
                chip(sp, "item", iid, dex()["items"][iid], ev)
            elif iid and ev:
                dropped.append({"field": "item", "value": st["item"], "reason": f"not attributed to {sp}: {why}"})
            else:
                dropped.append({"field": "item", "value": st["item"], "reason": "unknown item" if not iid else
                                "the note does not name it"})
        if st.get("ability"):
            aid = to_id(st["ability"])
            ev = ability_evidence(note_n, aid)
            if aid in dex()["abilities"] and ev:
                chip(sp, "ability", aid, dex()["abilities"][aid], ev)
            else:
                dropped.append({"field": "ability", "value": st["ability"], "reason": "not named in the note"})
        if st.get("tera"):
            tid = to_id(st["tera"])
            m = re.search(r"\btera\w*\s+(?:type\s+)?" + re.escape(tid), note_n)
            if tid in dex()["types"] and m:
                chip(sp, "tera", tid, dex()["types"][tid], m.group(0).strip())
            else:
                dropped.append({"field": "tera", "value": st["tera"], "reason": "not named in the note"})
        for field in ("moves_has", "moves_not"):
            for mv in st.get(field) or []:
                mid = _resolve_move(mv)
                ev = move_evidence(note_n, mid) if mid else None
                ok, why = attributed(note_n, ev, sp, opp, own_moves, mid) if ev else (False, None)
                if mid and ev and ok:
                    chip(sp, field, mid, move_name(mid), ev)
                elif mid and ev:
                    dropped.append({"field": field, "value": mv, "reason": f"not attributed to {sp}: {why}"})
                else:
                    dropped.append({"field": field, "value": mv, "reason": "unknown move" if not mid else
                                    "the note does not name it"})
    exp = (raw or {}).get("expects_action")
    if isinstance(exp, dict) and exp.get("kind") in ("switch", "move", "tera"):
        sp = _resolve_species(exp.get("species"), own + opp) if exp.get("species") else None
        mid = _resolve_move(exp.get("move")) if exp.get("move") else None
        ok = True
        if exp["kind"] == "switch" and not SWITCH_RE.search(note):
            ok = False
        if exp["kind"] == "tera" and not re.search(r"\btera", note_n):
            ok = False
        if mid and not move_evidence(note_n, mid):
            mid = None
            if exp["kind"] == "move":
                ok = False
        if exp["kind"] == "move" and not mid:
            ok = False
        if ok:
            disp = {"switch": "a switch", "tera": "Terastallization", "move": move_name(mid) if mid else None}[exp["kind"]]
            if sp and sp in opp and species_evidence(note_n, sp):
                disp = f"{disp} ({sp})"
            else:
                sp = None
            chips.append({"species": sp, "field": "expects_action", "value": disp, "id": {"kind": exp["kind"],
                          "move": mid, "species": sp}, "evidence": None, "source": source, "editable": True})
        else:
            dropped.append({"field": "expects_action", "value": exp, "reason": "the note does not state it"})
    conf = (raw or {}).get("confidence")
    if conf in ("sure", "think", "guess"):
        chips.append({"species": None, "field": "confidence", "value": conf, "id": conf, "evidence": None,
                      "source": source, "editable": True})
    if SPREAD_RE.search(note):
        chips.append({"species": None, "field": "spread", "value": "not used", "id": None,
                      "evidence": SPREAD_RE.search(note).group(0), "source": "rule", "editable": False,
                      "note": "EVs, natures and speed tiers are not used by verdict v1"})
    return chips, dropped


def belief_from_chips(chips):
    """Chips (possibly edited by the user) -> the research/27 §4.4 belief schema used by verdict.py."""
    sets, exp, conf = {}, None, "think"
    for c in chips:
        f = c["field"]
        if f in ("item", "ability", "tera"):
            sets.setdefault(c["species"], {})[f] = c["id"]
        elif f in ("moves_has", "moves_not"):
            lst = sets.setdefault(c["species"], {}).setdefault(f, [])
            if c["id"] not in lst:
                lst.append(c["id"])
        elif f == "expects_action":
            exp = {k: v for k, v in c["id"].items() if v is not None}
        elif f == "confidence":
            conf = c["id"]
    out = {"sets": {species_name(sp) if sp else sp: v for sp, v in sets.items()}, "confidence": conf}
    if exp:
        out["expects_action"] = exp
    return out


def has_invented_number(obj, note):
    """True if any string in the parsed belief carries a digit that the note's names do not (no numbers)."""
    s = json.dumps(obj)
    digits = set(re.findall(r"\d+", s))
    allowed = set(re.findall(r"\d+", note))
    return bool(digits - allowed)


# ------------------------------------------------------------------ read check (v1: data only)

NO_CHOICE_IDS = {"", "none", "nomove", "noop", "pass", "wait", "recharge", "struggle"}


def read_check(review, turn, k, belief):
    """Compare the expected opponent action with the engine's opponent policy (top three) and what was played.
    Every number is copied from decisions[].opponent_policy_top; returns None if no expectation or no data."""
    exp = (belief or {}).get("expects_action")
    if not exp:
        return None
    d = next((x for x in review.get("decisions") or [] if x.get("turn") == turn and x.get("k", 0) == k), None)
    t = next((x for x in review.get("turns") or [] if x.get("turn") == turn), None)
    if d is None or not d.get("opponent_policy_top"):
        return None
    top = [(a, p) for a, p in d["opponent_policy_top"]]
    played = (t or {}).get("opponent_action")
    real = [(a, p) for a, p in top if to_id(a) not in NO_CHOICE_IDS]
    if not real or (len(real) == 1 and real[0][1] >= 0.999):
        # e.g. a replacement after a faint: the opponent had no meaningful choice, so there is no read to check
        return {"turn": turn, "k": k, "expected": exp, "status": "no_opponent_choice",
                "engine_top": [[action_name(a), p] for a, p in top],
                "sentence": "Your opponent had no real choice at this decision (for example a replacement after a "
                            "faint), so there is no read to check here.",
                "scope": "We check your read of their move against the engine; we don't yet replay the turn under it."}
    top = real

    def matches(a):
        if exp["kind"] == "switch":
            return a.startswith("switch ") and (not exp.get("species") or to_id(a[7:]) == to_id(exp["species"]))
        if exp["kind"] == "tera":
            return a.endswith("-tera")
        return to_id(a.replace("-tera", "")) == to_id(exp.get("move") or "")

    hits = [(a, p) for a, p in top if matches(a)]
    if exp["kind"] == "switch":
        want = f"a switch to {species_name(exp['species'])}" if exp.get("species") else "a switch"
    elif exp["kind"] == "tera":
        want = "Terastallization"
    else:
        want = move_name(exp.get("move"))
    fp = lambda p: f"{int(round(100 * p))}%" if p >= 0.1 else f"{100 * p:.1f}%"  # noqa: E731
    first = f"You expected {want}. "
    if hits:
        tot = sum(p for _, p in hits)
        eng = (f"The engine gave it {fp(tot)}" if len(hits) == 1 else
               f"The engine gave those options {fp(tot)} together (within its top three)")
    else:
        eng = f"It was not in the engine's top three (each of those had at least {fp(top[-1][1])})"
    eng += f"; its top guess was {action_name(top[0][0])} at {fp(top[0][1])}. "
    third = f"They played {action_name(played)}." if played else "The log does not show their action."
    return {"turn": turn, "k": k, "expected": exp, "status": "checked",
            "engine_top": [[action_name(a), p] for a, p in top],
            "engine_prob_of_expected": round(sum(p for _, p in hits), 4) if hits else None,
            "upper_bound_if_not_listed": None if hits else top[-1][1], "played": action_name(played),
            "matched": bool(played) and matches(played), "sentence": first + eng + third,
            "scope": "We check your read of their move against the engine; we don't yet replay the turn under it."}


# ------------------------------------------------------------------ entry points

def parse(note, own, opp, parser="glimmer", client=None, context=None, own_moves=None):
    t0 = time.time()
    meta = {}
    if parser == "glimmer":
        raw, meta = glimmer_parse(note, own, opp, client, context)
        if raw is None:  # fallback: keyword parser + chips (research/28 S2.4)
            raw, parser = keyword_parse(note, own, opp, own_moves), "keyword(fallback)"
    else:
        raw = keyword_parse(note, own, opp, own_moves)
    chips, dropped = to_chips(raw, note, own, opp, parser, own_moves)
    dropped = (raw or {}).get("_rejected", []) + dropped  # keyword-path rejections too: nothing is dropped silently
    raw = {k: v for k, v in (raw or {}).items() if k != "_rejected"}
    belief = belief_from_chips(chips)
    return {"version": PARSER_VERSION, "parser": parser, "note": note, "raw": raw, "chips": chips,
            "dropped": dropped, "belief": belief, "invented_number": has_invented_number(belief, note),
            "seconds": round(time.time() - t0, 3), "llm": meta}


SMOKE_NOTES = [  # (log, side, review, turn, note) on the two local reviewed games
    ("scratch_ds/review/public/gen9ou-2704948800.json", "p1",
     "scratch_ds/review/public/gen9ou-2704948800.p1.review_v2.json", 11,
     "I thought Clefable would switch out to Ogerpon-Wellspring so I used Defog to clear the spikes"),
    ("scratch_ds/review/public/gen9ou-2704948800.json", "p1",
     "scratch_ds/review/public/gen9ou-2704948800.p1.review_v2.json", 12,
     "Dragapult was Choice Specs for sure, no way it had Will-O-Wisp"),
    ("scratch_ds/review/public/gen9ou-2704948800.json", "p1",
     "scratch_ds/review/public/gen9ou-2704948800.p1.review_v2.json", 15,
     "pretty sure Klefki had Light Clay and would set up screens, maybe Thunder Wave"),
    ("scratch_ds/review/public/gen9ou-2704948800.json", "p1",
     "scratch_ds/review/public/gen9ou-2704948800.p1.review_v2.json", 10,
     "I read Manaphy as Tera Fairy with Tail Glow, expected it to boost"),
    ("scratch_ds/review/public/gen9ou-2704948800.json", "p1",
     "scratch_ds/review/public/gen9ou-2704948800.p1.review_v2.json", 20,
     "Rillaboom has to be banded, I expected Grassy Glide"),
    ("scratch_ds/review/games/lab-n4-c000_g0019.public.json", "p1",
     "scratch_ds/review/games/lab-n4-c000_g0019.review_bridge.json", 8,
     "I thought Iron Moth was scarfed so it would outspeed and use Fiery Dance"),
    ("scratch_ds/review/games/lab-n4-c000_g0019.public.json", "p1",
     "scratch_ds/review/games/lab-n4-c000_g0019.review_bridge.json", 3,
     "Iron Treads probably had Rapid Spin and Knock Off, and I guessed it would switch to Iron Valiant"),
    ("scratch_ds/review/games/lab-n4-c000_g0019.public.json", "p1",
     "scratch_ds/review/games/lab-n4-c000_g0019.review_bridge.json", 5,
     "Hawlucha is always Unburden with Swords Dance, 252 Atk Jolly"),
    ("scratch_ds/review/games/lab-n4-c000_g0019.public.json", "p1",
     "scratch_ds/review/games/lab-n4-c000_g0019.review_bridge.json", 6,
     "maybe Iron Hands was Assault Vest, I didn't think it had Swords Dance"),
    ("scratch_ds/review/games/lab-n4-c000_g0019.public.json", "p1",
     "scratch_ds/review/games/lab-n4-c000_g0019.review_bridge.json", 11,
     "no idea honestly, just clicked the strongest move"),
    ("scratch_ds/review/games/lab-n4-c000_g0019.public.json", "p1",
     "scratch_ds/review/games/lab-n4-c000_g0019.review_bridge.json", 9,
     "I thought Iron Hands had no Ice Punch so Psychic was safe"),  # Ag-App regression: own move not credited
]


def smoke(client, out, root):
    rows = []
    for log, side, rv, turn, note in SMOKE_NOTES:
        own, opp = species_from_log(load_log(root / log), side)
        review = json.loads((root / rv).read_text())
        om = own_moves_from_review(review)
        g = parse(note, own, opp, "glimmer", client, context=f"turn {turn}", own_moves=om)
        kw = parse(note, own, opp, "keyword", own_moves=om)
        rc = read_check(review, turn, 0, g["belief"])
        valid = isinstance(g["belief"].get("sets"), dict) and g["belief"].get("confidence") in ("sure", "think",
                                                                                             "guess")
        rows.append({"note": note, "turn": turn, "glimmer": g, "keyword": kw, "read_check": rc,
                     "schema_valid": valid, "invented_number": g["invented_number"]})
        print(f"{'ok' if valid else 'BAD'} {len(g['chips'])} chips, {len(g['dropped'])} dropped, "
              f"{g['seconds']} s | {note[:60]}", file=sys.stderr)
    res = {"step": "S2.4 note parser smoke (research/28)", "when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "parser_version": PARSER_VERSION, "model": "base Muse Glimmer-30B Q4_K_M (local llama-server)",
           "n": len(rows), "schema_valid": sum(r["schema_valid"] for r in rows),
           "invented_numbers": sum(r["invented_number"] for r in rows),
           "with_read_check": sum(1 for r in rows if r["read_check"]),
           "glimmer_fallbacks": sum(1 for r in rows if r["glimmer"]["parser"] != "glimmer"),
           "latency_s_p50": sorted(r["glimmer"]["seconds"] for r in rows)[len(rows) // 2], "rows": rows}
    res["accept"] = res["schema_valid"] == len(rows) and res["invented_numbers"] == 0
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: res[k] for k in ("n", "schema_valid", "invented_numbers", "with_read_check",
                                          "glimmer_fallbacks", "latency_s_p50", "accept")}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log")
    ap.add_argument("--side", choices=["p1", "p2"])
    ap.add_argument("--note")
    ap.add_argument("--review")
    ap.add_argument("--turn", type=int)
    ap.add_argument("--k", type=int, default=0)
    ap.add_argument("--parser", default="glimmer", choices=["glimmer", "keyword"])
    ap.add_argument("--url", default="http://127.0.0.1:18131/v1/chat/completions")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--out", default="-")
    a = ap.parse_args()
    from llm import GlimmerClient
    client = GlimmerClient(a.url, 1024, 0.0, "low")
    root = HERE.parents[1]
    if a.smoke:
        return smoke(client, a.out if a.out != "-" else "scratch_ds/agent/parser_smoke.json", root)
    own, opp = species_from_log(load_log(a.log), a.side)
    review = json.loads(Path(a.review).read_text()) if a.review else None
    res = parse(a.note, own, opp, a.parser, client, context=f"turn {a.turn}" if a.turn is not None else None,
                own_moves=own_moves_from_review(review) if review else None)
    if review and a.turn is not None:
        res["read_check"] = read_check(review, a.turn, a.k, res["belief"])
    txt = json.dumps(res, indent=1, ensure_ascii=False)
    print(txt) if a.out == "-" else Path(a.out).write_text(txt + "\n")


if __name__ == "__main__":
    main()
