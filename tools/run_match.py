#!/usr/bin/env python3
"""Run Foul Play vs Foul Play games on the private HardRead Showdown server.

Each game pairs an accepting bot with a challenging bot. Results go to
runs/<run_id>/games.jsonl, one record per game.
"""
import argparse
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
FOULPLAY_DIR = HARDREAD / "foul-play"
FOULPLAY_PY = PROJECT / "environments" / "foulplay" / "bin" / "python"
RUNTIME = HARDREAD / "showdown" / "runtime"
NODE = "/Users/REDACTED/.nvm/versions/node/v24.13.0/bin/node"
PORT = 18010

CONFIGS = {
    # name: (search_parallelism, search_time_ms, search_threads)
    "F1": (2, 100, 1),
    "C1": (4, 2000, 2),
    "C2": (8, 3500, 1),
}


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_server(log_dir):
    if port_open(PORT):
        return None
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


def play_game(idx, run_dir, cfg_a, cfg_b, team_a, team_b, timeout, log_level="INFO"):
    tag = uuid.uuid4().hex[:6]
    name_a, name_b = f"hra{idx}{tag}", f"hrb{idx}{tag}"
    log_a = open(run_dir / f"g{idx:04d}_a.log", "w")
    log_b = open(run_dir / f"g{idx:04d}_b.log", "w")
    t0 = time.time()
    # B accepts, A challenges.
    pb = subprocess.Popen(bot_cmd(name_b, "accept_challenge", cfg_b, team_b, log_level=log_level),
                          cwd=FOULPLAY_DIR, stdout=log_b, stderr=subprocess.STDOUT)
    time.sleep(2.0)
    pa = subprocess.Popen(bot_cmd(name_a, "challenge_user", cfg_a, team_a, opponent=name_b, log_level=log_level),
                          cwd=FOULPLAY_DIR, stdout=log_a, stderr=subprocess.STDOUT)
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
    return {"game": idx, "a": name_a, "b": name_b, "cfg_a": cfg_a, "cfg_b": cfg_b,
            "team_a": team_a, "team_b": team_b, "winner": winner, "status": status,
            "seconds": round(dt, 1), "turns_logged": turns,
            "rc_a": pa.returncode, "rc_b": pb.returncode}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--cfg-a", default="F1", choices=CONFIGS)
    ap.add_argument("--cfg-b", default="F1", choices=CONFIGS)
    ap.add_argument("--team-a", default="gen9/ou/example")
    ap.add_argument("--team-b", default="gen9/ou/example")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    run_id = args.run_id or time.strftime("%Y%m%dT%H%M%S")
    run_dir = HARDREAD / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    server = start_server(run_dir)
    t0 = time.time()
    results = []
    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = [ex.submit(play_game, i, run_dir, args.cfg_a, args.cfg_b,
                              args.team_a, args.team_b, args.timeout, args.log_level)
                    for i in range(args.games)]
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
               "wall_seconds": round(wall, 1),
               "mean_game_seconds": round(sum(r["seconds"] for r in results) / max(1, len(results)), 1),
               "games_per_hour": round(len(done) / wall * 3600, 1) if wall else None,
               "concurrency": args.concurrency, "cfg_a": args.cfg_a, "cfg_b": args.cfg_b}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print("SUMMARY", json.dumps(summary), flush=True)


if __name__ == "__main__":
    sys.exit(main())
