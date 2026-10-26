#!/usr/bin/env python3
"""Run Foul Play vs Foul Play games on the private HardRead Showdown server.

Each game pairs an accepting bot with a challenging bot. Results go to
runs/<run_id>/games.jsonl, one record per game.
"""
import argparse
import random
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HARDREAD = Path(__file__).resolve().parents[1]
PROJECT = HARDREAD.parent
# Paths default to the Mac layout; Azure jobs override them with HARDREAD_* variables.
FOULPLAY_DIR = Path(os.environ.get("HARDREAD_FOULPLAY_DIR", HARDREAD / "foul-play"))
FOULPLAY_PY = Path(os.environ.get("HARDREAD_FOULPLAY_PY", PROJECT / "environments" / "foulplay" / "bin" / "python"))
RUNTIME = Path(os.environ.get("HARDREAD_RUNTIME", HARDREAD / "showdown" / "runtime"))
NODE = os.environ.get("HARDREAD_NODE", "/Users/REDACTED/.nvm/versions/node/v24.13.0/bin/node")
RUNS_DIR = Path(os.environ.get("HARDREAD_RUNS_DIR", HARDREAD / "runs"))
PORT = 18010  # default; overridden by --port

CONFIGS = {
    # name: (search_parallelism, search_time_ms, search_threads)
    "F1": (2, 100, 1),
    "C1": (4, 2000, 2),
    "C2": (8, 3500, 1),
    "C1t1": (4, 2000, 1),
}


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_server(log_dir, reuse=False):
    if port_open(PORT):
        if reuse:
            return None
        raise SystemExit(f"Port {PORT} is busy. Use --port N for a private server, "
                         f"or --reuse-server to share one you started yourself.")
    env = {k: v for k, v in os.environ.items() if k not in ("PSPORT", "PSBINDADDR", "PSNOSSL")}
    log = open(log_dir / "showdown.log", "w")
    proc = subprocess.Popen(
        [NODE, "--preserve-symlinks", "--preserve-symlinks-main", "./pokemon-showdown",
         "start", "--skip-build", str(PORT)],
        cwd=RUNTIME, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    for _ in range(120):
        if port_open(PORT):
            return proc
        time.sleep(0.5)
    proc.kill()
    raise RuntimeError("Showdown did not open port %d" % PORT)


def bot_cmd(username, mode, cfg, team, opponent=None, log_level="INFO"):
    par, ms, threads = CONFIGS[cfg]
    cmd = [str(FOULPLAY_PY), "run.py",
           "--websocket-uri", f"ws://127.0.0.1:{PORT}/showdown/websocket",
           "--ps-username", username,
           "--bot-mode", mode,
           "--pokemon-format", "gen9ou",
           "--team-name", team,
           "--search-parallelism", str(par),
           "--search-time-ms", str(ms),
           "--search-threads", str(threads),
           "--run-count", "1",
           "--log-level", log_level]
    if opponent:
        cmd += ["--user-to-challenge", opponent]
    return cmd


def play_game(idx, run_dir, cfg_a, cfg_b, team_a, team_b, timeout, log_level="INFO",
              oracle_p=None, oracle_fields="all", oracle_seed=None, log_decisions=False, log_states=False,
              oracle_side="A"):
    tag = uuid.uuid4().hex[:6]
    name_a, name_b = f"hra{idx}{tag}", f"hrb{idx}{tag}"
    log_a = open(run_dir / f"g{idx:04d}_a.log", "w")
    log_b = open(run_dir / f"g{idx:04d}_b.log", "w")
    t0 = time.time()
    # B accepts, A challenges.
    env_b = dict(os.environ)
    oracle_keys = ("HARDREAD_ORACLE_TEAM", "HARDREAD_ORACLE_P", "HARDREAD_ORACLE_FIELDS", "HARDREAD_ORACLE_SEED")
    for k in oracle_keys:
        env_b.pop(k, None)
    if oracle_p is not None and oracle_side == "B":
        # Evaluator-only: side B sees side A's true team.
        env_b.update(oracle_env(team_a, oracle_p, oracle_fields, oracle_seed, idx))
    if log_decisions:
        env_b["HARDREAD_DECISION_LOG"] = str(run_dir / f"g{idx:04d}_b.decisions.jsonl")
        if log_states:
            env_b["HARDREAD_LOG_STATES"] = "1"
    pb = subprocess.Popen(bot_cmd(name_b, "accept_challenge", cfg_b, team_b, log_level=log_level),
                          cwd=FOULPLAY_DIR, stdout=log_b, stderr=subprocess.STDOUT, env=env_b)
    time.sleep(2.0)
    env_a = dict(os.environ)
    for k in oracle_keys:
        env_a.pop(k, None)
    if log_decisions:
        env_a["HARDREAD_DECISION_LOG"] = str(run_dir / f"g{idx:04d}_a.decisions.jsonl")
        if log_states:
            env_a["HARDREAD_LOG_STATES"] = "1"
    if oracle_p is not None and oracle_side == "A":
        # Evaluator-only: side A sees side B's true team with probability p per Pokemon per world.
        env_a.update(oracle_env(team_b, oracle_p, oracle_fields, oracle_seed, idx))
    pa = subprocess.Popen(bot_cmd(name_a, "challenge_user", cfg_a, team_a, opponent=name_b, log_level=log_level),
                          cwd=FOULPLAY_DIR, stdout=log_a, stderr=subprocess.STDOUT, env=env_a)
    status = "ok"
    try:
        pa.wait(timeout=timeout)
        pb.wait(timeout=60)
    except subprocess.TimeoutExpired:
        status = "timeout"
        for p in (pa, pb):
            p.kill()
    dt = time.time() - t0
    log_a.close(); log_b.close()
    text_a = (run_dir / f"g{idx:04d}_a.log").read_text(errors="replace")
    winner = None
    m = re.findall(r"W: (\d+)\s+L: (\d+)", text_a)
    if m:
        w, l = map(int, m[-1])
        winner = "A" if w == 1 else ("B" if l == 1 else None)
    turns = len(re.findall(r"\|turn\|", text_a)) or None
    if winner is None and status == "ok":
        status = "no_result"
    text_o = text_a if oracle_side == "A" else (run_dir / f"g{idx:04d}_b.log").read_text(errors="replace")
    oracle_hits = len(re.findall(r"Oracle applied", text_o)) if oracle_p is not None else 0
    return {"game": idx, "a": name_a, "b": name_b, "cfg_a": cfg_a, "cfg_b": cfg_b,
            "oracle_p": oracle_p, "oracle_side": oracle_side if oracle_p is not None else None,
            "oracle_fields": oracle_fields if oracle_p is not None else None,
            "oracle_hits": oracle_hits,
            "team_a": team_a, "team_b": team_b, "winner": winner, "status": status,
            "seconds": round(dt, 1), "turns_logged": turns,
            "rc_a": pa.returncode, "rc_b": pb.returncode}


def oracle_env(true_team, p, fields, seed, idx):
    env = {"HARDREAD_ORACLE_TEAM": str(FOULPLAY_DIR / "fp" / "teams" / "teams" / true_team),
           "HARDREAD_ORACLE_P": str(p), "HARDREAD_ORACLE_FIELDS": fields}
    if seed is not None:
        env["HARDREAD_ORACLE_SEED"] = str(seed + idx)
    return env


def oracle_side_for(i, seat, side_swap):
    if seat != "alternate":
        return seat
    # Alternate the oracle seat every team pairing (every 2 games with --side-swap), so that
    # each pairing block of 4 games covers both seats and both team assignments.
    return "A" if ((i // 2) if side_swap else i) % 2 == 0 else "B"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--cfg-a", default="F1", choices=CONFIGS)
    ap.add_argument("--cfg-b", default="F1", choices=CONFIGS)
    ap.add_argument("--team-a", default="gen9/ou/example")
    ap.add_argument("--team-b", default="gen9/ou/example")
    ap.add_argument("--team-pool", default=None,
                    help="Folder under fp/teams/teams; each game draws two different teams from it")
    ap.add_argument("--seed", type=int, default=0, help="Seed for team-pool pairing")
    ap.add_argument("--side-swap", action="store_true",
                    help="Play every team pairing twice with the teams swapped between sides "
                         "(A is always the challenger). --games counts pairs.")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--port", type=int, default=18010,
                    help="Private Showdown port for this run (18000/18002 belong to other lanes)")
    ap.add_argument("--reuse-server", action="store_true",
                    help="Use an already running server on --port instead of starting one")
    ap.add_argument("--oracle-p", "--oracle-a-p", dest="oracle_a_p", type=float, default=None,
                    help="Evaluator-only: give the oracle side its opponent's true sets with this probability")
    ap.add_argument("--oracle-seat", default="A", choices=["A", "B", "alternate"],
                    help="Which side gets the oracle (alternate: switch every pairing to cancel seat bias)")
    ap.add_argument("--oracle-fields", default="all")
    ap.add_argument("--oracle-seed", type=int, default=None)
    ap.add_argument("--log-decisions", action="store_true",
                    help="Write per-decision JSONL for both bots (fp/hardread_decision_log.py)")
    ap.add_argument("--log-states", action="store_true", help="Also store world state strings (large)")
    args = ap.parse_args()
    global PORT
    if args.port in (18000, 18002):
        raise SystemExit("Ports 18000/18002 belong to other lanes")
    PORT = args.port

    run_id = args.run_id or time.strftime("%Y%m%dT%H%M%S")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    server = start_server(run_dir, reuse=args.reuse_server)
    t0 = time.time()
    results = []
    try:
        pairs = [(args.team_a, args.team_b)] * args.games
        if args.team_pool:
            pool_dir = FOULPLAY_DIR / "fp" / "teams" / "teams" / args.team_pool
            names = sorted(f.name for f in pool_dir.iterdir() if f.is_file())
            rng = random.Random(args.seed)
            pairs = []
            for _ in range(args.games):
                a, b = rng.sample(names, 2)
                pairs.append((f"{args.team_pool}/{a}", f"{args.team_pool}/{b}"))
        if args.side_swap:
            pairs = [p for ab in pairs for p in (ab, (ab[1], ab[0]))]
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = [ex.submit(play_game, i, run_dir, args.cfg_a, args.cfg_b,
                              pairs[i][0], pairs[i][1], args.timeout, args.log_level,
                              args.oracle_a_p, args.oracle_fields, args.oracle_seed,
                              args.log_decisions, args.log_states,
                              oracle_side_for(i, args.oracle_seat, args.side_swap))
                    for i in range(len(pairs))]
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
    wall = time.time() - t0
    done = [r for r in results if r["winner"]]
    summary = {"run_id": run_id, "games": len(results), "completed": len(done),
               "a_wins": sum(r["winner"] == "A" for r in done),
               "b_wins": sum(r["winner"] == "B" for r in done),
               "oracle_side_wins": (sum(r["winner"] == r["oracle_side"] for r in done)
                                    if args.oracle_a_p is not None else None),
               "failures": sum(r["status"] != "ok" for r in results),
               "wall_seconds": round(wall, 1),
               "mean_game_seconds": round(sum(r["seconds"] for r in results) / max(1, len(results)), 1),
               "games_per_hour": round(len(done) / wall * 3600, 1) if wall else None,
               "concurrency": args.concurrency, "cfg_a": args.cfg_a, "cfg_b": args.cfg_b,
               "oracle_p": args.oracle_a_p, "oracle_seat": args.oracle_seat, "oracle_fields": args.oracle_fields,
               "team_pool": args.team_pool, "seed": args.seed, "side_swap": args.side_swap}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print("SUMMARY", json.dumps(summary), flush=True)


if __name__ == "__main__":
    sys.exit(main())
