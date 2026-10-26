#!/usr/bin/env python3
"""Polite, resumable crawler for public Gen 9 OU replays of the top ladder players.

Standard library only. Public JSON endpoints only (no login, no HTML scraping):
  ladder : https://pokemonshowdown.com/ladder/gen9ou.json
  search : https://replay.pokemonshowdown.com/search.json?user=<id>&format=gen9ou[&before=<uploadtime>]
  replay : https://replay.pokemonshowdown.com/<id>.json

Phases (all resumable; re-run the same command to continue):
  python3 crawl_replays.py ladder     # snapshot top-50 (by the ladder's own order = Elo)
  python3 crawl_replays.py search     # list replays per user (newest first), save listings
  python3 crawl_replays.py fetch      # fetch replays round-robin over users, checkpointed
  python3 crawl_replays.py manifest   # (re)write manifest.json with shard SHA-256s and stats
  python3 crawl_replays.py all        # ladder, search, fetch, manifest

Politeness: <=1 request/s overall, descriptive User-Agent, exponential backoff on
errors/429/5xx, abort after repeated 403/429, skips private/password replays,
stops if the raw directory exceeds the size cap.
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

UA = "HardRead research crawler (hackathon project)"
FORMAT = "gen9ou"
TOP_N = 50
PER_USER = 200
SINCE = 1782864000          # 2026-07-01T00:00:00Z
MIN_INTERVAL = 1.1          # seconds between request starts (global)
SIZE_CAP = 5 * 1024 ** 3
SHARD_REPLAYS = 500
FLUSH_EVERY = 20
MAX_SEARCH_PAGES = 12
RAW = Path("<PROJECT_ROOT>/data/replays/gen9ou-top/raw")
LADDER_URL = "https://pokemonshowdown.com/ladder/gen9ou.json"
SEARCH_URL = "https://replay.pokemonshowdown.com/search.json"
REPLAY_URL = "https://replay.pokemonshowdown.com/{}.json"


class Abort(Exception):
    pass


class Http:
    def __init__(self, log):
        self.last = 0.0
        self.blocked = 0       # consecutive 403/429
        self.n = 0
        self.log = log

    def get(self, url, retries=5):
        delay = 2.0
        for attempt in range(retries + 1):
            wait = self.last + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                                       "Accept-Encoding": "gzip"})
            code, body, err = None, None, None
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    code = r.status
                    body = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
            except urllib.error.HTTPError as e:
                code = e.code
                err = str(e)
            except Exception as e:  # network, timeout
                err = repr(e)
            self.log(f"{code} {url} {err or ''}")
            if code == 200:
                self.blocked = 0
                return code, body
            if code in (403, 429):
                self.blocked += 1
                if self.blocked >= 3:
                    raise Abort(f"repeated {code}: stopping politely")
            if code == 404:
                return code, None
            if attempt == retries:
                return code, None
            time.sleep(delay)
            delay = min(delay * 2, 120)
        return None, None


def dir_size(p):
    return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file())


def mk_logger():
    RAW.mkdir(parents=True, exist_ok=True)
    f = open(RAW / "crawl.log", "a")

    def log(msg):
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")
        f.flush()
    return log


def do_ladder(http, log):
    p = RAW / "ladder_top50.json"
    if p.exists():
        print("ladder snapshot exists:", p)
        return
    code, body = http.get(LADDER_URL)
    if code != 200:
        raise Abort(f"ladder fetch failed {code}")
    d = json.loads(body)
    top = d["toplist"][:TOP_N]
    snap = {"snapshot_time_unix": int(time.time()),
            "snapshot_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "url": LADDER_URL, "formatid": d.get("formatid"), "ordered_by": "ladder list order (Elo, descending)",
            "toplist_len_returned": len(d["toplist"]), "top50": top}
    p.write_text(json.dumps(snap, indent=1))
    print("saved", p)


def do_search(http, log):
    snap = json.loads((RAW / "ladder_top50.json").read_text())
    sd = RAW / "search"
    sd.mkdir(exist_ok=True)
    for u in snap["top50"]:
        uid = u["userid"]
        out = sd / f"{uid}.json"
        if out.exists():
            continue
        seen, items, before, pages, done_reason = set(), [], None, 0, "exhausted"
        while pages < MAX_SEARCH_PAGES:
            q = {"user": uid, "format": FORMAT}
            if before is not None:
                q["before"] = before
            code, body = http.get(SEARCH_URL + "?" + urllib.parse.urlencode(q))
            if code != 200:
                done_reason = f"error_{code}"
                break
            res = json.loads(body)
            pages += 1
            new = [r for r in res if r["id"] not in seen]
            for r in new:
                seen.add(r["id"])
                items.append(r)
            if len(res) < 51 or not new:
                break
            last = res[-1]["uploadtime"]
            if last < SINCE:
                done_reason = "crossed_cutoff"
                break
            before = last + 1   # +1: the 51st row is the next page's first; duplicates are dropped by id
        else:
            done_reason = "max_pages"
        if done_reason.startswith("error"):
            print(f"{uid}: search error, will retry on next run")
            continue
        out.write_text(json.dumps({"userid": uid, "pages": pages, "end": done_reason, "results": items}))
        print(f"{uid}: {len(items)} listed in {pages} page(s) [{done_reason}]")


def build_queue():
    snap = json.loads((RAW / "ladder_top50.json").read_text())
    per = []
    for u in snap["top50"]:
        p = RAW / "search" / f"{u['userid']}.json"
        if not p.exists():
            per.append([])
            continue
        res = json.loads(p.read_text())["results"]
        ok = [r for r in res if r["uploadtime"] >= SINCE and not r.get("private") and not r.get("password")
              and r["id"].startswith(FORMAT)]
        ok.sort(key=lambda r: -r["uploadtime"])
        per.append([r["id"] for r in ok[:PER_USER]])
    queue, seen = [], set()
    for i in range(PER_USER):          # round-robin so a partial crawl is balanced over users
        for lst in per:
            if i < len(lst) and lst[i] not in seen:
                seen.add(lst[i])
                queue.append(lst[i])
    return queue


def shard_files():
    return sorted((RAW).glob("replays-*.jsonl.gz"))


def load_ckpt():
    p = RAW / "fetched_ids.txt"
    ids = set(p.read_text().split()) if p.exists() else set()
    m = RAW / "missing_ids.txt"
    miss = set(m.read_text().split()) if m.exists() else set()
    return ids, miss


def do_fetch(http, log):
    queue = build_queue()
    done, missing = load_ckpt()
    todo = [i for i in queue if i not in done and i not in missing]
    print(f"queue {len(queue)}, done {len(done)}, missing {len(missing)}, todo {len(todo)}")
    shards = shard_files()
    if shards:
        idx = int(shards[-1].name.split("-")[1].split(".")[0])
        # count replays in the last shard
        with gzip.open(shards[-1], "rt") as f:
            cnt = sum(1 for _ in f)
    else:
        idx, cnt = 0, 0
    if cnt >= SHARD_REPLAYS:
        idx, cnt = idx + 1, 0
    buf, buf_ids = [], []

    def flush():
        nonlocal buf, buf_ids, idx, cnt
        if not buf:
            return
        path = RAW / f"replays-{idx:04d}.jsonl.gz"
        with open(path, "ab") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as gz:   # one gzip member per flush
            gz.write(("\n".join(buf) + "\n").encode())
        with open(RAW / "fetched_ids.txt", "a") as f:
            f.write("\n".join(buf_ids) + "\n")
        cnt += len(buf)
        buf, buf_ids = [], []
        if cnt >= SHARD_REPLAYS:
            idx, cnt = idx + 1, 0
        if dir_size(RAW) > SIZE_CAP:
            raise Abort("size cap exceeded")

    t0 = time.time()
    try:
        for n, rid in enumerate(todo):
            code, body = http.get(REPLAY_URL.format(rid))
            if code == 404:
                with open(RAW / "missing_ids.txt", "a") as f:
                    f.write(rid + "\n")
                continue
            if code != 200:
                log(f"giving up for now on {rid} ({code})")
                continue
            try:
                d = json.loads(body)
            except Exception:
                log(f"bad json {rid}")
                continue
            if d.get("private") or d.get("password"):
                with open(RAW / "missing_ids.txt", "a") as f:
                    f.write(rid + "\n")
                continue
            buf.append(json.dumps(d, separators=(",", ":")))
            buf_ids.append(rid)
            if len(buf) >= FLUSH_EVERY:
                flush()
            if (n + 1) % 100 == 0:
                print(f"{n+1}/{len(todo)} fetched, {time.time()-t0:.0f}s", flush=True)
    finally:
        flush()


def do_manifest(log):
    snap = json.loads((RAW / "ladder_top50.json").read_text())
    shards, n, times, ratings, ids = [], 0, [], [], set()
    fmt, both_unrated = Counter(), 0
    for p in shard_files():
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        c = 0
        with gzip.open(p, "rt") as f:
            for line in f:
                d = json.loads(line)
                if d["id"] in ids:
                    continue
                ids.add(d["id"])
                c += 1
                times.append(d.get("uploadtime"))
                ratings.append(d.get("rating"))
                fmt[d.get("formatid")] += 1
        n += c
        shards.append({"file": p.name, "sha256": h, "bytes": p.stat().st_size, "replays": c})
    # search-listing counts
    listed, outside, private, per_user = 0, 0, 0, {}
    listed_ids = {}
    for u in snap["top50"]:
        p = RAW / "search" / f"{u['userid']}.json"
        if not p.exists():
            continue
        s = json.loads(p.read_text())
        r = s["results"]
        o = sum(1 for x in r if x["uploadtime"] < SINCE)
        pv = sum(1 for x in r if x.get("private") or x.get("password"))
        per_user[u["userid"]] = {"listed": len(r), "outside_window": o, "private": pv, "pages": s["pages"], "end": s["end"]}
        for x in r:
            listed_ids[x["id"]] = x
    for x in listed_ids.values():
        if x["uploadtime"] < SINCE:
            outside += 1
        if x.get("private") or x.get("password"):
            private += 1
    rated = sorted(r for r in ratings if isinstance(r, (int, float)))

    def q(p):
        return rated[int(p * (len(rated) - 1))] if rated else None
    fetched, missing = load_ckpt()
    m = {
        "ladder_snapshot_utc": snap["snapshot_time_utc"], "ladder_snapshot_unix": snap["snapshot_time_unix"],
        "ladder_url": LADDER_URL, "ladder_top50": [
            {"rank": i + 1, **{k: u[k] for k in ("userid", "username", "elo", "gxe", "r", "w", "l")}}
            for i, u in enumerate(snap["top50"])],
        "query": {"search": SEARCH_URL + "?user=<userid>&format=gen9ou[&before=<uploadtime of last row + 1>]",
                  "replay": REPLAY_URL, "user_agent": UA, "since_unix": SINCE, "since_utc": "2026-07-01T00:00:00Z",
                  "per_user_cap": PER_USER, "min_interval_s": MIN_INTERVAL, "order": "round-robin over users, newest first"},
        "counts": {"unique_replays_in_shards": n, "unique_listed_total": len(listed_ids),
                   "listed_outside_window_lower_bound": outside, "listed_private_or_password_skipped": private,
                   "missing_or_private_at_fetch": len(missing), "fetched_ids_checkpoint": len(fetched),
                   "queue_size": len(build_queue()), "formatid": dict(fmt)},
        "per_user_listing": per_user,
        "date_range": {"min_uploadtime": min(times) if times else None, "max_uploadtime": max(times) if times else None,
                       "min_utc": time.strftime("%Y-%m-%d", time.gmtime(min(times))) if times else None,
                       "max_utc": time.strftime("%Y-%m-%d", time.gmtime(max(times))) if times else None},
        "rating_distribution": {"rated": len(rated), "unrated": len(ratings) - len(rated),
                                "min": rated[0] if rated else None, "p10": q(.1), "p25": q(.25), "median": q(.5),
                                "p75": q(.75), "p90": q(.9), "max": rated[-1] if rated else None},
        "shards": shards, "total_bytes": sum(s["bytes"] for s in shards),
        "note": "Raw replay data is local-only. Do not re-host. Public release needs owner go-ahead and a check of Showdown's terms.",
    }
    (RAW / "manifest.json").write_text(json.dumps(m, indent=1))
    print("manifest written:", n, "replays,", len(shards), "shards")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["ladder", "search", "fetch", "manifest", "all"])
    a = ap.parse_args()
    log = mk_logger()
    http = Http(log)
    try:
        if a.phase in ("ladder", "all"):
            do_ladder(http, log)
        if a.phase in ("search", "all"):
            do_search(http, log)
        if a.phase in ("fetch", "all"):
            do_fetch(http, log)
        if a.phase in ("manifest", "all"):
            do_manifest(log)
    except Abort as e:
        print("ABORT:", e)
        log("ABORT " + str(e))
        sys.exit(2)


if __name__ == "__main__":
    main()
