#!/usr/bin/env python3
"""Parse raw Showdown replay JSON shards (crawl_replays.py) into HardRead tables.

Standard library only. Usage:
  python3 parse_replays.py [--raw RAW_DIR] [--out scratch_ds/human] [--limit N]

Outputs (OUT, default hardread/scratch_ds/human/, gitignored):
  games.jsonl.gz           one record per game, extractor-compatible (uid, run_id, game, winner, status,
                           player_a, turns, log) plus p1, p2, ratings, uploadtime, top-50 tags
  actions.jsonl.gz         one row per player per decision point (turn, k, kind, action, state, chance events)
  sets.jsonl.gz            one row per player per Pokemon at game end (revealed item/ability/tera/moves)
  species_profiles.json    aggregated item/ability/tera/move frequencies per species (no player info)
  summary.json, summary.md summary statistics
  PRIVATE/                 salt + pseudonym -> username map (LOCAL ONLY, never release)

Privacy: usernames are replaced by salted-hash pseudonyms (u_xxxxxxxxxx) in every output file. Chat, join/leave,
badges and html lines are dropped from the stored log; names inside `|player|`, `|win|`, `|-message|` and
side idents are replaced. The Showdown replay id is kept as `replay_id` for local traceability only; drop it
(and `uploadtime`) before any release, since replay ids link back to usernames on the public site.
"""
import argparse
import gzip
import hashlib
import json
import math
import os
import re
import secrets
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARDREAD = HERE.parent.parent
DEFAULT_RAW = Path("<PROJECT_ROOT>/data/replays/gen9ou-top/raw")
DEFAULT_OUT = HARDREAD / "scratch_ds" / "human"

HYPHEN_NAMES = {"hooh", "porygonz", "jangmoo", "hakamoo", "kommoo", "tinglu", "chienpao", "wochien", "chiyu"}
PIVOT_MOVES = {"uturn", "voltswitch", "flipturn", "partingshot", "teleport", "batonpass", "shedtail", "chillyreception"}
ALWAYS_CRIT = {"wickedblow", "surgingstrikes", "frostbreath", "stormthrow", "flowertrick"}
PRIORITY_MOVES = {"extremespeed", "fakeout", "firstimpression", "aquajet", "bulletpunch", "iceshard", "machpunch",
                  "quickattack", "shadowsneak", "suckerpunch", "vacuumwave", "watershuriken", "accelerock",
                  "jetpunch", "grassyglide", "protect", "detect", "spikyshield", "kingsshield", "banefulbunker",
                  "silktrap", "burningbulwark", "endure", "followme", "ragepowder", "helpinghand", "thunderclap"}
CALLED_FROM_OTHER = {"metronome", "copycat", "assist", "mirrormove", "mefirst", "naturepower", "magiccoat",
                     "snatch", "dancer", "magicbounce", "sleeptalk_target"}
# lines whose holder (of an `[from] ability/item:`) is the primary mon even when `[of]` is present
PRIMARY_TYPES = {"-heal", "-immune", "-boost", "-block", "-activate", "-start", "-fail", "-miss", "move",
                 "switch", "drag", "-fieldactivate", "-end", "-singleturn", "-ability", "-enditem", "-item"}
SKIP_LOG_TAGS = {"j", "J", "l", "L", "c", "c:", "chat", "raw", "html", "uhtml", "uhtmlchange", "badge", "n", "N",
                 "inactive", "inactiveoff", "join", "leave", "name", "debug", "seed", "unlink", "error", ""}
CHANCE_NET_TYPES = ("crit", "miss", "full_para", "flinch", "sleep_turn", "freeze_turn", "secondary")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def base_species(name):
    """Species with the forme suffix stripped ('Ogerpon-Wellspring-Tera' -> 'ogerpon')."""
    n = norm(name)
    for h in HYPHEN_NAMES:
        if n.startswith(h):
            return h
    return norm((name or "").split("-")[0])


def parse_ref(s):
    """'p1a: Nick' -> ('p1', 'Nick'); 'p1: Name' -> ('p1', 'Name')."""
    if not s or len(s) < 2 or s[0] != "p" or s[1] not in "12":
        return None, None
    side = s[:2]
    nick = s.split(": ", 1)[1] if ": " in s else ""
    return side, nick


def parse_hp(s):
    """'87/100 tox' -> (87.0, 'tox', False); '0 fnt' -> (0.0, None, True)."""
    s = (s or "").strip()
    if not s:
        return None, None, False
    parts = s.split(" ")
    status = None
    fnt = False
    for x in parts[1:]:
        if x == "fnt":
            fnt = True
        else:
            status = x
    cur = parts[0]
    if "/" in cur:
        a, b = cur.split("/")
        try:
            return (100.0 * float(a) / float(b)) if float(b) else None, status, fnt
        except ValueError:
            return None, status, fnt
    try:
        return float(cur), status, fnt
    except ValueError:
        return None, status, fnt


class Mon:
    def __init__(self, species, idx):
        self.idx = idx
        self.species_preview = species
        self.species = species
        self.hp = 100.0
        self.status = None
        self.fainted = False
        self.seen = False
        self.tera_type = None
        self.terastallized = False
        self.items = []         # {item, how, turn}
        self.item_state = None
        self.ability = None
        self.ability_how = None
        self.ability_turn = None
        self.moves = []
        self.moves_called = []
        self.switch_ins = 0
        self.turns_active = 0
        self.transformed = False
        self.first_seen_turn = None
        self.fainted_turn = None


class Side:
    def __init__(self, side):
        self.side = side
        self.name = None
        self.rating = None
        self.roster = []
        self.nick2idx = {}
        self.active = None
        self.conditions = {}
        self.alive_known = 6

    def match(self, species):
        """Roster index for a switch-in species (exact, then base species, then wildcard forms)."""
        n = norm(species)
        for m in self.roster:
            if norm(m.species) == n or norm(m.species_preview) == n:
                return m.idx
        b = base_species(species)
        c = [m for m in self.roster if base_species(m.species_preview) == b]
        if len(c) == 1:
            return c[0].idx
        if len(c) > 1:
            unseen = [m for m in c if not m.seen]
            return (unseen or c)[0].idx
        return None

    def n_alive(self):
        return sum(1 for m in self.roster if not m.fainted)


def snap_mon(side):
    if side.active is None:
        return None
    m = side.roster[side.active]
    return {"species": m.species, "hp": None if m.hp is None else round(m.hp, 1), "status": m.status,
            "tera": m.tera_type if m.terastallized else None}


