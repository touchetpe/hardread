"""Foul Play side of the HardRead student (PROPOSAL; nothing in foul-play/ imports this yet).

Two uses, both env-gated and off by default:

1. Lever  HARDREAD_LEVER_SELECT=student-blend  (final move selection after the search)
   Exact rule, per decision:
     s(a) = chance-weighted visit share of root action a over the sampled worlds
            (fp.hardread_levers.aggregate; sum_a s(a) = 1; A = keys(s) = the legal menu)
     p(a) = student policy on the same menu: policy_value(view, sorted(A), turn, k, team_preview,
            history, player), view built exactly like the decision log on the same battle copy,
            history = canonical public history cut like extract.history_cuts
     q(a) = (1 - lam) * s(a) + lam * p(a)           lam = HARDREAD_STUDENT_LAMBDA (default 0.3)
     choice: Foul Play's own final rule applied to q: C = {a : q(a) >= 0.75 * max q},
             sample a ~ q restricted to C           (HARDREAD_STUDENT_SELECT=sample, default)
             or argmax q                            (HARDREAD_STUDENT_SELECT=argmax)
   Any student failure (missing checkpoint, sidecar down, empty menu) -> None, i.e. Foul Play's
   own selection, and the failure is logged. Compute accounting: the student runs in the main
   process while the search workers run (submit futures -> student -> collect), so it adds no
   wall-clock time; for strict equal-compute accounting subtract its measured latency from the
   per-world search time (HARDREAD_STUDENT_STRICT=1; ~1% of a 2 s search).

2. Standalone player  HARDREAD_POLICY=student  (fp/hardread_policy.py, other lane's file)
   In hardread_policy.choose(), next to the "llm" branch:
       if _POLICY == "student":
           from fp_student import student_choose          # tools/student on sys.path
           action, info = student_choose(view, legal, turn, k, battle.team_preview, history, player)
   using that module's own legal_actions(battle) / build_view(battle) / k counter, so the menu
   and view match the decision log the student was trained on.

Backends: in process (needs numpy in Foul Play's venv: `pip install numpy`) with
HARDREAD_STUDENT_CKPT=/path/best.npz, or the sidecar (no numpy needed):
    python3 tools/student/infer.py serve --ckpt best.npz --port 18061
    HARDREAD_STUDENT_URL=http://127.0.0.1:18061/policy_value
"""
import json
import logging
import os
import random
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

LAMBDA = float(os.environ.get("HARDREAD_STUDENT_LAMBDA", "0.3"))
SELECT = os.environ.get("HARDREAD_STUDENT_SELECT", "sample")
STRICT = os.environ.get("HARDREAD_STUDENT_STRICT") == "1"
_URL = os.environ.get("HARDREAD_STUDENT_URL")
_CKPT = os.environ.get("HARDREAD_STUDENT_CKPT")
_LOG = os.environ.get("HARDREAD_STUDENT_LOG")  # optional JSONL of student inputs (parity audits)
_student = None
_lock = threading.Lock()
_turn_count = defaultdict(lambda: defaultdict(int))  # battle_tag -> turn -> decisions so far


def _get_student():
    global _student
    if _student is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import infer
        _student = infer.load(_CKPT)
        logger.info("HardRead student loaded from {} ({})".format(_CKPT, _student.backend))
    return _student


def student_policy_value(view, legal, turn, k, team_preview, history=None, player=None):
    """-> (probs dict over legal, P(win)); raises on failure. history = canonical public history
    (fp.hardread_history.canonical_history), player = "p1"/"p2"."""
    if _URL:
        import requests
        r = requests.post(_URL, json={"view": view, "legal": legal, "turn": turn, "k": k,
                                      "team_preview": bool(team_preview), "history": history,
                                      "player": player}, timeout=2.0)
        r.raise_for_status()
        d = r.json()
        return d["policy"], d["value"]
    if not _CKPT:
        raise RuntimeError("set HARDREAD_STUDENT_CKPT or HARDREAD_STUDENT_URL")
    return _get_student().policy_value(view, legal, turn, k, team_preview, history, player)


