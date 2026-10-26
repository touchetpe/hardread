#!/usr/bin/env python3
"""Train the learned belief (tools/belief/model.py): v0 (farm full labels) and v1 (farm full labels + human partial
labels with masked losses and a reveal-propensity correction), and write predictions in benchmark format.

Splits come from the examples (targets.py / partial_targets.py: hashed by opponent roster): train fits, dev selects
the step (early stopping on the summed dev loss of the FARM dev rows, i.e. true labels), test is only predicted.
A learning curve retrains on a fraction of the farm training GAMES (hash-selected, nested).

  # v0 (as committed in 4c6a2b3)
  python3 tools/belief/train.py --examples lab.examples.jsonl.gz --extra-train vol.examples.jsonl.gz --out-dir D
  # v1 farm+human, predictions on the hr_val test set (all rows) and on held-out human games (true and revealed heads)
  python3 tools/belief/train.py --examples lab.train.jsonl.gz --extra-train vol.train.jsonl.gz \
      --human human/human.train.jsonl.gz --out-dir D --fractions 1.0 --tag _v1h \
      --predict hrval=val.examples.jsonl.gz --predict-revealed human=human/human.test.jsonl.gz
writes <out-dir>/pred_<tag>.jsonl.gz (farm test split), pred_<tag>_<name>.jsonl.gz per --predict set
(+ _revealed for --predict-revealed), model_<tag>.pt (state, vocab, args) and train_summary<tag>.json.
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
from model import (BeliefNet, Vocab, build_vocabs, constrain, featurize, legal_ability_matrix, losses,  # noqa: E402
                   prior_tables, to_pred_dicts)


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


def prepare(examples, voc, args, device):
    f = featurize(examples, voc)
    for k in [x for x in args.drop_inputs.split(",") if x]:  # blank an input everywhere
        f[k][:] = 0
    return tensors(f, device)


def make_net(voc, args, device):
    priors = prior_tables(voc) if args.prior else None
    return BeliefNet(voc, d=args.dim, hidden=args.hidden, dropout=args.dropout, priors=priors,
                     worlds=bool(args.worlds), reveal=bool(getattr(args, "reveal", 0))).to(device)


def constraint_tensors(voc, device):
    legal = torch.from_numpy(legal_ability_matrix(voc)).to(device)
    choice_ids = torch.tensor([voc["item"](c) for c in ("choiceband", "choicespecs", "choicescarf")], device=device)
    return legal, choice_ids


def predict(net, voc, examples, args, device, reveal=False, chunk=50000):
    """Predictions in benchmark format, in example order (chunked to bound memory)."""
    legal, choice_ids = constraint_tensors(voc, device)
    rb = net.reveal_bias if (reveal and getattr(net, "use_reveal", False)) else None
    for s in range(0, len(examples), chunk):
        part = examples[s:s + chunk]
        f = prepare(part, voc, args, device)
        _, out = run_eval(net, f, voc, legal, choice_ids, device)
        yield from to_pred_dicts(out, voc, part, reveal_bias=rb)


def write_preds(path, preds):
    n = 0
    with open_text(path, "wt") as f:
        for p in preds:
            f.write(json.dumps(p, separators=(",", ":")) + "\n")
            n += 1
    return n


def train_one(train_ex, dev_ex, args, device, tag):
    t0 = time.time()
    init = None
    if args.init:  # fine-tune: start from a checkpoint (its vocabulary and weights)
        init, voc, _ = load_checkpoint(args.init, device)
    else:
        voc = build_vocabs(train_ex)
    ftr, fdev = prepare(train_ex, voc, args, device), prepare(dev_ex, voc, args, device)
    if args.human_weight != 1.0:
        ftr["w"] = ftr["w"] * torch.where(ftr["src_h"], torch.tensor(args.human_weight), torch.tensor(1.0))
    legal, choice_ids = constraint_tensors(voc, device)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    net = make_net(voc, args, device)
    if init is not None:
        missing = net.load_state_dict(init.state_dict(), strict=False)
        print("[{}] init from {} ({})".format(tag, args.init, missing), file=sys.stderr, flush=True)
    rb = net.reveal_bias if net.use_reveal else None
    dl0 = None
    if (args.prior and not args.worlds) or init is not None:  # dev loss before training: usage prior (or the init)
        dl0, _ = run_eval(net, fdev, voc, legal, choice_ids, device)
        print("[{}] {} dev {:.4f} {}".format(tag, "init" if init is not None else "prior-only", sum(dl0.values()),
              {k: round(v, 3) for k, v in dl0.items()}), file=sys.stderr, flush=True)
    rb_params = list(net.reveal_bias.parameters()) if net.use_reveal else []
    rb_ids = {id(q) for q in rb_params}
    groups = [{"params": [q for q in net.parameters() if id(q) not in rb_ids]}]
    if rb_params:  # the reveal bias is one free vector per head: faster, no weight decay
        groups.append({"params": rb_params, "lr": args.lr * 10, "weight_decay": 0.0})
    opt = torch.optim.AdamW(groups, lr=args.lr, weight_decay=args.wd)
    N = len(ftr["species"])
    steps_per_epoch = max(1, N // args.batch)
    total_steps = steps_per_epoch * args.epochs
    eval_every = max(20, min(steps_per_epoch // 2, args.eval_every))
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda st: min(1.0, (st + 1) / 100))
    if dl0 is not None:
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
            L = losses(out, b, len(voc["move"]), weight=b["w"] if args.dedup_weight else None, reveal_bias=rb,
                       rank_weight=args.rank_weight)
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
    net.load_state_dict(best_state)
    n_h = sum(1 for e in train_ex if "scored" in e)
    info = {"tag": tag, "train_examples": len(train_ex), "train_farm_examples": len(train_ex) - n_h,
            "train_human_examples": n_h, "train_games": len({e["game"] for e in train_ex}),
            "train_farm_games": len({e["game"] for e in train_ex if "scored" not in e}),
            "train_human_games": len({e["game"] for e in train_ex if "scored" in e}),
            "train_rosters": len({e["roster"] for e in train_ex}), "dev_examples": len(dev_ex),
            "best_epoch": round(best_step / steps_per_epoch, 2), "best_dev_loss": round(best, 4),
            "prior_only_dev_loss": round(sum(dl0.values()), 4) if dl0 else None,
            "params": sum(p.numel() for p in net.parameters()), "seconds": round(time.time() - t0, 1),
            "vocab": {k: len(v) for k, v in voc.items()}, "history": hist}
    return net, voc, info


def load_checkpoint(path, device="cpu"):
    ck = torch.load(path, map_location=device)
    a = argparse.Namespace(**ck["args"])
    for k, v in (("reveal", 0), ("rank_weight", 0.25), ("human_weight", 1.0)):
        if not hasattr(a, k):
            setattr(a, k, v)
    voc = {}
    for k, itos in ck["vocab"].items():
        v = Vocab(specials=())
        for t in itos:
            v.add(t)
        voc[k] = v
    net = make_net(voc, a, device)
    net.load_state_dict(ck["state"])
    net.eval()
    return net, voc, a


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--examples", required=True, help="farm examples (targets.py); train/dev/test splits")
    ap.add_argument("--extra-train", nargs="*", default=[], help="more farm example files, TRAINING rows only "
                    "(their train split; e.g. the F1 volume farm)")
    ap.add_argument("--human", nargs="*", default=[], help="human partial-label example files (human_prep.py); "
                    "their train split is added to training")
    ap.add_argument("--human-frac", type=float, default=1.0, help="fraction of human training games (hash-selected)")
    ap.add_argument("--human-weight", type=float, default=1.0, help="loss weight of human rows relative to farm rows")
    ap.add_argument("--reveal", type=int, default=None, help="1: learned reveal-propensity bias on human rows "
                    "(default: on when --human is given)")
    ap.add_argument("--rank-weight", type=float, default=0.25, help="weight of the positive-only move ranking term")
    ap.add_argument("--predict", nargs="*", default=[], help="name=examples.jsonl.gz: predict EVERY row of the file")
    ap.add_argument("--predict-revealed", nargs="*", default=[], help="name=examples: also write the reveal-biased "
                    "predictions (P(value that will be revealed | view)) for these sets")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--fractions", default="0.1,0.3,1.0")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--patience", type=int, default=6, help="evaluations without dev improvement before stopping")
    ap.add_argument("--eval-every", type=int, default=250, help="steps between dev evaluations (at most half an epoch)")
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
                    "in-game belief) as additive log-probabilities (variant +worlds)")
    ap.add_argument("--prior", type=int, default=1, help="1: residual on the Smogon usage prior (default); 0: from scratch")
    ap.add_argument("--threads", type=int, default=0, help="torch CPU threads (0 = torch default)")
    ap.add_argument("--init", default=None, help="fine-tune from this checkpoint (model_<tag>.pt): same vocabulary; "
                    "e.g. pre-train on farm+human, then fine-tune on farm rows only")
    args = ap.parse_args()
    if args.reveal is None:
        args.reveal = 1 if args.human else 0
    if args.threads:
        torch.set_num_threads(args.threads)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    allex = list(iter_jsonl([args.examples]))
    train = [e for e in allex if e["split"] == "train"]
    dev = [e for e in allex if e["split"] == "dev"]
    test = [e for e in allex if e["split"] == "test"]
    del allex
    heldout = {e["roster"] for e in dev + test}
    for path in args.extra_train:
        extra = [e for e in iter_jsonl([path]) if e["split"] == "train" and e["roster"] not in heldout]
        print("extra train {}: {} examples".format(path, len(extra)), file=sys.stderr)
        train += extra
    human = []
    for path in args.human:
        h = [e for e in iter_jsonl([path]) if e["split"] == "train" and e["roster"] not in heldout
             and (args.human_frac >= 1 or game_frac(e["game"]) < args.human_frac)]
        print("human train {}: {} examples".format(path, len(h)), file=sys.stderr)
        human += h
    assert not ({e["roster"] for e in train + human} & {e["roster"] for e in test}), "roster leak between train and test"
    device = torch.device(args.device)
    summary = {"examples": args.examples, "extra_train": args.extra_train, "human": args.human, "device": args.device,
               "args": vars(args), "runs": []}
    for frac in [float(x) for x in args.fractions.split(",")]:
        tr = [e for e in train if game_frac(e["game"]) < frac] if frac < 1 else list(train)
        tag = "f{}{}".format(frac, args.tag)
        net, voc, info = train_one(tr + human, dev, args, device, tag)
        info["fraction"] = frac
        info["test_examples"] = write_preds(out_dir / "pred_{}.jsonl.gz".format(tag), predict(net, voc, test, args, device))
        sets = [(s, False) for s in args.predict] + [(s, True) for s in args.predict_revealed]
        for spec, rev in sets:
            name, path = spec.split("=", 1)
            ex = list(iter_jsonl([path]))
            info["pred_" + name] = write_preds(out_dir / "pred_{}_{}.jsonl.gz".format(tag, name), predict(net, voc, ex, args, device))
            if rev and net.use_reveal:
                write_preds(out_dir / "pred_{}_{}_revealed.jsonl.gz".format(tag, name), predict(net, voc, ex, args, device, reveal=True))
            del ex
        if frac >= 1:
            torch.save({"state": net.state_dict(), "vocab": {k: v.itos for k, v in voc.items()}, "args": vars(args)},
                       out_dir / "model_{}.pt".format(tag))
        summary["runs"].append(info)
        print(json.dumps({k: v for k, v in info.items() if k != "history"}), file=sys.stderr, flush=True)
        (out_dir / "train_summary{}.json".format(args.tag)).write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
