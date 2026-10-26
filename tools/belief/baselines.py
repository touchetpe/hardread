#!/usr/bin/env python3
"""S-A baselines: a probability distribution per hidden attribute for every belief example.

Methods (--method):
  usage   Smogon usage prior (foul-play/fp/data/smogon_stats_cache/gen9ou-0.json, the same file Foul
          Play and the Azure farm use) for the species, renormalised after the view's hard constraints:
          impossible items, no choice item when can_have_choice_item is False, impossible abilities,
          revealed moves (excluded), speed range (spreads whose speed, x1.5 with a possible Choice
          Scarf, falls outside the view's speed_range are dropped). Full stats, not Foul Play's
          truncated top-10 items / top-20 spreads / top-6 tera types.
  fp      Foul Play's own belief: the opponent Pokemon is rebuilt as an fp Pokemon from the v2 view
          fields and fp.search.standard_battles.sample_pokemon is called N times (default 64) on deep
          copies, with a StandardBattleMode whose TeamDatasets and SmogonSets are initialised exactly
          as in a gen9ou battle (initialize_team_preview_datasets with the game's 12 team-preview
          species). Needs the Foul Play env (Python 3.13):
          ../environments/foulplay/bin/python tools/belief/baselines.py --method fp ...
          Identical views within a game share one set of N samples (the sampler reads only the view).
  played  As played: the decision's logged worlds (2-8 sampled sets, chance-weighted).

Output JSONL(.gz): {"uid", "item", "ability", "tera", "nature", "evclass", "spread": {class: p},
"moves": {move: P(move in the set)} (revealed moves excluded), "n": samples}.
Sampled sets that leave an attribute unsampled ("unknownitem", ability None) keep that value as a
class of its own, which never matches the truth.
"""
import argparse
import json
import logging
import random
import sys
import time
import zlib
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from targets import (HARDREAD, FP_DATA, canon_nature, dex_entry, ev_class, ev_list, iter_jsonl, open_text,  # noqa: E402
                     species_key, spread_class, spread_speed_ok, toid)

SMOGON_STATS = FP_DATA / "smogon_stats_cache" / "gen9ou-0.json"
CHOICE_ITEMS = {"choiceband", "choicespecs", "choicescarf"}
CAT_ATTRS = ("item", "ability", "tera", "nature", "evclass", "spread")


def _norm(d, digits=6):
    tot = sum(d.values())
    if tot <= 0:
        return {}
    return {k: round(v / tot, digits) for k, v in sorted(d.items(), key=lambda kv: -kv[1]) if v / tot >= 1e-6}


def dist_from_sets(weighted_sets, revealed):
    """weighted_sets: [(weight, {item, ability, tera, moves, nature, evs})] -> prediction dict."""
    acc = {a: defaultdict(float) for a in CAT_ATTRS}
    moves = defaultdict(float)
    total = 0.0
    for w, s in weighted_sets:
        if w <= 0:
            continue
        total += w
        acc["item"][toid(s.get("item")) or "none"] += w
        acc["ability"][toid(s.get("ability")) or "?"] += w
        acc["tera"][toid(s.get("tera")) or "?"] += w
        evs = ev_list(s.get("evs")) or [0] * 6
        acc["nature"][canon_nature(s.get("nature"))] += w
        acc["evclass"][ev_class(evs)] += w
        acc["spread"][spread_class(s.get("nature"), evs)] += w
        for m in set(toid(m) for m in s.get("moves") or []):
            if m not in revealed:
                moves[m] += w
    out = {a: _norm(acc[a]) for a in CAT_ATTRS}
    out["moves"] = {m: round(v / total, 6) for m, v in sorted(moves.items(), key=lambda kv: -kv[1])} if total else {}
    out["n"] = len(weighted_sets)
    return out


