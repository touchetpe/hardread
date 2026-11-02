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
import ipaddress
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
sys.path.insert(0, str(HARDREAD / "tools" / "review"))
import pipeline as P  # noqa: E402
import card as CARD  # noqa: E402
import own_ladder_review as OWN  # noqa: E402
import verdict_identity as VI  # noqa: E402

STATE = Path(os.environ.get("HARDREAD_APP_STATE", HARDREAD / "scratch_ds" / "app"))
CACHE = STATE / "cache"
JOBS = STATE / "jobs"
SHOWCASES = APP / "data" / "showcases"
FP_PY = Path(os.environ.get("HARDREAD_FP_PYTHON", HARDREAD.parent / "environments" / "foulplay" / "bin" / "python"))
VERDICT_SCRIPT = HARDREAD / "tools" / "review" / "verdict.py"
# the shared local Glimmer server (owned by lanes Ag-LLM / Coord; this app never starts or stops it)
LLM_URL = os.environ.get("HARDREAD_LLM_URL", "http://127.0.0.1:18131").rstrip("/")
GLIMMER_URL = LLM_URL + "/v1/chat/completions"
EVAL_LOCK = HARDREAD / "scratch_ds" / "agent" / "EVAL_LOCK"   # present = a recorded evaluation is running: no calls
LLM_GATE = threading.Lock()   # at most one model call in flight from this app (the server runs --parallel 1)
EXPLAIN_MODE = os.environ.get("HARDREAD_EXPLAIN_MODE", "auto")   # auto | loop | prefetch | template | off
EXPLANATION_POLICY_VERSION = "offered-scope-v2"
SHOW_SAVED_ANSWERS = True
# Public copy (reached only through the tunnel): public replay links only, no owner-only or path-revealing endpoints.
PUBLIC = os.environ.get("HARDREAD_PUBLIC") == "1"
PUBLIC_ORIGIN = os.environ.get("HARDREAD_PUBLIC_ORIGIN", "")   # the static site, allowed to read /api/health
PUBLIC_REVIEWS_PER_HOUR = int(os.environ.get("HARDREAD_PUBLIC_REVIEWS_PER_HOUR", "60"))
PUBLIC_BLOCKED = re.compile(r"^/(api/own-ladder|api/provenance|api/share/|api/card/|r/|api/review/[^/]+/receipts)")
PUBLIC_EXPLAINS_PER_HOUR = int(os.environ.get("HARDREAD_PUBLIC_EXPLAINS_PER_HOUR", "30"))
PUBLIC_MAX_BODY = 32_000
PUBLIC_FETCHES_PER_HOUR = 200   # replay downloads: staying polite keeps this address welcome on the replay server
PUBLIC_NOTES_PER_HOUR = 60
_public_reviews = deque()
_public_explains = deque()
_public_fetches = deque()
_public_notes = deque()
_public_lock = threading.Lock()


