#!/usr/bin/env python3
"""Train / evaluate the HardRead student on extracted decision files (tools/dataset/extract.py).

  python3 tools/student/train.py --data 'scratch_ds/student/ext/lab-*.jsonl.gz' --out scratch_ds/student/runs/main
  python3 tools/student/train.py ... --fracs 0.125,0.25,0.5,1      # learning curve (nested game subsets)
  python3 tools/student/train.py ... --eval-only path/to/best.pt    # metrics on the test split

Splits (deterministic, by hash; both sides of a game always fall in the same split):
  test   every game in which either team's roster (sorted species ids) has
         crc32(roster) % 1000 < --test-roster-permille (default 70): each test game has at
         least one roster never seen in training; metrics are also reported on the subset where
         both rosters are unseen.
  dev    remaining games with crc32(game uid) % 100 < --dev-pct (default 10): early stopping
         (on policy + search-value loss) and the choice of value head only.
  train  everything else; --frac keeps the first frac of train games in crc32("lc"+uid) order
         (nested subsets for the learning curve).

Losses (per decision; documented mix):
  L = KL(pi_search || p)                          search policy (chance-weighted visit shares)
    + 0.1 * CE(played choice)                     the action Foul Play actually played
    + 0.5 * BCE(sigmoid(v_search), root_value)    search value (soft target in [0, 1])
    + 0.5 * BCE(sigmoid(v_outcome), outcome)      game result for this side; this head reads
                                                  stop-gradient features (see model.py)
The opponent's true sets (opp_team) are never read, except to define rosters for the split.
"""
import argparse
import contextlib
import glob
import json
import math
import os
import random
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent))
import encode as E  # noqa: E402
from model import build  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("HARDREAD_STUDENT_CACHE") or (REPO / "scratch_ds" / "student" / "cache"))
W_CHOICE, W_VSEARCH, W_VOUT = 0.1, 0.5, 0.5
W_OPP = 0.5  # opponent-next-action auxiliary (--opp-weight; 0 disables the head)
INT16_KEYS = {"ent_cat", "act_kind", "act_move", "act_type", "act_target", "field_cat", "ev_cat"} | {
    "ent_bag_" + n for n, _, _ in E.ENT_BAGS}


def crc(s):
    return zlib.crc32(s.encode())


def roster(team):
    return "|".join(sorted(E.to_id(s.get("species")) for s in team or []))


# ---------------------------------------------------------------- data
N_OPP_T = 16  # sparse opponent-action target entries per decision


def opp_names(view):
    """Opponent entity order exactly as encode.py builds it (active first, then reserves)."""
    sd = view.get("opponent") or {}
    ps = [sd.get("active")] + list(sd.get("reserve") or [])
    return [E.to_id(p.get("name")) for p in ps if p][:E.N_ENT - E.N_OWN]


def opp_target(enc, view, other):
    """Sparse soft target over the opponent-action output space from the OTHER seat's record at
    the same decision point: its search policy over its own root actions, mapped to
    [move id | n_moves + move id (tera) | 2 * n_moves + opponent slot]. Unmappable mass is dropped;
    if under half the mass maps, fall back to the other seat's played choice. Label only."""
    names = opp_names(view)
    nm = enc.vocab["moves"].size

    def idx_of(a):
        if a.startswith("switch "):
            t = E.to_id(a[len("switch "):])
            j = next((i for i, n in enumerate(names) if n == t), None)
            if j is None:
                j = next((i for i, n in enumerate(names) if n and (n.startswith(t) or t.startswith(n))), None)
            return None if j is None else 2 * nm + j
        tera = a.endswith("-tera")
        mid = E.to_id(a[:-len("-tera")] if tera else a)
        return (nm if tera else 0) + enc.vocab["moves"](mid)

    acc = {}
    for a, p in (other.get("policy") or {}).items():
        i = idx_of(a)
        if i is not None and p > 0:
            acc[i] = acc.get(i, 0.0) + p
    mass = sum(acc.values())
    if mass < 0.5:
        i = idx_of(other.get("choice") or "")
        acc, mass = ({i: 1.0}, 1.0) if i is not None else ({}, 0.0)
    items = sorted(acc.items(), key=lambda kv: -kv[1])[:N_OPP_T]
    idx = np.zeros(N_OPP_T, np.int32)
    prob = np.zeros(N_OPP_T, np.float32)
    for j, (i, p) in enumerate(items):
        idx[j], prob[j] = i, p
    if prob.sum() > 0:
        prob /= prob.sum()
    return idx, prob, bool(prob.sum() > 0)


