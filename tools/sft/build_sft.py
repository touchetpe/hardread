#!/usr/bin/env python3
"""Decisions (tools/dataset/extract.py output) -> Glimmer SFT data (pre-tokenized, axolotl plugin format).

Usage:
  python3 tools/sft/build_sft.py scratch_ds/c1t1/decisions.jsonl.gz [...] --out-dir scratch_ds/sft/v1 \
      [--task action|reads|both] [--val-frac 0.05] [--drop-team-preview] [--min-legal 2] \
      [--min-visits 0] [--min-conf 0] [--cfg C1t1] [--weight none|conf] [--teampreview-weight 1] \
      [--reads-every 4] [--max-tokens 2048] [--keep-frac 1.0] [--max-train 0] [--model-dir models/muse-glimmer-metadata]

Each decisions file needs its sibling <name>.games.jsonl[.gz] (public battle logs).

Split: by GAME. bucket = int(sha256("<run_id>/g<game>")[:8], 16) / 2**32; val if bucket < val_frac.
Both sides of a game share its log, so they always land in the same split. Teams are drawn from
a 2,000-team dev pool, so a team can appear in both splits (positions/games do not).

Targets
  action: Foul Play's played `choice`, as submit_action(choice_id) over the frozen menu.
  reads : opponent's TRUE sets (opp_team, label only) for every non-fainted opponent Pokemon,
          as submit_reads(reads={species: {ability, item, moves, spread, tera}}).

Weights are applied by deterministic resampling (copies = floor(w) + [hash < frac(w)]), so the
trainer stays a plain unweighted SFT. --weight conf: w = clip(policy[choice], .05, 1) / --conf-ref.

Outputs (out-dir):
  train.jsonl.gz, val.jsonl.gz  {"example_id","task","input_ids","labels"} for the trainer plugin
  eval.jsonl.gz                  held-out prompts with id_map/choice/policy/reads for eval_sft.py
  manifest.json                  args, counts, drops, token stats, tokenizer/template hashes
  examples.txt                   rendered examples
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import serialize as S  # noqa: E402
from glimmer_chat import GlimmerChat  # noqa: E402


def opener(p):
    p = str(p)
    return gzip.open(p, "rt") if p.endswith(".gz") else open(p)


def games_path(dec_path: Path) -> Path:
    return dec_path.with_name(dec_path.name.replace(".jsonl", ".games.jsonl"))


def h01(key: str) -> float:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 2 ** 32


def copies(w: float, key: str) -> int:
    base = int(math.floor(w))
    return base + (1 if h01("w/" + key) < w - base else 0)


def pstats(xs):
    if not xs:
        return {}
    xs = sorted(xs)
    q = lambda f: xs[min(len(xs) - 1, int(f * (len(xs) - 1)))]  # noqa: E731
    return {"n": len(xs), "mean": round(statistics.mean(xs), 1), "min": xs[0], "p50": q(.5), "p90": q(.9),
            "p99": q(.99), "max": xs[-1]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--task", choices=["action", "reads", "both"], default="action")
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--drop-team-preview", action="store_true")
    ap.add_argument("--min-legal", type=int, default=2, help="drop decisions with fewer legal actions (forced moves)")
    ap.add_argument("--min-visits", type=int, default=0, help="min total search visits summed over worlds")
    ap.add_argument("--min-conf", type=float, default=0.0, help="min search policy share of the played action")
    ap.add_argument("--cfg", default=None, help="keep only decisions made at this search config (e.g. C1t1)")
    ap.add_argument("--weight", choices=["none", "conf"], default="none")
    ap.add_argument("--conf-ref", type=float, default=0.5)
    ap.add_argument("--weight-cap", type=float, default=2.0)
    ap.add_argument("--teampreview-weight", type=float, default=1.0)
    ap.add_argument("--reads-every", type=int, default=4, help="reads task: keep ~1 in N decisions")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--max-train", type=int, default=0, help="cap train examples after resampling (0 = all)")
    ap.add_argument("--keep-frac", type=float, default=1.0,
                    help="keep a deterministic sha256(uid) subset of decisions before rendering (fast subsampling)")
    ap.add_argument("--model-dir", default=None, help="dir with tokenizer.json + chat_template.jinja (pinned)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n-examples", type=int, default=3)
    args = ap.parse_args(argv)

    t0 = time.time()
    gc = GlimmerChat(args.model_dir)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    tasks = ["action", "reads"] if args.task == "both" else [args.task]

    drops, counts = Counter(), Counter()
    tok = defaultdict(lambda: defaultdict(list))
    train_rows, val_rows, eval_rows, shown = [], [], [], []
    games_seen = {"train": set(), "val": set()}
    sources = []

    for dec_path in args.inputs:
        gpath = games_path(dec_path)
        logs = {}
        with opener(gpath) as f:
            for line in f:
                g = json.loads(line)
                logs[g["uid"]] = g["log"]
        sources.append({"decisions": str(dec_path), "games": str(gpath), "n_games": len(logs)})
        with opener(dec_path) as f:
            for line in f:
                r = json.loads(line)
                counts["decisions_in"] += 1
                if args.keep_frac < 1.0 and h01("keep/" + r["uid"]) >= args.keep_frac:
                    drops["keep_frac"] += 1
                    continue
                game_uid = f"{r['run_id']}/g{r['game']:04d}"
                log = logs.get(game_uid)
                if log is None:
                    drops["no_game_log"] += 1
                    continue
                if r.get("history_end") is None:
                    drops["no_history_cut"] += 1
                    continue
                if args.cfg and r.get("cfg") != args.cfg:
                    drops["cfg"] += 1
                    continue
                if r.get("team_preview") and args.drop_team_preview:
                    drops["team_preview"] += 1
                    continue
                legal = r.get("legal") or []
                if len(legal) < args.min_legal:
                    drops["min_legal"] += 1
                    continue
                if r.get("choice") not in legal:
                    drops["choice_not_legal"] += 1
                    continue
                visits = sum(w.get("total_visits") or 0 for w in r.get("worlds") or [])
                if visits < args.min_visits:
                    drops["min_visits"] += 1
                    continue
                conf = float((r.get("policy") or {}).get(r["choice"], 0.0))
                if conf < args.min_conf:
                    drops["min_conf"] += 1
                    continue
                split = "val" if h01(game_uid) < args.val_frac else "train"
                games_seen[split].add(game_uid)
                history = log[: r["history_end"]]
                w = 1.0
                if args.weight == "conf":
                    w = min(args.weight_cap, max(0.05, min(conf, 1.0)) / args.conf_ref)
                if r.get("team_preview"):
                    w *= args.teampreview_weight

                for task in tasks:
                    if task == "reads" and h01("reads/" + r["uid"]) >= 1.0 / max(1, args.reads_every):
                        continue
                    ex = S.build_example(r["view"], history, legal, r["player"], r["turn"], r["k"],
                                         bool(r.get("team_preview")), task=task)
                    if task == "action":
                        tgt = S.target_message(r["choice"], ex["id_map"])
                        reads = None
                    else:
                        reads = S.reads_label(ex["species"], r.get("opp_team") or [])
                        if not reads:
                            drops["reads_no_label"] += 1
                            continue
                        tgt = S.target_message(None, {}, task="reads", reads=reads)
                    enc = gc.encode(ex["messages"], ex["tools"], tgt)
                    n = len(enc["input_ids"])
                    if n > args.max_tokens:
                        drops[f"too_long_{task}"] += 1
                        continue
                    eid = f"{r['uid']}/{task}"
                    tok[task]["prompt"].append(enc["n_prompt"])
                    tok[task]["target"].append(enc["n_target"])
                    tok[task]["total"].append(n)
                    rec = {"example_id": eid, "task": task, "input_ids": enc["input_ids"], "labels": enc["labels"]}
                    if split == "train":
                        k = copies(w, eid)
                        counts[f"train_{task}_unique"] += 1
                        for c in range(k):
                            train_rows.append(rec if c == 0 else dict(rec, example_id=f"{eid}#{c}"))
                    else:
                        val_rows.append(rec)
                        inv = {v: k for k, v in ex["id_map"].items()}
                        eval_rows.append({
                            "example_id": eid, "task": task, "uid": r["uid"], "turn": r["turn"], "k": r["k"],
                            "team_preview": bool(r.get("team_preview")), "cfg": r.get("cfg"),
                            "messages": ex["messages"], "tools": ex["tools"], "id_map": ex["id_map"],
                            "choice": r["choice"] if task == "action" else None,
                            "choice_id": inv.get(r["choice"]) if task == "action" else None,
                            "policy": {inv[a]: p for a, p in (r.get("policy") or {}).items() if a in inv} if task == "action" else None,
                            "reads": reads, "n_prompt": enc["n_prompt"], "serializer": S.SERIALIZER_VERSION})
                    if len(shown) < args.n_examples * len(tasks) and sum(1 for s in shown if s[0] == task) < args.n_examples \
                            and h01("show/" + r["uid"]) < 0.05:
                        shown.append((task, eid, gc.render(ex["messages"], ex["tools"], add_generation_prompt=True)
                                       + enc["target_text"]))

    rng = random.Random(args.seed)
    rng.shuffle(train_rows)
    if args.max_train and len(train_rows) > args.max_train:
        drops["max_train_cap"] += len(train_rows) - args.max_train
        train_rows = train_rows[: args.max_train]

    def dump(name, rows):
        with gzip.open(out / name, "wt") as f:
            for row in rows:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")

    dump("train.jsonl.gz", train_rows)
    dump("val.jsonl.gz", val_rows)
    dump("eval.jsonl.gz", eval_rows)
    with open(out / "examples.txt", "w") as f:
        for task, eid, text in shown:
            f.write(f"===== {task} {eid}\n{text}\n\n")

    manifest = {
        "schema": "hardread-sft-data/v1", "created_unix": int(time.time()), "build_seconds": round(time.time() - t0, 1),
        "serializer_version": S.SERIALIZER_VERSION, "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()
                                                            if k != "inputs"},
        "sources": sources, "tokenizer_sha256": gc.tokenizer_sha256, "chat_template_sha256": gc.template_sha256,
        "split_rule": "game-level: sha256('<run_id>/g<game:04d>')[:8]/2^32 < val_frac -> val",
        "supervision": "labels on every token after the generation prompt '<|start|>assistant' (channel header, ATEM call, <|eot|>)",
        "counts": dict(counts, train_rows=len(train_rows), val_rows=len(val_rows), eval_rows=len(eval_rows),
                       train_games=len(games_seen["train"]), val_games=len(games_seen["val"]),
                       train_by_task=dict(Counter(r["task"] for r in train_rows)),
                       val_by_task=dict(Counter(r["task"] for r in val_rows))),
        "drops": dict(drops),
        "tokens": {task: {k: pstats(v) for k, v in d.items()} for task, d in tok.items()},
        "train_supervised_tokens": sum(sum(1 for x in r["labels"] if x != -100) for r in train_rows),
        "train_total_tokens": sum(len(r["input_ids"]) for r in train_rows),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: manifest[k] for k in ("counts", "drops", "tokens", "build_seconds")}, indent=1))


if __name__ == "__main__":
    main()