def _client_key(address):
    """One person on IPv6 usually controls a whole /64, so limits apply to the /64."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 else address


def within_hourly_cap(events, cap):
    now = time.time()
    with _public_lock:
        while events and now - events[0] > 3600:
            events.popleft()
        if len(events) >= cap:
            return False
        events.append(now)
        return True
VERDICTS_ON = os.environ.get("HARDREAD_DISABLE_VERDICT", "") != "1"

RATES = {"review": int(os.environ.get("HARDREAD_RATE_REVIEWS", "5")),
         "verdict": int(os.environ.get("HARDREAD_RATE_VERDICTS", "10")),
         "explain": int(os.environ.get("HARDREAD_RATE_EXPLAIN", "30")),
         "fetch": int(os.environ.get("HARDREAD_RATE_FETCH", "40")),
         "note": int(os.environ.get("HARDREAD_RATE_NOTE", "20"))}
QUEUE_CAP = int(os.environ.get("HARDREAD_QUEUE_CAP", "50"))
TTL_PUBLIC = 14 * 24 * 3600
TTL_PRIVATE = 24 * 3600
TIMEOUTS = {"review": int(os.environ.get("HARDREAD_JOB_TIMEOUT", "600")), "verdict": 300, "explain": 1800}
QUESTIONS = {   # the three fixed questions (EVAL_PROTOCOL_AGENT.md Q1-Q3); free-form "Ask" is out of scope
    "q1": "Was this game decided by luck or by my choices?",
    "q2": "What was the biggest piece of luck in this game, and how much did it matter?",
    "q3": "Where did the engine disagree most with my choices, and by how much?",
    "q4": "What was the turning point of this game?",
}
# What the model is asked, when it differs from the label on the button. q4 is an investigation: it must look up the turn.
AGENT_QUESTIONS = {
    "q4": ("What was the turning point of this game? Call list_key_moments and then get_turn on the most important "
           "turn before you answer. In three or four sentences, say what happened on that turn, how win chance moved, "
           "and what the engine preferred if it disagreed."),
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

    def active_for(self, client):
        """Does this client already have a job queued or running? (public copy: one at a time)"""
        h = hashlib.sha256((_SALT + client).encode()).hexdigest()[:16]
        with self.lock:
            return any(j.get("client") == h and j["state"] in ("queued", "running") for j in self.jobs.values())

    def submit(self, kind, key, files, cmd, on_done, client=None):
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
                              "state": "queued", "created": time.time(), "proc": None,
                              "client": hashlib.sha256((_SALT + client).encode()).hexdigest()[:16] if client else None}
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
        last_expire = time.time()
        while not self._stop:
            try:
                self._tick()
                if time.time() - last_expire > 3600:
                    last_expire = time.time()
                    expire_cache()
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
                                                 stderr=errf, start_new_session=True)
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
            ok = j["proc"].returncode == 0
            st = {} if ok else (_jsonfile(j["dir"] / "status.json", {}) or {})
            # the worker's own sentence ("The engine could not review this game.") beats a generic failure
            self._finish(j, ok, st.get("message") if st.get("state") == "error" else None)

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
        for f in ("replay.json", "input.json", "team.txt", "review.json", "belief.json", "identity.json", "verdicts.json"):
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
        os.killpg(j["proc"].pid, 15)   # the job leads its own process group, so its search workers stop too
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


def verdicts_of(rid, expected_identities=None):
    """Read only verdict results whose full request identities were supplied by the caller.

    An explanation request has no active stated-read identity, so its caller must
    not pick one of several beliefs for the same offered decision by file mtime.
    """
    d = cache_dir(rid)
    out = {}
    if d is None or not expected_identities or not (d / "verdicts").exists():
        return out
    for p in sorted((d / "verdicts").glob("*.json"), key=lambda p: p.stat().st_mtime):
        v = _jsonfile(p)
        if not isinstance(v, dict):
            continue
        identity, digest = v.get("cache_identity"), v.get("cache_identity_sha256")
        try:
            if expected_identities.get(f"{v['turn']}:{v['k']}") != digest:
                continue
            if not digest or p.name != VI.cache_filename(v["turn"], v["k"], digest):
                continue
            VI.validate_result(v, identity, digest, require_app_belief=True)
        except (KeyError, TypeError, ValueError):
            continue
        out[f"{v['turn']}:{v['k']}"] = v
    return out


_runtime_provenance = {"at": 0, "manifest": None}


def _engine_runtime_provenance():
    """Ask the pinned verdict interpreter for hashes only; this command runs no search."""
    if PUBLIC and _runtime_provenance["manifest"] and time.time() - _runtime_provenance["at"] < 600:
        return _runtime_provenance["manifest"]   # the public image doesn't change, and this starts a process
    try:
        result = subprocess.run([str(FP_PY), str(VERDICT_SCRIPT), "fingerprint"], cwd=str(HARDREAD),
                                capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("the verdict runtime provenance check did not complete") from exc
    if result.returncode != 0:
        raise ValueError("the verdict runtime provenance check failed")
    try:
        manifest = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("the verdict runtime returned invalid provenance JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != VI.RUNTIME_SCHEMA:
        raise ValueError("the verdict runtime returned an unsupported provenance manifest")
    _runtime_provenance.update(at=time.time(), manifest=manifest)
    return manifest


def explain_store(rid):
    return (cache_dir(rid) / "explain.json") if cache_dir(rid) else (STATE / "showcase-explanations" / f"{rid}.json")


def explain_key_store(rid):
    d = cache_dir(rid)
    return d / "explain-offered-keys.json" if d else STATE / "explain-offered-keys" / f"{rid}.json"


def _offered_keys(offered):
    return [{"turn": d["turn"], "k": d.get("k", 0)} for d in offered]


def _explanation_offered(review, ctx, provided_keys):
    """Validate the browser's displayed keys and retain the exact raw list used by the view."""
    view, offered = P.build_view_with_offered(review, ctx)
    keys = _offered_keys(offered)
    if not isinstance(provided_keys, list) or provided_keys != keys:
        raise ValueError("visible disagreement keys are stale")
    return view, offered, keys