def history_of(battle, k):
    """Canonical public history at this decision, from the protocol lines fp.hardread_policy.record()
    captured (the lever patch makes websocket_client record them when student-blend is on)."""
    from fp import hardread_policy
    from fp.hardread_history import canonical_history
    with hardread_policy._lock:
        raw = list(hardread_policy._history.get(battle.battle_tag, []))
    if not raw:
        return None
    cut = hardread_policy.history_for_decision(raw, battle.turn or 0, k, battle.team_preview)
    return canonical_history(cut, battle.user.name)


def view_of(battle):
    """Decision-log view of a battle copy (same fields and JSON round trip as fp.hardread_decision_log)."""
    from fp.hardread_decision_log import _side
    view = {"user": _side(battle.user), "opponent": _side(battle.opponent),
            "weather": str(battle.weather) if battle.weather else None,
            "field": str(battle.field) if battle.field else None, "trick_room": battle.trick_room}
    return json.loads(json.dumps(view, default=str))


def next_k(battle):
    """Index of this decision within the turn (call once per decision, like extract.py's k)."""
    turn = battle.turn or 0
    with _lock:
        k = _turn_count[battle.battle_tag][turn]
        _turn_count[battle.battle_tag][turn] += 1
    return k


# ---------------------------------------------------------------- lever: student-blend
def student_probs_for_battle(battle, legal):
    """Student policy for the lever; returns (probs or None, latency_s). Never raises."""
    t0 = time.time()
    try:
        k = next_k(battle)
        history = history_of(battle, k)
        probs, value = student_policy_value(view_of(battle), sorted(legal), battle.turn or 0, k,
                                            battle.team_preview, history, battle.user.name)
        logger.info("HardRead student: turn {} k {}, {} history lines, value {:.3f}, {:.1f} ms".format(
            battle.turn or 0, k, len(history or []), value, 1000 * (time.time() - t0)))
        if _LOG:  # parity audits: the exact student input history at this decision
            with _lock, open(_LOG, "a") as f:
                f.write(json.dumps({"battle_tag": battle.battle_tag, "player": battle.user.name,
                                    "turn": battle.turn or 0, "k": k, "team_preview": battle.team_preview,
                                    "legal": sorted(legal), "history": history, "probs": probs}) + "\n")
        return probs, time.time() - t0
    except Exception as e:  # noqa: BLE001 - a lever must never break play
        logger.warning("HardRead student-blend: student failed ({!r}); Foul Play's own selection".format(e))
        return None, time.time() - t0


def blend_select(share, student_probs, lam=LAMBDA, select=SELECT, rng=random):
    """The student-blend rule on an aggregated search share {action: s}. Returns (choice, q)."""
    if not share or not student_probs:
        return None, None
    acts = sorted(share)
    p_tot = sum(student_probs.get(a, 0.0) for a in acts) or 1.0
    q = {a: (1 - lam) * share[a] + lam * student_probs.get(a, 0.0) / p_tot for a in acts}
    top = max(q.values())
    cands = [a for a in acts if q[a] >= 0.75 * top]
    if select == "argmax":
        choice = max(cands, key=lambda a: (q[a], share[a]))
    else:
        choice = rng.choices(cands, weights=[q[a] for a in cands])[0]
    return choice, q


def lever_select(mcts_results, student_probs, lam=LAMBDA, select=SELECT):
    """Drop-in for fp.hardread_levers.select_move when SELECT == 'student-blend'."""
    from fp.hardread_levers import aggregate
    share, _ = aggregate(mcts_results)
    tot = sum(share.values()) or 1.0
    share = {a: v / tot for a, v in share.items()}
    choice, q = blend_select(share, student_probs, lam, select)
    if choice is not None:
        logger.info("HardRead lever student-blend lam={}: {} (search {:.3f}, student {:.3f}, blend {:.3f})".format(
            lam, choice, share[choice], (student_probs or {}).get(choice, 0.0), q[choice]))
    return choice


# ---------------------------------------------------------------- standalone player
def student_choose(view, legal, turn, k, team_preview, history=None, player=None):
    """For HARDREAD_POLICY=student: argmax of the student policy. Returns (action, info)."""
    t0 = time.time()
    probs, value = student_policy_value(view, legal, turn, k, team_preview, history, player)
    action = max(probs, key=probs.get)
    return action, {"student_latency_s": round(time.time() - t0, 4), "student_value": round(value, 4),
                    "student_prob": round(probs[action], 4)}
