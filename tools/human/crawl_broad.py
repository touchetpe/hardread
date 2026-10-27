#!/usr/bin/env python3
"""Broader, rating-stratified crawl of public Gen 9 OU replays (standard library only).

Extends crawl_replays.py (kept unchanged; its Http rules are re-implemented here with an adaptive slow-down) with:
  1. ladder users 51-500: up to 100 in-window replays each (source "top500")
  2. the global recent-replays search without a user filter, sampled over the whole window with
     `before=<time>` (source "global"), stratified by the replay's `rating` field into bands, topping up the
     band that currently has the fewest games and never a band that already has BAND_CAP games.
The top-50 replays already crawled (data/replays/gen9ou-top) count toward the bands and are never re-fetched.

Endpoints (public JSON only): ladder/gen9ou.json, replay.pokemonshowdown.com/search.json, <replay-id>.json.

Rules: <= 1 request/s overall (1.1 s spacing), descriptive User-Agent, exponential backoff, the spacing only ever
grows after a 429/5xx (x2, cap 10 s, never relaxed in this run), abort after 3 consecutive 403/429, skips private /
password replays, stops above the size cap. Everything is checkpointed (listings atomically, replays every 20);
kill it at any time and re-run the same command.

  python3 crawl_broad.py run [--max-hours 8]
  python3 crawl_broad.py status
  python3 crawl_broad.py manifest
"""
import argparse
import gzip
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_replays import Abort, UA, dir_size  # noqa: E402

BASE = Path("<PROJECT_ROOT>/data/replays")
TOP_RAW = BASE / "gen9ou-top" / "raw"
RAW = BASE / "gen9ou-broad" / "raw"
FORMAT = "gen9ou"
SINCE = 1782864000                    # 2026-07-01T00:00:00Z
PER_USER = 100
BAND_CAP = 6000
BANDS = [("lt1300", 0, 1300), ("1300-1499", 1300, 1500), ("1500-1699", 1500, 1700),
         ("1700-1899", 1700, 1900), ("1900plus", 1900, 10 ** 6)]
N_SLOTS = 2400
LISTING_EVERY = 8                      # one global listing request per this many fetches (after the warm-up)
WARMUP_SLOTS = 250
SHARD_REPLAYS = 500
FLUSH_EVERY = 20
SIZE_CAP = 5 * 1024 ** 3
INTERVAL0 = 1.1
LADDER_URL = "https://pokemonshowdown.com/ladder/gen9ou.json"
SEARCH_URL = "https://replay.pokemonshowdown.com/search.json"
REPLAY_URL = "https://replay.pokemonshowdown.com/{}.json"


def band_of(r):
    if r is None:
        return None
    for name, lo, hi in BANDS:
        if lo <= r < hi:
            return name
    return None