TEMPLATE_CACHE_VERSION = "deterministic-template-v1"


def _effective_explain_mode():
    if EXPLAIN_MODE != "auto":
        return EXPLAIN_MODE
    return "loop" if eval_locked() or glimmer_up() else "template"


def _save_explain_keys(rid, qid, keys, mode):
    path = explain_key_store(rid)
    current = _jsonfile(path, {}) or {}
    current[qid] = {"offered_keys": keys, "policy_version": EXPLANATION_POLICY_VERSION, "mode": mode}
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, current)


def _explanation_cache_current(explanation, identity, offered_keys, mode):
    expected = {"offered_keys": offered_keys, "policy_version": EXPLANATION_POLICY_VERSION, "mode": mode}
    return (isinstance(explanation, dict) and explanation.get("policy_version") == EXPLANATION_POLICY_VERSION
            and identity == expected and (mode != "template" or
                (explanation.get("final") == "template" and explanation.get("requested_mode") == "template"
                 and explanation.get("template_cache_version") == TEMPLATE_CACHE_VERSION)))


def _public_explanation(explanation, mode):
    if mode != "template":
        return explanation
    # Product templates expose only fields created by the deterministic path.
    return {key: explanation[key] for key in ("question", "final", "rendered", "policy_version")
            if key in explanation}


def _visible_verdicts(offered, verdicts):
    """Keep cached decision checks inside the same offered-key boundary as explanation tools."""
    keys = {f"{d['turn']}:{d.get('k', 0)}" for d in offered if isinstance(d, dict) and "turn" in d}
    return {key: value for key, value in (verdicts or {}).items() if key in keys}


