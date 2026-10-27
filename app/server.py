"""HardRead local app: paste a finished Gen 9 OU replay, see the dice separated from the decisions.

Run (from hardread/, system python3 with fastapi + uvicorn):
  python3 app/server.py                      # http://127.0.0.1:18051
  python3 app/server.py --port 18052 --concurrency 1

Reviews run tools/review/review.py in the Foul Play environment as background jobs (app/worker.py), one game per
process, at most --concurrency at a time; results are cached under scratch_ds/app/cache/ (public replays 14 days,
private replays 24 hours). Nothing is posted anywhere; the only outbound request is the replay link you paste.
"""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

APP = Path(__file__).resolve().parent
HARDREAD = APP.parent
sys.path.insert(0, str(APP))
import pipeline as P  # noqa: E402

STATE = Path(os.environ.get("HARDREAD_APP_STATE", HARDREAD / "scratch_ds" / "app"))
CACHE = STATE / "cache"
JOBS = STATE / "jobs"
SHOWCASES = APP / "data" / "showcases"
FP_PY = Path(os.environ.get("HARDREAD_FP_PYTHON", HARDREAD.parent / "environments" / "foulplay" / "bin" / "python"))

RATE_REVIEWS_PER_HOUR = int(os.environ.get("HARDREAD_RATE_REVIEWS", "5"))
QUEUE_CAP = int(os.environ.get("HARDREAD_QUEUE_CAP", "50"))
TTL_PUBLIC = 14 * 24 * 3600
TTL_PRIVATE = 24 * 3600
JOB_TIMEOUT = int(os.environ.get("HARDREAD_JOB_TIMEOUT", "600"))
_SALT = secrets.token_hex(16)   # per process: hashed client addresses are never written anywhere