class PoliteHttp:
    def __init__(self, log, interval=INTERVAL0):
        self.interval = interval
        self.last = 0.0
        self.blocked = 0
        self.n = 0
        self.throttle_events = 0
        self.errors = Counter()
        self.last_ok = time.time()
        self.abort_after = 0
        self.log = log

    def slow_down(self, why):
        self.throttle_events += 1
        self.interval = min(self.interval * 2, 10.0)
        self.log(f"THROTTLE ({why}): interval now {self.interval}s")

    def get(self, url, retries=5):
        delay = 2.0
        for attempt in range(retries + 1):
            wait = self.last + self.interval - time.time()
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
                code, err = e.code, str(e)
            except Exception as e:  # noqa: BLE001
                err = repr(e)
            self.log(f"{code} {url} {err or ''}")
            self.errors[code] += 1
            if code == 200:
                self.blocked = 0
                self.last_ok = time.time()
                return code, body
            if self.abort_after and time.time() - self.last_ok > self.abort_after:
                raise Abort("no successful request for %d minutes (server down or throttling): stopping" % (self.abort_after // 60))
            if code in (403, 429):
                self.blocked += 1
                self.slow_down(str(code))
                if self.blocked >= 3:
                    raise Abort(f"repeated {code}: stopping politely")
            elif code is None or (code >= 500):
                self.slow_down(str(code))
            if code == 404:
                return code, None
            if attempt == retries:
                return code, None
            time.sleep(delay)
            delay = min(delay * 2, 120)
        return None, None


def atomic_write(path, text):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def mk_logger():
    RAW.mkdir(parents=True, exist_ok=True)
    f = open(RAW / "crawl.log", "a")

    def log(msg):
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")
        f.flush()
    return log


class State:
    """All checkpointed state, rebuilt from disk at start."""

    def __init__(self):
        self.fetched = set()          # ids in either dataset
        self.band_count = Counter()   # all fetched replays (top50 + top500 + global)
        self.source_count = Counter()
        self.index_rows = 0
        # top-50 replays already crawled
        for p in sorted(TOP_RAW.glob("replays-*.jsonl.gz")):
            with gzip.open(p, "rt") as f:
                for line in f:
                    d = json.loads(line)
                    if d["id"] in self.fetched:
                        continue
                    self.fetched.add(d["id"])
                    b = band_of(d.get("rating"))
                    if b:
                        self.band_count[b] += 1
                    self.source_count["top50"] += 1
        ip = RAW / "index.jsonl"
        if ip.exists():
            for line in ip.read_text().splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r["id"] in self.fetched:
                    continue
                self.fetched.add(r["id"])
                self.index_rows += 1
                if r.get("band"):
                    self.band_count[r["band"]] += 1
                self.source_count[r["source"]] += 1
        self.missing = set((RAW / "missing_ids.txt").read_text().split()) if (RAW / "missing_ids.txt").exists() else set()


def ensure_ladder(http, log):
    p = RAW / "ladder_top500.json"
    if p.exists():
        return json.loads(p.read_text())
    code, body = http.get(LADDER_URL)
    if code != 200:
        raise Abort(f"ladder fetch failed {code}")
    d = json.loads(body)
    snap = {"snapshot_time_unix": int(time.time()), "snapshot_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "url": LADDER_URL, "toplist": d["toplist"]}
    atomic_write(p, json.dumps(snap))
    return snap


def old_top50_ids():
    p = TOP_RAW / "ladder_top50.json"
    return {u["userid"] for u in json.loads(p.read_text())["top50"]}


def list_user(http, uid, log):
    """Search listing for one user until PER_USER in-window public rows are known or the window is left."""
    seen, items, before, pages = set(), [], None, 0
    while pages < 6:
        q = {"user": uid, "format": FORMAT}
        if before is not None:
            q["before"] = before
        code, body = http.get(SEARCH_URL + "?" + urllib.parse.urlencode(q))
        if code != 200:
            return None
        res = json.loads(body)
        pages += 1
        new = [r for r in res if r["id"] not in seen]
        for r in new:
            seen.add(r["id"])
            items.append({k: r.get(k) for k in ("id", "uploadtime", "rating", "private", "password")})
        inwin = sum(1 for r in items if r["uploadtime"] >= SINCE and not r["private"] and not r["password"])
        if len(res) < 51 or not new or res[-1]["uploadtime"] < SINCE or inwin >= PER_USER:
            break
        before = res[-1]["uploadtime"] + 1
    return {"userid": uid, "pages": pages, "results": items}


def make_slots(t_end):
    """Equal time slots over the window; a deterministic random order so that any prefix is uniform in time."""
    L = (t_end - SINCE) / N_SLOTS
    order = list(range(N_SLOTS))
    random.Random(20261026).shuffle(order)
    return L, order


def slot_before(i, L, t_end):
    rng = random.Random(i * 7919 + 13)
    return int(SINCE + (i + 1) * L - rng.random() * max(L - 2100, 1))


def load_global_pages():
    done, pool = set(), {}
    p = RAW / "global_pages.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            done.add(r["slot"])
            for row in r["rows"]:
                pool.setdefault(row["id"], row)
    return done, pool


class Writer:
    def __init__(self):
        shards = sorted(RAW.glob("replays-*.jsonl.gz"))
        if shards:
            self.idx = int(shards[-1].name.split("-")[1].split(".")[0])
            with gzip.open(shards[-1], "rt") as f:
                self.cnt = sum(1 for _ in f)
        else:
            self.idx, self.cnt = 0, 0
        if self.cnt >= SHARD_REPLAYS:
            self.idx, self.cnt = self.idx + 1, 0
        self.buf, self.meta = [], []

    def add(self, d, meta):
        self.buf.append(json.dumps(d, separators=(",", ":")))
        self.meta.append(meta)

    def flush(self):
        if not self.buf:
            return
        path = RAW / f"replays-{self.idx:04d}.jsonl.gz"
        with open(path, "ab") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as gz:
            gz.write(("\n".join(self.buf) + "\n").encode())
        with open(RAW / "index.jsonl", "a") as f:
            f.write("".join(json.dumps(m) + "\n" for m in self.meta))
        self.cnt += len(self.buf)
        self.buf, self.meta = [], []
        if self.cnt >= SHARD_REPLAYS:
            self.idx, self.cnt = self.idx + 1, 0
        if dir_size(RAW) > SIZE_CAP:
            raise Abort("size cap exceeded")


def run(max_hours, interval=INTERVAL0, abort_after_min=30.0):
    log = mk_logger()
    lock = RAW / "run.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text())
            os.kill(pid, 0)
            print("another crawl is running (pid %d)" % pid)
            return
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    lock.write_text(str(os.getpid()))
    http = PoliteHttp(log, interval)
    http.abort_after = abort_after_min * 60
    t_start = time.time()
    deadline = t_start + max_hours * 3600
    state = State()
    writer = Writer()
    try:
        ladder = ensure_ladder(http, log)
        t_end = json.loads((RAW / "meta.json").read_text())["t_end"] if (RAW / "meta.json").exists() else None
        if t_end is None:
            t_end = int(time.time()) - 600
            atomic_write(RAW / "meta.json", json.dumps({"t_end": t_end, "since": SINCE, "n_slots": N_SLOTS}))
        old50 = old_top50_ids()
        users = [u for u in ladder["toplist"][50:500] if u["userid"] not in old50]
        sd = RAW / "search_top500"
        sd.mkdir(exist_ok=True)
        # phase 1: user listings (cheap)
        for i, u in enumerate(users):
            out = sd / f"{u['userid']}.json"
            if out.exists():
                continue
            if time.time() > deadline:
                raise Abort("time budget reached")
            res = list_user(http, u["userid"], log)
            if res is None:
                continue
            atomic_write(out, json.dumps(res))
            if (i + 1) % 50 == 0:
                print(f"listed {i+1}/{len(users)} users", flush=True)
        # build top500 queue (round-robin newest first)
        per = []
        for u in users:
            p = sd / f"{u['userid']}.json"
            if not p.exists():
                continue
            rows = [r for r in json.loads(p.read_text())["results"]
                    if r["uploadtime"] >= SINCE and not r["private"] and not r["password"] and r["id"].startswith(FORMAT)]
            rows.sort(key=lambda r: -r["uploadtime"])
            per.append(rows[:PER_USER])
        q500, seen = [], set()
        for i in range(PER_USER):
            for lst in per:
                if i < len(lst) and lst[i]["id"] not in seen:
                    seen.add(lst[i]["id"])
                    q500.append(lst[i])
        q500 = [r for r in q500 if r["id"] not in state.fetched and r["id"] not in state.missing]
        print(f"top500 queue: {len(q500)} replays from {len(per)} users", flush=True)
        # global listing state
        L, order = make_slots(t_end)
        done_slots, pool = load_global_pages()
        todo_slots = [s for s in order if s not in done_slots]
        rng = random.Random(4242)
        cand = defaultdict(list)
        for rid, row in pool.items():
            b = band_of(row["rating"])
            if b and rid not in state.fetched and rid not in state.missing and not row["private"] and not row["password"] \
                    and row["uploadtime"] >= SINCE:
                cand[b].append(row)
        for b in cand:
            rng.shuffle(cand[b])

        def listing_step():
            if not todo_slots:
                return False
            s = todo_slots.pop(0)
            before = slot_before(s, L, t_end)
            code, body = http.get(SEARCH_URL + "?" + urllib.parse.urlencode({"format": FORMAT, "before": before}))
            if code != 200:
                todo_slots.append(s)
                return True
            res = json.loads(body)
            rows = [{k: r.get(k) for k in ("id", "uploadtime", "rating", "private", "password")} for r in res]
            with open(RAW / "global_pages.jsonl", "a") as f:
                f.write(json.dumps({"slot": s, "before": before, "rows": rows}) + "\n")
            for row in rows:
                if row["id"] in pool:
                    continue
                pool[row["id"]] = row
                b = band_of(row["rating"])
                if b and row["id"] not in state.fetched and not row["private"] and not row["password"] \
                        and row["uploadtime"] >= SINCE:
                    cand[b].append(row)
            return True

        for _ in range(WARMUP_SLOTS):
            if time.time() > deadline:
                break
            if not listing_step():
                break
        print(f"global warm-up: {len(pool)} listed, candidates per band "
              f"{ {b: len(v) for b, v in cand.items()} }", flush=True)

        def next_global():
            avail = [b for b, _, _ in BANDS if cand.get(b) and state.band_count[b] < BAND_CAP]
            if not avail:
                return None
            b = min(avail, key=lambda x: state.band_count[x])
            return cand[b].pop()

        n_fetch, last_report, last_manifest = 0, time.time(), time.time()
        turn = 0
        pos500 = 0
        while time.time() < deadline:
            if (n_fetch % LISTING_EVERY) == LISTING_EVERY - 1 and todo_slots:
                listing_step()
                n_fetch += 1
                continue
            row, source = None, None
            first = "top500" if turn % 2 == 0 else "global"
            turn += 1
            for src in (first, "global" if first == "top500" else "top500"):
                if src == "top500":
                    while pos500 < len(q500) and (q500[pos500]["id"] in state.fetched):
                        pos500 += 1
                    if pos500 < len(q500):
                        row, source = q500[pos500], "top500"
                        pos500 += 1
                        break
                else:
                    r = next_global()
                    while r is not None and r["id"] in state.fetched:
                        r = next_global()
                    if r is not None:
                        row, source = r, "global"
                        break
            if row is None:
                if todo_slots:
                    listing_step()
                    continue
                print("nothing left to fetch", flush=True)
                break
            n_fetch += 1
            code, body = http.get(REPLAY_URL.format(row["id"]))
            if code == 404:
                state.missing.add(row["id"])
                with open(RAW / "missing_ids.txt", "a") as f:
                    f.write(row["id"] + "\n")
                continue
            if code != 200:
                continue
            try:
                d = json.loads(body)
            except Exception:
                continue
            if d.get("private") or d.get("password") or d.get("formatid") != FORMAT:
                state.missing.add(row["id"])
                with open(RAW / "missing_ids.txt", "a") as f:
                    f.write(row["id"] + "\n")
                continue
            b = band_of(d.get("rating"))
            writer.add(d, {"id": d["id"], "source": source, "rating": d.get("rating"), "band": b,
                           "uploadtime": d.get("uploadtime")})
            state.fetched.add(d["id"])
            if b:
                state.band_count[b] += 1
            state.source_count[source] += 1
            if len(writer.buf) >= FLUSH_EVERY:
                writer.flush()
            if time.time() - last_report > 300:
                last_report = time.time()
                st = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "requests": http.n, "interval_s": http.interval,
                      "throttle_events": http.throttle_events, "status_codes": dict(http.errors),
                      "bands": dict(state.band_count), "sources": dict(state.source_count),
                      "global_pages_done": N_SLOTS - len(todo_slots), "candidates": {b: len(v) for b, v in cand.items()},
                      "top500_queue_left": len(q500) - pos500, "disk_mb": round(dir_size(RAW) / 1e6, 1)}
                atomic_write(RAW / "status.json", json.dumps(st, indent=1))
                print(json.dumps(st), flush=True)
            if time.time() - last_manifest > 1800:
                last_manifest = time.time()
                writer.flush()
                write_manifest(state)
    except Abort as e:
        print("ABORT:", e, flush=True)
        log("ABORT " + str(e))
    finally:
        writer.flush()
        write_manifest(State())
        try:
            lock.unlink()
        except OSError:
            pass
        print("stopped; requests this run:", http.n, flush=True)


def write_manifest(state=None):
    state = state or State()
    ladder = json.loads((RAW / "ladder_top500.json").read_text()) if (RAW / "ladder_top500.json").exists() else None
    shards, rows = [], 0
    for p in sorted(RAW.glob("replays-*.jsonl.gz")):
        n = 0
        with gzip.open(p, "rt") as f:
            n = sum(1 for _ in f)
        shards.append({"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size,
                       "replays": n})
        rows += n
    ratings, times, src_band = [], [], defaultdict(Counter)
    ip = RAW / "index.jsonl"
    if ip.exists():
        for line in ip.read_text().splitlines():
            r = json.loads(line)
            ratings.append(r.get("rating"))
            times.append(r.get("uploadtime"))
            src_band[r["source"]][r.get("band") or "unrated"] += 1
    rated = sorted(x for x in ratings if isinstance(x, (int, float)))
    st = json.loads((RAW / "status.json").read_text()) if (RAW / "status.json").exists() else {}
    m = {
        "ladder_snapshot_utc": ladder and ladder["snapshot_time_utc"], "ladder_url": LADDER_URL,
        "ladder_users_51_500": ladder and [
            {"rank": i + 51, **{k: u.get(k) for k in ("userid", "username", "elo", "gxe", "r", "w", "l")}}
            for i, u in enumerate(ladder["toplist"][50:500])],
        "query": {"top500": "search.json?user=<id>&format=gen9ou[&before=<last uploadtime+1>], newest first, up to %d in window" % PER_USER,
                  "global": "search.json?format=gen9ou&before=<time>, %d time slots over the window, random order" % N_SLOTS,
                  "replay": REPLAY_URL, "since_utc": "2026-07-01T00:00:00Z", "user_agent": UA,
                  "bands": [b[0] for b in BANDS], "band_cap": BAND_CAP, "band_field": "replay `rating` (lower player's post-game rating)"},
        "counts": {"new_replays_in_broad_shards": rows, "top50_replays_reused": state.source_count.get("top50", 0),
                   "by_source_and_band_new": {s: dict(c) for s, c in src_band.items()},
                   "band_counts_including_top50": dict(state.band_count), "by_source_including_top50": dict(state.source_count),
                   "missing_or_private": len(state.missing)},
        "date_range_new": [min(times, default=None), max(times, default=None)],
        "rating_distribution_new": {"n": len(rated), "min": rated[0] if rated else None,
                                    "median": rated[len(rated) // 2] if rated else None, "max": rated[-1] if rated else None},
        "crawl_status": st, "shards": shards, "total_bytes": sum(s["bytes"] for s in shards),
        "note": "Raw data is local-only. Do not re-host. A public release needs the owner's go-ahead and a check of Showdown's terms.",
    }
    atomic_write(RAW / "manifest.json", json.dumps(m, indent=1))
    print("manifest written:", rows, "new replays")


def status():
    state = State()
    print(json.dumps({"bands_including_top50": dict(state.band_count), "sources": dict(state.source_count),
                      "disk_mb": round(dir_size(RAW) / 1e6, 1) if RAW.exists() else 0}, indent=1))
    p = RAW / "status.json"
    if p.exists():
        print(p.read_text())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["run", "status", "manifest"])
    ap.add_argument("--max-hours", type=float, default=8.0)
    ap.add_argument("--interval", type=float, default=INTERVAL0,
                    help="seconds between requests (default 1.1; use a larger value after throttling or an outage)")
    ap.add_argument("--abort-after-failing-minutes", type=float, default=30.0,
                    help="stop when no request has succeeded for this long")
    a = ap.parse_args()
    if a.phase == "run":
        run(a.max_hours, a.interval, a.abort_after_failing_minutes)
    elif a.phase == "status":
        status()
    else:
        write_manifest()


if __name__ == "__main__":
    main()
