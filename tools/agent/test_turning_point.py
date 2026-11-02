#!/usr/bin/env python3
"""The turning-point question: list_key_moments(kind="win_chance"), its template, and the Q1 choices repair text.

  python3 tools/agent/test_turning_point.py
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import explain  # noqa: E402
import templates  # noqa: E402
from render import render  # noqa: E402
from tools import TOOL_SCHEMAS, ToolSession, all_tool_schemas  # noqa: E402
from verifier import verify  # noqa: E402

Q4_LABEL = "What was the turning point of this game?"


def review(result="loss", wps=(0.54, 0.47, 0.28, 0.20, 0.25, 0.01), lucks=(0.0, -0.08, -0.04, 0.0, 0.0, 0.0)):
    turns = [{"turn": i + 1, "win_prob": w, "luck": lucks[i], "action": "surf", "opponent_action": "switch dragonite",
              "luck_events": {"crit": {"u": 1}} if i == 1 else {}} for i, w in enumerate(wps)]
    return {"summary": {"result": result, "start_win_prob": 0.53, "total_luck": sum(lucks)},
            "bridge": {"s": 53, "l": -12, "c": -41, "L": -0.12}, "meta": {}, "turns": turns, "decisions": []}


def session(rv=None, offered=()):
    return ToolSession(rv or review(), offered=list(offered))


class TurningPointTool(unittest.TestCase):
    def test_largest_move_comes_first_whether_or_not_from_luck(self):
        ms = session().call("list_key_moments", {"kind": "win_chance"})["moments"]
        self.assertEqual([m["turn"] for m in ms], [5, 2, 3])  # 25 -> 1, then 47 -> 28, then 28 -> 20
        self.assertEqual((ms[0]["wp_before_pct"], ms[0]["wp_after_pct"], ms[0]["main_cause"]), (25, 1, "choices"))

    def test_parts_add_up_to_the_shown_move(self):
        for m in session().call("list_key_moments", {"kind": "win_chance"})["moments"]:
            self.assertEqual(m["wp_after_pct"] - m["wp_before_pct"], m.get("luck_pts", 0) + m.get("choices_pts", 0))

    def test_moves_toward_the_result_rank_above_a_bigger_brief_recovery(self):
        rv = review(wps=(0.40, 0.34, 0.05, 0.40, 0.30, 0.01), lucks=(0.0,) * 6)
        ms = session(rv).call("list_key_moments", {"kind": "win_chance"})["moments"]
        self.assertEqual(ms[0]["turn"], 2)  # -29 toward the loss, above the +35 recovery on turn 3
        self.assertNotEqual(ms[0]["turn"], 3)

    def test_luck_is_main_cause_only_when_it_pushes_the_way_of_the_move(self):
        rv = review(result="win", wps=(0.70, 0.98, 0.99), lucks=(0.51, 0.0, 0.0))
        m = session(rv).call("list_key_moments", {"kind": "win_chance", "k": 1})["moments"][0]
        self.assertEqual((m["main_cause"], m["luck_pts"], m["choices_pts"]), ("luck", 51, -23))
        rv = review(result="win", wps=(0.40, 0.70, 0.99), lucks=(-0.08, 0.0, 0.0))
        m = session(rv).call("list_key_moments", {"kind": "win_chance", "k": 1})["moments"][0]
        self.assertEqual(m["main_cause"], "choices")

    def test_a_gradual_game_says_so(self):
        rv = review(wps=(0.52, 0.45, 0.40, 0.33, 0.27, 0.20), lucks=(0.0,) * 6)
        out = session(rv).call("list_key_moments", {"kind": "win_chance"})
        self.assertIn("no single turn stands out", out["note"])

    def test_no_zero_luck_or_engine_field_without_a_displayed_disagreement(self):
        m = session().call("list_key_moments", {"kind": "win_chance", "k": 1})["moments"][0]
        for field in ("luck_pts", "engine_best", "gap_pts", "chosen"):
            self.assertNotIn(field, m)

    def test_engine_fields_only_for_the_exact_displayed_turn(self):
        rv = review()
        shown = {"turn": 5, "k": 0, "action": "surf", "engine_best": "switch dragonite", "decision_cost": 0.12,
                 "win_prob": 0.25}
        ms = session(rv, [shown]).call("list_key_moments", {"kind": "win_chance"})["moments"]
        self.assertEqual((ms[0]["gap_pts"], ms[0]["engine_best"]), (12, "switch to Dragonite"))
        self.assertNotIn("gap_pts", ms[1])

    def test_what_happened_reads_as_a_sentence(self):
        m = session().call("list_key_moments", {"kind": "win_chance", "k": 1})["moments"][0]
        self.assertEqual(m["what_happened"], "you used Surf and your opponent switched to Dragonite")

    def test_last_turn_is_not_a_move(self):
        ms = session().call("list_key_moments", {"kind": "win_chance", "k": 3})["moments"]
        self.assertNotIn(6, [m["turn"] for m in ms])


class SchemaBytes(unittest.TestCase):
    def test_other_questions_keep_the_schema_of_the_fixed_record(self):
        kinds = [t for t in all_tool_schemas() if t["function"]["name"] == "list_key_moments"][0]
        self.assertEqual(kinds["function"]["parameters"]["properties"]["kind"]["enum"], ["luck", "disagreement"])
        digest = hashlib.sha256(json.dumps(TOOL_SCHEMAS, sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, BASE_SCHEMA_SHA256)

    def test_turning_point_question_sees_the_new_kind(self):
        kinds = [t for t in all_tool_schemas(turning_point=True) if t["function"]["name"] == "list_key_moments"][0]
        self.assertIn("win_chance", kinds["function"]["parameters"]["properties"]["kind"]["enum"])


class TurningPointTemplate(unittest.TestCase):
    def check(self, rv, offered=()):
        s = session(rv, offered)
        payload = templates.build(s, Q4_LABEL)
        report = verify(payload, s)
        self.assertTrue(report["pass"], report["failures"])
        return render(payload, s, "template")["text"]

    def test_question_kind(self):
        self.assertEqual(templates.question_kind(Q4_LABEL)[0], "turning_point")
        self.assertEqual(templates.question_kind(Q4_LABEL + " Say what the engine preferred.")[0], "turning_point")
        self.assertEqual(templates.question_kind("What did the engine prefer on turn 4?")[0], "turn")

    def test_template_names_the_move_and_passes_the_checks(self):
        text = self.check(review())
        self.assertIn("The turning point was turn 5", text)
        self.assertIn("from 25% to 1%", text)
        self.assertNotIn("Luck: ", text)

    def test_template_with_luck_as_main_cause(self):
        text = self.check(review(result="win", wps=(0.70, 0.98, 0.99), lucks=(0.51, 0.0, 0.0)))
        self.assertIn("Luck on that turn was +51 points, the main cause of that move.", text)

    def test_template_with_luck_against_the_move(self):
        text = self.check(review(result="win", wps=(0.40, 0.70, 0.99), lucks=(-0.08, 0.0, 0.0)))
        self.assertIn("Luck on that turn was −8 points", text)
        self.assertIn("choices, yours and your opponent's (+38 points)", text)

    def test_template_without_luck(self):
        text = self.check(review())
        self.assertIn("That move came from choices, yours and your opponent's (−24 points).", text)

    def test_template_with_engine_preference(self):
        shown = {"turn": 5, "k": 0, "action": "surf", "engine_best": "switch dragonite", "decision_cost": 0.12,
                 "win_prob": 0.25}
        self.assertIn("Instead of Surf, the engine preferred switch to Dragonite by 12 points",
                      self.check(review(), [shown]))

    def test_template_for_a_gradual_game(self):
        text = self.check(review(wps=(0.52, 0.45, 0.40, 0.33, 0.27, 0.20), lucks=(0.0,) * 6))
        self.assertIn("No single turn stands out", text)
        self.assertIn("largest move toward the final result", text)
        self.assertNotIn("turning point", text)


class RequiredFacts(unittest.TestCase):
    """The answer's wording is free; the facts of the moment it chose are not."""

    def moments(self, s):
        return s.call("list_key_moments", {"kind": "win_chance", "k": 3})["moments"]

    def payload(self, m, fields):
        words = {"turn": "On turn {turn},", "what_happened": "{what_happened}.", "wp_before_pct": "Win chance was {wp_before_pct}",
                 "wp_after_pct": "and then win chance was {wp_after_pct}.", "luck_pts": "Luck was {luck_pts}.",
                 "choices_pts": "Choices, yours and your opponent's, were {choices_pts}."}
        return {"sentences": [{"text": " ".join(words[f] for f in fields),
                               "slots": {f: f"{m['id']}.{f}" for f in fields}}]}

    def test_a_lower_ranked_moment_counts_as_a_choice(self):
        s = session()
        m = self.moments(s)[1]
        fields = [f for f in explain.TURNING_POINT_REQUIRED if f in m]
        self.assertEqual(explain.chosen_moment(self.payload(m, fields), s)["turn"], m["turn"])
        self.assertEqual(explain.turning_point_failures(self.payload(m, fields), s), [])

    def test_a_missing_fact_is_named(self):
        s = session()
        m = self.moments(s)[1]
        fails = explain.turning_point_failures(self.payload(m, ["turn", "wp_before_pct", "wp_after_pct"]), s)
        self.assertEqual(fails[0]["rule"], "content")
        self.assertIn(f"{m['id']}.what_happened", fails[0]["message"].replace("##", "#"))

    def test_a_field_the_moment_does_not_have_is_not_required(self):
        s = session()
        m = self.moments(s)[0]
        self.assertNotIn("luck_pts", m)
        fields = [f for f in explain.TURNING_POINT_REQUIRED if f in m]
        self.assertEqual(explain.turning_point_failures(self.payload(m, fields), s), [])

    def test_an_unused_slot_does_not_count(self):
        s = session()
        m = self.moments(s)[1]
        p = self.payload(m, ["turn", "wp_before_pct", "wp_after_pct"])
        p["sentences"][0]["slots"]["what_happened"] = f"{m['id']}.what_happened"
        self.assertIsNone(explain.chosen_moment(p, s))

    def test_must_look_up_the_moments(self):
        s = session()
        rec = s.call("get_summary", {})
        fails = explain.turning_point_failures({"sentences": [{"text": "Luck was {l}.", "slots": {"l": f"{rec['id']}.luck_pts"}}]}, s)
        self.assertIn("win_chance", fails[0]["message"])

    def test_loop_repairs_then_falls_back_and_template_still_catches(self):
        class Client:
            def __init__(self):
                self.n = 0

            def chat(self, msgs, tools=None, tool_choice=None):
                self.n += 1
                if self.n == 1:
                    calls = [("list_key_moments", {"kind": "win_chance", "k": 3})]
                else:  # passes the checker, but never shows the facts of a moment
                    calls = [("submit_explanation", {"sentences": [{"text": "Luck decided it.", "slots": {}}]})]
                tcs = [{"id": f"c{self.n}", "name": n, "arguments": a, "parse_error": None, "raw_arguments": ""}
                       for n, a in calls]
                return {"message": {"content": ""}, "tool_calls": tcs, "finish_reason": "tool_calls", "wall_s": 0,
                        "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "decode_tps": 0, "reasoning": ""}
        res = explain.run(review(), Q4_LABEL, client=Client(), mode="loop", offered=[])
        self.assertEqual(res["final"], "template")
        self.assertEqual(len(res["attempts"]), 2)
        self.assertTrue(res["attempts"][0]["report"]["checker_pass"])
        self.assertFalse(res["attempts"][0]["report"]["pass"])
        self.assertIn("The turning point was turn 5", res["rendered"]["text"])


class ChoicesRepairText(unittest.TestCase):
    """The rule is unchanged; only the message that tells the model how to satisfy it is clearer."""

    def report(self, text):
        s = ToolSession(review())
        rid = s.call("get_summary", {})["id"]
        return verify({"sentences": [{"text": text, "slots": {"c": f"{rid}.choices_pts", "l": f"{rid}.luck_pts"}}]}, s)

    def assert_fails_with_exact_wording(self, text):
        report = self.report(text)
        self.assertFalse(report["pass"])
        self.assertIn("written exactly: \"Choices, yours and your opponent's, were {c}.\"", explain.repair_text(report))

    def test_other_failures_get_the_old_repair_text(self):
        report = self.report("Luck was {c}.")
        self.assertFalse(report["pass"])
        report = {"failures": [f for f in report["failures"] if "choices_pts" not in f["message"]]}
        self.assertEqual(explain.repair_text(report), explain.REPAIR_PROMPT.format(failures=explain.failure_text(report)))

    def test_your_choices_still_fails(self):
        self.assert_fails_with_exact_wording("Your choices were {c}.")

    def test_without_both_players_still_fails(self):
        self.assert_fails_with_exact_wording("The combined choices contribution was {c}.")

    def test_luck_word_after_the_slot_still_fails(self):
        self.assert_fails_with_exact_wording("Choices, yours and your opponent's, contributed {c} points while luck "
                                             "contributed {l} points.")

    def test_the_suggested_wording_passes(self):
        self.assertTrue(self.report("Choices, yours and your opponent's, were {c}.")["pass"])


BASE_SCHEMA_SHA256 = "c8e92e0afa2ec1248af683f0f40733d33d792d15e118074457e0bc25fd5d60fd"  # at commit f2af975

if __name__ == "__main__":
    unittest.main()
