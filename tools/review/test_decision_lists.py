#!/usr/bin/env python3
"""Each decision samples from the set lists as they stood when it was made, not from what the rest of the game pruned.

  ../environments/foulplay/bin/python tools/review/test_decision_lists.py
"""
import contextlib
import io
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]
GAMES = ROOT / "scratch_ds/review/games"
GAME = "lab-n1-c000_g0000"


def sizes(mode):
    return ({k: len(v) for k, v in mode.team_datasets.pkmn_sets.items()},
            {k: len(v) for k, v in mode.smogon_sets.pkmn_sets.items()})


def replay():
    import reconstruct as R
    lines, _ = R.load_log(str(GAMES / f"{GAME}.public.json"))
    team = (GAMES / f"{GAME}.team_a.txt").read_text()
    with contextlib.redirect_stdout(io.StringIO()):
        return R.reconstruct(lines, "p1", team, compute_legal=False)


def fresh(decision):
    from fp.modes import battle_mode
    b = decision.battle
    mode = battle_mode(b.mode.name)
    names = {p.name for p in b.opponent.reserve + b.user.reserve}
    with contextlib.redirect_stdout(io.StringIO()):
        mode.initialize_team_preview_datasets(b.pokemon_format, names, "")
    return mode


def test_copy_is_independent():
    import reconstruct as R
    from types import SimpleNamespace as NS

    class Mode:
        team_datasets = NS(pkmn_sets={"a": [1, 2]})
        smogon_sets = NS(pkmn_sets={"a": [3]})
    live = Mode()
    kept = R._lists_at_this_decision(live)
    live.team_datasets.pkmn_sets["a"].pop()
    live.smogon_sets.pkmn_sets["a"].pop()
    assert sizes(kept) == ({"a": 2}, {"a": 1}), sizes(kept)


def test_first_decision_equals_a_fresh_load_after_a_full_replay():
    decs, info = replay()
    first = decs[0].battle.mode
    end = sizes(info["final_battle"].mode)   # read now: battle_mode() hands back the same object, so fresh() resets it
    assert sizes(first) != end, "this game pruned nothing, so it can't test the fix"
    assert first is not decs[-1].battle.mode
    assert sizes(decs[-1].battle.mode) == end, "the last decision has what the game had by then"
    assert sizes(first) == sizes(fresh(decs[0]))


def test_sampling_uses_the_decisions_lists_and_leaves_them_unchanged():
    from evaluate import sample_worlds
    decs, _ = replay()
    d = decs[0]
    def worlds():
        with contextlib.redirect_stdout(io.StringIO()):
            _, w = sample_worlds(d.battle, 30, seed=1)
        return [sorted((p.name, p.item, p.ability, tuple(sorted(m.name for m in p.moves))) for p in b.opponent.reserve) for b, _c in w]
    before = sizes(d.battle.mode)
    kept = worlds()
    assert sizes(d.battle.mode) == before, "sampling changed the lists"
    d.battle.mode = fresh(d)
    assert worlds() == kept, "same seed, same lists: the samples must match a fresh load"


def test_luck_samples_from_the_lists_after_its_own_outcome_and_no_later():
    import luck as L

    class Done(Exception):
        pass
    decs, info = replay()
    seen = []

    def spy(battle, *a, **k):
        seen.append(sizes(battle.mode))
        raise Done

    real, L.sample_worlds = L.sample_worlds, spy
    meter = L.LuckMeter.__new__(L.LuckMeter)
    meter.worlds, meter.max_draw_factor = 1, 1
    meter.student = None
    try:
        checked = differs = 0
        for i, d in enumerate(decs):
            if d.kind != "move" or d.action is None:
                continue
            nxt = decs[i + 1].battle if i + 1 < len(decs) else info["final_battle"]
            try:
                meter.turn_record(d, nxt, [], "p1")
            except Done:
                pass
            assert seen.pop() == sizes(nxt.mode), f"turn {d.turn}: not the lists after its own outcome"
            checked += 1
            differs += sizes(nxt.mode) != sizes(d.battle.mode)
    finally:
        L.sample_worlds = real
    assert checked and differs, "no turn pruned anything, so this game can't test the timing"
    end = sizes(info["final_battle"].mode)
    later = sizes(decs[1].battle.mode)
    assert later != end, "the first turn's lists must not be the end of the game's"


