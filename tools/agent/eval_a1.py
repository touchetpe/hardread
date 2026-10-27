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
import contextlib
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import explain  # noqa: E402
import build_eval_manifest as manifest_tools  # noqa: E402
from lexicon import LEXICON_VERSION  # noqa: E402
from evallock import eval_lock  # noqa: E402
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
    ap.add_argument("--server-provenance", help="private observed server evidence JSON (required for records)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.pilot:
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
        return _run_pilot(a, items, client)

    if not a.items or not a.server_provenance:
        raise SystemExit("recorded A1 requires --items and --server-provenance")
    item_doc = json.loads(Path(a.items).read_text(encoding="utf-8"))
    manifest, manifest_hash, prepared = manifest_tools.load_validated_a1(item_doc)
    items_hash = sha256(a.items)
    items_path = str(Path(a.items).resolve())
    server_provenance = manifest_tools.validate_server_provenance(a.server_provenance)
    if (a.url != server_provenance["url"] or a.max_tokens != 2048 or a.reasoning != "low"):
        raise SystemExit("recorded A1 request settings differ from EVAL_PROTOCOL_AGENT.md")
    out_items = Path(a.out).with_suffix(".items.jsonl")
    output_path, trace_path = manifest_tools.validate_record_output_paths(a.out, companion=out_items)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    client = GlimmerClient(a.url, a.max_tokens, 0.0, a.reasoning)
    reviews = {}
    rows = []
    audit_pop = []
    t0 = time.time()
    commit_at_start = git_head()  # the code that runs (a commit during the run must not relabel it)
    from app import server as app_server
    lock = eval_lock(f"A1 record {output_path.name}", 330 * len(prepared))
    with lock as lock_line:
        if not client.health():
            sys.exit(f"llama-server not healthy at {a.url}")
        # Reserve both private outputs exclusively after every input check and before the first chat.
        with open(trace_path, "x", encoding="utf-8") as fo, open(output_path, "x", encoding="utf-8") as fmain:
            for i, it in enumerate(prepared):
                rp = it["review"]
                reviews[rp] = it["review_sha256"]
                review = it["review_data"]
                offered = it["offered_data"]
                visible_verdicts = it["visible_verdicts"]
                res = explain.run(review, it["question"], client, mode="loop",
                                  verdicts=visible_verdicts, offered=offered)
                displayed = app_server.wording_guard(res, review, it["question"], visible_verdicts, offered)
                product_guard = {"policy_rejected": bool(displayed.get("policy_rejected")),
                                 "final": displayed.get("final"),
                                 "text": ((displayed.get("rendered") or {}).get("text")) or ""}
                row = {"i": i, "review": rp, "q": it["q"], "question": it["question"], "final": res["final"],
                       "raw_pass": res["raw_pass"], "after_repair_pass": res["after_repair_pass"],
                       "data_tool_calls": res["data_tool_calls"], "llm_requests": res["llm_requests"],
                       "latency_s": res["latency_s"], "request_wall_s": [s["wall_s"] for s in res["steps"]],
                       "errors": res["errors"], "product_guard": product_guard,
                       "raw_failed_rules": sorted({f["rule"] for f in (res["attempts"][0]["report"]["failures"]
                                                                      if res["attempts"] else [])}),
                       "text": res["rendered"]["text"]}
                trace_binding = {"expected_items": 60, "items_path": items_path,
                                 "items_sha256": items_hash, "manifest_sha256": manifest_hash,
                                 "item": {key: it[key] for key in ("row_index", "pool", "uid", "side",
                                                                   "rank_hash", "q")}}
                rows.append(row)
                if res["final"] != "template":
                    audit_pop.extend({"i": i, "sentence": s["text"]} for s in res["rendered"]["sentences"])
                fo.write(json.dumps(dict(row, result=res, trace_binding=trace_binding), ensure_ascii=False) + "\n")
                fo.flush()
                print(f"[{i + 1}/{len(prepared)}] {res['final']:<16} {res['latency_s']:7.1f} s",
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
    guard_rejected = sum(bool(r["product_guard"]["policy_rejected"]) for r in rows)
    product_templates = sum(r["product_guard"]["final"] == "template" for r in rows)
    product_modes = {name: sum(r["product_guard"]["final"] == name for r in rows)
                     for name in ("glimmer_raw", "glimmer_repaired", "template")}
    out = {
        "what": "A1 Glimmer + tools (EVAL_PROTOCOL_AGENT.md)", "pilot": False,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
        "ended_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provenance": {"model": "base Muse Glimmer-30B Q4_K_M",
                       "gguf_sha256": server_provenance["gguf_sha256"],
                       "llama_cpp": server_provenance["llama_cpp_commit"],
                       "llama_cpp_build": server_provenance["llama_cpp_build"],
                       "server_flags": server_provenance["server_flags"],
                       "server_url": server_provenance["url"], "server_pid": server_provenance["pid"],
                       "request": {"temperature": 0, "top_k": 40, "top_p": 1, "seed": 42,
                                   "reasoning_strength": a.reasoning, "max_tokens": a.max_tokens},
                       "prompt_version": explain.PROMPT_VERSION, "verifier_version": VERIFIER_VERSION,
                       "lexicon_version": LEXICON_VERSION, "tools_agent_commit": commit_at_start,
                       "tools_agent_dirty": bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                                                                 "--", "tools/agent"], capture_output=True,
                                                                text=True).stdout.strip()), "host": server_provenance["host"],
                       "server_provenance_sha256": sha256(a.server_provenance),
                       "server_provenance": server_provenance,
                       "app_commit": server_provenance["app_commit"],
                       "source_commit": server_provenance["source_commit"],
                       "eval_lock": lock_line,
                       "latency_label": "exclusive server (EVAL_LOCK)",
                       "manifest_sha256": manifest_hash,
                       "items_sha256": items_hash,
                       "source_sha256": {x["path"]: x["sha256"] for x in manifest["sources"]},
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
        "product_guard": {"policy_rejected": guard_rejected,
                          "final_template": product_templates,
                          "displayed_modes": product_modes,
                          "n": n,
                          "note": "post-wording_guard display counts; registered rates and audit population above use direct verifier results"},
        "items_file": str(out_items),
        "rows": rows,
    }
    # The path was exclusively reserved before the first request; write only that file handle.
    with open(output_path, "w", encoding="utf-8") as fmain:
        fmain.write(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
        fmain.flush()
        os.fsync(fmain.fileno())
    print(json.dumps({k: out[k] for k in ("n", "raw_pass", "after_repair", "template", "raw_pass_ci95",
                                          "after_repair_ci95", "latency_s", "raw_failures_by_rule")}))


def _run_pilot(a, items, client):
    """Keep the existing unrecorded pilot path separate from the strict v1 record."""
    out_items = Path(a.out).with_suffix(".items.jsonl")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    reviews, rows, audit_pop = {}, [], []
    t0 = time.time()
    commit_at_start = git_head()
    with eval_lock("pilot must not acquire the record lock", 1) if False else contextlib.nullcontext(None) as _:
        with open(out_items, "w", encoding="utf-8") as fo:
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
                       "errors": res["errors"], "text": res["rendered"]["text"]}
                rows.append(row)
                if res["final"] != "template":
                    audit_pop.extend({"i": i, "sentence": s["text"]} for s in res["rendered"]["sentences"])
                fo.write(json.dumps(dict(row, result=res), ensure_ascii=False) + "\n")
                print(f"[{i + 1}/{len(items)}] {res['final']:<16} {res['latency_s']:7.1f} s", file=sys.stderr)
    n = len(rows)
    raw, rep = sum(r["raw_pass"] for r in rows), sum(r["after_repair_pass"] for r in rows)
    tmpl = sum(r["final"] == "template" for r in rows)
    lat = [r["latency_s"] for r in rows]
    req = [w for r in rows for w in r["request_wall_s"]]
    out = {"what": "A1 pilot (not part of the record; EVAL_PROTOCOL_AGENT.md §7)", "pilot": True,
           "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
           "provenance": {"model": "base Muse Glimmer-30B Q4_K_M", "gguf_sha256": a.gguf_sha256,
                          "llama_cpp": "f8def7f", "prompt_version": explain.PROMPT_VERSION,
                          "verifier_version": VERIFIER_VERSION, "lexicon_version": LEXICON_VERSION,
                          "tools_agent_commit": commit_at_start, "host": a.host_note,
                          "latency_label": "shared server, upper bound (other clients may call :18131)",
                          "review_sha256": reviews},
           "n": n, "raw_pass": raw, "raw_pass_rate": round(raw / n, 4) if n else None,
           "raw_pass_ci95": wilson(raw, n), "after_repair": rep,
           "after_repair_rate": round(rep / n, 4) if n else None, "after_repair_ci95": wilson(rep, n),
           "template": tmpl, "template_share": round(tmpl / n, 4) if n else None,
           "template_ci95": wilson(tmpl, n), "audit_population_sentences": len(audit_pop),
           "latency_s": {"p50": pct(lat, .5), "p95": pct(lat, .95), "per_request_p50": pct(req, .5),
                         "per_request_p95": pct(req, .95)}, "items_file": str(out_items), "rows": rows}
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: out[k] for k in ("n", "raw_pass", "after_repair", "template", "latency_s")}))


if __name__ == "__main__":
    main()
