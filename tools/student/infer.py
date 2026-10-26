#!/usr/bin/env python3
"""HardRead student inference: load(path) and policy_value(view, legal, turn, k, team_preview).

    import infer
    student = infer.load("best.npz")            # or best.pt (needs torch)
    probs, value = student.policy_value(view, legal, turn, k, team_preview)
    # probs: {action: prob} over `legal`; value: P(win) for the deciding side (outcome head)
    action = student.choose(view, legal, turn, k, team_preview)   # argmax (temperature=0)

Inputs are exactly the public decision view (decision log v2 / hardread_policy.build_view),
the MCTS root menu, turn, k (index of this decision within the turn) and team_preview.

Backends: "numpy" (default for .npz: numpy only, no torch, same math as model.py) or "torch"
(.pt checkpoints, CPU). Inside Foul Play's venv (no numpy there today) either
`pip install numpy` or run the sidecar:  python3 infer.py serve --ckpt best.npz --port 18061
and POST {"view", "legal", "turn", "k", "team_preview"} to http://127.0.0.1:18061/policy_value.

CLI:
  python3 infer.py export --ckpt best.pt --out best.npz      # torch checkpoint -> numpy weights
  python3 infer.py bench  --ckpt best.npz --data decisions.jsonl.gz [--n 500] [--threads 1]
  python3 infer.py serve  --ckpt best.npz --port 18061
Future HARDREAD_POLICY=student: fp/hardread_policy.py would call
  infer.load(os.environ["HARDREAD_STUDENT_CKPT"]).choose(build_view(battle), legal_actions(battle),
  battle.turn or 0, k, battle.team_preview)    (see tools/student/fp_student.py)
"""
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import encode as E  # noqa: E402

_DEFAULT = None


def _gelu(x):
    return 0.5 * x * (1.0 + np.tanh(0.7978845608028654 * (x + 0.044715 * x * x * x)))


def _ln(x, w, b, eps=1e-5):
    mu = x.mean(-1, keepdims=True)
    var = ((x - mu) ** 2).mean(-1, keepdims=True)
    return (x - mu) / np.sqrt(var + eps) * w + b


