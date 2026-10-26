"""Absolute state values on a common reference.

Why: poke-engine's MCTS scores a leaf as sigmoid(0.0125 * (eval(leaf) - eval(root))), i.e. *relative
to the root's own static evaluation* (src/mcts.rs). A root value therefore measures "how much better
than now", not "how good". Two MCTS runs rooted at different states (two chance branches, or two
decision points) are on different scales, and a branch whose immediate gain is large (a KO) has
that gain subtracted out by its own higher root eval.

The fix used here:
    A(s) = eval(s) + logit(V_mcts(s)) / 0.0125
where eval is a faithful Python port of poke-engine's genx evaluate() (checked against the Rust CLI),
and V_mcts is the MCTS root value from s. A(s) is in eval points on one absolute scale; it is then
calibrated to P(win) on farm outcomes (calibrate.py --absolute).
"""
import math

from fp.data import all_move_json

K = 0.0125  # poke-engine's sigmoid slope (src/mcts.rs)

POKEMON_ALIVE, POKEMON_HP, USED_TERA = 30.0, 100.0, -75.0
BOOST = {"attack": 30.0, "defense": 15.0, "special_attack": 30.0, "special_defense": 15.0, "speed": 30.0}
BOOST_MULT = {6: 3.3, 5: 3.15, 4: 3.0, 3: 2.5, 2: 2.0, 1: 1.0, 0: 0.0, -1: -1.0, -2: -2.0, -3: -2.5, -4: -3.0,
              -5: -3.15, -6: -3.3}
STATUS = {"freeze": -40.0, "sleep": -25.0, "paralyze": -25.0}
LEECH_SEED, SUBSTITUTE, CONFUSION = -30.0, 40.0, -20.0
SIDE = {"reflect": 20.0, "light_screen": 20.0, "aurora_veil": 40.0, "safeguard": 5.0, "tailwind": 7.0,
        "healing_wish": 30.0}
STEALTH_ROCK, SPIKES, TOXIC_SPIKES, STICKY_WEB = -10.0, -7.0, -7.0, -25.0


def _s(x):
    return str(x).lower().replace("_", "")


def _has_type(p, t):
    if p.terastallized:
        return _s(p.tera_type) == t
    return t in (_s(p.types[0]), _s(p.types[1]))


def _grounded(p):
    if _s(p.item) == "ironball":
        return True
    return not (_has_type(p, "flying") or _s(p.ability) in ("levitate", "eelevate") or _s(p.item) == "airballoon")


def _poison(p, base):
    a = _s(p.ability)
    if a == "poisonheal":
        return 15.0
    if a in ("guts", "marvelscale", "quickfeet", "toxicboost", "magicguard"):
        return 10.0
    return base


def _burn(p):
    if _s(p.ability) in ("guts", "marvelscale", "quickfeet"):
        return 50.0
    mult = sum(1.0 for m in p.moves if all_move_json.get(_s(m.id), {}).get("category") == "physical")
    if p.special_attack > p.attack:
        mult /= 2.0
    return mult * -25.0


def _pokemon(p):
    score = POKEMON_HP * p.hp / p.maxhp
    st = _s(p.status)
    if st == "burn":
        score += _burn(p)
    elif st in STATUS:
        score += STATUS[st]
    elif st == "toxic":
        score += _poison(p, -30.0)
    elif st == "poison":
        score += _poison(p, -10.0)
    if _s(p.item) not in ("none", ""):
        score += 10.0
    return max(score, 0.0) + POKEMON_ALIVE


def _hazards(p, sc):
    score = 0.0
    g = _grounded(p)
    if _s(p.item) != "heavydutyboots":
        if _s(p.ability) != "magicguard":
            score += sc.stealth_rock * STEALTH_ROCK
            if g:
                score += sc.spikes * SPIKES + sc.toxic_spikes * TOXIC_SPIKES
        if g:
            score += sc.sticky_web * STICKY_WEB
    return score


def _side(side):
    score = 0.0
    used_tera = False
    act = int(str(side.active_index))
    vols = {_s(v) for v in side.volatile_statuses}
    for i, p in enumerate(side.pokemon):
        if p.hp > 0:
            score += _pokemon(p) + _hazards(p, side.side_conditions)
            if i == act:
                if "leechseed" in vols:
                    score += LEECH_SEED
                if "substitute" in vols:
                    score += SUBSTITUTE
                if "confusion" in vols:
                    score += CONFUSION
                for k, w in BOOST.items():
                    score += BOOST_MULT[getattr(side, k + "_boost")] * w
        if p.terastallized:
            used_tera = True
    if used_tera:
        score += USED_TERA
    sc = side.side_conditions
    for k, w in SIDE.items():
        score += getattr(sc, k) * w
    return score


def evaluate(state):
    """Port of poke-engine genx evaluate(): side one minus side two, in eval points."""
    return _side(state.side_one) - _side(state.side_two)


def battle_over(state):
    """1 if side one won, 0 if side two won, None otherwise (all six fainted)."""
    s1 = any(p.hp > 0 for p in state.side_one.pokemon)
    s2 = any(p.hp > 0 for p in state.side_two.pokemon)
    if not s2 and s1:
        return 1
    if not s1:
        return 0
    return None


def logit(v, eps=1e-4):
    v = min(1 - eps, max(eps, v))
    return math.log(v / (1 - v))


def absolute(state, root_value):
    """A(s) = eval(s) + logit(V_mcts(s)) / K, in eval points. Terminal states: +/- 1000."""
    over = battle_over(state)
    if over is not None:
        return 1000.0 if over == 1 else -1000.0
    return evaluate(state) + logit(root_value) / K