# ---------------------------------------------------------------- (a) usage prior
class UsagePrior:
    def __init__(self, path=SMOGON_STATS):
        raw = json.loads(Path(path).read_text())
        self.stats = {toid(k): v for k, v in raw.items()}
        self.cache = {}
        self.missing = defaultdict(int)

    def entry(self, name):
        for cand in (toid(name), species_key(name)):
            if cand in self.stats:
                return self.stats[cand]
        e = dex_entry(name) or {}
        for key in ("changesFrom", "baseSpecies"):
            if e.get(key) and toid(e[key]) in self.stats:
                return self.stats[toid(e[key])]
        return None

    def base(self, name):
        if name not in self.cache:
            ent = self.entry(name)
            if ent is None:
                self.missing[name] += 1
                self.cache[name] = None
            else:
                raw = ent["Raw count"] or 1
                items = defaultdict(float)
                for k, c in ent["Items"].items():
                    if c > 0:
                        items["none" if toid(k) in ("", "nothing") else toid(k)] += c
                tera = {toid(k): c for k, c in ent.get("Tera Types", {}).items() if c > 0 and toid(k) != "nothing"}
                moves = {toid(k): min(1.0, c / raw) for k, c in ent["Moves"].items() if c > 0 and toid(k) not in ("", "nothing")}
                spreads = []
                for k, c in ent["Spreads"].items():
                    if c > 0:
                        nat, evs = k.split(":")
                        spreads.append((nat, ev_list(evs), c))
                self.cache[name] = {"items": dict(items), "abilities": {toid(k): c for k, c in ent["Abilities"].items() if c > 0},
                                    "tera": tera, "moves": moves, "spreads": spreads}
        return self.cache[name]

    def predict(self, e):
        v = e["view"]
        b = self.base(e["species"])
        revealed = {toid(m) for m in v.get("moves") or []}
        if b is None:
            abil = {a: 1.0 for a in (toid(x) for x in (dex_entry(e["species"]) or {}).get("abilities", {}).values())}
            return {"item": {}, "ability": _norm(abil), "tera": {}, "nature": {}, "evclass": {}, "spread": {}, "moves": {}, "n": 0}
        imp = set(v.get("impossible_items") or [])
        if v.get("can_have_choice_item") is False:
            imp |= CHOICE_ITEMS
        items = {k: c for k, c in b["items"].items() if k not in imp} or b["items"]
        imp_ab = set(v.get("impossible_abilities") or [])
        abil = {k: c for k, c in b["abilities"].items() if k not in imp_ab} or b["abilities"]
        item_view = v.get("item")
        if item_view == "unknownitem" and not v.get("removed_item"):
            scarf = v.get("can_have_choice_item") is not False and "choicescarf" not in imp
        else:
            scarf = item_view == "choicescarf"
        sr = v.get("speed_range")
        key = (e["species"], tuple(sr) if sr else None, scarf, v.get("level") or 100)
        sp = self.cache.get(("spreads",) + key)
        if sp is None:
            nat, evc, spr = defaultdict(float), defaultdict(float), defaultdict(float)
            for strict in (True, False):
                for n, evs, c in b["spreads"]:
                    if strict and not spread_speed_ok(e["species"], n, evs, sr, scarf, v.get("level") or 100):
                        continue
                    nat[canon_nature(n)] += c
                    evc[ev_class(evs)] += c
                    spr[spread_class(n, evs)] += c
                if nat:
                    break
            sp = (_norm(nat), _norm(evc), _norm(spr))
            self.cache[("spreads",) + key] = sp
        return {"item": _norm(items), "ability": _norm(abil), "tera": _norm(b["tera"]),
                "nature": sp[0], "evclass": sp[1], "spread": sp[2],
                "moves": {m: round(p, 6) for m, p in sorted(b["moves"].items(), key=lambda kv: -kv[1]) if m not in revealed},
                "n": 0}


# ---------------------------------------------------------------- (b) Foul Play's sampler
_FP = {}