def encode_file(path, enc):
    """Encode one decisions file (+ its .games file for the public history) into arrays + targets
    (cached as .npz)."""
    import gzip
    CACHE.mkdir(parents=True, exist_ok=True)
    st = os.stat(path)
    tag = "{}.{}.{}.{}".format(Path(path).name.replace(".jsonl.gz", ""), E.ENCODER_VERSION, st.st_size, int(st.st_mtime))
    cpath = CACHE / (tag + ".npz")
    if cpath.exists():
        z = np.load(cpath, allow_pickle=False)
        return {k: z[k] for k in z.files}
    opener = gzip.open if str(path).endswith(".gz") else open
    gpath = str(path).replace(".jsonl", ".games.jsonl")
    logs = {}
    if os.path.exists(gpath):
        with opener(gpath, "rt") as f:
            for line in f:
                g = json.loads(line)
                logs[g["uid"]] = g.get("log") or []
    with opener(path, "rt") as f:
        recs = [json.loads(line) for line in f]
    by_key = {("{}/g{:04d}".format(r["run_id"], r["game"]), r["side"], r.get("turn") or 0, r.get("k") or 0): r
              for r in recs}
    canon = {}
    feats, pol, meta = [], [], {k: [] for k in ("choice", "amax", "rv", "outcome", "turn", "k", "tp", "nlegal",
                                                  "game", "roster_own", "roster_opp", "uid", "has_hist",
                                                  "opp_idx", "opp_p", "has_opp")}
    for r in recs:
        legal = E.legal_trunc(r["legal"])
        if not legal or r.get("choice") not in legal:
            continue
        gid = "{}/g{:04d}".format(r["run_id"], r["game"])
        hist = None
        if gid in logs and r.get("history_end") is not None:
            key = (gid, r["player"])
            if key not in canon:  # canonical form of every log line, once per game and seat
                canon[key] = [E.canonical_history([x], r["player"]) for x in logs[gid]]
            hist = [x[0] for x in canon[key][:r["history_end"]] if x]
        feats.append(enc.encode(r["view"], legal, r.get("turn") or 0, r.get("k") or 0, r.get("team_preview"),
                                hist, r["player"]))
        p = np.zeros(E.MAX_ACT, np.float32)
        for i, a in enumerate(legal):
            p[i] = r["policy"].get(a, 0.0)
        p /= max(p.sum(), 1e-8)
        pol.append(p)
        meta["choice"].append(legal.index(r["choice"]))
        meta["amax"].append(int(np.argmax(p)))
        meta["rv"].append(r.get("root_value") or 0.0)
        meta["outcome"].append(-1.0 if r.get("outcome") is None else float(r["outcome"]))
        meta["turn"].append(r.get("turn") or 0)
        meta["k"].append(r.get("k") or 0)
        meta["tp"].append(bool(r.get("team_preview")))
        meta["nlegal"].append(len(legal))
        meta["game"].append(gid)
        meta["roster_own"].append(roster(r.get("own_team")))   # split only, never a feature
        meta["roster_opp"].append(roster(r.get("opp_team")))   # split only, never a feature
        meta["uid"].append(r["uid"])
        meta["has_hist"].append(hist is not None)
        # Opponent-action target: only simultaneous decision points (k == 0: turn start or team preview).
        other = by_key.get((gid, "B" if r["side"] == "A" else "A", r.get("turn") or 0, 0)) if (r.get("k") or 0) == 0 else None
        if other is not None:
            oi, op, oh = opp_target(enc, r["view"], other)
        else:
            oi, op, oh = np.zeros(N_OPP_T, np.int32), np.zeros(N_OPP_T, np.float32), False
        meta["opp_idx"].append(oi)
        meta["opp_p"].append(op)
        meta["has_opp"].append(oh)
    out = E.collate(feats) if feats else {}
    for k in INT16_KEYS:
        if k in out:
            out[k] = out[k].astype(np.int16)
    out["pol"] = np.stack(pol) if pol else np.zeros((0, E.MAX_ACT), np.float32)
    for k, v in meta.items():
        out[k] = np.array(v)
    np.savez(cpath, **out)
    return out


