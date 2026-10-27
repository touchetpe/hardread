#!/usr/bin/env python3
"""VT3: unit tests of verdict.py (research/27 §4.4 merge semantics; research/28 §0.2 rule).

  ../environments/foulplay/bin/python tools/review/test_verdict.py      # from hardread/; all tests
  python3 tools/review/test_verdict.py                                   # the pure rule tests only

Merge/contradiction tests reconstruct two local farm games (scratch_ds/review/games/lab-n3-c000_g0002 and
lab-n4-c000_g0019, user side p1) and sample worlds with Foul Play's sampler; no MCTS is run.
"""
import contextlib
import io
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import verdict as VD  # noqa: E402

GAMES = ROOT / "scratch_ds/review/games"


# ------------------------------------------------------------------ pure rule

def test_rule_table():
    d = VD.decide
    assert d([0.06, 0.05, 0.07], [0.0, 0.0, 0.0]) == "Misplay"
    assert d([0.06, 0.05, 0.07], [0.2, 0.2, 0.2]) == "Misplay"           # U BAD wins regardless of R
    assert d([0.12, 0.08, -0.01], [0.0, 0.0, 0.0]) == "Within noise"     # mean >= m but one seed <= 0
    assert d([0.049, 0.05, 0.05], [0.0, 0.0, 0.0]) == "Within noise"     # mean just below m, U not OK either
    assert d([0.01, -0.02, 0.03], [0.09, 0.06, 0.07]) == "Misread"       # U OK (mean 0.0067), R BAD
    assert d([0.01, 0.02, 0.03], [0.09, 0.06, 0.07]) == "Misread"        # U OK at exactly 0.02
    assert d([0.01, 0.02, 0.031], [0.09, 0.06, 0.07]) == "Within noise"  # U mean 0.0203 > 0.02
    assert d([0.0, 0.01, -0.01], [0.02, 0.0, 0.01]) == "Defensible"
    assert d([0.0, 0.01, -0.01], [0.04, 0.03, 0.02]) == "Within noise"   # R between OK and BAD
    assert d([-0.2, -0.1, -0.3], [-0.1, -0.1, -0.1]) == "Defensible"     # chosen better than X: OK
    assert d([None, 0.1, 0.1], [0.0, 0.0, 0.0]) == "Within noise"       # missing option in a run
    assert d([0.07, 0.07, 0.07], [0.0, 0.0, 0.0], m=0.08) == "Within noise"
    assert VD.k_bad([0.05, 0.05, 0.05]) and not VD.k_bad([0.05, 0.05, 0.0])
    lo, hi = VD.wilson(10, 100)
    assert abs(lo - 0.0552) < 1e-3 and abs(hi - 0.1744) < 1e-3, (lo, hi)
    lo, hi = VD.wilson(0, 100)
    assert lo == 0.0 and abs(hi - 0.0370) < 1e-3
    s = VD.summarize([{"delta_U": [0.06] * 3, "delta_R": [0.0] * 3}] * 5 + [{"delta_U": [0.0] * 3, "delta_R": [0.0] * 3}] * 95)
    assert s["counts"] == {"Misplay": 5, "Defensible": 95} and s["pass"] is True
    s = VD.summarize([{"delta_U": [0.06] * 3, "delta_R": [0.0] * 3}] * 12 + [{"delta_U": [0.0] * 3, "delta_R": [0.0] * 3}] * 88)
    assert s["pass"] is False
    c = VD.card_copy("Misplay", "knockoff", [0.06, 0.05, 0.07], [0, 0, 0], "engine")
    assert "knockoff was better by 6 points" in c and "+6.0, +5.0, +7.0" in c
    assert "deterministic" not in c


# ------------------------------------------------------------------ Foul Play fixtures

def _fp():
    try:
        import fpenv
        fpenv.configure()
        import reconstruct  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


_CACHE = {}


def _game(name):
    # Foul Play's set datasets are process-global and initialised by reconstruct(): keep only the last game
    if name not in _CACHE:
        _CACHE.clear()
        import reconstruct as R
        lines, _ = R.load_log(str(GAMES / f"{name}.public.json"))
        team = (GAMES / f"{name}.team_a.txt").read_text()
        with contextlib.redirect_stdout(io.StringIO()):
            decs, info = R.reconstruct(lines, "p1", team, compute_legal=False)
        _CACHE[name] = (decs, info)
    return _CACHE[name]


