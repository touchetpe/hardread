"""Learned belief v0: an MLP over the bot's public view of one opponent Pokemon.

Inputs (public only; the true set is a label, never an input):
  species (battle-only formes mapped to the base forme, plus a "forme changed" flag), opponent
  teammates seen (bag), own active species, own team (bag), revealed moves (bag), view item /
  removed item / ability / tera type (categorical, "?" when unknown), terastallized, Foul Play's
  flags (can_have_choice_item, impossible items bag, impossible abilities bag), speed-range bounds
  (absolute and relative to the species' possible speed range), HP fraction, status, active flag,
  turn, team-preview flag, fainted counts.
Heads: item, ability, tera (softmax), moves (multi-label sigmoid), nature, EV class, spread (softmax).
Hard constraints at train and test time: impossible items, no choice item when Foul Play rules it
out, ability restricted to the species' legal abilities minus impossible ones.
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from targets import canon_nature, dex_entry, ev_class, species_abilities, speed_stat, spread_class, toid  # noqa: E402
from baselines import SMOGON_STATS  # noqa: E402

UNK = "<unk>"
PAD = "<pad>"
CHOICE = ("choiceband", "choicespecs", "choicescarf")
STATUSES = (None, "brn", "par", "psn", "tox", "slp", "frz")
WORLD_HEADS = ("item", "ability", "tera", "nature", "evclass", "spread", "moves")
MAXLEN = {"teammates": 6, "own_team": 6, "moves": 6, "imp_items": 24, "imp_abilities": 16, "y_moves": 4}


class Vocab:
    def __init__(self, tokens=(), specials=(PAD, UNK)):
        self.itos = list(specials)
        self.stoi = {t: i for i, t in enumerate(self.itos)}
        for t in tokens:
            self.add(t)

    def add(self, t):
        if t not in self.stoi:
            self.stoi[t] = len(self.itos)
            self.itos.append(t)

    def __len__(self):
        return len(self.itos)

    def __call__(self, t):
        return self.stoi.get(t, 1)


def build_vocabs(train_examples):
    """Vocabularies from the training split plus the Smogon usage stats (no test labels)."""
    c = {k: Counter() for k in ("species", "item", "ability", "tera", "move", "nature", "evclass", "spread")}
    for e in train_examples:
        v, y, x = e["view"], e["y"], e["ctx"]
        c["species"].update([e["skey"]] + [toid(n) for n in x["teammates"] + x["own_team"]] + [toid(x["own_active"])])
        c["item"].update([y["item"], toid(v.get("item")), toid(v.get("removed_item"))] + list(v.get("impossible_items") or []))
        c["ability"].update([y["ability"], toid(v.get("ability"))] + list(v.get("impossible_abilities") or []))
        c["tera"].update([y["tera"]])
        c["move"].update(y["moves"] + [toid(m) for m in v.get("moves") or []])
        c["nature"].update([y["nature"]])
        c["evclass"].update([y["evclass"]])
        c["spread"].update([y["spread"]])
    stats = json.loads(Path(SMOGON_STATS).read_text())
    for name, ent in stats.items():
        c["species"][toid(name)] += 0
        for k in ent["Items"]:
            c["item"][toid(k) if toid(k) not in ("", "nothing") else "none"] += 0
        for k in ent["Abilities"]:
            c["ability"][toid(k)] += 0
        for k in ent["Moves"]:
            if toid(k):
                c["move"][toid(k)] += 0
        for k in ent.get("Tera Types", {}):
            c["tera"][toid(k)] += 0
    voc = {}
    for k, cnt in c.items():
        toks = sorted(t for t in cnt if t)
        if k == "spread":  # only spread classes seen at least 3 times in training get their own class
            toks = sorted(t for t, n in cnt.items() if t and n >= 3)
        voc[k] = Vocab(toks + (["unknownitem", "none"] if k == "item" else []))
    voc["ability"].add("?")
    voc["tera"].add("?")
    return voc


def _pad(ids, n):
    ids = list(ids)[:n]
    return ids + [0] * (n - len(ids))


def featurize(examples, voc):
    N = len(examples)
    f = {k: np.zeros((N, MAXLEN[k]), np.int64) for k in ("teammates", "own_team", "moves", "imp_items", "imp_abilities", "y_moves")}
    for k in ("species", "own_active", "item_view", "removed_item", "ability_view", "tera_view",
              "y_item", "y_ability", "y_tera", "y_nature", "y_evclass", "y_spread"):
        f[k] = np.zeros(N, np.int64)
    for k in ("m_item", "m_ability", "m_tera"):
        f[k] = np.zeros(N, np.bool_)
    dense = np.zeros((N, 24), np.float32)
    spe_cache = {}
    for i, e in enumerate(examples):
        v, x, y, h = e["view"], e["ctx"], e["y"], e["hidden"]
        f["species"][i] = voc["species"](e["skey"])
        f["teammates"][i] = _pad([voc["species"](toid(n)) for n in x["teammates"]], MAXLEN["teammates"])
        f["own_team"][i] = _pad([voc["species"](toid(n)) for n in x["own_team"]], MAXLEN["own_team"])
        f["own_active"][i] = voc["species"](toid(x["own_active"])) if x["own_active"] else 0
        f["moves"][i] = _pad([voc["move"](toid(m)) for m in v.get("moves") or []], MAXLEN["moves"])
        f["item_view"][i] = voc["item"](toid(v.get("item")) if v.get("item") else "none")
        f["removed_item"][i] = voc["item"](toid(v["removed_item"])) if v.get("removed_item") else 0
        f["ability_view"][i] = voc["ability"](toid(v["ability"])) if v.get("ability") else voc["ability"]("?")
        f["tera_view"][i] = voc["tera"](toid(v["tera_type"])) if v.get("terastallized") and v.get("tera_type") else voc["tera"]("?")
        f["imp_items"][i] = _pad([voc["item"](t) for t in v.get("impossible_items") or []], MAXLEN["imp_items"])
        f["imp_abilities"][i] = _pad([voc["ability"](t) for t in v.get("impossible_abilities") or []], MAXLEN["imp_abilities"])
        sp = e["species"]
        if sp not in spe_cache:
            lo_s = speed_stat(sp, "brave", [0] * 6) or 100
            hi_s = speed_stat(sp, "jolly", [0, 0, 0, 0, 0, 252]) or 300
            spe_cache[sp] = (lo_s, hi_s)
        lo_s, hi_s = spe_cache[sp]
        sr = v.get("speed_range") or [0, None]
        lo = sr[0] or 0
        hi = sr[1]
        span = max(1.0, hi_s - lo_s)
        hp = (v.get("hp") or 0) / (v.get("max_hp") or 1)
        st = v.get("status")
        d = [hp, float(e["active"]), float(bool(v.get("terastallized"))), float(v.get("can_have_choice_item") is not False),
             lo / 600.0, float(lo > 0), (hi or 0) / 600.0, float(hi is not None),
             max(-1.0, min(3.0, (lo - lo_s) / span)) if lo > 0 else 0.0,
             max(-1.0, min(3.0, (hi - lo_s) / span)) if hi is not None else 0.0,
             min(e["turn"] or 0, 60) / 60.0, float(e["team_preview"]),
             x["n_opp_fainted"] / 6.0, x["n_own_fainted"] / 6.0,
             float(e["species"] != e["skey"]), len(v.get("moves") or []) / 4.0, float(bool(v.get("removed_item"))),
             (v.get("level") or 100) / 100.0]
        d += [float(st == s) for s in STATUSES[1:]][:6]
        dense[i, :len(d)] = d
        f["y_item"][i] = voc["item"](y["item"])
        f["y_ability"][i] = voc["ability"](y["ability"])
        f["y_tera"][i] = voc["tera"](y["tera"])
        f["y_nature"][i] = voc["nature"](y["nature"])
        f["y_evclass"][i] = voc["evclass"](y["evclass"])
        f["y_spread"][i] = voc["spread"](y["spread"])
        f["y_moves"][i] = _pad([voc["move"](m) for m in y["moves"]], MAXLEN["y_moves"])
        f["m_item"][i] = h["item"]
        f["m_ability"][i] = h["ability"]
        f["m_tera"][i] = h["tera"]
    f["dense"] = dense
    # the decision's logged worlds (Foul Play's in-game belief, public-information derived): per head,
    # the world values as vocab ids + chance weights; used only by the "+worlds" variant
    W = 8
    for k in WORLD_HEADS:
        f["wi_" + k] = np.zeros((N, W * (4 if k == "moves" else 1)), np.int64)
        f["ww_" + k] = np.zeros((N, W * (4 if k == "moves" else 1)), np.float32)
    for i, e in enumerate(examples):
        if e.get("fpb"):
            # test-time alternative: Foul Play's own belief histogram (decision log v3, 64 pruned draws) instead of the
            # 4-8 worlds; each head gets its top values with their probabilities (moves: marginal P(move in set))
            for k, dist in fpb_dists(e["fpb"]).items():
                top = sorted(dist.items(), key=lambda kv: -kv[1])[:f["wi_" + k].shape[1]]
                for j, (t, pr) in enumerate(top):
                    f["wi_" + k][i, j] = voc["move" if k == "moves" else k](t)
                    f["ww_" + k][i, j] = pr
            continue
        worlds = e.get("played") or []
        tot = sum(c for c, _ in worlds) or 1.0
        for j, (c, ws) in enumerate(worlds[:W]):
            c = c / tot
            evs = ws.get("evs") or [0] * 6
            vals = {"item": toid(ws.get("item")) or "none", "ability": toid(ws.get("ability")) or "?",
                    "tera": toid(ws.get("tera")) or "?", "nature": canon_nature(ws.get("nature")),
                    "evclass": ev_class(evs), "spread": spread_class(ws.get("nature"), evs)}
            for k, t in vals.items():
                f["wi_" + k][i, j] = voc[k](t)
                f["ww_" + k][i, j] = c
            for q, m in enumerate((ws.get("moves") or [])[:4]):
                f["wi_moves"][i, j * 4 + q] = voc["move"](toid(m))
                f["ww_moves"][i, j * 4 + q] = c
    # v1: partial labels (human rows from tools/metamon/partial_targets.py carry `scored`/`ymask`).
    # Farm rows keep the full-label masks (hidden attributes; all 4 moves; spread heads). Human rows: item/ability/tera
    # only where hidden AND revealed later (and not *_forced), no spread heads, the 4-move BCE only when all 4 moves
    # were revealed, and a positive-only ranking target (y_pos) for the revealed hidden moves.
    src_h = np.array(["scored" in e for e in examples], np.bool_)
    f["src_h"] = src_h
    f["m_spread"] = ~src_h
    f["m_moves_complete"] = np.ones(N, np.bool_)
    f["y_pos"] = np.zeros((N, 4), np.int64)
    for i, e in enumerate(examples):
        if not src_h[i]:
            continue
        sc, ym = e["scored"], e["ymask"]
        f["m_item"][i] = bool(sc["item"]) and not ym.get("item_forced")
        f["m_ability"][i] = bool(sc["ability"]) and not ym.get("ability_forced")
        f["m_tera"][i] = bool(sc["tera"])
        f["m_moves_complete"][i] = bool(ym.get("moves_complete"))
        ids = [voc["move"](toid(m)) for m in e["hidden"]["moves"]][:4]
        f["y_pos"][i, :len(ids)] = ids
    # dedup weight: 1 / number of examples of the same opponent Pokemon in the same game and side
    cnt = Counter((e["game"], e["side"], e["skey"]) for e in examples)
    f["w"] = np.array([1.0 / cnt[(e["game"], e["side"], e["skey"])] for e in examples], np.float32)
    f["w"] *= len(examples) / max(1e-9, f["w"].sum())
    return f


def fpb_dists(h):
    """fp_belief histogram -> per-head distributions over the model's value strings."""
    n = h.get("n") or 0
    out = {k: Counter() for k in WORLD_HEADS}
    if not n:
        return {}
    for k, c in h.get("item", {}).items():
        out["item"]["none" if k in ("None", "", "none") else toid(k)] += c / n
    for k, c in h.get("ability", {}).items():
        out["ability"]["?" if k in ("None", "") else toid(k)] += c / n
    for k, c in h.get("tera", {}).items():
        out["tera"]["?" if k in ("None", "") else toid(k)] += c / n
    for k, c in h.get("spread", {}).items():
        nat, _, evs = k.partition("|")
        try:
            evs = [int(float(x)) for x in evs.split(",")] if evs else [0] * 6
        except ValueError:
            evs = [0] * 6
        out["nature"][canon_nature(nat)] += c / n
        out["evclass"][ev_class(evs)] += c / n
        out["spread"][spread_class(nat, evs)] += c / n
    for m, c in h.get("moves", {}).items():
        out["moves"][toid(m)] += min(1.0, c / n)
    return out


