#!/usr/bin/env python3
"""S-A belief targets: one example per (decision, alive opponent Pokemon in the bot's view).

Input: decision records from tools/dataset/extract.py (log version 2).
Output: JSONL(.gz), one example per (decision, opponent Pokemon) with
  uid, duid (decision uid), game, run_id, side, turn, k, team_preview, cfg,
  roster   opponent roster key: sorted Showdown display species joined by '|'
           canonical keys contain six species; the strict digest manifest stores only their SHA-256 digests,
  split    train / dev / test, assigned by a hash of the roster (held out by roster),
  species  the view's species id (what the bot sees, e.g. ogerponwellspringtera),
  view     the bot's public view of this Pokemon (decision log v2 fields, untouched),
  ctx      public context: teammates seen, own active / own team species, weather, ...,
  y        TRUE attributes (labels, from the Showdown battle log; never a model input):
           item, ability, tera, moves, nature, evs, evclass, spread,
  hidden   which attributes are not revealed in the view: item/ability/tera (bool),
           moves (list of true moves not yet revealed), spread (always True),
  item_state  how Foul Play encodes the item (see below),
  played   the decision's sampled worlds for this Pokemon: [[chance, set], ...],
  dq       data-quality flags (view disagrees with the true set).

Item encoding in Foul Play (fp/battle/state.py, fp/battle/protocol.py, checked 2026-10-26):
  item == "unknownitem", removed_item None   -> never revealed                     (state "unknown")
  item == <id>, removed_item None             -> revealed or inferred (choicescarf from speed order,
                                                 heavydutyboots from hazards; item_inferred is not logged)
                                                                                    (state "known")
  item None, removed_item <id>                -> the original item <id> was revealed and then
                                                 knocked off / consumed / stolen   (state "removed")
  item <id2>, removed_item <id>               -> original <id> swapped away (Trick, Thief, ...) and the
                                                 Pokemon now holds <id2>            (state "removed")
  item None, removed_item None                -> holds nothing, original unknown (Z-move only; should
                                                 not occur in Gen 9)                (state "none_unexplained")
  Illusion ending resets the disguised Pokemon's item to "unknownitem".
  In the logged worlds (decision log opp_sets) a None item appears as the STRING "None":
  fp/search/poke_engine_helpers.py sets pkmn.item = "None" on the battle copy (also when knocked_off)
  before the opp_sets are logged.
  The original item is hidden only in state "unknown"; only those examples are scored for item.

Only alive opponent Pokemon are kept: Foul Play does not sample fainted ones, and they do not matter
for play. Names are Showdown ids (lowercase alphanumerics); the view species is matched to the true
set by National Dex number (species clause makes it unique), so formes (ogerpontealtera vs Ogerpon,
landorustherian vs Landorus-Therian, palafinhero vs Palafin) match.
"""
import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:  # Package import (tests) and direct-script import (human_prep / CLI).
    from .roster_hash import (
        EXPECTED_VAL_ROSTERS, canonical_roster_key, load_val_roster_digests, roster_digest, toid,
    )
except ImportError:  # pragma: no cover - exercised by direct-script import tests.
    from roster_hash import (
        EXPECTED_VAL_ROSTERS, canonical_roster_key, load_val_roster_digests, roster_digest, toid,
    )

HARDREAD = Path(__file__).resolve().parents[2]
FP_DATA = HARDREAD / "foul-play" / "fp" / "data"
POOLS = HARDREAD / "data" / "pools-v1.json"

STATS = ("hp", "atk", "def", "spa", "spd", "spe")
NEUTRAL_NATURES = {"hardy", "docile", "serious", "bashful", "quirky"}
TURN_BUCKETS = ((0, 0, "0 (preview)"), (1, 5, "1-5"), (6, 10, "6-10"), (11, 20, "11-20"), (21, 10 ** 6, "21+"))


def canon_nature(n):
    n = toid(n) or "serious"
    return "serious" if n in NEUTRAL_NATURES else n


