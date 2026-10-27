#!/usr/bin/env python3
"""Export a belief checkpoint (train.py model_<tag>.pt) to a self-contained .npz for Foul Play's belief lever
(foul-play/fp/hardread_belief.py, numpy inference, no torch).

The npz holds every weight and buffer (float32), the vocabularies, the legal-ability matrix, the choice-item ids and
the config (dims, dropped inputs, worlds input) as a JSON string under "meta". With --check EXAMPLES it also runs the
torch model (tools/belief/model.py featurize + constrain) and the numpy model (hardread_belief featurize from the
same view dicts) on N examples and reports the largest difference of the per-head log-probabilities.

  python3 tools/belief/export_npz.py scratch_ds/belief/v1/model_f1.0_farmhuman_ft.pt \
      --out scratch_ds/belief/export/belief_v1.npz --check scratch_ds/belief/v1data/val.examples.jsonl.gz
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from model import NEG, legal_ability_matrix  # noqa: E402
from train import load_checkpoint  # noqa: E402

HEADS = ("item", "ability", "tera", "nature", "evclass", "spread", "moves")


def export(ckpt, out):
    net, voc, args = load_checkpoint(ckpt)
    arrays = {k.replace("/", "."): v.detach().cpu().numpy().astype(np.float32) for k, v in net.state_dict().items()}
    arrays["legal_ability"] = legal_ability_matrix(voc)
    meta = {
        "format": "hardread-belief-npz-v1",
        "source_checkpoint": str(ckpt),
        "dim": args.dim, "hidden": args.hidden,
        "drop_inputs": [x for x in (args.drop_inputs or "").split(",") if x],
        "worlds": bool(args.worlds), "world_alpha": getattr(net, "world_alpha", 0.05),
        "prior": bool(args.prior), "neg": NEG,
        "vocab": {k: v.itos for k, v in voc.items()},
        "choice_ids": [voc["item"](c) for c in ("choiceband", "choicespecs", "choicescarf")],
        "train_args": {k: v for k, v in vars(args).items() if isinstance(v, (int, float, str, bool, type(None), list))},
    }
    arrays["meta"] = np.array(json.dumps(meta))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, **arrays)
    return net, voc, args


def played_dists(e, HB):
    """The decision's logged worlds as per-head distributions (what model.featurize scatters for --worlds models)."""
    worlds = (e.get("played") or [])[:8]
    tot = sum(c for c, _ in worlds) or 1.0
    d = {k: {} for k in HB.WORLD_HEADS}
    for c, ws in worlds:
        c = c / tot
        evs = ws.get("evs") or [0] * 6
        vals = {"item": HB.toid(ws.get("item")) or "none", "ability": HB.toid(ws.get("ability")) or "?",
                "tera": HB.toid(ws.get("tera")) or "?", "nature": HB.canon_nature(ws.get("nature")),
                "evclass": HB.ev_class(evs), "spread": HB.spread_class(ws.get("nature"), evs)}
        for k, t in vals.items():
            d[k][t] = d[k].get(t, 0.0) + c
        for m in (ws.get("moves") or [])[:4]:
            d["moves"][HB.toid(m)] = d["moves"].get(HB.toid(m), 0.0) + c
    return d


def check(net, voc, args, npz, examples_path, n):
    from targets import iter_jsonl
    from train import constraint_tensors, prepare, run_eval
    sys.path.insert(0, str(HERE.parents[1] / "foul-play"))
    from fp import hardread_belief as HB
    model = HB.BeliefModel(npz)
    ex = []
    for e in iter_jsonl([examples_path]):
        ex.append(e)
        if len(ex) >= n:
            break
    f = prepare(ex, voc, args, "cpu")
    legal, choice_ids = constraint_tensors(voc, "cpu")
    _, out = run_eval(net, f, voc, legal, choice_ids, "cpu")
    rows = [HB.view_row(e["view"], e["ctx"], e["turn"], e["team_preview"], e["active"],
                        world_dists=played_dists(e, HB) if args.worlds else None) for e in ex]
    got = model.forward(rows)
    worst = {}
    for k in HEADS:
        t = out[k].float()
        tl = (torch.log_softmax(t, 1) if k != "moves" else torch.nn.functional.logsigmoid(t)).numpy()
        nl = got[k]
        worst[k] = float(np.max(np.abs(tl - nl)))
    return worst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("checkpoint")
    ap.add_argument("--out", required=True)
    ap.add_argument("--check", default=None, help="examples file for a torch-vs-numpy parity check")
    ap.add_argument("--n", type=int, default=2000)
    a = ap.parse_args()
    net, voc, args = export(a.checkpoint, a.out)
    print(json.dumps({"out": a.out, "bytes": Path(a.out).stat().st_size}))
    if a.check:
        print(json.dumps({"max_abs_logprob_diff": check(net, voc, args, a.out, a.check, a.n)}))


if __name__ == "__main__":
    main()
