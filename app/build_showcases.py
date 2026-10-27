"""Precompute showcase reviews from the pseudonymised ladder crawl (Draft B: team drafted from the replay).

  python3 app/build_showcases.py run  gen9ou-2671564416:p1 gen9ou-2700188535:p2 ... [--parallel 3]
  python3 app/build_showcases.py list                       # candidates already reviewed, with their numbers
  python3 app/build_showcases.py publish gen9ou-2671564416:p1 ...   # -> app/data/showcases/

Reviews run in scratch_ds/app/showcase_candidates/<id>_<side>/ (gitignored). Published files carry no replay id,
no username and no pseudonym: players are "Player1"/"Player2" and the view says "You"/"Opponent".
"""
import argparse
import gzip
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

APP = Path(__file__).resolve().parent
HARDREAD = APP.parent
sys.path.insert(0, str(APP))
import pipeline as P  # noqa: E402

CAND = HARDREAD / "scratch_ds" / "app" / "showcase_candidates"
OUT = APP / "data" / "showcases"
GAMES = HARDREAD / "scratch_ds" / "human_broad" / "games.jsonl.gz"
FP_PY = HARDREAD.parent / "environments" / "foulplay" / "bin" / "python"


def load_games(ids):
    want = {"human/" + i for i in ids}
    out = {}
    with gzip.open(GAMES, "rt") as f:
        for line in f:
            if '"uid": "human/' not in line:
                continue
            g = json.loads(line)
            if g["uid"] in want:
                out[g["uid"][6:]] = g
    return out


def run_one(rid, side, g, processes):
    d = CAND / f"{rid}_{side}"
    d.mkdir(parents=True, exist_ok=True)
    rep = {"log": "\n".join(g["log"]), "formatid": "gen9ou", "private": False}
    lines, ctx, job = P.prepare(rep, side)
    job["processes"] = processes
    (d / "replay.json").write_text(json.dumps({"log": "\n".join(lines)}))
    (d / "input.json").write_text(json.dumps(job))
    meta = {"rating_band": g.get("rating_band"), "turns": g["turns"], "end_reason": g.get("end_reason"),
            "side": side}
    (d / "ctx.json").write_text(json.dumps(ctx))
    (d / "showmeta.json").write_text(json.dumps(meta))
    t0 = time.time()
    rc = subprocess.call([str(FP_PY), str(APP / "worker.py"), str(d)], cwd=str(HARDREAD))
    ok = rc == 0 and (d / "review.json").exists()
    if ok:
        view = P.build_view(json.loads((d / "review.json").read_text()), ctx)
        (d / "view.json").write_text(json.dumps(view))
    print(f"{rid}:{side} rc={rc} {time.time() - t0:.0f}s", flush=True)
    return ok


def cmd_run(a):
    pairs = [p.split(":") for p in a.pairs]
    games = load_games({r for r, _ in pairs})
    missing = [r for r, _ in pairs if r not in games]
    if missing:
        sys.exit(f"not in the crawl: {missing}")
    with ThreadPoolExecutor(a.parallel) as ex:
        list(ex.map(lambda p: run_one(p[0], p[1], games[p[0]], a.processes), pairs))


def cmd_list(a):
    for d in sorted(CAND.glob("*_p?")):
        vp = d / "view.json"
        if not vp.exists():
            continue
        v = json.loads(vp.read_text())
        m = json.loads((d / "showmeta.json").read_text())
        b = v["bridge"]
        dis = "; ".join(f"t{x['turn']} {x['chosen']}->{x['best']} {x['gap_pts']}" for x in v["disagreements"])
        lm = ", ".join(f"t{x['turn']} {x['luck_pts']:+d}" for x in v["luck_moments"])
        print(f"{d.name:28s} {v['result']:4s} {m['rating_band']:9s} {m['turns']:3d}t {m['end_reason']:7s} "
              f"s={b.get('s')} l={b.get('l')} c={b.get('c')} | luck: {lm} | dis: {dis}")


BAND_TEXT = {"lt1300": "rated under 1300", "1300-1499": "rated 1300-1499", "1500-1699": "rated 1500-1699",
             "1700-1899": "rated 1700-1899", "1900plus": "rated 1900+"}


def cmd_publish(a):
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for i, p in enumerate(a.pairs):
        rid, side = p.split(":")
        d = CAND / f"{rid}_{side}"
        review = json.loads((d / "review.json").read_text())
        ctx = json.loads((d / "ctx.json").read_text())
        m = json.loads((d / "showmeta.json").read_text())
        sid = f"showcase-{i + 1}"
        review["meta"]["players"] = {"p1": "Player1", "p2": "Player2"}
        ctx["private"] = False
        ctx["names"] = P.name_table(json.loads((d / "replay.json").read_text())["log"].split("\n"))
        view = P.build_view(review, ctx)
        blob, cblob = json.dumps(review), json.dumps(ctx)
        for b in (blob, cblob):
            assert not re.search(r"u_[0-9a-f]{10}", b) and rid not in b, "pseudonym or replay id leaked"
        (OUT / f"{sid}.review.json").write_text(blob)
        (OUT / f"{sid}.ctx.json").write_text(cblob)
        for old in OUT.glob(f"{sid}.view.json"):
            old.unlink()
        title = a.titles[i] if a.titles and i < len(a.titles) else None
        index.append({"id": sid, "title": title, "band": BAND_TEXT.get(m["rating_band"], "ladder game"),
                      "turns": m["turns"], "end_reason": m["end_reason"], "result": view["result"],
                      "bridge": {k: view["bridge"].get(k) for k in ("s", "l", "c", "r")},
                      "disagreements": len(view["disagreements"]),
                      "top_luck": view["luck_moments"][0] if view["luck_moments"] else None})
        print("published", sid)
    (OUT / "index.json").write_text(json.dumps({"showcases": index}, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("pairs", nargs="+")
    r.add_argument("--parallel", type=int, default=2)
    r.add_argument("--processes", type=int, default=4)
    sub.add_parser("list")
    p = sub.add_parser("publish")
    p.add_argument("pairs", nargs="+")
    p.add_argument("--titles", nargs="*")
    a = ap.parse_args()
    {"run": cmd_run, "list": cmd_list, "publish": cmd_publish}[a.cmd](a)


if __name__ == "__main__":
    main()