def _encode_worker(args):
    path, state = args
    return path, encode_file(path, E.Encoder(state))


def load_data(patterns, enc, workers=6):
    files = sorted({f for p in patterns for f in glob.glob(str(p)) if not f.endswith(".games.jsonl.gz")})
    todo = [f for f in files]
    parts = {}
    if workers > 1 and len(todo) > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for path, part in ex.map(_encode_worker, [(f, enc.state) for f in todo]):
                parts[path] = part
    else:
        for f in todo:
            parts[f] = encode_file(f, enc)
    parts = [parts[f] for f in files if len(parts[f].get("pol", []))]
    data = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    return data, files


def split(data, test_permille=70, dev_pct=6):
    ro, rp, g = data["roster_own"], data["roster_opp"], data["game"]
    test_ro = {r for r in set(ro) | set(rp) if crc(r) % 1000 < test_permille}
    is_test = np.array([a in test_ro or b in test_ro for a, b in zip(ro, rp)])
    is_dev = np.array([crc(x) % 100 < dev_pct for x in g]) & ~is_test
    is_train = ~is_test & ~is_dev
    train_rosters = set(ro[is_train]) | set(rp[is_train])
    both_unseen = np.array([a not in train_rosters and b not in train_rosters for a, b in zip(ro, rp)])
    return is_train, is_dev, is_test, both_unseen


def subset_games(data, mask, frac):
    games = sorted(set(data["game"][mask]), key=lambda x: crc("lc" + x))
    keep = set(games[:max(1, int(math.ceil(frac * len(games))))])
    return mask & np.array([x in keep for x in data["game"]]), len(keep)


def batch_actions(data, idx):
    """Number of action slots needed by this batch (trims padding; the model handles any A)."""
    return max(1, int(data["nlegal"][idx].max()))


DEV_KEYS = tuple(E.KEYS) + ("pol", "choice", "rv", "outcome", "opp_idx", "opp_p", "has_opp")


def _np32(a):
    return a.astype(np.float32) if a.dtype == np.float64 else a


def put_on_device(data, device):
    """Copy the model inputs and targets to the device once (e.g. CUDA): batches are then gathered
    there instead of being built in numpy and copied every step. Numpy copies stay for metrics."""
    data["__dev__"] = {k: torch.from_numpy(np.ascontiguousarray(_np32(data[k]))).to(device) for k in DEV_KEYS}
    return data


def _index(data, idx, device):
    return torch.as_tensor(idx, device=device) if "__dev__" in data else None


def _take(data, k, idx, it, device):
    if it is not None:
        return data["__dev__"][k][it]
    return torch.from_numpy(np.ascontiguousarray(_np32(data[k][idx]))).to(device)


def to_batch(data, idx, device):
    A = batch_actions(data, idx)
    it = _index(data, idx, device)
    b = {}
    for k in E.KEYS:
        a = _take(data, k, idx, it, device)
        if k.startswith("act_"):
            a = a[:, :A]
        if k in INT16_KEYS:
            a = a.long()
        b[k] = a.contiguous()
    return b


def amp_ctx(device, bf16):
    """bf16 autocast on CUDA when asked; a no-op elsewhere (MPS/CPU stay fp32)."""
    if bf16 and device.type == "cuda":
        return torch.autocast("cuda", dtype=torch.bfloat16)
    return contextlib.nullcontext()


