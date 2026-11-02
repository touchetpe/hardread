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
_hardened = False


def harden_engine():
    """Guards for two engine crashes that real replays trigger. Neither changes a game that did not crash.

    * A species with no usage statistics (Roaring Moon while banned, unranked Pokemon): the sets lookup returns a
      list where move_usage_rates expects a dict. No statistics means no moves to sample from.
    * A Pokemon with more than 4 known moves (your own Zoroark's move recorded on the Pokemon it was disguised as):
      the engine truncates the moves to 4 only after indexing the last used move, then panics on "move:4".
    """
    global _hardened
    if _hardened:
        return
    from fp.data.sets import smogon
    from fp.search import poke_engine_helpers as helpers

    def move_usage_rates(self, pkmn):
        found = self.get_pkmn_by_name_in_dict(pkmn, self.raw_pkmn_sets)
        return found.get(smogon.MOVES_STRING, []) if isinstance(found, dict) else []

    side_builder = helpers.battler_to_poke_engine_side

    def battler_to_side(battler, *args, **kwargs):
        for pkmn in [battler.active, *battler.reserve]:
            if pkmn is not None and len(pkmn.moves) > 4:
                pkmn.moves = pkmn.moves[:4]
        return side_builder(battler, *args, **kwargs)

    smogon.SmogonSets.move_usage_rates = move_usage_rates
    helpers.battler_to_poke_engine_side = battler_to_side
    _hardened = True


def configure(fmt="gen9ou", parallelism=4, search_time_ms=1000, threads=1, log_level=logging.WARNING):
    """Set the FoulPlayConfig fields that the battle-update and search code read."""
    global _configured
    harden_engine()
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
