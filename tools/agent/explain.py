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
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lexicon import PLACEHOLDER_RE  # noqa: E402
from llm import GlimmerClient, LLMError, assistant_message, tool_message  # noqa: E402
from render import render  # noqa: E402
import templates  # noqa: E402
from tools import DATA_TOOLS, GRADUAL_MAX_PTS, SUBMIT_SCHEMA, ToolSession, all_tool_schemas  # noqa: E402
from verifier import VERIFIER_VERSION, failure_text, verify  # noqa: E402

PROMPT_VERSION = "explain-prompt-v3"
MAX_TOOL_CALLS = 4
MAX_REQUESTS = 8

SYSTEM_PROMPT = """You are HardRead's explainer. A game engine reviewed one competitive Pokémon battle from the player's side; the player is "you". Answer the player's question in one to three short sentences, using only facts from the review tools.

How to answer:
- First call the tools you need (at most four calls), then call submit_explanation once.
- Never write a digit or a number word in "text". Put every number, turn, move or Pokémon name in a {placeholder} and map it in "slots" to a tool field written "#id.field", where #id is the id of the record that holds the field. Example: {"text": "On turn {t}, luck swung {x} against you.", "slots": {"t": "#2.turn", "x": "#2.luck_pts"}}
- Say what each number is: "luck" for luck_pts, "counterfactual swing" for swing_pts (a separate counterfactual quantity), "win chance" for fields ending in _wp_pct, "chance" for realised_prob and prob, "preferred" for gap_pts, and "choices, yours and your opponent's" for choices_pts (it counts both players). Write turns as "turn {t}". Units are added when the slot is filled.
- For example, "Luck was {l}." may cite #2.luck_pts; "The counterfactual swing was {s}." may cite #2.swing_pts. Never call swing_pts luck points.
- Positive luck_pts helped you; negative went against you. Use "helped you", "toward you" or "against you" only to match the sign. gap_pts says how much higher the engine rated its own option.
- For a chance below one half, hedge: "had only a {p} chance".
- Never call a decision a mistake, misplay, error or correct: the tools show the engine's preference, not a verdict. Describe a disagreement as "the engine preferred {best} by {g}". Never use the words "cost", "total" or "coach", and never add disagreements together.
- Name a Pokémon or move only through a slot, or if it appears in a tool output you cite."""

REPAIR_PROMPT = ("The verifier rejected this explanation:\n{failures}\n"
                 "Fix every listed problem and call submit_explanation again (same format). You may not call other "
                 "tools unless you need a field you do not have.")
# Added to the repair turn when the choices number was rejected. A luck word near that slot binds the slot to luck,
# so the number gets its own sentence, in the words the rule asks for. Repair text only: the checks are unchanged.
CHOICES_REPAIR = ("\nFor the choices number: give it a sentence of its own with no luck word in it and never the words "
                  "\"your choices\", written exactly: \"Choices, yours and your opponent's, were {c}.\" Say anything "
                  "about luck, or which was larger, in a different sentence.")


def repair_text(report):
    """The repair turn's message: the checker's failures, plus how to phrase the choices number if it was rejected."""
    text = REPAIR_PROMPT.format(failures=failure_text(report))
    if any("choices_pts" in (f.get("message") or "") for f in report["failures"]):
        text += CHOICES_REPAIR
    return text


# The turning-point answer is in the model's own words, but it must show these facts of the moment it chose, by slot.
TURNING_POINT_REQUIRED = ("turn", "what_happened", "standing", "wp_before_pct", "wp_after_pct", "luck_pts", "choices_pts")
LEANING_PCT = 70      # the eventual winner this far up before a move: a larger later move does not outrank an earlier one
OPEN_MAX_PCT = 60     # above this for the eventual winner, the answer may not call the game open
NEAR_TIE_PTS = 2
# Words the checker does not test (it was measured as it is, so this lives here): claims about who led.
FLIP_RE = re.compile(r"\bflip\w*|\b(?:chang\w*|swung|swap\w*|revers\w*|took|take[sn]?|taking|gave|lost)\b[^.;,]{0,25}\blead\b"
                     r"|\bput(?:s|ting)?\s+(?:you|your\s+opponent|the\s+opponent|a\s+different\s+side|the\s+other\s+side)"
                     r"\s+(?:ahead|behind|in\s+front)\b|\bovert(?:ook|ake|akes|aking)\b|\bcross(?:ed|es|ing)\b", re.I)
