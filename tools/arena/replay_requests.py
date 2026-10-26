#!/usr/bin/env python3
"""Replay logged arena decisions against a live endpoint through the policy's own LLM call.

Takes decisions from a run made with --log-history (view, history, legal), rebuilds the
prompt with the real serializer and sends it with fp.hardread_policy._llm_choose, i.e. the
exact request the arena player sends. Checks endpoint plumbing (tool calls, choice ids,
latency) without playing a whole game.

  HARDREAD_LLM_URL=http://127.0.0.1:18045/v1/chat/completions HARDREAD_LLM_MAX_TOKENS=1024 \\
    <foulplay python> tools/arena/replay_requests.py runs/<run_id> --n 5
"""
import argparse
import json
import sys
import time
from pathlib import Path

HR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HR / "foul-play"))
sys.path.insert(0, str(HR / "tools" / "sft"))
import serialize as S  # noqa: E402
from fp import hardread_policy as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args()
    recs = []
    for f in sorted(Path(args.run).glob("*.policy.jsonl")):
        recs += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    recs = [r for r in recs if r.get("event") == "decision" and "history" in r]
    step = max(1, len(recs) // args.n)
    out = []
    for r in recs[::step][: args.n]:
        ex = S.build_example(r["view"], r["history"], r["legal"], r["player"], r["turn"], r["k"], r["team_preview"])
        t = time.time()
        try:
            action, info = P._llm_choose(ex, r["legal"])
            res = {"ok": True, "action": action, **info}
        except P.PolicyFailure as e:
            res = {"ok": False, "failure": e.kind, "detail": e.detail}
        res.update(turn=r["turn"], k=r["k"], latency_s=round(time.time() - t, 2), n_legal=len(r["legal"]))
        out.append(res)
        print(json.dumps(res), flush=True)
    lat = sorted(x["latency_s"] for x in out)
    print("REPLAY", json.dumps({"n": len(out), "ok": sum(x["ok"] for x in out),
                                "latency_p50": lat[len(lat) // 2] if lat else None, "latency_max": lat[-1] if lat else None}))


if __name__ == "__main__":
    main()