def _jsonfile(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- jobs

class Jobs:
    def __init__(self, concurrency=1, processes=4):
        self.concurrency = concurrency
        self.processes = processes
        self.lock = threading.Lock()
        self.jobs = {}          # job_id -> dict
        self.by_key = {}        # cache key -> job_id (queued or running)
        self.queue = deque()
        self.hits = {}          # hashed client -> deque of submit times
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    # rate limit: RATE_REVIEWS_PER_HOUR new reviews per client per hour (cache hits are free)
    def allow(self, client):
        now = time.time()
        h = hashlib.sha256((_SALT + client).encode()).hexdigest()[:16]
        with self.lock:
            q = self.hits.setdefault(h, deque())
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= RATE_REVIEWS_PER_HOUR:
                return False, int(3600 - (now - q[0]))
            q.append(now)
            return True, 0

    def submit(self, key, lines, ctx, job_input):
        with self.lock:
            if key in self.by_key:
                return self.by_key[key]
            if len(self.queue) >= QUEUE_CAP:
                return None
            jid = secrets.token_urlsafe(9)
            d = JOBS / jid
            d.mkdir(parents=True, exist_ok=True)
            job_input = dict(job_input, processes=self.processes)
            (d / "replay.json").write_text(json.dumps({"log": "\n".join(lines)}))
            (d / "input.json").write_text(json.dumps(job_input))
            self.jobs[jid] = {"id": jid, "key": key, "ctx": ctx, "dir": d, "state": "queued",
                              "created": time.time(), "proc": None}
            self.by_key[key] = jid
            self.queue.append(jid)
            return jid

    def status(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            if j is None:
                return None
            out = {"id": jid, "state": j["state"]}
            if j["state"] == "queued":
                out["position"] = list(self.queue).index(jid) + 1 if jid in self.queue else 1
                out["message"] = "Waiting for a free engine slot"
                out["progress"] = 0.0
            elif j["state"] == "running":
                st = _jsonfile(j["dir"] / "status.json", {}) or {}
                out.update({k: st.get(k) for k in ("stage", "progress", "message", "done", "decisions")})
                out["elapsed"] = round(time.time() - j.get("started", time.time()), 1)
            elif j["state"] == "done":
                out["review_id"] = j["review_id"]
            else:
                out["message"] = j.get("message") or "The engine could not review this game."
            return out

    def _loop(self):
        while not self._stop:
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                print("job loop error:", type(e).__name__, e, file=sys.stderr)
            time.sleep(0.5)

    def _tick(self):
        with self.lock:
            running = [j for j in self.jobs.values() if j["state"] == "running"]
            while self.queue and len(running) < self.concurrency:
                jid = self.queue.popleft()
                j = self.jobs[jid]
                j["proc"] = subprocess.Popen([str(FP_PY), str(APP / "worker.py"), str(j["dir"])], cwd=str(HARDREAD),
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                j["state"], j["started"] = "running", time.time()
                running.append(j)
        for j in running:
            p = j["proc"]
            if p.poll() is None:
                if time.time() - j["started"] > JOB_TIMEOUT:
                    _stop_job(j)
                    self._finish(j, ok=False, message="The review took too long and was stopped.")
                continue
            self._finish(j, ok=p.returncode == 0 and (j["dir"] / "review.json").exists())

    def _finish(self, j, ok, message=None):
        rid = None
        if ok:
            try:
                review = json.loads((j["dir"] / "review.json").read_text())
                P.build_view(review, j["ctx"])   # fail here, not on the first page view
                rid = store_review(j["key"], review, j["ctx"])
            except Exception as e:  # noqa: BLE001
                ok, message = False, "The review finished but could not be displayed."
                print("finish error:", type(e).__name__, e, file=sys.stderr)
        with self.lock:
            j["state"] = "done" if ok else "error"
            j["review_id"] = rid
            j["message"] = message
            j["finished"] = time.time()
            self.by_key.pop(j["key"], None)
        # the pseudonymised log and team are not kept once the review is cached
        for f in ("replay.json", "input.json", "team.txt", "review.json"):
            try:
                (j["dir"] / f).unlink()
            except OSError:
                pass

    def shutdown(self):
        self._stop = True
        with self.lock:
            for j in self.jobs.values():
                if j["state"] == "running":
                    _stop_job(j)


def _stop_job(j):
    """Stop only the processes this server started: the worker and its review.py child (recorded PIDs)."""
    p = j["proc"]
    pid_file = j["dir"] / "review.pid"
    try:
        if pid_file.exists():
            os.kill(int(pid_file.read_text().strip()), 15)
    except (OSError, ValueError):
        pass
    try:
        p.terminate()
    except OSError:
        pass


# ---------------------------------------------------------------- cache

def review_id_for(key):
    return "r" + key[:19]


def store_review(key, review, ctx):
    """Cache the review.py output and the app context; the view is rebuilt from them on every request."""
    rid = review_id_for(key)
    d = CACHE / rid
    d.mkdir(parents=True, exist_ok=True)
    review = dict(review)
    review["meta"] = dict(review["meta"], players={"p1": "Player1", "p2": "Player2"})
    private = bool(ctx.get("private"))
    (d / "review.json").write_text(json.dumps(review))
    (d / "ctx.json").write_text(json.dumps(ctx))
    (d / "meta.json").write_text(json.dumps({"created": time.time(), "private": private}))
    return rid


def load_review(rid, raw=False):
    """A cached review or a showcase: the view (default) or the raw review.py JSON (receipts)."""
    if rid.startswith("showcase-") and rid[9:].isdigit():
        d, stem, extra = SHOWCASES, rid + ".", {"showcase": True, "private": False}
    elif rid.startswith("r") and rid[1:].isalnum() and len(rid) == 20:
        m = _jsonfile(CACHE / rid / "meta.json")
        if not m or time.time() - m["created"] > (TTL_PRIVATE if m.get("private") else TTL_PUBLIC):
            return None
        d, stem, extra = CACHE / rid, "", {"private": bool(m.get("private"))}
    else:
        return None
    review = _jsonfile(d / f"{stem}review.json")
    if review is None or raw:
        return review
    ctx = _jsonfile(d / f"{stem}ctx.json", {}) or {}
    view = P.build_view(review, ctx)
    view.update(extra, id=rid)
    return view


def expire_cache():
    now = time.time()
    for d in CACHE.glob("r*"):
        m = _jsonfile(d / "meta.json")
        if m is None or now - m["created"] > (TTL_PRIVATE if m.get("private") else TTL_PUBLIC):
            shutil.rmtree(d, ignore_errors=True)
    for d in JOBS.glob("*"):
        try:
            if now - d.stat().st_mtime > 6 * 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


# ---------------------------------------------------------------- input resolution

_FETCHED = {}   # replay id -> (time, replay dict); in memory only, 10 minutes


def resolve_input(text):
    kind, val, _ = P.classify_input(text)
    if kind == "url":
        hit = _FETCHED.get(val)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        rep = P.fetch_replay(val)
        _FETCHED[val] = (time.time(), rep)
        for k in [k for k, (t, _) in _FETCHED.items() if time.time() - t > 600]:
            _FETCHED.pop(k, None)
        return rep
    return val


# ---------------------------------------------------------------- app

def create_app(concurrency=1, processes=4):
    CACHE.mkdir(parents=True, exist_ok=True)
    JOBS.mkdir(parents=True, exist_ok=True)
    expire_cache()
    jobs = Jobs(concurrency, processes)
    app = FastAPI(title="HardRead", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.jobs = jobs

    def client_of(request):
        return request.client.host if request.client else "unknown"

    def err(status, msg, **kw):
        return JSONResponse({"ok": False, "error": msg, **kw}, status_code=status)

    @app.middleware("http")
    async def headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/health")
    def health():
        with jobs.lock:
            q = len(jobs.queue)
            r = sum(1 for j in jobs.jobs.values() if j["state"] == "running")
        import bridge_adapter
        return {"ok": True, "queue": q, "running": r, "concurrency": jobs.concurrency,
                "engine_python": FP_PY.exists(), "bridge": "tools/review/bridge.py" if bridge_adapter._B else "stub",
                "showcases": len((_jsonfile(SHOWCASES / "index.json", {}) or {}).get("showcases", []))}

    @app.get("/api/showcases")
    def showcases():
        return _jsonfile(SHOWCASES / "index.json", {"showcases": []})

    @app.post("/api/preflight")
    async def preflight(request: Request):
        body = await _body(request)
        if body is None:
            return err(400, "Send JSON.")
        try:
            rep = resolve_input(body.get("input", ""))
            game = P.inspect_game(rep)
            export = P.parse_export(body.get("team") or "")
        except P.InputError as e:
            return err(422, str(e))
        suggested = P.detect_side(game, export)
        # names go back to the person who pasted the replay (to pick a side); they are not stored or logged
        return {"ok": True, "players": game["names"], "turns": game["turns"], "end_reason": game["end_reason"],
                "winner": game["winner"], "private": bool(rep.get("private")), "suggested_side": suggested,
                "team_species": export}

    @app.post("/api/review")
    async def submit(request: Request):
        body = await _body(request)
        if body is None:
            return err(400, "Send JSON.")
        side = body.get("side")
        try:
            rep = resolve_input(body.get("input", ""))
            game = P.inspect_game(rep)
            lines, ctx, job_input = P.prepare(rep, side, body.get("team"), game)
        except P.InputError as e:
            return err(422, str(e))
        key = P.cache_key(lines, side, job_input.get("team"))
        rid = review_id_for(key)
        if load_review(rid) is not None:
            return {"ok": True, "review_id": rid, "cached": True}
        with jobs.lock:
            existing = jobs.by_key.get(key)
        if existing:
            return {"ok": True, "job_id": existing}
        ok, wait = jobs.allow(client_of(request))
        if not ok:
            return err(429, f"You have reached {RATE_REVIEWS_PER_HOUR} new reviews this hour. "
                            f"Try again in about {max(1, wait // 60)} minutes.")
        jid = jobs.submit(key, lines, ctx, job_input)
        if jid is None:
            return err(503, "The review queue is full right now. Try again in a few minutes.")
        return {"ok": True, "job_id": jid}

    @app.get("/api/job/{jid}")
    def job(jid: str):
        st = jobs.status(jid)
        if st is None:
            return err(404, "No such review job (it may have expired).")
        return st

    @app.get("/api/review/{rid}")
    def review(rid: str):
        v = load_review(rid)
        if v is None:
            return err(404, "This review does not exist or has expired.")
        return v

    @app.get("/api/review/{rid}/receipts")
    def receipts(rid: str):
        r = load_review(rid, raw=True)
        if r is None:
            return err(404, "This review does not exist or has expired.")
        return Response(json.dumps(r, indent=1), media_type="application/json",
                        headers={"Content-Disposition": f'inline; filename="hardread-{rid}.json"'})

    app.mount("/static", StaticFiles(directory=str(APP / "static")), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(APP / "static" / "index.html"))

    @app.on_event("shutdown")
    def _shutdown():
        jobs.shutdown()

    return app


async def _body(request):
    try:
        raw = await request.body()
        if len(raw) > P.MAX_BYTES + P.MAX_EXPORT_BYTES + 4096:
            return None
        d = json.loads(raw.decode("utf-8"))
        return d if isinstance(d, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18051)
    ap.add_argument("--concurrency", type=int, default=1, help="reviews running at once")
    ap.add_argument("--processes", type=int, default=4, help="search processes per review")
    a = ap.parse_args()
    if a.port in (18000, 18002):
        sys.exit("ports 18000 and 18002 belong to another workflow")
    import uvicorn
    uvicorn.run(create_app(a.concurrency, a.processes), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