def fp_setup():
    if _FP:
        return _FP
    fp_root = HARDREAD / "foul-play"
    sys.path.insert(0, str(fp_root))
    logging.disable(logging.WARNING)  # the sampler logs every sampled set at INFO; keep warnings off too
    from fp.config import FoulPlayConfig
    FoulPlayConfig.pokemon_format = "gen9ou"
    FoulPlayConfig.smogon_stats = None
    from fp.format_spec import FormatSpec
    from fp.data.mods.apply_mods import apply_mods
    apply_mods(FormatSpec.from_format_string("gen9ou"))  # no-op for gen9, kept for fidelity
    from fp.battle.state import Pokemon, StatRange
    from fp.modes.standard_battle import StandardBattleMode
    from fp.search.standard_battles import sample_pokemon
    _FP.update(Pokemon=Pokemon, StatRange=StatRange, Mode=StandardBattleMode, sample=sample_pokemon)
    return _FP


def fp_mode(names):
    fp = fp_setup()
    mode = fp["Mode"]()
    # same call Foul Play makes after team preview (fp/modes/standard_battle.py)
    mode.initialize_team_preview_datasets("gen9ou", set(names), "")
    return mode


def fp_pokemon(v):
    fp = fp_setup()
    pk = fp["Pokemon"](v["name"], v.get("level") or 100)
    if v.get("max_hp"):
        pk.max_hp = v["max_hp"]
        pk.hp = v.get("hp") or 0
    pk.status = v.get("status")
    pk.item = v.get("item")
    pk.removed_item = v.get("removed_item")
    pk.ability = v.get("ability")
    pk.original_ability = v.get("original_ability")
    pk.tera_type = v.get("tera_type")
    pk.terastallized = bool(v.get("terastallized"))
    pk.moves = []
    for m in v.get("moves") or []:
        pk.add_move(m)
    pk.impossible_items = set(v.get("impossible_items") or [])
    pk.impossible_abilities = set(v.get("impossible_abilities") or [])
    if v.get("can_have_choice_item") is not None:
        pk.can_have_choice_item = v["can_have_choice_item"]
    sr = v.get("speed_range")
    if sr:
        pk.speed_range = fp["StatRange"](min=sr[0] or 0, max=float("inf") if sr[1] is None else sr[1])
    if v.get("nature") and v.get("evs"):
        pk.set_spread(v["nature"], list(v["evs"]))
    return pk


SIG_FIELDS = ("name", "level", "item", "removed_item", "ability", "tera_type", "terastallized", "moves",
              "impossible_items", "impossible_abilities", "can_have_choice_item", "speed_range")


def fp_group(args):
    """One (game, side): initialise the datasets with the game's team-preview species, then sample."""
    names, exs, n, seed = args
    fp = fp_setup()
    t0 = time.perf_counter()
    mode = fp_mode(names)
    t_init = time.perf_counter() - t0
    cache, out, calls, t_sample = {}, [], 0, 0.0
    for e in exs:
        v = e["view"]
        sig = json.dumps([v.get(k) for k in SIG_FIELDS])
        if sig not in cache:
            random.seed(seed ^ zlib.crc32(sig.encode()))
            base = fp_pokemon(v)
            sets = []
            t1 = time.perf_counter()
            for _ in range(n):
                c = deepcopy(base)
                fp["sample"](c, mode)
                sets.append((1.0, {"item": c.item, "ability": c.ability, "tera": c.tera_type,
                                   "moves": [m.name for m in c.moves], "nature": c.nature, "evs": list(c.evs)}))
            t_sample += time.perf_counter() - t1
            calls += n
            cache[sig] = dist_from_sets(sets, {toid(m) for m in v.get("moves") or []})
        p = dict(cache[sig])
        p["uid"] = e["uid"]
        out.append(p)
    return out, {"init_s": t_init, "calls": calls, "sample_s": t_sample, "unique_views": len(cache), "examples": len(exs)}


