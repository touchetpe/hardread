#!/usr/bin/env python3
"""Run PokéChamp's "abyssal" heuristic bot (AbyssalPlayer) on the private HardRead Showdown.

The bot is unchanged: poke_env/player/baselines.py:AbyssalPlayer from the PokéChamp
repository (pinned commit, see README). It plays one or more games with a fixed team,
either accepting a challenge or challenging a given user, and logs in as a local guest
(the server has noguestsecurity; poke-env sends `/trn name,0,` when no password is set).

PokéChamp's poke_env package imports every LLM backend at import time (torch, transformers,
google-genai, ollama). Abyssal uses none of them, so the arena environment does not install
them: they are replaced by inert stub modules before the import. Any attempt to *use* a stub
raises, so a silent behaviour change is impossible.

Prints one line "ABYSSAL_RESULT {json}" per finished battle (won/lost, battle tag) and exits.
Run with the arena environment, e.g.
  environments/arena/bin/python hardread/tools/arena/abyssal_bot.py --port 18040 \
      --username abyssal1 --team-file .../team_000000.gen9ou_team --accept-from hra0xyz
"""
import argparse
import asyncio
import importlib.machinery
import importlib.util
import json
import logging
import os
import sys
import time
import types
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
POKECHAMP_DIR = Path(os.environ.get("HARDREAD_POKECHAMP_DIR", PROJECT / "sources" / "pokechamp"))


class _Inert(types.ModuleType):
    """Module stub: importable, any attribute is a class that raises when used."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        def _fail(*a, **k):
            raise RuntimeError(f"stub module {self.__name__}.{name} used: abyssal must not need it")
        return type(name, (), {"__init__": _fail, "__call__": _fail, "__getattr__": lambda s, n: _fail()})


_STUBS = ("torch", "torch.nn", "torch.nn.functional", "transformers", "bitsandbytes",
          "google", "google.genai", "ollama", "mcp")


def _install_stubs():
    def installed(root):
        try:
            return importlib.util.find_spec(root) is not None
        except (ImportError, ValueError):
            return False
    real = {root for root in {n.split(".")[0] for n in _STUBS} if installed(root)}
    for name in _STUBS:
        if name.split(".")[0] in real:
            continue  # real package installed: leave it alone
        mod = _Inert(name)
        mod.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
        mod.__path__ = []
        sys.modules[name] = mod
        parent, _, child = name.rpartition(".")
        if parent and parent in sys.modules:
            setattr(sys.modules[parent], child, mod)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--team-file", required=True, help="Showdown export-format team file")
    ap.add_argument("--format", default="gen9ou")
    ap.add_argument("--accept-from", default=None, help="Accept challenges from this user (default: anyone)")
    ap.add_argument("--challenge", default=None, help="Challenge this user instead of accepting")
    ap.add_argument("--n", type=int, default=1, help="Number of battles")
    ap.add_argument("--log-level", default="WARNING")
    args = ap.parse_args()

    _install_stubs()
    sys.path.insert(0, str(POKECHAMP_DIR))
    os.chdir(POKECHAMP_DIR)  # PokéChamp loads static data with paths relative to the repo root

    from poke_env.player.baselines import AbyssalPlayer
    from poke_env.ps_client.account_configuration import AccountConfiguration
    from poke_env.ps_client.server_configuration import ServerConfiguration

    team = Path(args.team_file).read_text()
    # poke-env picks ws:// (not wss://) only when the URL contains "localhost".
    server = ServerConfiguration(f"localhost:{args.port}", "http://127.0.0.1:1/unused")
    player = AbyssalPlayer(
        battle_format=args.format,
        account_configuration=AccountConfiguration(args.username, None),
        server_configuration=server,
        team=team,
        log_level=getattr(logging, args.log_level.upper(), logging.WARNING),
        start_timer_on_battle_start=False,
    )

    async def run():
        if args.challenge:
            await player.send_challenges(args.challenge, n_challenges=args.n)
        else:
            await player.accept_challenges(args.accept_from, n_challenges=args.n)

    t0 = time.time()
    while not player.ps_client.logged_in.is_set():  # set on poke-env's own loop
        if time.time() - t0 > 120:
            print("ABYSSAL_LOGIN_TIMEOUT", flush=True)
            os._exit(2)
        time.sleep(0.1)
    # Challenges that arrive before accept_challenges() runs are queued by poke-env.
    print(f"ABYSSAL_READY {args.username}", flush=True)
    asyncio.run(run())  # poke-env forwards these coroutines to its own background loop
    for tag, b in player.battles.items():
        print("ABYSSAL_RESULT " + json.dumps({
            "battle_tag": tag, "won": b.won, "lost": b.lost, "finished": b.finished,
            "turns": b.turn, "player_role": getattr(b, "player_role", None),
            "seconds": round(time.time() - t0, 1)}), flush=True)
    sys.stdout.flush()
    os._exit(0)  # poke-env's background loop thread would otherwise keep the process alive


if __name__ == "__main__":
    main()