class NumpyStudent:
    """numpy mirror of model.StudentNet.forward (eval mode)."""

    def __init__(self, weights, config):
        self.w = {k: np.asarray(v, np.float32) for k, v in weights.items()}
        self.cfg = config
        self.lin = {}
        for k in list(self.w):
            if k.endswith(".weight") and self.w[k].ndim == 2 and not k.startswith(("emb.", "act_kind", "tok_type")):
                self.lin[k[:-len(".weight")]] = (np.ascontiguousarray(self.w[k].T), self.w[k[:-len(".weight")] + ".bias"])

    def _L(self, name, x):
        W, b = self.lin[name]
        return x @ W + b

    def forward(self, b):
        from encode import BAG_TABLE, CAT_TABLE, EV_CAT, EV_TABLE, TOK_ACT, TOK_CLS, TOK_EV, TOK_FIELD, TOK_OPP, TOK_OWN
        w = self.w
        ent_cat = b["ent_cat"]
        B = ent_cat.shape[0]
        x_ent = self._L("ent_num", b["ent_num"])
        for j, name in enumerate(E.ENT_CAT):
            x_ent = x_ent + w["emb.{}.weight".format(CAT_TABLE[name])][ent_cat[:, :, j]]
        for name, _, _ in E.ENT_BAGS:
            idx = b["ent_bag_" + name]
            m = (idx > 0).astype(np.float32)
            e = w["emb.{}.weight".format(BAG_TABLE[name])][idx] * m[..., None]
            x_ent = x_ent + e.sum(2) / np.maximum(m.sum(2, keepdims=True), 1.0)
        tok = w["tok_type.weight"]
        x_ent[:, :E.N_OWN] += tok[TOK_OWN]
        x_ent[:, E.N_OWN:] += tok[TOK_OPP]
        x_field = (self._L("field_num", b["field_num"]) + w["emb.weather.weight"][b["field_cat"][:, 0]]
                   + w["emb.terrain.weight"][b["field_cat"][:, 1]] + tok[TOK_FIELD])
        x_cls = np.broadcast_to(tok[TOK_CLS], (B, tok.shape[1]))
        tgt = b["act_target"]
        gathered = np.take_along_axis(x_ent, np.maximum(tgt, 0)[..., None], axis=1) * (tgt >= 0)[..., None]
        x_act = (w["act_kind.weight"][b["act_kind"]] + w["emb.move.weight"][b["act_move"]]
                 + w["emb.ptype.weight"][b["act_type"]] + self._L("act_num", b["act_num"]) + gathered + tok[TOK_ACT])
        toks = [x_cls[:, None], x_field[:, None], x_ent]
        masks = [np.ones((B, 2), bool), b["ent_mask"]]
        if self.cfg.get("events"):
            ev_cat = b["ev_cat"]
            x_ev = self._L("ev_num", b["ev_num"]) + tok[TOK_EV]
            for j, name in enumerate(EV_CAT):
                x_ev = x_ev + w["emb.{}.weight".format(EV_TABLE[name])][ev_cat[:, :, j]]
            toks.append(x_ev)
            masks.append(b["ev_mask"])
        a0 = sum(t.shape[1] for t in toks)
        x = np.concatenate(toks + [x_act], 1)
        x = _ln(x, w["ln_in.weight"], w["ln_in.bias"])
        A = x_act.shape[1]
        keep = np.concatenate(masks + [b["act_mask"]], 1)
        neg = np.where(keep, 0.0, -1e9).astype(np.float32)[:, None, None, :]
        H = self.cfg["heads"]
        D = x.shape[-1]
        dh = D // H
        S = x.shape[1]
        for i in range(self.cfg["layers"]):
            p = "blocks.{}.".format(i)
            h = _ln(x, w[p + "ln1.weight"], w[p + "ln1.bias"])
            qkv = self._L(p + "qkv", h).reshape(B, S, 3, H, dh).transpose(2, 0, 3, 1, 4)
            q, k, v = qkv[0], qkv[1], qkv[2]
            s = q @ k.transpose(0, 1, 3, 2) / math.sqrt(dh) + neg
            s = np.exp(s - s.max(-1, keepdims=True))
            s /= s.sum(-1, keepdims=True)
            a = (s @ v).transpose(0, 2, 1, 3).reshape(B, S, D)
            x = x + self._L(p + "proj", a)
            x = x + self._L(p + "fc2", _gelu(self._L(p + "fc1", _ln(x, w[p + "ln2.weight"], w[p + "ln2.bias"]))))
        x = _ln(x, w["ln_out.weight"], w["ln_out.bias"])
        h_act = x[:, a0:a0 + A]
        pol = self._L("pol2", _gelu(self._L("pol1", h_act)))[..., 0]
        pol = np.where(b["act_mask"], pol, -1e9)
        val = np.concatenate([self._L("val2", _gelu(self._L("val1", x[:, 0]))),
                              self._L("out2", _gelu(self._L("out1", x[:, 0])))], -1)
        return pol, val


