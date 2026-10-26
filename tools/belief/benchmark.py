#!/usr/bin/env python3
"""S-A benchmark: score belief predictions on hidden opponent attributes.

Population: belief examples of the chosen split(s) (tools/belief/targets.py: full true sets; or
tools/metamon/partial_targets.py: labels revealed by the end of a human game). Prediction files must list the
same examples in the same order (every tool in tools/belief writes them in example order); the benchmark streams
them in lockstep, so memory does not grow with the number of rows or methods.
One example = one (decision, alive opponent Pokemon); each decision counts once per Pokemon.

Scored attributes (only when hidden in the view at decision time):
  item      Foul Play's item is "unknownitem" and no removed_item
  ability   view ability None;   ability_nt: the same restricted to species with >1 legal ability
  tera      not terastallized
  moves     the true moves not yet revealed (full labels); with partial labels, the hidden moves the opponent
            revealed later (positives only)
  nature, evclass, spread   full labels only (never revealed in human games)
    nature: neutral natures merged into "serious"; evclass: the two stats with the most EVs; spread: nature|evclass
Partial labels (examples with `scored`): item/ability/tera are scored only when hidden AND revealed later, and not
`*_forced`; this measures P(revealed value | view), which is biased towards values that get revealed.

Metrics, categorical: top-1; ptrue = mean p(true) (expected accuracy of one draw; unbiased for sampled beliefs at
any sample count); NLL = -ln max(p_true, eps) (pre-registered S-A primary: item NLL with eps = 1e-4); Brier;
zero = share of rows with p_true = 0. Moves: candidates ranked by P(move in set), revealed moves excluded;
k = number of hidden labelled moves; recall_k, set (top-k equals them), recall_4, ptrue (mean P(move)).
Uncertainty: bootstrap over games (--bootstrap B): 95% percentile CI of every method's mean, and of the paired
difference to --ref (same rows, game resampling), for the headline statistics.

  python3 tools/belief/benchmark.py --examples EX --pred fp_belief=F --pred v1=V ... --eps 1e-4 \
      --bootstrap 2000 --ref fp_belief --out-json out.json --out-md out.md
"""
import argparse
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from targets import iter_jsonl, species_abilities, turn_bucket  # noqa: E402

CAT = ("item", "ability", "ability_nt", "tera", "nature", "evclass", "spread")
ATTRS = CAT + ("moves",)
HEADLINE = (("item", "nll"), ("item", "top1"), ("item", "ptrue"), ("ability_nt", "top1"), ("tera", "top1"),
            ("tera", "nll"), ("moves", "recall_k"), ("moves", "recall_4"), ("evclass", "top1"), ("spread", "top1"),
            ("spread", "nll"))
TURN_ORDER = ["0 (preview)", "1-5", "6-10", "11-20", "21+"]


def score_cat(dist, true, eps):
    p = dist.get(true, 0.0)
    top = max(dist.items(), key=lambda kv: (kv[1], kv[0]))[0] if dist else None
    return {"top1": float(top == true), "ptrue": p, "nll": -math.log(max(p, eps)),
            "brier": sum(q * q for q in dist.values()) - 2 * p + 1, "zero": float(p <= 0)}


def score_moves(mv, revealed, hidden):
    ranked = [m for m, _ in sorted(mv.items(), key=lambda kv: (-kv[1], kv[0])) if m not in revealed]
    k = len(hidden)
    hs = set(hidden)
    return {"recall_k": len(set(ranked[:k]) & hs) / k, "set": float(set(ranked[:k]) == hs),
            "recall_4": len(set(ranked[:4]) & hs) / k, "ptrue": sum(min(1.0, mv.get(m, 0.0)) for m in hs) / k}


_NT = {}


def nontrivial_ability(species):
    if species not in _NT:
        _NT[species] = len(species_abilities(species)) > 1
    return _NT[species]


def scored_attrs(e):
    """(attribute, true value) pairs scored for this example, plus the hidden labelled moves."""
    h, y = e["hidden"], e["y"]
    partial = "scored" in e
    out = []
    for attr in CAT:
        base = "ability" if attr == "ability_nt" else attr
        if base in ("item", "ability", "tera"):
            if partial:
                if not e["scored"][base] or e["ymask"].get(base + "_forced"):
                    continue
            elif not h[base]:
                continue
        elif partial:
            continue  # nature / EV spread never known in human games
        if attr == "ability_nt" and not nontrivial_ability(e["species"]):
            continue
        if y.get(base) is None:
            continue
        out.append((attr, base, y[base]))
    return out, list(h["moves"] or [])


