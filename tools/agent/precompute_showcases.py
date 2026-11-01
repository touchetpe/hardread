#!/usr/bin/env python3
"""Save Muse Glimmer's answers for the showcase games, and what the same model says with a plain prompt and no engine.

  python3 tools/agent/precompute_showcases.py [--urls URL,URL] [--only showcase-1]

Writes app/data/showcases/showcase-N.explain.json and showcase-N.plain.json. The answers come from the same loop and
checks as the live product (app/explain_worker.py + server.wording_guard).
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "app")]
import eval_a0  # noqa: E402
import explain  # noqa: E402
from llm import GlimmerClient  # noqa: E402
import pipeline as P  # noqa: E402
import server as S  # noqa: E402

OUT = ROOT / "app" / "data" / "showcases"
CAND = ROOT / "scratch_ds" / "app" / "showcase_candidates"
PLAIN_QUESTION = S.QUESTIONS["q1"]


def find_log(review):
    """The anonymized replay behind a showcase; the published files deliberately carry no replay id."""
    want = json.dumps(review.get("decisions"), sort_keys=True)
    for d in sorted(CAND.glob("*_*")):
        f = d / "review.json"
        if f.exists() and json.dumps(json.loads(f.read_text()).get("decisions"), sort_keys=True) == want:
            return d / "replay.json"
    return None


def one_explain(sid, qid, url, review, ctx):
    view, offered = P.build_view_with_offered(review, ctx)
    client = GlimmerClient(url, max_tokens=2048, temperature=0.0, reasoning="low", timeout=400)
    t0 = time.time()
    res = explain.run(review, S.AGENT_QUESTIONS.get(qid, S.QUESTIONS[qid]), client=client, mode="loop", offered=offered)
    out = {"question": S.QUESTIONS[qid], "requested_mode": "loop", "final": res.get("final"), "rendered": res.get("rendered"),
           "raw_pass": res.get("raw_pass"), "after_repair_pass": res.get("after_repair_pass"),
           "latency_s": round(time.time() - t0, 1), "data_tool_calls": res.get("data_tool_calls"),
           "llm_requests": res.get("llm_requests"), "errors": res.get("errors"), "trace": res.get("trace"),
           "checks": explain.checks_of(res), "prompt_version": res.get("prompt_version"),
           "verifier_version": res.get("verifier_version"), "created": time.time()}
    return S.wording_guard(out, review, S.QUESTIONS[qid], {}, offered)


def one_plain(url, log_path, side):
    client = GlimmerClient(url, max_tokens=2048, temperature=0.0, reasoning="low", timeout=400)
    text = eval_a0.log_text(str(log_path), side)
    msgs = [{"role": "system", "content": eval_a0.SYSTEM},
            {"role": "user", "content": f"You are advising player {side}.\n\nQuestion: {PLAIN_QUESTION}\n\nBattle log:\n{text}"}]
    answer = (client.chat(msgs, tools=None).get("content") or "").strip()
    answer = re.sub(r"\bu_[0-9a-f]{6,}\b", "the player", answer)
    return {"question": PLAIN_QUESTION, "answer": answer, "model": "Muse Glimmer-30B (base)", "created": time.time()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--urls", default="http://127.0.0.1:18131/v1/chat/completions")
    ap.add_argument("--only")
    a = ap.parse_args()
    urls = a.urls.split(",")
    tasks = []
    for f in sorted(OUT.glob("showcase-*.review.json")):
        sid = f.name.split(".")[0]
        if a.only and sid != a.only:
            continue
        review, ctx = json.loads(f.read_text()), json.loads((OUT / f"{sid}.ctx.json").read_text())
        tasks += [(sid, q, review, ctx) for q in S.QUESTIONS] + [(sid, "plain", review, ctx)]
    results, pending = {}, list(tasks)

    def worker(url):
        while pending:
            sid, q, review, ctx = pending.pop(0)
            try:
                if q == "plain":
                    log = find_log(review)
                    res = one_plain(url, log, ctx.get("side", "p1")) if log else None
                else:
                    res = one_explain(sid, q, url, review, ctx)
                results.setdefault(sid, {})[q] = res
                print(sid, q, (res or {}).get("final", "plain" if res else "no-log"), (res or {}).get("latency_s"), flush=True)
            except Exception as e:  # noqa: BLE001
                print(sid, q, "FAILED", type(e).__name__, str(e)[:200], flush=True)
            cur = results.get(sid, {})
            (OUT / f"{sid}.explain.json").write_text(json.dumps({k: v for k, v in cur.items() if k != "plain" and v}))
            if cur.get("plain"):
                (OUT / f"{sid}.plain.json").write_text(json.dumps(cur["plain"]))

    with ThreadPoolExecutor(len(urls)) as ex:
        list(ex.map(worker, urls))


if __name__ == "__main__":
    main()