OPEN_RE = re.compile(r"\b(?:still|was|remained|wide)\s+(?:open|close|even|level|balanced|undecided|contested)\b|\bopen\s+game\b"
                     r"|\bclose\s+game\b|\bin\s+the\s+balance\b|\bup\s+for\s+grabs\b|\beither\s+way\b", re.I)
ALREADY_RE = re.compile(r"\balready\b[^.;]{0,30}\b(?:ahead|behind|lead\w*|trailing|winning|losing|lean\w*|decided)\b", re.I)
# A reason in words, needed when the standing itself does not give one (the chosen move did not put the winner ahead).
REASON_RE = re.compile(r"\b(?:largest|biggest|bigger|larger|greatest|more\s+than|because|unlike|whereas|lean\w*|stands?\s+out)\b", re.I)
SIZE_CLAIM_RE = re.compile(r"\b(?:largest|biggest|greatest)\b", re.I)
# "After turn 3 when you used X, which moved your win chance from a to b." never reaches a main clause.
NO_MAIN_CLAUSE_RE = re.compile(r"^\s*(?:(?:after|when)\b[^.]*,\s*which\b[^,.]*\.?\s*$"
                               r"|turn\s+\{[^}]+\}\s+(?:when|after)\b[^.]*,\s*which\b)", re.I)
ROUGH_RE = re.compile(r"\b(?:after\s+when|happened\s+when)\b", re.I)
CONTRAST_RE = re.compile(r"\b(?:whereas|unlike|rather\s+than|later|the\s+others?|other\s+(?:moves?|swings?|drops?|turns?))\b", re.I)


def _turning_point_moments(session):
    seen, out = set(), []
    for c in session.calls:
        if c["name"] == "list_key_moments" and (c["args"] or {}).get("kind") == "win_chance":
            for m in (c["output"] or {}).get("moments") or []:
                if m["turn"] not in seen:
                    seen.add(m["turn"])
                    out.append(m)
    return out


def _cited_facts(payload, session):
    """[(field, value)] of every slot the sentences actually use, in order."""
    out = []
    for s in (payload.get("sentences") if isinstance(payload, dict) else None) or []:
        if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not isinstance(s.get("slots"), dict):
            continue
        for name in PLACEHOLDER_RE.findall(s["text"]):
            try:
                _, field, value = session.resolve(s["slots"].get(name))
            except KeyError:
                continue
            out.append((field, value))
    return out


def chosen_moment(payload, session):
    """The listed win_chance moment whose required facts the answer all cites; None if there is none."""
    cited = set(_cited_facts(payload, session))
    for m in _turning_point_moments(session):
        if all((f, m[f]) in cited for f in TURNING_POINT_REQUIRED if f in m):
            return m
    return None


def _winner_pct(pct, session):
    """A win chance from the eventual winner's side; None if the game has no winner."""
    result = (session.review.get("summary") or {}).get("result")
    return pct if result == "win" else 100 - pct if result == "loss" else None


def _crossed(m):
    return (m["wp_before_pct"] - 50) * (m["wp_after_pct"] - 50) < 0 or (m["wp_before_pct"] == 50 and m["wp_after_pct"] != 50)


def _won_it_for_good(m, session):
    """It put the eventual winner ahead and they stayed there. A lead that changed again doesn't explain the game: a reader
    of the chart sees the later swing, however large."""
    lasting = ("for the rest of the game", "won the game on this turn")
    return (_crossed(m) and (_winner_pct(m["wp_after_pct"], session) or 0) > 50
            and any(x in (m.get("standing") or "") for x in lasting))


def _pick_problem(m, moments, session):
    """A smaller listed move needs a fact that sets it apart from each larger one: it put the eventual winner ahead for
    good, or the larger one started with the winner already well up. "Most telling" is not a fact."""
    size = lambda x: abs(x["wp_after_pct"] - x["wp_before_pct"])
    if m is moments[0] or size(moments[0]) < GRADUAL_MAX_PTS or _winner_pct(m["wp_after_pct"], session) is None:
        return None
    if _won_it_for_good(m, session):
        return None
    for x in moments:
        if x is m:
            return None
        if size(x) - size(m) > NEAR_TIE_PTS and _winner_pct(x["wp_before_pct"], session) < LEANING_PCT:
            return (f"turn {m['turn']} is a smaller move than turn {x['turn']}, did not put the eventual winner ahead for "
                    f"good, and turn {x['turn']} started before the winner was well up: choose turn {x['turn']}, or a listed "
                    "moment whose standing says it put the eventual winner ahead for the rest of the game")
    return None


