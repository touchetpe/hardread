#!/usr/bin/env python3
"""Unit tests: verifier (7 rules + structure), renderer, tool wrappers, templates (research/28 S2.5).

  python3 tools/agent/test_verifier.py [--json scratch_ds/agent/verifier_tests.json]

Self-contained: a synthetic review (no gitignored data needed). Adversarial cases cover wrong bindings,
digits and number words, polarity, negated verdicts, missing hedges, uncited names, and the research/28 §0.1
fix (digits inside a cited species/move name such as Porygon2 are allowed).
"""
import json
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import templates  # noqa: E402
from render import render  # noqa: E402
from tools import ToolSession, field_kind  # noqa: E402
from verifier import verify  # noqa: E402

REVIEW = {
    "meta": {"side": "p1", "end_reason": "ko", "end_turn": 5},
    "bridge": {"version": 1, "S": 0.52, "L": -0.18, "R": 0.0, "C": -0.34, "s": 52, "l": -18, "c": -34,
               "result": "loss", "swingy": False, "end_tag": None},
    "summary": {"result": "loss", "start_win_prob": 0.52, "total_luck": -0.18,
                "win_prob_curve": [[1, 0.52], [2, 0.55], [3, 0.40], [4, 0.30], [5, 0.10]]},
    "turns": [
        {"turn": 1, "win_prob": 0.52, "action": "knockoff", "opponent_action": "switch porygon2", "luck": 0.0,
         "luck_events": {}, "luck_realised_prob": 1.0, "chance_swing_vs_counterfactual": 0.0,
         "decision_cost": 0.0, "engine_best": "knockoff"},
        {"turn": 2, "win_prob": 0.55, "action": "swordsdance", "opponent_action": "triattack", "luck": -0.15,
         "luck_events": {"crit": {"u": 1, "o": 0}}, "luck_realised_prob": 0.0417,
         "chance_swing_vs_counterfactual": -0.2, "decision_cost": 0.09, "engine_best": "knockoff"},
        {"turn": 3, "win_prob": 0.40, "action": "switch greattusk", "opponent_action": "icebeam", "luck": 0.06,
         "luck_events": {"miss_by": {"u": 0, "o": 1}}, "luck_realised_prob": 0.1,
         "chance_swing_vs_counterfactual": 0.08, "decision_cost": 0.01, "engine_best": "switch greattusk"},
        {"turn": 4, "win_prob": 0.30, "action": "headlongrush", "opponent_action": "recover", "luck": -0.09,
         "luck_events": {}, "luck_realised_prob": 0.3, "chance_swing_vs_counterfactual": -0.1,
         "decision_cost": 0.03, "engine_best": "rapidspin"},
        {"turn": 5, "win_prob": 0.10, "action": "rapidspin", "opponent_action": "triattack", "luck": 0.0,
         "luck_events": {}, "luck_realised_prob": 1.0, "chance_swing_vs_counterfactual": 0.0,
         "decision_cost": 0.0, "engine_best": "rapidspin"},
    ],
    "decisions": [
        {"turn": 0, "k": 0, "kind": "teampreview", "action": "switch kingambit", "win_prob": 0.52,
         "engine_best": "switch kingambit", "engine_best_win_prob": 0.52, "chosen_win_prob": 0.52,
         "decision_cost": 0.0, "top_alternatives": [], "opponent_policy_top": []},
        {"turn": 2, "k": 0, "kind": "move", "action": "swordsdance", "win_prob": 0.55, "engine_best": "knockoff",
         "engine_best_win_prob": 0.60, "chosen_win_prob": 0.51, "decision_cost": 0.09,
         "top_alternatives": [{"action": "knockoff", "win_prob": 0.60, "share": 0.5},
                              {"action": "suckerpunch", "win_prob": 0.56, "share": 0.3}],
         "opponent_policy_top": [["triattack", 0.41], ["switch porygon2", 0.22], ["recover", 0.2]]},
        {"turn": 4, "k": 0, "kind": "move", "action": "headlongrush", "win_prob": 0.30, "engine_best": "rapidspin",
         "engine_best_win_prob": 0.33, "chosen_win_prob": 0.30, "decision_cost": 0.03,
         "top_alternatives": [], "opponent_policy_top": []},
        {"turn": 5, "k": 0, "kind": "move", "action": "rapidspin", "win_prob": 0.10, "engine_best": "earthquake",
         "engine_best_win_prob": 0.19, "chosen_win_prob": 0.10, "decision_cost": 0.09,
         "top_alternatives": [], "opponent_policy_top": []},
    ],
}
VERDICTS = {"2:0": {"verdict": "Within noise", "X": "knockoff", "chosen": "swordsdance", "reference": "engine",
                    "delta_U": [0.04, 0.07, 0.01], "delta_R": [0.02, 0.03, -0.01], "mean_delta_U": 0.04,
                    "mean_delta_R": 0.0133}}  # the shape tools/review/verdict.py returns