def ev_list(evs):
    if evs is None:
        return None
    if isinstance(evs, dict):
        return [int(evs.get(s) or 0) for s in STATS]
    if isinstance(evs, str):
        evs = evs.replace("/", ",").split(",")
    return [int(v) for v in evs]


def ev_class(evs):
    """Coarse EV class: the two stats with the most EVs (ties broken by stat order), e.g. 'atk+spe'."""
    evs = ev_list(evs)
    if not evs:
        return None
    order = sorted(range(6), key=lambda i: (-evs[i], i))
    top = sorted(i for i in order[:2] if evs[i] > 0)
    return "+".join(STATS[i] for i in top) or "none"


def spread_class(nature, evs):
    return "{}|{}".format(canon_nature(nature), ev_class(evs))


def turn_bucket(turn, team_preview=False):
    t = 0 if team_preview else int(turn or 0)
    for lo, hi, name in TURN_BUCKETS:
        if lo <= t <= hi:
            return name
    return "?"


_POKEDEX = None


def pokedex():
    global _POKEDEX
    if _POKEDEX is None:
        _POKEDEX = json.loads((FP_DATA / "pokedex.json").read_text())
    return _POKEDEX


def dex_entry(name):
    dex = pokedex()
    n = toid(name)
    if n in dex:
        return dex[n]
    # Foul Play's fallback for unknown names: longest pokedex key that prefixes the name
    cands = [k for k in dex if n and n.startswith(k)]
    return dex[max(cands, key=len)] if cands else None


def dex_num(name):
    e = dex_entry(name)
    return e.get("num") if e else None


def species_abilities(name):
    e = dex_entry(name)
    return sorted({toid(a) for a in (e or {}).get("abilities", {}).values()})


def species_key(name):
    """Out-of-battle species id: battle-only formes map to their base forme (ogerponwellspringtera ->
    ogerponwellspring, palafinhero -> palafin); other formes are kept (landorustherian)."""
    e = dex_entry(name)
    if e and e.get("battleOnly"):
        b = e["battleOnly"]
        return toid(b if isinstance(b, str) else b[0])
    return toid(name)


# nature -> (boosted stat, lowered stat); neutral natures map to (None, None)
NATURES = {
    "lonely": ("atk", "def"), "brave": ("atk", "spe"), "adamant": ("atk", "spa"), "naughty": ("atk", "spd"),
    "bold": ("def", "atk"), "relaxed": ("def", "spe"), "impish": ("def", "spa"), "lax": ("def", "spd"),
    "timid": ("spe", "atk"), "hasty": ("spe", "def"), "jolly": ("spe", "spa"), "naive": ("spe", "spd"),
    "modest": ("spa", "atk"), "mild": ("spa", "def"), "quiet": ("spa", "spe"), "rash": ("spa", "spd"),
    "calm": ("spd", "atk"), "gentle": ("spd", "def"), "sassy": ("spd", "spe"), "careful": ("spd", "spa"),
    "serious": (None, None),
}


