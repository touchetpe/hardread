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
import hashlib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import build_eval_manifest as manifest_tools  # noqa: E402
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
    ap.add_argument("--server-provenance", help="private observed server evidence JSON (required for records)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.pilot:
        items = json.loads(Path(a.items).read_text())
        client = GlimmerClient(a.url, a.max_tokens, 0.0, a.reasoning)
        if not client.health():
            sys.exit(f"llama-server not healthy at {a.url}")
        rows = []
        _run(items, client, rows)
        _write(a, rows, None)
        return
    if not a.server_provenance:
        raise SystemExit("recorded A0 requires --server-provenance")
    item_doc = json.loads(Path(a.items).read_text(encoding="utf-8"))
    manifest, manifest_hash, items = manifest_tools.load_validated_a0(item_doc)
    server_provenance = manifest_tools.validate_server_provenance(a.server_provenance)
    if (a.url != server_provenance["url"] or a.max_tokens != 2048 or a.reasoning != "low"):
        raise SystemExit("recorded A0 request settings differ from EVAL_PROTOCOL_AGENT.md")
    output_path = Path(a.out)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    receipt_path = output_path.with_suffix(output_path.suffix + ".receipt.json")
    output_path, _ = manifest_tools.validate_record_output_paths(a.out, companion=receipt_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    client = GlimmerClient(a.url, a.max_tokens, 0.0, a.reasoning)
    rows = []
    started = time.time()
    with eval_lock(f"A0 record {output_path.name}", 240 * len(items)) as lock_line:
        if not client.health():
            sys.exit(f"llama-server not healthy at {a.url}")
        # Reserve both the answer record and its separate receipt before the first chat.
        with open(output_path, "xb") as reserved, open(receipt_path, "xb") as receipt_reserved:
            _run(items, client, rows)
            out = _make_output(a, rows, lock_line, pilot=False,
                               extra={"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                                      "ended_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                      "manifest_sha256": manifest_hash,
                                      "items_sha256": hashlib.sha256(Path(a.items).read_bytes()).hexdigest(),
                                      "source_sha256": {x["path"]: x["sha256"] for x in manifest["sources"]},
                                      "server_provenance_sha256": hashlib.sha256(
                                          Path(a.server_provenance).read_bytes()).hexdigest(),
                                      "server_provenance": server_provenance,
                                      "provenance": {"model": "base Muse Glimmer-30B Q4_K_M",
                                                     "gguf_sha256": server_provenance["gguf_sha256"],
                                                     "llama_cpp_commit": server_provenance["llama_cpp_commit"],
                                                     "llama_cpp_build": server_provenance["llama_cpp_build"],
                                                     "server_flags": server_provenance["server_flags"],
                                                     "url": server_provenance["url"],
                                                     "pid": server_provenance["pid"],
                                                     "host": server_provenance["host"],
                                                     "tools_agent_commit": manifest_tools.git_head_for("tools/agent"),
                                                     "prompt_version": A0_PROMPT_VERSION,
                                                     "request": {"temperature": 0, "top_k": 40, "top_p": 1,
                                                                 "seed": 42, "reasoning_strength": a.reasoning,
                                                                 "max_tokens": a.max_tokens},
                                                     "eval_lock": lock_line,
                                                     "latency_label": "exclusive server (EVAL_LOCK)"},
                                      "app_commit": server_provenance["app_commit"],
                                      "source_commit": server_provenance["source_commit"]})
            answer_bytes = (json.dumps(out, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
            reserved.write(answer_bytes)
            reserved.flush()
            import os
            os.fsync(reserved.fileno())
            receipt = {"schema": "c9.a0-answer-receipt-v1", "n": 30,
                       "answer_sha256": hashlib.sha256(answer_bytes).hexdigest(),
                       "manifest_sha256": manifest_hash,
                       "items_sha256": hashlib.sha256(Path(a.items).read_bytes()).hexdigest()}
            receipt_reserved.write((json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8"))
            receipt_reserved.flush()
            os.fsync(receipt_reserved.fileno())
    print(json.dumps({k: out[k] for k in ("n", "answers_with_numeric_claims", "candidate_claims")}))


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
        print(f"[{i + 1}/{len(items)}] {rows[-1]['latency_s']} s "
              f"{len(rows[-1]['candidate_claims'])} candidate claims", file=sys.stderr)


def _write(a, rows, lock_line):
    out = _make_output(a, rows, lock_line, pilot=bool(a.pilot))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: out[k] for k in ("n", "answers_with_numeric_claims", "candidate_claims")}))


def _make_output(a, rows, lock_line, pilot=False, extra=None):
    out = {"what": "A0 Glimmer without engine (EVAL_PROTOCOL_AGENT.md)" + (" — pilot" if pilot else ""),
           "pilot": pilot, "prompt_version": A0_PROMPT_VERSION, "eval_lock": lock_line, "n": len(rows),
           "answers_with_numeric_claims": sum(1 for r in rows if r["candidate_claims"]),
           "candidate_claims": sum(len(r["candidate_claims"]) for r in rows),
           "rubric": "pending (checkable share and within ±5 points are filled by the full-answer scorer)",
           "rows": rows}
    if extra:
        out.update(extra)
    return out


if __name__ == "__main__":
    main()