def load_view(rid):
    parts = load_parts(rid)
    if parts is None:
        return None
    review, ctx, extra = parts
    view, offered = P.build_view_with_offered(review, ctx)
    view.update(extra, id=rid)
    d = cache_dir(rid)
    view["features"] = {"verdict": bool(VERDICTS_ON and ctx.get("mode") == "export" and d is not None
                                        and (d / "team.txt").exists()),
                        "explain": EXPLAIN_MODE != "off",
                        "share": not extra.get("private") and not PUBLIC, "receipts": not PUBLIC}
    # A view has no current stated-read identity, so no cached decision result
    # can safely be presented as the answer to a read that has not been checked.
    view["verdicts"] = {}
    cached = _jsonfile(explain_store(rid), {}) or {}
    saved_keys = _jsonfile(explain_key_store(rid), {}) or {}
    keys = _offered_keys(offered)
    mode = _effective_explain_mode()
    view["explanations"] = {q: _public_explanation(ex, mode) for q, ex in cached.items()
                             if _explanation_cache_current(ex, saved_keys.get(q), keys, mode)}
    if SHOW_SAVED_ANSWERS and is_showcase(rid):   # saved model answers: a showcase never waits, and needs no GPU
        saved = _jsonfile(SHOWCASES / f"{rid}.explain.json", {}) or {}
        for q, ex in saved.items():
            if q in QUESTIONS and ex.get("final") != "template":
                view["explanations"][q] = dict(ex, precomputed=True)
        view["plain_compare"] = _jsonfile(SHOWCASES / f"{rid}.plain.json")
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
    if not isinstance(text, str):
        raise P.InputError("Paste a replay link.")
    kind, val, private = P.classify_input(text)
    if PUBLIC and (kind != "url" or private):
        raise P.InputError("This public copy only reviews public replay links.")
    if kind == "url":
        hit = _FETCHED.get(val)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        if PUBLIC and not within_hourly_cap(_public_fetches, PUBLIC_FETCHES_PER_HOUR):
            raise P.InputError("This public copy has fetched enough replays for this hour. The showcases still work; try again later.")
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
        if PUBLIC:   # everyone arrives through the tunnel, so the address is in its header
            return _client_key(request.headers.get("cf-connecting-ip") or "unknown")
        return request.client.host if request.client else "unknown"

    def public_review_allowed():
        return within_hourly_cap(_public_reviews, PUBLIC_REVIEWS_PER_HOUR)

    def public_fetch_refused(request):
        """Public copy: limit each client before any replay is downloaded."""
        if PUBLIC and not jobs.allow(client_of(request), "fetch")[0]:
            return err(429, "You have checked a lot of replays this hour. The showcases still work; try again later.")
        return None

    def one_at_a_time(request):
        if PUBLIC and jobs.active_for(client_of(request)):
            return err(429, "You already have a job running on this public copy. Try again when it finishes.")
        return None

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
        if PUBLIC and PUBLIC_BLOCKED.match(request.url.path):
            return JSONResponse({"ok": False, "error": "Not available on this public copy."}, status_code=404)
        if request.method == "POST" and _too_large(request.headers.get("content-length")):
            return JSONResponse({"ok": False, "error": "That is too large. Paste one replay link."
                                 if PUBLIC else "That is larger than 1 MB. Paste one finished game."}, status_code=413)
        resp = await call_next(request)
        if PUBLIC_ORIGIN and request.url.path == "/api/health":
            resp.headers["Access-Control-Allow-Origin"] = PUBLIC_ORIGIN
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
        if PUBLIC:
            return {"ok": True, "queue": q, "running": r}
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

    @app.get("/api/own-ladder")
    def own_ladder():
        # Only a separately admitted, sanitized projection is accepted here.
        path = os.environ.get("HARDREAD_OWN_LADDER_PROJECTION")
        try:
            if path:
                with open(path, "rb") as source:
                    projection_bytes = source.read(4_000_001)
                if len(projection_bytes) > 4_000_000:
                    raise ValueError("projection too large")
                view = OWN.build_projection(json.loads(projection_bytes))
                sidecar_path = os.environ.get("HARDREAD_OWN_LADDER_SIDECAR")
                sidecar_sha = os.environ.get("HARDREAD_OWN_LADDER_SIDECAR_SHA256")
                receipt_sha = os.environ.get("HARDREAD_OWN_LADDER_RECEIPT_SHA256")
                if sidecar_path and sidecar_sha and receipt_sha:
                    try:
                        import own_ladder_selection as selection
                        with open(sidecar_path, "rb") as source:
                            sidecar_bytes = source.read(1_000_001)
                        if len(sidecar_bytes) > 1_000_000 or selection.digest_bytes(sidecar_bytes) != sidecar_sha:
                            raise ValueError("sidecar differs")
                        selector_sha = selection.digest_bytes(Path(selection.__file__).read_bytes())
                        view = selection.apply_sidecar(view, selection.digest_bytes(projection_bytes), selector_sha,
                                                       receipt_sha, json.loads(sidecar_bytes))
                    except (OSError, ValueError, TypeError, KeyError):
                        pass  # Fail to the original template-only view.
                post_path = os.environ.get("HARDREAD_OWN_LADDER_POSTGAME_SIDECAR")
                post_sha = os.environ.get("HARDREAD_OWN_LADDER_POSTGAME_SHA256")
                native_sha = os.environ.get("HARDREAD_OWN_LADDER_NATIVE_RECEIPT_SHA256")
                mapping_sha = os.environ.get("HARDREAD_OWN_LADDER_NATIVE_MAPPING_SHA256")
                if post_path and post_sha and native_sha and mapping_sha:
                    try:
                        import own_ladder_postgame as postgame
                        with open(post_path, "rb") as source:
                            post_bytes = source.read(4_000_001)
                        if len(post_bytes) > 4_000_000 or postgame.digest(post_bytes) != post_sha:
                            raise ValueError("postgame sidecar differs")
                        view = postgame.apply_sidecar(view, postgame.read_json(post_bytes),
                                                      postgame.digest(projection_bytes), mapping_sha,
                                                      native_sha, postgame.own_hash())
                    except (OSError, ValueError, TypeError, KeyError):
                        pass  # Recorded facts and selector results remain unchanged.
                return view
        except (OSError, ValueError, TypeError):
            pass
        return {"games": [{"alias": alias, "decisions": []} for alias in ("G1", "G2", "G3")],
                "mode": "template-only"}

    # ---- review
    @app.post("/api/preflight")
    async def preflight(request: Request):
        body = await _body(request)
        if body is None:
            return err(400, "Send JSON.")
        refused = public_fetch_refused(request)
        if refused:
            return refused
        try:
            rep = await run_in_threadpool(resolve_input, body.get("input", ""))
            game = P.inspect_game(rep)
            export = P.parse_export(_text_field(body.get("team")))
            suggested = P.detect_side(game, export)
        except P.InputError as e:
            return err(422, str(e))
        except Exception as e:  # noqa: BLE001  (a clear answer, never a raw 500, whatever was pasted)
            print("preflight error:", type(e).__name__, file=sys.stderr)
            return err(422, P.UNREADABLE_LOG)
        # names go back to the person who pasted the replay (to pick a side); they are not stored or logged
        return {"ok": True, "players": game["names"], "turns": game["turns"], "end_reason": game["end_reason"],
                "winner": game["winner"], "private": bool(rep.get("private")), "suggested_side": suggested,
                "team_species": export}

    @app.post("/api/review")
    async def submit(request: Request):
        body = await _body(request)
        if body is None:
            return err(400, "Send JSON.")
        refused = public_fetch_refused(request)
        if refused:
            return refused
        side = body.get("side")
        try:
            rep = await run_in_threadpool(resolve_input, body.get("input", ""))
            game = P.inspect_game(rep)
            lines, ctx, job_input = P.prepare(rep, side, _text_field(body.get("team")) or None, game)
        except P.InputError as e:
            return err(422, str(e))
        except Exception as e:  # noqa: BLE001
            print("review input error:", type(e).__name__, file=sys.stderr)
            return err(422, P.UNREADABLE_LOG)
        key = P.cache_key(lines, side, job_input.get("team"))
        rid = review_id_for(key)
        if load_parts(rid) is not None:
            return {"ok": True, "review_id": rid, "cached": True}
        jid = jobs.existing("review:" + key)
        if jid:
            return {"ok": True, "job_id": jid}
        busy = one_at_a_time(request)
        if busy:
            return busy
        ok, wait = jobs.allow(client_of(request), "review")
        if not ok:
            return err(429, f"You have reached {RATES['review']} new reviews this hour. "
                            f"Try again in about {max(1, wait // 60)} minutes.")
        if PUBLIC and not public_review_allowed():   # after the per-client check, so one person can't spend it all
            return err(429, "This public copy has reached its reviews for this hour. The showcases still work; try again later.")
        files = {"replay.json": json.dumps({"log": "\n".join(lines)}),
                 "input.json": json.dumps(dict(job_input, processes=jobs.processes))}
        if job_input.get("team"):
            files["team.txt"] = job_input["team"]

        def done(j, key=key, ctx=ctx):
            review = json.loads((j["dir"] / "review.json").read_text())
            P.build_view(review, ctx)   # fail here, not on the first page view
            return store_review(key, review, ctx, j["dir"])
        jid = jobs.submit("review", "review:" + key, files,
                          lambda d: [str(FP_PY), str(APP / "worker.py"), str(d)], done, client=client_of(request))
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
        model_allowed = not PUBLIC or (jobs.allow(client_of(request), "note")[0]
                                       and within_hourly_cap(_public_notes, PUBLIC_NOTES_PER_HOUR))

        def read_note():
            explaining = any(j["kind"] == "explain" and j["state"] == "running" for j in list(jobs.jobs.values()))
            if model_allowed and not explaining and glimmer_up() and LLM_GATE.acquire(blocking=False):
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
        if PUBLIC and not jobs.allow(client_of(request), "verdict")[0]:
            return err(429, f"You have reached {RATES['verdict']} decision checks this hour.")
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
        _, offered = P.build_view_with_offered(review, ctx)
        if {"turn": turn, "k": k} not in _offered_keys(offered):
            return err(422, "That decision is not available for checking in this review.")
        dec = next((x for x in review["decisions"] if x["turn"] == turn and x["k"] == k), None)
        if dec is None or not dec.get("action") or not dec.get("engine_best"):
            return err(422, "That decision cannot be checked (the log hides your move).")
        belief = P.clean_belief(body.get("belief") or {})
        settings = dict(VI.PRODUCT_SETTINGS)
        settings["seeds"] = list(VI.PRODUCT_SETTINGS["seeds"])
        try:
            runtime = await run_in_threadpool(_engine_runtime_provenance)   # starts a process: never on the event loop
            identity = VI.build_request_identity(
                review_id=rid, replay_path=d / "replay.json", team_path=d / "team.txt", side=ctx["side"],
                turn=turn, k=k, chosen=dec["action"], engine_best=dec["engine_best"], belief=belief,
                settings=settings, runtime_manifest=runtime)
            identity_digest = VI.identity_sha256(identity)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return err(503, "The decision check could not verify its source files. Try again after checking the local engine.")
        vp = d / "verdicts" / VI.cache_filename(turn, k, identity_digest)
        if vp.exists():
            try:
                VI.load_verified_cache(vp, identity, identity_digest, require_app_belief=True)
            except VI.StaleCacheError:
                return err(409, "A saved check does not match this read or engine version. It was not used.")
            return {"ok": True, "cached": True, "key": f"{turn}:{k}",
                    "identity_sha256": identity_digest, "belief_sha256": identity["belief_sha256"]}
        job_key = "verdict:" + identity_digest
        jid = jobs.existing(job_key)
        if jid:
            return {"ok": True, "job_id": jid, "identity_sha256": identity_digest,
                    "belief_sha256": identity["belief_sha256"]}
        busy = one_at_a_time(request)
        if busy:
            return busy
        if not PUBLIC and not jobs.allow(client_of(request), "verdict")[0]:
            return err(429, f"You have reached {RATES['verdict']} decision checks this hour.")
        x = dec["engine_best"]

        def cmd(jd, turn=turn, k=k, x=x):
            return [str(FP_PY), str(VERDICT_SCRIPT), "one", str(d / "replay.json"),
                    "--team", str(d / "team.txt"), "--side", ctx["side"], "--turn", str(turn), "--k", str(k),
                    "--x", x, "--belief", str(jd / "belief.json"), "--review-id", rid,
                    "--reference", str(settings["reference"]),
                    "--seeds", ",".join(map(str, settings["seeds"])),
                    "--worlds", str(settings["worlds"]),
                    "--iterations", str(settings["iterations"]),
                    "--margin", str(settings["margin"]), "--cal", str(settings["calibration"]),
                    "--identity", str(jd / "identity.json"),
                    "--out", str(jd / "verdict.json")]

        def done(j, vp=vp, identity=identity, identity_digest=identity_digest):
            v = json.loads((j["dir"] / "verdict.json").read_text())
            VI.validate_result(v, identity, identity_digest, require_app_belief=True)
            try:
                VI.write_json_exclusive(vp, v)
            except FileExistsError:
                VI.load_verified_cache(vp, identity, identity_digest, require_app_belief=True)
            return {"identity_sha256": identity_digest, "decision": f"{v['turn']}:{v['k']}"}
        jid = jobs.submit("verdict", job_key,
                          {"belief.json": json.dumps(belief, sort_keys=True),
                           "identity.json": json.dumps(identity, sort_keys=True)}, cmd, done, client=client_of(request))
        if jid is None:
            return err(503, "The queue is full right now. Try again in a few minutes.")
        return {"ok": True, "job_id": jid, "identity_sha256": identity_digest,
                "belief_sha256": identity["belief_sha256"]}

    @app.get("/api/review/{rid}/verdict/{turn}/{k}")
    def verdict_receipt(rid: str, turn: int, k: int, identity_sha256: str = None):
        """Read one exact, provenance-bound stated-read receipt."""
        parts = load_parts(rid)
        d = cache_dir(rid)
        if parts is None:
            return err(404, "This review does not exist or has expired.")
        if not identity_sha256 or len(identity_sha256) != 64:
            return err(400, "This receipt needs the request identity. Run the decision check again.")
        if d is None or not (d / "team.txt").exists():
            return err(422, "A decision receipt requires the review's own exported team.")
        review, ctx, _ = parts
        _, offered = P.build_view_with_offered(review, ctx)
        if {"turn": turn, "k": k} not in _offered_keys(offered):
            return err(422, "That decision is not available for checking in this review.")
        try:
            path = d / "verdicts" / VI.cache_filename(turn, k, identity_sha256)
        except ValueError:
            return err(400, "This receipt needs a valid request identity.")
        try:
            raw = json.loads(path.read_text())
            identity = raw.get("cache_identity") if isinstance(raw, dict) else None
            if not isinstance(identity, dict) or identity.get("review_id") != rid:
                raise VI.StaleCacheError("review identity does not match")
            if identity.get("inputs", {}).get("replay_sha256") != VI.sha256_file(d / "replay.json") or \
                    identity.get("inputs", {}).get("team_sha256") != VI.sha256_file(d / "team.txt"):
                raise VI.StaleCacheError("review input hashes changed")
            decision = next((x for x in review.get("decisions", [])
                             if x.get("turn") == turn and x.get("k", 0) == k), None)
            if (decision is None or identity.get("decision") != {
                    "side": ctx.get("side"), "turn": turn, "k": k, "chosen": decision.get("action"),
                    "engine_best": decision.get("engine_best")}):
                raise VI.StaleCacheError("review decision changed")
            VI.validate_result(raw, identity, identity_sha256, require_app_belief=True)
            settings = dict(VI.PRODUCT_SETTINGS)
            settings["seeds"] = list(VI.PRODUCT_SETTINGS["seeds"])
            current_identity = VI.build_request_identity(
                review_id=rid, replay_path=d / "replay.json", team_path=d / "team.txt", side=ctx["side"],
                turn=turn, k=k, chosen=decision["action"], engine_best=decision["engine_best"],
                belief=raw["app_belief"], settings=settings, runtime_manifest=_engine_runtime_provenance())
            if current_identity != identity or VI.identity_sha256(current_identity) != identity_sha256:
                raise VI.StaleCacheError("receipt does not match the current product settings and engine")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return err(409, "This saved receipt does not match the current decision and stated read. The result was withheld.")
        return {"ok": True, "identity_sha256": identity_sha256,
                "belief_sha256": identity["belief_sha256"], "raw": raw,
                "card": P.verdict_card(raw, ctx, review)}

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
        review, ctx, _ = parts
        try:
            view, offered, offered_keys = _explanation_offered(review, ctx, body.get("offered_keys"))
        except ValueError:
            return err(409, "The visible disagreement list changed; refresh this review before asking for an explanation.")
        store = explain_store(rid)
        key_store = explain_key_store(rid)
        have = _jsonfile(store, {}) or {}
        saved_keys = _jsonfile(key_store, {}) or {}
        mode = await run_in_threadpool(_effective_explain_mode)
        if qid in have and _explanation_cache_current(have[qid], saved_keys.get(qid), offered_keys, mode):
            return {"ok": True, "cached": True}
        identity = {"offered_keys": offered_keys, "policy_version": EXPLANATION_POLICY_VERSION, "mode": mode}
        key_digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
        key = f"explain:{rid}:{qid}:{EXPLANATION_POLICY_VERSION}:{mode}:{key_digest}"
        visible_verdicts = _visible_verdicts(offered, verdicts_of(rid))
        template = await run_in_threadpool(_template_explanation, review, QUESTIONS[qid], visible_verdicts, offered)
        ok, _ = jobs.allow(client_of(request), "explain")
        if not ok:
            return err(429, "Too many explanation requests this hour.")
        if mode == "template" and template is None:
            return err(503, "The template explanation could not be generated.")
        if mode == "template":   # nothing to wait for
            cur = _jsonfile(store, {}) or {}
            cur[qid] = dict(template, policy_version=EXPLANATION_POLICY_VERSION,
                             template_cache_version=TEMPLATE_CACHE_VERSION)
            store.parent.mkdir(parents=True, exist_ok=True)
            _write_json(store, cur)
            _save_explain_keys(rid, qid, offered_keys, mode)
            return {"ok": True, "cached": True}

        jid = jobs.existing(key)
        if jid:
            return {"ok": True, "job_id": jid, "template": template}
        busy = one_at_a_time(request)
        if busy:
            return busy
        if PUBLIC and not within_hourly_cap(_public_explains, PUBLIC_EXPLAINS_PER_HOUR):
            return err(429, "Live explanations are paused for this hour. The saved showcases still work.")

        def cmd(jd, mode=mode):
            return [sys.executable, str(APP / "explain_worker.py"), str(jd), "--mode", mode, "--url", GLIMMER_URL,
                    "--lock", str(EVAL_LOCK)]

        def done(j, store=store, qid=qid, review=review, offered=offered, offered_keys=offered_keys,
                 visible_verdicts=visible_verdicts, mode=mode):
            res = json.loads((j["dir"] / "explain.json").read_text())
            if res.get("pending_eval_lock"):   # stopped by an evaluation lock: not cached, a later visit retries
                return "pending"
            if EXPLAIN_MODE == "template":
                return "withheld"
            res = wording_guard(res, review, QUESTIONS[qid], visible_verdicts, offered)
            res["policy_version"] = EXPLANATION_POLICY_VERSION
            cur = _jsonfile(store, {}) or {}
            cur[qid] = res
            store.parent.mkdir(parents=True, exist_ok=True)
            _write_json(store, cur)
            _save_explain_keys(rid, qid, offered_keys, mode)
            return qid
        files = {"review.json": json.dumps(review), "question.txt": AGENT_QUESTIONS.get(qid, QUESTIONS[qid]),
                 "verdicts.json": json.dumps(visible_verdicts), "offered.json": json.dumps(offered),
                 "offered_keys.json": json.dumps(offered_keys)}
        jid = jobs.submit("explain", key, files, cmd, done, client=client_of(request))
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
        if PUBLIC:
            html = (APP / "static" / "index.html").read_text()
            return HTMLResponse(html.replace("<head>", '<head>\n<meta name="hardread-public" content="1">', 1))
        return FileResponse(str(APP / "static" / "index.html"))

    return app


