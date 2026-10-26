#!/usr/bin/env python3
"""Rebuild a player's decision points from a Showdown battle log, offline, with Foul Play's own code.

Given a battle log (protocol lines), the user's side (p1/p2) and the user's team, this
replays the log through Foul Play's battle-update code (fp.battle.protocol) exactly as the
bot would have received it live: the same message batches, a synthesized `|request|`
before every batch (Showdown sends one to each player after every update), and the
same copies Foul Play makes before searching (fp.modes.base.async_pick_move ->
fp.search.main.find_best_move). No server is used.

What is synthesized and how:
  * The opponent's HP is converted to what a player sees (Showdown's HP Percentage Mod:
    ceil(100*hp/max), 99 if not full). Farm logs carry exact HP for both sides; public
    replays carry percent for both sides, and the user's own exact HP is then estimated
    from the team's max HP (error <= 1% of max HP).
  * The user's request JSON (exact HP, status, item, abilities, stats, PP, disabled moves,
    trapping, tera availability, locked moves) is rebuilt from the user's team export and
    the log by a small tracker (UserTracker) that follows Showdown's request rules.
  * Batch boundaries: team preview, every `|turn|N`, and every request round that a
    forced/pivot switch produces (before the switch lines it caused).

Output per user decision: turn, k (index within the turn), team_preview, the view in the
decision-logger v2 format (fp/hardread_decision_log.py), the legal root actions
(poke-engine options from the reconstructed state), and the action actually taken as
read from the log (None when the log does not reveal it, e.g. the user's Pokemon fainted
or was fully paralysed before moving).

The opponent's true team is never used here.
"""
import argparse
import gzip
import json
import math
import re
import sys
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpenv  # noqa: E402

fpenv.configure()

from fp import constants  # noqa: E402
from fp import hardread_decision_log as declog  # noqa: E402
from fp.battle import protocol  # noqa: E402
from fp.battle.helpers import normalize_name  # noqa: E402
from fp.battle.state import Battle, LastUsedMove, Pokemon  # noqa: E402
from fp.data import all_move_json, pokedex  # noqa: E402
from fp.format_spec import FormatSpec  # noqa: E402
from fp.modes import battle_mode  # noqa: E402
from fp.teams.team_converter import export_to_dict  # noqa: E402

STATS = ("hp", "atk", "def", "spa", "spd", "spe")
POKEDEX_STAT = {"hp": "hp", "atk": "attack", "def": "defense", "spa": "special-attack",
                "spd": "special-defense", "spe": "speed"}
NATURES = {
    "adamant": ("atk", "spa"), "bold": ("def", "atk"), "brave": ("atk", "spe"), "calm": ("spd", "atk"),
    "careful": ("spd", "spa"), "gentle": ("spd", "def"), "hasty": ("spe", "def"), "impish": ("def", "spa"),
    "jolly": ("spe", "spa"), "lax": ("def", "spd"), "lonely": ("atk", "def"), "mild": ("spa", "def"),
    "modest": ("spa", "atk"), "naive": ("spe", "spd"), "naughty": ("atk", "spd"), "quiet": ("spa", "spe"),
    "rash": ("spa", "spd"), "relaxed": ("def", "spe"), "sassy": ("spd", "spe"), "timid": ("spe", "atk"),
}
CHOICE_ITEMS = {"choiceband", "choicescarf", "choicespecs"}
LOCKING_MOVES = {"outrage", "petaldance", "thrash", "ragingfury"}
PARTIAL_TRAP = {"bind", "wrap", "firespin", "clamp", "whirlpool", "sandtomb", "magmastorm", "infestation",
                "snaptrap", "thundercage"}
SKIP_LINES = {"", "t:", "j", "J", "l", "L", "c", "c:", "raw", "html", "uhtml", "uhtmlchange", "n", "N",
              "chat", "join", "leave", "name", "inactive", "inactiveoff", "badge", "debug", "seed", "unlink"}
EJECT_RE = re.compile(r"Eject Button|Eject Pack|ability: Emergency Exit|ability: Wimp Out")


# ----------------------------------------------------------------------------- teams

def _norm(s):
    return normalize_name(s or "")


def parse_export(text):
    """Showdown export text -> list of set dicts with display names (species, item, moves...)."""
    sets = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        head = lines[0]
        item = ""
        if "@" in head:
            head, item = head.split("@", 1)
        head = head.strip()
        gender = ""
        for g in ("(M)", "(F)"):
            if head.endswith(g):
                gender = g[1]
                head = head[: -len(g)].strip()
        m = re.match(r"^(.*)\(([^()]+)\)$", head)
        if m:
            nickname, species = m.group(1).strip(), m.group(2).strip()
        else:
            nickname, species = "", head
        s = {"name": nickname, "species": species, "item": item.strip(), "ability": "", "moves": [],
             "nature": "", "evs": {k: 0 for k in STATS}, "ivs": {k: 31 for k in STATS}, "level": 100,
             "teraType": "", "gender": gender}
        for l in lines[1:]:
            if l.startswith("Ability:"):
                s["ability"] = l.split(":", 1)[1].strip()
            elif l.startswith("Level:"):
                s["level"] = int(l.split(":", 1)[1].strip())
            elif l.startswith("Tera Type:"):
                s["teraType"] = l.split(":", 1)[1].strip()
            elif l.startswith("EVs:") or l.startswith("IVs:"):
                key = "evs" if l.startswith("EVs:") else "ivs"
                for part in l.split(":", 1)[1].split("/"):
                    amount, stat = part.strip().split(" ", 1)
                    s[key][_norm(stat)[:3] if _norm(stat) != "hp" else "hp"] = int(amount)
            elif l.endswith("Nature"):
                s["nature"] = l.replace("Nature", "").strip()
            elif l.startswith("-"):
                s["moves"].append(l[1:].strip())
        sets.append(s)
    return sets


def sets_to_export(sets):
    out = []
    for s in sets:
        head = f"{s['name']} ({s['species']})" if s.get("name") and s["name"] != s["species"] else s["species"]
        if s.get("gender"):
            head += f" ({s['gender']})"
        if s.get("item"):
            head += f" @ {s['item']}"
        lines = [head]
        if s.get("ability"):
            lines.append(f"Ability: {s['ability']}")
        if s.get("level") and int(s["level"]) != 100:
            lines.append(f"Level: {s['level']}")
        if s.get("teraType"):
            lines.append(f"Tera Type: {s['teraType']}")
        evs = s.get("evs") or {}
        lines.append("EVs: " + " / ".join(f"{int(evs.get(k) or 0)} {lbl}" for k, lbl in
                                         zip(STATS, ("HP", "Atk", "Def", "SpA", "SpD", "Spe"))))
        if s.get("nature"):
            lines.append(f"{s['nature']} Nature")
        ivs = s.get("ivs") or {}
        lines.append("IVs: " + " / ".join(f"{int(ivs.get(k, 31) if ivs.get(k) not in (None, '') else 31)} {lbl}"
                                         for k, lbl in zip(STATS, ("HP", "Atk", "Def", "SpA", "SpD", "Spe"))))
        lines += [f"- {m}" for m in s.get("moves") or []]
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def normalize_sets(team):
    """Accept export text, a list of Showdown set dicts, or extractor norm_set dicts."""
    if isinstance(team, str):
        return parse_export(team)
    sets = []
    for s in team:
        sets.append({"name": s.get("name") if s.get("name") != s.get("species") else "",
                     "species": s.get("species") or s.get("name"), "item": s.get("item") or "",
                     "ability": s.get("ability") or "", "moves": list(s.get("moves") or []),
                     "nature": s.get("nature") or "", "evs": {k: int((s.get("evs") or {}).get(k) or 0) for k in STATS},
                     "ivs": {k: int((s.get("ivs") or {}).get(k, 31) if (s.get("ivs") or {}).get(k) not in (None, "") else 31)
                             for k in STATS},
                     "level": int(s.get("level") or 100), "teraType": s.get("teraType") or s.get("tera_type") or "",
                     "gender": s.get("gender") or ""})
    return sets