def S(*sents):
    return {"sentences": [{"text": t, "slots": sl} for t, sl in sents]}


class Base(unittest.TestCase):
    def setUp(self):
        self.s = ToolSession(REVIEW, verdicts=VERDICTS)
        self.summary = self.s.call("get_summary", {})                                   # #1
        self.luck = self.s.call("list_key_moments", {"kind": "luck", "k": 2})["moments"]  # turn 2 (#2), turn 4 (#3)
        self.dis = self.s.call("list_key_moments", {"kind": "disagreement"})["moments"]  # turn 2 only (#4)

    def rules(self, payload):
        rep = verify(payload, self.s)
        return rep["pass"], sorted({f["rule"] for f in rep["failures"]}), rep


class TestToolsAndRender(Base):
    def test_tools_records_and_filter(self):
        self.assertEqual(self.summary["luck_pts"], -18)
        self.assertEqual(self.summary["choices_pts"], -34)
        self.assertEqual([m["turn"] for m in self.luck], [2, 4])
        # turn 4 (cost 0.03 < 0.05) and turn 5 (WP 0.10 < 0.20) are filtered out; never summed
        self.assertEqual([m["turn"] for m in self.dis], [2])
        self.assertEqual(self.dis[0]["gap_pts"], 9)

    def test_field_kinds(self):
        for f, k in [("luck_pts", "pts"), ("wp_before_pct", "wp"), ("start_wp_pct", "wp"), ("realised_prob", "prob"),
                     ("prob", "prob"), ("turn", "turn"), ("engine_best", "name"), ("verdict", "verdict")]:
            self.assertEqual(field_kind(f), k, f)

    def test_render_copies_numbers(self):
        m = self.luck[0]["id"]
        out = render(S(("On turn {t}, luck swung {x} against you.", {"t": f"{m}.turn", "x": f"{m}.luck_pts"}),
                       ("What happened had only a {p} chance.", {"p": f"{m}.realised_prob"})), self.s)
        self.assertEqual(out["text"], "On turn 2, luck swung 15 points against you. What happened had only a 4.2% "
                                      "chance.")

    def test_render_signed_and_unit_not_doubled(self):
        out = render(S(("Luck over the game came to {l} points.", {"l": "#1.luck_pts"})), self.s)
        self.assertEqual(out["text"], "Luck over the game came to −18 points.")

    def test_render_keeps_sign_of_slot_without_polarity(self):
        # smoke run 1 regression: "toward you" describes {l}, not {c}; {c} must keep its minus sign
        self.s.verdicts = {}
        out = render(S(("Luck came to {l} points toward you while choices came to {c} points overall.",
                        {"l": "#1.luck_pts", "c": "#1.choices_pts"})), self.s)
        self.assertIn("−34 points overall", out["text"])
        self.assertIn("18 points toward you", out["text"])

    def test_render_no_unit_doubling(self):
        m = self.luck[0]["id"]
        out = render(S(("It gave {x} luck points against you, a {p} percent chance.",
                        {"x": f"{m}.luck_pts", "p": f"{m}.realised_prob"})), self.s)
        self.assertEqual(out["text"], "It gave 15 luck points against you, a 4.2% chance.")

    def test_templates_pass_verifier(self):
        for q in ["Was this game decided by luck or by choices?", "What was the biggest piece of luck?",
                  "Where did the engine disagree most with my choices?", "What happened on turn 3?"]:
            s = ToolSession(REVIEW)
            p = templates.build(s, q)
            rep = verify(p, s)
            self.assertTrue(rep["pass"], (q, rep["failures"]))
            self.assertNotIn("?", render(p, s)["text"].replace("?", "") or "")

    def test_template_uses_models_selection(self):
        s = ToolSession(REVIEW)
        s.call("list_key_moments", {"kind": "luck", "k": 1})
        n_before = len(s.calls)
        p = templates.build(s, "What was the biggest piece of luck?")
        self.assertEqual(len(s.calls), n_before)  # no new fetch: phrased from the model's own selection
        self.assertTrue(verify(p, s)["pass"])