def _claim_problems(payload, m, moments, session):
    """Who-led words in the answer's own text, compared with the win chances of the listed moments."""
    out = []
    before = _winner_pct(m["wp_before_pct"], session)
    sentences = (payload.get("sentences") if isinstance(payload, dict) else None) or []
    free = " ".join(PLACEHOLDER_RE.sub(" ", s.get("text") or "") for s in sentences)
    if not _won_it_for_good(m, session) and not REASON_RE.search(free):
        out.append(f"say in words why turn {m['turn']} rather than the others, for example that it was the largest move "
                   "toward the final result")
    if re.search(r"\bstanding\b", free, re.I):
        out.append("do not use the word \"standing\": put its slot in the sentence and say the rest in plain words")
    size = abs(m["wp_after_pct"] - m["wp_before_pct"])
    for s in sentences:
        text = PLACEHOLDER_RE.sub(" ", s.get("text") or "")
        if NO_MAIN_CLAUSE_RE.search(s.get("text") or ""):
            out.append("a sentence built as \"Turn {t} when ..., which ...\" or \"After ..., which ...\" has no main verb: "
                       "rewrite it, for example \"Turn {t} moved your win chance from {a} to {b} when {what_happened}.\"")
        hit = ROUGH_RE.search(text)
        if hit:
            out.append(f"\"{hit.group(0)}\" doesn't read correctly: write \"Turn {{t}} moved your win chance from {{a}} to {{b}} "
                       "when {what_happened}.\"")
        hit = SIZE_CLAIM_RE.search(text)
        cites_it = any((s.get("slots") or {}).get(n) == f"{m['id']}.turn" for n in PLACEHOLDER_RE.findall(s.get("text") or ""))
        if hit and cites_it and size < abs(moments[0]["wp_after_pct"] - moments[0]["wp_before_pct"]):
            out.append(f"\"{hit.group(0)}\" is not true of turn {m['turn']}: turn {moments[0]['turn']} moved the win chance more")
        hit = FLIP_RE.search(text)
        if hit and not _crossed(m):
            out.append(f"\"{hit.group(0)}\" is not supported: the win chance stayed on the same side of even on turn "
                       f"{m['turn']}; use its standing instead")
        hit = OPEN_RE.search(text)
        if hit and before is not None and before > OPEN_MAX_PCT:
            out.append(f"\"{hit.group(0)}\" is not supported: the eventual winner was already well up before turn "
                       f"{m['turn']}; use its standing instead")
        hit = ALREADY_RE.search(text)
        if hit and before is not None and CONTRAST_RE.search(text):
            later = [x for x in moments if x["turn"] > m["turn"] and _winner_pct(x["wp_before_pct"], session) < 50]
            if before > 50:
                out.append(f"\"{hit.group(0)}\" sets turn {m['turn']} apart from other moves, but the eventual winner was "
                           "already ahead before it too; use its standing instead")
            elif later:
                out.append(f"\"{hit.group(0)}\" is not true of turn {later[0]['turn']}: the eventual winner was not ahead "
                           "before it")
    return out


def _note_problem(payload, session):
    """The lookup's note that no single turn stands out is true and useful, so the answer has to keep it."""
    gradual = any("no single turn stands out" in ((c["output"] or {}).get("note") or "") for c in session.calls
                  if c["name"] == "list_key_moments")
    free = " ".join(PLACEHOLDER_RE.sub(" ", s.get("text") or "") for s in payload.get("sentences") or [])
    if gradual and not re.search(r"\bno\s+single\s+turn\b", free, re.I):
        return "the lookup says no single turn stands out: say so in the answer, in those words"
    gave_up = any((c["output"] or {}).get("end_note") for c in session.calls if c["name"] == "list_key_moments")
    if gave_up and not re.search(r"\b(?:forfeit\w*|timeout|timed\s+out|disconnect\w*)\b", free, re.I):
        return "the lookup's end_note applies: say in words how the game ended, and that this is not counted as a move"
    return None