def legal_ability_matrix(voc):
    """[species, ability] bool: the species' legal abilities (pokedex); all True when unknown."""
    m = np.zeros((len(voc["species"]), len(voc["ability"])), np.bool_)
    for s, i in voc["species"].stoi.items():
        abil = [voc["ability"].stoi[a] for a in species_abilities(s) if a in voc["ability"].stoi] if dex_entry(s) else []
        if abil:
            m[i, abil] = True
        else:
            m[i, :] = True
    return m


class BagEmb(nn.Module):
    def __init__(self, n, d):
        super().__init__()
        self.emb = nn.Embedding(n, d, padding_idx=0)

    def forward(self, idx):
        e = self.emb(idx)
        cnt = (idx > 0).sum(1, keepdim=True).clamp(min=1).float()
        return e.sum(1) / cnt


def prior_tables(voc, smooth=0.02):
    """Per-species usage-prior base logits from the Smogon stats (public data, not labels):
    log of a smoothed prior for the categorical heads, logit of the usage rate for moves.
    Species absent from the stats get flat rows. Constraints are applied later by masking, so
    softmax(prior + mask) is the constrained, renormalised usage prior."""
    from baselines import UsagePrior
    from targets import ev_class, spread_class
    up = UsagePrior()
    nS = len(voc["species"])
    T = {k: np.zeros((nS, len(voc[v])), np.float32) for k, v in
         (("item", "item"), ("ability", "ability"), ("tera", "tera"), ("nature", "nature"),
          ("evclass", "evclass"), ("spread", "spread"), ("moves", "move"))}
    nM = len(voc["move"])
    T["moves"][:] = np.log(4.0 / nM) - np.log(1 - 4.0 / nM)  # species without stats: 4 expected moves, flat
    for s, i in voc["species"].stoi.items():
        if i < 2:
            continue
        b = up.base(s) if dex_entry(s) else None
        if b is None:
            continue
        cats = {"item": b["items"], "ability": b["abilities"], "tera": b["tera"],
                "nature": Counter(), "evclass": Counter(), "spread": Counter()}
        for n, evs, c in b["spreads"]:
            cats["nature"][canon_nature(n)] += c
            cats["evclass"][ev_class(evs)] += c
            cats["spread"][spread_class(n, evs)] += c
        for k, d in cats.items():
            vk = "move" if k == "moves" else k
            row = np.zeros(len(voc[vk]), np.float64)
            for t, c in d.items():
                row[voc[vk](t)] += c
            row[:2] = 0
            if row.sum() > 0:
                row = row / row.sum()
                V = len(row) - 2
                row[2:] = (1 - smooth) * row[2:] + smooth / V
                T[k][i, 2:] = np.log(row[2:])
                T[k][i, :2] = np.log(smooth / V)
        mv = np.full(len(voc["move"]), 1e-3)
        for t, pr in b["moves"].items():
            mv[voc["move"](t)] = min(max(pr, 1e-3), 1 - 1e-3)
        T["moves"][i] = np.log(mv / (1 - mv))
    return T