def _dec(name, turn, k=0):
    decs, info = _game(name)
    return next(d for d in decs if d.turn == turn and d.k == k), info


def _worlds(d, seed=5, n=4):
    from evaluate import sample_worlds
    with contextlib.redirect_stdout(io.StringIO()):
        base, ws = sample_worlds(d.battle, n, seed)
    return base, ws


def _snap(p):
    return {"item": p.item, "ability": p.ability, "tera": p.tera_type, "moves": sorted(m.name for m in p.moves)}


def test_partial_belief_keeps_sampled_fields():
    d, _ = _dec("lab-n3-c000_g0002", 11)
    base, ws = _worlds(d)
    bel = {"sets": {"Gholdengo": {"item": "Choice Scarf"}}}
    for b, _ in ws:
        before = {p.name: _snap(p) for p in VD._all_opp(b.opponent)}
        b2 = deepcopy(b)
        VD.merge_world(b2, bel, base, d.battle.mode)
        after = {p.name: _snap(p) for p in VD._all_opp(b2.opponent)}
        assert after["gholdengo"]["item"] == "choicescarf"
        for k in ("ability", "tera", "moves"):
            assert after["gholdengo"][k] == before["gholdengo"][k], k
        for n in before:
            if n != "gholdengo":
                assert after[n] == before[n], n


def test_revealed_move_never_removed_and_contradiction():
    d, info = _dec("lab-n3-c000_g0002", 11)
    bel = {"sets": {"Ninetales-Alola": {"moves_not": ["Moonblast", "Blizzard"]}}}
    kept, bad = VD.contradictions(bel, d.battle)
    assert any(c["field"] == "moves_not" and c["stated"] == ["Moonblast"] for c in bad), bad
    assert kept["sets"]["Ninetales-Alola"]["moves_not"] == ["Blizzard"]
    base, ws = _worlds(d)
    for b, _ in ws:
        VD.merge_world(b, bel, base, d.battle.mode)  # even if merged directly, a revealed move stays
        nt = VD.find_pkmn(b.opponent, "ninetalesalola")
        names = [m.name for m in nt.moves]
        assert "moonblast" in names and "blizzard" not in names and len(names) <= 4
    assert VD._contra_text(bad[0], info, d).startswith("Read contradicted on turn ")


def test_moves_has_replaces_an_unrevealed_move():
    d, _ = _dec("lab-n3-c000_g0002", 11)
    base, ws = _worlds(d)
    revealed = {"freezedry", "moonblast", "auroraveil"}
    bel = {"sets": {"Ninetales-Alola": {"moves_has": ["Encore"]}}}
    for b, _ in ws:
        nt = VD.find_pkmn(b.opponent, "ninetalesalola")
        n_before = len(nt.moves)
        VD.merge_world(b, bel, base, d.battle.mode)
        names = [m.name for m in nt.moves]
        assert "encore" in names and revealed <= set(names) and len(names) == max(n_before, len(names)) <= 4


def test_moves_has_keeps_every_stated_move():
    d, _ = _dec("lab-n3-c000_g0002", 11)
    base, ws = _worlds(d, seed=3)
    want = ["shadowball", "nastyplot", "recover", "focusblast"]
    for b, _ in ws:
        VD.merge_world(b, {"sets": {"Gholdengo": {"moves_has": want}}}, base, d.battle.mode)
        g = VD.find_pkmn(b.opponent, "gholdengo")
        assert sorted(m.name for m in g.moves) == sorted(want), [m.name for m in g.moves]


def test_moves_not_removes_and_refills():
    d, _ = _dec("lab-n3-c000_g0002", 11)
    base, ws = _worlds(d, seed=9)
    revealed = set(VD.known_state(VD.find_pkmn(base.opponent, "gholdengo"))["moves"])
    n_checked = 0
    for b, _ in ws:
        g = VD.find_pkmn(b.opponent, "gholdengo")
        sampled = [m.name for m in g.moves if m.name not in revealed]
        if not sampled:
            continue
        drop = sampled[0]
        n_before = len(g.moves)
        VD.merge_world(b, {"sets": {"Gholdengo": {"moves_not": [drop]}}}, base, d.battle.mode)
        names = [m.name for m in g.moves]
        assert drop not in names and revealed <= set(names) and len(names) == n_before, (drop, names)
        n_checked += 1
    assert n_checked > 0


