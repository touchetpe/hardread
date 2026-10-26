#!/usr/bin/env python3
"""HardRead arena: a Foul Play-based player vs PokéChamp's "abyssal" heuristic bot.

The HardRead player (side A, always the challenger, Foul Play client) is either vanilla
Foul Play MCTS at a tools/run_match.py config (--player F1, C1t1, ...) or the HardRead
policy hook (--player llm: fp/hardread_policy.py asks an OpenAI-compatible endpoint).
Abyssal (side B) accepts, running tools/arena/abyssal_bot.py in the arena environment.

Conventions follow tools/run_match.py (its server start, Foul Play command and configs are
reused): a private Showdown on --port (18040-18049 here), unique usernames per game, team
pairs drawn from --team-pool with --seed, --side-swap plays each pairing twice with the
teams exchanged (--games counts pairings), results in runs/<run_id>/games.jsonl and
summary.json, per-bot logs, per-decision policy logs and the Showdown battle logs
(runs/<run_id>/battlelogs/).

Illegal moves: every policy fallback (timeout, no tool call, bad/unknown choice id, serializer
or HTTP error) and every server rejection counts as illegal. A game the HardRead player
does not finish (crash, timeout) counts as a loss in win_rate_failures_as_losses.
"""
import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import run_match as rm  # noqa: E402  (shared paths, configs, server start, Foul Play command)

PROJECT = rm.PROJECT
ARENA_PY = Path(os.environ.get("HARDREAD_ARENA_PY", PROJECT / "environments" / "arena" / "bin" / "python"))
ABYSSAL = HERE / "abyssal_bot.py"
POLICY_ENV = ("HARDREAD_POLICY", "HARDREAD_LLM_URL", "HARDREAD_LLM_MODEL", "HARDREAD_LLM_MAX_TOKENS",
              "HARDREAD_LLM_TIMEOUT", "HARDREAD_LLM_TOOL_CHOICE", "HARDREAD_LLM_EXTRA",
              "HARDREAD_LLM_DEBUG_IDS", "HARDREAD_SERIALIZER", "HARDREAD_POLICY_LOG",
              "HARDREAD_POLICY_LOG_HISTORY", "HARDREAD_DECISION_LOG", "HARDREAD_LOG_STATES",
              "HARDREAD_ORACLE_TEAM", "HARDREAD_ORACLE_P", "HARDREAD_ORACLE_FIELDS", "HARDREAD_ORACLE_SEED")
GATES = {  # name: (min win rate, max illegal rate, max p95 latency s, games)
    "g24h": (0.35, 0.02, 5.0, 200),
    "g1": (0.60, None, None, 400),
}


def clean_env():
    return {k: v for k, v in os.environ.items() if k not in POLICY_ENV}


def wait_for(path, pattern, timeout, proc=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if path.exists() and pattern in path.read_text(errors="replace"):
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.2)
    return False


def private_runtime(port):
    """Showdown runtime folder private to this arena port: the same pinned code (absolute
    symlinks into the shared runtime) with its own config copy, logs/ and battle counter.
    Servers sharing one runtime share logs/lastbattle.txt, so concurrent servers reuse room
    ids and overwrite each other's saved battle logs."""
    base = rm.RUNTIME.resolve()
    rt = base.parent / f"arena-runtime-{port}"
    if not rt.exists():
        rt.mkdir(parents=True)
        for name in os.listdir(base):
            if name in ("logs", "config"):
                continue
            os.symlink(os.path.realpath(base / name), rt / name)
        shutil.copytree(base / "config", rt / "config", symlinks=True)
        (rt / "logs").mkdir()
    return rt


def find_battle_log(names, since, timeout=20):
    """The server's saved log for this game, matched by usernames (written at battle end)."""
    root = rm.RUNTIME / "logs"
    t0 = time.time()
    while time.time() - t0 < timeout:
        for f in root.glob("*/*/*/*.log.json"):
            try:
                if f.stat().st_mtime < since:
                    continue
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if {d.get("p1"), d.get("p2")} == set(names):
                return f, d
        time.sleep(1.0)
    return None, None


def policy_stats(path):
    decisions, rejections = [], []
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            (rejections if r.get("event") == "server_rejection" else decisions).append(r)
    return decisions, rejections


