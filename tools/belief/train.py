#!/usr/bin/env python3
"""Train learned belief v0 (tools/belief/model.py) and write test predictions in benchmark format.

Splits come from the examples (targets.py: hashed by opponent roster): train fits, dev selects the
epoch (early stopping on the summed dev loss), test is only predicted. A learning curve retrains on a
fraction of the training GAMES (hash-selected, nested: the 10% games are inside the 30%).

  python3 tools/belief/train.py --examples scratch_ds/belief/lab.examples.jsonl.gz \
      --out-dir scratch_ds/belief/v0 --fractions 0.1,0.3,1.0
writes <out-dir>/pred_f<frac>.jsonl.gz, model_f1.0.pt (+ vocab) and train_summary.json.
"""
import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from targets import iter_jsonl, open_text  # noqa: E402
from model import BeliefNet, build_vocabs, constrain, featurize, legal_ability_matrix, losses, prior_tables, to_pred_dicts  # noqa: E402


def game_frac(game):
    return int(hashlib.sha1(("frac:" + game).encode()).hexdigest()[:8], 16) / 16 ** 8


def to_dev(f, idx, device):
    """Batch view: f holds full tensors already on the device (moved once), idx is a numpy index array."""
    it = torch.from_numpy(np.asarray(idx)).to(device)
    return {k: v.index_select(0, it) for k, v in f.items()}


def tensors(f, device):
    return {k: torch.from_numpy(v).to(device) for k, v in f.items()}


def run_eval(net, f, voc, legal, choice_ids, device, bs=4096):
    net.eval()
    tot, n = {}, 0
    outs = []
    with torch.no_grad():
        for s in range(0, len(f["species"]), bs):
            idx = np.arange(s, min(s + bs, len(f["species"])))
            b = to_dev(f, idx, device)
            out = constrain(net(b), b, legal, choice_ids)
            L = losses(out, b, len(voc["move"]))
            for k, v in L.items():
                tot[k] = tot.get(k, 0.0) + float(v) * len(idx)
            n += len(idx)
            outs.append({k: v.cpu() for k, v in out.items()})
    merged = {k: torch.cat([o[k] for o in outs]) for k in outs[0]}
    return {k: v / n for k, v in tot.items()}, merged


