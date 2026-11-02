#!/usr/bin/env python3
"""Descriptive run of the luck-or-choices (q1) and turning-point (q4) questions over saved reviews, through the same
loop and checks as the product. Not part of the fixed A1 evaluation.

  python3 tools/agent/eval_q1_q4.py --url URL --candidates DIR --out OUT.json [--questions q4,q1] [--only LABEL,LABEL]

One request at a time. Labels hide replay ids. A candidate review with the same decisions as a showcase is the same
game seen from the same side: it is labelled as a repeat, and repeats run last and are counted apart.
Results are rewritten to --out after every answer.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "app")]
import explain  # noqa: E402
from llm import GlimmerClient  # noqa: E402
import pipeline as P  # noqa: E402
import server as S  # noqa: E402
from tools import ToolSession  # noqa: E402

SHOWCASES = ROOT / "app" / "data" / "showcases"


def game_items(cand_dir):
    """[(label, review, ctx, repeat_of)]: showcases, then the other candidates, then repeats of showcases."""
    items, by_decisions = [], {}
    for f in sorted(SHOWCASES.glob("showcase-*.review.json")):
        sid = f.name.split(".")[0]
        review = json.loads(f.read_text())
        items.append((sid, review, json.loads((SHOWCASES / f"{sid}.ctx.json").read_text()), None))
        by_decisions[json.dumps(review.get("decisions"), sort_keys=True)] = sid
    others, repeats = [], []
    for d in sorted(Path(cand_dir).glob("*/")):
        if not (d / "review.json").exists():
            continue
        review = json.loads((d / "review.json").read_text())
        ctx = json.loads((d / "ctx.json").read_text())
        repeat_of = by_decisions.get(json.dumps(review.get("decisions"), sort_keys=True))
        if repeat_of:
            repeats.append((f"repeat-of-{repeat_of}", review, ctx, repeat_of))
        else:
            others.append((f"other-{len(others) + 1:02d}", review, ctx, None))
    return items + others + repeats


def largest_moves(review):
    """(largest move in any direction, largest move toward the final result), by the tool's own arithmetic."""
    moves = ToolSession(review, offered=[]).win_chance_moves()
    if not moves:
        return None, None
    any_dir = sorted(moves, key=lambda m: (-abs(m["move"]), m["turn"]))[0]
    return any_dir, moves[0]


def chosen_turn(res):
    """The turn the answer's first sentence names, read from its citations; None if it names none."""
    for s in (res.get("rendered") or {}).get("sentences") or []:
        for c in s.get("citations") or []:
            if c.get("kind") == "turn":
                return c.get("value")
    return None


def one(label, review, ctx, repeat_of, qid, url):
    _, offered = P.build_view_with_offered(review, ctx)
    client = GlimmerClient(url, max_tokens=2048, temperature=0.0, reasoning="low", timeout=400)
    t0 = time.time()
    res = explain.run(review, S.AGENT_QUESTIONS.get(qid, S.QUESTIONS[qid]), client=client, mode="loop", offered=offered)
    latency = round(time.time() - t0, 1)
    out = {"label": label, "question": qid, "repeat_of": repeat_of, "final": res.get("final"),
           "raw_pass": bool(res.get("raw_pass")), "after_repair_pass": bool(res.get("after_repair_pass")),
           "latency_s": latency, "data_tool_calls": res.get("data_tool_calls"), "llm_requests": res.get("llm_requests"),
           "errors": res.get("errors"), "text": (res.get("rendered") or {}).get("text"), "checks": explain.checks_of(res),
           "citations": [c for s in (res.get("rendered") or {}).get("sentences") or [] for c in s.get("citations") or []],
           "tool_calls": [{"name": c["name"], "args": c["args"]} for c in res.get("trace") or []],
           "result": (review.get("summary") or {}).get("result")}
    guarded = S.wording_guard({"final": res.get("final"), "rendered": res.get("rendered"), "requested_mode": "loop"},
                              review, S.QUESTIONS[qid], {}, offered)
    out["wording_guard_replaced"] = bool(guarded.get("policy_rejected"))
    if qid == "q4":
        any_dir, toward = largest_moves(review)
        out.update(turn_chosen=chosen_turn(res), largest_any_turn=any_dir and any_dir["turn"],
                   largest_any_pts=any_dir and any_dir["move"], largest_toward_turn=toward and toward["turn"],
                   largest_toward_pts=toward and toward["move"], biggest_luck_turn=(ToolSession(review, offered=[]).luck_turns() or [{}])[0].get("turn"))
    return out


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else None


def metrics(rows):
    lat = [r["latency_s"] for r in rows]
    out = {"n": len(rows), "first_draft_pass": sum(r["raw_pass"] for r in rows),
           "pass_after_repair": sum(r["after_repair_pass"] for r in rows),
           "template": sum(r["final"] == "template" for r in rows),
           "wording_guard_replaced": sum(r["wording_guard_replaced"] for r in rows),
           "latency_median_s": statistics.median(lat) if lat else None, "latency_p95_s": p95(lat)}
    if rows and rows[0]["question"] == "q4":
        out["turn_is_largest_any_direction"] = sum(r["turn_chosen"] == r["largest_any_turn"] for r in rows)
        out["turn_is_largest_toward_result"] = sum(r["turn_chosen"] == r["largest_toward_turn"] for r in rows)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--questions", default="q4,q1")
    ap.add_argument("--only", help="comma-separated labels (pilot)")
    ap.add_argument("--no-repeats", action="store_true")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    rows = []
    games = [g for g in game_items(a.candidates) if (not only or g[0] in only) and not (a.no_repeats and g[3])]

    def save():
        summ = {}
        for q in a.questions.split(","):
            cur = [r for r in rows if r["question"] == q]
            summ[q] = {"unique": metrics([r for r in cur if not r["repeat_of"]]),
                       "showcases": metrics([r for r in cur if r["label"].startswith("showcase")]),
                       "others": metrics([r for r in cur if r["label"].startswith("other")]),
                       "repeats": metrics([r for r in cur if r["repeat_of"]])}
        Path(a.out).write_text(json.dumps({"settings": {"max_tokens": 2048, "temperature": 0.0, "reasoning": "low",
                                                        "timeout": 400, "mode": "loop"},
                                           "agent_questions": {q: S.AGENT_QUESTIONS.get(q) for q in a.questions.split(",")},
                                           "prompt_version": explain.PROMPT_VERSION,
                                           "verifier_version": explain.VERIFIER_VERSION,
                                           "summary": summ, "rows": rows}, indent=1, ensure_ascii=False) + "\n")

    for q in a.questions.split(","):
        for label, review, ctx, repeat_of in games:
            row = one(label, review, ctx, repeat_of, q, a.url)
            rows.append(row)
            print(label, q, row["final"], row["latency_s"], "|", row["text"], flush=True)
            save()
    print(json.dumps(json.loads(Path(a.out).read_text())["summary"], indent=1))


if __name__ == "__main__":
    main()
