#!/usr/bin/env python3
"""Run real public replays through a running HardRead app, and keep a row per review plus a summary.

Rows hold replay ids, so keep the output private. Names are never written.

  python3 tools/review/reliability.py --base http://127.0.0.1:18095 --n 15 --moon 2 --out handoff/evidence/reliability/final.jsonl
"""
import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

REPLAYS = "https://replay.pokemonshowdown.com"
UA = {"User-Agent": "HardRead-check"}


def fetch(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))


def call(base, path, body=None, client=""):
    headers = {"content-type": "application/json"}
    if client:   # public mode limits per client; this reads as a different one per game, on the app's own loopback port only
        headers["cf-connecting-ip"] = client
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
    try:
        return json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        return {"http": e.code, "error": e.read().decode(errors="replace")[:150]}


def pick(n, moon, pages=8, every=3):
    """Every `every`-th recent replay, plus games where Roaring Moon really came out, in search order so a run can be traced."""
    rows, moons = [], []
    for page in range(1, pages + 1):
        rows += fetch(f"{REPLAYS}/search.json?format=gen9ou&page={page}")
    plain = [x["id"] for x in rows[::every][:n]]
    for x in rows:
        if len(moons) >= moon:
            break
        log = fetch(f"{REPLAYS}/{x['id']}.json")["log"]
        m = re.search(r"\|poke\|(p[12])\|Roaring Moon", log)
        if m and "|win|" in log and x["id"] not in plain and re.search(rf"\|(?:switch|drag)\|{m.group(1)}a: [^|]*\|Roaring Moon", log):
            moons.append((x["id"], m.group(1)))
    return plain, moons


def one(base, replay, side, client, limit):
    url = f"{REPLAYS}/{replay}"
    row = {"replay": replay, "side": side, "ok": False}
    preflight = call(base, "/api/preflight", {"input": url}, client)
    if not preflight.get("ok"):
        return dict(row, stage="preflight", error=str(preflight.get("error"))[:120])
    t = time.time()
    job = call(base, "/api/review", {"input": url, "side": side}, client)
    if not job.get("job_id") and not job.get("review_id"):
        return dict(row, stage="submit", error=str(job.get("error"))[:120])
    state = job if job.get("review_id") else {}
    while job.get("job_id") and time.time() - t < limit:
        state = call(base, f"/api/job/{job['job_id']}", None, client)
        if state.get("state") in ("done", "error"):
            break
        time.sleep(3)
    if not state.get("review_id"):
        return dict(row, stage="review", seconds=round(time.time() - t), error=str(state.get("message"))[:120])
    view = call(base, f"/api/review/{state['review_id']}", None, client)
    b = view.get("bridge") or {}
    return dict(row, ok=True, seconds=round(time.time() - t), turns=view.get("turns"), end=view.get("end_reason"),
                adds_up=bool(b.get("available")) and b["s"] + b["l"] + b["c"] == b.get("r", b["s"] + b["l"] + b["c"]),
                offered=len(view.get("disagreements") or []), no_match=view.get("luck_no_match"),
                roaring_moon="Roaring Moon" in json.dumps(view))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:18095")
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--moon", type=int, default=2, help="games with Roaring Moon brought out, reviewed from its side")
    ap.add_argument("--limit", type=int, default=420, help="seconds before a review counts as failed")
    ap.add_argument("--public", action="store_true", help="vary the client header, for a copy in public mode")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    plain, moons = pick(a.n, a.moon)
    games = [(r, "p1" if i % 2 == 0 else "p2") for i, r in enumerate(plain)] + moons
    rows = []
    with open(a.out, "w") as f:
        f.write(json.dumps({"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "base": a.base,
                            "health": call(a.base, "/api/health")}) + "\n")
        for i, (replay, side) in enumerate(games):
            row = one(a.base, replay, side, f"198.51.100.{60 + i}" if a.public else "", a.limit)
            rows.append(row)
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(i + 1, "of", len(games), "ok" if row["ok"] else row.get("stage"), row.get("seconds"), flush=True)
        times = [r["seconds"] for r in rows if r["ok"]]
        summary = {"reviews": len(rows), "ok": len(times), "failed": len(rows) - len(times),
                   "median_s": statistics.median(times) if times else None, "max_s": max(times) if times else None,
                   "all_add_up": all(r.get("adds_up") for r in rows if r["ok"]),
                   "roaring_moon_ok": sum(1 for r in rows if r["ok"] and r.get("roaring_moon"))}
        f.write(json.dumps({"summary": summary}) + "\n")
    print(json.dumps(summary))
    sys.exit(1 if summary["failed"] else 0)


if __name__ == "__main__":
    main()