class TestVerifierPass(Base):
    def test_good_luck_sentence(self):
        m = self.luck[0]["id"]
        ok, rules, rep = self.rules(S(("On turn {t}, luck swung {x} against you.",
                                       {"t": f"{m}.turn", "x": f"{m}.luck_pts"})))
        self.assertTrue(ok, rep)

    def test_good_disagreement_sentence(self):
        d = self.dis[0]["id"]
        ok, rules, rep = self.rules(S(("On turn {t}, the engine preferred {best} to {chosen} by {g}.",
                                       {"t": f"{d}.turn", "best": f"{d}.engine_best", "chosen": f"{d}.chosen",
                                        "g": f"{d}.gap_pts"})))
        self.assertTrue(ok, rep)

    def test_good_summary_win_chance(self):
        ok, rules, rep = self.rules(S(("You started with a {s} win chance, and luck came to {l}.",
                                       {"s": "#1.start_wp_pct", "l": "#1.luck_pts"})))
        self.assertTrue(ok, rep)

    def test_hedged_low_probability(self):
        m = self.luck[0]["id"]
        ok, rules, rep = self.rules(S(("The critical hit on turn {t} had only a {p} chance.",
                                       {"t": f"{m}.turn", "p": f"{m}.realised_prob"})))
        self.assertTrue(ok, rep)

    def test_cited_move_name_in_free_text(self):
        m = self.luck[0]["id"]  # turn 2: your_action Swords Dance
        ok, rules, rep = self.rules(S(("Your Swords Dance turn saw luck swing {x} against you.",
                                       {"x": f"{m}.luck_pts"})))
        self.assertTrue(ok, rep)

    def test_digits_inside_cited_name_allowed(self):
        t = self.s.call("get_turn", {"turn": 2})  # opp_policy includes "switch to Porygon2"
        ok, rules, rep = self.rules(S(("The engine gave a switch to Porygon2 a {p} chance on turn {t}.",
                                       {"p": f"{t['opp_policy'][1]['id']}.prob", "t": f"{t['id']}.turn"})))
        self.assertTrue(ok, rep)

    def test_percent_unit_after_placeholder(self):
        ok, rules, rep = self.rules(S(("You started with a win chance of {s} percent.", {"s": "#1.start_wp_pct"})))
        self.assertTrue(ok, rep)

    def test_choices_swung_against_you(self):
        # smoke run 4 regression: "swung" is a neutral verb; "choices" names the slot
        ok, rules, rep = self.rules(S(("Luck swung {l} against you while choices, yours and your opponent's, "
                                       "swung {c} against you.", {"l": "#1.luck_pts", "c": "#1.choices_pts"})))
        self.assertTrue(ok, rep)

    def test_trigger_binding_preceding_same_clause(self):
        # v3 pilot item 1 regression: "choices" after {l} belongs to {c}, not to {l}
        ok, rules, rep = self.rules(S(("Luck was net {l} and choices, yours and your opponent's, came to {c}.",
                                       {"l": "#1.luck_pts", "c": "#1.choices_pts"})))
        self.assertTrue(ok, rep)

    def test_trigger_binding_adjacent_following(self):
        m = self.luck[0]["id"]
        ok, rules, rep = self.rules(S(("The crit missed nothing, a {p} chance event that gave {x} luck points "
                                       "against you.", {"p": f"{m}.realised_prob", "x": f"{m}.luck_pts"})))
        self.assertTrue(ok, rep)

    def test_event_word_labels_luck(self):
        m = self.luck[0]["id"]
        ok, rules, rep = self.rules(S(("On turn {t}, the critical hit swung {x} against you.",
                                       {"t": f"{m}.turn", "x": f"{m}.luck_pts"})))
        self.assertTrue(ok, rep)

    def test_verdict_slot_from_get_verdict(self):
        v = self.s.call("get_verdict", {"turn": 2, "k": 0})
        ok, rules, rep = self.rules(S(("The verdict on turn {t} is {v}.", {"t": f"{v['id']}.turn",
                                                                          "v": f"{v['id']}.verdict"})))
        self.assertTrue(ok, rep)


    def test_verdict_margin_sentence(self):
        v = self.s.call("get_verdict", {"turn": 2, "k": 0})
        self.assertEqual((v["engine_best"], v["read_run_mean_pts"], v["reference"]),
                         ("Knock Off", 4, "the engine's estimate"))
        ok, rules, rep = self.rules(S(("The verdict is {v}: under your read, {x} was better by {m}.",
                                       {"v": f"{v['id']}.verdict", "x": f"{v['id']}.engine_best",
                                        "m": f"{v['id']}.read_run_mean_pts"})))
        self.assertTrue(ok, rep)

    def test_offered_list_from_the_app(self):
        s = ToolSession(REVIEW, offered=[REVIEW["decisions"][3]])  # the UI shows only turn 5
        self.assertEqual([m["turn"] for m in s.call("list_key_moments", {"kind": "disagreement"})["moments"]], [5])
        s2 = ToolSession(REVIEW, offered=[])
        self.assertIn("note", s2.call("list_key_moments", {"kind": "disagreement"}))


