#!/usr/bin/env python3
"""Predict with a saved belief checkpoint (train.py model_<tag>.pt: v0 or v1) on EVERY row of an examples file.

  python3 tools/belief/predict.py --model scratch_ds/belief/v0/model_f1.0_vol.pt \
      --examples scratch_ds/belief/val.examples.jsonl.gz --out pred_v0_hrval.jsonl.gz [--revealed]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from targets import iter_jsonl  # noqa: E402
from train import load_checkpoint, predict, write_preds  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--examples", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--revealed", action="store_true", help="add the learned reveal bias (v1 with --reveal only)")
    ap.add_argument("--worlds-from-fpbelief", nargs="*", default=None, help="raw chunk tarballs (decision log v3): "
                    "feed Foul Play's fp_belief histogram to a --worlds model instead of the logged worlds")
    a = ap.parse_args()
    net, voc, args = load_checkpoint(a.model)
    ex = list(iter_jsonl([a.examples]))
    if a.worlds_from_fpbelief:
        from baselines import load_fp_beliefs
        bel = load_fp_beliefs(a.worlds_from_fpbelief, {e["duid"] for e in ex})
        miss = 0
        for e in ex:
            h = ((bel.get(e["duid"]) or {}).get("pkmn") or {}).get(e["view"]["name"])
            miss += h is None
            e["fpb"] = h
        print(json.dumps({"rows_without_fp_belief": miss}), file=sys.stderr)
    n = write_preds(a.out, predict(net, voc, ex, args, "cpu", reveal=a.revealed))
    print(json.dumps({"model": a.model, "examples": a.examples, "predictions": n}), file=sys.stderr)


if __name__ == "__main__":
    main()