def train_one(train_ex, dev_ex, test_ex, args, device, tag):
    t0 = time.time()
    voc = build_vocabs(train_ex)
    ftr, fdev, fte = featurize(train_ex, voc), featurize(dev_ex, voc), featurize(test_ex, voc)
    for k in [x for x in args.drop_inputs.split(",") if x]:  # ablation: blank an input everywhere
        for f in (ftr, fdev, fte):
            f[k][:] = 0
    ftr, fdev, fte = tensors(ftr, device), tensors(fdev, device), tensors(fte, device)
    legal = torch.from_numpy(legal_ability_matrix(voc)).to(device)
    choice_ids = torch.tensor([voc["item"](c) for c in ("choiceband", "choicespecs", "choicescarf")], device=device)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    priors = prior_tables(voc) if args.prior else None
    net = BeliefNet(voc, d=args.dim, hidden=args.hidden, dropout=args.dropout, priors=priors,
                    worlds=bool(args.worlds)).to(device)
    if args.prior and not args.worlds:  # dev loss of the untrained net = constrained usage prior
        dl0, _ = run_eval(net, fdev, voc, legal, choice_ids, device)
        print("[{}] prior-only dev {:.4f} {}".format(tag, sum(dl0.values()), {k: round(v, 3) for k, v in dl0.items()}),
              file=sys.stderr, flush=True)
    opt = torch.optim.AdamW(net.parameters(), lr=args.lr, weight_decay=args.wd)
    N = len(ftr["species"])
    steps_per_epoch = max(1, N // args.batch)
    total_steps = steps_per_epoch * args.epochs
    eval_every = max(20, min(steps_per_epoch // 2, args.eval_every))
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda st: min(1.0, (st + 1) / 100))
    if args.prior and not args.worlds:
        best, best_step = sum(dl0.values()), 0
    else:
        best, best_step = float("inf"), -1
    best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
    bad, step, hist = 0, 0, []
    stop = False
    while step < total_steps and not stop:
        net.train()
        perm = np.random.permutation(N)
        for s in range(steps_per_epoch):
            idx = perm[s * args.batch:(s + 1) * args.batch]
            b = to_dev(ftr, idx, device)
            b["_train"] = True
            out = constrain(net(b), b, legal, choice_ids)
            L = losses(out, b, len(voc["move"]), weight=b["w"] if args.dedup_weight else None)
            loss = sum(L.values())
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
            opt.step()
            sched.step()
            step += 1
            if step % eval_every == 0 or step == total_steps:
                dl, _ = run_eval(net, fdev, voc, legal, choice_ids, device)
                net.train()
                tot = sum(dl.values())
                hist.append({"step": step, "epoch": round(step / steps_per_epoch, 2), "dev_loss": round(tot, 4),
                             **{k: round(v, 4) for k, v in dl.items()}})
                print("[{}] step {} ep {:.2f} dev {:.4f} {}".format(tag, step, step / steps_per_epoch, tot,
                      {k: round(v, 3) for k, v in dl.items()}), file=sys.stderr, flush=True)
                if tot < best - 1e-4:
                    best, best_step, bad = tot, step, 0
                    best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
                else:
                    bad += 1
                    if bad >= args.patience:
                        stop = True
                        break
            if step >= total_steps:
                break
    best_epoch = round(best_step / steps_per_epoch, 2)
    net.load_state_dict(best_state)
    _, out = run_eval(net, fte, voc, legal, choice_ids, device)
    preds = to_pred_dicts(out, voc, test_ex)
    info = {"tag": tag, "train_examples": len(train_ex), "train_games": len({e["game"] for e in train_ex}),
            "train_rosters": len({e["roster"] for e in train_ex}), "dev_examples": len(dev_ex),
            "test_examples": len(test_ex), "best_epoch": best_epoch, "best_dev_loss": round(best, 4),
            "params": sum(p.numel() for p in net.parameters()), "seconds": round(time.time() - t0, 1),
            "vocab": {k: len(v) for k, v in voc.items()}, "history": hist}
    return net, voc, preds, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--examples", required=True)
    ap.add_argument("--extra-train", nargs="*", default=[], help="additional example files used for TRAINING only "
                    "(their train split; e.g. the F1 volume farm)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--fractions", default="0.1,0.3,1.0")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--patience", type=int, default=6, help="evaluations without dev improvement before stopping")
    ap.add_argument("--eval-every", type=int, default=250, help="steps between dev evaluations (at most one epoch)")
    ap.add_argument("--dedup-weight", type=int, default=1, help="1: weight each example by 1/(decisions showing the same "
                    "opponent Pokemon in the same game and side), so every (game, side, Pokemon) weighs the same")
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--wd", type=float, default=0.1)
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--hidden", type=int, default=512)
    ap.add_argument("--dropout", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cpu", help="cpu is ~5x faster than mps for this small net (per-step overhead)")
    ap.add_argument("--tag", default="")
    ap.add_argument("--drop-inputs", default="teammates,own_team,own_active",
                    help="comma-separated input arrays to zero (default: the team-context inputs, which let the net "
                    "memorise training rosters: dropping them lowered dev loss 6.93 -> 6.85 at 811 games); '' keeps all")
    ap.add_argument("--worlds", type=int, default=0, help="1: also feed the decision's logged worlds (Foul Play's "
                    "in-game belief) as additive log-probabilities (variant v0+worlds)")
    ap.add_argument("--prior", type=int, default=1, help="1: residual on the Smogon usage prior (default); 0: from scratch")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    allex = list(iter_jsonl([args.examples]))
    train = [e for e in allex if e["split"] == "train"]
    for path in args.extra_train:
        test_rosters = {e["roster"] for e in allex if e["split"] != "train"}
        extra = [e for e in iter_jsonl([path]) if e["split"] == "train" and e["roster"] not in test_rosters]
        print("extra train {}: {} examples".format(path, len(extra)), file=sys.stderr)
        train += extra
    dev = [e for e in allex if e["split"] == "dev"]
    test = [e for e in allex if e["split"] == "test"]
    assert not ({e["roster"] for e in train} & {e["roster"] for e in test}), "roster leak between train and test"
    device = torch.device(args.device)
    summary = {"examples": args.examples, "extra_train": args.extra_train, "device": args.device, "args": vars(args), "runs": []}
    for frac in [float(x) for x in args.fractions.split(",")]:
        tr = [e for e in train if game_frac(e["game"]) < frac] if frac < 1 else train
        tag = "f{}{}".format(frac, args.tag)
        net, voc, preds, info = train_one(tr, dev, test, args, device, tag)
        info["fraction"] = frac
        summary["runs"].append(info)
        with open_text(out_dir / "pred_{}.jsonl.gz".format(tag), "wt") as f:
            for p in preds:
                f.write(json.dumps(p, separators=(",", ":")) + "\n")
        if frac >= 1:
            torch.save({"state": net.state_dict(), "vocab": {k: v.itos for k, v in voc.items()}, "args": vars(args)},
                       out_dir / "model_{}.pt".format(tag))
        print(json.dumps({k: v for k, v in info.items() if k != "history"}), file=sys.stderr, flush=True)
        (out_dir / "train_summary{}.json".format(args.tag)).write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