class Stats:
    def __init__(self):
        self.n = defaultdict(int)
        self.s = defaultdict(float)

    def add(self, attr, sc):
        self.n[attr] += 1
        for k, v in sc.items():
            self.s[(attr, k)] += v

    def out(self):
        res = {}
        for attr in ATTRS:
            n = self.n.get(attr, 0)
            res[attr] = {"n": n}
            if n:
                for (a, k), v in self.s.items():
                    if a == attr:
                        res[attr][k] = round(v / n, 4)
        return res


def open_preds(path):
    return gzip.open(path, "rt") if str(path).endswith(".gz") else open(path)


def run(examples_path, splits, preds, eps, min_species):
    names = list(preds)
    files = {m: open_preds(p) for m, p in preds.items()}
    overall = {m: Stats() for m in names}
    by_turn = {m: defaultdict(Stats) for m in names}
    by_rev = {m: defaultdict(Stats) for m in names}
    by_species = {m: defaultdict(Stats) for m in names}
    # per game sums for the bootstrap: game -> [method][(attr, stat)] sum, and counts per attr
    g_sum = defaultdict(lambda: np.zeros((len(names), len(HEADLINE))))
    g_cnt = defaultdict(lambda: np.zeros(len(HEADLINE)))
    meta = {"examples": 0, "games": set(), "decisions": set(), "rosters": set()}
    for e in iter_jsonl([examples_path]):
        if splits and e["split"] not in splits:
            continue
        row = {}
        for m in names:
            line = files[m].readline()
            if not line:
                raise SystemExit("{}: ran out of predictions at example {}".format(m, e["uid"]))
            p = json.loads(line)
            while p["uid"] != e["uid"]:  # predictions may cover more rows (other splits), in the same order
                line = files[m].readline()
                if not line:
                    raise SystemExit("{}: no prediction for {} (predictions must follow the example order)".format(m, e["uid"]))
                p = json.loads(line)
            row[m] = p
        meta["examples"] += 1
        meta["games"].add(e["game"])
        meta["decisions"].add(e["duid"])
        meta["rosters"].add(e["roster"])
        tb = turn_bucket(e["turn"], e["team_preview"])
        nrev = str(len(e["view"].get("moves") or []))
        sp = e.get("skey") or e["species"]
        cats, hmoves = scored_attrs(e)
        revealed = set(e["view"].get("moves") or [])
        gs, gc = g_sum[e["game"]], g_cnt[e["game"]]
        for mi, m in enumerate(names):
            p = row[m]
            scs = []
            for attr, base, true in cats:
                scs.append((attr, score_cat(p.get(base) or {}, true, eps)))
            if hmoves:
                scs.append(("moves", score_moves(p.get("moves") or {}, revealed, hmoves)))
            for attr, sc in scs:
                for acc in (overall[m], by_turn[m][tb], by_rev[m][nrev], by_species[m][sp]):
                    acc.add(attr, sc)
                for hi, (ha, hs) in enumerate(HEADLINE):
                    if ha == attr and hs in sc:
                        gs[mi, hi] += sc[hs]
                        if mi == 0:
                            gc[hi] += 1
    res = {}
    for m in names:
        macro = {}
        for attr in ATTRS:
            key = "recall_k" if attr == "moves" else "top1"
            vals = [v.s[(attr, key)] / v.n[attr] for v in by_species[m].values() if v.n.get(attr, 0) >= min_species]
            macro[attr] = {"species": len(vals), key: round(sum(vals) / len(vals), 4) if vals else None}
        res[m] = {"overall": overall[m].out(),
                  "by_turn": {k: v.out() for k, v in sorted(by_turn[m].items())},
                  "by_revealed_moves": {k: v.out() for k, v in sorted(by_rev[m].items())},
                  "species_macro": macro}
    meta = {"examples": meta["examples"], "games": len(meta["games"]), "decisions": len(meta["decisions"]),
            "rosters": len(meta["rosters"]), "eps": eps}
    games = sorted(g_sum)
    S = np.stack([g_sum[g] for g in games]) if games else np.zeros((0, len(names), len(HEADLINE)))
    C = np.stack([g_cnt[g] for g in games]) if games else np.zeros((0, len(HEADLINE)))
    return names, res, meta, S, C


