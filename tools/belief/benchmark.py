#!/usr/bin/env python3
"""S-A benchmark: score belief predictions on hidden opponent attributes.

Population: belief examples (tools/belief/targets.py) of the chosen split(s), restricted to the
examples every compared method predicts (intersection, so all methods are scored on the same rows).
One example = one (decision, alive opponent Pokemon); each decision counts once per Pokemon.

Only hidden attributes are scored:
  item      original item never revealed (Foul Play item "unknownitem" and no removed_item)
  ability   view ability None;   ability_nt: the same restricted to species with >1 legal ability
  tera      not terastallized
  moves     the true moves not yet revealed (examples with at least one)
  nature, evclass, spread   always hidden (Foul Play never learns them)
    nature: neutral natures merged into "serious";
    evclass: the two stats with the most EVs, e.g. "atk+spe";  spread: "nature|evclass"

Metrics, categorical attributes: top-1 accuracy; NLL = -ln max(p_true, eps), eps = 1e-3 by default
(caps a miss at 6.91 nats; sampled beliefs with N samples have resolution 1/N, and 2-8 world beliefs
put zero mass on most values); Brier = sum_c (p_c - 1[c = true])^2 (0..2); zero = share of rows with
p_true = 0 (support miss); ptrue = mean p_true, the expected accuracy of a draw from the belief
(unbiased for sampled beliefs whatever the number of samples, so it compares the 4-8 logged worlds
fairly with 64-sample or exact distributions). Moves: candidates ranked by P(move in set), revealed moves excluded;
k = number of hidden true moves; recall@k = |top-k ∩ hidden| / k; set = top-k equals the hidden
moves exactly; recall@4 also reported.
Strata: turn bucket, number of revealed moves in the view; per-species macro average (mean over
species with >= --min-species scored rows of the per-species top-1 / recall@k).

  python3 tools/belief/benchmark.py --examples EX --pred usage=U --pred fp=F --pred played=P \
      --out-json out.json --out-md out.md
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from targets import iter_jsonl, species_abilities, turn_bucket  # noqa: E402

CAT = ("item", "ability", "ability_nt", "tera", "nature", "evclass", "spread")
MAIN_COLS = ("item", "ability_nt", "tera", "moves", "nature", "evclass", "spread")


class Acc:
    def __init__(self):
        self.n = 0
        self.s = defaultdict(float)

    def add(self, **kw):
        self.n += 1
        for k, v in kw.items():
            self.s[k] += v

    def out(self):
        return dict({"n": self.n}, **{k: round(v / self.n, 4) for k, v in self.s.items()}) if self.n else {"n": 0}


def score_cat(dist, true, eps):
    p = dist.get(true, 0.0)
    top = max(dist.items(), key=lambda kv: (kv[1], kv[0]))[0] if dist else None
    return {"top1": float(top == true), "ptrue": p, "nll": -math.log(max(p, eps)),
            "brier": sum(q * q for q in dist.values()) - 2 * p + 1, "zero": float(p <= 0)}


def score_moves(mv, revealed, hidden):
    ranked = [m for m, _ in sorted(mv.items(), key=lambda kv: (-kv[1], kv[0])) if m not in revealed]
    k = len(hidden)
    hs = set(hidden)
    topk = set(ranked[:k])
    return {"recall_k": len(topk & hs) / k, "set": float(topk == hs), "recall_4": len(set(ranked[:4]) & hs) / k,
            "ptrue": sum(min(1.0, mv.get(m, 0.0)) for m in hs) / k}


def nontrivial_ability(species, cache={}):
    if species not in cache:
        cache[species] = len(species_abilities(species)) > 1
    return cache[species]


def evaluate(examples, preds, eps, min_species):
    overall = {a: Acc() for a in CAT + ("moves",)}
    by_turn = defaultdict(lambda: {a: Acc() for a in CAT + ("moves",)})
    by_rev = defaultdict(lambda: {a: Acc() for a in CAT + ("moves",)})
    by_species = defaultdict(lambda: {a: Acc() for a in CAT + ("moves",)})
    for e in examples:
        p = preds[e["uid"]]
        h = e["hidden"]
        y = e["y"]
        tb = turn_bucket(e["turn"], e["team_preview"])
        nrev = str(len(e["view"].get("moves") or []))
        sp = e.get("skey") or e["species"]
        buckets = (overall, by_turn[tb], by_rev[nrev], by_species[sp])
        for attr in CAT:
            base = "ability" if attr == "ability_nt" else attr
            if base in ("item", "ability", "tera") and not h[base]:
                continue
            if attr == "ability_nt" and not nontrivial_ability(e["species"]):
                continue
            sc = score_cat(p.get(base) or {}, y[base], eps)
            for b in buckets:
                b[attr].add(**sc)
        if h["moves"]:
            revealed = set(e["view"].get("moves") or [])
            sc = score_moves(p.get("moves") or {}, revealed, h["moves"])
            for b in buckets:
                b["moves"].add(**sc)
    res = {"overall": {a: overall[a].out() for a in overall},
           "by_turn": {k: {a: v[a].out() for a in v} for k, v in sorted(by_turn.items())},
           "by_revealed_moves": {k: {a: v[a].out() for a in v} for k, v in sorted(by_rev.items())}}
    macro = {}
    for attr in CAT + ("moves",):
        key = "recall_k" if attr == "moves" else "top1"
        vals = [v[attr].s[key] / v[attr].n for v in by_species.values() if v[attr].n >= min_species]
        macro[attr] = {"species": len(vals), key: round(sum(vals) / len(vals), 4) if vals else None}
    res["species_macro"] = macro
    return res


def fmt(x, nd=3):
    return "–" if x is None else ("{:.%df}" % nd).format(x)


def markdown(results, n_rows, title):
    methods = list(results)
    lines = ["## " + title, "", "Rows (decision × alive opponent Pokémon): {}.".format(n_rows), ""]
    r0 = results[methods[0]]["overall"]
    lines.append("Scored rows per attribute: " + ", ".join("{} {}".format(a, r0[a]["n"]) for a in CAT + ("moves",)) + ".")
    lines += ["", "**Top-1 accuracy** (moves: recall@k of the unrevealed moves; set = exact top-k)", "",
              "| Method | item | ability | ability (>1 legal) | tera | moves R@k | moves set | nature | EV class | spread |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for m in methods:
        o = results[m]["overall"]
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            m, fmt(o["item"].get("top1")), fmt(o["ability"].get("top1")), fmt(o["ability_nt"].get("top1")),
            fmt(o["tera"].get("top1")), fmt(o["moves"].get("recall_k")), fmt(o["moves"].get("set")),
            fmt(o["nature"].get("top1")), fmt(o["evclass"].get("top1")), fmt(o["spread"].get("top1"))))
    lines += ["", "**Mean p(true)** (probability the belief gives the true value; moves: mean P(move in set) over the hidden moves)", "",
              "| Method | item | ability (>1) | tera | moves | nature | EV class | spread |", "|---|---|---|---|---|---|---|---|"]
    for m in methods:
        o = results[m]["overall"]
        lines.append("| {} | ".format(m) + " | ".join(fmt(o[a].get("ptrue")) for a in
                     ("item", "ability_nt", "tera", "moves", "nature", "evclass", "spread")) + " |")
    lines += ["", "**NLL** (nats, eps floor) / **Brier** / **zero-mass rate**", "",
              "| Method | item | ability (>1) | tera | nature | EV class | spread |", "|---|---|---|---|---|---|---|"]
    for m in methods:
        o = results[m]["overall"]
        lines.append("| {} | ".format(m) + " | ".join(
            "{} / {} / {}".format(fmt(o[a].get("nll"), 2), fmt(o[a].get("brier"), 3), fmt(o[a].get("zero"), 2))
            for a in ("item", "ability_nt", "tera", "nature", "evclass", "spread")) + " |")
    for strat, label in (("by_turn", "turn bucket"), ("by_revealed_moves", "revealed moves in view")):
        keys = list(results[methods[0]][strat])
        if strat == "by_turn":
            order = ["0 (preview)", "1-5", "6-10", "11-20", "21+"]
            keys = [k for k in order if k in keys]
        lines += ["", "**By {}**: item top-1 / tera top-1 / moves R@k / spread top-1".format(label), "",
                  "| Method | " + " | ".join("{} (n={})".format(k, results[methods[0]][strat][k]["item"]["n"]) for k in keys) + " |",
                  "|---|" + "---|" * len(keys)]
        for m in methods:
            cells = []
            for k in keys:
                s = results[m][strat][k]
                cells.append("{} / {} / {} / {}".format(fmt(s["item"].get("top1"), 2), fmt(s["tera"].get("top1"), 2),
                                                         fmt(s["moves"].get("recall_k"), 2), fmt(s["spread"].get("top1"), 2)))
            lines.append("| {} | ".format(m) + " | ".join(cells) + " |")
    lines += ["", "**Per-species macro average** (species with enough scored rows)", "",
              "| Method | " + " | ".join(MAIN_COLS) + " |", "|---|" + "---|" * len(MAIN_COLS)]
    for m in methods:
        mac = results[m]["species_macro"]
        lines.append("| {} | ".format(m) + " | ".join(
            "{} ({} sp.)".format(fmt(mac[a].get("recall_k" if a == "moves" else "top1")), mac[a]["species"]) for a in MAIN_COLS) + " |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--examples", required=True)
    ap.add_argument("--pred", action="append", required=True, help="name=predictions.jsonl.gz (repeatable)")
    ap.add_argument("--splits", default="test")
    ap.add_argument("--eps", type=float, default=1e-3)
    ap.add_argument("--min-species", type=int, default=30)
    ap.add_argument("--title", default="S-A benchmark")
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    args = ap.parse_args()
    splits = set(args.splits.split(","))
    preds = {}
    for spec in args.pred:
        name, path = spec.split("=", 1)
        preds[name] = {p["uid"]: p for p in iter_jsonl([path])}
    common = None
    for d in preds.values():
        common = set(d) if common is None else common & set(d)
    examples = [e for e in iter_jsonl([args.examples]) if e["split"] in splits and e["uid"] in common]
    results = {name: evaluate(examples, d, args.eps, args.min_species) for name, d in preds.items()}
    meta = {"examples": len(examples), "splits": sorted(splits), "eps": args.eps,
            "games": len({e["game"] for e in examples}), "rosters": len({e["roster"] for e in examples}),
            "decisions": len({e["duid"] for e in examples})}
    md = markdown(results, len(examples), args.title)
    md = md.replace("\n\n", "\n\nGames {games}, decisions {decisions}, held-out rosters {rosters}, eps {eps}.\n\n".format(**meta), 1)
    if args.out_json:
        Path(args.out_json).write_text(json.dumps({"meta": meta, "results": results}, indent=1))
    if args.out_md:
        Path(args.out_md).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