# ---------------------------------------------------------------- losses and metrics
def losses(pol_logits, val_logits, data, idx, device, opp_logits=None, w_opp=0.0):
    it = _index(data, idx, device)
    pi = _take(data, "pol", idx, it, device)[:, :pol_logits.shape[1]].float()
    choice = _take(data, "choice", idx, it, device).long()
    rv = _take(data, "rv", idx, it, device).float()
    out = _take(data, "outcome", idx, it, device).float()
    logp = F.log_softmax(pol_logits, -1)
    l_pol = -(pi * logp).sum(-1).mean()  # KL up to the target entropy
    l_choice = F.nll_loss(logp, choice)
    l_vs = F.binary_cross_entropy_with_logits(val_logits[:, 0], rv)
    has = (out >= 0).float()  # float masks: bool-mask reductions gave NaN on MPS (torch 2.8)
    l_vo = (F.binary_cross_entropy_with_logits(val_logits[:, 1], out.clamp(min=0), reduction="none") * has).sum() / has.sum().clamp(min=1.0)
    total = l_pol + W_CHOICE * l_choice + W_VSEARCH * l_vs + W_VOUT * l_vo
    parts = {"pol": l_pol.detach(), "choice": l_choice.detach(), "vsearch": l_vs.detach(), "vout": l_vo.detach()}
    if opp_logits is not None:
        # Dense target via scatter_add (gather-based versions returned NaN on MPS, torch 2.8).
        oi = _take(data, "opp_idx", idx, it, device).long()
        op = _take(data, "opp_p", idx, it, device).float()
        oh = _take(data, "has_opp", idx, it, device).float()
        tgt = torch.zeros_like(opp_logits).scatter_add_(1, oi, op)
        tgt = tgt * (opp_logits.detach() > -1e3).float()        # drop target mass on masked slots
        tot = tgt.sum(-1)
        ohf = oh * (tot > 0).float()
        tgt = tgt / torch.where(tot > 0, tot, torch.ones_like(tot)).unsqueeze(-1)  # (clamp gave NaN on MPS)
        row = -(tgt * F.log_softmax(opp_logits, -1)).sum(-1)
        l_opp = (row * ohf).sum() / ohf.sum().clamp(min=1.0)
        total = total + w_opp * l_opp
        parts["opp"] = l_opp.detach()
    return total, parts


@torch.no_grad()
def predict(model, data, idx, device, bs=1024, opp_stats=None, bf16=False):
    """-> (P [N, MAX_ACT], V [N, 2]); fills opp_stats (dict) with the opponent head's per-row
    top-1 hit and cross-entropy vs its target when the model has that head."""
    model.eval()
    P, V = [], []
    o_hit, o_ce = [], []
    for s in range(0, len(idx), bs):
        sl = idx[s:s + bs]
        b = to_batch(data, sl, device)
        with amp_ctx(device, bf16):
            pl, vl, ol = model(b)
        if ol is not None and opp_stats is not None:
            olp = F.log_softmax(ol, -1).cpu().numpy()
            oi, op = data["opp_idx"][sl].astype(np.int64), data["opp_p"][sl]
            o_hit.append(olp.argmax(1) == oi[np.arange(len(sl)), op.argmax(1)])
            o_ce.append(-(op * np.take_along_axis(np.maximum(olp, -30), oi, 1)).sum(1))
        p = np.zeros((len(pl), E.MAX_ACT), np.float32)
        p[:, :pl.shape[1]] = F.softmax(pl.float(), -1).cpu().numpy()
        P.append(p)
        V.append(torch.sigmoid(vl.float()).cpu().numpy())
    if o_hit and opp_stats is not None:
        has = data["has_opp"][idx]
        hit, ce = np.concatenate(o_hit), np.concatenate(o_ce)
        opp_stats.update({"n": int(has.sum()), "top1_vs_opp_search_argmax": float(hit[has].mean()),
                          "xent_vs_opp_search_policy": float(ce[has].mean())})
    return np.concatenate(P), np.concatenate(V)


def _vmetrics(pred, y):
    pred = np.clip(pred, 1e-6, 1 - 1e-6)
    return {"brier": float(np.mean((pred - y) ** 2)),
            "logloss": float(-np.mean(y * np.log(pred) + (1 - y) * np.log(1 - pred)))}