def bootstrap(names, S, C, B, ref, seed=0):
    """Percentile 95% CIs over games for each method's mean and the paired difference to `ref`."""
    G = S.shape[0]
    if not B or G < 2:
        return {}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, G, size=(B, G))
    cnt = C[idx].sum(1)  # B x H
    out = {}
    ri = names.index(ref) if ref in names else None
    for mi, m in enumerate(names):
        tot = S[idx, mi, :].sum(1)  # B x H
        means = tot / np.maximum(cnt, 1)
        d = {}
        for hi, (ha, hs) in enumerate(HEADLINE):
            if C[:, hi].sum() == 0:
                continue
            point = S[:, mi, hi].sum() / C[:, hi].sum()
            lo, hi_ = np.percentile(means[:, hi], [2.5, 97.5])
            entry = {"mean": round(float(point), 4), "ci": [round(float(lo), 4), round(float(hi_), 4)]}
            if ri is not None and mi != ri:
                diff_tot = (S[idx, mi, hi] - S[idx, ri, hi]).sum(1) / np.maximum(cnt[:, hi], 1)
                dpoint = (S[:, mi, hi].sum() - S[:, ri, hi].sum()) / C[:, hi].sum()
                dlo, dhi = np.percentile(diff_tot, [2.5, 97.5])
                entry["diff_vs_ref"] = round(float(dpoint), 4)
                entry["diff_ci"] = [round(float(dlo), 4), round(float(dhi), 4)]
            d["{}.{}".format(ha, hs)] = entry
        out[m] = d
    return out


def fmt(x, nd=3):
    return "–" if x is None else ("{:.%df}" % nd).format(x)


def ci(entry, nd=3):
    if not entry:
        return "–"
    return "{} [{}, {}]".format(fmt(entry["mean"], nd), fmt(entry["ci"][0], nd), fmt(entry["ci"][1], nd))


def dci(entry, nd=3):
    if not entry or "diff_vs_ref" not in entry:
        return "(ref)"
    return "{:+.{n}f} [{:+.{n}f}, {:+.{n}f}]".format(entry["diff_vs_ref"], entry["diff_ci"][0], entry["diff_ci"][1], n=nd)