def test_item_contradiction_and_turn():
    d, info = _dec("lab-n3-c000_g0002", 11)
    kept, bad = VD.contradictions({"sets": {"Rillaboom": {"item": "Choice Band", "ability": "Grassy Surge"}}}, d.battle)
    assert [c["field"] for c in bad] == ["item"] and bad[0]["revealed"] == "lifeorb"
    assert kept["sets"]["Rillaboom"] == {"ability": "Grassy Surge"}
    assert VD._contra_text(bad[0], info, d) == "Read contradicted on turn 10"
    kept, bad = VD.contradictions({"sets": {"Rillaboom": {"item": "Life Orb"}}}, d.battle)
    assert not bad


def test_tera_edge_cases():
    d, _ = _dec("lab-n3-c000_g0002", 11)  # Ninetales-Alola Terastallized (Water) on turn 1
    kept, bad = VD.contradictions({"sets": {"Ninetales-Alola": {"tera": "Ice"}}}, d.battle)
    assert bad and bad[0]["field"] == "tera"
    kept, bad = VD.contradictions({"sets": {"Ninetales-Alola": {"tera": "Water"}}}, d.battle)
    assert not bad
    base, ws = _worlds(d)
    for b, _ in ws:
        ch = VD.merge_world(b, kept, base, d.battle.mode)
        nt = VD.find_pkmn(b.opponent, "ninetalesalola")
        assert nt.terastallized and nt.tera_type == "water"
        assert any(c.get("tera") == "not set (already Terastallized)" for c in ch)


def test_knocked_off_or_consumed_item_not_set():
    d, _ = _dec("lab-n4-c000_g0019", 4)  # Iron Valiant's Booster Energy already consumed
    kept, bad = VD.contradictions({"sets": {"Iron Valiant": {"item": "Choice Specs"}}}, d.battle)
    assert not bad
    base, ws = _worlds(d)
    for b, _ in ws:
        ch = VD.merge_world(b, kept, base, d.battle.mode)
        iv = VD.find_pkmn(b.opponent, "ironvaliant")
        assert iv.item in (None, "") and any(c.get("item") == "not set (already removed)" for c in ch)


def test_revealed_later_reference():
    d, info = _dec("lab-n4-c000_g0019", 4)
    later = VD.revealed_later(d.battle, info["final_battle"])
    im = later["sets"].get("ironmoth") or {}
    assert im.get("item") == "choicespecs", later       # held at decision time; tricked away later
    assert "fierydance" in im.get("moves_has", [])
    assert "ironhands" not in later["sets"] or "item" not in later["sets"]["ironhands"]  # already known
    only = VD.revealed_later(d.battle, info["final_battle"], ["Iron Moth"])
    assert list(only["sets"]) == ["ironmoth"]


def test_merge_is_noop_for_empty_belief_and_relocks():
    d, _ = _dec("lab-n3-c000_g0002", 11)
    base, ws = _worlds(d)
    for b, _ in ws:
        before = {p.name: _snap(p) for p in VD._all_opp(b.opponent)}
        dis = [m.disabled for m in b.opponent.active.moves]
        VD.merge_world(b, {"sets": {}}, base, d.battle.mode)
        assert {p.name: _snap(p) for p in VD._all_opp(b.opponent)} == before
        assert [m.disabled for m in b.opponent.active.moves] == dis


def main():
    pure = [test_rule_table]
    fp = [test_partial_belief_keeps_sampled_fields, test_revealed_move_never_removed_and_contradiction,
          test_moves_has_replaces_an_unrevealed_move, test_moves_has_keeps_every_stated_move,
          test_moves_not_removes_and_refills,
          test_item_contradiction_and_turn, test_tera_edge_cases, test_knocked_off_or_consumed_item_not_set,
          test_revealed_later_reference, test_merge_is_noop_for_empty_belief_and_relocks]
    have_fp = _fp() and (GAMES / "lab-n3-c000_g0002.public.json").exists()
    tests = pure + (fp if have_fp else [])
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok    {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    if not have_fp:
        print(f"note: {len(fp)} merge tests skipped (needs the Foul Play venv and scratch_ds/review/games fixtures)")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