class GameParser:
    def __init__(self, rep, names_to_uid, top50_ids):
        self.rep = rep
        self.sides = {"p1": Side("p1"), "p2": Side("p2")}
        self.turn = 0
        self.started = False
        self.rows = []
        self.k_count = defaultdict(int)
        self.body = None          # dict for the current turn body
        self.phase = "preview"    # preview | lead | body | post
        self.weather = None
        self.field = set()
        self.last_turn_state = None
        self.winner = None
        self.end_reason = "ko"
        self.rated = False
        self.rating_after = {}
        self.rating_delta = {}
        self.chance_events = []   # {turn, t, fav, move}
        self.flags = Counter()
        self.last_move = None     # {side, move, damaging, block_id}
        self.names_to_uid = names_to_uid
        self.top50_ids = top50_ids
        self.n_turn_lines = 0
        self.leads = {}
        self.eject_pending = {"p1": False, "p2": False}

    # --- state helpers ---------------------------------------------------
    def mon(self, ref):
        side, nick = parse_ref(ref)
        if side is None:
            return None, None
        s = self.sides[side]
        idx = s.nick2idx.get(nick)
        if idx is None and s.active is not None:
            idx = s.active
        if idx is None:
            return s, None
        return s, s.roster[idx]

    def snapshot(self):
        d = {"turn": self.turn, "weather": self.weather, "field": sorted(self.field)}
        for sd in ("p1", "p2"):
            s = self.sides[sd]
            d[sd] = {"active": snap_mon(s), "alive": s.n_alive(), "conditions": dict(s.conditions),
                     "seen": sum(1 for m in s.roster if m.seen)}
        return d

    def new_row(self, side, kind, snap):
        opp = "p2" if side == "p1" else "p1"
        key = (self.turn, side)
        k = self.k_count[key]
        self.k_count[key] += 1
        row = {"turn": self.turn, "k": k, "player": side, "kind": kind,
               "forced_switch": kind == "faint_replace",
               "single_option": False,
               "action_type": None, "action": None, "hidden_reason": None, "locked_move": False,
               "self_active": snap[side]["active"], "opp_active": snap[opp]["active"],
               "self_alive": snap[side]["alive"], "opp_alive": snap[opp]["alive"],
               "opp_seen": snap[opp]["seen"],
               "weather": snap["weather"], "field": snap["field"],
               "hazards_self": snap[side]["conditions"], "hazards_opp": snap[opp]["conditions"],
               "first_mover": None, "speed_tie_hint": None, "chance": []}
        self.rows.append(row)
        return row

    # --- reveal helpers --------------------------------------------------
    def reveal_item(self, mon, item, how):
        if mon is None or not item:
            return
        item = item.strip()
        if mon.items and mon.items[-1]["item"] == item and mon.items[-1]["how"] == how:
            return
        if any(e["item"] == item and e["how"] == how for e in mon.items):
            return
        mon.items.append({"item": item, "how": how, "turn": self.turn})

    def reveal_ability(self, mon, ability, how):
        if mon is None or not ability or mon.transformed:
            return
        ability = ability.strip()
        if mon.ability is None:
            mon.ability, mon.ability_how, mon.ability_turn = ability, how, self.turn

    def scan_item_ability(self, tag, args):
        """Generic reveal: `[from] item: X`, `ability: X`, `item: X` arguments."""
        primary = args[0] if args and parse_ref(args[0])[0] else None
        of = None
        for a in args:
            if a.startswith("[of] "):
                of = a[5:]
        for a in args:
            m = re.match(r"(?:\[from\]\s*)?(item|ability):\s*(.+)$", a)
            if not m:
                continue
            kind, name = m.group(1), m.group(2).strip()
            holder = primary
            if of and tag not in PRIMARY_TYPES:
                holder = of
            if kind == "ability" and tag == "-ability" and name == "Trace":
                holder = primary
            _, mon = self.mon(holder) if holder else (None, None)
            if mon is None:
                continue
            if kind == "item":
                self.reveal_item(mon, name, "message")
            else:
                self.reveal_ability(mon, name, "message")

    # --- chance events ---------------------------------------------------
    def chance(self, t, fav, move=None):
        ev = {"turn": self.turn, "t": t, "fav": fav, "move": move}
        self.chance_events.append(ev)
        if self.body is not None:
            self.body["chance"].append(ev)

    # --- main loop ---------------------------------------------------------
    def run(self):
        lines = self.rep["log"].split("\n")
        for ln in lines:
            if not ln.startswith("|"):
                continue
            p = ln.split("|")
            tag = p[1] if len(p) > 1 else ""
            args = p[2:]
            fn = getattr(self, "h_" + re.sub(r"[^a-z]", "_", tag.lower()), None) if tag else None
            if fn is not None:
                fn(args)
            elif tag.startswith("-"):
                self.generic(tag, args)
        self.end_turn_body()
        return self

    def generic(self, tag, args):
        if tag == "-activate" and args and any(x in ("ability: Emergency Exit", "ability: Wimp Out") for x in args[1:]):
            sd = parse_ref(args[0])[0]
            if sd:
                self.eject_pending[sd] = True
        self.scan_item_ability(tag, args)

    # handlers -----------------------------------------------------------
    def h_player(self, a):
        if len(a) >= 2 and a[0] in ("p1", "p2") and a[1]:
            s = self.sides[a[0]]
            s.name = a[1]
            if len(a) >= 4 and a[3].strip().isdigit():
                s.rating = int(a[3])

    def h_rated(self, a):
        self.rated = True

    def h_poke(self, a):
        if len(a) >= 2 and a[0] in ("p1", "p2"):
            s = self.sides[a[0]]
            sp = a[1].split(",")[0].strip()
            s.roster.append(Mon(sp, len(s.roster)))

    def h_teampreview(self, a):
        self.phase = "preview"

    def h_start(self, a):
        self.started = True
        self.phase = "lead"
        self.lead_snap = self.snapshot()

    def h_raw(self, a):
        txt = a[0] if a else ""
        m = re.match(r"(.+?)'s rating: (\d+) &rarr; <strong>(\d+)</strong>", txt)
        if m:
            self.rating_after[m.group(1)] = int(m.group(3))
            self.rating_delta[m.group(1)] = int(m.group(3)) - int(m.group(2))

    def h__message(self, a):
        txt = a[0] if a else ""
        if "forfeited" in txt:
            self.end_reason = "forfeit"
        elif "inactivity" in txt or "timer" in txt.lower():
            self.end_reason = "timeout"
        elif "disconnect" in txt.lower():
            self.end_reason = "disconnect"

    def h_win(self, a):
        for sd in ("p1", "p2"):
            if self.sides[sd].name == (a[0] if a else None):
                self.winner = sd
        self.end_turn_body()

    def h_tie(self, a):
        self.winner = "tie"
        self.end_turn_body()

    def h_turn(self, a):
        self.end_turn_body()
        self.turn = int(a[0])
        self.phase = "body"
        snap = self.snapshot()
        if self.turn == 1 and self.leads:
            self.turn = 0
            lsnap = dict(snap)
            for sd in ("p1", "p2"):
                if sd in self.leads:
                    r = self.new_row(sd, "lead", lsnap)
                    r["action_type"], r["action"] = "lead", self.leads[sd]
            self.turn = 1
        for sd in ("p1", "p2"):
            s = self.sides[sd]
            if s.active is not None:
                s.roster[s.active].turns_active += 1
        rows = {sd: self.new_row(sd, "turn", snap) for sd in ("p1", "p2")}
        self.body = {"snap": snap, "rows": rows, "acts": {"p1": {}, "p2": {}}, "chance": [], "order": [],
                     "tera": {}, "n_moves": 0}
        self.last_move = None

    def h_upkeep(self, a):
        if self.body is not None:
            self.phase = "post"
        self.last_move = None

    def h_switch(self, a, kind="switch"):
        if len(a) < 2:
            return
        side, nick = parse_ref(a[0])
        if side is None:
            return
        s = self.sides[side]
        sp = a[1].split(",")[0].strip()
        frm = next((x[6:].strip() for x in a[2:] if x.startswith("[from]")), None)
        idx = s.match(sp)
        if idx is None:   # unknown species (e.g. Illusion, missing preview)
            self.flags["unmatched_species"] += 1
            s.roster.append(Mon(sp, len(s.roster)))
            idx = len(s.roster) - 1
        m = s.roster[idx]
        prev_active = s.active
        prev_dead = prev_active is not None and s.roster[prev_active].fainted
        # decision classification
        row = None
        if kind == "switch":
            if self.phase == "lead":
                self.leads[side] = sp
            elif frm or self.eject_pending[side]:
                frm = frm or "Eject"
                row = self.new_row(side, "pivot", self.snapshot())
                row["action_type"], row["action"] = "switch", sp
                row["pivot_via"] = frm
                row["single_option"] = s.n_alive() <= 2
            elif prev_dead or self.phase == "post":
                row = self.new_row(side, "faint_replace", self.snapshot())
                row["action_type"], row["action"] = "switch", sp
                row["single_option"] = s.n_alive() <= 1
            elif self.body is not None:
                # voluntary switch in the turn body
                r = self.body["rows"][side]
                r["action_type"], r["action"] = "switch", sp
                self.body["acts"][side]["switched"] = True
                self.body["order"].append(side)
        self.last_move = None
        if kind == "switch":
            self.eject_pending[side] = False
        # apply
        s.active = idx
        s.nick2idx[nick] = idx
        if not m.seen:
            m.seen = True
            m.first_seen_turn = self.turn
        m.switch_ins += 1
        m.transformed = False
        hp, st, fnt = parse_hp(a[2]) if len(a) > 2 and not a[2].startswith("[") else (None, None, False)
        if hp is not None:
            m.hp = hp
        m.status = st
        if "tera:" in a[1]:
            t = a[1].split("tera:")[1].split(",")[0].strip()
            m.tera_type = t
            m.terastallized = True
        m.species = sp
        self.scan_item_ability("switch", [a[0]] + a[2:])

    def h_drag(self, a):
        self.h_switch(a, kind="drag")

    def h_replace(self, a):
        self.flags["illusion"] += 1
        self.h_switch(a, kind="replace")

    def h_detailschange(self, a):
        s, m = self.mon(a[0])
        if m is not None and len(a) > 1:
            m.species = a[1].split(",")[0].strip()

    def h__formechange(self, a):
        s, m = self.mon(a[0])
        if m is not None and len(a) > 1:
            m.species = a[1].split(",")[0].strip()
        self.generic("-formechange", a)

    def h__transform(self, a):
        s, m = self.mon(a[0])
        if m is not None:
            m.transformed = True
        self.flags["transform"] += 1

    def h_faint(self, a):
        s, m = self.mon(a[0])
        if m is not None:
            m.fainted = True
            m.hp = 0.0
            m.fainted_turn = self.turn

    def h_move(self, a):
        if len(a) < 3:
            return
        side, nick = parse_ref(a[0])
        if side is None:
            return
        s, m = self.mon(a[0])
        mv = a[1]
        mid = norm(mv)
        frm = next((x[6:].strip() for x in a[3:] if x.startswith("[from]")), None)
        self.scan_item_ability("move", [a[0]] + a[3:])
        miss_flag = any(x.startswith("[miss]") for x in a[3:])
        self.last_move = {"side": side, "move": mv, "mid": mid, "damaging": False, "miss": miss_flag}
        counts = False
        decision = False
        locked = False
        if frm is None:
            counts = True
            decision = True
        elif frm == "lockedmove":
            counts = True
            decision = True
            locked = True
        elif frm.replace("move: ", "").strip().lower() in ("sleep talk", "instruct"):
            counts = frm.lower().endswith("sleep talk") or "instruct" in frm.lower()
        if counts and m is not None and not m.transformed and mv != "Struggle":
            if mv not in m.moves:
                m.moves.append(mv)
        if frm is not None and not counts and m is not None and not m.transformed:
            self.flags["called_moves"] += 1
        if self.body is None or self.phase != "body":
            return
        acts = self.body["acts"][side]
        if decision and "move" not in acts and not acts.get("switched"):
            acts["move"] = mv
            acts["locked"] = locked
            r = self.body["rows"][side]
            r["action_type"], r["action"], r["locked_move"] = "move", mv, locked
            self.body["order"].append(side)
            self.body["n_moves"] += 1
            self.body.setdefault("mid", {})[side] = mid
        elif decision and "move" in acts:
            pass

    def h_cant(self, a):
        if len(a) < 2:
            return
        side, nick = parse_ref(a[0])
        if side is None or self.body is None:
            return
        reason = a[1]
        move = a[2] if len(a) > 2 else None
        if move and not move.startswith("["):
            _, cm = self.mon(a[0])
            if cm is not None and not cm.transformed and move not in cm.moves:
                cm.moves.append(move)      # the attempted move is shown (Taunt, Disable, Focus Punch...)
        acts = self.body["acts"][side]
        if "move" not in acts and not acts.get("switched"):
            acts["cant"] = reason
            r = self.body["rows"][side]
            r["action_type"], r["hidden_reason"] = "hidden", reason
            if move and not move.startswith("["):
                r["action"] = move   # the attempted move is shown for Taunt/Disable/Focus Punch etc.
            self.body["order"].append(side)
        opp = "p2" if side == "p1" else "p1"
        rl = reason.lower()
        if rl == "par":
            self.chance("full_para", opp)
        elif rl == "flinch":
            self.chance("flinch", opp)
        elif rl == "slp":
            self.chance("sleep_turn", opp)
        elif rl == "frz":
            self.chance("freeze_turn", opp)

    def h__terastallize(self, a):
        s, m = self.mon(a[0])
        if m is not None and len(a) > 1:
            m.tera_type = a[1].strip()
            m.terastallized = True
        side = parse_ref(a[0])[0]
        if self.body is not None and side:
            self.body["tera"][side] = a[1].strip()
            if self.body["acts"][side].get("move") is None:
                pass

    def h__damage(self, a):
        s, m = self.mon(a[0])
        side = parse_ref(a[0])[0]
        hp, st, fnt = parse_hp(a[1]) if len(a) > 1 else (None, None, False)
        frm = next((x[6:].strip() for x in a[2:] if x.startswith("[from]")), None)
        if m is not None and hp is not None:
            m.hp = hp
            if st:
                m.status = st
            if fnt:
                m.hp = 0.0
        if frm is None and self.last_move and side and side != self.last_move["side"]:
            self.last_move["damaging"] = True
        if frm == "confusion" and side:
            self.chance("confusion_self_hit", "p2" if side == "p1" else "p1")
        self.scan_item_ability("-damage", [a[0]] + a[2:])
        self.generic_of(a)

    def generic_of(self, a):
        pass

    def h__heal(self, a):
        s, m = self.mon(a[0])
        hp, st, fnt = parse_hp(a[1]) if len(a) > 1 else (None, None, False)
        if m is not None and hp is not None:
            m.hp = hp
        if m is not None and any(("Healing Wish" in x or "Lunar Dance" in x) for x in a[2:]):
            m.status = None
        if m is not None and any("Revival Blessing" in x for x in a[2:]):
            m.fainted = False
            self.flags["revival_blessing"] += 1
        self.scan_item_ability("-heal", [a[0]] + a[2:])

    def h__sethp(self, a):
        if len(a) >= 2:
            s, m = self.mon(a[0])
            hp, _, _ = parse_hp(a[1])
            if m is not None and hp is not None:
                m.hp = hp

    def h__status(self, a):
        s, m = self.mon(a[0])
        side = parse_ref(a[0])[0]
        if m is not None and len(a) > 1:
            m.status = a[1]
        frm = next((x for x in a[2:] if x.startswith("[from]")), None)
        if frm is None and self.last_move and self.last_move["damaging"] and side and side != self.last_move["side"]:
            self.chance("secondary", self.last_move["side"], self.last_move["move"])
        self.scan_item_ability("-status", a)

    def h__curestatus(self, a):
        s, m = self.mon(a[0])
        side = parse_ref(a[0])[0]
        if m is not None:
            m.status = None
        if len(a) > 1 and a[1] == "frz" and any(x.startswith("[msg]") for x in a[2:]) \
                and not any(x.startswith("[from]") for x in a[2:]) and side:
            self.chance("thaw", side)

    def h__cureteam(self, a):
        side = parse_ref(a[0])[0]
        if side:
            for m in self.sides[side].roster:
                m.status = None

    def h__unboost(self, a):
        side = parse_ref(a[0])[0]
        frm = next((x for x in a[3:] if x.startswith("[from]")), None)
        if frm is None and self.last_move and self.last_move["damaging"] and side and side != self.last_move["side"]:
            self.chance("secondary", self.last_move["side"], self.last_move["move"])
        self.scan_item_ability("-unboost", a)

    def h__start(self, a):
        side = parse_ref(a[0])[0]
        if len(a) > 1 and a[1] == "confusion" and self.last_move and self.last_move["damaging"] \
                and side and side != self.last_move["side"] and not any(x.startswith("[from]") for x in a[2:]):
            self.chance("secondary", self.last_move["side"], self.last_move["move"])
        self.scan_item_ability("-start", a)

    def h__crit(self, a):
        if self.last_move and self.last_move["mid"] not in ALWAYS_CRIT:
            self.chance("crit", self.last_move["side"], self.last_move["move"])

    def h__miss(self, a):
        side = parse_ref(a[0])[0]
        if side:
            self.chance("miss", "p2" if side == "p1" else "p1", self.last_move["move"] if self.last_move else None)

    def h__item(self, a):
        s, m = self.mon(a[0])
        if m is None or len(a) < 2:
            return
        how = "item_message"
        for x in a[2:]:
            if x.startswith("[from]"):
                v = x[6:].strip()
                how = "frisk" if "Frisk" in v else ("trick" if "Trick" in v or "Switcheroo" in v else
                                                    v.replace("move: ", "move:").replace("ability: ", "ability:"))
        self.reveal_item(m, a[1], how)
        self.scan_item_ability("-item", [a[0]] + a[2:])

    def h__enditem(self, a):
        s, m = self.mon(a[0])
        if m is None or len(a) < 2:
            return
        how = "consumed"
        for x in a[2:]:
            if x.startswith("[from]"):
                v = x[6:].strip()
                how = "knock_off" if "Knock Off" in v else v.replace("move: ", "move:").replace("ability: ", "ability:")
            elif x.startswith("[eat]"):
                how = "consumed"
        self.reveal_item(m, a[1], how)
        m.item_state = how
        if a[1] in ("Eject Button", "Eject Pack"):
            sd = parse_ref(a[0])[0]
            if sd:
                self.eject_pending[sd] = True
        self.scan_item_ability("-enditem", [a[0]] + [x for x in a[2:] if not x.startswith("[from] move")])

    def h__ability(self, a):
        s, m = self.mon(a[0])
        if m is None or len(a) < 2:
            return
        frm = next((x for x in a[2:] if x.startswith("[from]")), None)
        if frm and "Trace" in frm:
            self.reveal_ability(m, "Trace", "-ability")
            of = next((x[5:] for x in a[2:] if x.startswith("[of] ")), None)
            if of:
                _, om = self.mon(of)
                self.reveal_ability(om, a[1], "-ability")
            return
        if not frm:
            self.reveal_ability(m, a[1], "-ability")
        self.scan_item_ability("-ability", a)

    def h__weather(self, a):
        w = a[0] if a else None
        self.weather = None if w in (None, "none") else w
        self.scan_item_ability("-weather", a)

    def h__fieldstart(self, a):
        self.field.add(a[0].replace("move: ", "").strip())
        self.scan_item_ability("-fieldstart", a)

    def h__fieldend(self, a):
        self.field.discard(a[0].replace("move: ", "").strip())

    def h__sidestart(self, a):
        side = parse_ref(a[0])[0]
        if side and len(a) > 1:
            n = a[1].replace("move: ", "").strip()
            self.sides[side].conditions[n] = self.sides[side].conditions.get(n, 0) + 1

    def h__sideend(self, a):
        side = parse_ref(a[0])[0]
        if side and len(a) > 1:
            self.sides[side].conditions.pop(a[1].replace("move: ", "").strip(), None)

    def h__swapsideconditions(self, a):
        c1, c2 = self.sides["p1"].conditions, self.sides["p2"].conditions
        self.sides["p1"].conditions, self.sides["p2"].conditions = c2, c1

    # turn body flush ---------------------------------------------------------
    def end_turn_body(self):
        b = self.body
        if b is None:
            return
        self.body = None
        snap = b["snap"]
        order = [x for x in b["order"]]
        first = None
        # first mover: the first side whose action line appeared (switches come first by game rules)
        if order:
            first = order[0]
        moves_mid = b.get("mid", {})
        for sd in ("p1", "p2"):
            r = b["rows"][sd]
            opp = "p2" if sd == "p1" else "p1"
            if r["action_type"] is None:
                r["action_type"] = "hidden"
                r["hidden_reason"] = "no_action_shown"
            if sd in b["tera"]:
                r["tera_type"] = b["tera"][sd]
                if r["action_type"] == "move":
                    r["action_type"] = "tera_move"
                elif r["action_type"] == "hidden":
                    r["action_type"] = "tera_hidden"
            r["first_mover"] = None if first is None else ("self" if first == sd else "opp")
            sa, oa = snap[sd]["active"], snap[opp]["active"]
            if sa and oa and base_species(sa["species"]) == base_species(oa["species"]):
                if not (moves_mid.get(sd) in PRIORITY_MOVES or moves_mid.get(opp) in PRIORITY_MOVES):
                    r["speed_tie_hint"] = "mirror_match_same_species"
            r["chance"] = [{"t": e["t"], "favoured": "self" if e["fav"] == sd else "opp", "move": e["move"]}
                           for e in b["chance"]]


