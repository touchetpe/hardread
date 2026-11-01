"""One explanation job: tools/agent/explain.py (lane Ag-LLM) on a cached review, for one fixed question.

  python3 app/explain_worker.py JOBDIR --mode loop|prefetch|template [--url http://127.0.0.1:18131/v1/chat/completions]

JOBDIR/review.json (review.py output), JOBDIR/question.txt, JOBDIR/verdicts.json (cached checks only).
Writes JOBDIR/explain.json: the rendered sentences with their citations (every number comes from a cited tool
field), the mode that produced them (glimmer_raw / glimmer_repaired / template) and the tool trace (receipts).
"""
import argparse
import json
import sys
import time
from pathlib import Path

HARDREAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARDREAD / "tools" / "agent"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobdir")
    ap.add_argument("--mode", default="template", choices=["loop", "prefetch", "template"])
    ap.add_argument("--url", default="http://127.0.0.1:18131/v1/chat/completions")
    ap.add_argument("--lock", default=str(HARDREAD / "scratch_ds" / "agent" / "EVAL_LOCK"),
                    help="while this file exists a recorded evaluation owns the model: no calls")
    ap.add_argument("--max-lock-wait", type=int, default=1500)
    a = ap.parse_args()
    d = Path(a.jobdir)
    import explain
    review = json.loads((d / "review.json").read_text())
    question = (d / "question.txt").read_text().strip()
    verdicts = json.loads((d / "verdicts.json").read_text()) if (d / "verdicts.json").exists() else None
    offered_path, keys_path = d / "offered.json", d / "offered_keys.json"
    if not offered_path.is_file() or not keys_path.is_file():
        raise ValueError("explanation job is missing its offered-list identity")
    offered = json.loads(offered_path.read_text())
    offered_keys = json.loads(keys_path.read_text())
    if not isinstance(offered, list) or not isinstance(offered_keys, list):
        raise ValueError("explanation job offered-list identity is malformed")
    if any(not isinstance(x, dict) or "turn" not in x for x in offered):
        raise ValueError("explanation job offered-list decisions are malformed")
    actual_keys = [{"turn": x["turn"], "k": x.get("k", 0)} for x in offered]
    if actual_keys != offered_keys:
        raise ValueError("explanation job offered-list keys do not match its decisions")
    client = None
    state = {"locked_out": False}
    if a.mode != "template":
        from llm import GlimmerClient, LLMError
        client = GlimmerClient(a.url, max_tokens=2048, temperature=0.0, reasoning="low", timeout=300)
        lock = Path(a.lock)
        raw_chat = client.chat

        def chat(*args, **kw):
            """Check the evaluation lock before every request; wait for it to clear (the job stays pending)."""
            t_wait = time.time()
            while lock.exists():
                (d / "status.json").write_text(json.dumps({"stage": "pending", "message":
                                                           "Explanation pending: the local model is busy with an evaluation run"}))
                if time.time() - t_wait > a.max_lock_wait:
                    state["locked_out"] = True
                    raise LLMError("evaluation lock")
                time.sleep(10)
            (d / "status.json").write_text(json.dumps({"stage": "writing", "message": "Muse Glimmer is writing"}))
            return raw_chat(*args, **kw)
        client.chat = chat
    t0 = time.time()
    res = explain.run(review, question, client=client, mode=a.mode, verdicts=verdicts or None, offered=offered)
    out = {"question": question, "requested_mode": a.mode, "final": res.get("final"),
           "rendered": res.get("rendered"), "raw_pass": res.get("raw_pass"),
           "after_repair_pass": res.get("after_repair_pass"), "latency_s": round(time.time() - t0, 1),
           "data_tool_calls": res.get("data_tool_calls"), "llm_requests": res.get("llm_requests"),
           "errors": res.get("errors"), "trace": res.get("trace"), "checks": explain.checks_of(res),
           "prompt_version": res.get("prompt_version"), "verifier_version": res.get("verifier_version"),
           "created": time.time(), "pending_eval_lock": state["locked_out"]}
    (d / "explain.json").write_text(json.dumps(out))
    return 0 if out["rendered"] else 1


if __name__ == "__main__":
    sys.exit(main())