def markdown(names, results, meta, boot, ref, title, partial):
    L = ["## " + title, "",
         "Rows (decision × alive opponent Pokémon): {examples}; games {games}; decisions {decisions}; rosters {rosters}; "
         "NLL floor ε = {eps}.".format(**meta), ""]
    r0 = results[names[0]]["overall"]
    L.append("Scored rows: " + ", ".join("{} {}".format(a, r0[a]["n"]) for a in ATTRS if r0[a]["n"]) + ".")
    if boot:
        L += ["", "**Headline** (95% CI: bootstrap over games, {} resamples; Δ = paired difference to `{}` on the same rows)".format(
            meta.get("bootstrap"), ref), ""]
        cols = [("item.nll", "item NLL"), ("item.top1", "item top-1"), ("item.ptrue", "item p(true)"),
                ("ability_nt.top1", "ability (>1) top-1"), ("tera.top1", "tera top-1"),
                ("moves.recall_4" if partial else "moves.recall_k", "moves R@4" if partial else "moves R@k")]
        if not partial:
            cols += [("evclass.top1", "EV class top-1"), ("spread.top1", "spread top-1")]
        L.append("| Method | item NLL | Δ item NLL vs {} | ".format(ref) + " | ".join(c[1] for c in cols[1:]) + " |")
        L.append("|---|---|---|" + "---|" * (len(cols) - 1))
        for m in names:
            b = boot.get(m, {})
            L.append("| {} | {} | {} | ".format(m, ci(b.get("item.nll"), 3), dci(b.get("item.nll"), 3)) +
                     " | ".join(fmt((b.get(c) or {}).get("mean")) for c, _ in cols[1:]) + " |")
        L += ["", "Paired differences to `{}` (95% CI):".format(ref), "",
              "| Method | " + " | ".join(c[1] for c in cols) + " |", "|---|" + "---|" * len(cols)]
        for m in names:
            if m == ref:
                continue
            b = boot.get(m, {})
            L.append("| {} | ".format(m) + " | ".join(dci(b.get(c)) for c, _ in cols) + " |")
    attrs = ("item", "ability_nt", "tera", "moves") + (() if partial else ("nature", "evclass", "spread"))
    L += ["", "**Top-1** (moves: {}) / **mean p(true)** / **NLL**".format("recall@4" if partial else "recall@k"), "",
          "| Method | " + " | ".join(attrs) + " |", "|---|" + "---|" * len(attrs)]
    for m in names:
        o = results[m]["overall"]
        cells = []
        for a in attrs:
            if a == "moves":
                cells.append("{} / {}".format(fmt(o[a].get("recall_4" if partial else "recall_k")), fmt(o[a].get("ptrue"))))
            else:
                cells.append("{} / {} / {}".format(fmt(o[a].get("top1")), fmt(o[a].get("ptrue")), fmt(o[a].get("nll"), 2)))
        L.append("| {} | ".format(m) + " | ".join(cells) + " |")
    if not partial:
        L += ["", "**Zero-mass rate** (p(true) = 0) / **Brier**", "",
              "| Method | item | ability (>1) | tera | nature | EV class | spread |", "|---|---|---|---|---|---|---|"]
        for m in names:
            o = results[m]["overall"]
            L.append("| {} | ".format(m) + " | ".join("{} / {}".format(fmt(o[a].get("zero"), 2), fmt(o[a].get("brier"), 3))
                                                         for a in ("item", "ability_nt", "tera", "nature", "evclass", "spread")) + " |")
    for strat, label in (("by_turn", "turn bucket"), ("by_revealed_moves", "revealed moves in view")):
        keys = list(results[names[0]][strat])
        if strat == "by_turn":
            keys = [k for k in TURN_ORDER if k in keys]
        mv = "recall_4" if partial else "recall_k"
        L += ["", "**By {}**: item top-1 / item NLL / tera top-1 / moves {}".format(label, mv), "",
              "| Method | " + " | ".join("{} (n={})".format(k, results[names[0]][strat][k]["item"]["n"]) for k in keys) + " |",
              "|---|" + "---|" * len(keys)]
        for m in names:
            cells = []
            for k in keys:
                s = results[m][strat][k]
                cells.append("{} / {} / {} / {}".format(fmt(s["item"].get("top1"), 2), fmt(s["item"].get("nll"), 2),
                                                         fmt(s["tera"].get("top1"), 2), fmt(s["moves"].get(mv), 2)))
            L.append("| {} | ".format(m) + " | ".join(cells) + " |")
    L += ["", "**Per-species macro average** (species with enough scored rows)", "",
          "| Method | " + " | ".join(attrs) + " |", "|---|" + "---|" * len(attrs)]
    for m in names:
        mac = results[m]["species_macro"]
        L.append("| {} | ".format(m) + " | ".join(
            "{} ({} sp.)".format(fmt(mac[a].get("recall_k" if a == "moves" else "top1")), mac[a]["species"]) for a in attrs) + " |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--examples", required=True)
    ap.add_argument("--pred", action="append", required=True, help="name=predictions.jsonl.gz (repeatable)")
    ap.add_argument("--splits", default="test", help="comma-separated splits to score ('' = all rows)")
    ap.add_argument("--eps", type=float, default=1e-3)
    ap.add_argument("--min-species", type=int, default=30)
    ap.add_argument("--bootstrap", type=int, default=0, help="resamples over games for 95%% CIs (0 = none)")
    ap.add_argument("--ref", default=None, help="method for the paired differences (default: the first --pred)")
    ap.add_argument("--title", default="S-A benchmark")
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    args = ap.parse_args()
    splits = set(s for s in args.splits.split(",") if s)
    preds = {}
    for spec in args.pred:
        name, path = spec.split("=", 1)
        preds[name] = path
    names, results, meta, S, C = run(args.examples, splits, preds, args.eps, args.min_species)
    ref = args.ref or names[0]
    boot = bootstrap(names, S, C, args.bootstrap, ref)
    meta["bootstrap"] = args.bootstrap
    meta["ref"] = ref
    partial = False
    for e in iter_jsonl([args.examples]):
        partial = "scored" in e
        break
    md = markdown(names, results, meta, boot, ref, args.title, partial)
    if args.out_json:
        Path(args.out_json).write_text(json.dumps({"meta": meta, "bootstrap": boot, "results": results}, indent=1))
    if args.out_md:
        Path(args.out_md).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