# ---------------------------------------------------------------------------- game-level driver

BANDS = [("lt1300", 0, 1300), ("1300-1499", 1300, 1500), ("1500-1699", 1500, 1700),
         ("1700-1899", 1700, 1900), ("1900plus", 1900, 10 ** 6)]


def band_of(r):
    if r is None:
        return None
    for name, lo, hi in BANDS:
        if lo <= r < hi:
            return name
    return None


class Pseudonymizer:
    def __init__(self, out, salt_from=None):
        pdir = out / "PRIVATE"
        pdir.mkdir(parents=True, exist_ok=True)
        sp = pdir / "salt.txt"
        if not sp.exists():
            if salt_from is not None and Path(salt_from).exists():
                sp.write_text(Path(salt_from).read_text())   # same pseudonyms as the top-50 dataset
            else:
                sp.write_text(secrets.token_hex(16))
        self.salt = sp.read_text().strip()
        self.map = {}

    def uid(self, name):
        u = norm(name)
        h = "u_" + hashlib.sha256((self.salt + ":" + u).encode()).hexdigest()[:10]
        self.map[h] = {"userid": u, "username": name}
        return h

    def aid(self, replay_id):
        return "g_" + hashlib.sha256((self.salt + ":" + replay_id).encode()).hexdigest()[:12]