class Student:
    def __init__(self, net, encoder, backend, meta=None):
        self.net = net
        self.encoder = encoder
        self.backend = backend
        self.meta = meta or {}

    def _forward(self, batch):
        if self.backend == "numpy":
            # numpy 2.0 + Accelerate on macOS emits spurious matmul FP warnings; results match torch to 1e-7.
            with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                return self.net.forward(batch)
        import torch
        with torch.no_grad():
            tb = {k: torch.from_numpy(v) for k, v in batch.items()}
            pl, vl, _ = self.net(tb)
            return pl.numpy(), vl.numpy()

    def evaluate(self, view, legal, turn, k, team_preview, history=None, player=None):
        """-> (probs dict, outcome head P(win), search-value head).

        history: canonical public history visible at this decision (fp.hardread_history.
        canonical_history of the protocol lines, cut like extract.history_cuts; pass raw=True
        lines through encode.canonical_history first); player: "p1"/"p2". The model was trained
        with them: omitting them masks the recent-event tokens (a distribution shift)."""
        legal = E.legal_trunc(legal)
        if not legal:
            return {}, 0.5, 0.5
        batch = E.collate([self.encoder.encode(view, legal, turn or 0, k or 0, team_preview, history, player)])
        pl, vl = self._forward(batch)
        logits = pl[0, :len(legal)].astype(np.float64)
        p = np.exp(logits - logits.max())
        p /= p.sum()
        sig = 1.0 / (1.0 + np.exp(-vl[0].astype(np.float64)))
        return {a: float(x) for a, x in zip(legal, p)}, float(sig[1]), float(sig[0])

    def policy_value(self, view, legal, turn, k, team_preview, history=None, player=None):
        """-> ({action: prob}, value). value = P(win) estimate from the head chosen on the dev split
        (checkpoint meta "value_head": "outcome" or "search")."""
        probs, v_out, v_search = self.evaluate(view, legal, turn, k, team_preview, history, player)
        return probs, self._value(v_out, v_search)

    def _value(self, v_out, v_search):
        """Chosen head, then the dev Platt scaling stored in the checkpoint (if any)."""
        v = v_search if self.meta.get("value_head") == "search" else v_out
        ab = self.meta.get("value_calibration")
        if ab:
            z = math.log(min(max(v, 1e-6), 1 - 1e-6) / max(1 - v, 1e-6))
            v = 1.0 / (1.0 + math.exp(-(ab[0] * z + ab[1])))
        return v

    def choose(self, view, legal, turn, k, team_preview, history=None, player=None, temperature=0.0, rng=None):
        probs, _ = self.policy_value(view, legal, turn, k, team_preview, history, player)
        if not probs:
            return None
        acts = list(probs)
        if temperature <= 0:
            return max(acts, key=lambda a: probs[a])
        w = np.array([probs[a] for a in acts]) ** (1.0 / temperature)
        w /= w.sum()
        rng = rng or np.random.default_rng()
        return acts[int(rng.choice(len(acts), p=w))]


def load(path, backend="auto", threads=None):
    """Load a student checkpoint (.npz: numpy backend; .pt: torch backend unless backend='numpy')."""
    global _DEFAULT
    path = str(path)
    if path.endswith(".npz"):
        z = np.load(path, allow_pickle=False)
        config = json.loads(bytes(z["__config__"]).decode())
        enc_state = json.loads(bytes(z["__encoder__"]).decode())
        meta = json.loads(bytes(z["__meta__"]).decode()) if "__meta__" in z.files else {}
        weights = {k: z[k] for k in z.files if not k.startswith("__")}
        if backend == "torch":
            import torch
            from model import StudentNet
            net = StudentNet(**config)
            net.load_state_dict({k: torch.from_numpy(v) for k, v in weights.items()})
            net.eval()
            if threads:
                torch.set_num_threads(threads)
            s = Student(net, E.Encoder(enc_state), "torch", meta)
        else:
            s = Student(NumpyStudent(weights, config), E.Encoder(enc_state), "numpy", meta)
    else:
        import torch
        ck = torch.load(path, map_location="cpu", weights_only=False)
        meta = {k: ck.get(k) for k in ("version", "n_params", "train_games", "train_decisions", "value_head",
                                        "value_calibration")}
        if backend == "numpy":
            weights = {k: v.numpy() for k, v in ck["model"].items()}
            s = Student(NumpyStudent(weights, ck["config"]), E.Encoder(ck["encoder_state"]), "numpy", meta)
        else:
            from model import StudentNet
            net = StudentNet(**ck["config"])
            net.load_state_dict(ck["model"])
            net.eval()
            if threads:
                torch.set_num_threads(threads)
            s = Student(net, E.Encoder(ck["encoder_state"]), "torch", meta)
    _DEFAULT = s
    return s


def policy_value(view, legal, turn, k, team_preview, history=None, player=None, student=None):
    """Module-level helper on the last loaded student (or HARDREAD_STUDENT_CKPT)."""
    s = student or _DEFAULT or load(os.environ["HARDREAD_STUDENT_CKPT"])
    return s.policy_value(view, legal, turn, k, team_preview, history, player)


