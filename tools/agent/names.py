"""Display names for engine ids (species, moves, items, abilities) from tools/sft/dex.json (Showdown c0845f3).

Pure Python 3.9, no Foul Play import. Used by the tool wrappers (display strings in tool outputs), the
renderer, the note parser and verifier rule 5 (named-entity lexicon).
"""
import json
import re
from functools import lru_cache
from pathlib import Path

DEX_PATH = Path(__file__).resolve().parents[1] / "sft" / "dex.json"


def to_id(s):
    """Showdown id: lowercase alphanumerics only ("Porygon2" -> "porygon2", "U-turn" -> "uturn")."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


@lru_cache(maxsize=1)
def dex():
    return json.loads(DEX_PATH.read_text())


def species_name(sid):
    e = dex()["species"].get(to_id(sid))
    return e["n"] if e else (sid or "").title()


def move_name(mid):
    e = dex()["moves"].get(to_id(mid))
    return e["n"] if e else (mid or "").title()


def item_name(iid):
    return dex()["items"].get(to_id(iid)) or (iid or "").title()


def ability_name(aid):
    return dex()["abilities"].get(to_id(aid)) or (aid or "").title()


def type_name(tid):
    return dex()["types"].get(to_id(tid)) or (tid or "").title()


def action_name(a):
    """Engine action id -> display text: 'switch ogerpon' -> 'switch to Ogerpon',
    'knockoff-tera' -> 'Tera + Knock Off', 'sludgebomb' -> 'Sludge Bomb'. Team preview leads are 'switch x'."""
    if a is None:
        return None
    a = str(a)
    if a.startswith("switch "):
        return "switch to " + species_name(a[len("switch "):])
    tera = a.endswith("-tera")
    base = a[:-len("-tera")] if tera else a
    n = move_name(base)
    return "Tera + " + n if tera else n


def action_entities(a):
    """The (kind, display name) entities an action id mentions: [('species', 'Ogerpon')] or [('move', 'Knock Off')]."""
    if a is None:
        return []
    a = str(a)
    if a.startswith("switch "):
        return [("species", species_name(a[len("switch "):]))]
    base = a[:-len("-tera")] if a.endswith("-tera") else a
    return [("move", move_name(base))]


# Single-word display names that are ordinary English words: rule 5 only treats them as a Pokémon or move
# name when they are capitalised away from the start of a sentence.
COMMON_WORDS = {
    "absorb", "acid", "agility", "amnesia", "attract", "barrier", "bind", "bite", "block", "bounce", "camouflage",
    "captivate", "charge", "charm", "chatter", "clamp", "confide", "constrict", "conversion", "copycat", "counter",
    "covet", "crunch", "curse", "cut", "defog", "detect", "dig", "disable", "dive", "doom", "endure", "entrainment",
    "explosion", "facade", "feint", "flail", "flash", "fling", "fly", "foresight", "frustration", "growl", "growth",
    "grudge", "guillotine", "haze", "headbutt", "heal", "hex", "howl", "imprison", "ingrain", "instruct", "kinesis",
    "leer", "lick", "lockon", "memento", "metronome", "mimic", "minimize", "nightmare", "outrage", "payback",
    "pound", "present", "protect", "psywave", "punishment", "pursuit", "quash", "rage", "recover", "recycle",
    "reflect", "refresh", "rest", "retaliate", "return", "revenge", "reversal", "roar", "roost", "rollout", "round",
    "safeguard", "scratch", "screech", "sketch", "slam", "smog", "snarl", "snatch", "snore", "spite", "splash",
    "stockpile", "stomp", "strength", "struggle", "stuff", "substitute", "superpower", "supersonic", "surf",
    "swagger", "swallow", "switcheroo", "synthesis", "tackle", "taunt", "teleport", "thief", "thrash", "tickle",
    "torment", "transform", "trick", "uproar", "venoshock", "whirlwind", "wish", "withdraw", "wrap", "yawn",
    # species that are English words
    "ditto", "electrode", "magnet", "onix", "unown",
}


@lru_cache(maxsize=1)
def entity_lexicon():
    """[(display name, kind, id)] for every species and move, longest names first (greedy matching)."""
    d = dex()
    out = [(v["n"], "species", k) for k, v in d["species"].items()]
    out += [(v["n"], "move", k) for k, v in d["moves"].items()]
    out.sort(key=lambda x: -len(x[0]))
    return out


@lru_cache(maxsize=1)
def _entity_re():
    names = [n for n, _, _ in entity_lexicon() if len(n) >= 3]
    by_name = {}
    for n, kind, eid in entity_lexicon():
        by_name.setdefault(n, (kind, eid))
    rx = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9])")
    return rx, by_name


def find_entities(text):
    """Species and move names mentioned in free text: [(name, kind, id, start)], non-overlapping, longest first.
    Case-sensitive (display names are capitalised); single common English words only count when capitalised
    and not at the start of the sentence."""
    rx, by_name = _entity_re()
    found = []
    for m in rx.finditer(text):
        name, s = m.group(0), m.start()
        kind, eid = by_name[name]
        if " " not in name and to_id(name) in COMMON_WORDS:
            before = text[:s].rstrip()
            if before == "" or before.endswith((".", "!", "?", ":")):
                continue
        found.append((name, kind, eid, s))
    return found