def speed_stat(name, nature, evs, level=100, iv=31):
    e = dex_entry(name)
    if not e:
        return None
    base = e["baseStats"]["speed"]
    evs = ev_list(evs)
    raw = (2 * base + iv + evs[5] // 4) * level // 100 + 5
    plus, minus = NATURES.get(canon_nature(nature), (None, None))
    mult = 1.1 if plus == "spe" else 0.9 if minus == "spe" else 1.0
    return int(raw * mult)


def spread_speed_ok(name, nature, evs, speed_range, scarf_possible, level=100):
    """Foul Play's speed check (PokemonSet.speed_check): the set's speed (x1.5 with a Choice Scarf)
    must lie in the view's speed_range. The item is unknown here, so a scarf is allowed when possible."""
    if not speed_range:
        return True
    lo, hi = speed_range[0] or 0, speed_range[1]
    hi = float("inf") if hi is None else hi
    sp = speed_stat(name, nature, evs, level)
    if sp is None:
        return True
    if lo <= sp <= hi:
        return True
    return bool(scarf_possible) and lo <= int(sp * 1.5) <= hi


def norm_roster(roster):
    """Order- and format-free six-species key shared with the versioned hash manifest."""
    return canonical_roster_key(roster)


def hr_val_roster_digests(pool_path=None, expected_count=EXPECTED_VAL_ROSTERS):
    """Load only the validated held-out digest set; malformed or absent manifests raise."""
    return load_val_roster_digests(pool_path or POOLS, expected_count=expected_count)


def reserved_roster_digests(split_path=None, validated_path=None):
    """Hash reserved species-only metadata; never open reserved team files or silently omit them."""
    split_path = Path(split_path or (HARDREAD.parent / "data/teams/splits/hl_05_26-gen9ou-split-v1.json"))
    validated_path = Path(validated_path or (HARDREAD.parent / "data/teams/validated/hl_05_26-gen9ou.jsonl"))
    if not split_path.is_file():
        raise FileNotFoundError("required reserved split metadata is missing")
    if not validated_path.is_file():
        raise FileNotFoundError("required validated species metadata is missing")
    try:
        split = json.loads(split_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("required reserved split metadata is unreadable or malformed") from exc
    if not isinstance(split, dict) or not isinstance(split.get("reserved_files"), list):
        raise ValueError("reserved split metadata must contain a reserved_files list")
    names = split["reserved_files"]
    if any(not isinstance(name, str) or not name or Path(name).name != name for name in names):
        raise ValueError("reserved_files must contain nonempty basenames")
    if len(names) != len(set(names)):
        raise ValueError("reserved_files must be unique")
    reserved = set(names)
    found = {}
    try:
        with validated_path.open() as f:
            for line_number, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError("validated species metadata contains malformed JSON") from exc
                if not isinstance(row, dict) or not isinstance(row.get("file"), str):
                    raise ValueError("validated species metadata row is malformed")
                name = Path(row["file"]).name
                if name in reserved:
                    if name in found:
                        raise ValueError("validated species metadata duplicates a reserved filename")
                    found[name] = roster_digest(row.get("species"))
    except OSError as exc:
        raise ValueError("required validated species metadata is unreadable") from exc
    if set(found) != reserved:
        raise ValueError("validated species metadata does not cover every reserved filename")
    return set(found.values())


def excluded_rosters(pool_path=None, split_path=None, validated_path=None,
                     expected_count=EXPECTED_VAL_ROSTERS):
    """Return hr_val and reserved roster digests; all required metadata must validate first."""
    hr_val = hr_val_roster_digests(pool_path, expected_count=expected_count)
    reserved = reserved_roster_digests(split_path, validated_path)
    return hr_val | reserved


def record_hr_val_diagnostic(stats, roster, hr_val_digests):
    """Count hr_val membership only; reserved-only exclusions do not inflate this diagnostic."""
    if roster_digest(roster) in hr_val_digests:
        stats["examples_from_hr_val_rosters"] += 1
        return True
    return False


def roster_key(team):
    return "|".join(sorted(s["species"] for s in team))


def split_of(roster, test_pct=20, dev_pct=10):
    h = int(hashlib.sha1(roster.encode()).hexdigest()[:8], 16) % 100
    return "test" if h < test_pct else "dev" if h < test_pct + dev_pct else "train"


def true_set(s):
    """Normalise one true set (extract.py norm_set output) to ids."""
    species = toid(s["species"])
    e = dex_entry(species) or {}
    tera = toid(s.get("tera_type")) or toid((e.get("types") or [None])[0])
    evs = ev_list(s.get("evs")) or [0] * 6
    return {
        "species": species,
        "item": toid(s.get("item")) or "none",
        "ability": toid(s.get("ability")),
        "tera": tera,
        "moves": [toid(m) for m in s.get("moves") or []],
        "nature": canon_nature(s.get("nature")),
        "evs": evs,
        "evclass": ev_class(evs),
        "spread": spread_class(s.get("nature"), evs),
    }


def item_state(p):
    item, removed = p.get("item"), p.get("removed_item")
    if removed:
        return "removed"
    if item == "unknownitem":
        return "unknown"
    if item is None or item == "None":
        return "none_unexplained"
    return "known"


def world_set(w):
    return {"item": w.get("item"), "ability": w.get("ability"), "tera": w.get("tera_type"),
            "moves": list(w.get("moves") or []), "nature": canon_nature(w.get("nature")),
            "evs": ev_list(w.get("evs")) or [0] * 6}


def examples_from_decision(r, stats):
    if "view" not in r:  # a games-file record (<out>.games.jsonl.gz) passed by a glob
        stats["skip_not_a_decision"] += 1
        return []
    if r.get("log_version", 1) < 2:
        stats["skip_log_v1"] += 1
        return []
    view = r["view"]
    opp = view.get("opponent") or {}
    opp_view = ([opp["active"]] if opp.get("active") else []) + list(opp.get("reserve") or [])
    if not opp_view or not r.get("opp_team"):
        stats["skip_no_opp"] += 1
        return []
    true_by_num = {}
    for s in r["opp_team"]:
        true_by_num[dex_num(s["species"])] = s
    roster = roster_key(r["opp_team"])
    user = view.get("user") or {}
    own_active = (user.get("active") or {}).get("name")
    own_team = [p["name"] for p in ([user["active"]] if user.get("active") else []) + list(user.get("reserve") or [])]
    opp_names = [p["name"] for p in opp_view]
    n_opp_fainted = sum(1 for p in opp_view if (p.get("hp") or 0) <= 0)
    n_own_fainted = sum(1 for p in ([user["active"]] if user.get("active") else []) + list(user.get("reserve") or [])
                        if (p.get("hp") or 0) <= 0)
    worlds = r.get("worlds") or []
    out = []
    for slot, p in enumerate(opp_view):
        if (p.get("hp") or 0) <= 0:
            stats["skip_fainted"] += 1
            continue
        s = true_by_num.get(dex_num(p["name"]))
        if s is None:
            stats["skip_unmatched_species"] += 1
            continue
        y = true_set(s)
        revealed = [toid(m) for m in p.get("moves") or []]
        ist = item_state(p)
        hidden = {
            "item": ist == "unknown",
            "ability": p.get("ability") is None,
            "tera": not p.get("terastallized"),
            "moves": [m for m in y["moves"] if m not in revealed],
            "spread": True,
        }
        dq = {}
        if ist == "known" and toid(p["item"]) != y["item"]:
            dq["item_view_wrong"] = [p["item"], y["item"]]
        if ist == "removed" and toid(p["removed_item"]) != y["item"]:
            dq["removed_item_wrong"] = [p["removed_item"], y["item"]]
        if p.get("ability") is not None and toid(p["ability"]) != y["ability"]:
            # battle-only formes change the ability (Ogerpon-*-Tera: Embody Aspect): expected
            key = "ability_view_forme" if species_key(p["name"]) != toid(p["name"]) else "ability_view_wrong"
            dq[key] = [p["ability"], y["ability"]]
        if p.get("terastallized") and toid(p.get("tera_type")) != y["tera"]:
            dq["tera_view_wrong"] = [p.get("tera_type"), y["tera"]]
        extra = [m for m in revealed if m not in y["moves"]]
        if extra:
            dq["moves_not_in_true"] = extra
        played = []
        for w in worlds:
            for ws in w.get("opp_sets") or []:
                if ws.get("name") == p["name"]:
                    played.append([w.get("chance") or 0.0, world_set(ws)])
                    break
        out.append({
            "uid": "{}/{}".format(r["uid"], slot), "duid": r["uid"],
            "game": "{}/g{:04d}".format(r["run_id"], r["game"]), "run_id": r["run_id"],
            "side": r["side"], "turn": r["turn"], "k": r["k"], "team_preview": bool(r.get("team_preview")),
            "cfg": r.get("cfg"), "roster": roster, "split": split_of(roster),
            "species": toid(p["name"]), "skey": species_key(p["name"]), "slot": slot, "active": slot == 0 and bool(opp.get("active")),
            "view": p,
            "ctx": {"teammates": [n for n in opp_names if n != p["name"]], "own_active": own_active,
                    "own_team": own_team, "weather": view.get("weather"), "field": view.get("field"),
                    "trick_room": view.get("trick_room"), "n_opp_fainted": n_opp_fainted,
                    "n_own_fainted": n_own_fainted},
            "y": y, "hidden": hidden, "item_state": ist, "played": played, "dq": dq,
        })
    return out


def open_text(path, mode="rt"):
    path = str(path)
    return gzip.open(path, mode) if path.endswith(".gz") else open(path, mode[0])


def iter_jsonl(paths):
    for path in paths:
        with open_text(path) as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def load_examples(path, splits=None):
    exs = []
    for e in iter_jsonl([path]):
        if splits is None or e["split"] in splits:
            exs.append(e)
    return exs


def capped(examples, max_per_mon, seed=0):
    """Keep at most `max_per_mon` rows per (game, side, opponent Pokemon), chosen at random (seeded per group).

    Streams: rows of one game must be contiguous, as extract.py / partial_targets.py write them (a
    non-contiguous group is just capped per contiguous run). Consecutive decisions repeat the same Pokemon state,
    so capping loses little and makes every (game, side, Pokemon) weigh about the same."""
    import random as _random
    if not max_per_mon:
        yield from examples
        return
    key, buf = None, []

    def flush():
        groups = defaultdict(list)
        for e in buf:
            groups[(e["side"], e["skey"])].append(e)
        for (side, sk), rows in groups.items():
            if len(rows) > max_per_mon:
                rng = _random.Random("{}|{}|{}|{}".format(rows[0]["game"], side, sk, seed))
                rows = sorted(rng.sample(rows, max_per_mon), key=lambda e: (e["turn"], e["k"]))
            yield from rows

    for e in examples:
        k = e["game"]
        if k != key:
            yield from flush()
            key, buf = k, []
        buf.append(e)
    yield from flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="decision files from tools/dataset/extract.py (.jsonl[.gz])")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-per-mon", type=int, default=0,
                    help="keep at most N decisions per (game, side, opponent Pokemon) (training files; 0 = all)")
    ap.add_argument("--force-split", default=None,
                    help="put every row in this split (e.g. 'test' for the hr_val farm, the pre-registered S-A test set)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    val_roster_digests = hr_val_roster_digests()
    stats = Counter()
    states = Counter()
    hidden_counts = Counter()
    dq_counts = Counter()
    dq_examples = defaultdict(list)
    rosters = defaultdict(set)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    def gen():
        for r in iter_jsonl(args.inputs):
            stats["records"] += 1
            for e in examples_from_decision(r, stats):
                if args.force_split:
                    e["split"] = args.force_split
                yield e

    with open_text(args.out, "wt") as out:
        for e in capped(gen(), args.max_per_mon, args.seed):
            out.write(json.dumps(e, separators=(",", ":")) + "\n")
            stats["examples"] += 1
            stats["examples_" + e["split"]] += 1
            rosters[e["split"]].add(e["roster"])
            record_hr_val_diagnostic(stats, e["roster"], val_roster_digests)
            states[e["item_state"]] += 1
            for k in ("item", "ability", "tera"):
                hidden_counts[k] += e["hidden"][k]
            hidden_counts["moves_hidden_total"] += len(e["hidden"]["moves"])
            for k, v in e["dq"].items():
                dq_counts[k] += 1
                if len(dq_examples[k]) < 5:
                    dq_examples[k].append([e["uid"], e["species"], v])
    report = {"stats": dict(stats), "item_state": dict(states), "hidden": dict(hidden_counts),
              "rosters": {k: len(v) for k, v in rosters.items()},
              "data_quality": dict(dq_counts), "data_quality_examples": dict(dq_examples)}
    print(json.dumps(report, indent=1), file=sys.stderr)


if __name__ == "__main__":
    main()