def export(pt_path, out_path):
    import torch
    ck = torch.load(pt_path, map_location="cpu", weights_only=False)
    arrs = {k: v.float().numpy() for k, v in ck["model"].items()}
    meta = {k: ck.get(k) for k in ("version", "n_params", "train_games", "train_decisions", "value_head",
                                        "value_calibration")}
    arrs["__config__"] = np.frombuffer(json.dumps(ck["config"]).encode(), np.uint8)
    arrs["__encoder__"] = np.frombuffer(json.dumps(ck["encoder_state"]).encode(), np.uint8)
    arrs["__meta__"] = np.frombuffer(json.dumps(meta).encode(), np.uint8)
    np.savez(out_path, **arrs)
    return out_path


def _bench(args):
    """Per-decision CPU latency (encode incl. recent events + forward), history from the games file."""
    import gzip
    recs = []
    with gzip.open(args.data, "rt") as f:
        for line in f:
            recs.append(json.loads(line))
            if len(recs) >= args.n:
                break
    logs = {}
    gpath = args.data.replace(".jsonl", ".games.jsonl")
    if os.path.exists(gpath):
        with gzip.open(gpath, "rt") as f:
            for line in f:
                g = json.loads(line)
                logs[g["uid"]] = g["log"]
    for r in recs:
        log = logs.get("{}/g{:04d}".format(r["run_id"], r["game"]))
        r["_hist"] = E.canonical_history(log[:r["history_end"]], r["player"]) if log else None
    out = {}
    for backend in args.backends.split(","):
        if backend == "torch":
            import torch
            torch.set_num_threads(args.threads)
        s = load(args.ckpt, backend=backend, threads=args.threads)
        args_of = lambda r: (r["view"], r["legal"], r["turn"], r["k"], r["team_preview"], r["_hist"], r["player"])  # noqa
        for r in recs[:5]:
            s.policy_value(*args_of(r))
        ts, te = [], []
        for r in recs:
            t0 = time.perf_counter()
            s.encoder.encode(r["view"], E.legal_trunc(r["legal"]), r["turn"], r["k"], r["team_preview"], r["_hist"],
                             r["player"])
            t1 = time.perf_counter()
            s.policy_value(*args_of(r))
            ts.append(time.perf_counter() - t1)
            te.append(t1 - t0)
        ts = np.array(ts) * 1000
        out[backend] = {"n": len(ts), "threads": args.threads, "with_history": sum(r["_hist"] is not None for r in recs),
                        "encode_ms_mean": round(float(np.mean(te)) * 1000, 3),
                        "total_ms_p50": round(float(np.percentile(ts, 50)), 2),
                        "total_ms_p95": round(float(np.percentile(ts, 95)), 2),
                        "total_ms_mean": round(float(ts.mean()), 2)}
    print(json.dumps(out, indent=1))


def _serve(args):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    s = load(args.ckpt)

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                probs, v_out, v_search = s.evaluate(req["view"], req["legal"], req.get("turn", 0), req.get("k", 0),
                                                    req.get("team_preview", False), req.get("history"),
                                                    req.get("player"))
                v_out = s._value(v_out, v_search)
                body = json.dumps({"policy": probs, "value": v_out, "value_search": v_search}).encode()
                self.send_response(200)
            except Exception as e:  # noqa: BLE001
                body = json.dumps({"error": repr(e)}).encode()
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    if args.port in (18000, 18002):
        raise SystemExit("ports 18000/18002 belong to other lanes")
    print("student server on 127.0.0.1:{}".format(args.port), flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("export"); a.add_argument("--ckpt", required=True); a.add_argument("--out", required=True)
    a = sub.add_parser("bench"); a.add_argument("--ckpt", required=True); a.add_argument("--data", required=True)
    a.add_argument("--n", type=int, default=500); a.add_argument("--threads", type=int, default=1)
    a.add_argument("--backends", default="numpy,torch")
    a = sub.add_parser("serve"); a.add_argument("--ckpt", required=True); a.add_argument("--port", type=int, default=18061)
    args = ap.parse_args()
    if args.cmd == "export":
        print(export(args.ckpt, args.out))
    elif args.cmd == "bench":
        _bench(args)
    else:
        _serve(args)


if __name__ == "__main__":
    main()