# product wording (research/28 B): never "cost" for choices, never mistake/misplay words outside a verdict card,
# never coach/trainer/analyzer. A model sentence that uses them is not shown; the template is, labelled as such.
NEVER_SAY = re.compile(r"\b(cost|costs|costed|costing|mistakes?|misplay(?:s|ed)?|blunder(?:s|ed)?|errors?|"
                       r"coach(?:es|ing)?|trainer|analy[sz]er|throw(?:s|n)?)\b", re.I)


def wording_guard(res, review, question, verdicts, offered=None):
    text = ((res.get("rendered") or {}).get("text")) or ""
    if res.get("final") == "template" or not NEVER_SAY.search(text):
        return res
    tpl = _template_explanation(review, question, verdicts, offered) or {}
    out = dict(tpl, final="template", policy_rejected=True, requested_mode=res.get("requested_mode"),
               rejected={"final": res.get("final"), "text": text, "matched": NEVER_SAY.findall(text)})
    return out


def _template_explanation(review, question, verdicts, offered=None):
    """The deterministic template answer (instant; shown while Glimmer writes, and as its fallback)."""
    try:
        ex = _agent("explain")
        t0 = time.time()
        res = ex.run(review, question, mode="template", verdicts=verdicts or None, offered=offered)
        return {"question": question, "requested_mode": "template", "final": "template", "rendered": res.get("rendered"),
                "latency_s": round(time.time() - t0, 3), "trace": res.get("trace"), "created": time.time()}
    except Exception as e:  # noqa: BLE001
        print("template explanation failed:", type(e).__name__, e, file=sys.stderr)
        return None


def _text_field(value):
    """A pasted text field: anything but a string counts as empty."""
    return value if isinstance(value, str) else ""


MAX_BODY = PUBLIC_MAX_BODY if PUBLIC else P.MAX_BYTES + P.MAX_EXPORT_BYTES + 4096


def _too_large(content_length):
    try:
        return int(content_length or 0) > MAX_BODY
    except ValueError:
        return False


async def _body(request):
    try:
        if PUBLIC:   # a chunked upload has no declared length, so count it as it arrives
            raw = b""
            async for chunk in request.stream():
                raw += chunk
                if len(raw) > MAX_BODY:
                    return None
        else:
            raw = await request.body()
            if len(raw) > MAX_BODY:
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
