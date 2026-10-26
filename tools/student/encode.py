"""Deterministic encoder for the HardRead student: (view, legal, turn, k, team_preview) -> arrays.

Input is exactly the public decision information (decision log v2 view + the MCTS root menu):
  view          {"user": side, "opponent": side, "weather", "field", "trick_room"} as logged by
                fp/hardread_decision_log.py (own full sets; opponent public state + Foul Play's
                inferences: impossible items/abilities, speed range, choice-item flag)
  legal         root actions: "<move>", "<move>-tera", "switch <species>"
  turn, k, team_preview
Never pass the opponent's true sets. The opponent's nature/EVs in the view are Foul Play's
defaults (not public) and are ignored, like tools/sft/serialize.py does.

Tokens (a set; no positional encoding, so reserve order does not matter):
  field    1 token: weather, terrain, trick room, turn, k, team preview, both sides' side
           conditions, alive counts and HP totals
  entity   12 tokens (own 6, then opponent 6; missing ones masked): species, item, ability,
           tera type, current types, status, removed item, bags of known moves, impossible
           items, impossible abilities; numeric HP, boosts, flags, base stats, own computed
           stats (own side only), speed range (opponent inference)
  action   one token per legal action: kind (move / move-tera / switch), move id and type,
           move numbers (power, accuracy, priority, category, STAB, effectiveness vs the
           opponent active), or for a switch the target entity index and defensive matchup

Vocabularies and per-species / per-move tables are built from Foul Play's data files
(fp/data/pokedex.json, moves.json, the Smogon stats and sets caches, fp/battle/helpers.py
type chart and natures). Unknown names map to hashed OOV buckets (crc32), so unseen species
or moves still encode. The full encoder state (vocabularies + tables) is saved in every
checkpoint, so inference never depends on the data files changing.

numpy only (no torch), Python >= 3.9.
"""
import ast
import json
import math
import os
import re
import zlib
from pathlib import Path

import numpy as np

ENCODER_VERSION = "hr-student-enc-v3"
REPO = Path(__file__).resolve().parents[2]
FP_ROOT = Path(os.environ.get("HARDREAD_FOULPLAY", REPO / "foul-play"))

N_OWN = 6
N_ENT = 12
MAX_ACT = 16           # legal menus have at most 13 actions (4 moves x 2 + 5 switches)
MAX_MOVES = 6          # known moves per entity (opponents can show a 5th via Illusion bugs)
MAX_IMP_ITEMS = 32
MAX_IMP_ABIL = 12
N_EV = 16              # recent public events (event tokens)
N_OOV = 8
SPECIALS = ("<pad>", "<none>", "<unk>")
PAD, NONE, UNK = 0, 1, 2

STATS = ("hp", "attack", "defense", "special-attack", "special-defense", "speed")
BOOSTS = ("attack", "defense", "special-attack", "special-defense", "speed", "accuracy", "evasion")
STATUSES = ("psn", "tox", "brn", "par", "slp", "frz")
SIDE_CONDS = ("stealthrock", "spikes", "toxicspikes", "stickyweb", "reflect", "lightscreen",
              "auroraveil", "tailwind", "safeguard", "mist", "healingwish", "futuresight", "wish",
              "protect", "toxic_count")
SIDE_SCALE = {"stealthrock": 1, "spikes": 3, "toxicspikes": 2, "stickyweb": 1, "protect": 3, "toxic_count": 8}
WEATHERS = ("raindance", "sunnyday", "sandstorm", "hail", "snowscape", "snow", "desolateland",
            "primordialsea", "deltastream")
TERRAINS = ("electricterrain", "grassyterrain", "mistyterrain", "psychicterrain")
CATEGORIES = ("physical", "special", "status")

# Categorical entity fields (one embedding table each) and bag fields (mean of embeddings).
ENT_CAT = ("species", "item", "ability", "tera", "type1", "type2", "status", "removed_item")
ENT_CAT_VOCAB = {"species": "species", "item": "items", "ability": "abilities", "tera": "types",
                 "type1": "types", "type2": "types", "status": "status", "removed_item": "items"}
ENT_BAGS = (("moves", "moves", MAX_MOVES), ("imp_items", "items", MAX_IMP_ITEMS),
            ("imp_abil", "abilities", MAX_IMP_ABIL))

ENT_NUM = (["own", "active", "hp_frac", "fainted", "max_hp"] + ["boost_" + b for b in BOOSTS]
           + ["terastallized", "can_tera", "can_choice", "spe_min", "spe_max", "spe_max_inf", "level"]
           + ["base_" + s for s in STATS] + ["stat_" + s for s in STATS]
           + ["n_moves", "item_known", "item_none", "ability_known", "tera_known", "weight",
              "n_imp_items", "n_imp_abil", "has_removed_item", "team_preview",
              # damage estimates (calc_lite): best known/STAB-proxy move of the other side's active
              # vs this Pokemon, our active's best move vs this Pokemon, entry-hazard damage
              "threat_frac", "threat_vs_cur", "our_best_frac", "our_best_vs_cur", "hazard_frac"])
FIELD_NUM = (["trick_room", "turn", "turn_log", "k", "team_preview"]
             + ["own_" + c for c in SIDE_CONDS] + ["opp_" + c for c in SIDE_CONDS]
             + ["own_alive", "opp_alive", "own_hp", "opp_hp", "own_tera_avail", "opp_tera_used",
                "n_legal", "n_moves_legal", "n_switch_legal",
                "outspeed", "spe_log_ratio", "active_threat_vs_cur", "active_best_vs_cur"])
ACT_NUM = (["is_move", "is_tera", "is_switch", "bp", "acc", "acc_always", "priority"]
           + ["cat_" + c for c in CATEGORIES]
           + ["stab", "eff_log2", "immune", "self_target", "heal", "boosts_self", "inflicts_status",
              "switch_out", "drain", "recoil", "hazard", "multi_hit"]
           + ["sw_def_log2", "sw_def_immune", "sw_hp", "sw_speed_cmp", "sw_status"]
           + ["dmg_frac", "dmg_vs_cur", "ko", "sw_threat_vs_cur", "sw_hazard"])
