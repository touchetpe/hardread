#!/usr/bin/env python3
"""A1 runner (EVAL_PROTOCOL_AGENT.md §3–4): Glimmer + tools on (review, question) items.

  python3 tools/agent/eval_a1.py --items items.json --out scratch_ds/evals/faithfulness_v1.json
  python3 tools/agent/eval_a1.py --pilot --reviews R1.json R2.json --questions 1,2,3 --limit 5 \
      --out scratch_ds/agent/a1_dryrun.json

items.json: [{"review": "<path>", "question": "<text>"}] or use --reviews x --questions (Q1..Q3 of the protocol).
Writes provenance (GGUF SHA-256 if known, llama.cpp commit, flags, versions, tools/agent commit, review SHA-256s),
per-item results with full traces (a .items.jsonl next to the output) and the metrics: raw pass, after-repair,
template share (Wilson 95% CIs), tool calls per answer, latency p50/p95, verifier failures by rule.
The misleading-sentence audit is a separate step (it needs the blinded rubric); this file lists the population.
"""
import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import explain  # noqa: E402
from lexicon import LEXICON_VERSION  # noqa: E402
from llm import GlimmerClient  # noqa: E402
from verifier import VERIFIER_VERSION  # noqa: E402

QUESTIONS = {
    1: "Was this game decided by luck or by my choices?",
    2: "What was the biggest piece of luck in this game, and how much did it matter?",
    3: "Where did the engine disagree most with my choices, and by how much?",
}


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return round(xs[min(len(xs) - 1, max(0, int(math.ceil(q * len(xs))) - 1))], 3)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def git_head():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%H", "--", "tools/agent"],
                              capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items")
    ap.add_argument("--reviews", nargs="*")
    ap.add_argument("--questions", default="1,2,3")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:18131/v1/chat/completions")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--reasoning", default="low")
    ap.add_argument("--gguf-sha256", default=None, help="SHA-256 of the served GGUF (recorded)")
    ap.add_argument("--host-note", default="owner's Mac (M4 Max), local llama-server; other lanes' jobs may share it")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.items:
        items = json.loads(Path(a.items).read_text())
    else:
        qs = [int(x) for x in a.questions.split(",")]
        items = [{"review": r, "question": QUESTIONS[q], "q": q} for r in a.reviews for q in qs]
    if a.limit:
        items = items[:a.limit]
    client = GlimmerClient(a.url, a.max_tokens, 0.0, a.reasoning)
    if not client.health():
        sys.exit(f"llama-server not healthy at {a.url}")
    out_items = Path(a.out).with_suffix(".items.jsonl")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    reviews = {}
    rows = []
    t0 = time.time()
    with open(out_items, "w") as fo:
        for i, it in enumerate(items):
            rp = it["review"]
            if rp not in reviews:
                reviews[rp] = sha256(ROOT / rp if not Path(rp).is_absolute() else rp)
            review = json.loads(Path(ROOT / rp if not Path(rp).is_absolute() else rp).read_text())
            res = explain.run(review, it["question"], client, mode="loop")
            row = {"i": i, "review": rp, "q": it.get("q"), "question": it["question"], "final": res["final"],
                   "raw_pass": res["raw_pass"], "after_repair_pass": res["after_repair_pass"],
                   "data_tool_calls": res["data_tool_calls"], "llm_requests": res["llm_requests"],
                   "latency_s": res["latency_s"], "request_wall_s": [s["wall_s"] for s in res["steps"]],
                   "errors": res["errors"],
                   "raw_failed_rules": sorted({f["rule"] for f in (res["attempts"][0]["report"]["failures"]
                                                                  if res["attempts"] else [])}),
                   "text": res["rendered"]["text"]}
            rows.append(row)
            fo.write(json.dumps(dict(row, result=res), ensure_ascii=False) + "\n")
            fo.flush()
            print(f"[{i + 1}/{len(items)}] {res['final']:<16} {res['latency_s']:7.1f} s  {row['text'][:110]}",
                  file=sys.stderr)
    n = len(rows)
    raw = sum(r["raw_pass"] for r in rows)
    rep = sum(r["after_repair_pass"] for r in rows)
    tmpl = sum(r["final"] == "template" for r in rows)
    fail_by_rule = {}
    for r in rows:
        for x in r["raw_failed_rules"]:
            fail_by_rule[str(x)] = fail_by_rule.get(str(x), 0) + 1
    lat = [r["latency_s"] for r in rows]
    req = [w for r in rows for w in r["request_wall_s"]]
    audit_pop = [{"i": r["i"], "sentence": s["text"]} for r, line in zip(rows, open(out_items))
                 for s in (json.loads(line)["result"]["rendered"]["sentences"] if r["final"] != "template" else [])]
    out = {
        "what": "A1 pilot (not part of the record; EVAL_PROTOCOL_AGENT.md §7)" if a.pilot else
                "A1 Glimmer + tools (EVAL_PROTOCOL_AGENT.md)",
        "pilot": bool(a.pilot), "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "provenance": {"model": "base Muse Glimmer-30B Q4_K_M", "gguf_sha256": a.gguf_sha256, "llama_cpp": "f8def7f",
                       "server_flags": "--ctx-size 32768 --jinja --reasoning-format deepseek --swa-full "
                                       "--gpu-layers 99 --parallel 1",
                       "request": {"temperature": 0, "top_k": 40, "top_p": 1, "seed": 42,
                                   "reasoning_strength": a.reasoning, "max_tokens": a.max_tokens},
                       "prompt_version": explain.PROMPT_VERSION, "verifier_version": VERIFIER_VERSION,
                       "lexicon_version": LEXICON_VERSION, "tools_agent_commit": git_head(), "host": a.host_note,
                       "review_sha256": reviews},
        "n": n,
        "raw_pass": raw, "raw_pass_rate": round(raw / n, 4) if n else None, "raw_pass_ci95": wilson(raw, n),
        "after_repair": rep, "after_repair_rate": round(rep / n, 4) if n else None, "after_repair_ci95": wilson(rep, n),
        "template": tmpl, "template_share": round(tmpl / n, 4) if n else None, "template_ci95": wilson(tmpl, n),
        "server_errors": sum(1 for r in rows if any(e.startswith("llm:") for e in r["errors"])),
        "tool_calls_mean": round(sum(r["data_tool_calls"] for r in rows) / n, 2) if n else None,
        "tool_calls_max": max((r["data_tool_calls"] for r in rows), default=None),
        "latency_s": {"p50": pct(lat, .5), "p95": pct(lat, .95), "max": max(lat) if lat else None,
                      "per_request_p50": pct(req, .5), "per_request_p95": pct(req, .95)},
        "raw_failures_by_rule": fail_by_rule,
        "audit_population_sentences": len(audit_pop),
        "items_file": str(out_items),
        "rows": rows,
    }
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: out[k] for k in ("n", "raw_pass", "after_repair", "template", "raw_pass_ci95",
                                          "after_repair_ci95", "latency_s", "raw_failures_by_rule")}))


if __name__ == "__main__":
    main()