def test_a_turn_no_world_matched_is_flagged():
    import luck as L
    assert L.no_world_matched({"luck": -0.1, "worlds_accepted": 0})
    assert not L.no_world_matched({"luck": -0.1, "worlds_accepted": 1})
    assert not L.no_world_matched({"luck": None, "worlds_accepted": 0}), "a turn with no luck number has nothing to flag"


def test_set_data_comes_from_the_file_with_no_network_call():
    import human_eval as H
    import fp.data.sets.base as B
    calls = []
    real = B.requests.get
    B.requests.get = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("network"))
    H._FULL = None
    try:
        assert H._full_sets(), "the checked-out cache should load"
        assert not calls, "a file on disk must not be fetched"
    finally:
        B.requests.get = real
        H._FULL = None


def test_missing_set_data_fails_plainly_and_leaves_no_empty_file():
    import human_eval as H
    import fp.data.sets.base as B
    import fp.data.sets.team_datasets as T
    class Refused:
        status_code = 503
    real_get, real_dir = B.requests.get, T.PKMN_SETS_CACHE_DIR
    real_base = B.PKMN_SETS_CACHE_DIR
    with tempfile.TemporaryDirectory() as td:
        T.PKMN_SETS_CACHE_DIR = B.PKMN_SETS_CACHE_DIR = td
        try:
            for get in (lambda *a, **k: (_ for _ in ()).throw(OSError("no network")), lambda *a, **k: Refused()):
                B.requests.get = get
                H._FULL = None
                try:
                    H._full_sets()
                    raise AssertionError("no data must not return a table")
                except H.NoSetData as e:
                    assert "couldn't get its set data" in str(e)
                assert not Path(td, "gen9ou", "pokemon_full_sets.json").exists(), "an empty table was left in the cache"
        finally:
            B.requests.get, T.PKMN_SETS_CACHE_DIR, B.PKMN_SETS_CACHE_DIR = real_get, real_dir, real_base
            H._FULL = None


def test_a_logged_hit_keeps_its_price():
    """A move that hit for normal damage is an 86% outcome. With before-the-turn lists no world reproduced it, and it
    scored as the 10% miss."""
    import json
    import luck as L
    import reconstruct as R
    game = ROOT / "scratch_ds/sci/rm/run/r04"
    lines, _ = R.load_log(str(game / "replay.json"))
    side = json.loads((game / "input.json").read_text())["side"]
    with contextlib.redirect_stdout(io.StringIO()):
        decs, info = R.reconstruct(lines, side, (game / "team.txt").read_text(), compute_legal=False)
    with L.LuckMeter(4, 10000, processes=4, hidden_mix=True) as meter, contextlib.redirect_stdout(io.StringIO()):
        rec = next(r for r in meter.game_records(decs, info) if r["turn"] == 6)
    s = L.turn_summary(rec, L.load_abs_calibrator(None, human=True), "branch_cond")
    assert abs(s["realised_prob"] - 0.86) < 0.02, s["realised_prob"]
    assert s["worlds_accepted"] == 4, s["worlds_accepted"]


def main():
    tests = [test_copy_is_independent, test_a_turn_no_world_matched_is_flagged]
    if (ROOT / "foul-play/fp/data/pkmn_sets_cache/gen9ou/pokemon_full_sets.json").exists():
        tests += [test_set_data_comes_from_the_file_with_no_network_call, test_missing_set_data_fails_plainly_and_leaves_no_empty_file]
    have = (GAMES / f"{GAME}.public.json").exists()
    if have:
        tests += [test_first_decision_equals_a_fresh_load_after_a_full_replay,
                  test_sampling_uses_the_decisions_lists_and_leaves_them_unchanged,
                  test_luck_samples_from_the_lists_after_its_own_outcome_and_no_later]
    if (ROOT / "scratch_ds/sci/rm/run/r04/replay.json").exists():
        tests.append(test_a_logged_hit_keeps_its_price)
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok    {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    if not have:
        print("note: 3 replay tests skipped (needs scratch_ds/review/games fixtures)")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