def calibration(pred, y, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    rows, ece = [], 0.0
    for i in range(bins):
        m = (pred >= edges[i]) & ((pred < edges[i + 1]) if i < bins - 1 else (pred <= 1))
        if m.sum() == 0:
            continue
        rows.append({"bin": [round(edges[i], 2), round(edges[i + 1], 2)], "n": int(m.sum()),
                     "mean_pred": round(float(pred[m].mean()), 4), "win_rate": round(float(y[m].mean()), 4)})
        ece += m.sum() / len(pred) * abs(pred[m].mean() - y[m].mean())
    return rows, float(ece)


def metrics(P, V, data, idx, value_head=None, calib=None):
    pi = data["pol"][idx]
    choice, amax, n = data["choice"][idx], data["amax"][idx], data["nlegal"][idx]
    top = P.argmax(1)
    eps = 1e-8
    kl = (pi * (np.log(pi + eps) - np.log(P + eps))).sum(1)
    kl_unif = (pi * np.log((pi + eps) * n[:, None])).sum(1)
    m = {"n": int(len(idx)), "games": int(len(set(data["game"][idx]))),
         "top1_choice": float((top == choice).mean()), "top1_search_argmax": float((top == amax).mean()),
         "kl_search": float(kl.mean()), "nll_choice": float(-np.log(P[np.arange(len(idx)), choice] + eps).mean()),
         "baseline_uniform": {"top1": float((1.0 / n).mean()), "kl_search": float(kl_unif.mean())},
         "ref_choice_eq_search_argmax": float((choice == amax).mean())}
    # Heuristic baseline: hardest-hitting move (power x type effectiveness x STAB, non-tera first);
    # decisions without a damaging move count as uniform (1/n expected agreement).
    an = data["act_num"][idx]
    j = E.ACT_NUM.index
    dmg = an[:, :, j("bp")] * np.exp2(2 * an[:, :, j("eff_log2")]) * (1 + 0.5 * an[:, :, j("stab")])
    dmg = np.where(an[:, :, j("is_move")] > 0, dmg - 1e-3 * an[:, :, j("is_tera")], -1.0)
    has_dmg = dmg.max(1) > 0
    h_top = dmg.argmax(1)
    m_h = np.where(has_dmg, (h_top == choice).astype(float), 1.0 / n)
    m_ha = np.where(has_dmg, (h_top == amax).astype(float), 1.0 / n)
    multi = n >= 2
    m["baseline_max_damage"] = {"top1_choice": float(m_h.mean()), "top1_search_argmax": float(m_ha.mean())}
    m["multi_action_only"] = {"n": int(multi.sum()), "top1_choice": float((top == choice)[multi].mean()),
                              "top1_search_argmax": float((top == amax)[multi].mean()),
                              "kl_search": float(kl[multi].mean()),
                              "ref_choice_eq_search_argmax": float((choice == amax)[multi].mean())}
    phase = np.where(data["tp"][idx], "team_preview", np.where(data["k"][idx] > 0, "extra_switch", "turn_start"))
    m["by_phase"] = {ph: {"n": int((phase == ph).sum()),
                          "top1_choice": float((top == choice)[phase == ph].mean()),
                          "top1_search_argmax": float((top == amax)[phase == ph].mean()),
                          "kl_search": float(kl[phase == ph].mean())}
                     for ph in ("team_preview", "turn_start", "extra_switch") if (phase == ph).any()}
    out = data["outcome"][idx]
    has = out >= 0
    y = out[has]
    rv = data["rv"][idx][has]
    m["value"] = {"n": int(has.sum()),
                  "outcome_head": _vmetrics(V[has, 1], y), "search_head": _vmetrics(V[has, 0], y),
                  "search_root_value": _vmetrics(rv, y), "constant_0.5": _vmetrics(np.full_like(y, 0.5), y),
                  "search_head_mse_vs_root_value": float(np.mean((V[has, 0] - rv) ** 2))}
    rows, ece = calibration(V[has, 1], y)
    m["value"]["outcome_head_calibration"] = rows
    m["value"]["outcome_head_ece"] = ece
    if value_head:  # what infer.policy_value returns: chosen head after dev Platt scaling
        pv = apply_platt(V[has, 1 if value_head == "outcome" else 0], calib)
        m["value"]["infer_value"] = dict(_vmetrics(pv, y), head=value_head, platt=calib,
                                         ece=calibration(pv, y)[1], calibration=calibration(pv, y)[0])
    m["value"]["search_root_value_ece"] = calibration(rv, y)[1]
    return m


def fit_platt(p, y, iters=50):
    """Platt scaling: (a, b) minimising log-loss of sigmoid(a * logit(p) + b) vs y (Newton)."""
    z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / np.clip(1 - p, 1e-6, 1))
    a, b = 1.0, 0.0
    for _ in range(iters):
        q = 1 / (1 + np.exp(-(a * z + b)))
        g = np.array([((q - y) * z).sum(), (q - y).sum()])
        w = q * (1 - q)
        H = np.array([[(w * z * z).sum(), (w * z).sum()], [(w * z).sum(), w.sum()]]) + 1e-6 * np.eye(2)
        step = np.linalg.solve(H, g)
        a, b = a - step[0], b - step[1]
        if np.abs(step).max() < 1e-8:
            break
    if not (np.isfinite(a) and np.isfinite(b) and 0.2 <= a <= 5.0 and abs(b) <= 3.0):
        return 1.0, 0.0  # degenerate fit (e.g. near-constant predictions): keep the raw head
    return float(a), float(b)


