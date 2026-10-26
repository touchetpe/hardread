"""Public-observation serializer for Muse Glimmer SFT and live play (HardRead).

Shared interface (keep stable; the live player imports it):

    build_example(view, history, legal, player, turn, k, team_preview, task="action")
        -> {"messages": [...], "tools": [...], "tool_choice": ..., "id_map": {choice_id: foulplay_action}}
    target_message(choice, id_map, task="action", reads=None) -> assistant message (tool call)

Inputs
  view     Foul Play's public battle view (decision-log v2 `_side`/`_pkmn` dicts):
           {"user": side, "opponent": side, "weather", "field", "trick_room"}.
  history  public Showdown protocol lines visible at decision time (log[:history_end]).
           Exact HP on opponent lines is converted to Showdown's public percentage, so
           both the omniscient farm log and a live player's own stream render the same.
  legal    Foul Play root actions: "closecombat", "closecombat-tera", "switch kyurem".
  player   "p1" or "p2" (our side in the protocol).

Only public information and our own team are rendered. The opponent's nature/EVs in the
view are Foul Play defaults (not public) and are never rendered.

Format: system message = fixed instructions (stable prefix for KV-cache reuse, and it
carries "Reasoning strength: low." so rendering does not depend on template kwargs);
user message = compact text observation + frozen legal menu (act_01...); one tool
`submit_action(choice_id enum)` like the battle harness / workload.json. The SFT target
is a bare tool call with no reasoning frame (see target_message).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

SERIALIZER_VERSION = "hr-sft-v1.2"
# Minimal reasoning frame kept in every target so the output keeps the native channel layout
#   <|start|>assistant to=self<|message|>{ACT_REASONING}<|eom|><|start|>assistant to=<tool><|message|>...<|eot|>
# (<|eom|> separates reasoning from the answer and never ends the response; EOS = 200001/200008).
# Set to None for a bare tool call with no reasoning frame (also parseable by llama.cpp).
ACT_REASONING = "Acting directly."
RECENT_TURNS = 3  # at most this many complete turns of events, plus the current partial turn
MIN_TURNS = 2  # always keep at least this many complete turns when they exist
EVENT_CHAR_BUDGET = 650  # older turns beyond MIN_TURNS are dropped past this many characters

_DEX = json.loads((Path(__file__).resolve().parent / "dex.json").read_text())

SYSTEM_ACTION = (
    "You are Muse Glimmer playing a Pokemon Showdown Gen 9 OU singles battle, using only the public "
    "observation and your own team. Call submit_action once with the best choice_id from the legal "
    "menu. No explanation.\nReasoning strength: low."
)
SYSTEM_READS = (
    "You are Muse Glimmer analysing a Pokemon Showdown Gen 9 OU singles battle, using only the public "
    "observation and your own team. Predict the most likely set (ability, item, moves, spread, Tera type) "
    "of each listed opponent Pokemon and call submit_reads once. No explanation.\nReasoning strength: low."
)

STAT_NAMES = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
BOOST_NAMES = {"attack": "Atk", "defense": "Def", "special-attack": "SpA", "special-defense": "SpD",
               "speed": "Spe", "accuracy": "Acc", "evasion": "Eva",
               "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}
NATURE_SPE = {"timid": 1.1, "hasty": 1.1, "jolly": 1.1, "naive": 1.1,
              "brave": 0.9, "relaxed": 0.9, "quiet": 0.9, "sassy": 0.9}
WEATHER = {"sunnyday": "Sun", "raindance": "Rain", "sandstorm": "Sandstorm", "snowscape": "Snow",
           "snow": "Snow", "hail": "Hail", "desolateland": "Harsh Sun", "primordialsea": "Heavy Rain",
           "deltastream": "Strong Winds"}
SKIP_SIDE = {"protect", "toxic_count"}  # Foul Play internal counters
STATUS = {"psn": "PSN", "tox": "TOX", "brn": "BRN", "par": "PAR", "slp": "SLP", "frz": "FRZ"}


def to_id(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower()) if s else ""


def species_entry(name):
    i = to_id(name)
    e = _DEX["species"].get(i)
    if e is None:  # e.g. cosmetic or unknown formes: fall back to the longest known prefix
        for j in range(len(i) - 1, 2, -1):
            if i[:j] in _DEX["species"]:
                e = _DEX["species"][i[:j]]
                break
    return e


def species_name(name) -> str:
    e = species_entry(name)
    return e["n"] if e else str(name).title()


def move_name(m) -> str:
    e = _DEX["moves"].get(to_id(m))
    return e["n"] if e else str(m).title()


def item_name(it) -> str:
    return _DEX["items"].get(to_id(it), str(it).title())


def ability_name(a) -> str:
    return _DEX["abilities"].get(to_id(a), str(a).title())


def type_name(t) -> str:
    return _DEX["types"].get(to_id(t), str(t).title())


def move_label(m) -> str:
    e = _DEX["moves"].get(to_id(m))
    if not e:
        return str(m).title()
    cat = {"P": "phys", "S": "spec"}.get(e["c"], "status")
    desc = f"{e['t']}, {cat} {e['p']}" if cat != "status" and e["p"] else f"{e['t']}, {cat}"
    if e["r"]:
        desc += f", {e['r']:+d} prio"
    return f"{e['n']} ({desc})"


def pct_view(p) -> int:
    """HP percent from a Foul Play view entry (opponent hp is max_hp * public percent)."""
    if not p or not p.get("max_hp"):
        return 0
    return int(round(100.0 * float(p.get("hp") or 0) / float(p["max_hp"])))


def pct_public(hp: float, max_hp: float) -> int:
    """Showdown's public HP percentage (sim/pokemon.ts getHealth, HP Percentage Mod)."""
    if max_hp <= 0 or hp <= 0:
        return 0
    p = math.ceil(100.0 * hp / max_hp)
    if p >= 100 and hp < max_hp:
        p = 99
    return int(min(p, 100))