def calc_stats(species, level, evs, ivs, nature):
    base = pokedex[_norm(species)][constants.BASESTATS]
    up, down = NATURES.get(_norm(nature), (None, None))
    out = {}
    for k in STATS:
        b = base[POKEDEX_STAT[k]]
        core = (2 * b + ivs[k] + evs[k] // 4) * level // 100
        if k == "hp":
            out[k] = 1 if _norm(species) == "shedinja" else core + level + 10
        else:
            v = core + 5
            if k == up:
                v = v * 110 // 100
            elif k == down:
                v = v * 90 // 100
            out[k] = v
    return out


def default_nickname(species):
    entry = pokedex.get(_norm(species), {})
    return entry.get("baseSpecies") or species


def species_types(species):
    return [t.lower() for t in pokedex.get(_norm(species), {}).get(constants.TYPES, [])]


# ----------------------------------------------------------------------------- HP views

def showdown_percent(hp, maxhp):
    if hp <= 0:
        return 0
    p = math.ceil(100 * hp / maxhp)
    return 99 if p == 100 and hp < maxhp else p


def _split_hp(field):
    """'208/289 psn' -> (208, 289, 'psn'); '0 fnt' -> (0, None, 'fnt')."""
    parts = field.split(" ")
    if parts[0] == "0" or "fnt" in parts:
        return 0, None, "fnt"
    cur, _, mx = parts[0].partition("/")
    mx = re.sub(r"[gyr]$", "", mx)
    return int(float(cur)), int(mx) if mx else None, parts[1] if len(parts) > 1 else ""


HP_FIELD = {"switch": 4, "drag": 4, "-damage": 3, "-heal": 3, "-sethp": 3}


def normalize_log(log, side, maxhp_by_nick=None):
    """Return the log as the `side` player would have received it.

    Handles |split| blocks, converts the opponent's exact HP to percent, and (for
    percent-only replays) converts the user's percent HP to an exact estimate using
    maxhp_by_nick (nickname -> max HP from the team). Returns (lines, own_hp_mode).
    """
    if isinstance(log, str):
        log = log.split("\n")
    lines = []
    i = 0
    while i < len(log):
        l = log[i]
        if l.startswith("|split|"):
            who = l.split("|")[2]
            if i + 2 < len(log):
                lines.append(log[i + 1] if who == side else log[i + 2])
            i += 3
            continue
        lines.append(l)
        i += 1
    # decide whether the user's own HP is exact or percent
    own_dens = []
    for l in lines:
        p = l.split("|")
        if len(p) > 4 and p[1] in ("switch", "drag") and p[2].startswith(side):
            _, mx, _ = _split_hp(p[4])
            if mx:
                own_dens.append(mx)
    own_mode = "percent" if own_dens and all(d == 100 for d in own_dens) else "exact"
    out = []
    for l in lines:
        p = l.split("|")
        if len(p) > 2 and p[1] in HP_FIELD and len(p) > HP_FIELD[p[1]]:
            idx = HP_FIELD[p[1]]
            who = p[2][:2]
            cur, mx, st = _split_hp(p[idx])
            if st == "fnt":
                pass
            elif who != side and mx and mx != 100:
                p[idx] = f"{showdown_percent(cur, mx)}/100" + (f" {st}" if st else "")
            elif who == side and own_mode == "percent" and mx == 100 and maxhp_by_nick is not None:
                nick = p[2].split(": ", 1)[1] if ": " in p[2] else p[2]
                real_max = maxhp_by_nick.get(nick)
                if real_max:
                    if cur >= 100:
                        est = real_max
                    else:
                        est = min(real_max - 1, max(1, round((cur - 0.5) * real_max / 100)))
                    p[idx] = f"{est}/{real_max}" + (f" {st}" if st else "")
            l = "|".join(p)
        out.append(l)
    return out, own_mode


def to_public_log(log):
    """Spectator view of an exact-HP log: every HP shown in percent (what a public replay shows)."""
    out = []
    for l in log:
        p = l.split("|")
        if len(p) > 2 and p[1] in HP_FIELD and len(p) > HP_FIELD[p[1]]:
            idx = HP_FIELD[p[1]]
            cur, mx, st = _split_hp(p[idx])
            if st != "fnt" and mx and mx != 100:
                p[idx] = f"{showdown_percent(cur, mx)}/100" + (f" {st}" if st else "")
            l = "|".join(p)
        out.append(l)
    return out


# ----------------------------------------------------------------------------- user request tracker

class Mon:
    def __init__(self, s):
        self.set = s
        self.species = s["species"]
        self.nickname = s.get("name") or default_nickname(s["species"])
        self.level = int(s.get("level") or 100)
        self.gender = s.get("gender") or ""
        shown = default_nickname(self.species) if self.species in ("Greninja-Bond", "Rockruff-Dusk") else self.species
        self.details = shown + (f", L{self.level}" if self.level != 100 else "") + \
            (f", {self.gender}" if self.gender else "")
        st = calc_stats(self.species, self.level, s["evs"], s["ivs"], s.get("nature") or "serious")
        self.maxhp = st.pop("hp")
        self.hp = self.maxhp
        self.stats = st
        self.status = ""
        self.item = _norm(s.get("item"))
        self.base_ability = _norm(s.get("ability"))
        self.ability = self.base_ability
        self.moves = []
        for m in s.get("moves") or []:
            mid = _norm(m)
            pp = all_move_json.get(mid, {}).get("pp", 5)
            maxpp = pp if all_move_json.get(mid, {}).get("noPPBoosts") else pp * 8 // 5
            self.moves.append({"id": mid, "name": m, "pp": maxpp, "maxpp": maxpp})
        tt = s.get("teraType") or (species_types(self.species) or ["normal"])[0].capitalize()
        self.tera_type = tt
        self.terastallized = ""
        self.ate_berry = False

    def condition(self):
        if self.hp <= 0:
            return "0 fnt"
        return f"{self.hp}/{self.maxhp}" + (f" {self.status}" if self.status else "")

    def types(self):
        if self.terastallized:
            return [self.terastallized.lower()]
        return species_types(self.details.split(",")[0])


class UserTracker:
    """Follows the user's side through the log and emits Showdown-style request JSON."""

    def __init__(self, sets, side, username):
        self.side = side
        self.opp = "p2" if side == "p1" else "p1"
        self.username = username
        self.mons = [Mon(s) for s in sets]  # Showdown side.pokemon order (active first once started)
        self.active = None
        self.started = False
        self.tera_used = False
        self.rqid = 0
        self.opp_species = None
        self.opp_ability = None
        self.opp_ngas = False
        self._reset_volatiles()

    def _reset_volatiles(self):
        self.last_move = None
        self.choice_lock = None
        self.encore = None
        self.taunt = False
        self.disable = None
        self.torment = False
        self.healblock = False
        self.throatchop = False
        self.locked = None          # move id or 'recharge'
        self.charge = None          # pending two-turn move
        self.trap_self = False      # ingrain / no retreat
        self.trap_foe = False       # mean look, partial trap, jaw lock, octolock (end when the foe leaves)
        self.lock_fresh = False

    # --- lookup
    def find(self, ident, details=None):
        nick = ident.split(": ", 1)[1].strip() if ": " in ident else ident
        if details:
            sp = _norm(details.split(",")[0])
            cands = [m for m in self.mons if _norm(m.details.split(",")[0]) == sp or _norm(m.species) == sp]
            if not cands:
                cands = [m for m in self.mons if sp.startswith(_norm(m.species)) or _norm(m.species).startswith(sp)]
            if len(cands) == 1:
                return cands[0]
            named = [m for m in cands if m.nickname == nick]
            if named:
                return named[0]
        for m in self.mons:
            if m.nickname == nick:
                return m
        if ident[2:3] == "a" and self.active is not None:
            return self.active
        raise KeyError(f"user pokemon not found: {ident} {details}")

    def apply_preview(self, details_list):
        """Use the team-preview details Showdown shows for the user's own Pokemon (display forme, gender)."""
        free = list(self.mons)
        for det in details_list:
            sp = det.split(",")[0]
            if sp.endswith("-*"):
                continue
            n = _norm(sp)
            m = next((m for m in free if _norm(m.species) == n), None) or \
                next((m for m in free if _norm(m.species).startswith(n) or n.startswith(_norm(m.species))), None)
            if m is None:
                continue
            free.remove(m)
            m.details = det

    def is_user(self, ident):
        return ident.startswith(self.side)

    # --- events
    def feed(self, line):
        p = line.split("|")
        if len(p) < 2:
            return
        tag = p[1]
        if tag in ("switch", "drag") and len(p) > 4:
            if self.is_user(p[2]):
                mon = self.find(p[2], p[3])
                if self.active is not None and self.active is not mon:
                    old = self.active
                    if old.hp > 0 and old.ability == "regenerator" and not self.opp_ngas:
                        old.hp = min(old.maxhp, old.hp + old.maxhp // 3)  # silent in the log
                    old.ability = old.base_ability
                    i, j = self.mons.index(old), self.mons.index(mon)
                    self.mons[i], self.mons[j] = self.mons[j], self.mons[i]
                elif self.active is None:
                    j = self.mons.index(mon)
                    self.mons.insert(0, self.mons.pop(j))
                self.active = mon
                mon.nickname = p[2].split(": ", 1)[1]
                mon.details = ", ".join(x for x in p[3].split(", ") if not x.startswith("tera:"))
                self._set_hp(mon, p[4])
                self._reset_volatiles()
                self.started = True
            else:
                self.opp_species = p[3].split(",")[0]
                self.opp_ability = None
                self.opp_ngas = False
                self.trap_foe = False
            return
        if tag == "-end" and len(p) > 3 and p[3] == "ability: Neutralizing Gas":
            self.opp_ngas = False
        if tag == "-damage" and "[from] item: Sticky Barb" in line:
            # Sticky Barb moves silently to a contact attacker without an item; the damage reveals where it is
            if self.is_user(p[2]):
                self.find(p[2]).item = "stickybarb"
            else:
                for m in self.mons:
                    if m.item == "stickybarb":
                        m.item = ""
        if tag == "-ability" and len(p) > 3 and not self.is_user(p[2]):
            self.opp_ability = _norm(p[3])
            if self.opp_ability == "neutralizinggas":
                self.opp_ngas = True
            return
        if "[of] " + self.opp in line and "[from] ability:" in line:
            m = re.search(r"\[from\] ability: ([^|]+)\|\[of\] " + self.opp, line)
            if m:
                self.opp_ability = _norm(m.group(1))
        if tag == "-item" and len(p) > 3 and not self.is_user(p[2]) and f"[of] {self.side}" in line and \
                re.search(r"\[from\] (ability: (Magician|Pickpocket)|move: (Thief|Covet))", line):
            m = re.search(r"\[of\] (" + self.side + r"a?: [^|]+)", line)
            if m:
                self.find(m.group(1)).item = ""
        if len(p) < 3 or not self.is_user(p[2]):
            if tag == "-fieldactivate" and "Fairy Lock" in line:
                self.trap_foe = True
            if self.lock_fresh and self.locked in LOCKING_MOVES and (
                    tag == "-immune" or (tag == "-activate" and re.search(r"move: (Protect|Detect|King's Shield|Spiky Shield|Baneful Bunker|Silk Trap|Burning Bulwark|Obstruct)", line))):
                self.locked = None
            if tag in ("move", "upkeep", "turn"):
                self.lock_fresh = False
            return
        ident = p[2]
        if tag == "detailschange" and len(p) > 3:
            mon = self.find(ident)
            mon.details = ", ".join(x for x in p[3].split(", ") if not x.startswith("tera:"))
            entry = pokedex.get(_norm(mon.details.split(",")[0]))
            if entry and entry.get(constants.ABILITIES):
                # Showdown: a permanent forme change sets ability and base ability to the new forme's
                mon.ability = mon.base_ability = _norm(entry[constants.ABILITIES].get("0"))
        elif tag in ("-damage", "-heal", "-sethp") and len(p) > 3:
            mon = self.find(ident)
            self._set_hp(mon, p[3])
        elif tag == "faint":
            mon = self.find(ident)
            mon.hp = 0
            mon.status = ""
        elif tag == "-status" and len(p) > 3:
            self.find(ident).status = p[3]
        elif tag == "-curestatus":
            self.find(ident).status = ""
        elif tag == "-cureteam":
            for m in self.mons:
                m.status = ""
        elif tag == "-item" and len(p) > 3:
            self.find(ident).item = _norm(p[3])
            if self.find(ident) is self.active and self.choice_lock and _norm(p[3]) not in CHOICE_ITEMS:
                self.choice_lock = None
        elif tag == "-enditem" and len(p) > 3:
            mon = self.find(ident)
            if "[eat]" in line or (_norm(p[3]).endswith("berry") and "[from] stealeat" not in line):
                mon.ate_berry = True
            mon.item = ""
            if _norm(p[3]) == "powerherb" and mon is self.active:
                self.charge = None
        elif tag == "-ability" and len(p) > 3 and "[from]" in line and ("Trace" in line or "move:" in line):
            self.find(ident).ability = _norm(p[3])
        elif tag == "-terastallize" and len(p) > 3:
            mon = self.find(ident)
            mon.terastallized = p[3]
            self.tera_used = True
        elif tag == "move" and len(p) > 3:
            self._on_move(p, line)
        elif tag == "-prepare" and len(p) > 3:
            self.charge = _norm(p[3])
        elif tag == "-anim" and len(p) > 3 and self.charge == _norm(p[3]):
            self.charge = None
        elif tag == "-mustrecharge":
            self.locked = "recharge"
        elif tag in ("-miss", "-fail") and self.lock_fresh and self.locked in LOCKING_MOVES:
            self.locked = None  # a first Outrage that fails or misses does not lock
        elif tag == "cant" and len(p) > 3:
            if p[3] == "recharge" or (self.locked and self.locked != "recharge" and self.locked not in LOCKING_MOVES):
                self.locked = None
            if p[3] == "slp":
                self.locked = None if self.locked in LOCKING_MOVES else self.locked
        elif tag == "-start" and len(p) > 3:
            what = p[3]
            if what == "Encore":
                self.encore = self.last_move
            elif what in ("move: Taunt", "Taunt"):
                self.taunt = True
            elif what in ("Disable", "move: Disable") and len(p) > 4:
                self.disable = _norm(p[4])
            elif what in ("Torment", "move: Torment"):
                self.torment = True
            elif what in ("move: Heal Block", "Heal Block"):
                self.healblock = True
            elif what in ("Throat Chop", "move: Throat Chop"):
                self.throatchop = True
            elif what == "confusion" and "[fatigue]" in line:
                self.locked = None
            elif what in ("move: Ingrain", "move: No Retreat", "No Retreat", "Ingrain"):
                self.trap_self = True
            elif what in ("move: Octolock", "Octolock"):
                self.trap_foe = True
        elif tag == "-end" and len(p) > 3:
            what = p[3]
            if what == "Encore":
                self.encore = None
            elif what in ("move: Taunt", "Taunt"):
                self.taunt = False
            elif what in ("Disable", "move: Disable"):
                self.disable = None
            elif what in ("move: Heal Block", "Heal Block"):
                self.healblock = False
            elif what in ("Throat Chop", "move: Throat Chop"):
                self.throatchop = False
            elif "[partiallytrapped]" in line or what in ("move: Octolock", "Octolock"):
                self.trap_foe = False
        elif tag == "-activate" and len(p) > 3:
            what = p[3]
            if what == "trapped" or what == "move: Jaw Lock":
                self.trap_foe = True
            elif what.startswith("move: ") and _norm(what[6:]) in PARTIAL_TRAP:
                self.trap_foe = True

    def _set_hp(self, mon, field):
        cur, mx, st = _split_hp(field)
        if st == "fnt":
            mon.hp = 0
            mon.status = ""
            return
        mon.hp = cur
        if mx and mx != 100 or (mx == 100 and mon.maxhp == 100):
            mon.maxhp = mx
        mon.status = st if st else ""

    def _on_move(self, p, line):
        mon = self.active
        if mon is None:
            return
        mid = _norm(p[3])
        tags = p[4:]
        frm = [t for t in tags if t.startswith("[from]")]
        if frm and not any("lockedmove" in t for t in frm):
            return  # called (Sleep Talk, Metronome...) or reflected (Magic Bounce, Dancer) move
        if frm:  # [from]lockedmove: continuing a locked/two-turn move, no PP used
            if mid in LOCKING_MOVES:
                self.lock_fresh = True  # the lock is renewed only if this use hits
            if self.charge == mid:
                self.charge = None
            if self.locked == mid and mid not in LOCKING_MOVES:
                self.locked = None  # the two-turn move executed
            return
        if mid == "struggle":
            return
        slot = next((m for m in mon.moves if m["id"] == mid), None)
        if slot is not None:
            cost = 1
            info = all_move_json.get(mid, {})
            tgt = info.get("target")
            # Showdown getMoveTargets: Pressure counts actual foe targets (none on [notarget]); foe-side
            # moves only with the mustpressure flag (hazards); 'all' moves include the foes.
            hits_foe = tgt in ("normal", "any", "adjacentFoe", "allAdjacentFoes", "allAdjacent", "randomNormal",
                               "all") or (tgt == "foeSide" and info.get("flags", {}).get("mustpressure"))
            if self.opp_ability == "pressure" and not self.opp_ngas and hits_foe and "[notarget]" not in line:
                cost = 2
            slot["pp"] = max(0, slot["pp"] - cost)
        self.last_move = mid
        if mon.item in CHOICE_ITEMS and self.choice_lock is None:
            self.choice_lock = mid
        if mid in LOCKING_MOVES:
            self.locked = mid
            self.lock_fresh = True
        elif self.locked in LOCKING_MOVES:
            self.locked = None
        if "[still]" in tags:
            self.charge = mid  # confirmed by -prepare; cleared if it executes this turn

    def end_of_segment(self):
        """Called at each request: a pending two-turn move becomes a lock."""
        if self.charge:
            self.locked = self.charge if all_move_json.get(self.charge, {}).get("flags", {}).get("charge") else self.locked
            self.charge = None

    # --- request
    def _entry(self, mon, active):
        return {"ident": f"{self.side}: {mon.nickname}", "details": mon.details, "condition": mon.condition(),
                "active": active, "stats": dict(mon.stats), "moves": [m["id"] for m in mon.moves],
                "baseAbility": mon.base_ability, "item": mon.item, "pokeball": "pokeball", "ability": mon.ability,
                "commanding": False, "reviving": False, "teraType": mon.tera_type,
                "terastallized": mon.terastallized}

    def side_data(self):
        return {"name": self.username, "id": self.side,
                "pokemon": [self._entry(m, i == 0) for i, m in enumerate(self.mons)]}

    def _maybe_trapped(self):
        mon = self.active
        if mon.item == "shedshell" or "ghost" in mon.types() or not self.opp_species:
            return False
        abilities = [_norm(a) for a in pokedex.get(_norm(self.opp_species), {}).get(constants.ABILITIES, {}).values()]
        # Shadow Tag and Arena Trap are banned in Gen 9 OU (the ruleset skips them); Magnet Pull is not.
        if "magnetpull" in abilities and "steel" in mon.types():
            return True
        return False

    def active_data(self):
        mon = self.active
        if self.locked:
            mid = self.locked
            name = "Recharge" if mid == "recharge" else all_move_json.get(mid, {}).get("name", mid)
            return {"moves": [{"move": name, "id": mid}], "trapped": True}
        moves = []
        any_ok = False
        for m in mon.moves:
            info = all_move_json.get(m["id"], {})
            status_move = info.get(constants.CATEGORY) == constants.MoveCategory.STATUS
            flags = info.get("flags", {})
            d = m["pp"] <= 0
            if self.choice_lock and mon.item in CHOICE_ITEMS and m["id"] != self.choice_lock:
                d = True
            if self.encore and m["id"] != self.encore:
                d = True
            if self.taunt and status_move:
                d = True
            if self.disable == m["id"]:
                d = True
            if self.torment and m["id"] == self.last_move:
                d = True
            if self.healblock and flags.get("heal"):
                d = True
            if self.throatchop and flags.get("sound"):
                d = True
            if mon.item == "assaultvest" and status_move:
                d = True
            if m["id"] in ("gigatonhammer", "bloodmoon") and self.last_move == m["id"]:
                d = True
            if m["id"] == "belch" and not mon.ate_berry:
                d = True
            if m["id"] == "stuffcheeks" and not mon.item.endswith("berry"):
                d = True
            any_ok |= not d
            moves.append({"move": m["name"], "id": m["id"], "pp": m["pp"], "maxpp": m["maxpp"],
                          "target": info.get("target", "normal"), "disabled": d})
        if not any_ok:
            return {"moves": [{"move": "Struggle", "id": "struggle", "target": "randomNormal", "disabled": False}]}
        data = {"moves": moves}
        if (self.trap_self or self.trap_foe) and "ghost" not in mon.types() and mon.item != "shedshell":
            data["trapped"] = True
        elif self._maybe_trapped():
            data["maybeTrapped"] = True
        if not self.tera_used and not mon.terastallized:
            data["canTerastallize"] = mon.tera_type
        return data

    def request(self, kind):
        self.rqid += 1
        if kind == "teampreview":
            return {"teamPreview": True, "side": self.side_data(), "rqid": self.rqid}
        if kind == "wait":
            return {"wait": True, "side": self.side_data(), "rqid": self.rqid}
        if kind == "switch":
            return {"forceSwitch": [True], "side": self.side_data(), "noCancel": True, "rqid": self.rqid}
        return {"active": [self.active_data()], "side": self.side_data(), "rqid": self.rqid}


# ----------------------------------------------------------------------------- driver

def _tag(line):
    p = line.split("|")
    return p[1] if len(p) > 1 else ""


def _is_filler(line):
    return line.strip() in ("", "|") or _tag(line) in SKIP_LINES


class Decision:
    def __init__(self, idx, turn, k, team_preview, kind, log_index):
        self.idx, self.turn, self.k, self.team_preview, self.kind = idx, turn, k, team_preview, kind
        self.log_index = log_index
        self.view = None
        self.battle = None          # the Battle copy Foul Play would search from (find_best_move input)
        self.action = None          # action read from the log (None: not revealed)
        self.tera = False
        self.action_source = None
        self.legal = None
        self.active_name = None
        self.candidates = None
        self.opp_action = None      # the opponent's turn-start action read from the log (None: hidden)
        self.opp_tera = False
        self.opp_hidden = False

    def to_dict(self):
        return {"idx": self.idx, "turn": self.turn, "k": self.k, "team_preview": self.team_preview,
                "kind": self.kind, "log_index": self.log_index, "view": self.view, "legal": self.legal,
                "action": self.action, "action_source": self.action_source,
                "candidates": self.candidates, "opp_action": self.opp_action}


def _view(b):
    """The decision-logger v2 view of a battle (the copy find_best_move logs)."""
    return {"turn": b.turn, "team_preview": b.team_preview,
            "weather": str(b.weather) if b.weather else None, "field": str(b.field) if b.field else None,
            "trick_room": b.trick_room, "user": declog._side(b.user), "opponent": declog._side(b.opponent)}


def _search_copy(battle):
    """What find_best_move logs: a deepcopy, with the team-preview actives popped."""
    b = deepcopy(battle)
    if b.team_preview:
        b.user.active = b.user.reserve.pop(0)
        b.opponent.active = b.opponent.reserve.pop(0)
    return b


def legal_actions(battle, iterations=200):
    """Root options poke-engine generates for the user from the reconstructed state (one sampled world)."""
    from fp.search.poke_engine_helpers import battle_to_poke_engine_state
    from poke_engine import monte_carlo_tree_search
    b = _search_copy(battle)
    worlds = b.mode.prepare_battles(b, 1)
    state = battle_to_poke_engine_state(worlds[0][0])
    res = monte_carlo_tree_search(state, 0, iterations=iterations)
    return sorted(o.move_choice for o in res.side_one)


def players_from_log(lines):
    names = {}
    for l in lines:
        p = l.split("|")
        if len(p) > 3 and p[1] == "player" and p[3]:
            names[p[2]] = p[3]
    return names


def reconstruct(log, side, team, fmt="gen9ou", compute_legal=True, oracle_choices=None, legal_iterations=200):
    """Rebuild the user's decisions. Returns (decisions, info).

    oracle_choices: optional list of true choices (validation only) used *only* to set
    Foul Play's last_selected_move when the log does not reveal the action.
    """
    sets = normalize_sets(team)
    tracker_probe = [Mon(s) for s in sets]
    raw = log.split("\n") if isinstance(log, str) else list(log)
    names = players_from_log(raw)
    opp = "p2" if side == "p1" else "p1"
    lines, own_mode = normalize_log(raw, side, {m.nickname: m.maxhp for m in tracker_probe})
    try:
        tp = next(i for i, l in enumerate(lines) if l.startswith("|teampreview"))
    except StopIteration:
        raise ValueError("log has no |teampreview (only Gen 9 team-preview formats are supported)")

    spec = FormatSpec.from_format_string(fmt)
    battle = Battle("battle-review")
    battle.opponent.account_name = names.get(opp)
    battle.pokemon_format = fmt
    battle.generation = spec.generation
    battle.battle_type = spec.battle_type
    battle.mode = battle_mode(spec.battle_type)
    battle.opponent.name = opp
    battle.user.name = side
    battle.user.team_dict = export_to_dict(sets_to_export(sets))

    tracker = UserTracker(sets, side, names.get(side, side))
    tracker.apply_preview([l.split("|")[3] for l in lines[:tp] if l.startswith(f"|poke|{side}|")])
    opp_preview = [l.split("|")[3] for l in lines[:tp] if l.startswith(f"|poke|{opp}|")]

    req = tracker.request("teampreview")
    battle.request_json = req
    battle.user.initialize_first_turn_user_from_json(req)
    battle.rqid = req["rqid"]
    battle.initialize_team_preview(opp_preview, fmt)
    battle.during_team_preview()
    battle.mode.initialize_team_preview_datasets(
        fmt, set(p.name for p in battle.opponent.reserve + battle.user.reserve), "")

    decisions = []
    per_turn = defaultdict(int)

    def new_decision(kind, log_index, b_for_search):
        t = b_for_search.turn or 0
        d = Decision(len(decisions), t, per_turn[t], bool(b_for_search.team_preview), kind, log_index)
        per_turn[t] += 1
        sc = _search_copy(b_for_search)
        d.view = _view(sc)
        d.battle = b_for_search
        d.active_name = b_for_search.user.active.name if b_for_search.user.active else None
        decisions.append(d)
        return d

    # --- team preview decision (fp.modes.base.handle_team_preview)
    bc = deepcopy(battle)
    bc.user.active = Pokemon.get_dummy()
    bc.opponent.active = Pokemon.get_dummy()
    bc.team_preview = True
    tp_dec = new_decision("teampreview", tp, deepcopy(bc))
    lead_line = next((l for l in lines[tp:] if l.startswith(f"|switch|{side}a: ")), None)
    if lead_line:
        tp_dec.action = "switch " + _norm(lead_line.split("|")[3].split(",")[0])
        tp_dec.action_source = "log"
    battle.user.last_selected_move = LastUsedMove("teampreview", tp_dec.action or "", battle.turn)

    # --- main loop
    pending = []
    turn = 0
    actions_started = False
    start_switched = {side: False, opp: False}
    eject_pending = {side: False, opp: False}
    open_dec = None           # the turn-start move decision whose action is being read from the log
    group_left = 0            # remaining switch lines of the current forced-switch group
    group_dec = None

    def resolve_last_selected():
        """Set Foul Play's last_selected_move from the previous decision (as async_pick_move does)."""
        d = decisions[-1]
        if d.team_preview:
            return
        choice = d.action
        if choice is None:
            if oracle_choices is not None and d.idx < len(oracle_choices) and oracle_choices[d.idx]:
                choice = oracle_choices[d.idx]
            else:
                choice = guess_unrevealed_move(d.battle)
        if choice is None:
            return
        battle.user.last_selected_move = LastUsedMove(d.active_name or "", choice.removesuffix("-tera").removesuffix("-mega"),
                                                      d.turn)

    def flush(kind, log_index):
        nonlocal open_dec
        tracker.end_of_segment()
        if decisions and decisions[-1] is not None:
            resolve_last_selected()
        req = tracker.request(kind)
        battle.msg_list.extend(pending)
        pending.clear()
        protocol.request(battle, ["", "request", json.dumps(req)])
        protocol.process_battle_updates(battle)
        if kind in ("move", "switch") and not battle.wait:
            b = deepcopy(battle)
            b.user.update_from_request_json(b.request_json)
            return new_decision(kind, log_index, b)
        return None

    def finish_open():
        nonlocal open_dec
        if open_dec is not None and open_dec.action is None and open_dec.tera:
            open_dec.action_source = "tera-only"
        open_dec = None

    n = len(lines)
    for i in range(tp + 1, n):
        line = lines[i]
        tag = _tag(line)
        p = line.split("|")
        if tag in ("win", "tie"):
            break
        who = p[2][:2] if len(p) > 2 else ""

        if tag == "switch" and len(p) > 4 and turn >= 1:
            forced = actions_started or start_switched[who] or eject_pending[who]
            eject_pending[who] = False
            if not forced:
                start_switched[who] = True
                if who == side and open_dec is not None and open_dec.action is None:
                    open_dec.action = "switch " + _norm(p[3].split(",")[0])
                    open_dec.action_source = "log"
            elif group_left > 0:
                group_left -= 1
                if who == side and group_dec is not None:
                    group_dec.action = "switch " + _norm(p[3].split(",")[0])
                    group_dec.action_source = "log"
            else:
                # a request round: this switch plus the other side's adjacent forced switch
                group = [(i, who)]
                j = i + 1
                while j < n and (_is_filler(lines[j]) or _tag(lines[j]) == "switch"):
                    if _tag(lines[j]) == "switch":
                        w2 = lines[j].split("|")[2][:2]
                        if w2 == who or len(group) >= 2:
                            break
                        group.append((j, w2))
                    j += 1
                user_in = any(w == side for _, w in group)
                finish_open()
                d = flush("switch" if user_in else "wait", i)
                group_left = len(group) - 1
                group_dec = d
                if who == side and d is not None:
                    d.action = "switch " + _norm(p[3].split(",")[0])
                    d.action_source = "log"
        elif tag == "-heal" and "[from] move: Revival Blessing" in line and who == side:
            finish_open()
            d = flush("switch", i)
            if d is not None:
                d.action = "switch " + _norm(p[2].split(": ", 1)[1])
                d.action_source = "log"

        if tag in ("move", "cant", "faint", "upkeep") and turn >= 1:
            actions_started = True
        if EJECT_RE.search(line) and tag in ("-enditem", "-activate") and who:
            eject_pending[who] = True

        # read the opponent's turn-start action (used by hax.py; public information)
        if open_dec is not None and open_dec.opp_action is None and who == opp and not open_dec.opp_hidden:
            if tag == "switch" and len(p) > 4 and not actions_started:
                open_dec.opp_action = "switch " + _norm(p[3].split(",")[0])
            elif tag == "-start" and len(p) > 3 and p[3] == "Encore":
                open_dec.opp_hidden = True
            elif tag == "-terastallize":
                open_dec.opp_tera = True
            elif tag == "move" and len(p) > 3:
                frm = [t for t in p[4:] if t.startswith("[from]")]
                if not frm or any("lockedmove" in t for t in frm):
                    open_dec.opp_action = _norm(p[3]) + ("-tera" if open_dec.opp_tera else "")
            elif tag == "cant" and len(p) > 4 and p[4]:
                open_dec.opp_action = _norm(p[4]) + ("-tera" if open_dec.opp_tera else "")

        # read the user's turn-start action
        if open_dec is not None and open_dec.action is None and who == side:
            if tag == "-start" and len(p) > 3 and p[3] == "Encore" and not open_dec.action_source:
                open_dec.action_source = "hidden-by-encore"
            elif open_dec.action_source == "hidden-by-encore":
                pass
            elif tag == "-terastallize":
                open_dec.tera = True
            elif tag == "move" and len(p) > 3:
                frm = [t for t in p[4:] if t.startswith("[from]")]
                if not frm or any("lockedmove" in t for t in frm):
                    open_dec.action = _norm(p[3]) + ("-tera" if open_dec.tera else "")
                    open_dec.action_source = "log"
            elif tag == "cant" and len(p) > 4 and p[4]:
                open_dec.action = _norm(p[4]) + ("-tera" if open_dec.tera else "")
                open_dec.action_source = "log-cant"

        tracker.feed(line)
        pending.append(line)

        if tag == "turn":
            turn = int(p[2])
            actions_started = False
            start_switched = {side: False, opp: False}
            group_left = 0
            finish_open()
            open_dec = flush("move", i + 1)

    finish_open()
    # the state at the end of the log (after the last decision), for the last turn's luck
    battle.msg_list.extend(pending)
    pending.clear()
    try:
        protocol.process_battle_updates(battle)
    except Exception:  # noqa: BLE001
        pass
    final_battle = battle
    winner = next((l.split("|")[2] for l in lines if l.startswith("|win|")), None)
    if compute_legal:
        for d in decisions:
            try:
                d.legal = legal_actions(d.battle, legal_iterations)
            except Exception as e:  # noqa: BLE001
                d.legal = None
                d.legal_error = repr(e)
    for d in decisions:
        if d.action is None and d.kind == "move":
            infer_unrevealed(d)
    info = {"side": side, "opponent": opp, "own_hp_mode": own_mode, "players": names,
            "final_battle": final_battle, "winner_name": winner, "lines": lines,
            "user_won": None if winner is None else winner == names.get(side),
            "decisions": len(decisions), "unrevealed": sum(1 for d in decisions if d.action is None)}
    return decisions, info


def candidate_moves(d):
    """Move options consistent with an unrevealed action (it was not a switch; tera seen or not)."""
    if d.legal:
        opts = [m for m in d.legal if not m.startswith("switch ")]
    else:
        act = d.battle.user.active
        opts = [m.name for m in act.moves if not m.disabled] if act else []
        if act is not None and act.can_terastallize:
            opts += [m + "-tera" for m in opts]
    return [m for m in opts if m.endswith("-tera") == bool(d.tera)]


def infer_unrevealed(d):
    """If only one move was possible (choice lock, encore, locked move...), the action is known."""
    cands = candidate_moves(d)
    d.candidates = cands
    if len(cands) == 1:
        d.action = cands[0]
        d.action_source = "inferred-only-option"


def guess_unrevealed_move(battle):
    """Stand-in for an unrevealed move: the first usable priority-0 move (feeds speed inference only)."""
    act = battle.user.active
    if act is None:
        return None
    for m in act.moves:
        if not m.disabled and all_move_json.get(m.name, {}).get("priority", 0) == 0:
            return m.name
    return act.moves[0].name if act.moves else None


# ----------------------------------------------------------------------------- validation against farm logs

def _cmp_pkmn(a, b, hp_tol):
    diffs = []
    if a is None or b is None:
        return ["missing"] if (a is None) != (b is None) else []
    for k in ("name", "status", "item", "ability", "tera_type", "terastallized", "level"):
        if a.get(k) != b.get(k):
            diffs.append(k)
    if a.get("max_hp") != b.get("max_hp"):
        diffs.append("max_hp")
    if abs(float(a.get("hp") or 0) - float(b.get("hp") or 0)) > hp_tol * max(1, float(b.get("max_hp") or 1)):
        diffs.append("hp")
    if sorted(a.get("moves") or []) != sorted(b.get("moves") or []):
        diffs.append("moves")
    if (a.get("boosts") or {}) != (b.get("boosts") or {}):
        diffs.append("boosts")
    belief = []
    for k in ("impossible_items", "impossible_abilities", "speed_range", "can_have_choice_item", "removed_item",
              "nature", "evs", "can_terastallize"):
        av, bv = a.get(k), b.get(k)
        if isinstance(av, (list, tuple)) and isinstance(bv, (list, tuple)):
            av, bv = list(av), list(bv)
        if av != bv:
            belief.append(k)
    return diffs, belief


def compare_views(rec_view, log_view, hp_tol=0.0):
    """Field-level comparison. Returns (core_diffs, belief_diffs) as lists of 'side.slot.field'."""
    core, belief = [], []
    for k in ("weather", "field", "trick_room"):
        if rec_view.get(k) != log_view.get(k):
            core.append(k)
    for sd in ("user", "opponent"):
        rs, ls = rec_view[sd], log_view[sd]
        if (rs.get("side_conditions") or {}) != (ls.get("side_conditions") or {}):
            core.append(f"{sd}.side_conditions")
        r_all = ([rs["active"]] if rs.get("active") else []) + list(rs.get("reserve") or [])
        l_all = ([ls["active"]] if ls.get("active") else []) + list(ls.get("reserve") or [])
        if (rs.get("active") or {}).get("name") != (ls.get("active") or {}).get("name"):
            core.append(f"{sd}.active")
        if sorted(p["name"] for p in r_all) != sorted(p["name"] for p in l_all):
            core.append(f"{sd}.roster")
        lmap = {}
        for p in l_all:
            lmap.setdefault(p["name"], p)
        for p in r_all:
            q = lmap.get(p["name"])
            if q is None:
                continue
            res = _cmp_pkmn(p, q, hp_tol)
            if isinstance(res, list):
                core += [f"{sd}.{x}" for x in res]
                continue
            dcore, dbel = res
            slot = "active" if p is rs.get("active") else "reserve"
            core += [f"{sd}.{slot}.{x}" for x in dcore]
            belief += [f"{sd}.{slot}.{x}" for x in dbel]
    return core, belief


def _team_from_norm_sets(own_team):
    return [{"species": s["species"], "item": s["item"], "ability": s["ability"], "moves": s["moves"],
             "nature": s["nature"], "evs": s["evs"], "ivs": s["ivs"], "teraType": s["tera_type"],
             "level": s["level"]} for s in own_team]


def validate_side(job):
    """Reconstruct one (game, side) and compare each decision with the logged one."""
    log, recs, legal, hp_tol, use_oracle, public = job
    player = recs[0]["player"]
    if public:
        log = to_public_log(log)
    oracle = [r["choice"] for r in recs] if use_oracle else None
    try:
        decs, info = reconstruct(log, player, _team_from_norm_sets(recs[0]["own_team"]), compute_legal=legal,
                                 oracle_choices=oracle)
    except Exception as e:  # noqa: BLE001
        import traceback
        return {"crash": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(), "n": len(recs), "rows": []}
    rec_by = {(d.turn, d.k): d for d in decs}
    log_keys = {(r["turn"] or 0, r["k"]) for r in recs}
    rows = []
    for r in recs:
        d = rec_by.get((r["turn"] or 0, r["k"]))
        if d is None:
            rows.append({"uid": r["uid"], "found": False, "correct": False, "reasons": ["not_found"]})
            continue
        core, belief = compare_views(d.view, r["view"], hp_tol)
        if d.team_preview != bool(r.get("team_preview")):
            core.append("team_preview")
        act_ok = d.action == r["choice"]
        legal_ok = None
        if legal:
            legal_ok = d.legal is not None and sorted(d.legal) == sorted(r["legal"])
        reasons = []
        if not act_ok:
            reasons.append("action_unrevealed" if d.action is None else "action")
        if legal_ok is False:
            reasons.append("legal" if d.legal is not None else "legal_error")
        reasons += ["view:" + c for c in sorted(set(core))]
        rows.append({"uid": r["uid"], "turn": r["turn"], "k": r["k"], "found": True,
                     "correct": act_ok and legal_ok is not False and not core,
                     "state_ok": legal_ok is not False and not core, "belief": sorted(set(belief)),
                     "act_ok": act_ok, "unrevealed": d.action is None, "legal_ok": legal_ok, "view_ok": not core,
                     "reasons": reasons, "rec_action": d.action, "log_choice": r["choice"],
                     "rec_legal": d.legal, "log_legal": r["legal"], "action_source": d.action_source})
    return {"crash": None, "n": len(recs), "rows": rows, "extra": sum(1 for k in rec_by if k not in log_keys),
            "info": {k: v for k, v in info.items() if k not in ("final_battle", "lines")}}


def validate(decisions_path, games_path, limit_games=None, out_path=None, legal=True, hp_tol=0.0,
             use_oracle_choices=False, verbose=False, workers=1, public=False):
    games = {}
    with gzip.open(games_path, "rt") if str(games_path).endswith(".gz") else open(games_path) as f:
        for l in f:
            g = json.loads(l)
            games[g["uid"]] = g
    by_game_side = defaultdict(list)
    with gzip.open(decisions_path, "rt") if str(decisions_path).endswith(".gz") else open(decisions_path) as f:
        for l in f:
            r = json.loads(l)
            by_game_side[(f"{r['run_id']}/g{r['game']:04d}", r["side"])].append(r)
    keys = sorted(by_game_side)
    if limit_games:
        uids = sorted({k[0] for k in keys})[:limit_games]
        keys = [k for k in keys if k[0] in uids]
    stats = defaultdict(int)
    mism = defaultdict(int)
    belief_mism = defaultdict(int)
    jobs = []
    for key in keys:
        g = games.get(key[0])
        if g is None or g.get("status") != "ok":
            stats["skipped_sides"] += 1
            continue
        jobs.append((g["log"], by_game_side[key], legal, hp_tol, use_oracle_choices, public))
    if workers > 1:
        from multiprocessing import get_context
        with get_context("spawn").Pool(workers) as pool:
            results = pool.map(validate_side, jobs, chunksize=1)
    else:
        results = [validate_side(j) for j in jobs]
    out = open(out_path, "w") if out_path else None
    for res in results:
        if res["crash"]:
            stats["crashed_sides"] += 1
            stats["decisions"] += res["n"]
            stats["wrong"] += res["n"]
            mism["crash:" + res["crash"].split(":")[0]] += res["n"]
            if verbose:
                print(res["trace"], file=sys.stderr)
            if out:
                out.write(json.dumps({"crash": res["crash"], "trace": res["trace"][-2000:]}) + "\n")
            continue
        stats["sides"] += 1
        stats["extra_reconstructed"] += res["extra"]
        for row in res["rows"]:
            stats["decisions"] += 1
            if not row["found"]:
                mism["not_found"] += 1
            else:
                stats["correct"] += int(row["correct"])
                stats["correct_state"] += int(row["state_ok"])
                stats["correct_state_and_belief"] += int(row["state_ok"] and not row["belief"])
                stats["action_ok"] += int(row["act_ok"])
                stats["action_unrevealed"] += int(row["unrevealed"])
                stats["action_ok_when_revealed"] += int(row["act_ok"] and not row["unrevealed"])
                stats["legal_ok"] += int(row["legal_ok"] is True)
                stats["view_ok"] += int(row["view_ok"])
                stats["view_and_belief_ok"] += int(row["view_ok"] and not row["belief"])
                for c in row["reasons"]:
                    mism[c] += 1
                for c in row["belief"]:
                    belief_mism["belief:" + c] += 1
            if out:
                out.write(json.dumps(row) + "\n")
    if out:
        out.close()
    n = max(1, stats["decisions"])
    summary = {"decisions": stats["decisions"], "sides": stats["sides"],
               "rate_correct": round(stats["correct"] / n, 4),
               "rate_state_correct": round(stats["correct_state"] / n, 4),
               "rate_state_and_belief_inputs": round(stats["correct_state_and_belief"] / n, 4),
               "rate_action": round(stats["action_ok"] / n, 4),
               "rate_action_when_revealed": round(stats["action_ok_when_revealed"] / max(1, n - stats["action_unrevealed"]), 4),
               "rate_action_revealed": round(1 - stats["action_unrevealed"] / n, 4),
               "rate_legal": round(stats["legal_ok"] / n, 4) if legal else None,
               "rate_view": round(stats["view_ok"] / n, 4),
               "rate_view_and_belief": round(stats["view_and_belief_ok"] / n, 4),
               "counts": dict(stats), "mismatches": dict(sorted(mism.items(), key=lambda kv: -kv[1])),
               "belief_mismatches": dict(sorted(belief_mism.items(), key=lambda kv: -kv[1]))}
    return summary


# ----------------------------------------------------------------------------- CLI

def load_log(path):
    """A log file: .json (Showdown replay JSON with 'log', or a farm battle-log JSON) or plain text."""
    text = Path(path).read_text()
    if path.endswith(".json"):
        d = json.loads(text)
        log = d.get("log")
        return log if isinstance(log, list) else log.split("\n"), d
    return text.split("\n"), {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="reconstruct one log")
    r.add_argument("log")
    r.add_argument("--side", required=True, choices=["p1", "p2"])
    r.add_argument("--team", required=True, help="team export text file")
    r.add_argument("--out", default="-")
    r.add_argument("--no-legal", action="store_true")
    v = sub.add_parser("validate", help="compare with farm decision logs (extractor output)")
    v.add_argument("decisions")
    v.add_argument("--games", default=None)
    v.add_argument("--limit-games", type=int, default=None)
    v.add_argument("--no-legal", action="store_true")
    v.add_argument("--oracle-choices", action="store_true",
                   help="use the logged choice for Foul Play's last_selected_move when the log hides it")
    v.add_argument("--details", default=None, help="per-decision JSONL of comparisons")
    v.add_argument("--summary", default=None)
    v.add_argument("--verbose", action="store_true")
    v.add_argument("--workers", type=int, default=1)
    v.add_argument("--public", action="store_true",
                   help="convert the farm log to public-replay format (percent HP for both sides) first; "
                        "own HP then compares within 1%% of max HP")
    args = ap.parse_args()
    if args.cmd == "run":
        lines, _ = load_log(args.log)
        decs, info = reconstruct(lines, args.side, Path(args.team).read_text(), compute_legal=not args.no_legal)
        out = sys.stdout if args.out == "-" else open(args.out, "w")
        for d in decs:
            out.write(json.dumps(d.to_dict(), default=str) + "\n")
        print(json.dumps({k: v for k, v in info.items() if k not in ("final_battle", "lines")}), file=sys.stderr)
    else:
        games = args.games or args.decisions.replace(".jsonl", ".games.jsonl")
        s = validate(args.decisions, games, args.limit_games, args.details, legal=not args.no_legal,
                     use_oracle_choices=args.oracle_choices, verbose=args.verbose,
                     workers=args.workers, public=args.public, hp_tol=0.011 if args.public else 0.0)
        txt = json.dumps(s, indent=1)
        print(txt)
        if args.summary:
            Path(args.summary).write_text(txt + "\n")


if __name__ == "__main__":
    main()