def apply_platt(p, ab):
    if not ab:
        return p
    z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / np.clip(1 - p, 1e-6, 1))
    return 1 / (1 + np.exp(-(ab[0] * z + ab[1])))


# ---------------------------------------------------------------- training
def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def pick_device(name):
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def train_one(data, train_mask, dev_mask, enc, args, out_dir, device, tag):
    set_seed(args.seed)
    tr = np.flatnonzero(train_mask)
    dv = np.flatnonzero(dev_mask)
    model = build(enc.sizes(), size=args.size, dropout=args.dropout, events=not args.no_events,
                  opp_head=args.opp_weight > 0).to(device)
    if args.init:  # warm start (e.g. pretrained on the F1 volume farm)
        model.load_state_dict(torch.load(args.init, map_location="cpu", weights_only=False)["model"])
    n_params = model.n_params()
    decay = [p for n, p in model.named_parameters() if p.dim() >= 2 and "emb" not in n]
    no_decay = [p for n, p in model.named_parameters() if not (p.dim() >= 2 and "emb" not in n)]
    opt = torch.optim.AdamW([{"params": decay, "weight_decay": args.wd}, {"params": no_decay, "weight_decay": 0.0}],
                            lr=args.lr, betas=(0.9, 0.98))
    spe = max(1, len(tr) // args.bs)
    epochs = args.epochs
    if args.min_steps and spe * epochs < args.min_steps:
        epochs = int(math.ceil(args.min_steps / spe))
    total = spe * epochs
    warm = min(args.warmup, total // 10 + 1)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * min(1.0, s / total)))))
    rng = np.random.default_rng(args.seed)
    best, best_state, bad, log = float("inf"), None, 0, []
    t0 = time.time()
    step = 0
    print("[{}] train {} decisions ({} games), dev {}, {} params, {} epochs x {} steps on {}".format(
        tag, len(tr), len(set(data["game"][tr])), len(dv), n_params, epochs, spe, device), flush=True)
    for ep in range(epochs):
        model.train()
        perm = rng.permutation(tr)
        agg = {}
        for s in range(spe):
            idx = perm[s * args.bs:(s + 1) * args.bs]
            b = to_batch(data, idx, device)
            with amp_ctx(device, getattr(args, "bf16", False)):
                pl, vl, ol = model(b)
            loss, parts = losses(pl, vl, data, idx, device, ol, args.opp_weight)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            step += 1
            for k, v in parts.items():
                agg[k] = agg.get(k, 0.0) + v / spe
        agg = {k: float(v) for k, v in agg.items()}
        # dev
        model.eval()
        dl = {}
        with torch.no_grad():
            for s in range(0, len(dv), 1024):
                idx = dv[s:s + 1024]
                with amp_ctx(device, getattr(args, "bf16", False)):
                    pl, vl, ol = model(to_batch(data, idx, device))
                _, parts = losses(pl, vl, data, idx, device, ol, args.opp_weight)
                for k, v in parts.items():
                    dl[k] = dl.get(k, 0.0) + float(v) * len(idx) / len(dv)
        # Early stopping on the per-decision targets only (policy + search value). The outcome
        # head sits on stop-gradient features and is reported, not used for model selection.
        dev_total = dl["pol"] + W_VSEARCH * dl["vsearch"]
        rec = {"epoch": ep + 1, "step": step, "sec": round(time.time() - t0, 1), "train": agg, "dev": dl,
               "dev_total": dev_total, "lr": sched.get_last_lr()[0]}
        log.append(rec)
        print("[{}] ep {} {:.0f}s train pol {:.4f} vout {:.4f} | dev pol {:.4f} vs {:.4f} vout {:.4f} opp {:.4f}".format(
            tag, ep + 1, rec["sec"], agg["pol"], agg["vout"], dl["pol"], dl["vsearch"], dl["vout"], dl.get("opp", float("nan"))), flush=True)
        if dev_total < best - 1e-4:
            best, bad = dev_total, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            if getattr(args, "save_every_best", False):  # checkpoint as training goes (partial: no calibration yet)
                out_dir.mkdir(parents=True, exist_ok=True)
                torch.save({"version": "hr-student-v1", "partial": True, "epoch": ep + 1, "model": best_state,
                            "config": model.config, "encoder_state": enc.state, "dev_total": best},
                           out_dir / "best.pt")
        else:
            bad += 1
            if bad >= args.patience:
                print("[{}] early stop at epoch {}".format(tag, ep + 1), flush=True)
                break
    model.load_state_dict(best_state)
    # Value head used by infer.policy_value: the one with the lower dev log-loss vs the game outcome.
    Pd, Vd = predict(model, data, dv, device, bf16=getattr(args, "bf16", False))
    od = data["outcome"][dv]
    hd = od >= 0
    ll = {name: _vmetrics(Vd[hd, j], od[hd])["logloss"] for j, name in ((0, "search"), (1, "outcome"))}
    value_head = min(ll, key=ll.get)
    # Post-hoc Platt calibration of the chosen value head on dev (2 parameters; used by infer).
    calib = fit_platt(Vd[hd, 1 if value_head == "outcome" else 0], od[hd])
    print("[{}] dev outcome log-loss: search head {:.4f}, outcome head {:.4f} -> value_head={}, platt {}".format(
        tag, ll["search"], ll["outcome"], value_head, [round(x, 3) for x in calib]), flush=True)
    ckpt = {"version": "hr-student-v1", "model": best_state, "config": model.config, "value_head": value_head,
            "value_calibration": calib,
            "dev_value_logloss": ll,
            "encoder_state": enc.state, "args": vars(args), "log": log, "n_params": n_params,
            "train_games": int(len(set(data["game"][tr]))), "train_decisions": int(len(tr)),
            "train_seconds": round(time.time() - t0, 1)}
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save(ckpt, out_dir / "best.pt")
    return model, ckpt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", nargs="+", required=True, help="decision files or globs (.jsonl.gz)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fracs", default="1", help="comma-separated train fractions (learning curve)")
    ap.add_argument("--size", default="base", choices=["base", "small", "tiny", "large"])
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--min-steps", type=int, default=1500, help="small fractions train at least this many steps")
    ap.add_argument("--patience", type=int, default=2)
    ap.add_argument("--bs", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--wd", type=float, default=0.05)
    ap.add_argument("--warmup", type=int, default=300)
    ap.add_argument("--dropout", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=20261026)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--test-roster-permille", type=int, default=70)
    ap.add_argument("--dev-pct", type=int, default=10)
    ap.add_argument("--eval-only", default=None, help="checkpoint to evaluate on the test split")
    ap.add_argument("--init", default=None, help="initialise weights from this checkpoint (same size)")
    ap.add_argument("--no-events", action="store_true", help="ablation: no recent-event tokens")
    ap.add_argument("--opp-weight", type=float, default=W_OPP, help="opponent-action auxiliary weight (0 = no head)")
    ap.add_argument("--workers", type=int, default=6, help="encoding processes")
    ap.add_argument("--bf16", action="store_true", help="bf16 autocast (CUDA only)")
    ap.add_argument("--gpu-data", action="store_true", help="keep the encoded data on the device")
    ap.add_argument("--save-every-best", action="store_true", help="write best.pt whenever dev improves")
    ap.add_argument("--encoder-state", default=None, help="JSON encoder state (default: built from Foul Play data)")
    ap.add_argument("--calibrate-only", default=None,
                    help="fit dev Platt scaling for an existing checkpoint, save it in place, report test metrics")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    device = pick_device(args.device)
    set_seed(args.seed)
    enc = E.Encoder(json.loads(Path(args.encoder_state).read_text())) if args.encoder_state else E.Encoder()
    if args.eval_only:
        ck = torch.load(args.eval_only, map_location="cpu", weights_only=False)
        enc = E.Encoder(ck["encoder_state"])
    t0 = time.time()
    data, files = load_data(args.data, enc, args.workers)
    print("loaded {} decisions from {} files in {:.0f}s".format(len(data["pol"]), len(files), time.time() - t0), flush=True)
    is_train, is_dev, is_test, both_unseen = split(data, args.test_roster_permille, args.dev_pct)
    if args.gpu_data and device.type != "cpu":
        put_on_device(data, device)
    te = np.flatnonzero(is_test)
    split_info = {"files": len(files), "decisions": int(len(data["pol"])), "games": int(len(set(data["game"]))),
                  "train_decisions": int(is_train.sum()), "train_games": int(len(set(data["game"][is_train]))),
                  "dev_decisions": int(is_dev.sum()), "test_decisions": int(is_test.sum()),
                  "test_games": int(len(set(data["game"][is_test]))),
                  "test_decisions_both_rosters_unseen": int((is_test & both_unseen).sum()),
                  "frac_with_history": float(data["has_hist"].mean()),
                  "frac_with_opp_target": float(data["has_opp"].mean())}
    print(json.dumps(split_info), flush=True)
    results = {"split": split_info, "runs": {}}
    if args.calibrate_only:
        from model import StudentNet
        ck = torch.load(args.calibrate_only, map_location="cpu", weights_only=False)
        model = StudentNet(**ck["config"]).to(device)
        model.load_state_dict(ck["model"])
        dv = np.flatnonzero(is_dev)
        Pd, Vd = predict(model, data, dv, device)
        od = data["outcome"][dv]
        hd = od >= 0
        ck["value_calibration"] = fit_platt(Vd[hd, 1 if ck["value_head"] == "outcome" else 0], od[hd])
        torch.save(ck, args.calibrate_only)
        ostats = {}
        P, V = predict(model, data, te, device, opp_stats=ostats)
        res = {"test": metrics(P, V, data, te, ck["value_head"], ck["value_calibration"]), "test_opp_head": ostats}
        sub = np.flatnonzero(is_test & both_unseen)
        res["test_both_rosters_unseen"] = metrics(P[np.isin(te, sub)], V[np.isin(te, sub)], data, sub,
                                                  ck["value_head"], ck["value_calibration"])
        (out_dir / "calibrated_eval.json").write_text(json.dumps({"split": split_info, "ckpt": args.calibrate_only,
                                                                  **res}, indent=1))
        iv = res["test"]["value"]["infer_value"]
        print("platt {} -> test value brier {:.4f} logloss {:.4f} ece {:.3f}".format(
            ck["value_calibration"], iv["brier"], iv["logloss"], iv["ece"]), flush=True)
        return
    if args.eval_only:
        from model import StudentNet
        model = StudentNet(**{k: v for k, v in ck["config"].items()}).to(device)  # noqa
        model.load_state_dict(ck["model"])
        P, V = predict(model, data, te, device)
        results["runs"]["eval"] = {"test": metrics(P, V, data, te)}
        (out_dir / "eval.json").write_text(json.dumps(results, indent=1))
        print(json.dumps(results["runs"]["eval"]["test"], indent=1)[:3000])
        return
    for frac in [float(x) for x in args.fracs.split(",")]:
        tag = "frac{:g}".format(frac)
        tr_mask, n_games = subset_games(data, is_train, frac)
        model, ck = train_one(data, tr_mask, is_dev, enc, args, out_dir / tag, device, tag)
        ostats = {}
        P, V = predict(model, data, te, device, opp_stats=ostats)
        res = {"frac": frac, "train_games": ck["train_games"], "train_decisions": ck["train_decisions"],
               "config": {"size": args.size, "events": not args.no_events, "opp_weight": args.opp_weight},
               "test_opp_head": ostats or None,
               "value_head": ck["value_head"], "dev_value_logloss": ck["dev_value_logloss"],
               "n_params": ck["n_params"], "train_seconds": ck["train_seconds"], "epochs_run": len(ck["log"]),
               "test": metrics(P, V, data, te, ck["value_head"], ck["value_calibration"])}
        sub = np.flatnonzero(is_test & both_unseen)
        if len(sub):
            res["test_both_rosters_unseen"] = metrics(P[np.isin(te, sub)], V[np.isin(te, sub)], data, sub,
                                                      ck["value_head"], ck["value_calibration"])
        results["runs"][tag] = res
        t = res["test"]
        print("[{}] TEST top1 choice {:.3f} argmax {:.3f} KL {:.3f} | outcome brier {:.4f} logloss {:.4f} ece {:.3f}".format(
            tag, t["top1_choice"], t["top1_search_argmax"], t["kl_search"], t["value"]["outcome_head"]["brier"],
            t["value"]["outcome_head"]["logloss"], t["value"]["outcome_head_ece"]), flush=True)
        (out_dir / "results.json").write_text(json.dumps(results, indent=1))
        if device.type == "mps":
            torch.mps.empty_cache()


if __name__ == "__main__":
    main()