def turning_point_failures(payload, session):
    """Failures in the checker's format if the answer does not show the required facts of a listed moment, picks a
    smaller move with no fact behind it, or says something about who led that the win chances do not show."""
    def fail(msg):
        return [{"rule": "content", "sentence": None, "slot": None, "message": msg}]
    if not any(c["name"] == "list_key_moments" and (c["args"] or {}).get("kind") == "win_chance"
               for c in session.calls):
        return fail("call list_key_moments with kind \"win_chance\" and choose one of its moments")
    moments = _turning_point_moments(session)
    if not moments:
        return []
    chosen = chosen_moment(payload, session)
    if chosen is not None:
        problems = [_pick_problem(chosen, moments, session), _note_problem(payload, session)] + _claim_problems(
            payload, chosen, moments, session)
        return [f for p in problems if p for f in fail(p)]
    cited = _cited_facts(payload, session)
    turns = [v for f, v in cited if f == "turn"]
    target = next((m for t in turns for m in moments if m["turn"] == t), None)
    if target is None:
        return fail("name the turn of one of the listed win_chance moments, citing its turn field")
    missing = [f"#{target['id'][1:]}.{f}" for f in TURNING_POINT_REQUIRED if f in target and (f, target[f]) not in cited]
    return fail("the answer must cite these facts of the moment you chose, each in a slot: " + ", ".join(missing))


def _capitalized(rendered):
    """A turning-point sentence may be one slot alone, a lowercase clause such as `standing`: start it with a capital."""
    for r in rendered["sentences"]:
        r["text"] = r["text"][:1].upper() + r["text"][1:]
        r["text"] = re.sub(r"(?<![\d.])([+−-]?1) points\b", r"\1 point", r["text"])   # the model writes "{l} points"
    rendered["text"] = " ".join(r["text"] for r in rendered["sentences"])
    return rendered


def _now():
    return time.time()


def run(review, question, client=None, mode="loop", verdicts=None, luck_reference=None, repair=True,
        template_fallback=True, max_tool_calls=MAX_TOOL_CALLS, offered=None):
    """-> result dict (see module doc). `client` is a GlimmerClient (None only for mode='template')."""
    t0 = _now()
    session = ToolSession(review, verdicts=verdicts, luck_reference=luck_reference, offered=offered)
    res = {"question": question, "mode": mode, "prompt_version": PROMPT_VERSION, "verifier_version": VERIFIER_VERSION,
           "steps": [], "attempts": [], "errors": [], "data_tool_calls": 0, "llm_requests": 0,
           "raw_pass": False, "after_repair_pass": False, "final": None}
    if mode == "template":
        return _finish_template(res, session, question, t0)

    turning_point = templates.question_kind(question)[0] == "turning_point"
    tools = all_tool_schemas(turning_point=turning_point)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"Question: {question}"}]
    if mode == "prefetch":
        pre = session.prefetch(turning_point=turning_point)
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
            if turning_point:  # stricter than the checker, never looser: its report is kept and only added to
                extra = turning_point_failures(payload, session)
                report["checker_pass"] = report["pass"]
                report["failures"] += extra
                report["pass"] = report["pass"] and not extra
        phase = "repair" if repaired else "raw"
        res["attempts"].append({"phase": phase, "payload": payload, "report": report})
        submitted = payload
        if report["pass"]:
            res["raw_pass"] = phase == "raw"
            res["after_repair_pass"] = True
            res["final"] = "glimmer_raw" if phase == "raw" else "glimmer_repaired"
            res["rendered"] = render(payload, session, res["final"])
            if turning_point:
                res["rendered"] = _capitalized(res["rendered"])
                res["chosen_turn"] = (chosen_moment(payload, session) or {}).get("turn")
            break
        if repair and not repaired:
            repaired = True
            msgs.append(tool_message(submit["id"], "submit_explanation", repair_text(report)))
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


def checks_of(res):
    """Each draft the model submitted and what the verifier said about it (shown on the review page)."""
    out = []
    for a in res.get("attempts") or []:
        report, payload = a.get("report") or {}, a.get("payload") or {}
        out.append({"phase": a.get("phase"), "pass": bool(report.get("pass")),
                    "draft": " ".join(str(s.get("text", "")) for s in payload.get("sentences") or [] if isinstance(s, dict)),
                    "failures": [f.get("message") for f in report.get("failures") or []][:5]})
    return out


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
