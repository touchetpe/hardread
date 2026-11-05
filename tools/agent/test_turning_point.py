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
        words = {"turn": "On turn {turn},", "what_happened": "{what_happened}.", "standing": "Then {standing}.",
                 "wp_before_pct": "Win chance was {wp_before_pct}",
                 "wp_after_pct": "and then win chance was {wp_after_pct}.", "luck_pts": "Luck was {luck_pts}.",
                 "choices_pts": "Choices, yours and your opponent's, were {choices_pts}."}
        return {"sentences": [{"text": " ".join(words[f] for f in fields) + " It mattered more than the others.",
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


class Standing(unittest.TestCase):
    """Who led around a move is worked out by code, from the same win chances the answer shows."""

    def standing(self, turn, **kw):
        ms = session(review(**kw)).call("list_key_moments", {"kind": "win_chance"})["moments"]
        return next(m["standing"] for m in ms if m["turn"] == turn)

    def test_a_move_that_flips_the_lead_for_good(self):
        wps, lucks = (0.40, 0.60, 0.70, 0.90, 0.95, 0.99), (0.0,) * 6
        self.assertEqual(self.standing(1, result="win", wps=wps, lucks=lucks), "it put you ahead for the rest of the game")

    def test_a_lead_that_changed_again(self):
        wps, lucks = (0.40, 0.60, 0.45, 0.90, 0.95, 0.99), (0.0,) * 6
        self.assertEqual(self.standing(1, result="win", wps=wps, lucks=lucks),
                         "it put you ahead, though the lead changed again later")

    def test_a_lead_the_player_held_to_the_end_of_the_list_but_lost(self):
        wps, lucks = (0.40, 0.70, 0.80, 0.85, 0.85, 0.85), (0.0,) * 6
        self.assertEqual(self.standing(1, result="loss", wps=wps, lucks=lucks),
                         "it put you ahead, though the lead changed again later")

    def test_already_ahead_and_never_trailed(self):
        wps, lucks = (0.60, 0.63, 0.79, 0.85, 0.90, 0.99), (0.0,) * 6
        self.assertEqual(self.standing(2, result="win", wps=wps, lucks=lucks),
                         "you were already ahead before it and never trailed in this game")

    def test_already_ahead_after_trailing_earlier(self):
        wps, lucks = (0.40, 0.60, 0.70, 0.95, 0.96, 0.99), (0.0,) * 6
        self.assertEqual(self.standing(3, result="win", wps=wps, lucks=lucks), "you were already ahead before it")

    def test_the_opponent_side_reads_the_same_way(self):
        self.assertEqual(self.standing(5), "your opponent was already ahead before it")
        wps, lucks = (0.60, 0.40, 0.30, 0.20, 0.10, 0.01), (0.0,) * 6
        self.assertEqual(self.standing(1, wps=wps, lucks=lucks), "it put your opponent ahead for the rest of the game")

    def test_template_states_it(self):
        s = session()
        text = render(templates.build(s, Q4_LABEL), s, "template")["text"]
        self.assertIn("from 25% to 1%, and your opponent was already ahead before it.", text)


class ChoiceNeedsAFact(unittest.TestCase):
    """A smaller move has to be set apart by a listed fact, or the answer goes to repair."""

    def check(self, chosen_turn, **kw):
        s = session(review(**kw))
        ms = s.call("list_key_moments", {"kind": "win_chance", "k": 3})["moments"]
        m = next(x for x in ms if x["turn"] == chosen_turn)
        return explain._pick_problem(m, ms, s)

    def test_the_largest_needs_nothing(self):
        wps, lucks = (0.44, 0.33, 0.21, 0.35, 0.02, 0.01), (-0.09, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.assertIsNone(self.check(4, wps=wps, lucks=lucks))

    def test_a_smaller_move_with_no_fact_is_refused(self):
        wps, lucks = (0.44, 0.33, 0.21, 0.35, 0.02, 0.01), (-0.09, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.assertIn("choose turn 4", self.check(1, wps=wps, lucks=lucks))

    def test_a_smaller_move_that_put_the_winner_ahead_is_fine(self):
        wps, lucks = (0.58, 0.48, 0.45, 0.20, 0.10, 0.01), (0.0,) * 6
        self.assertIsNone(self.check(1, wps=wps, lucks=lucks))

    def test_a_smaller_move_is_fine_if_the_larger_one_started_with_the_winner_well_up(self):
        wps, lucks = (0.60, 0.72, 0.80, 0.98, 0.99, 1.0), (0.0,) * 6
        self.assertIsNone(self.check(1, result="win", wps=wps, lucks=lucks))

    def test_a_near_tie_is_a_judgement_call(self):
        wps, lucks = (0.60, 0.45, 0.31, 0.22, 0.15, 0.01), (0.0,) * 6
        self.assertIsNone(self.check(2, wps=wps, lucks=lucks))

    def test_a_gradual_game_has_nothing_to_refuse(self):
        self.assertIsNone(self.check(3, wps=(0.52, 0.45, 0.40, 0.33, 0.27, 0.20), lucks=(0.0,) * 6))


class ClaimsAboutTheLead(unittest.TestCase):
    """The checker does not read these words; the turning-point check does."""

    def problems(self, text, chosen_turn, **kw):
        s = session(review(**kw))
        ms = s.call("list_key_moments", {"kind": "win_chance", "k": 3})["moments"]
        m = next(x for x in ms if x["turn"] == chosen_turn)
        return explain._claim_problems({"sentences": [{"text": text, "slots": {}}]}, m, ms, s)

    AHEAD = dict(result="win", wps=(0.56, 0.63, 0.79, 0.74, 0.84, 0.99), lucks=(0.0,) * 6)
    FLIP = dict(wps=(0.62, 0.46, 0.33, 0.21, 0.20, 0.12), lucks=(0.0,) * 6)

    def test_the_answer_that_prompted_this(self):
        text = "The shift came while the game was still open, whereas the later increases happened after you were already ahead."
        self.assertEqual(len(self.problems(text, 2, **self.AHEAD)), 2)

    def test_flipped_the_lead_needs_a_flip(self):
        self.assertTrue(self.problems("It flipped the lead.", 2, **self.AHEAD))
        self.assertFalse(self.problems("It flipped the lead.", 1, **self.FLIP))

    def test_true_contrast_passes(self):
        text = "It came while the game was still open, unlike later drops when you were already behind."
        self.assertEqual(self.problems(text, 1, **self.FLIP), [])

    def test_contrast_with_a_later_move_that_does_not_hold_fails(self):
        rv = dict(wps=(0.65, 0.48, 0.80, 0.10, 0.05, 0.01), lucks=(0.0,) * 6)  # the largest drop, on turn 3, starts at 80%
        self.assertTrue(self.problems("Later drops came when you were already behind.", 1, **rv))

    def test_saying_the_whole_game_leaned_is_not_a_contrast(self):
        text = "The biggest moves all came after you were already ahead, so the game was already leaning your way."
        self.assertEqual(self.problems(text, 2, **self.AHEAD), [])

    def test_a_reason_is_needed_unless_the_move_put_the_winner_ahead(self):
        self.assertTrue(self.problems("It mattered.", 2, **self.AHEAD))
        self.assertEqual(self.problems("It was the largest move toward the final result.", 2, **self.AHEAD), [])
        self.assertEqual(self.problems("It mattered.", 1, **self.FLIP), [])

    def test_the_field_name_stays_out_of_the_answer(self):
        self.assertTrue(self.problems("Its standing shows it put your opponent ahead.", 1, **self.FLIP))

    def test_largest_is_checked_against_the_list(self):
        s = session(review(**self.FLIP))
        ms = s.call("list_key_moments", {"kind": "win_chance", "k": 3})["moments"]
        m = next(x for x in ms if x["turn"] == 3)
        self.assertEqual(ms[0]["turn"], 1)
        said = {"text": "Turn {t} was the largest of the listed moves.", "slots": {"t": f"{m['id']}.turn"}}
        self.assertTrue(any("not true of turn" in p for p in explain._claim_problems({"sentences": [said]}, m, ms, s)))

    def test_open_is_fine_when_the_winner_is_not_well_up(self):
        self.assertEqual(self.problems("The game was still open.", 1, **self.FLIP), [])
        rv = dict(result="win", wps=(0.63, 0.79, 0.74, 0.84, 0.90, 0.99), lucks=(0.0,) * 6)
        self.assertTrue(self.problems("The game was still open.", 1, **rv))


class BadAnswersThroughTheLoop(unittest.TestCase):
    """Real drafts these checks never met in the measured runs. Each passes the checker and cites every required fact,
    so only the turning-point check can stop it. The same bad draft twice ends in the template."""
    REASON = "It mattered more than the others."

    def run_with(self, rv, turn, extra):
        s = session(rv)
        ms = s.call("list_key_moments", {"kind": "win_chance", "k": 3})["moments"]
        m = next(x for x in ms if x["turn"] == turn)
        slots = {f: f"{m['id']}.{f}" for f in explain.TURNING_POINT_REQUIRED if f in m}
        text = ["The turning point was turn {turn}, when {what_happened}, and your win chance went from {wp_before_pct} "
                "to {wp_after_pct}.", "On the lead: {standing}.", extra]
        if "luck_pts" in m:
            text.append("Luck was {luck_pts}.")
        if "choices_pts" in m:
            text[1] += " Choices, yours and your opponent's, were {choices_pts}."
        sentences = [{"text": t, "slots": {n: slots[n] for n in explain.PLACEHOLDER_RE.findall(t)}} for t in text]

        class Client:
            n = 0

            def chat(self, msgs, tools=None, tool_choice=None):
                Client.n += 1
                calls = ([("list_key_moments", {"kind": "win_chance", "k": 3})] if Client.n == 1
                         else [("submit_explanation", {"sentences": sentences})])
                tcs = [{"id": f"c{Client.n}", "name": n, "arguments": a, "parse_error": None, "raw_arguments": ""}
                       for n, a in calls]
                return {"message": {"content": ""}, "tool_calls": tcs, "finish_reason": "tool_calls", "wall_s": 0,
                        "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "decode_tps": 0, "reasoning": ""}
        return explain.run(rv, Q4_LABEL, client=Client(), mode="loop", offered=[])

    def assert_stopped_by_the_check(self, res, words):
        first = res["attempts"][0]["report"]
        self.assertTrue(first["checker_pass"], first["failures"])
        self.assertFalse(first["pass"])
        self.assertTrue(any(words in f["message"] for f in first["failures"]), first["failures"])
        self.assertEqual(res["final"], "template")
        self.assertEqual(len(res["attempts"]), 2)

    AHEAD = dict(result="win", wps=(0.56, 0.63, 0.79, 0.74, 0.84, 0.99), lucks=(0.0, 0.05, 0.0, 0.0, 0.0, 0.0))
    SLIDE = dict(wps=(0.44, 0.33, 0.21, 0.35, 0.02, 0.01), lucks=(-0.09, 0.0, 0.0, 0.0, 0.0, 0.0))
    RECOVERY = dict(wps=(0.65, 0.48, 0.80, 0.10, 0.05, 0.01), lucks=(0.0,) * 6)

    def test_control_an_honest_draft_passes(self):
        res = self.run_with(review(**self.AHEAD), 2, "It was the largest move toward the final result.")
        self.assertEqual(res["final"], "glimmer_raw")

    def test_a_sentence_that_is_only_a_slot_starts_with_a_capital(self):
        res = self.run_with(review(**self.AHEAD), 2, "It was the largest move toward the final result.")
        self.assertEqual(res["final"], "glimmer_raw")
        rendered = {"sentences": [{"text": "you were already ahead before it."}, {"text": "Luck was +5 points."}]}
        self.assertEqual(explain._capitalized(rendered)["text"], "You were already ahead before it. Luck was +5 points.")

    def test_pick_a_smaller_move_with_nothing_behind_it(self):
        res = self.run_with(review(**self.SLIDE), 1, self.REASON)
        self.assert_stopped_by_the_check(res, "is a smaller move than turn 4")
        self.assertIn("The turning point was turn 4", res["rendered"]["text"])

    def collapse(self):
        """The player climbed to 97% and was knocked out: the last turn is the story a reader of the chart sees."""
        rv = review(wps=(0.60, 0.40, 0.70, 0.90, 0.97, 0.97), lucks=(0.0,) * 6)
        rv["meta"] = {"end_reason": "ko"}
        return rv

    def test_a_lead_that_changed_again_does_not_excuse_ignoring_the_last_turn(self):
        res = self.run_with(self.collapse(), 1, "It put your opponent ahead, so it was the turning point.")
        self.assert_stopped_by_the_check(res, "is a smaller move than turn 6")
        text = res["rendered"]["text"]
        self.assertIn("The turning point was turn 6", text)
        self.assertIn("from 97% to 0%", text)
        self.assertIn("won the game on this turn", text)

    def test_the_last_turn_itself_passes(self):
        res = self.run_with(self.collapse(), 6, "It was the largest move toward the final result.")
        self.assertEqual(res["final"], "glimmer_raw")

    def test_a_smaller_move_that_won_it_for_good_still_passes(self):
        rv = review(wps=(0.55, 0.45, 0.44, 0.04, 0.03, 0.02), lucks=(0.0,) * 6)
        res = self.run_with(rv, 1, "It put your opponent ahead for good.")
        self.assertEqual(res["final"], "glimmer_raw")

    def test_one_point_is_singular(self):
        rendered = {"sentences": [{"text": "Luck was 1 points against you."}, {"text": "Choices were −1 points."},
                                  {"text": "Luck was 11 points, and 21 points."}]}
        self.assertEqual(explain._capitalized(rendered)["text"],
                         "Luck was 1 point against you. Choices were −1 point. Luck was 11 points, and 21 points.")

    def test_a_sentence_with_no_main_clause_is_sent_back(self):
        draft = "After turn 3 when you moved, which moved your win chance from 59% to 49%."
        self.assertTrue(explain.NO_MAIN_CLAUSE_RE.search(draft))
        for fine in ("When you moved, your win chance fell.", "Turn 3 moved your win chance when you moved, which mattered.",
                     "When you moved, which was odd, your win chance fell.",
                     "Turn {t} moved your win chance from {a} to {b} when {w}, which mattered.",
                     "Turn {t} when {w} was the turning point."):
            self.assertFalse(explain.NO_MAIN_CLAUSE_RE.search(fine), fine)
        for rough in ("Turn {t} when {w}, which moved your win chance from {a} to {b}.",
                      "Turn {t} when {w}, which moved your win chance from {a} to {b}, the largest move toward the final result."):
            self.assertTrue(explain.NO_MAIN_CLAUSE_RE.search(rough), rough)
        for rough in ("Turn {t} was the turning point after when {w}.", "Turn {t} happened when {w}."):
            self.assertTrue(explain.ROUGH_RE.search(rough), rough)

    def test_claim_flipped_the_lead_when_it_did_not(self):
        self.assert_stopped_by_the_check(self.run_with(review(**self.AHEAD), 2, "It flipped the lead, so it mattered most."),
                                         "stayed on the same side of even")

    def test_claim_still_open_with_the_winner_well_up(self):
        self.assert_stopped_by_the_check(self.run_with(review(**self.AHEAD), 2, "It was largest while the game was still open."),
                                         "already well up")

    def test_claim_contrast_when_the_chosen_turn_was_ahead_too(self):
        self.assert_stopped_by_the_check(
            self.run_with(review(**self.AHEAD), 2, "It was larger, whereas later moves came after you were already ahead."),
            "already ahead before it too")

    def test_claim_about_a_later_move_that_is_not_true(self):
        self.assert_stopped_by_the_check(
            self.run_with(review(**self.RECOVERY), 1, "Later drops came when you were already behind."),
            "the eventual winner was not ahead before it")

    def test_a_missing_reason(self):
        self.assert_stopped_by_the_check(self.run_with(review(**self.AHEAD), 2, "It mattered."), "say in words why")

    def test_a_dropped_note(self):
        gradual = dict(wps=(0.52, 0.45, 0.40, 0.33, 0.27, 0.20), lucks=(0.0,) * 6)
        res = self.run_with(review(**gradual), 1, "It was the largest move toward the final result.")
        self.assert_stopped_by_the_check(res, "no single turn stands out")
        self.assertIn("No single turn stands out", res["rendered"]["text"])
        res = self.run_with(review(**gradual), 1, "No single turn stands out, but it was the largest move.")
        self.assertEqual(res["final"], "glimmer_raw")

    def test_known_limit_a_paraphrase_is_not_caught(self):
        # the word lists are short: this false claim about the lead gets through, and only the reader would catch it
        res = self.run_with(review(**self.AHEAD), 2, "It was the largest move, and it carried you into the lead.")
        self.assertEqual(res["final"], "glimmer_raw")


class HowTheGameEnded(unittest.TestCase):
    """The last turn, for someone reading the chart."""

    def game(self, end, result="loss", wps=(0.60, 0.45, 0.70, 0.74, 0.90, 0.97)):
        rv = review(result=result, wps=wps, lucks=(0.0,) * 6)
        rv["meta"] = {"end_reason": end}
        return rv

    def listed(self, rv):
        return session(rv).call("list_key_moments", {"kind": "win_chance", "k": 3})

    def test_a_knockout_ends_the_last_move_at_the_result(self):
        m = self.listed(self.game("ko"))["moments"][0]
        self.assertEqual((m["turn"], m["wp_before_pct"], m["wp_after_pct"]), (6, 97, 0))
        self.assertEqual(m["standing"], "your opponent was not ahead before it and won the game on this turn")
        self.assertEqual(m["wp_after_pct"] - m["wp_before_pct"], m.get("luck_pts", 0) + m.get("choices_pts", 0))

    def test_a_knockout_win_already_ahead(self):
        m = self.listed(self.game("ko", "win", (0.60, 0.62, 0.64, 0.66, 0.68, 0.71)))["moments"][0]
        self.assertEqual((m["turn"], m["wp_after_pct"]), (6, 100))
        self.assertEqual(m["standing"], "you were already ahead before it and never trailed in this game")

    def test_a_small_last_step_does_not_outrank_the_game(self):
        m = self.listed(self.game("ko", "loss", (0.60, 0.30, 0.20, 0.10, 0.05, 0.02)))["moments"][0]
        self.assertEqual(m["turn"], 1)

    def test_other_questions_see_no_change(self):
        s = session(self.game("ko"))
        self.assertNotIn("wp_after_pct", s.call("get_turn", {"turn": 6}))

    def test_a_forfeit_is_not_a_move_but_the_lookup_says_so(self):
        for end in ("forfeit", "timeout", "disconnect"):
            out = self.listed(self.game(end))
            self.assertNotIn(6, [m["turn"] for m in out["moments"]])
            self.assertIn(f"the game ended by {end}", out["end_note"])

    def test_no_forfeit_note_when_the_jump_is_not_larger(self):
        out = self.listed(self.game("forfeit", "loss", (0.60, 0.30, 0.20, 0.10, 0.05, 0.02)))
        self.assertNotIn("end_note", out)
        self.assertNotIn("end_note", self.listed(self.game("ko")))

    def test_template_says_how_the_game_ended_and_passes_the_checks(self):
        s = session(self.game("forfeit"))
        payload = templates.build(s, Q4_LABEL)
        report = verify(payload, s)
        self.assertTrue(report["pass"], report["failures"])
        self.assertIn("The game ended by forfeit", render(payload, s, "template")["text"])

    def test_answer_must_say_how_the_game_ended(self):
        s = session(self.game("forfeit"))
        s.call("list_key_moments", {"kind": "win_chance", "k": 3})
        said = lambda t: explain._note_problem({"sentences": [{"text": t, "slots": {}}]}, s)
        self.assertIn("end_note", said("It was the largest move."))
        self.assertIsNone(said("It was the largest move in play, before the forfeit."))


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
