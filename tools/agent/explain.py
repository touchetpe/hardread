#!/usr/bin/env python3
"""The explainer agent: base Muse Glimmer-30B + read-only review tools + pointer output (research/27 §4.6).

  python3 tools/agent/explain.py REVIEW.json "Was this game decided by luck or by choices?" \
      [--url http://127.0.0.1:18131/v1/chat/completions] [--mode loop|prefetch|template] [--out result.json]

Flow (mode "loop"): system + question -> Glimmer calls up to 4 data tools -> submit_explanation (sentences with
{placeholders} whose slots cite "#id.field") -> verifier (7 rules) -> pass: render ("glimmer_raw"); fail: one
repair turn listing the failures -> pass: "glimmer_repaired"; fail again (or any error): deterministic template
from the same tool outputs ("template", labelled as such). Mode "prefetch" is the single-call fallback: the
tool outputs are fetched up front and placed in the prompt; Glimmer only calls submit_explanation.
Numbers shown are always rendered by code from cited tool fields (render.py).
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from llm import GlimmerClient, LLMError, assistant_message, tool_message  # noqa: E402
from render import render  # noqa: E402
import templates  # noqa: E402
from tools import DATA_TOOLS, SUBMIT_SCHEMA, ToolSession, all_tool_schemas  # noqa: E402
from verifier import VERIFIER_VERSION, failure_text, verify  # noqa: E402

PROMPT_VERSION = "explain-prompt-v2"
MAX_TOOL_CALLS = 4
MAX_REQUESTS = 8

SYSTEM_PROMPT = """You are HardRead's explainer. A game engine reviewed one competitive Pokémon battle from the player's side; the player is "you". Answer the player's question in one to three short sentences, using only facts from the review tools.