def sanitize_log(rep_log, names, pseudo):
    out = _sanitize_log(rep_log, names, pseudo)
    # usernames can also appear inside Pokemon nicknames: replace any remaining occurrence (case-insensitive,
    # names of 4+ characters), consistently on every line so nickname identity is preserved
    pats = []
    for sd, nm in names.items():
        for variant in {nm, norm(nm)}:
            if len(variant) >= 4:
                pats.append((re.compile(re.escape(variant), re.I), pseudo[sd]))
    if pats:
        for i, ln in enumerate(out):
            for rx, rep in pats:
                if rx.search(ln):
                    ln = rx.sub(rep, ln)
            out[i] = ln
    return out


def _sanitize_log(rep_log, names, pseudo):
    out = []
    for ln in rep_log.split("\n"):
        if not ln.startswith("|"):
            continue
        p = ln.split("|")
        tag = p[1] if len(p) > 1 else ""
        if tag in SKIP_LOG_TAGS and tag != "":
            continue
        if tag == "" and ln.strip() in ("", "|"):
            out.append("|")
            continue
        if tag in ("-message", "win", "tie", "player") or "p1: " in ln or "p2: " in ln:
            for sd, nm in names.items():
                if nm:
                    ln = ln.replace(nm, pseudo[sd]) if tag in ("-message", "win", "player") else \
                        ln.replace(f"{sd}: {nm}", f"{sd}: {pseudo[sd]}")
        if tag == "player" and len(p) > 4:     # custom avatars can be named after the user: blank the avatar
            p = ln.split("|")
            p[4] = ""
            ln = "|".join(p)
        out.append(ln)
    return out


