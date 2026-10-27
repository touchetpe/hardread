"""HardRead local app: paste a finished Gen 9 OU replay, see the dice separated from the decisions.

Run (from hardread/, system python3 with fastapi + uvicorn):
  python3 app/server.py                      # http://127.0.0.1:18051
  python3 app/server.py --port 18052 --concurrency 1

Background jobs, one game per process, at most --concurrency engine jobs at a time:
  review   app/worker.py -> tools/review/review.py (Foul Play environment)
  verdict  tools/review/verdict.py one (Foul Play environment; needs the team export)
  explain  app/explain_worker.py -> tools/agent/explain.py (base Muse Glimmer via the local llama-server when it
           answers, else deterministic templates; every number is rendered from a cited tool field)
Results are cached under scratch_ds/app/cache/ (public replays 14 days, private replays 24 hours). Nothing is
posted anywhere; the only outbound request is the replay link you paste.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

APP = Path(__file__).resolve().parent
HARDREAD = APP.parent
sys.path.insert(0, str(APP))
import pipeline as P  # noqa: E402
import card as CARD  # noqa: E402

STATE = Path(os.environ.get("HARDREAD_APP_STATE", HARDREAD / "scratch_ds" / "app"))
CACHE = STATE / "cache"
JOBS = STATE / "jobs"
SHOWCASES = APP / "data" / "showcases"
FP_PY = Path(os.environ.get("HARDREAD_FP_PYTHON", HARDREAD.parent / "environments" / "foulplay" / "bin" / "python"))
# the shared local Glimmer server (owned by lanes Ag-LLM / Coord; this app never starts or stops it)
LLM_URL = os.environ.get("HARDREAD_LLM_URL", "http://127.0.0.1:18131").rstrip("/")
GLIMMER_URL = LLM_URL + "/v1/chat/completions"
EVAL_LOCK = HARDREAD / "scratch_ds" / "agent" / "EVAL_LOCK"   # present = a recorded evaluation is running: no calls
LLM_GATE = threading.Lock()   # at most one model call in flight from this app (the server runs --parallel 1)
EXPLAIN_MODE = os.environ.get("HARDREAD_EXPLAIN_MODE", "auto")   # auto | loop | prefetch | template | off
VERDICTS_ON = os.environ.get("HARDREAD_DISABLE_VERDICT", "") != "1"

RATES = {"review": int(os.environ.get("HARDREAD_RATE_REVIEWS", "5")),
         "verdict": int(os.environ.get("HARDREAD_RATE_VERDICTS", "10")),
         "explain": int(os.environ.get("HARDREAD_RATE_EXPLAIN", "30"))}
QUEUE_CAP = int(os.environ.get("HARDREAD_QUEUE_CAP", "50"))
TTL_PUBLIC = 14 * 24 * 3600
TTL_PRIVATE = 24 * 3600
TIMEOUTS = {"review": int(os.environ.get("HARDREAD_JOB_TIMEOUT", "600")), "verdict": 300, "explain": 1800}
QUESTIONS = {   # the three fixed questions (EVAL_PROTOCOL_AGENT.md Q1-Q3); free-form "Ask" is out of scope
    "q1": "Was this game decided by luck or by my choices?",
    "q2": "What was the biggest piece of luck in this game, and how much did it matter?",
    "q3": "Where did the engine disagree most with my choices, and by how much?",
}
_SALT = secrets.token_hex(16)   # per process: hashed client addresses are never written anywhere


def _jsonfile(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def _write_json(p, obj):
    p = Path(p)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, p)


def eval_locked():
    return EVAL_LOCK.exists()


def glimmer_up():
    """True if the local llama-server answers its health endpoint and no recorded evaluation holds it."""
    if eval_locked():
        return False
    try:
        with urllib.request.urlopen(LLM_URL + "/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- jobs

class Jobs:
    """A small queue of subprocess jobs (review / verdict / explain): at most `concurrency` engine jobs at once;
    explanations only call the language model, so they get their own single slot."""

    def __init__(self, concurrency=1, processes=4):
        self.concurrency = concurrency
        self.processes = processes
        self.lock = threading.Lock()
        self.jobs = {}
        self.by_key = {}
        self.queue = deque()
        self.hits = {}
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def allow(self, client, kind="review"):
        now = time.time()
        h = hashlib.sha256((_SALT + client).encode()).hexdigest()[:16]
        with self.lock:
            q = self.hits.setdefault((kind, h), deque())
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= RATES[kind]:
                return False, int(3600 - (now - q[0]))
            q.append(now)
            return True, 0

    def existing(self, key):
        with self.lock:
            return self.by_key.get(key)

    def submit(self, kind, key, files, cmd, on_done):
        """files: {name: text} written into the job dir; cmd(jobdir) -> argv; on_done(job) -> result."""
        with self.lock:
            if key in self.by_key:
                return self.by_key[key]
            if len(self.queue) >= QUEUE_CAP:
                return None
            jid = secrets.token_urlsafe(9)
            d = JOBS / jid
            d.mkdir(parents=True, exist_ok=True)
            for name, text in files.items():
                (d / name).write_text(text)
            self.jobs[jid] = {"id": jid, "kind": kind, "key": key, "dir": d, "cmd": cmd, "on_done": on_done,
                              "state": "queued", "created": time.time(), "proc": None}
            self.by_key[key] = jid
            self.queue.append(jid)
            return jid

    def status(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            if j is None:
                return None
            out = {"id": jid, "kind": j["kind"], "state": j["state"]}
            if j["state"] == "queued":
                lane = [x for x in self.queue if (self.jobs[x]["kind"] == "explain") == (j["kind"] == "explain")]
                out["position"] = lane.index(jid) + 1 if jid in lane else 1
                out["message"] = "Waiting for a free engine slot"
                if j["kind"] == "explain" and eval_locked():
                    out["pending"] = True
                    out["message"] = "Explanation pending: the local model is busy with an evaluation run"
                out["progress"] = 0.0
            elif j["state"] == "running":
                st = _jsonfile(j["dir"] / "status.json", {}) or {}
                out.update({k: st.get(k) for k in ("stage", "progress", "message", "done", "decisions")})
                out["elapsed"] = round(time.time() - j.get("started", time.time()), 1)
            elif j["state"] == "done":
                out["result"] = j.get("result")
                if j["kind"] == "review":
                    out["review_id"] = j.get("result")
            else:
                out["message"] = j.get("message") or "This job failed."
            return out

    def _loop(self):
        while not self._stop:
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                print("job loop error:", type(e).__name__, e, file=sys.stderr)
            time.sleep(0.4)

    def _tick(self):
        with self.lock:
            running = [j for j in self.jobs.values() if j["state"] == "running"]
            eng = sum(1 for j in running if j["kind"] != "explain")
            exp = len(running) - eng
            llm_free = not eval_locked() and not LLM_GATE.locked()
            for jid in list(self.queue):
                j = self.jobs[jid]
                if j["kind"] == "explain" and (exp >= 1 or not llm_free):
                    continue
                if j["kind"] != "explain" and eng >= self.concurrency:
                    continue
                self.queue.remove(jid)
                with open(j["dir"] / "stderr.txt", "w") as errf:
                    j["proc"] = subprocess.Popen(j["cmd"](j["dir"]), cwd=str(HARDREAD), stdout=subprocess.DEVNULL,
                                                 stderr=errf)
                j["state"], j["started"] = "running", time.time()
                running.append(j)
                if j["kind"] == "explain":
                    exp += 1
                else:
                    eng += 1
        for j in running:
            if j["proc"].poll() is None:
                if time.time() - j["started"] > TIMEOUTS[j["kind"]]:
                    _stop_job(j)
                    self._finish(j, False, "This took too long and was stopped.")
                continue
            self._finish(j, j["proc"].returncode == 0)

    def _finish(self, j, ok, message=None):
        result = None
        if ok:
            try:
                result = j["on_done"](j)
            except Exception as e:  # noqa: BLE001
                ok, message = False, "The job finished but its result could not be read."
                print("finish error:", j["kind"], type(e).__name__, e, file=sys.stderr)
        with self.lock:
            j["state"] = "done" if ok else "error"
            j["result"] = result
            j["message"] = message
            j["finished"] = time.time()
            self.by_key.pop(j["key"], None)
        for f in ("replay.json", "input.json", "team.txt", "review.json", "belief.json", "verdicts.json"):
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
    """Stop only the processes this server started (the job's own process and its recorded child)."""
    try:
        pid_file = j["dir"] / "review.pid"
        if pid_file.exists():
            os.kill(int(pid_file.read_text().strip()), 15)
    except (OSError, ValueError):
        pass
    try:
        j["proc"].terminate()
    except OSError:
        pass


# ---------------------------------------------------------------- cache

def review_id_for(key):
    return "r" + key[:19]


def is_showcase(rid):
    return rid.startswith("showcase-") and rid[9:].isdigit()


def cache_dir(rid):
    if rid.startswith("r") and rid[1:].isalnum() and len(rid) == 20:
        return CACHE / rid
    return None


def store_review(key, review, ctx, jobdir=None):
    """Cache review.py output + app context (the view is rebuilt per request). Export-mode reviews also keep the
    pseudonymised log and the team export (same retention) so a decision can be checked later."""
    rid = review_id_for(key)
    d = CACHE / rid
    d.mkdir(parents=True, exist_ok=True)
    review = dict(review)
    review["meta"] = dict(review["meta"], players={"p1": "Player1", "p2": "Player2"})
    private = bool(ctx.get("private"))
    _write_json(d / "review.json", review)
    _write_json(d / "ctx.json", ctx)
    _write_json(d / "meta.json", {"created": time.time(), "private": private, "shared": False})
    if jobdir is not None and ctx.get("mode") == "export":
        for f in ("replay.json", "team.txt"):
            if (jobdir / f).exists():
                shutil.copyfile(jobdir / f, d / f)
    return rid


def _live_meta(rid):
    d = cache_dir(rid)
    if d is None:
        return None
    m = _jsonfile(d / "meta.json")
    if not m or time.time() - m["created"] > (TTL_PRIVATE if m.get("private") else TTL_PUBLIC):
        return None
    return m


def load_parts(rid):
    """(review, ctx, extra) for a showcase or a live cached review, else None."""
    if is_showcase(rid):
        review = _jsonfile(SHOWCASES / f"{rid}.review.json")
        if review is None:
            return None
        return review, _jsonfile(SHOWCASES / f"{rid}.ctx.json", {}) or {}, {"showcase": True, "private": False,
                                                                            "shared": True}
    m = _live_meta(rid)
    if m is None:
        return None
    review = _jsonfile(cache_dir(rid) / "review.json")
    if review is None:
        return None
    return review, _jsonfile(cache_dir(rid) / "ctx.json", {}) or {}, {"private": bool(m.get("private")),
                                                                      "shared": bool(m.get("shared"))}


def verdicts_of(rid):
    """Latest raw verdict.py result per decision ("turn:k")."""
    d = cache_dir(rid)
    out = {}
    if d is None or not (d / "verdicts").exists():
        return out
    for p in sorted((d / "verdicts").glob("*.json"), key=lambda p: p.stat().st_mtime):
        v = _jsonfile(p)
        if v:
            out[f"{v['turn']}:{v['k']}"] = v
    return out


def explain_store(rid):
    return (cache_dir(rid) / "explain.json") if cache_dir(rid) else (SHOWCASES / f"{rid}.explain.json")


def load_view(rid):
    parts = load_parts(rid)
    if parts is None:
        return None
    review, ctx, extra = parts
    view = P.build_view(review, ctx)
    view.update(extra, id=rid)
    d = cache_dir(rid)
    view["features"] = {"verdict": bool(VERDICTS_ON and ctx.get("mode") == "export" and d is not None
                                        and (d / "team.txt").exists()),
                        "explain": EXPLAIN_MODE != "off",
                        "share": not extra.get("private")}
    view["verdicts"] = {k: P.verdict_card(v, ctx, review) for k, v in verdicts_of(rid).items()}
    view["explanations"] = _jsonfile(explain_store(rid), {}) or {}
    view["questions"] = QUESTIONS
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


_AGENT = {}


def _agent(name):
    """Import a tools/agent module (lane Ag-LLM) lazily; raises if it is not there."""
    if name not in _AGENT:
        p = str(HARDREAD / "tools" / "agent")
        if p not in sys.path:
            sys.path.insert(0, p)
        _AGENT[name] = __import__(name)
    return _AGENT[name]


# ---------------------------------------------------------------- app

def create_app(concurrency=1, processes=4):
    CACHE.mkdir(parents=True, exist_ok=True)
    JOBS.mkdir(parents=True, exist_ok=True)
    expire_cache()
    jobs = Jobs(concurrency, processes)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        jobs.shutdown()

    app = FastAPI(title="HardRead", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.jobs = jobs

    def client_of(request):
        return request.client.host if request.client else "unknown"

    def err(status, msg, **kw):
        return JSONResponse({"ok": False, "error": msg, **kw}, status_code=status)

    blocked = set()
    bl = os.environ.get("HARDREAD_BLOCKLIST")   # optional file: one client address per line (abuse control)
    if bl and Path(bl).exists():
        blocked = {x.strip() for x in Path(bl).read_text().splitlines() if x.strip() and not x.startswith("#")}

    @app.middleware("http")
    async def headers(request, call_next):
        if blocked and request.method == "POST" and client_of(request) in blocked:
            return JSONResponse({"ok": False, "error": "Not available from this address."}, status_code=403)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/card/"):
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
                "disagreement_rule": "tools/review/select_offered.py" if P._SO else "app",
                "verdicts": VERDICTS_ON, "explain_mode": EXPLAIN_MODE, "glimmer": glimmer_up(),
                "llm_url": LLM_URL, "eval_lock": eval_locked(),
                "showcases": len((_jsonfile(SHOWCASES / "index.json", {}) or {}).get("showcases", []))}

    @app.get("/api/showcases")
    def showcases():
        return _jsonfile(SHOWCASES / "index.json", {"showcases": []})

    # ---- review
    @app.post("/api/preflight")
    async def preflight(request: Request):
        body = await _body(request)
        if body is None:
            return err(400, "Send JSON.")
        try:
            rep = await run_in_threadpool(resolve_input, body.get("input", ""))
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
            rep = await run_in_threadpool(resolve_input, body.get("input", ""))
            game = P.inspect_game(rep)
            lines, ctx, job_input = P.prepare(rep, side, body.get("team"), game)
        except P.InputError as e:
            return err(422, str(e))
        key = P.cache_key(lines, side, job_input.get("team"))
        rid = review_id_for(key)
        if load_parts(rid) is not None:
            return {"ok": True, "review_id": rid, "cached": True}
        jid = jobs.existing("review:" + key)
        if jid:
            return {"ok": True, "job_id": jid}
        ok, wait = jobs.allow(client_of(request), "review")
        if not ok:
            return err(429, f"You have reached {RATES['review']} new reviews this hour. "
                            f"Try again in about {max(1, wait // 60)} minutes.")
        files = {"replay.json": json.dumps({"log": "\n".join(lines)}),
                 "input.json": json.dumps(dict(job_input, processes=jobs.processes))}
        if job_input.get("team"):
            files["team.txt"] = job_input["team"]

        def done(j, key=key, ctx=ctx):
            review = json.loads((j["dir"] / "review.json").read_text())
            P.build_view(review, ctx)   # fail here, not on the first page view
            return store_review(key, review, ctx, j["dir"])
        jid = jobs.submit("review", "review:" + key, files,
                          lambda d: [str(FP_PY), str(APP / "worker.py"), str(d)], done)
        if jid is None:
            return err(503, "The review queue is full right now. Try again in a few minutes.")
        return {"ok": True, "job_id": jid}

    @app.get("/api/job/{jid}")
    def job(jid: str):
        st = jobs.status(jid)
        if st is None:
            return err(404, "No such job (it may have expired).")
        return st

    @app.get("/api/review/{rid}")
    def review(rid: str):
        v = load_view(rid)
        if v is None:
            return err(404, "This review does not exist or has expired.")
        return v

    @app.get("/api/review/{rid}/receipts")
    def receipts(rid: str):
        parts = load_parts(rid)
        if parts is None:
            return err(404, "This review does not exist or has expired.")
        return Response(json.dumps(parts[0], indent=1), media_type="application/json",
                        headers={"Content-Disposition": f'inline; filename="hardread-{rid}.json"'})

    # ---- the player's read: note -> chips (tools/agent/parse_note.py; Glimmer when up, keyword parser otherwise)
    @app.post("/api/note/parse")
    async def parse_note(request: Request):
        body = await _body(request)
        if body is None:
            return err(400, "Send JSON.")
        parts = load_parts(str(body.get("review_id", "")))
        if parts is None:
            return err(404, "This review does not exist or has expired.")
        note = str(body.get("note") or "").strip()[:600]
        if not note:
            return err(422, "Write what you were thinking first.")
        own, opp = P.species_lists(parts[1])
        try:
            PN = _agent("parse_note")
        except Exception:  # noqa: BLE001
            return err(503, "The note reader is not available in this build; add the details by hand below.")
        try:   # your own revealed moves are never credited to the opponent (parse-note-v3)
            own_moves = PN.own_moves_from_review(parts[0])
        except Exception:  # noqa: BLE001
            own_moves = None
        def read_note():
            explaining = any(j["kind"] == "explain" and j["state"] == "running" for j in list(jobs.jobs.values()))
            if not explaining and glimmer_up() and LLM_GATE.acquire(blocking=False):
                try:   # one call in flight; short timeout; the keyword reader answers at once otherwise
                    from llm import GlimmerClient
                    if not eval_locked():
                        return PN.parse(note, own, opp, parser="glimmer",
                                        client=GlimmerClient(GLIMMER_URL, max_tokens=1024, timeout=25),
                                        own_moves=own_moves)
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    LLM_GATE.release()
            return PN.parse(note, own, opp, parser="keyword", own_moves=own_moves)
        res = await run_in_threadpool(read_note)   # never block the event loop on the model
        return {"ok": True, "chips": res["chips"], "dropped": res["dropped"], "parser": res["parser"]}

    @app.get("/api/options/{rid}")
    def options(rid: str):
        """What a person can pick when adding a detail by hand: the opponent's six and their common set fields."""
        parts = load_parts(rid)
        if parts is None:
            return err(404, "This review does not exist or has expired.")
        return {"ok": True, **P.belief_options(parts[1])}

    # ---- verdicts (tools/review/verdict.py one; export mode only)
    @app.post("/api/verdict")
    async def verdict(request: Request):
        if not VERDICTS_ON:
            return err(503, "Decision checks are switched off on this server.")
        body = await _body(request)
        if body is None:
            return err(400, "Send JSON.")
        rid = str(body.get("review_id", ""))
        parts = load_parts(rid)
        d = cache_dir(rid)
        if parts is None:
            return err(404, "This review does not exist or has expired.")
        if d is None:
            return err(422, "Showcases use assumed sets; checking a decision needs your own game and team export.")
        review, ctx, _ = parts
        if ctx.get("mode") != "export" or not (d / "team.txt").exists():
            return err(422, "Checking a decision needs your team export: review the game again with it.")
        try:
            turn, k = int(body.get("turn")), int(body.get("k", 0))
        except (TypeError, ValueError):
            return err(422, "Which decision?")
        dec = next((x for x in review["decisions"] if x["turn"] == turn and x["k"] == k), None)
        if dec is None or not dec.get("action") or not dec.get("engine_best"):
            return err(422, "That decision cannot be checked (the log hides your move).")
        belief = P.clean_belief(body.get("belief") or {})
        bkey = hashlib.sha256(json.dumps([rid, turn, k, belief], sort_keys=True).encode()).hexdigest()[:20]
        vp = d / "verdicts" / f"{turn}_{k}_{bkey}.json"
        if vp.exists():
            os.utime(vp)   # becomes the latest check for this decision
            return {"ok": True, "cached": True, "key": f"{turn}:{k}"}
        jid = jobs.existing("verdict:" + bkey)
        if jid:
            return {"ok": True, "job_id": jid}
        ok, _ = jobs.allow(client_of(request), "verdict")
        if not ok:
            return err(429, f"You have reached {RATES['verdict']} decision checks this hour.")
        x = dec["engine_best"]

        def cmd(jd, turn=turn, k=k, x=x):
            return [str(FP_PY), str(HARDREAD / "tools" / "review" / "verdict.py"), "one", str(d / "replay.json"),
                    "--team", str(d / "team.txt"), "--side", ctx["side"], "--turn", str(turn), "--k", str(k),
                    "--x", x, "--belief", str(jd / "belief.json"), "--out", str(jd / "verdict.json")]

        def done(j, vp=vp, belief=belief):
            v = json.loads((j["dir"] / "verdict.json").read_text())
            v["app_belief"] = belief       # the full read as entered (incl. the expected action, for the read check)
            vp.parent.mkdir(parents=True, exist_ok=True)
            _write_json(vp, v)
            return f"{v['turn']}:{v['k']}"
        jid = jobs.submit("verdict", "verdict:" + bkey, {"belief.json": json.dumps(belief)}, cmd, done)
        if jid is None:
            return err(503, "The queue is full right now. Try again in a few minutes.")
        return {"ok": True, "job_id": jid}

    @app.get("/api/review/{rid}/verdict/{turn}/{k}")
    def verdict_receipt(rid: str, turn: int, k: int):
        """The raw verdict.py output behind a check (receipts)."""
        if load_parts(rid) is None:
            return err(404, "This review does not exist or has expired.")
        v = verdicts_of(rid).get(f"{turn}:{k}")
        if v is None:
            return err(404, "No check for this decision yet.")
        return Response(json.dumps(v, indent=1), media_type="application/json")

    # ---- explanation (tools/agent/explain.py, async)
    @app.post("/api/explain")
    async def explain(request: Request):
        if EXPLAIN_MODE == "off":
            return err(503, "Explanations are switched off on this server.")
        body = await _body(request)
        if body is None:
            return err(400, "Send JSON.")
        rid = str(body.get("review_id", ""))
        qid = str(body.get("question", "q1"))
        if qid not in QUESTIONS:
            return err(422, "Unknown question.")
        parts = load_parts(rid)
        if parts is None:
            return err(404, "This review does not exist or has expired.")
        store = explain_store(rid)
        have = _jsonfile(store, {}) or {}
        if qid in have:
            return {"ok": True, "cached": True}
        key = f"explain:{rid}:{qid}"
        template = await run_in_threadpool(_template_explanation, parts[0], QUESTIONS[qid], verdicts_of(rid))
        jid = jobs.existing(key)
        if jid:
            return {"ok": True, "job_id": jid, "template": template}
        ok, _ = jobs.allow(client_of(request), "explain")
        if not ok:
            return err(429, "Too many explanation requests this hour.")
        if EXPLAIN_MODE != "auto":
            mode = EXPLAIN_MODE
        elif eval_locked():
            mode = "loop"   # queued until the evaluation lock is gone; the template is shown meanwhile
        else:
            mode = "loop" if await run_in_threadpool(glimmer_up) else "template"
        if mode == "template" and template is not None:   # nothing to wait for
            cur = _jsonfile(store, {}) or {}
            cur[qid] = template
            _write_json(store, cur)
            return {"ok": True, "cached": True}

        def cmd(jd, mode=mode):
            return [sys.executable, str(APP / "explain_worker.py"), str(jd), "--mode", mode, "--url", GLIMMER_URL,
                    "--lock", str(EVAL_LOCK)]

        def done(j, store=store, qid=qid, review=parts[0], rid=rid):
            res = json.loads((j["dir"] / "explain.json").read_text())
            if res.get("pending_eval_lock"):   # stopped by an evaluation lock: not cached, a later visit retries
                return "pending"
            res = wording_guard(res, review, QUESTIONS[qid], verdicts_of(rid))
            cur = _jsonfile(store, {}) or {}
            cur[qid] = res
            _write_json(store, cur)
            return qid
        files = {"review.json": json.dumps(parts[0]), "question.txt": QUESTIONS[qid],
                 "verdicts.json": json.dumps(verdicts_of(rid))}
        jid = jobs.submit("explain", key, files, cmd, done)
        if jid is None:
            return err(503, "The queue is full right now.")
        return {"ok": True, "job_id": jid, "template": template}

    # ---- provenance (the Open Weights evidence: which model, which build, which engine)
    @app.get("/api/provenance")
    def provenance():
        prov = _jsonfile(APP / "data" / "provenance.json", {}) or {}

        def git(*args, cwd=HARDREAD):
            try:
                return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                                      timeout=5).stdout.strip() or None
            except Exception:  # noqa: BLE001
                return None
        import bridge_adapter
        return {"ok": True, **prov,
                "live": {"hardread_commit": git("rev-parse", "--short", "HEAD"),
                         "foulplay_commit": git("rev-parse", "--short", "HEAD", cwd=HARDREAD / "foul-play"),
                         "explainer_reachable": glimmer_up(), "explain_mode": EXPLAIN_MODE,
                         "bridge": "tools/review/bridge.py" if bridge_adapter._B else "stub",
                         "disagreement_rule": "tools/review/select_offered.py" if P._SO else "app"}}

    # ---- share (opt-in, public replays only)
    @app.post("/api/share/{rid}")
    def share(rid: str):
        parts = load_parts(rid)
        if parts is None:
            return err(404, "This review does not exist or has expired.")
        if parts[2].get("private"):
            return err(403, "Private replays never get a share page.")
        d = cache_dir(rid)
        if d is not None:
            m = _jsonfile(d / "meta.json")
            m["shared"] = True
            _write_json(d / "meta.json", m)
        return {"ok": True, "url": f"/r/{rid}", "card": f"/api/card/{rid}.svg"}

    def shared_view(rid):
        v = load_view(rid)
        if v is None or v.get("private") or not v.get("shared"):
            return None
        return v

    @app.get("/api/card/{rid}.svg")
    def card(rid: str):
        v = shared_view(rid)
        if v is None:
            return err(404, "No share card for this review.")
        return Response(CARD.svg(v), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=300"})

    @app.get("/r/{rid}")
    def share_page(rid: str):
        v = shared_view(rid)
        if v is None:
            return HTMLResponse("<!doctype html><title>Not found</title><p>This share link does not exist.</p>",
                                status_code=404)
        html = (APP / "static" / "index.html").read_text()
        og = (f'<meta property="og:title" content="{CARD.esc(CARD.title(v))}">\n'
              f'<meta property="og:description" content="{CARD.esc(CARD.one_line(v))}">\n'
              f'<meta property="og:image" content="/api/card/{rid}.svg">\n'
              f'<meta name="twitter:card" content="summary_large_image">\n')
        return HTMLResponse(html.replace("</head>", og + "</head>", 1))

    app.mount("/static", StaticFiles(directory=str(APP / "static")), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(APP / "static" / "index.html"))

    return app


# product wording (research/28 B): never "cost" for choices, never mistake/misplay words outside a verdict card,
# never coach/trainer/analyzer. A model sentence that uses them is not shown; the template is, labelled as such.
NEVER_SAY = re.compile(r"\b(cost|costs|costed|costing|mistakes?|misplay(?:s|ed)?|blunder(?:s|ed)?|errors?|"
                       r"coach(?:es|ing)?|trainer|analy[sz]er|throw(?:s|n)?)\b", re.I)


def wording_guard(res, review, question, verdicts):
    text = ((res.get("rendered") or {}).get("text")) or ""
    if res.get("final") == "template" or not NEVER_SAY.search(text):
        return res
    tpl = _template_explanation(review, question, verdicts) or {}
    out = dict(tpl, final="template", policy_rejected=True, requested_mode=res.get("requested_mode"),
               rejected={"final": res.get("final"), "text": text, "matched": NEVER_SAY.findall(text)})
    return out


def _template_explanation(review, question, verdicts):
    """The deterministic template answer (instant; shown while Glimmer writes, and as its fallback)."""
    try:
        ex = _agent("explain")
        t0 = time.time()
        res = ex.run(review, question, mode="template", verdicts=verdicts or None)
        return {"question": question, "requested_mode": "template", "final": "template", "rendered": res.get("rendered"),
                "latency_s": round(time.time() - t0, 3), "trace": res.get("trace"), "created": time.time()}
    except Exception as e:  # noqa: BLE001
        print("template explanation failed:", type(e).__name__, e, file=sys.stderr)
        return None


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
    ap.add_argument("--concurrency", type=int, default=1, help="engine jobs (reviews, checks) running at once")
    ap.add_argument("--processes", type=int, default=4, help="search processes per review")
    a = ap.parse_args()
    if a.port in (18000, 18002):
        sys.exit("ports 18000 and 18002 belong to another workflow")
    import uvicorn
    uvicorn.run(create_app(a.concurrency, a.processes), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