def boosts_str(b) -> str:
    return " ".join(f"{v:+d} {BOOST_NAMES.get(k, k)}" for k, v in (b or {}).items() if v)


def spread_str(nature, evs) -> str:
    parts = [f"{v}{STAT_NAMES[i]}" for i, v in enumerate(evs or []) if v]
    return f"{(nature or '').title()} {'/'.join(parts)}".strip()


def speed_stat(p) -> int | None:
    e = species_entry(p.get("name"))
    evs = p.get("evs") or []
    if not e or len(evs) != 6:
        return None
    lvl = p.get("level") or 100
    base = int((2 * e["b"][5] + 31 + evs[5] // 4) * lvl / 100) + 5
    return int(base * NATURE_SPE.get(to_id(p.get("nature")), 1.0))


def types_str(p) -> str:
    if p.get("terastallized") and p.get("tera_type"):
        return f"Tera {type_name(p['tera_type'])}"
    e = species_entry(p.get("name"))
    return "/".join(e["t"]) if e else "?"


def own_line(p, active: bool, preview: bool) -> str:
    name = species_name(p["name"])
    head = f"{'*' if active else '-'} {name}{' [active]' if active else ''}"
    if not preview and (p.get("hp") or 0) <= 0:
        return f"{head}: fainted"
    bits = [types_str(p)]
    if not preview:
        hp = f"{pct_view(p)}%" + (f" ({int(p.get('hp') or 0)}/{int(p.get('max_hp') or 0)})" if active else "")
        if p.get("status"):
            hp += " " + STATUS.get(p["status"], str(p["status"]).upper())
        bits.append(hp)
    it = p.get("item")
    if it and it != "unknownitem":
        bits.append(item_name(it))
    else:
        bits.append("no item" + (f" (lost {item_name(p['removed_item'])})" if p.get("removed_item") else ""))
    if p.get("ability"):
        bits.append(ability_name(p["ability"]))
    if p.get("tera_type") and not p.get("terastallized"):  # when terastallized the typing already says "Tera X"
        bits.append("Tera " + type_name(p["tera_type"]))
    spe = speed_stat(p)
    if spe:
        bits.append(f"Spe {spe}")
    bits.append(", ".join(move_name(m) for m in p.get("moves") or []))
    if active and p.get("boosts"):
        bits.append("boosts " + boosts_str(p["boosts"]))
    return head + " " + " | ".join(bits)


def opp_line(p, active: bool, preview: bool) -> str:
    name = species_name(p["name"])
    head = f"{'*' if active else '-'} {name}{' [active]' if active else ''}"
    if preview:
        return f"{head} {types_str(p)}"
    known_moves = [move_name(m) for m in p.get("moves") or []]
    if (p.get("hp") or 0) <= 0 and p.get("max_hp"):
        return f"{head}: fainted" + (f" | moves {', '.join(known_moves)}" if known_moves else "")
    bits = [types_str(p)]
    hp = f"{pct_view(p)}%"
    if p.get("status"):
        hp += " " + STATUS.get(p["status"], str(p["status"]).upper())
    bits.append(hp)
    it = p.get("item")
    if it is None:
        bits.append("no item" + (f" (lost {item_name(p['removed_item'])})" if p.get("removed_item") else ""))
    elif it != "unknownitem":
        bits.append(f"item {item_name(it)}")
    else:
        notes = []
        if p.get("impossible_items"):
            notes.append("not " + ", ".join(item_name(x) for x in p["impossible_items"]))
        if p.get("can_have_choice_item") is False:
            notes.append("no Choice item")
        bits.append("item ?" + (f" ({'; '.join(notes)})" if notes else ""))
    bits.append(f"ability {ability_name(p['ability'])}" if p.get("ability") else "ability ?")
    bits.append(f"moves {', '.join(known_moves)}" if known_moves else "moves ?")
    sr = p.get("speed_range") or [0, None]
    lo, hi = (sr + [None, None])[:2]
    if (lo or 0) > 0 and hi is not None:
        bits.append(f"Spe {int(lo)}" if int(lo) == int(hi) else f"Spe {int(lo)}-{int(hi)}")
    elif (lo or 0) > 0:
        bits.append(f"Spe >={int(lo)}")
    elif hi is not None:
        bits.append(f"Spe <={int(hi)}")
    if active and p.get("boosts"):
        bits.append("boosts " + boosts_str(p["boosts"]))
    return head + " " + " | ".join(bits)


def side_conditions_str(sc) -> str:
    out = []
    for k, v in (sc or {}).items():
        if k in SKIP_SIDE or not v:
            continue
        nm = move_name(k) if to_id(k) in _DEX["moves"] else str(k).title()
        out.append(f"{nm} x{v}" if k in ("spikes", "toxicspikes") and v > 1 else nm)
    return ", ".join(out) or "none"


def field_str(view) -> str:
    bits = []
    w = view.get("weather")
    if w and w != "none":
        bits.append(WEATHER.get(to_id(w), str(w).title()))
    f = view.get("field")
    if f and f != "none":
        bits.append(move_name(f) if to_id(f) in _DEX["moves"] else str(f).title())
    if view.get("trick_room"):
        bits.append("Trick Room")
    return "; ".join(bits) or "clear"


# --------------------------------------------------------------------------- history

_HP_RE = re.compile(r"^(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)(?:\s+(\w+))?$")


def _hp(field: str) -> str:
    """'397/514 psn' -> '77% psn'; '0 fnt' -> '0%'. Percent only, never exact HP."""
    f = (field or "").strip()
    if f.startswith("0 fnt") or f == "0":
        return "0%"
    m = _HP_RE.match(f)
    if not m:
        return ""
    hp, mx, st = float(m.group(1)), float(m.group(2)), m.group(3)
    pct = int(round(hp)) if mx == 100 else pct_public(hp, mx)
    return f"{pct}%" + (f" {STATUS.get(st, st.upper())}" if st and st != "fnt" else "")


def _from(args) -> str:
    for a in args:
        if a.startswith("[from]"):
            s = a[len("[from]"):].strip()
            for pfx in ("item: ", "ability: ", "move: "):
                if s.startswith(pfx):
                    s = s[len(pfx):]
            return s
    return ""


def _effect(s: str) -> str:
    s = s or ""
    for pfx in ("move: ", "ability: ", "item: "):
        if s.startswith(pfx):
            return s[len(pfx):]
    if s.startswith("quarkdrive") or s.startswith("protosynthesis"):
        base = "Quark Drive" if s.startswith("quark") else "Protosynthesis"
        stat = s[len("quarkdrive"):] if s.startswith("quark") else s[len("protosynthesis"):]
        return f"{base} {BOOST_NAMES.get(stat, stat)}".strip()
    return s


class _Events:
    def __init__(self, player: str):
        self.me = player

    def who(self, ident: str) -> str:
        # "p2a: Ting-Lu" -> "Opp Ting-Lu"
        side, _, name = ident.partition(": ")
        own = side[:2] == self.me
        return f"{'Your' if own else 'Opp'} {name or ident}"

    def side(self, ident: str) -> str:
        return "your side" if ident[:2] == self.me else "opp side"

    def render(self, line: str) -> str | None:
        parts = line.split("|")
        if len(parts) < 2:
            return None
        cmd, a = parts[1], parts[2:]
        src = _from(a)
        sfx = f" ({src})" if src else ""
        try:
            if cmd in ("switch", "drag"):
                spc = a[1].split(",")[0]
                who = "You" if a[0][:2] == self.me else "Opp"
                return f"{who} {'dragged in' if cmd == 'drag' else 'sent in'} {spc} ({_hp(a[2])})"
            if cmd == "move":
                miss = " (missed)" if "[miss]" in a else ""
                return f"{self.who(a[0])} used {a[1]}{miss}"
            if cmd == "-damage":
                hp = _hp(a[1])
                if hp == "0%":
                    return None  # the faint line follows
                return f"{self.who(a[0])} {hp}{sfx}"
            if cmd == "-heal":
                return f"{self.who(a[0])} healed to {_hp(a[1])}{sfx}"
            if cmd == "-sethp":
                return f"{self.who(a[0])} HP set to {_hp(a[1])}"
            if cmd == "faint":
                return f"{self.who(a[0])} fainted"
            if cmd == "-supereffective":
                return "super effective"
            if cmd == "-resisted":
                return "resisted"
            if cmd == "-immune":
                return f"{self.who(a[0])} immune{sfx}"
            if cmd == "-crit":
                return "critical hit"
            if cmd == "-miss":
                return f"{self.who(a[0])} missed"
            if cmd == "-fail":
                return f"{self.who(a[0])}: failed" + (f" ({_effect(a[1])})" if len(a) > 1 and a[1] and not a[1].startswith("[") else "")
            if cmd == "-status":
                return f"{self.who(a[0])} got {STATUS.get(a[1], a[1].upper())}{sfx}"
            if cmd == "-curestatus":
                return f"{self.who(a[0])} cured of {STATUS.get(a[1], a[1].upper())}"
            if cmd in ("-boost", "-unboost"):
                n = int(a[2]) * (1 if cmd == "-boost" else -1)
                return f"{self.who(a[0])} {n:+d} {BOOST_NAMES.get(a[1], a[1])}{sfx}"
            if cmd == "-setboost":
                return f"{self.who(a[0])} {BOOST_NAMES.get(a[1], a[1])} set to {int(a[2]):+d}"
            if cmd in ("-clearallboost",):
                return "all stat changes cleared"
            if cmd in ("-clearboost", "-clearnegativeboost", "-clearpositiveboost"):
                return f"{self.who(a[0])} stat changes cleared"
            if cmd == "-weather":
                if "[upkeep]" in a:
                    return None
                if a[0] == "none":
                    return "weather ended"
                return f"{WEATHER.get(to_id(a[0]), a[0])} started{sfx}"
            if cmd == "-fieldstart":
                return f"{_effect(a[0])} started{sfx}"
            if cmd == "-fieldend":
                return f"{_effect(a[0])} ended"
            if cmd == "-sidestart":
                return f"{_effect(a[1])} set on {self.side(a[0])}"
            if cmd == "-sideend":
                return f"{_effect(a[1])} removed from {self.side(a[0])}{sfx}"
            if cmd == "-ability":
                return f"{self.who(a[0])} ability {a[1]}{sfx}"
            if cmd == "-item":
                return f"{self.who(a[0])} item {a[1]}{sfx}"
            if cmd == "-enditem":
                eaten = " (eaten)" if "[eat]" in a else ""
                return f"{self.who(a[0])} lost {a[1]}{eaten}{sfx}"
            if cmd == "-terastallize":
                return f"{self.who(a[0])} terastallized ({a[1]})"
            if cmd in ("-start", "-end"):
                if "[silent]" in a:
                    return None
                eff = _effect(a[1])
                return f"{self.who(a[0])}: {eff} {'started' if cmd == '-start' else 'ended'}{sfx}"
            if cmd == "-activate":
                eff = _effect(a[1]) if len(a) > 1 else ""
                if eff.lower() in ("quark drive", "protosynthesis") or not eff:
                    return None
                return f"{self.who(a[0])}: {eff} activated"
            if cmd == "cant":
                return f"{self.who(a[0])} can't move ({_effect(a[1])})"
            if cmd == "-prepare":
                return f"{self.who(a[0])} charging {a[1]}"
            if cmd == "-mustrecharge":
                return f"{self.who(a[0])} must recharge"
            if cmd in ("detailschange", "-formechange"):
                return f"{self.who(a[0])} became {a[1].split(',')[0]}"
            if cmd == "replace":
                return f"{self.who(a[0])} was {a[1].split(',')[0]} (Illusion)"
            if cmd == "-transform":
                return f"{self.who(a[0])} transformed"
            if cmd == "-hitcount":
                return f"hit {a[1]} times"
            if cmd == "-ohko":
                return "one-hit KO"
        except (IndexError, ValueError):
            return None
        return None  # t:, upkeep, inactive, -anim, player, poke, ... carry no decision-relevant info


def recent_events(history: list[str], player: str, n_turns: int = RECENT_TURNS) -> list[str]:
    """Summaries of the last n complete turns plus any events of the current partial turn."""
    ev = _Events(player)
    turns: list[tuple[int, list[str]]] = []
    cur, buf = 0, []
    started = False
    for line in history:
        if line.startswith("|start"):
            started = True
            continue
        if not started:
            continue
        if line.startswith("|turn|"):
            turns.append((cur, buf))
            cur, buf = int(line.split("|")[2]), []
            continue
        r = ev.render(line)
        if r:
            buf.append(r)
    out = [f"T{cur} so far: " + "; ".join(buf)] if buf else []
    used = len(out[0]) if out else 0
    kept = 0
    for t, evs in reversed(turns[-n_turns:]):
        if not evs:
            continue
        line = (f"T{t}: " if t else "Start: ") + "; ".join(evs)
        if kept >= MIN_TURNS and used + len(line) > EVENT_CHAR_BUDGET:
            break
        out.insert(0, line)
        used += len(line)
        kept += 1
    return out


# --------------------------------------------------------------------------- legal menu

def _team(side) -> list:
    return [p for p in [side.get("active")] + list(side.get("reserve") or []) if p]


def ordered_legal(view, legal: list[str], team_preview: bool) -> list[str]:
    """Deterministic, public-only menu order: moves (moveset order), Tera moves, switches (team order)."""
    user = view.get("user") or {}
    active = user.get("active") or {}
    order = {to_id(m): i for i, m in enumerate(active.get("moves") or [])}
    team = {to_id(p["name"]): i for i, p in enumerate(_team(user))}

    def key(a):
        if a.startswith("switch "):
            return (2, team.get(to_id(a[7:]), 99), a)
        tera = a.endswith("-tera")
        base = a[:-5] if tera else a
        return (1 if tera else 0, order.get(to_id(base), 50), a)

    return sorted(dict.fromkeys(legal), key=key)


def action_label(a: str, view, team_preview: bool) -> str:
    if a.startswith("switch "):
        tgt = a[7:]
        mon = next((p for p in _team(view.get("user") or {}) if to_id(p["name"]) == to_id(tgt)), None)
        nm = species_name(tgt)
        if team_preview:
            return f"Lead with {nm}"
        if mon is None:
            return f"Switch to {nm}"
        st = f" {STATUS[mon['status']]}" if mon.get("status") in STATUS else ""
        return f"Switch to {nm} ({pct_view(mon)}%{st})"
    if a.endswith("-tera"):
        act = (view.get("user") or {}).get("active") or {}
        t = act.get("can_terastallize") if isinstance(act.get("can_terastallize"), str) else act.get("tera_type")
        return f"Terastallize{f' ({type_name(t)})' if t else ''} + {move_name(a[:-5])}"
    return move_label(a)


# --------------------------------------------------------------------------- public API

def _action_tool(ids: list[str]) -> dict:
    # Keys sorted at every level so llama.cpp (which may re-serialise tool JSON) renders the
    # same system block as the HF template at training time.
    return {"function": {"description": "Choose exactly one frozen legal action ID for this request.",
                         "name": "submit_action",
                         "parameters": {"additionalProperties": False,
                                        "properties": {"choice_id": {"enum": list(ids), "type": "string"}},
                                        "required": ["choice_id"], "type": "object"},
                         "strict": True},
            "type": "function"}


READS_FIELDS = ["ability", "item", "moves", "spread", "tera"]


def _reads_tool() -> dict:
    inner = {"additionalProperties": False,
             "properties": {"ability": {"type": "string"}, "item": {"type": "string"},
                            "moves": {"items": {"type": "string"}, "type": "array"},
                            "spread": {"type": "string"}, "tera": {"type": "string"}},
             "required": READS_FIELDS, "type": "object"}
    return {"function": {"description": "Submit the predicted set of each non-fainted opponent Pokemon, keyed by species.",
                         "name": "submit_reads",
                         "parameters": {"additionalProperties": False,
                                        "properties": {"reads": {"additionalProperties": inner, "type": "object"}},
                                        "required": ["reads"], "type": "object"},
                         "strict": True},
            "type": "function"}


def observation_text(view, history, player, turn, k, team_preview) -> str:
    user, opp = view.get("user") or {}, view.get("opponent") or {}
    own, theirs = _team(user), _team(opp)
    L = []
    if team_preview:
        L.append(f"Gen 9 OU singles, team preview. You are {player}.")
    else:
        L.append(f"Gen 9 OU singles, turn {turn}. You are {player}.")
    L.append("== Your team ==")
    for i, p in enumerate(own):
        L.append(own_line(p, active=(i == 0 and not team_preview), preview=team_preview))
    if not team_preview:
        tera_used = any(p.get("terastallized") for p in own)
        act = own[0] if own else {}
        can = isinstance(act.get("can_terastallize"), str) and not tera_used
        L.append(f"Your Tera: {'used' if tera_used else ('available' if can else 'unavailable now')}")
    L.append("== Opponent ==")
    for i, p in enumerate(theirs):
        L.append(opp_line(p, active=(i == 0 and not team_preview), preview=team_preview))
    if not team_preview:
        used = [p for p in theirs if p.get("terastallized")]
        L.append(f"Opponent Tera: {'used (' + species_name(used[0]['name']) + ')' if used else 'not used'}")
        L.append(f"== Field ==\n{field_str(view)}. Your side: {side_conditions_str(user.get('side_conditions'))}. "
                 f"Opp side: {side_conditions_str(opp.get('side_conditions'))}.")
        ev = recent_events(history or [], player)
        if ev:
            L.append("== Recent turns ==")
            L.extend(ev)
    return "\n".join(L)


def build_example(view: dict, history: list[str], legal: list[str], player: str, turn: int, k: int,
                  team_preview: bool, task: str = "action") -> dict:
    """Returns {"messages": [...], "tools": [...], "tool_choice": ..., "id_map": {choice_id: foulplay_action}}."""
    obs = observation_text(view, history, player, turn, k, team_preview)
    if task == "reads":
        alive = [species_name(p["name"]) for p in _team(view.get("opponent") or {})
                 if team_preview or (p.get("hp") or 0) > 0 or not p.get("max_hp")]
        user = f"{obs}\n== Task ==\nPredict the sets of: {', '.join(alive)}."
        return {"messages": [{"role": "system", "content": SYSTEM_READS}, {"role": "user", "content": user}],
                "tools": [_reads_tool()], "tool_choice": "required", "id_map": {}, "species": alive}
    if task != "action":
        raise ValueError(f"unknown task {task!r}")
    menu = ordered_legal(view, legal, team_preview)
    id_map = {f"act_{i + 1:02d}": a for i, a in enumerate(menu)}
    own_active = (view.get("user") or {}).get("active") or {}
    if team_preview:
        ask = "Choose your lead Pokemon."
    elif menu and all(a.startswith("switch ") for a in menu) and (own_active.get("hp") or 0) <= 0:
        ask = f"Your {species_name(own_active.get('name'))} fainted: choose a replacement."
    elif menu and all(a.startswith("switch ") for a in menu) and k > 0:
        ask = "Choose a Pokemon to switch in."
    else:
        ask = "Choose one action."
    lines = [obs, f"== Legal actions == {ask}"] + [f"{cid}: {action_label(a, view, team_preview)}" for cid, a in id_map.items()]
    return {"messages": [{"role": "system", "content": SYSTEM_ACTION}, {"role": "user", "content": "\n".join(lines)}],
            "tools": [_action_tool(list(id_map))], "tool_choice": "required", "id_map": id_map}


def target_message(choice: str, id_map: dict, task: str = "action", reads: dict | None = None) -> dict:
    """The assistant message (tool call) to train on.

    With reasoning_content = ACT_REASONING the native template renders exactly
    `<|start|>assistant to=self<|message|>Acting directly.<|eom|>`
    `<|start|>assistant to=submit_action<|message|><atem:function_calls>...</atem:function_calls><|eot|>`,
    the layout llama.cpp's Muse PEG parser expects (reasoning frames, then one tool frame).
    """
    extra = {"reasoning_content": ACT_REASONING} if ACT_REASONING else {}
    if task == "reads":
        if reads is None:
            raise ValueError("reads task needs the reads label (see reads_label)")
        return {"role": "assistant", "content": "", **extra,
                "tool_calls": [{"type": "function", "function": {"name": "submit_reads", "arguments": {"reads": reads}}}]}
    inv = {v: k for k, v in id_map.items()}
    if choice not in inv:
        raise KeyError(f"choice {choice!r} not in legal menu")
    return {"role": "assistant", "content": "", **extra,
            "tool_calls": [{"type": "function", "function": {"name": "submit_action", "arguments": {"choice_id": inv[choice]}}}]}


def reads_label(species: list[str], opp_team: list[dict]) -> dict:
    """Reads target from the opponent's TRUE sets (label only, never an input).

    species: display names in prompt order (build_example(task="reads")["species"]).
    Returns {species: {"ability", "item", "moves", "spread", "tera"}} with keys in schema order.
    """
    by_id = {to_id(s.get("species")): s for s in opp_team}
    out = {}
    for sp in species:
        s = by_id.get(to_id(sp))
        if s is None:  # forme mismatch (e.g. preview "Zamazenta-*"): match on the base species
            s = next((v for k, v in by_id.items() if k.startswith(to_id(sp)) or to_id(sp).startswith(k)), None)
        if s is None:
            continue
        evs = s.get("evs") or {}
        ev_list = [evs.get(x, 0) for x in ("hp", "atk", "def", "spa", "spd", "spe")] if isinstance(evs, dict) else list(evs)
        out[sp] = {"ability": ability_name(s.get("ability") or ""),
                   "item": item_name(s["item"]) if s.get("item") else "none",
                   "moves": [move_name(m) for m in s.get("moves") or []],
                   "spread": spread_str(s.get("nature"), ev_list),
                   "tera": type_name(s.get("tera_type") or "")}
    return out