def ranks500(top500, name):
    return top500.get(norm(name), (None,))[0]


def parse_all(raw_dir, out_dir, limit=None, broad=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    ladder = json.loads((raw_dir / "ladder_top50.json").read_text())
    top = {u["userid"]: (i + 1, u) for i, u in enumerate(ladder["top50"])}
    top500, src_of = {}, {}
    if broad:
        l5 = json.loads((broad / "ladder_top500.json").read_text())
        top500 = {u["userid"]: (i + 1, u) for i, u in enumerate(l5["toplist"][:500])}
        ip = broad / "index.jsonl"
        if ip.exists():
            for ln in ip.read_text().splitlines():
                r = json.loads(ln)
                src_of.setdefault(r["id"], r["source"])
    ps = Pseudonymizer(out_dir, salt_from=DEFAULT_OUT / "PRIVATE" / "salt.txt" if broad else None)
    games_f = gzip.open(out_dir / "games.jsonl.gz", "wt")
    act_f = gzip.open(out_dir / "actions.jsonl.gz", "wt")
    set_f = gzip.open(out_dir / "sets.jsonl.gz", "wt")
    seen_ids = set()
    stats = {"games": 0, "skipped": Counter(), "turns": [], "ratings": [], "unrated": 0,
             "players": set(), "usage": Counter(), "usage_top": Counter(), "lead": Counter(),
             "end_reason": Counter(), "both_top50": 0, "one_top50": 0, "teams": 0,
             "seen_mons": 0, "item": 0, "ability": 0, "tera_used": 0, "moves_hist": Counter(),
             "moves_sum": 0, "chance_totals": Counter(), "chance_per_game": defaultdict(list),
             "net_vs_win": [], "decisions": Counter(), "hidden": Counter(), "flags": Counter(),
             "uploadtimes": [], "tera_games": 0, "items_how": Counter(), "ability_how": Counter(),
             "n_sets_rows": 0, "side_wins": Counter(), "higher_rated_wins": Counter(),
             "ratings_top": [], "ratings_other": [], "top_players": set(), "act_top": Counter(),
             "act_other": Counter(), "tera_top": [0, 0]}
    profiles = defaultdict(lambda: {"n": 0, "items": Counter(), "abilities": Counter(), "tera": Counter(),
                                    "moves": Counter(), "n_item": 0, "n_ability": 0, "n_tera": 0})
    shards = sorted(raw_dir.glob("replays-*.jsonl.gz"))
    if broad:
        shards = sorted(broad.glob("replays-*.jsonl.gz")) + shards
    n_game = 0
    for sh in shards:
        with gzip.open(sh, "rt") as f:
            for line in f:
                try:
                    rep = json.loads(line)
                except Exception:
                    stats["skipped"]["bad_json"] += 1
                    continue
                rid = rep.get("id")
                if rid in seen_ids:
                    stats["skipped"]["duplicate"] += 1
                    continue
                seen_ids.add(rid)
                if rep.get("formatid") != "gen9ou" or rep.get("private") or rep.get("password"):
                    stats["skipped"]["not_gen9ou_or_private"] += 1
                    continue
                if limit and n_game >= limit:
                    break
                try:
                    g = GameParser(rep, None, set(top)).run()
                except Exception as e:
                    stats["skipped"][f"parse_error:{type(e).__name__}"] += 1
                    continue
                s1, s2 = g.sides["p1"], g.sides["p2"]
                for sx in (s1, s2):
                    for m in sx.roster:
                        if "*" in m.species_preview and m.seen:
                            m.species_preview = m.species
                if not s1.name or not s2.name or len(s1.roster) < 6 or len(s2.roster) < 6 or g.turn < 1:
                    stats["skipped"]["incomplete_log"] += 1
                    continue
                if g.winner is None:
                    stats["skipped"]["no_winner"] += 1
                    continue
                n_game += 1
                names = {"p1": s1.name, "p2": s2.name}
                pseudo = {sd: ps.uid(nm) for sd, nm in names.items()}
                uid = "human/" + rid
                extra = {}
                if broad:
                    pre = [x for x in (s1.rating, s2.rating) if x is not None]
                    rb = band_of(rep.get("rating") if rep.get("rating") is not None else (min(pre) if pre else None))
                    extra = {"source": src_of.get(rid, "top50"), "rating_band": rb or "unrated",
                             "rating_band_pre": band_of(min(pre)) if pre else "unrated"}
                ranks = {sd: top.get(norm(nm), (None, None)) for sd, nm in names.items()}
                intop = {sd: ranks[sd][0] is not None for sd in names}
                r1, r2 = s1.rating, s2.rating
                higher = None if (r1 is None or r2 is None or r1 == r2) else ("p1" if r1 > r2 else "p2")
                log = sanitize_log(rep["log"], names, pseudo)
                chance_counts = {sd: Counter() for sd in ("p1", "p2")}
                for e in g.chance_events:
                    chance_counts[e["fav"]][e["t"]] += 1
                net = {}
                for sd in ("p1", "p2"):
                    opp = "p2" if sd == "p1" else "p1"
                    net[sd] = sum(chance_counts[sd][t] - chance_counts[opp][t] for t in CHANCE_NET_TYPES)
                win_ab = None if g.winner == "tie" else ("A" if g.winner == "p1" else "B")
                games_f.write(json.dumps({**extra,
                    **({"top500": {sd: ranks500(top500, names[sd]) is not None for sd in ("p1", "p2")}} if broad else {}),
                    "uid": uid, "run_id": "human-gen9ou-broad" if broad else "human-gen9ou-top", "game": n_game - 1, "winner": win_ab,
                    "winner_player": g.winner, "status": "ok", "player_a": "p1",
                    "replay_id": rid, "uid_anon": ps.aid(rid), "format": "gen9ou",
                    "p1": pseudo["p1"], "p2": pseudo["p2"],
                    "ratings": {"p1": r1, "p2": r2, "replay_field": rep.get("rating"),
                                "p1_after": g.rating_after.get(names["p1"]), "p2_after": g.rating_after.get(names["p2"])},
                    "rated": g.rated or rep.get("rating") is not None,
                    "turns": g.turn, "uploadtime": rep.get("uploadtime"), "end_reason": g.end_reason,
                    "top50": {"p1": intop["p1"], "p2": intop["p2"], "both": intop["p1"] and intop["p2"]},
                    "higher_rated": higher, "chance_counts": {sd: dict(c) for sd, c in chance_counts.items()},
                    "chance_net": net, "flags": dict(g.flags), "log": log}) + "\n")
                # actions
                for r in g.rows:
                    sd = r["player"]
                    r.update({"uid": uid, "player_uid": pseudo[sd], "top50": intop[sd],
                              "rating": s1.rating if sd == "p1" else s2.rating,
                              "won": None if g.winner == "tie" else int(g.winner == sd)})
                    r.update(extra)
                    act_f.write(json.dumps(r) + "\n")
                    stats["decisions"][r["kind"]] += 1
                    if r["kind"] == "turn":
                        (stats["act_top"] if intop[sd] else stats["act_other"])[r["action_type"]] += 1
                    stats["hidden"][(r["kind"], r["action_type"] == "hidden" or r["action_type"] == "tera_hidden")] += 1
                # sets
                for sd, s in g.sides.items():
                    stats["teams"] += 1
                    won = None if g.winner == "tie" else int(g.winner == sd)
                    for m in s.roster:
                        orig_item = next((e for e in m.items if e["how"] != "trick"), None)
                        item_how = None if orig_item is None else orig_item["how"]
                        row = {"uid": uid, "player": sd, "player_uid": pseudo[sd], "top50": intop[sd], "won": won,
                               "species_preview": m.species_preview, "species": m.species,
                               "base_species": base_species(m.species_preview),
                               "in_preview": True, "seen": m.seen, "switch_ins": m.switch_ins,
                               "turns_active": m.turns_active, "fainted": m.fainted,
                               "item": None if orig_item is None else orig_item["item"], "item_how": item_how,
                               "item_events": m.items, "item_final_state": m.item_state,
                               "ability": m.ability, "ability_how": m.ability_how,
                               "tera_type": m.tera_type, "terastallized": m.terastallized,
                               "moves": m.moves, "n_moves_seen": len(m.moves)}
                        row.update(extra)
                        set_f.write(json.dumps(row) + "\n")
                        stats["n_sets_rows"] += 1
                        stats["usage"][m.species_preview] += 1
                        if m.seen:
                            stats["seen_mons"] += 1
                            stats["item"] += orig_item is not None
                            stats["ability"] += m.ability is not None
                            stats["tera_used"] += m.terastallized
                            stats["moves_hist"][min(len(m.moves), 4)] += 1
                            stats["moves_sum"] += len(m.moves)
                            if item_how:
                                stats["items_how"][item_how.split(":")[0]] += 1
                            if m.ability_how:
                                stats["ability_how"][m.ability_how] += 1
                            P = profiles[m.species_preview]
                            P["n"] += 1
                            if orig_item:
                                P["items"][orig_item["item"]] += 1
                                P["n_item"] += 1
                            if m.ability:
                                P["abilities"][m.ability] += 1
                                P["n_ability"] += 1
                            if m.terastallized:
                                P["tera"][m.tera_type] += 1
                                P["n_tera"] += 1
                            for mv in m.moves:
                                P["moves"][mv] += 1
                    leadrow = next((r for r in g.rows if r["kind"] == "lead" and r["player"] == sd), None)
                    if leadrow:
                        stats["lead"][leadrow["action"]] += 1
                    stats["players"].add(pseudo[sd])
                    if intop[sd]:
                        stats["top_players"].add(pseudo[sd])
                        rr = s.rating
                        if rr is not None:
                            stats["ratings_top"].append(rr)
                        stats["tera_top"][0] += any(m.terastallized for m in s.roster)
                        stats["tera_top"][1] += 1
                    elif s.rating is not None:
                        stats["ratings_other"].append(s.rating)
                    stats["tera_games"] += any(m.terastallized for m in s.roster)
                # game-level stats
                stats["games"] += 1
                stats["turns"].append(g.turn)
                for sd in ("p1", "p2"):
                    rr = (r1, r2)[sd == "p2"]
                    if rr is not None:
                        stats["ratings"].append(rr)
                    else:
                        stats["unrated"] += 1
                stats["uploadtimes"].append(rep.get("uploadtime"))
                stats["end_reason"][g.end_reason] += 1
                stats["both_top50"] += intop["p1"] and intop["p2"]
                stats["one_top50"] += (intop["p1"] != intop["p2"])
                for ty in CHANCE_NET_TYPES + ("thaw", "confusion_self_hit"):
                    stats["chance_per_game"][ty].append(sum(chance_counts[sd][ty] for sd in ("p1", "p2")))
                if g.winner != "tie":
                    for sd in ("p1", "p2"):
                        stats["net_vs_win"].append((net[sd], int(g.winner == sd)))
                    stats["side_wins"][g.winner] += 1
                    if higher:
                        stats["higher_rated_wins"][g.winner == higher] += 1
                for k, v in g.flags.items():
                    stats["flags"][k] += 1
                for sd in ("p1", "p2"):
                    if intop[sd]:
                        for m in g.sides[sd].roster:
                            stats["usage_top"][m.species_preview] += 1
        if limit and n_game >= limit:
            break
    for fh in (games_f, act_f, set_f):
        fh.close()
    (out_dir / "PRIVATE" / "player_map.json").write_text(json.dumps(
        {h: {**v, "top50_rank": top.get(v["userid"], (None,))[0],
             **({"ladder500_rank": top500.get(v["userid"], (None,))[0]} if broad else {})} for h, v in ps.map.items()},
        indent=1))
    return stats, profiles, ladder


def scrub_known_names(out_dir):
    """Second pass (broad parse): a username of ANY player can appear in free text (a nickname) of another game.
    Replace a log field (or its `a: b` / `a, b` parts) that equals a known userid (5+ characters) by the pseudonym."""
    pmap = json.loads((out_dir / "PRIVATE" / "player_map.json").read_text())
    by_id = {v["userid"]: h for h, v in pmap.items() if len(v["userid"]) >= 5}
    tmp = out_dir / "games.jsonl.gz.tmp"
    n = 0
    with gzip.open(out_dir / "games.jsonl.gz", "rt") as fin, gzip.open(tmp, "wt") as fout:
        for line in fin:
            g = json.loads(line)
            for i, ln in enumerate(g["log"]):
                parts = re.split(r"(\|)", ln)
                changed = False
                for j, f in enumerate(parts):
                    if f == "|" or len(f) < 5:
                        continue
                    sub = re.split(r"(: |, )", f)
                    for t, seg in enumerate(sub):
                        if seg in (": ", ", "):
                            continue
                        h = by_id.get(norm(seg))
                        if h:
                            sub[t] = h
                            changed = True
                            n += 1
                    parts[j] = "".join(sub)
                if changed:
                    g["log"][i] = "".join(parts)
            fout.write(json.dumps(g) + "\n")
    os.replace(tmp, out_dir / "games.jsonl.gz")
    return n


def pct(xs, q):
    xs = sorted(xs)
    return xs[int(q * (len(xs) - 1))] if xs else None


def corr(pairs):
    if len(pairs) < 3:
        return None
    xs, ys = [a for a, _ in pairs], [b for _, b in pairs]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in pairs) / (sx * sy) if sx and sy else None


