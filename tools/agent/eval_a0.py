#!/usr/bin/env python3
"""A0 runner (EVAL_PROTOCOL_AGENT.md §2–4): the same base Glimmer, replay text only, no tools, free text.

  python3 tools/agent/eval_a0.py --items a0_items.json --out scratch_ds/evals/noengine_v1.json

a0_items.json: [{"log": "<replay .json or protocol text>", "side": "p1", "question": "...", "review": "<review.json>"}]
The review is NOT shown to the model; it is stored only so the rubric step can compare each numeric claim with
the engine value. This runner records the answers and a candidate list of numeric claims (regex: percentages,
points, odds, turn numbers, with their sentence). Classifying claims as checkable and within ±5 points is the
separate rubric step of the protocol (agent rubric, owner spot-checks 10); never reported as "% verified".
"""
import argparse
import contextlib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from evallock import eval_lock  # noqa: E402
from llm import GlimmerClient, LLMError  # noqa: E402

A0_PROMPT_VERSION = "a0-prompt-v1"
SYSTEM = ("You are an expert competitive Pokémon player. You get the full battle log of one game (Showdown "
          "protocol) and a question from the player you are told about. Answer in one to three short sentences. "
          "Be concrete about luck, win chances and decisions where you can.")
DROP = ("|j|", "|J|", "|l|", "|L|", "|c|", "|c:|", "|raw|", "|t:|", "|inactive", "|chat|", "|n|", "|html|", "|uhtml",
        "|badge|", "|rated|", "|gametype|", "|gen|", "|tier|", "|rule|", "|clearpoke", "|teampreview", "|timer|")
NUM_RE = re.compile(r"(?:\b\d+(?:\.\d+)?\s*(?:%|percent|points?|pts)\b|\bturn\s+\d+\b|\b\d+(?:\.\d+)?\s*(?:in|out of)\s*"
                    r"\d+\b|\b\d+(?:\.\d+)?%)", re.I)


def log_text(path, side):
    p = Path(path) if Path(path).is_absolute() else ROOT / path
    txt = p.read_text()
    try:
        d = json.loads(txt)
        log = d.get("log", d) if isinstance(d, dict) else d
    except json.JSONDecodeError:
        log = txt
    lines = log if isinstance(log, list) else str(log).split("\n")
    keep = [ln for ln in lines if ln.strip() and not ln.startswith(DROP)]
    return "\n".join(keep)


def claims(answer):
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", answer or ""):
        for m in NUM_RE.finditer(sent):
            out.append({"claim": m.group(0), "sentence": sent})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:18131/v1/chat/completions")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--reasoning", default="low")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    items = json.loads(Path(a.items).read_text())
    client = GlimmerClient(a.url, a.max_tokens, 0.0, a.reasoning)
    rows = []
    lock = contextlib.nullcontext(None) if a.pilot else eval_lock(f"A0 record {Path(a.out).name}", 240 * len(items))
    with lock as lock_line:
        _run(items, client, rows)
    out_lock = lock_line
    _write(a, rows, out_lock)


def _run(items, client, rows):
    for i, it in enumerate(items):
        user = (f"You are player {it['side']}.\nBattle log:\n{log_text(it['log'], it['side'])}\n\n"
                f"Question: {it['question']}")
        t = time.time()
        try:
            r = client.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], tools=None)
            ans, err = r["content"], None
            meta = {k: r[k] for k in ("prompt_tokens", "completion_tokens", "finish_reason", "decode_tps")}
        except LLMError as e:
            ans, err, meta = "", str(e), {}
        rows.append({"i": i, "review": it.get("review"), "log": it["log"], "side": it["side"],
                     "question": it["question"], "answer": ans, "error": err, "latency_s": round(time.time() - t, 3),
                     "candidate_claims": claims(ans), **meta})
        print(f"[{i + 1}/{len(items)}] {rows[-1]['latency_s']} s {len(rows[-1]['candidate_claims'])} claims: "
              f"{ans[:120]!r}", file=sys.stderr)


def _write(a, rows, lock_line):
    out = {"what": "A0 Glimmer without engine (EVAL_PROTOCOL_AGENT.md)" + (" — pilot" if a.pilot else ""),
           "pilot": a.pilot, "prompt_version": A0_PROMPT_VERSION, "eval_lock": lock_line, "n": len(rows),
           "answers_with_numeric_claims": sum(1 for r in rows if r["candidate_claims"]),
           "candidate_claims": sum(len(r["candidate_claims"]) for r in rows),
           "rubric": "pending (checkable share and within ±5 points are filled by the rubric step)", "rows": rows}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: out[k] for k in ("n", "answers_with_numeric_claims", "candidate_claims")}))


if __name__ == "__main__":
    main()
