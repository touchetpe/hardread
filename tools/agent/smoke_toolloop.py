#!/usr/bin/env python3
"""S1.6 / E10: Glimmer tool-loop smoke test (research/28 §E10).

  python3 tools/agent/smoke_toolloop.py --url http://127.0.0.1:18131/v1/chat/completions --runs 5 \
      --max-tokens 2048 --reasoning low --temperature 0 --out scratch_ds/agent/smoke_toolloop.json

Each run: question -> Glimmer calls a data tool -> tool result -> second call ... -> submit_explanation.
A run counts as a loop if (1) the first response calls a data tool, (2) its result is fed back, (3) a later
response calls submit_explanation, and (4) its arguments are valid JSON with the expected structure
({"sentences": [{"text", "slots"}]}). The raw loop is tested: no repair turn, no template fallback here.
The 7-rule verifier result is recorded for information (it is the A1 metric, not this gate).
Accept: loops_ok == runs, every final JSON valid, latency p50 logged.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import explain  # noqa: E402
from llm import GlimmerClient  # noqa: E402
from render import render  # noqa: E402
from tools import DATA_TOOLS, ToolSession  # noqa: E402

ROOT = HERE.parents[1]
CASES = [  # (review file relative to hardread/, question)
    ("scratch_ds/review/public/gen9ou-2704948800.p1.review_v2.json", "Was this game decided by luck or by choices?"),
    ("scratch_ds/review/public/gen9ou-2704948800.p1.review_v2.json", "What was the biggest piece of luck in this game?"),
    ("scratch_ds/review/public/gen9ou-2704948800.p1.review_v2.json",
     "Where did the engine disagree most with my choices?"),
    ("scratch_ds/review/games/lab-n4-c000_g0019.review_bridge.json", "Was this game decided by luck or by choices?"),
    ("scratch_ds/review/games/lab-n4-c000_g0019.review_bridge.json", "What happened on turn 8?"),
]


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return round(xs[i], 3)


def structural_ok(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("sentences"), list) or not payload["sentences"]:
        return False
    return all(isinstance(s, dict) and isinstance(s.get("text"), str) and isinstance(s.get("slots", {}), dict)
               for s in payload["sentences"])


def one_run(case, client):
    path, q = case
    review = json.loads((ROOT / path).read_text())
    res = explain.run(review, q, client, mode="loop", repair=False, template_fallback=False)
    steps = res["steps"]
    names = [[c["name"] for c in s["tool_calls"]] for s in steps]
    first_data = bool(steps) and any(n in DATA_TOOLS for n in names[0])
    sub_idx = next((i for i, ns in enumerate(names) if "submit_explanation" in ns), None)
    sub = None
    if sub_idx is not None:
        sub = next(c for c in steps[sub_idx]["tool_calls"] if c["name"] == "submit_explanation")
    json_ok = sub is not None and sub["parse_error"] is None and structural_ok(sub["arguments"])
    loop_ok = first_data and sub_idx is not None and sub_idx >= 1 and json_ok
    att = res["attempts"][0] if res["attempts"] else None
    shown = None
    if json_ok:  # render for inspection even when the verifier fails (unresolvable slots show '?')
        sess = ToolSession(review)
        for t in res["trace"]:
            sess.call(t["name"], t["args"])
        shown = render(sub["arguments"], sess)["text"]
    return {
        "review": path, "question": q, "loop_ok": loop_ok, "final_json_valid": json_ok,
        "sequence": names, "llm_requests": res["llm_requests"], "data_tool_calls": res["data_tool_calls"],
        "latency_s": res["latency_s"], "request_wall_s": [s["wall_s"] for s in steps],
        "completion_tokens": [s["completion_tokens"] for s in steps], "prompt_tokens": [s["prompt_tokens"] for s in steps],
        "cached_tokens": [s["cached_tokens"] for s in steps], "decode_tps": [s["decode_tps"] for s in steps],
        "reasoning_chars": [s["reasoning_chars"] for s in steps], "finish": [s["finish_reason"] for s in steps],
        "submit_arguments": sub["arguments"] if sub else None,
        "verifier_pass": bool(att and att["report"]["pass"]),
        "verifier_failures": (att["report"]["failures"] if att else None),
        "rendered_if_resolvable": shown, "errors": res["errors"], "trace": res["trace"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:18131/v1/chat/completions")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--reasoning", default="low")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--server-pid", default=None, help="PID of the llama-server started for this test (recorded)")
    ap.add_argument("--out", default="scratch_ds/agent/smoke_toolloop.json")
    a = ap.parse_args()
    client = GlimmerClient(a.url, a.max_tokens, a.temperature, a.reasoning)
    if not client.health():
        sys.exit(f"llama-server not healthy at {a.url}")
    t0 = time.time()
    runs = []
    for i in range(a.runs):
        r = one_run(CASES[i % len(CASES)], client)
        runs.append(r)
        print(f"run {i + 1}: loop_ok={r['loop_ok']} seq={r['sequence']} {r['latency_s']} s "
              f"verifier={'pass' if r['verifier_pass'] else 'fail'}", file=sys.stderr)
    lat = [r["latency_s"] for r in runs]
    req = [w for r in runs for w in r["request_wall_s"]]
    out = {
        "step": "S1.6 / E10 Glimmer tool-loop smoke (research/28)",
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "server": {"url": a.url, "pid": a.server_pid, "model": "Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf (base)",
                   "llama_cpp": "f8def7f", "flags": "--ctx-size 32768 --jinja --reasoning-format deepseek --swa-full "
                                               "--gpu-layers 99 --temp 0 --top-k 40 --top-p 1 --seed 42 --parallel 1",
                   "request": {"max_tokens": a.max_tokens, "reasoning_strength": a.reasoning,
                               "temperature": a.temperature, "tool_choice": "required"}},
        "prompt_version": explain.PROMPT_VERSION,
        "runs_n": len(runs), "loops_ok": sum(r["loop_ok"] for r in runs),
        "final_json_valid": sum(r["final_json_valid"] for r in runs),
        "latency_s": {"per_run_p50": pct(lat, .5), "per_run_p95": pct(lat, .95), "per_run_max": max(lat),
                      "per_request_p50": pct(req, .5), "per_request_p95": pct(req, .95),
                      "mean_requests_per_run": round(statistics.mean(r["llm_requests"] for r in runs), 2)},
        "decode_tps_p50": pct([x for r in runs for x in r["decode_tps"]], .5),
        "completion_tokens_p50": pct([x for r in runs for x in r["completion_tokens"]], .5),
        "verifier_raw_pass_info": sum(r["verifier_pass"] for r in runs),
        "runs": runs,
    }
    out["accept"] = out["loops_ok"] == len(runs) and out["final_json_valid"] == len(runs)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: out[k] for k in ("runs_n", "loops_ok", "final_json_valid", "latency_s", "decode_tps_p50",
                                          "verifier_raw_pass_info", "accept")}, indent=1))


if __name__ == "__main__":
    main()