# Recent public events (from the canonical public history, opponent HP as Showdown's percentage).
EV_TYPES = ("move", "switch", "drag", "damage", "heal", "status", "curestatus", "faint", "boost", "unboost",
            "weather", "fieldstart", "fieldend", "sidestart", "sideend", "start", "end", "terastallize",
            "item", "enditem", "ability", "cant", "activate", "fail", "replace", "singleturn", "clearboost")
EV_CAT = ("type", "move", "species", "item", "ability", "misc")
EV_CAT_VOCAB = {"type": "ev_type", "move": "moves", "species": "species", "item": "items", "ability": "abilities",
                "misc": "misc"}
EV_NUM = ("ours", "theirs", "hp_after", "has_hp", "dmg_delta", "crit", "miss", "supereff", "resisted", "immune",
          "fail", "amount", "from_effect", "turns_ago", "recency", "same_turn")
MISC_TOKENS = (list(STATUSES) + list(WEATHERS) + ["none"] + list(TERRAINS) + list(SIDE_CONDS)
               + ["boost" + b for b in ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")]
               + ["normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground", "flying",
                  "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy", "stellar"]
               + ["substitute", "confusion", "taunt", "encore", "leechseed", "yawn", "perishsong", "curse",
                  "disable", "torment", "saltcure", "protect", "focusenergy", "magnetrise", "noretreat",
                  "stockpile", "charge", "attract", "aquaring", "ingrain", "roost", "smackdown", "typechange",
                  "slowstart", "flashfire", "recharge", "partiallytrapped", "lockedmove", "mustrecharge",
                  "protosynthesisatk", "protosynthesisdef", "protosynthesisspa", "protosynthesisspd",
                  "protosynthesisspe", "quarkdriveatk", "quarkdrivedef", "quarkdrivespa", "quarkdrivespd",
                  "quarkdrivespe", "lifeorb", "rockyhelmet", "roughskin", "ironbarbs", "blacksludge", "leftovers",
                  "dynamax", "par", "slp", "frz", "endure", "detect", "kingsshield", "banefulbunker", "silktrap",
                  "spikyshield", "burningbulwark", "obstruct", "fallen1", "fallen2", "fallen3", "fallen4", "fallen5",
                  "drain", "recoil", "highjumpkick", "jumpkick", "struggle", "confusion", "trapped", "mustrecharge",
                  "atk", "def", "spa", "spd", "spe", "perish0", "perish1", "perish2", "perish3", "sitrusberry"])
EV_HISTORY_LINES = 200  # events are parsed from the last 200 canonical lines (bounded cost)
F_EV = len(EV_NUM)
F_ENT, F_FIELD, F_ACT = len(ENT_NUM), len(FIELD_NUM), len(ACT_NUM)
# Model token layout (shared by model.py and the torch-free numpy forward in infer.py).
# entity categorical field -> embedding table name
CAT_TABLE = {"species": "species", "item": "item", "ability": "ability", "tera": "tera", "type1": "ptype",
             "type2": "ptype", "status": "status", "removed_item": "removed_item"}
BAG_TABLE = {"moves": "move", "imp_items": "imp_item", "imp_abil": "imp_abil"}
# table name -> encoder vocabulary name
TABLE_VOCAB = {"species": "species", "item": "items", "ability": "abilities", "tera": "types", "ptype": "types",
               "status": "status", "removed_item": "items", "move": "moves", "imp_item": "items",
               "imp_abil": "abilities", "weather": "weather", "terrain": "terrain", "ev_type": "ev_type",
               "misc": "misc"}
TOK_CLS, TOK_FIELD, TOK_OWN, TOK_OPP, TOK_ACT, TOK_EV = range(6)
EV_TABLE = {"type": "ev_type", "move": "move", "species": "species", "item": "item", "ability": "ability",
            "misc": "misc"}
_SWITCH_OUT = {"uturn", "voltswitch", "partingshot", "teleport", "flipturn", "chillyreception", "shedtail"}
_HAZARD = {"stealthrock", "spikes", "toxicspikes", "stickyweb"}


def to_id(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower()) if s else ""


_CANON = None


def canonical_history(lines, player):
    """fp/hardread_history.canonical_history (imported by path, no Foul Play package import):
    the same canonical public history the SFT serializer and the live policy hook use."""
    global _CANON
    if _CANON is None:
        import importlib.util
        path = FP_ROOT / "fp" / "hardread_history.py"
        spec = importlib.util.spec_from_file_location("hardread_history_student", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CANON = mod.canonical_history
    return _CANON(lines, player)


# ---------------------------------------------------------------- vocabularies from Foul Play data
def _parse_helpers(path):
    """Type chart, type indices and natures from fp/battle/helpers.py (parsed, not imported)."""
    tree = ast.parse(Path(path).read_text())
    stat_of = {"ATTACK": "attack", "DEFENSE": "defense", "SPECIAL_ATTACK": "special-attack",
               "SPECIAL_DEFENSE": "special-defense", "SPEED": "speed", "HITPOINTS": "hp"}
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("POKEMON_TYPE_INDICES", "DAMAGE_MULTIPICATION_ARRAY"):
                out[name] = ast.literal_eval(node.value)
            elif name == "natures":
                nat = {}
                for k, v in zip(node.value.keys, node.value.values):
                    d = {}
                    for kk, vv in zip(v.keys, v.values):
                        d[kk.value] = stat_of[vv.attr] if isinstance(vv, ast.Attribute) else vv.value
                    nat[k.value] = d
                out["natures"] = nat
    return out


def build_state(fp_root=FP_ROOT):
    """Encoder state (JSON-serialisable) built from Foul Play's data files."""
    fp_root = Path(fp_root)
    data = fp_root / "fp" / "data"
    dex = json.loads((data / "pokedex.json").read_text())
    moves = json.loads((data / "moves.json").read_text())
    helpers = _parse_helpers(fp_root / "fp" / "battle" / "helpers.py")
    type_idx = helpers["POKEMON_TYPE_INDICES"]
    types = sorted({t for t in type_idx if t != "???"} | {"stellar"})

    items, abilities = set(), set()
    for f in sorted((data / "smogon_stats_cache").glob("*.json")):
        for v in json.loads(f.read_text()).values():
            items.update(to_id(i) for i in (v.get("Items") or {}))
            abilities.update(to_id(a) for a in (v.get("Abilities") or {}))
    for f in sorted((data / "pkmn_sets_cache").rglob("pokemon_full_sets.json")):
        for sets in json.loads(f.read_text()).values():
            for key in sets:
                parts = key.split("|")
                if len(parts) > 2:
                    abilities.add(to_id(parts[1]))
                    items.add(to_id(parts[2]))
    for e in dex.values():
        abilities.update(to_id(a) for a in (e.get("abilities") or {}).values())
        if e.get("requiredItem"):
            items.add(to_id(e["requiredItem"]))
    items.discard(""); items.discard("nothing"); items.discard("unknownitem")
    abilities.discard(""); abilities.discard("noability")

    species_tab = {}
    for sid, e in dex.items():
        bs = e.get("baseStats") or {}
        species_tab[sid] = {"types": [t for t in (e.get("types") or []) if t],
                            "base": [bs.get(s, 0) for s in STATS], "weight": e.get("weightkg", 0)}
    move_tab = {}
    for mid, m in moves.items():
        acc = m.get("accuracy", True)
        move_tab[mid] = {
            "type": m.get("type") or "typeless", "bp": m.get("basePower") or 0,
            "acc": 100 if acc is True else (acc or 0), "always": acc is True,
            "priority": m.get("priority") or 0, "cat": m.get("category") or "status",
            "self": m.get("target") in ("self", "allySide", "allies", "adjacentAllyOrSelf", "allyTeam"),
            "heal": bool(m.get("heal")) or bool((m.get("flags") or {}).get("heal")),
            "boosts_self": bool(m.get("boosts")) and m.get("target") == "self"
                           or bool(((m.get("self") or {}).get("boosts"))),
            "status": bool(m.get("status") or m.get("volatileStatus")),
            "drain": bool(m.get("drain")), "recoil": bool(m.get("recoil")),
            "multihit": bool(m.get("multihit")),
            "hits": (sum(m["multihit"]) / 2.0 + 0.1 if isinstance(m.get("multihit"), list) else (m.get("multihit") or 1)),
            "fixed": m.get("damage"), "def_override": m.get("overrideDefensiveStat"),
        }
    return {
        "version": ENCODER_VERSION,
        "vocab": {
            "species": sorted(dex), "moves": sorted(moves), "items": sorted(items),
            "abilities": sorted(abilities), "types": types, "status": list(STATUSES),
            "weather": list(WEATHERS), "terrain": list(TERRAINS),
            "ev_type": list(EV_TYPES), "misc": sorted(set(MISC_TOKENS)),
        },
        "species": species_tab, "moves": move_tab,
        "type_idx": {k: v for k, v in type_idx.items()}, "chart": helpers["DAMAGE_MULTIPICATION_ARRAY"],
        "natures": helpers["natures"],
    }


class Vocab:
    def __init__(self, tokens, n_oov=N_OOV):
        self.tokens = list(tokens)
        self.idx = {t: i + len(SPECIALS) for i, t in enumerate(self.tokens)}
        self.n_oov = n_oov
        self.size = len(SPECIALS) + len(self.tokens) + n_oov
        self.oov = {}

    def __call__(self, tok, none=NONE):
        if tok is None or tok == "":
            return none
        t = to_id(tok)
        i = self.idx.get(t)
        if i is None:
            if not t:
                return none
            i = len(SPECIALS) + len(self.tokens) + zlib.crc32(t.encode()) % self.n_oov
            self.oov[t] = self.oov.get(t, 0) + 1
        return i


# ---------------------------------------------------------------- encoder
class Encoder:
    def __init__(self, state=None):
        self.state = state or build_state()
        if self.state.get("version") != ENCODER_VERSION:
            raise ValueError("encoder state version {} != {}".format(self.state.get("version"), ENCODER_VERSION))
        self.vocab = {k: Vocab(v, n_oov=32 if k == "misc" else N_OOV) for k, v in self.state["vocab"].items()}
        self.species = self.state["species"]
        self.moves = self.state["moves"]
        self.type_idx = self.state["type_idx"]
        self.chart = self.state["chart"]
        self.natures = self.state["natures"]
        self._gcache = {}

    # -- sizes the model needs
    def sizes(self):
        return {k: v.size for k, v in self.vocab.items()}

    # -- helpers
    def _species(self, name):
        sid = to_id(name)
        e = self.species.get(sid)
        if e is None:  # cosmetic formes etc.: longest known prefix
            for j in range(len(sid) - 1, 2, -1):
                if sid[:j] in self.species:
                    return self.species[sid[:j]]
            return {"types": [], "base": [0] * 6, "weight": 0}
        return e

    def _cur_types(self, p):
        if p.get("terastallized") and p.get("tera_type") and p["tera_type"] != "stellar":
            return [p["tera_type"]]
        return list(self._species(p.get("name")).get("types") or [])

    def _eff(self, atk_type, def_types):
        a = self.type_idx.get(atk_type)
        if a is None:
            return 1.0
        m = 1.0
        for t in def_types:
            d = self.type_idx.get(t)
            if d is not None:
                m *= self.chart[a][d]
        return m

    def _stats(self, p, base):
        """Own Pokémon's actual stats (IVs assumed 31; nature and EVs from our own set)."""
        evs = list(p.get("evs") or [0] * 6)
        if len(evs) != 6:
            evs = [0] * 6
        lvl = p.get("level") or 100
        out = []
        for i, s in enumerate(STATS):
            core = math.floor((2 * base[i] + 31 + math.floor(evs[i] / 4)) * lvl / 100)
            v = core + lvl + 10 if s == "hp" else core + 5
            nat = self.natures.get(p.get("nature") or "", {})
            if s != "hp" and nat.get("plus") != nat.get("minus"):
                if nat.get("plus") == s:
                    v = math.floor(v * 1.1)
                elif nat.get("minus") == s:
                    v = math.floor(v * 0.9)
            out.append(v)
        return out

    # ------------------------------------------------------------ calc_lite (public information only)
    def _generic_stats(self, p):
        """Opponent stats from base stats with a fixed generic spread (85 EVs, neutral nature, IV 31):
        public knowledge only (the view's opponent nature/EVs are Foul Play defaults and not used)."""
        key = (to_id(p.get("name")), p.get("level") or 100)
        st = self._gcache.get(key)
        if st is None:
            st = self._stats({"evs": [85] * 6, "level": key[1], "nature": None}, self._species(p.get("name"))["base"])
            self._gcache[key] = st
        return st

    def _mon_stats(self, p, own):
        if own:
            st = self._stats(p, self._species(p.get("name"))["base"])
            if p.get("max_hp"):
                st[0] = p["max_hp"]
            return st
        return self._generic_stats(p)

    @staticmethod
    def _bm(stage):
        stage = max(-6, min(6, int(stage or 0)))
        return (2 + stage) / 2.0 if stage >= 0 else 2.0 / (2 - stage)

    _IMMUNE = {"levitate": "ground", "eartheater": "ground", "flashfire": "fire", "wellbakedbody": "fire",
               "waterabsorb": "water", "stormdrain": "water", "dryskin": "water", "voltabsorb": "electric",
               "lightningrod": "electric", "motordrive": "electric", "sapsipper": "grass"}

    def _damage(self, att, att_own, dfn, dfn_own, mid, tera=False, weather=None, dfn_side=None, proxy=None):
        """Expected damage of one move as a fraction of the defender's max HP (crits and rolls averaged)."""
        m = proxy or self.moves.get(mid)
        if not m or m.get("cat") == "status":
            return 0.0
        a_st, d_st = self._mon_stats(att, att_own), self._mon_stats(dfn, dfn_own)
        lvl = att.get("level") or 100
        if m.get("fixed") == "level":
            return lvl / max(d_st[0], 1)
        if mid in ("superfang", "ruination", "naturesmadness"):
            return 0.5 * (float(dfn.get("hp") or 0) / max(float(dfn.get("max_hp") or 1), 1.0))
        bp = m.get("bp") or 0
        if bp <= 0:
            bp = 60  # variable-power moves (Low Kick, Heavy Slam, ...)
        phys = m.get("cat") == "physical"
        ab, db = att.get("boosts") or {}, dfn.get("boosts") or {}
        if mid == "bodypress":
            A = a_st[2] * self._bm(ab.get("defense"))
        elif mid == "foulplay":
            A = d_st[1] * self._bm(db.get("attack"))
        else:
            A = a_st[1 if phys else 3] * self._bm(ab.get("attack" if phys else "special-attack"))
        use_def = phys or m.get("def_override") == "defense"
        D = d_st[2 if use_def else 4] * self._bm(db.get("defense" if use_def else "special-defense"))
        base = (2 * lvl / 5 + 2) * bp * A / max(D, 1) / 50 + 2
        mtype = m.get("type")
        a_types = self._cur_types(att)
        if tera and att.get("tera_type") and att["tera_type"] != "stellar":
            a_types = [att["tera_type"]] + list(self._species(att.get("name")).get("types") or [])
        mult = 1.5 if mtype in a_types else 1.0
        eff = self._eff(mtype, self._cur_types(dfn))
        mult *= eff
        ab_name = to_id(dfn.get("ability")) if dfn.get("ability") else ""
        if self._IMMUNE.get(ab_name) == mtype or (mtype == "ground" and dfn.get("item") == "airballoon"):
            return 0.0
        if att.get("status") == "brn" and phys and to_id(att.get("ability")) != "guts":
            mult *= 0.5
        item = att.get("item") or ""
        if item == "choiceband" and phys or item == "choicespecs" and not phys:
            mult *= 1.5
        elif item == "lifeorb":
            mult *= 1.3
        elif item == "expertbelt" and eff > 1:
            mult *= 1.2
        if weather in ("sunnyday", "desolateland"):
            mult *= 1.5 if mtype == "fire" else (0.5 if mtype == "water" and mid != "hydrosteam" else 1.0)
        elif weather in ("raindance", "primordialsea"):
            mult *= 1.5 if mtype == "water" else (0.5 if mtype == "fire" else 1.0)
        sc = dfn_side or {}
        if sc.get("auroraveil") or (phys and sc.get("reflect")) or (not phys and sc.get("lightscreen")):
            mult *= 0.5
        if mid == "knockoff" and dfn.get("item") not in (None, ""):
            mult *= 1.5
        hits = m.get("hits") or 1
        return base * mult * hits * 0.925 / max(d_st[0], 1)

    def _best(self, att, att_own, dfn, dfn_own, weather, dfn_side):
        """Best expected damage (fraction of max HP) of att's known moves vs dfn. For an opponent
        attacker with fewer than 4 revealed moves, also STAB proxies (90 BP, better attacking stat)."""
        best = 0.0
        moves = [m for m in (att.get("moves") or []) if m]
        for mid in moves:
            best = max(best, self._damage(att, att_own, dfn, dfn_own, to_id(mid), False, weather, dfn_side))
        if not att_own and len(moves) < 4:
            base = self._species(att.get("name")).get("base") or [0] * 6
            cat = "physical" if base[1] >= base[3] else "special"
            for t in self._species(att.get("name")).get("types") or []:
                proxy = {"type": t, "bp": 90, "cat": cat, "hits": 1}
                best = max(best, self._damage(att, att_own, dfn, dfn_own, "", False, weather, dfn_side, proxy))
        return best

    def _hazard(self, p, side_conds):
        """Entry-hazard damage (fraction of max HP) on switching in."""
        if not side_conds or p.get("item") == "heavydutyboots":
            return 0.0
        types = self._cur_types(p)
        dmg = 0.0
        if side_conds.get("stealthrock"):
            dmg += 0.125 * self._eff("rock", types)
        grounded = "flying" not in types and to_id(p.get("ability")) != "levitate" and p.get("item") != "airballoon"
        layers = int(side_conds.get("spikes") or 0)
        if grounded and layers:
            dmg += {1: 1 / 8.0, 2: 1 / 6.0}.get(layers, 0.25)
        return dmg

    def _speed(self, p, own, side_conds):
        st = self._mon_stats(p, own)
        spe = st[5] * self._bm((p.get("boosts") or {}).get("speed"))
        if not own:
            sr = p.get("speed_range") or [0, None]
            if sr[1] is not None and sr[0]:
                spe = (sr[0] + sr[1]) / 2.0 * self._bm((p.get("boosts") or {}).get("speed"))
        if p.get("status") == "par":
            spe *= 0.5
        if own and p.get("item") == "choicescarf":
            spe *= 1.5
        if (side_conds or {}).get("tailwind"):
            spe *= 2
        return max(spe, 1.0)

    def _entity(self, p, own, active, team_preview, cat, bags, num, i):
        V = self.vocab
        e = self._species(p.get("name"))
        types = self._cur_types(p)
        item = p.get("item")
        item_known = bool(item) and item != "unknownitem"
        tera = p.get("tera_type")
        ability = p.get("ability")
        cat[i] = [
            V["species"](p.get("name"), none=UNK),
            UNK if item == "unknownitem" else V["items"](item),
            V["abilities"](ability, none=UNK if not own else NONE),
            V["types"](tera, none=UNK if not own else NONE),
            V["types"](types[0] if types else None),
            V["types"](types[1] if len(types) > 1 else None),
            V["status"](p.get("status")),
            V["items"](p.get("removed_item")),
        ]
        mv = [m for m in (p.get("moves") or []) if m][:MAX_MOVES]
        bags["moves"][i, :len(mv)] = [V["moves"](m) for m in mv]
        imp = (p.get("impossible_items") or [])[:MAX_IMP_ITEMS]
        bags["imp_items"][i, :len(imp)] = [V["items"](x) for x in imp]
        ia = (p.get("impossible_abilities") or [])[:MAX_IMP_ABIL]
        bags["imp_abil"][i, :len(ia)] = [V["abilities"](x) for x in ia]

        hp, mhp = float(p.get("hp") or 0), float(p.get("max_hp") or 0)
        boosts = p.get("boosts") or {}
        sr = p.get("speed_range") or [0, None]
        base = e.get("base") or [0] * 6
        stats = self._stats(p, base) if own else [0] * 6
        f = num[i]
        f[0] = own
        f[1] = active and not team_preview
        f[2] = hp / mhp if mhp > 0 else 0.0
        f[3] = hp <= 0
        f[4] = mhp / 500.0
        for j, b in enumerate(BOOSTS):
            f[5 + j] = (boosts.get(b) or 0) / 6.0
        o = 5 + len(BOOSTS)
        f[o] = bool(p.get("terastallized"))
        f[o + 1] = bool(p.get("can_terastallize"))
        f[o + 2] = bool(p.get("can_have_choice_item"))
        if not own:
            f[o + 3] = (sr[0] or 0) / 400.0
            f[o + 4] = (sr[1] / 400.0) if sr[1] is not None else 1.5
            f[o + 5] = sr[1] is None
        f[o + 6] = (p.get("level") or 100) / 100.0
        o += 7
        f[o:o + 6] = [b / 200.0 for b in base]
        f[o + 6:o + 12] = [s / 500.0 for s in stats]
        o += 12
        f[o] = len(mv) / 4.0
        f[o + 1] = item_known
        f[o + 2] = item is None or item == ""
        f[o + 3] = bool(ability)
        f[o + 4] = bool(tera)
        f[o + 5] = math.log1p(e.get("weight") or 0) / 6.0
        f[o + 6] = len(imp) / 20.0
        f[o + 7] = len(ia) / 10.0
        f[o + 8] = bool(p.get("removed_item"))
        f[o + 9] = bool(team_preview)

    # ------------------------------------------------------------ recent public events
    _IDENT_RE = re.compile(r"^(p[1-4])([a-z]?): (.*)$")

    @classmethod
    def _ident(cls, x):
        """'p2a: Gholdengo' -> ('p2', 'Gholdengo'); 'p1: username' -> ('p1', ''); other -> ('', '')"""
        m = cls._IDENT_RE.match(x or "")
        if not m:
            return "", ""
        return m.group(1), (m.group(3) if m.group(2) else "")

    @staticmethod
    def _hp(x):
        m = re.match(r"^(\d+)/(\d+)", x or "")
        if not m:
            return None
        return int(m.group(1)) / max(int(m.group(2)), 1)

    def parse_events(self, history, player, turn):
        """Last N_EV public events from the canonical public history (list of protocol lines).

        A move absorbs its immediate outcome lines (-damage on the target, -crit, -miss,
        -supereffective, -resisted, -immune, -fail) into one event; everything else listed in
        EV_TYPES is its own event. Residual damage/heal lines carry their [from] source as misc.
        `history` must be canonical (fp/hardread_history.canonical_history): the opponent's HP is
        Showdown's public percentage, never the omniscient exact HP.
        """
        evs = []
        species = {}   # (side, name) -> species id, from switch/drag/replace details
        last_hp = {}   # side -> hp fraction of the active
        cur_turn = 0
        last_move = None
        for line in (history or [])[-EV_HISTORY_LINES:]:
            if not line.startswith("|"):
                continue
            parts = line.split("|")
            typ = parts[1] if len(parts) > 1 else ""
            if typ == "turn":
                try:
                    cur_turn = int(parts[2])
                except (IndexError, ValueError):
                    pass
                last_move = None
                continue
            if typ in ("", "upkeep", "t:"):
                continue
            t = typ[1:] if typ.startswith("-") else typ
            if t not in EV_TYPES and t not in ("crit", "miss", "supereffective", "resisted", "immune",
                                               "detailschange"):
                continue
            side, name = self._ident(parts[2] if len(parts) > 2 else "")
            from_raw = next((re.sub(r"^\[from\]\s*", "", x) for x in parts[3:] if x.startswith("[from]")), "")
            from_eff = to_id(from_raw.split(": ")[-1]) if from_raw else ""
            sp = species.get((side, name), to_id(name)) if name else None
            if t in ("switch", "drag", "replace", "detailschange"):
                sp = to_id((parts[3] if len(parts) > 3 else "").split(",")[0])
                species[(side, name)] = sp
                hp = self._hp(parts[4] if len(parts) > 4 else "")
                if hp is not None:
                    last_hp[side] = hp
                if t == "detailschange":
                    continue
                evs.append({"type": t, "side": side, "species": sp, "hp": hp, "turn": cur_turn})
                last_move = None
                continue
            if t == "move":
                tgt_side = self._ident(parts[4] if len(parts) > 4 else "")[0]
                last_move = {"type": "move", "side": side, "species": sp, "move": to_id(parts[3] if len(parts) > 3 else ""),
                             "tgt": tgt_side, "turn": cur_turn, "miss": "[miss]" in line}
                evs.append(last_move)
                continue
            if t in ("crit", "miss", "supereffective", "resisted", "immune", "fail"):
                if last_move is not None and last_move["turn"] == cur_turn:
                    key = {"crit": "crit", "miss": "miss", "supereffective": "supereff", "resisted": "resisted",
                           "immune": "immune", "fail": "fail"}[t]
                    last_move[key] = True
                    continue
                if t != "fail" and t != "immune":
                    continue
            if t in ("damage", "heal"):
                hp = self._hp(parts[3] if len(parts) > 3 else "")
                if hp is None and len(parts) > 3 and parts[3].startswith("0"):
                    hp = 0.0
                before = last_hp.get(side)
                if hp is not None:
                    last_hp[side] = hp
                if t == "damage" and not from_eff and last_move is not None and last_move["turn"] == cur_turn \
                        and last_move.get("tgt") == side and "hp" not in last_move:
                    last_move["hp"] = hp
                    last_move["delta"] = (before - hp) if (before is not None and hp is not None) else None
                    continue
                evs.append({"type": t, "side": side, "species": sp, "hp": hp, "turn": cur_turn, "misc": from_eff,
                            "delta": (before - hp) if (before is not None and hp is not None) else None,
                            "from": bool(from_eff)})
                continue
            ev = {"type": t, "side": side, "species": sp, "turn": cur_turn, "misc": from_eff, "from": bool(from_eff)}
            if from_raw.startswith("item: "):
                ev["item"], ev["misc"] = from_eff, ""
            elif from_raw.startswith("ability: "):
                ev["ability"], ev["misc"] = from_eff, ""
            elif from_raw.startswith("move: "):
                ev["move"], ev["misc"] = from_eff, ""
            arg = parts[3] if len(parts) > 3 else ""
            if t in ("status", "curestatus"):
                ev["misc"] = to_id(arg)
            elif t == "cant":
                ev["misc"] = to_id(arg.split(": ")[-1])
                if len(parts) > 4 and parts[4] and not parts[4].startswith("["):
                    ev["move"] = to_id(parts[4])
            elif t in ("boost", "unboost"):
                ev["misc"] = "boost" + to_id(arg)
                try:
                    ev["amount"] = int(parts[4]) * (1 if t == "boost" else -1)
                except (IndexError, ValueError):
                    pass
            elif t == "weather":
                ev["side"] = ""
                ev["misc"] = to_id(parts[2] if len(parts) > 2 else "")
                if "[upkeep]" in line:
                    continue
            elif t in ("fieldstart", "fieldend"):
                ev["side"] = ""
                ev["misc"] = to_id((parts[2] if len(parts) > 2 else "").split(": ")[-1])
            elif t in ("sidestart", "sideend"):
                ev["misc"] = to_id(arg.split(": ")[-1])
            elif t in ("start", "end", "activate", "singleturn"):
                eff = to_id(arg.split(": ")[-1])
                if arg.startswith("ability: "):
                    ev["ability"] = eff
                elif arg.startswith("move: "):
                    ev["move"] = eff
                elif arg.startswith("item: "):
                    ev["item"] = eff
                elif eff.startswith(("protosynthesis", "quarkdrive")) and eff[-3:] in ("atk", "def", "spa", "spd", "spe") \
                        and eff[:-3] in ("protosynthesis", "quarkdrive"):
                    ev["ability"], ev["misc"] = eff[:-3], eff[-3:]  # e.g. protosynthesis + atk
                elif eff in ("protosynthesis", "quarkdrive"):
                    ev["ability"] = eff
                else:
                    ev["misc"] = eff
            elif t == "terastallize":
                ev["misc"] = to_id(arg)
            elif t in ("item", "enditem"):
                ev["item"] = to_id(arg)
            elif t == "ability":
                ev["ability"] = to_id(arg)
            evs.append(ev)
        return evs[-N_EV:], cur_turn

    def _events(self, history, player, turn):
        V = self.vocab
        ev_cat = np.zeros((N_EV, len(EV_CAT)), np.int64)
        ev_num = np.zeros((N_EV, F_EV), np.float32)
        ev_mask = np.zeros(N_EV, bool)
        if not history or player not in ("p1", "p2"):
            return ev_cat, ev_num, ev_mask
        evs, cur_turn = self.parse_events(history, player, turn)
        n = len(evs)
        for i, e in enumerate(evs):
            ev_mask[i] = True
            ev_cat[i] = [V["ev_type"](e["type"]), V["moves"](e.get("move")), V["species"](e.get("species")),
                         V["items"](e.get("item")), V["abilities"](e.get("ability")), V["misc"](e.get("misc"))]
            f = ev_num[i]
            f[0] = e.get("side") == player
            f[1] = e.get("side") not in ("", None, player)
            hp = e.get("hp")
            f[2] = hp if hp is not None else 0.0
            f[3] = hp is not None
            d = e.get("delta")
            f[4] = max(-1.0, min(1.0, d)) if d is not None else 0.0
            f[5] = bool(e.get("crit"))
            f[6] = bool(e.get("miss"))
            f[7] = bool(e.get("supereff"))
            f[8] = bool(e.get("resisted"))
            f[9] = bool(e.get("immune"))
            f[10] = bool(e.get("fail"))
            f[11] = (e.get("amount") or 0) / 6.0
            f[12] = bool(e.get("from"))
            f[13] = min(max(cur_turn - e.get("turn", 0), 0), 6) / 3.0
            f[14] = (n - 1 - i) / float(N_EV)
            f[15] = e.get("turn", 0) == cur_turn
        return ev_cat, ev_num, ev_mask

    def encode(self, view, legal, turn, k, team_preview, history=None, player=None):
        """Return a dict of numpy arrays for one decision (no batch dimension).

        history/player: canonical public history lines visible at this decision and our protocol
        side ("p1"/"p2"); without them the event tokens are all masked.
        Keys: ev_cat [16, 6] int, ev_num [16, F_EV] float, ev_mask [16] bool (recent events),
        ent_cat [12, 8] int, ent_bag_<name> [12, L] int, ent_num [12, F_ENT] float,
        ent_mask [12] bool, field_cat [2] int, field_num [F_FIELD] float,
        act_kind [A] int, act_move [A] int, act_type [A] int, act_target [A] int (-1 none),
        act_num [A, F_ACT] float, act_mask [A] bool (A = MAX_ACT; actions in `legal` order).
        """
        V = self.vocab
        team_preview = bool(team_preview)
        ent_cat = np.zeros((N_ENT, len(ENT_CAT)), np.int64)
        bags = {name: np.zeros((N_ENT, L), np.int64) for name, _, L in ENT_BAGS}
        ent_num = np.zeros((N_ENT, F_ENT), np.float32)
        ent_mask = np.zeros(N_ENT, bool)
        names = [None] * N_ENT
        mons = [None] * N_ENT
        for s, (side, own) in enumerate((("user", True), ("opponent", False))):
            sd = view.get(side) or {}
            ps = [(sd.get("active"), True)] + [(p, False) for p in (sd.get("reserve") or [])]
            ps = [(p, a) for p, a in ps if p][:N_OWN]
            for j, (p, a) in enumerate(ps):
                i = s * N_OWN + j
                self._entity(p, own, a, team_preview, ent_cat, bags, ent_num, i)
                ent_mask[i] = True
                names[i] = to_id(p.get("name"))
                mons[i] = p

        # calc_lite features (public information only)
        weather = None if view.get("weather") in (None, "none") else to_id(view.get("weather"))
        sides = [(view.get("user") or {}).get("side_conditions") or {},
                 (view.get("opponent") or {}).get("side_conditions") or {}]
        own_act = mons[0] if ent_mask[0] and not team_preview else None
        opp_act = mons[N_OWN] if ent_mask[N_OWN] and not team_preview else None
        jt = ENT_NUM.index("threat_frac")
        for i in range(N_ENT):
            if not ent_mask[i]:
                continue
            p, own = mons[i], i < N_OWN
            cur = max(float(ent_num[i, 2]), 0.01)
            other = opp_act if own else own_act
            if team_preview:  # no actives yet: against the other side's lead slot
                other = mons[N_OWN] if own else mons[0]
            if other is not None and ent_num[i, 2] > 0:
                t = self._best(other, not own, p, own, weather, sides[0 if own else 1])
                ob = self._best(p, own, other, not own, weather, sides[1 if own else 0])
                ent_num[i, jt] = min(t, 3.0) / 3.0
                ent_num[i, jt + 1] = min(t / cur, 3.0) / 3.0
                ent_num[i, jt + 2] = min(ob, 3.0) / 3.0
                other_cur = max(float((other.get("hp") or 0)) / max(float(other.get("max_hp") or 1), 1.0), 0.01)
                ent_num[i, jt + 3] = min(ob / other_cur, 3.0) / 3.0
            ent_num[i, jt + 4] = min(self._hazard(p, sides[0 if own else 1]), 1.0)

        # field
        field_cat = np.array([V["weather"](None if view.get("weather") in (None, "none") else view.get("weather")),
                              V["terrain"](None if view.get("field") in (None, "none") else view.get("field"))],
                             np.int64)
        fn = np.zeros(F_FIELD, np.float32)
        fn[0] = bool(view.get("trick_room"))
        fn[1] = min(turn or 0, 100) / 30.0
        fn[2] = math.log1p(turn or 0) / 4.0
        fn[3] = min(k or 0, 3) / 3.0
        fn[4] = team_preview
        o = 5
        for side in ("user", "opponent"):
            sc = (view.get(side) or {}).get("side_conditions") or {}
            for j, c in enumerate(SIDE_CONDS):
                v = sc.get(c) or 0
                fn[o + j] = min(float(v), 8.0) / SIDE_SCALE.get(c, 8)
            o += len(SIDE_CONDS)
        own_alive = sum(1 for i in range(N_OWN) if ent_mask[i] and ent_num[i, 2] > 0)
        opp_alive = sum(1 for i in range(N_OWN, N_ENT) if ent_mask[i] and ent_num[i, 2] > 0)
        fn[o] = own_alive / 6.0
        fn[o + 1] = opp_alive / 6.0
        fn[o + 2] = float(ent_num[:N_OWN, 2].sum()) / 6.0
        fn[o + 3] = float(ent_num[N_OWN:, 2].sum()) / 6.0
        fn[o + 4] = any(a.endswith("-tera") for a in legal)
        fn[o + 5] = any(m and m.get("terastallized") for m in mons[N_OWN:])
        n_sw = sum(1 for a in legal if a.startswith("switch "))
        fn[o + 6] = len(legal) / 13.0
        fn[o + 7] = (len(legal) - n_sw) / 8.0
        fn[o + 8] = n_sw / 5.0
        if own_act is not None and opp_act is not None:
            s_own, s_opp = self._speed(own_act, True, sides[0]), self._speed(opp_act, False, sides[1])
            if view.get("trick_room"):
                s_own, s_opp = s_opp, s_own
            fn[o + 9] = np.tanh((s_own - s_opp) / 20.0)
            fn[o + 10] = float(np.clip(math.log(s_own / s_opp), -2, 2)) / 2.0
            fn[o + 11] = ent_num[0, jt + 1]
            fn[o + 12] = ent_num[0, jt + 3]

        # actions
        A = MAX_ACT
        act_kind = np.zeros(A, np.int64)
        act_move = np.zeros(A, np.int64)
        act_type = np.zeros(A, np.int64)
        act_target = np.full(A, -1, np.int64)
        act_num = np.zeros((A, F_ACT), np.float32)
        act_mask = np.zeros(A, bool)
        own_active = mons[0] if ent_mask[0] else None
        opp_active = mons[N_OWN] if ent_mask[N_OWN] else None
        opp_types = self._cur_types(opp_active) if opp_active else []
        my_types = self._cur_types(own_active) if own_active else []
        my_spe = ent_num[0, ENT_NUM.index("stat_speed")] * 500.0 if own_active else 0.0
        opp_spe_lo = ent_num[N_OWN, ENT_NUM.index("spe_min")] * 400.0 if opp_active else 0.0
        for a_i, a in enumerate(legal[:A]):
            f = act_num[a_i]
            act_mask[a_i] = True
            if a.startswith("switch "):
                tgt = to_id(a[len("switch "):])
                idx = next((i for i in range(N_OWN) if names[i] == tgt and i != 0), None)
                if idx is None:
                    idx = next((i for i in range(N_OWN) if names[i] == tgt), None)
                if idx is None:  # forme mismatch: prefix match
                    idx = next((i for i in range(N_OWN) if names[i] and (names[i].startswith(tgt) or tgt.startswith(names[i]))), -1)
                act_kind[a_i] = 3
                act_target[a_i] = idx
                f[2] = 1
                if idx >= 0 and mons[idx]:
                    sw_types = self._cur_types(mons[idx])
                    effs = [self._eff(t, sw_types) for t in opp_types] or [1.0]
                    worst = max(effs)
                    f[ACT_NUM.index("sw_def_log2")] = math.log2(worst) / 2.0 if worst > 0 else -2.0
                    f[ACT_NUM.index("sw_def_immune")] = min(effs) == 0
                    f[ACT_NUM.index("sw_hp")] = ent_num[idx, 2]
                    sw_spe = ent_num[idx, ENT_NUM.index("stat_speed")] * 500.0
                    f[ACT_NUM.index("sw_speed_cmp")] = np.tanh((sw_spe - opp_spe_lo) / 50.0) if opp_active else 0.0
                    f[ACT_NUM.index("sw_status")] = bool(mons[idx].get("status"))
                    f[ACT_NUM.index("sw_threat_vs_cur")] = ent_num[idx, jt + 1]
                    f[ACT_NUM.index("sw_hazard")] = ent_num[idx, jt + 4]
                continue
            tera = a.endswith("-tera")
            mid = a[:-len("-tera")] if tera else a
            mid = to_id(mid)
            m = self.moves.get(mid) or {"type": "typeless", "bp": 0, "acc": 100, "always": False, "priority": 0,
                                        "cat": "status"}
            act_kind[a_i] = 2 if tera else 1
            act_move[a_i] = V["moves"](mid)
            act_type[a_i] = V["types"](m["type"])
            f[0] = 1
            f[1] = tera
            f[3] = (m["bp"] or 0) / 150.0
            f[4] = (m["acc"] or 0) / 100.0
            f[5] = bool(m.get("always"))
            f[6] = (m["priority"] or 0) / 5.0
            f[7 + (CATEGORIES.index(m["cat"]) if m["cat"] in CATEGORIES else 2)] = 1
            atk_types = [own_active.get("tera_type")] + my_types if (tera and own_active) else my_types
            o = 7 + len(CATEGORIES)
            f[o] = m["type"] in atk_types
            if m["cat"] != "status" and opp_active:
                eff = self._eff(m["type"], opp_types)
                f[o + 1] = math.log2(eff) / 2.0 if eff > 0 else -2.0
                f[o + 2] = eff == 0
            f[o + 3] = bool(m.get("self"))
            f[o + 4] = bool(m.get("heal"))
            f[o + 5] = bool(m.get("boosts_self"))
            f[o + 6] = bool(m.get("status"))
            f[o + 7] = mid in _SWITCH_OUT
            f[o + 8] = bool(m.get("drain"))
            f[o + 9] = bool(m.get("recoil"))
            f[o + 10] = mid in _HAZARD
            f[o + 11] = bool(m.get("multihit"))
            if own_active is not None and opp_active is not None and m["cat"] != "status":
                d = self._damage(own_active, True, opp_active, False, mid, tera, weather, sides[1])
                cur = max(float(opp_active.get("hp") or 0) / max(float(opp_active.get("max_hp") or 1), 1.0), 0.01)
                f[ACT_NUM.index("dmg_frac")] = min(d, 3.0) / 3.0
                f[ACT_NUM.index("dmg_vs_cur")] = min(d / cur, 3.0) / 3.0
                f[ACT_NUM.index("ko")] = d >= cur
        ev_cat, ev_num, ev_mask = self._events(history, player, turn)
        out = {"ev_cat": ev_cat, "ev_num": ev_num, "ev_mask": ev_mask,
               "ent_cat": ent_cat, "ent_num": ent_num, "ent_mask": ent_mask, "field_cat": field_cat,
               "field_num": fn, "act_kind": act_kind, "act_move": act_move, "act_type": act_type,
               "act_target": act_target, "act_num": act_num, "act_mask": act_mask}
        for name, arr in bags.items():
            out["ent_bag_" + name] = arr
        return out


KEYS = ("ev_cat", "ev_num", "ev_mask", "ent_cat", "ent_num", "ent_mask", "field_cat", "field_num", "act_kind", "act_move", "act_type",
        "act_target", "act_num", "act_mask") + tuple("ent_bag_" + n for n, _, _ in ENT_BAGS)


def collate(encoded):
    """Stack a list of encode() outputs into batch arrays."""
    return {k: np.stack([e[k] for e in encoded]) for k in KEYS}


def legal_trunc(legal):
    """Actions beyond MAX_ACT are dropped (never happens in Gen9 singles: at most 13)."""
    return list(legal)[:MAX_ACT]