def play_game(idx, run_dir, args, team_hr, team_ab):
    if args.deadline and time.time() > args.deadline:
        return {"game": idx, "status": "skipped", "team_a": team_hr, "team_b": team_ab}
    tag = uuid.uuid4().hex[:6]
    name_hr, name_ab = f"hr{idx}{tag}", f"ab{idx}{tag}"
    log_hr_path, log_ab_path = run_dir / f"g{idx:04d}_hr.log", run_dir / f"g{idx:04d}_ab.log"
    policy_log = run_dir / f"g{idx:04d}_hr.policy.jsonl"
    t0 = time.time()
    # Abyssal accepts (side B).
    log_ab = open(log_ab_path, "w")
    team_file = rm.FOULPLAY_DIR / "fp" / "teams" / "teams" / team_ab
    pab = subprocess.Popen([str(ARENA_PY), str(ABYSSAL), "--port", str(rm.PORT), "--username", name_ab,
                            "--team-file", str(team_file), "--accept-from", name_hr],
                           stdout=log_ab, stderr=subprocess.STDOUT, env=clean_env())
    ready = wait_for(log_ab_path, "ABYSSAL_READY", 120, pab)
    # HardRead player challenges (side A).
    env = clean_env()
    cfg = args.player if args.player != "llm" else args.policy_cfg
    if args.player == "llm":
        env.update({"HARDREAD_POLICY": "llm", "HARDREAD_LLM_URL": args.llm_url,
                    "HARDREAD_LLM_MODEL": args.llm_model, "HARDREAD_LLM_MAX_TOKENS": str(args.llm_max_tokens),
                    "HARDREAD_LLM_TIMEOUT": str(args.llm_timeout), "HARDREAD_POLICY_LOG": str(policy_log)})
        if args.llm_extra:
            env["HARDREAD_LLM_EXTRA"] = args.llm_extra
        env["HARDREAD_SERIALIZER"] = str(Path(args.serializer).resolve())
        if args.llm_debug_ids:
            env["HARDREAD_LLM_DEBUG_IDS"] = "1"
        if args.log_history:
            env["HARDREAD_POLICY_LOG_HISTORY"] = "1"
    elif args.log_decisions:
        env["HARDREAD_DECISION_LOG"] = str(run_dir / f"g{idx:04d}_hr.decisions.jsonl")
    log_hr = open(log_hr_path, "w")
    status = "ok"
    phr = None
    if ready:
        phr = subprocess.Popen(rm.bot_cmd(name_hr, "challenge_user", cfg, team_hr, opponent=name_ab,
                                          log_level=args.log_level),
                               cwd=rm.FOULPLAY_DIR, stdout=log_hr, stderr=subprocess.STDOUT, env=env)
        try:
            phr.wait(timeout=args.timeout)
            pab.wait(timeout=60)
        except subprocess.TimeoutExpired:
            status = "timeout"
    else:
        status = "abyssal_not_ready"
    for p in (phr, pab):
        if p is not None and p.poll() is None:
            p.kill()
            p.wait()
    dt = time.time() - t0
    log_hr.close(); log_ab.close()
    text_hr = log_hr_path.read_text(errors="replace")
    text_ab = log_ab_path.read_text(errors="replace")
    winner = None
    m = re.findall(r"W: (\d+)\s+L: (\d+)", text_hr)
    if m:
        w, l = map(int, m[-1])
        winner = "A" if w == 1 else ("B" if l == 1 else None)
    ab = [json.loads(x) for x in re.findall(r"^ABYSSAL_RESULT (.*)$", text_ab, re.M)]
    ab_view = None
    if ab:
        ab_view = "B" if ab[-1].get("won") else ("A" if ab[-1].get("lost") else None)
    if winner is None and status == "ok":
        status = "no_result"
    # Server battle log (copied: the runtime's logs folder may be shared with other runs).
    blog, d = find_battle_log((name_hr, name_ab), t0 - 5, timeout=20 if winner else 3)
    if blog is not None:
        (run_dir / "battlelogs").mkdir(exist_ok=True)
        shutil.copy2(blog, run_dir / "battlelogs" / blog.name)
    decisions, rejections = policy_stats(policy_log)
    lat = [r["latency_s"] for r in decisions if r.get("latency_s") is not None]
    return {"game": idx, "a": name_hr, "b": name_ab, "player": args.player,
            "cfg_a": cfg, "team_a": team_hr, "team_b": team_ab, "winner": winner,
            "winner_abyssal_view": ab_view, "status": status, "seconds": round(dt, 1),
            "turns": (d or {}).get("turns"), "battle_log": blog.name if blog else None,
            "decisions": len(decisions), "fallbacks": sum(bool(r.get("fallback")) for r in decisions),
            "failure_kinds": dict(Counter(r["failure"] for r in decisions if r.get("failure"))),
            "server_rejections": len(rejections),
            "latency_p50": pct(lat, 50), "latency_p95": pct(lat, 95), "latency_max": max(lat) if lat else None,
            "rc_a": phr.returncode if phr else None, "rc_b": pab.returncode}


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, math.ceil(q / 100 * len(xs)) - 1))], 3)


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(c - h, 4), round(c + h, 4)]


