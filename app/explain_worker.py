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
    a = ap.parse_args()
    d = Path(a.jobdir)
    import explain
    review = json.loads((d / "review.json").read_text())
    question = (d / "question.txt").read_text().strip()
    verdicts = json.loads((d / "verdicts.json").read_text()) if (d / "verdicts.json").exists() else None
    client = None
    if a.mode != "template":
        from llm import GlimmerClient
        client = GlimmerClient(a.url, max_tokens=2048, temperature=0.0, reasoning="low", timeout=300)
    t0 = time.time()
    res = explain.run(review, question, client=client, mode=a.mode, verdicts=verdicts or None)
    out = {"question": question, "requested_mode": a.mode, "final": res.get("final"),
           "rendered": res.get("rendered"), "raw_pass": res.get("raw_pass"),
           "after_repair_pass": res.get("after_repair_pass"), "latency_s": round(time.time() - t0, 1),
           "data_tool_calls": res.get("data_tool_calls"), "llm_requests": res.get("llm_requests"),
           "errors": res.get("errors"), "trace": res.get("trace"),
           "prompt_version": res.get("prompt_version"), "verifier_version": res.get("verifier_version"),
           "created": time.time()}
    (d / "explain.json").write_text(json.dumps(out))
    return 0 if out["rendered"] else 1


if __name__ == "__main__":
    sys.exit(main())