def write_summary(stats, profiles, out_dir):
    G = max(stats["games"], 1)
    seen = max(stats["seen_mons"], 1)
    top30 = [{"species": s, "teams": n, "team_rate": round(n / stats["teams"], 4)}
             for s, n in stats["usage"].most_common(30)]
    s = {
        "games": stats["games"], "skipped": dict(stats["skipped"]), "unique_players": len(stats["players"]),
        "rated_player_slots": len(stats["ratings"]), "unrated_player_slots": stats["unrated"],
        "rating": {"min": min(stats["ratings"], default=None), "p10": pct(stats["ratings"], .1),
                   "p25": pct(stats["ratings"], .25), "median": pct(stats["ratings"], .5),
                   "p75": pct(stats["ratings"], .75), "p90": pct(stats["ratings"], .9),
                   "max": max(stats["ratings"], default=None),
                   "mean": round(statistics.mean(stats["ratings"]), 1) if stats["ratings"] else None},
        "unique_top50_players": len(stats["top_players"]),
        "rating_top50_side": {"n": len(stats["ratings_top"]), "min": min(stats["ratings_top"], default=None),
                              "p25": pct(stats["ratings_top"], .25), "median": pct(stats["ratings_top"], .5),
                              "p75": pct(stats["ratings_top"], .75), "max": max(stats["ratings_top"], default=None)},
        "rating_other_side": {"n": len(stats["ratings_other"]), "min": min(stats["ratings_other"], default=None),
                              "p25": pct(stats["ratings_other"], .25), "median": pct(stats["ratings_other"], .5),
                              "p75": pct(stats["ratings_other"], .75), "max": max(stats["ratings_other"], default=None)},
        "turn_action_type_pct_top50_players": {k: round(100 * v / max(sum(stats["act_top"].values()), 1), 1) for k, v in stats["act_top"].most_common()},
        "turn_action_type_pct_other_players": {k: round(100 * v / max(sum(stats["act_other"].values()), 1), 1) for k, v in stats["act_other"].most_common()},
        "top50_teams_terastallizing_pct": round(100 * stats["tera_top"][0] / max(stats["tera_top"][1], 1), 1),
        "games_both_top50": stats["both_top50"], "games_one_top50": stats["one_top50"],
        "turns_per_game": {"mean": round(statistics.mean(stats["turns"]), 1), "median": statistics.median(stats["turns"]),
                           "p10": pct(stats["turns"], .1), "p90": pct(stats["turns"], .9), "max": max(stats["turns"])},
        "species_usage_top30": top30,
        "lead_top10": [{"species": k, "n": v} for k, v in stats["lead"].most_common(10)],
        "reveal_by_game_end_among_seen_pokemon": {
            "seen_pokemon": stats["seen_mons"],
            "item_revealed_pct": round(100 * stats["item"] / seen, 1),
            "ability_revealed_pct": round(100 * stats["ability"] / seen, 1),
            "tera_used_pct": round(100 * stats["tera_used"] / seen, 1),
            "mean_moves_revealed": round(stats["moves_sum"] / seen, 2),
            "moves_revealed_hist_pct": {str(k): round(100 * stats["moves_hist"][k] / seen, 1) for k in range(5)},
            "item_how": dict(stats["items_how"].most_common()), "ability_how": dict(stats["ability_how"].most_common(8)),
        },
        "teams_terastallizing_pct": round(100 * stats["tera_games"] / max(stats["teams"], 1), 1),
        "end_reason": dict(stats["end_reason"]),
        "forfeit_or_timeout_pct": round(100 * (stats["end_reason"]["forfeit"] + stats["end_reason"]["timeout"]) / G, 2),
        "chance_events_per_game_both_players": {
            t: {"mean": round(statistics.mean(v), 2), "p90": pct(v, .9), "games_with_any_pct": round(100 * sum(1 for x in v if x) / len(v), 1)}
            for t, v in stats["chance_per_game"].items() if v},
        "chance_net_vs_win_corr_baseline": corr(stats["net_vs_win"]),
        "decisions_by_kind": dict(stats["decisions"]),
        "hidden_action_pct_by_kind": {k: round(100 * stats["hidden"][(k, True)] / max(stats["hidden"][(k, True)] + stats["hidden"][(k, False)], 1), 1)
                                      for k in stats["decisions"]},
        "side_p1_win_pct": round(100 * stats["side_wins"]["p1"] / max(sum(stats["side_wins"].values()), 1), 1),
        "higher_rated_win_pct": (round(100 * stats["higher_rated_wins"][True] / max(sum(stats["higher_rated_wins"].values()), 1), 1)
                                 if stats["higher_rated_wins"] else None),
        "games_with_flag": dict(stats["flags"]),
        "upload_range_utc": [None, None],
    }
    import time
    ts = [t for t in stats["uploadtimes"] if t]
    if ts:
        s["upload_range_utc"] = [time.strftime("%Y-%m-%d", time.gmtime(min(ts))), time.strftime("%Y-%m-%d", time.gmtime(max(ts)))]
    (out_dir / "summary.json").write_text(json.dumps(s, indent=1))
    prof = {}
    for sp, P in sorted(profiles.items(), key=lambda kv: -kv[1]["n"])[:80]:
        prof[sp] = {"seen_n": P["n"],
                    "items": {k: round(v / max(P["n_item"], 1), 3) for k, v in P["items"].most_common(5)},
                    "item_revealed_rate": round(P["n_item"] / P["n"], 3),
                    "abilities": {k: round(v / max(P["n_ability"], 1), 3) for k, v in P["abilities"].most_common(3)},
                    "ability_revealed_rate": round(P["n_ability"] / P["n"], 3),
                    "tera": {k: round(v / max(P["n_tera"], 1), 3) for k, v in P["tera"].most_common(4)},
                    "tera_used_rate": round(P["n_tera"] / P["n"], 3),
                    "moves": {k: round(v / P["n"], 3) for k, v in P["moves"].most_common(10)}}
    (out_dir / "species_profiles.json").write_text(json.dumps(prof, indent=1))
    md = ["# Human Gen 9 OU top-player set: summary", "", f"games: {s['games']}  unique players (pseudonyms): {s['unique_players']}",
          f"upload range: {s['upload_range_utc']}  both players top-50: {s['games_both_top50']}  one top-50: {s['games_one_top50']}",
          f"rating: {s['rating']}", f"turns/game: {s['turns_per_game']}", "", "## Species usage (top 30, share of teams)"]
    md += [f"- {x['species']}: {x['team_rate']*100:.1f}%" for x in s["species_usage_top30"]]
    md += ["", "## Reveal by game end (seen Pokemon)", json.dumps(s["reveal_by_game_end_among_seen_pokemon"], indent=1),
           "", "## Chance events per game (both players)", json.dumps(s["chance_events_per_game_both_players"], indent=1),
           "", f"end reasons: {s['end_reason']}", f"net-chance vs win corr: {s['chance_net_vs_win_corr_baseline']}"]
    (out_dir / "summary.md").write_text("\n".join(md))
    return s