def group_names(exs):
    """Team-preview species of a (game, side): the earliest example's species + teammates + own team."""
    first = min(exs, key=lambda e: (e["turn"], e["k"], e["uid"]))
    names = {first["species"], *first["ctx"]["teammates"], *first["ctx"]["own_team"]}
    if not first["team_preview"]:  # fall back to everything seen in the game
        for e in exs:
            names |= {e["species"], *e["ctx"]["teammates"], *e["ctx"]["own_team"]}
    return sorted(n for n in names if n)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--examples", required=True)
    ap.add_argument("--method", required=True, choices=["usage", "fp", "played"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--splits", default="test", help="comma-separated splits to predict (default test)")
    ap.add_argument("--n", type=int, default=64, help="fp: samples per view")
    ap.add_argument("--workers", type=int, default=8, help="fp: processes")
    ap.add_argument("--max-games", type=int, default=0, help="fp: limit to the first N (game, side) groups (0 = all)")
    ap.add_argument("--seed", type=int, default=20261026)
    args = ap.parse_args()
    splits = set(args.splits.split(","))
    exs = [e for e in iter_jsonl([args.examples]) if e["split"] in splits]
    t0 = time.time()
    n_out = 0
    info = {}
    with open_text(args.out, "wt") as out:
        if args.method == "played":
            for e in exs:
                p = dist_from_sets([(c, s) for c, s in e["played"]], {toid(m) for m in e["view"].get("moves") or []})
                p["uid"] = e["uid"]
                out.write(json.dumps(p, separators=(",", ":")) + "\n")
                n_out += 1
        elif args.method == "usage":
            prior = UsagePrior()
            for e in exs:
                p = prior.predict(e)
                p["uid"] = e["uid"]
                out.write(json.dumps(p, separators=(",", ":")) + "\n")
                n_out += 1
            info["species_missing_from_smogon"] = dict(prior.missing)
        else:
            groups = defaultdict(list)
            for e in exs:
                groups[(e["game"], e["side"])].append(e)
            keys = sorted(groups)
            if args.max_games:
                keys = keys[: args.max_games]
            jobs = [(group_names(groups[k]), groups[k], args.n, args.seed) for k in keys]
            tot = defaultdict(float)
            if args.workers > 1:
                import multiprocessing as mp
                with mp.get_context("spawn").Pool(args.workers) as pool:
                    results = pool.imap_unordered(fp_group, jobs, chunksize=1)
                    for i, (preds, st) in enumerate(results):
                        for p in preds:
                            out.write(json.dumps(p, separators=(",", ":")) + "\n")
                        n_out += len(preds)
                        for k, v in st.items():
                            tot[k] += v
                        if (i + 1) % 20 == 0:
                            print("fp: {}/{} groups, {} examples, {:.0f}s".format(i + 1, len(jobs), n_out, time.time() - t0),
                                  file=sys.stderr, flush=True)
            else:
                for job in jobs:
                    preds, st = fp_group(job)
                    for p in preds:
                        out.write(json.dumps(p, separators=(",", ":")) + "\n")
                    n_out += len(preds)
                    for k, v in st.items():
                        tot[k] += v
            info.update({k: round(v, 3) for k, v in tot.items()})
            info["groups"] = len(jobs)
            if tot["calls"]:
                info["ms_per_sample_pokemon_call"] = round(1000 * tot["sample_s"] / tot["calls"], 3)
                info["ms_per_view_at_n"] = round(1000 * tot["sample_s"] / max(1, tot["unique_views"]), 2)
                info["ms_per_dataset_init"] = round(1000 * tot["init_s"] / max(1, len(jobs)), 1)
    info.update({"method": args.method, "examples": n_out, "wall_s": round(time.time() - t0, 1)})
    print(json.dumps(info), file=sys.stderr)
    meta = Path(str(args.out).replace(".jsonl.gz", "").replace(".jsonl", "") + ".meta.json")
    meta.write_text(json.dumps(info, indent=1))


if __name__ == "__main__":
    main()