How to answer:
- First call the tools you need (at most four calls), then call submit_explanation once.
- Never write a digit or a number word in "text". Put every number, turn, move or Pokémon name in a {placeholder} and map it in "slots" to a tool field written "#id.field", where #id is the id of the record that holds the field. Example: {"text": "On turn {t}, luck swung {x} against you.", "slots": {"t": "#2.turn", "x": "#2.luck_pts"}}
- Say what each number is: "luck" for luck_pts and swing_pts, "win chance" for fields ending in _wp_pct, "chance" for realised_prob and prob, "preferred" for gap_pts, and "choices, yours and your opponent's" for choices_pts (it counts both players). Write turns as "turn {t}". Units are added when the slot is filled.
- Positive luck_pts helped you; negative went against you. Use "helped you", "toward you" or "against you" only to match the sign. gap_pts says how much higher the engine rated its own option.
- For a chance below one half, hedge: "had only a {p} chance".
- Never call a decision a mistake, misplay, error or correct: the tools show the engine's preference, not a verdict. Describe a disagreement as "the engine preferred {best} by {g}". Never use the words "cost", "total" or "coach", and never add disagreements together.
- Name a Pokémon or move only through a slot, or if it appears in a tool output you cite."""

REPAIR_PROMPT = ("The verifier rejected this explanation:\n{failures}\n"
                 "Fix every listed problem and call submit_explanation again (same format). You may not call other "
                 "tools unless you need a field you do not have.")


def _now():
    return time.time()


def run(review, question, client=None, mode="loop", verdicts=None, luck_reference=None, repair=True,
        template_fallback=True, max_tool_calls=MAX_TOOL_CALLS):
    """-> result dict (see module doc). `client` is a GlimmerClient (None only for mode='template')."""
    t0 = _now()
    session = ToolSession(review, verdicts=verdicts, luck_reference=luck_reference)
    res = {"question": question, "mode": mode, "prompt_version": PROMPT_VERSION, "verifier_version": VERIFIER_VERSION,
           "steps": [], "attempts": [], "errors": [], "data_tool_calls": 0, "llm_requests": 0,
           "raw_pass": False, "after_repair_pass": False, "final": None}
    if mode == "template":
        return _finish_template(res, session, question, t0)

    tools = all_tool_schemas()
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"Question: {question}"}]
    if mode == "prefetch":
        pre = session.prefetch()
        res["data_tool_calls"] = len(pre)
        blob = "\n".join(f"{n}({json.dumps(a)}) -> {session.model_view(o)}" for n, a, o in pre)
        msgs[1]["content"] = (f"Question: {question}\n\nTool outputs already fetched for you (cite them by id):\n{blob}\n\n"
                              f"Call submit_explanation now.")
        tools = [SUBMIT_SCHEMA]

    repaired = False
    submitted = None
    while res["llm_requests"] < MAX_REQUESTS:
        try:
            resp = client.chat(msgs, tools=tools, tool_choice="required")
        except LLMError as e:
            res["errors"].append(f"llm: {e}")
            break
        res["llm_requests"] += 1
        step = {k: resp[k] for k in ("finish_reason", "wall_s", "prompt_tokens", "completion_tokens", "cached_tokens",
                                     "decode_tps")}
        step["tool_calls"] = [{"name": c["name"], "arguments": c["arguments"], "parse_error": c["parse_error"]}
                              for c in resp["tool_calls"]]
        step["reasoning_chars"] = len(resp["reasoning"])
        res["steps"].append(step)
        if not resp["tool_calls"]:
            res["errors"].append(f"no tool call (finish={resp['finish_reason']})")
            fail = {"pass": False, "failures": [{"rule": 0, "sentence": None, "slot": None,
                                                 "message": "no tool call; answer by calling submit_explanation"}]}
            res["attempts"].append({"phase": "repair" if repaired else "raw", "payload": None, "report": fail})
            if repair and not repaired:
                repaired = True
                msgs.append(assistant_message(resp))
                msgs.append({"role": "user", "content": REPAIR_PROMPT.format(failures=failure_text(fail))})
                continue
            break
        msgs.append(assistant_message(resp))
        submit = None
        for c in resp["tool_calls"]:
            if c["name"] == "submit_explanation":
                submit = c
                continue
            if c["name"] in DATA_TOOLS and res["data_tool_calls"] < max_tool_calls and c["arguments"] is not None:
                out = session.call(c["name"], c["arguments"])
                res["data_tool_calls"] += 1
                content = session.model_view(out)
            elif c["name"] in DATA_TOOLS and c["arguments"] is not None:
                content = json.dumps({"error": "tool budget used; call submit_explanation now"})
            else:
                content = json.dumps({"error": c["parse_error"] or f"unknown tool {c['name']!r}"})
            msgs.append(tool_message(c["id"], c["name"], content))
        if submit is None:
            continue
        payload = submit["arguments"]
        if payload is None:
            report = {"pass": False, "failures": [{"rule": 0, "sentence": None, "slot": None,
                                                   "message": submit["parse_error"]}]}
        else:
            report = verify(payload, session)
        phase = "repair" if repaired else "raw"
        res["attempts"].append({"phase": phase, "payload": payload, "report": report})
        submitted = payload
        if report["pass"]:
            res["raw_pass"] = phase == "raw"
            res["after_repair_pass"] = True
            res["final"] = "glimmer_raw" if phase == "raw" else "glimmer_repaired"
            res["rendered"] = render(payload, session, res["final"])
            break
        if repair and not repaired:
            repaired = True
            msgs.append(tool_message(submit["id"], "submit_explanation",
                                     REPAIR_PROMPT.format(failures=failure_text(report))))
            continue
        break
    if res["final"] is None:
        if not submitted and not res["attempts"]:
            res["errors"].append("no submit_explanation within the request budget")
        if template_fallback:
            return _finish_template(res, session, question, t0)
    res["latency_s"] = round(_now() - t0, 3)
    res["trace"] = _trace(session)
    return res


def _finish_template(res, session, question, t0):
    payload = templates.build(session, question)
    report = verify(payload, session)
    if not report["pass"]:  # drop any sentence a template could not phrase within the rules (should not happen)
        bad = {f["sentence"] for f in report["failures"]}
        payload = {"sentences": [s for i, s in enumerate(payload["sentences"]) if i not in bad]}
        report = verify(payload, session) if payload["sentences"] else report
    res["final"] = "template"
    res["template_report"] = report
    res["rendered"] = render(payload, session, "template")
    res["latency_s"] = round(_now() - t0, 3)
    res["trace"] = _trace(session)
    return res


def _trace(session):
    """Receipts: every tool call with its exact output (what the answer may cite)."""
    return [{"name": c["name"], "args": c["args"], "output": json.loads(session.model_view(c["output"]))}
            for c in session.calls]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review")
    ap.add_argument("question")
    ap.add_argument("--url", default="http://127.0.0.1:18131/v1/chat/completions")
    ap.add_argument("--mode", default="loop", choices=["loop", "prefetch", "template"])
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--reasoning", default="low")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--verdicts", help="JSON file: {\"turn:k\": verdict card} (cached verdicts only)")
    ap.add_argument("--out", default="-")
    a = ap.parse_args()
    review = json.loads(Path(a.review).read_text())
    verdicts = json.loads(Path(a.verdicts).read_text()) if a.verdicts else None
    client = None if a.mode == "template" else GlimmerClient(a.url, a.max_tokens, a.temperature, a.reasoning)
    res = run(review, a.question, client, a.mode, verdicts)
    txt = json.dumps(res, indent=1, ensure_ascii=False)
    if a.out == "-":
        print(txt)
    else:
        Path(a.out).write_text(txt + "\n")
    print(f"[{res['final']}] {res['rendered']['text']}  ({res['latency_s']} s)", file=sys.stderr)


if __name__ == "__main__":
    main()