def summarize_bands(out_dir):
    """Per rating band summary, streamed from games / actions / sets of a broad parse."""
    uid_band = {}
    hr_pre = defaultdict(lambda: [0, 0])
    B = defaultdict(lambda: {"games": 0, "turns": [], "end": Counter(), "chance": defaultdict(list), "hr_win": [0, 0],
                             "teams": 0, "usage": Counter(), "seen": 0, "item": 0, "ability": 0, "tera": 0,
                             "moves": 0, "tera_team": 0, "act": Counter(), "sources": Counter(), "players": set(),
                             "top500_games": 0, "ratings": []})
    with gzip.open(out_dir / "games.jsonl.gz", "rt") as f:
        for line in f:
            g = json.loads(line)
            b = g["rating_band"]
            uid_band[g["uid"]] = b
            X = B[b]
            X["games"] += 1
            X["turns"].append(g["turns"])
            X["end"][g["end_reason"]] += 1
            X["sources"][g["source"]] += 1
            X["players"].update((g["p1"], g["p2"]))
            X["top500_games"] += bool(g["top500"]["p1"] or g["top500"]["p2"])
            if g["ratings"].get("replay_field") is not None:
                X["ratings"].append(g["ratings"]["replay_field"])
            for t in CHANCE_NET_TYPES + ("thaw", "confusion_self_hit"):
                X["chance"][t].append(sum(g["chance_counts"][sd].get(t, 0) for sd in ("p1", "p2")))
            if g["higher_rated"] and g["winner_player"] in ("p1", "p2"):
                hr_pre[g["rating_band_pre"]][1] += 1
                hr_pre[g["rating_band_pre"]][0] += g["winner_player"] == g["higher_rated"]
                X["hr_win"][1] += 1
                X["hr_win"][0] += g["winner_player"] == g["higher_rated"]
    team_has_tera = defaultdict(bool)
    with gzip.open(out_dir / "sets.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            X = B[uid_band[r["uid"]]]
            X["usage"][r["species_preview"]] += 1
            team_has_tera[(r["uid"], r["player"])] |= bool(r["terastallized"])
            if r["seen"]:
                X["seen"] += 1
                X["item"] += r["item"] is not None
                X["ability"] += r["ability"] is not None
                X["tera"] += bool(r["terastallized"])
                X["moves"] += r["n_moves_seen"]
    for (uid, pl), v in team_has_tera.items():
        B[uid_band[uid]]["tera_team"] += v
    with gzip.open(out_dir / "actions.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            if r["kind"] == "turn":
                B[uid_band[r["uid"]]]["act"][r["action_type"]] += 1
    out = {}
    order = [b[0] for b in BANDS] + ["unrated"]
    for b in order:
        if b not in B:
            continue
        X = B[b]
        n = max(X["games"], 1)
        acts = sum(X["act"].values()) or 1
        seen = max(X["seen"], 1)
        out[b] = {
            "games": X["games"], "unique_players": len(X["players"]), "sources": dict(X["sources"]),
            "games_with_a_top500_player": X["top500_games"],
            "rating_field": {"min": min(X["ratings"], default=None), "median": pct(X["ratings"], .5), "max": max(X["ratings"], default=None)},
            "turns": {"mean": round(statistics.mean(X["turns"]), 1), "median": statistics.median(X["turns"]), "p90": pct(X["turns"], .9)},
            "forfeit_or_timeout_pct": round(100 * (X["end"]["forfeit"] + X["end"]["timeout"]) / n, 1),
            "forfeit_pct": round(100 * X["end"]["forfeit"] / n, 1),
            "species_usage_top15": [{"species": sp, "team_pct": round(100 * c / (2 * n), 1)} for sp, c in X["usage"].most_common(15)],
            "reveal_pct_of_seen": {"item": round(100 * X["item"] / seen, 1), "ability": round(100 * X["ability"] / seen, 1),
                                   "tera_used": round(100 * X["tera"] / seen, 1), "mean_moves": round(X["moves"] / seen, 2)},
            "teams_terastallizing_pct": round(100 * X["tera_team"] / (2 * n), 1),
            "chance_per_game": {t: round(statistics.mean(v), 2) for t, v in X["chance"].items() if v},
            "turn_action_mix_pct": {k: round(100 * v / acts, 1) for k, v in X["act"].most_common()},
            "higher_rated_win_pct": round(100 * X["hr_win"][0] / X["hr_win"][1], 1) if X["hr_win"][1] else None,
            "higher_rated_games": X["hr_win"][1],
        }
    out["_higher_rated_win_pct_by_pre_game_band"] = {
        b: {"pct": round(100 * v[0] / v[1], 1), "games": v[1]} for b, v in sorted(hr_pre.items()) if v[1]}
    out["_note"] = ("rating_band uses the replay `rating` field (lower player's post-game rating), so it is conditioned on "
                    "the result and biases higher_rated_win_pct (upsets raise the lower rating); "
                    "_higher_rated_win_pct_by_pre_game_band uses the lower player's pre-game rating instead. "
                    "Games come from top-500 users' listings and from time-sampled global listings, not a uniform sample.")
    (out_dir / "summary_by_band.json").write_text(json.dumps(out, indent=1))
    lines = ["# Summary by rating band (replay `rating` field = lower player's rating)", "",
             "| band | games | players | turns mean | forfeit+timeout % | higher-rated wins % | move/switch/tera_move % | item/ability/tera revealed % | crits/game | misses/game |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for b, v in out.items():
        if b.startswith("_"):
            continue
        am = v["turn_action_mix_pct"]
        lines.append(f"| {b} | {v['games']} | {v['unique_players']} | {v['turns']['mean']} | {v['forfeit_or_timeout_pct']} | "
                     f"{v['higher_rated_win_pct']} | {am.get('move')}/{am.get('switch')}/{am.get('tera_move')} | "
                     f"{v['reveal_pct_of_seen']['item']}/{v['reveal_pct_of_seen']['ability']}/{v['reveal_pct_of_seen']['tera_used']} | "
                     f"{v['chance_per_game'].get('crit')} | {v['chance_per_game'].get('miss')} |")
    lines.append("")
    lines.append("Higher-rated win % by pre-game band (lower player's pre-game rating): " +
                 ", ".join(f"{b} {v['pct']} (n={v['games']})" for b, v in out["_higher_rated_win_pct_by_pre_game_band"].items()))
    lines.append("")
    for b, v in out.items():
        if b.startswith("_"):
            continue
        lines.append(f"- {b} top 15: " + ", ".join(f"{x['species']} {x['team_pct']}%" for x in v["species_usage_top15"]))
    (out_dir / "summary_by_band.md").write_text("\n".join(lines))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=str(DEFAULT_RAW))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--broad", default=None,
                    help="broad crawl raw dir (data/replays/gen9ou-broad/raw): parse it together with --raw (the top-50 "
                         "shards, source=top50) into --out and add rating_band, source, top500 columns")
    a = ap.parse_args()
    stats, profiles, ladder = parse_all(Path(a.raw), Path(a.out), a.limit, Path(a.broad) if a.broad else None)
    s = write_summary(stats, profiles, Path(a.out))
    if a.broad:
        n = scrub_known_names(Path(a.out))
        print("scrubbed known usernames from free text:", n)
        summarize_bands(Path(a.out))
    print(json.dumps({k: s[k] for k in ("games", "unique_players", "turns_per_game", "rating", "skipped")}, indent=1))


if __name__ == "__main__":
    main()