class TestVerifierFail(Base):
    def test_r0_empty(self):
        self.assertEqual(self.rules({"sentences": []})[1], [0])
        self.assertEqual(self.rules({"text": "hi"})[1], [0])

    def test_r0_too_many_sentences(self):
        one = ("Luck came to {l}.", {"l": "#1.luck_pts"})
        self.assertIn(0, self.rules(S(*([one] * 5)))[1])

    def test_r1_unknown_id(self):
        self.assertIn(1, self.rules(S(("Luck came to {l}.", {"l": "#99.luck_pts"})))[1])

    def test_r1_missing_slot(self):
        self.assertIn(1, self.rules(S(("Luck came to {l}.", {})))[1])

    def test_r1_bad_field(self):
        m = self.luck[0]["id"]
        self.assertIn(1, self.rules(S(("Luck came to {l}.", {"l": f"{m}.luck_points"})))[1])
        self.assertIn(1, self.rules(S(("Luck came to {l}.", {"l": f"{m}.events"})))[1])  # not a scalar

    def test_r2_luck_phrase_bound_to_gap(self):
        d = self.dis[0]["id"]
        self.assertIn(2, self.rules(S(("Luck swung {x} against you.", {"x": f"{d}.gap_pts"})))[1])

    def test_r2_win_chance_bound_to_event_probability(self):
        m = self.luck[0]["id"]
        self.assertIn(2, self.rules(S(("Your win chance was {w}.", {"w": f"{m}.realised_prob"})))[1])

    def test_r2_chance_bound_to_win_chance(self):
        m = self.luck[0]["id"]
        self.assertIn(2, self.rules(S(("The critical hit had only a {p} chance.", {"p": f"{m}.wp_before_pct"})))[1])

    def test_r2_turn_bound_to_points(self):
        m = self.luck[0]["id"]
        self.assertIn(2, self.rules(S(("On turn {t}, luck swung against you.", {"t": f"{m}.luck_pts"})))[1])

    def test_r2_unlabelled_number(self):
        m = self.luck[0]["id"]
        self.assertIn(2, self.rules(S(("It came out at {x} overall.", {"x": f"{m}.wp_after_pct"})))[1])

    def test_r2_choices_called_yours(self):
        self.assertIn(2, self.rules(S(("Your choices came to {c} in total.", {"c": "#1.choices_pts"})))[1])

    def test_r3_digit(self):
        self.assertIn(3, self.rules(S(("On turn 2, luck came to {l}.", {"l": "#1.luck_pts"})))[1])

    def test_r3_number_words(self):
        self.assertIn(3, self.rules(S(("Luck came to {l}, about twenty swings.", {"l": "#1.luck_pts"})))[1])
        self.assertIn(3, self.rules(S(("Luck came to {l} in just one turn.", {"l": "#1.luck_pts"})))[1])

    def test_r3_and_r5_uncited_digit_name(self):
        m = self.luck[0]["id"]  # the luck moment does not mention Porygon2
        ok, rules, rep = self.rules(S(("Porygon2 swung luck by {x} against you.", {"x": f"{m}.luck_pts"})))
        self.assertIn(3, rules)
        self.assertIn(5, rules)

    def test_r4_wrong_polarity(self):
        m = self.luck[0]["id"]  # luck_pts = -15
        self.assertIn(4, self.rules(S(("On turn {t}, luck swung {x} toward you.",
                                       {"t": f"{m}.turn", "x": f"{m}.luck_pts"})))[1])

    def test_r4_direction_without_signed_value(self):
        m = self.luck[0]["id"]
        self.assertIn(4, self.rules(S(("On turn {t}, luck went against you.", {"t": f"{m}.turn"})))[1])

    def test_r4_gap_is_never_help(self):
        d = self.dis[0]["id"]
        self.assertIn(4, self.rules(S(("The engine preferred {best}; that gap of {g} helped you.",
                                       {"best": f"{d}.engine_best", "g": f"{d}.gap_pts"})))[1])

    def test_r5_uncited_species(self):
        m = self.luck[0]["id"]  # Kingambit is only in the team preview decision, not in any cited output
        self.assertIn(5, self.rules(S(("Kingambit took luck worth {x} against you.", {"x": f"{m}.luck_pts"})))[1])

    def test_r6_verdict_word(self):
        d = self.dis[0]["id"]
        self.assertIn(6, self.rules(S(("On turn {t}, your move was a mistake.", {"t": f"{d}.turn"})))[1])

    def test_r6_negated_verdict(self):
        d = self.dis[0]["id"]
        self.assertIn(6, self.rules(S(("On turn {t}, it was not a misplay.", {"t": f"{d}.turn"})))[1])

    def test_r6_verdict_word_next_to_slot(self):
        v = self.s.call("get_verdict", {"turn": 2, "k": 0})
        self.assertIn(6, self.rules(S(("The verdict {v}: not a misread.", {"v": f"{v['id']}.verdict"})))[1])

    def test_r2_choices_must_say_both_players(self):
        self.assertIn(2, self.rules(S(("Luck swung {l} against you while choices swung {c} against you.",
                                       {"l": "#1.luck_pts", "c": "#1.choices_pts"})))[1])

    def test_r8_choices_cost(self):
        # Ag-App regression (showcase 4): "choices cost 9 points" must be rejected (research/28 §B)
        ok, rules, rep = self.rules(S(("Choices, yours and your opponent's, cost {c}.", {"c": "#1.choices_pts"})))
        self.assertIn(8, rules)
        d = self.dis[0]["id"]
        self.assertIn(8, self.rules(S(("On turn {t}, your choice cost {g} by the engine's estimate.",
                                       {"t": f"{d}.turn", "g": f"{d}.gap_pts"})))[1])

    def test_r8_summed_and_coach_and_claims(self):
        d = self.dis[0]["id"]
        for text in ["In total, the engine preferred {best} by {g}.",
                     "Altogether the engine preferred {best} by {g}.",
                     "As your coach: the engine preferred {best} by {g}.",
                     "The deterministic engine preferred {best} by {g}."]:
            ok, rules, rep = self.rules(S((text, {"best": f"{d}.engine_best", "g": f"{d}.gap_pts"})))
            self.assertIn(8, rules, text)

    def test_r7_certainty_with_low_probability(self):
        m = self.luck[0]["id"]
        self.assertIn(7, self.rules(S(("The critical hit was certain, despite its {p} chance.",
                                       {"p": f"{m}.realised_prob"})))[1])

    def test_r7_low_probability_stated_as_fact(self):
        t = self.s.call("get_turn", {"turn": 2})
        pol = t["opp_policy"][0]["id"]  # Tri Attack, prob 0.41
        ok, rules, rep = self.rules(S(("Your opponent was always going to click {a}, the engine said {p} chance.",
                                       {"a": f"{pol}.action", "p": f"{pol}.prob"})))
        self.assertIn(7, rules)


def main():
    out = None
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        sys.argv = [sys.argv[0]]
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({
            "when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tests": res.testsRun,
            "failures": len(res.failures), "errors": len(res.errors), "pass": res.wasSuccessful(),
            "failed": [str(t) for t, _ in res.failures + res.errors]}, indent=1) + "\n")
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