class BeliefNet(nn.Module):
    def __init__(self, voc, d=64, hidden=512, dropout=0.1, n_dense=24, priors=None, worlds=False, world_alpha=0.05,
                 reveal=False):
        super().__init__()
        self.use_reveal = reveal
        if reveal:
            # v1 reveal-propensity correction: human labels are values that GOT REVEALED, so for human rows
            # P(label = v) ∝ P(v | view) · r(v); log r(v) is a learned per-class vector added to the logits of human
            # rows only (farm rows, with true labels, anchor P(v | view)). Predictions of true sets omit it.
            self.reveal_bias = nn.ParameterDict({k: nn.Parameter(torch.zeros(len(voc[v]))) for k, v in
                                                 (("item", "item"), ("ability", "ability"), ("tera", "tera"), ("moves", "move"))})
        self.use_worlds = worlds
        self.world_alpha = world_alpha
        if worlds:  # + log of the (smoothed) in-game world distribution, learned scale per head
            self.world_scale = nn.ParameterDict({k: nn.Parameter(torch.full((1,), 0.5)) for k in WORLD_HEADS})
        nS, nI, nA, nT, nM = (len(voc[k]) for k in ("species", "item", "ability", "tera", "move"))
        self.species = nn.Embedding(nS, d * 2)
        self.teammates = BagEmb(nS, d)
        self.own_active = nn.Embedding(nS, d)
        self.own_team = BagEmb(nS, d)
        self.moves = BagEmb(nM, d)
        self.item_view = nn.Embedding(nI, d // 2)
        self.removed_item = nn.Embedding(nI, d // 2)
        self.ability_view = nn.Embedding(nA, d // 2)
        self.tera_view = nn.Embedding(nT, d // 4)
        self.imp_items = BagEmb(nI, d // 2)
        self.imp_abilities = BagEmb(nA, d // 2)
        n_in = d * 2 + d * 4 + (d // 2) * 5 + d // 4 + n_dense
        self.trunk = nn.Sequential(nn.Linear(n_in, hidden), nn.GELU(), nn.Dropout(dropout),
                                   nn.Linear(hidden, hidden), nn.GELU(), nn.Dropout(dropout))
        self.heads = nn.ModuleDict({
            "item": nn.Linear(hidden, nI), "ability": nn.Linear(hidden, nA), "tera": nn.Linear(hidden, nT),
            "moves": nn.Linear(hidden, nM), "nature": nn.Linear(hidden, len(voc["nature"])),
            "evclass": nn.Linear(hidden, len(voc["evclass"])), "spread": nn.Linear(hidden, len(voc["spread"]))})
        self.use_prior = priors is not None
        if self.use_prior:
            # residual on the usage prior: heads start at zero, so the untrained net IS the usage prior
            for k, t in priors.items():
                self.register_buffer("prior_" + k, torch.from_numpy(t))
            self.prior_scale = nn.ParameterDict({k: nn.Parameter(torch.ones(1)) for k in priors})
            for head in self.heads.values():
                nn.init.zeros_(head.weight)
                nn.init.zeros_(head.bias)

    def forward(self, b):
        h = torch.cat([self.species(b["species"]), self.teammates(b["teammates"]), self.own_active(b["own_active"]),
                       self.own_team(b["own_team"]), self.moves(b["moves"]), self.item_view(b["item_view"]),
                       self.removed_item(b["removed_item"]), self.ability_view(b["ability_view"]),
                       self.tera_view(b["tera_view"]), self.imp_items(b["imp_items"]),
                       self.imp_abilities(b["imp_abilities"]), b["dense"]], dim=1)
        z = self.trunk(h)
        out = {k: head(z) for k, head in self.heads.items()}
        if self.use_prior:
            for k in out:
                out[k] = out[k] + self.prior_scale[k] * getattr(self, "prior_" + k)[b["species"]]
        if self.use_worlds:
            a = self.world_alpha
            for k in WORLD_HEADS:
                dist = torch.zeros_like(out[k]).scatter_add_(1, b["wi_" + k], b["ww_" + k])
                if k == "moves":
                    dist = dist.clamp(0, 1)
                    out[k] = out[k] + self.world_scale[k] * torch.log((dist + a) / (1 - dist + a))
                else:
                    out[k] = out[k] + self.world_scale[k] * torch.log(dist + a)
        return out


NEG = -30.0  # finite: a wrong hard constraint costs ~30 nats instead of inf


def constrain(logits, b, legal_ab, choice_ids):
    """Apply the hard constraints: impossible items / no choice item; legal minus impossible abilities."""
    li = logits["item"].clone()
    li.scatter_(1, b["imp_items"], NEG)
    no_choice = b["dense"][:, 3] < 0.5
    if no_choice.any():
        li[no_choice.nonzero(as_tuple=True)[0].unsqueeze(1), choice_ids.unsqueeze(0)] = NEG
    li[:, 0] = NEG  # pad
    la = logits["ability"].clone()
    la = la.masked_fill(~legal_ab[b["species"]], NEG)
    la.scatter_(1, b["imp_abilities"], NEG)
    la[:, 0] = NEG
    # never mask the label out entirely (a constraint can be wrong); restore the true class' raw logit in training
    if "y_item" in b and b.get("_train", False):
        idx = torch.arange(li.shape[0], device=li.device)
        li[idx, b["y_item"]] = logits["item"][idx, b["y_item"]]
        la[idx, b["y_ability"]] = logits["ability"][idx, b["y_ability"]]
    out = dict(logits)
    out["item"], out["ability"] = li, la
    return out


def losses(out, b, n_moves, weight=None, reveal_bias=None, rank_weight=0.25):
    """Mean losses per head (weighted when `weight` is given).

    Farm rows (full labels): item/ability/tera CE where hidden, nature/EV class/spread CE, 4-move BCE: exactly v0.
    Human rows (partial labels, b["src_h"]): CE only where the label is known (masks from featurize), plus the reveal
    bias on their logits when `reveal_bias` is given; no spread heads; the move BCE only on rows whose 4 moves are all
    known, and a positive-only listwise term on the others: -mean over revealed hidden moves of log softmax(logits)."""
    B = out["item"].shape[0]
    dev = out["item"].device
    w = torch.ones(B, device=dev) if weight is None else weight
    hm = b.get("src_h")
    hmf = hm.float().unsqueeze(1) if hm is not None else None

    def logits(k):
        if reveal_bias is not None and hm is not None and k in reveal_bias:
            return out[k] + hmf * reveal_bias[k].unsqueeze(0)
        return out[k]

    def wmean(v, m):
        return (v * w[m]).sum() / w[m].sum().clamp(min=1e-9)

    L = {}
    for k in ("item", "ability", "tera"):
        m = b["m_" + k]
        if m.any():
            L[k] = wmean(F.cross_entropy(logits(k)[m], b["y_" + k][m], reduction="none"), m)
        else:
            L[k] = out[k].sum() * 0
    ms = b.get("m_spread")
    for k in ("nature", "evclass", "spread"):
        if ms is None:
            L[k] = (F.cross_entropy(out[k], b["y_" + k], reduction="none") * w).sum() / w.sum()
        elif ms.any():
            L[k] = wmean(F.cross_entropy(out[k][ms], b["y_" + k][ms], reduction="none"), ms)
        else:
            L[k] = out[k].sum() * 0
    mc = b.get("m_moves_complete")
    if mc is None:
        mc = torch.ones(B, dtype=torch.bool, device=dev)
    if mc.any():
        tgt = torch.zeros(out["moves"][mc].shape, device=dev)
        tgt.scatter_(1, b["y_moves"][mc], 1.0)
        tgt[:, 0] = 0.0
        bce = F.binary_cross_entropy_with_logits(out["moves"][mc], tgt, reduction="none").sum(1) / 4.0
        L["moves"] = wmean(bce, mc)
    else:
        L["moves"] = out["moves"].sum() * 0
    if "y_pos" in b:
        mp = (b["y_pos"] > 0).any(1) & ~mc
        if mp.any():
            logp = F.log_softmax(logits("moves")[mp], dim=1)
            pos = b["y_pos"][mp]
            valid = (pos > 0).float()
            nll = -(logp.gather(1, pos) * valid).sum(1) / valid.sum(1).clamp(min=1)
            L["moves_rank"] = rank_weight * wmean(nll, mp)
    return L


def to_pred_dicts(out, voc, examples, min_p=5e-5, top_moves=48, reveal_bias=None):
    """Convert constrained logits to the benchmark's prediction format.

    Categorical heads keep every class with p >= min_p (5e-5, below the pre-registered 1e-4 NLL floor, so item NLL is
    exact); moves keep the top 48. With `reveal_bias` the item/ability/tera/moves logits get the learned reveal
    propensity added: the prediction is then P(value that will be revealed | view), the target of human-game labels."""
    out = dict(out)
    if reveal_bias is not None:
        for k, v in reveal_bias.items():
            out[k] = out[k] + v.detach().to(out[k].device).unsqueeze(0)
    probs = {k: F.softmax(out[k].float(), dim=1).cpu().numpy() for k in ("item", "ability", "tera", "nature", "evclass", "spread")}
    pm = torch.sigmoid(out["moves"].float()).cpu().numpy()
    preds = []
    for i, e in enumerate(examples):
        p = {"uid": e["uid"], "n": 0}
        for k in ("item", "ability", "tera", "nature", "evclass", "spread"):
            row = probs[k][i]
            idx = np.nonzero(row >= min_p)[0]
            idx = idx[np.argsort(-row[idx])]
            p[k] = {voc[k].itos[j]: round(float(row[j]), 6) for j in idx if j > 1}
        revealed = {toid(m) for m in e["view"].get("moves") or []}
        idx = np.argsort(-pm[i])[: top_moves + 8]
        p["moves"] = {voc["move"].itos[j]: round(float(pm[i][j]), 6) for j in idx
                      if j > 1 and voc["move"].itos[j] not in revealed}
        preds.append(p)
    return preds
