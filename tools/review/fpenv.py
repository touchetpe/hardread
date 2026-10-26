"""Import Foul Play (the HardRead fork) in-process, without a server, and configure it.

Every review module imports Foul Play through here so that the format, search
parameters and logging are set once, the same way `run.py` would set them.
"""
import logging
import os
import sys
from pathlib import Path

HARDREAD = Path(__file__).resolve().parents[2]
FP_DIR = Path(os.environ.get("HARDREAD_FOULPLAY_DIR", HARDREAD / "foul-play"))
if str(FP_DIR) not in sys.path:
    sys.path.insert(0, str(FP_DIR))

from fp.config import FoulPlayConfig  # noqa: E402

_configured = None


def configure(fmt="gen9ou", parallelism=4, search_time_ms=1000, threads=1, log_level=logging.WARNING):
    """Set the FoulPlayConfig fields that the battle-update and search code read."""
    global _configured
    FoulPlayConfig.pokemon_format = fmt
    FoulPlayConfig.smogon_stats = None
    FoulPlayConfig.search_time_ms = int(search_time_ms)
    FoulPlayConfig.parallelism = int(parallelism)
    FoulPlayConfig.team_preview_search_time_ms = int(search_time_ms)
    FoulPlayConfig.team_preview_search_parallelism = int(parallelism)
    FoulPlayConfig.search_threads = int(threads)
    FoulPlayConfig.log_to_file = False
    FoulPlayConfig.username = None
    logging.getLogger().setLevel(log_level)
    for name in ("fp", "fp.battle", "fp.search", "fp.modes"):
        logging.getLogger(name).setLevel(log_level)
    _configured = (fmt, parallelism, search_time_ms, threads)
    return FoulPlayConfig