def summarize(results, args, run_id, wall):
    done = [r for r in results if r["winner"]]
    wins = sum(r["winner"] == "A" for r in done)
    decisions = sum(r["decisions"] for r in results)
    illegal = sum(r["fallbacks"] + r["server_rejections"] for r in results)
    lat = []
    for r in results:
        decs, _ = policy_stats(Path(args.run_dir) / f"g{r['game']:04d}_hr.policy.jsonl")
        lat += [x["latency_s"] for x in decs if x.get("latency_s") is not None]
    kinds = Counter()
    for r in results:
        kinds.update(r["failure_kinds"])
    s = {"run_id": run_id, "player": args.player,
         "cfg": args.player if args.player != "llm" else args.policy_cfg,
         "llm_url": args.llm_url if args.player == "llm" else None,
         "llm_model": args.llm_model if args.player == "llm" else None,
         "serializer": args.serializer if args.player == "llm" else None,
         "games": len(results), "completed": len(done),
         "hr_wins": wins, "abyssal_wins": sum(r["winner"] == "B" for r in done),
         "win_rate": round(wins / len(done), 4) if done else None,
         "win_rate_ci95": wilson(wins, len(done)),
         "win_rate_failures_as_losses": round(wins / len(results), 4) if results else None,
         "failures": sum(r["status"] != "ok" for r in results),
         "status_counts": dict(Counter(r["status"] for r in results)),
         "winner_disagreements": sum(1 for r in done if r["winner_abyssal_view"] not in (None, r["winner"])),
         "decisions": decisions, "illegal": illegal,
         "illegal_rate": round(illegal / decisions, 4) if decisions else None,
         "failure_kinds": dict(kinds),
         "server_rejections": sum(r["server_rejections"] for r in results),
         "latency_p50": pct(lat, 50), "latency_p95": pct(lat, 95), "latency_max": max(lat) if lat else None,
         "battle_logs": sum(1 for r in results if r["battle_log"]),
         "wall_seconds": round(wall, 1),
         "mean_game_seconds": round(sum(r["seconds"] for r in results) / max(1, len(results)), 1),
         "games_per_hour": round(len(done) / wall * 3600, 1) if wall else None,
         "concurrency": args.concurrency, "team_pool": args.team_pool, "seed": args.seed,
         "side_swap": args.side_swap, "port": args.port, "runtime": str(rm.RUNTIME)}
    if args.gate:
        wmin, imax, pmax, nmin = GATES[args.gate]
        checks = {"games": len(results) >= nmin,
                  "win_rate": s["win_rate_failures_as_losses"] is not None and s["win_rate_failures_as_losses"] >= wmin}
        if imax is not None:
            checks["illegal_rate"] = s["illegal_rate"] is not None and s["illegal_rate"] <= imax
        if pmax is not None:
            checks["latency_p95"] = s["latency_p95"] is not None and s["latency_p95"] <= pmax
        s["gate"] = {"name": args.gate, "checks": checks, "pass": all(checks.values())}
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--player", default="F1", choices=list(rm.CONFIGS) + ["llm"],
                    help="Vanilla Foul Play at a run_match config, or 'llm' (HardRead policy hook)")
    ap.add_argument("--policy-cfg", default="F1", choices=rm.CONFIGS,
                    help="llm only: search params used to sample the worlds for the legal-action menu")
    ap.add_argument("--llm-url", default="http://127.0.0.1:18045/v1/chat/completions")
    ap.add_argument("--llm-model", default="hardread")
    ap.add_argument("--llm-max-tokens", type=int, default=256)
    ap.add_argument("--llm-timeout", type=float, default=20.0)
    ap.add_argument("--llm-extra", default=None, help="JSON merged into each request body")
    ap.add_argument("--llm-debug-ids", action="store_true", help="Send choice ids to the endpoint (mock only)")
    ap.add_argument("--serializer", default=None,
                    help="Path to serialize.py (default: <repo>/tools/sft/serialize.py)")
    ap.add_argument("--log-history", action="store_true", help="Store history/view/prompt in the policy logs")
    ap.add_argument("--log-decisions", action="store_true", help="Vanilla player: Foul Play decision logs")
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--team-a", default="gen9/ou_hr/example_fixed", help="HardRead player's team (no pool)")
    ap.add_argument("--team-b", default="gen9/ou_hr/example_fixed", help="Abyssal's team (no pool)")
    ap.add_argument("--team-pool", default=None,
                    help="Folder under fp/teams/teams; each pairing draws two different teams from it")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--side-swap", action="store_true",
                    help="Play every pairing twice with the teams exchanged; --games counts pairings")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--port", type=int, default=18040)
    ap.add_argument("--reuse-server", action="store_true")
    ap.add_argument("--shared-runtime", action="store_true",
                    help="Run the server from the shared runtime folder instead of arena-runtime-<port>")
    ap.add_argument("--max-wall", type=float, default=None,
                    help="Seconds; games not started by then are skipped (listed, not counted)")
    ap.add_argument("--gate", choices=list(GATES), default=None, help="Also report pass/fail for a gate")
    args = ap.parse_args()
    if not 18040 <= args.port <= 18049:
        raise SystemExit("The arena uses ports 18040-18049 only")
    rm.PORT = args.port
    if not args.shared_runtime:
        rm.RUNTIME = private_runtime(args.port)
    if args.player == "llm":
        args.serializer = args.serializer or str(rm.HARDREAD / "tools" / "sft" / "serialize.py")
        if not Path(args.serializer).exists():
            raise SystemExit(f"{args.serializer} not found: pass --serializer (stand-in for smoke tests only)")

    run_id = args.run_id or time.strftime("arena-%Y%m%dT%H%M%S")
    run_dir = rm.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    args.run_dir = str(run_dir)
    args.deadline = time.time() + args.max_wall if args.max_wall else None
    (run_dir / "args.json").write_text(json.dumps(vars(args), indent=2))
    server = rm.start_server(run_dir, reuse=args.reuse_server)
    t0 = time.time()
    results = []
    try:
        pairs = [(args.team_a, args.team_b)] * args.games
        if args.team_pool:
            pool_dir = rm.FOULPLAY_DIR / "fp" / "teams" / "teams" / args.team_pool
            names = sorted(f.name for f in pool_dir.iterdir() if f.is_file())
            rng = random.Random(args.seed)
            pairs = []
            for _ in range(args.games):
                a, b = rng.sample(names, 2)
                pairs.append((f"{args.team_pool}/{a}", f"{args.team_pool}/{b}"))
        if args.side_swap:
            pairs = [p for ab in pairs for p in (ab, (ab[1], ab[0]))]
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = [ex.submit(play_game, i, run_dir, args, pairs[i][0], pairs[i][1]) for i in range(len(pairs))]
            with open(run_dir / "games.jsonl", "a") as out:
                for f in futs:
                    r = f.result()
                    results.append(r)
                    out.write(json.dumps(r) + "\n")
                    out.flush()
                    print(json.dumps(r), flush=True)
    finally:
        if server:
            server.terminate()
            server.wait(timeout=20)
    skipped = [r for r in results if r["status"] == "skipped"]
    summary = summarize([r for r in results if r["status"] != "skipped"], args, run_id, time.time() - t0)
    summary["skipped_after_max_wall"] = len(skipped)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print("SUMMARY", json.dumps(summary), flush=True)


if __name__ == "__main__":
    sys.exit(main())
