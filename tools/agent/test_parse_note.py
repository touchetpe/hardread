#!/usr/bin/env python3
"""Unit tests for the note parser: grounding, normalisation, chips, belief schema, read check (no model needed).

  python3 tools/agent/test_parse_note.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_note as P  # noqa: E402

OWN = ["Gholdengo", "Great Tusk", "Kingambit", "Dragonite", "Ogerpon-Wellspring", "Zamazenta"]
OPP = ["Dragapult", "Clefable", "Porygon2", "Rillaboom", "Manaphy", "Iron Valiant"]
REVIEW = {"turns": [{"turn": 7, "opponent_action": "switch clefable"}],
          "decisions": [{"turn": 7, "k": 0, "opponent_policy_top": [["dracometeor", 0.41], ["uturn", 0.24],
                                                                    ["switch clefable", 0.22]]}]}


def chips(raw, note):
    return P.to_chips(raw, note, OWN, OPP, "glimmer")


class TestGrounding(unittest.TestCase):
    def test_invented_item_dropped(self):
        c, d = chips({"sets": [{"species": "Dragapult", "item": "Choice Scarf"}]}, "Dragapult was Specs")
        self.assertFalse([x for x in c if x["field"] == "item"])
        self.assertEqual(d[0]["field"], "item")

    def test_alias_item_kept(self):
        c, _ = chips({"sets": [{"species": "Dragapult", "item": "Choice Specs"}]}, "dragapult was specs")
        it = [x for x in c if x["field"] == "item"]
        self.assertEqual((it[0]["id"], it[0]["evidence"]), ("choicespecs", "specs"))

    def test_species_outside_game_or_own(self):
        _, d = chips({"sets": [{"species": "Garchomp", "item": "Rocky Helmet"},
                               {"species": "Kingambit", "item": "Leftovers"}]}, "Garchomp helmet, Kingambit lefties")
        self.assertEqual([x["reason"].split()[0] for x in d], ["not", "your"])

    def test_species_not_named_in_note(self):
        _, d = chips({"sets": [{"species": "Clefable", "moves_has": ["Moonblast"]}]}, "I expected Moonblast")
        self.assertEqual(d[0]["reason"], "the note does not name it")

    def test_moves_not_and_short_species_name(self):
        c, _ = chips({"sets": [{"species": "Iron Valiant", "moves_not": ["Knock Off"]}]},
                     "Valiant had no Knock Off")
        self.assertEqual([(x["field"], x["id"]) for x in c], [("moves_not", "knockoff")])

    def test_no_numbers(self):
        note = "Porygon2 was 252 HP Download"
        c, d = chips({"sets": [{"species": "Porygon2", "ability": "252 HP", "moves_has": ["Tri Attack 80"]}]}, note)
        self.assertTrue(all(x["field"] == "spread" for x in c))
        self.assertEqual(len(d), 2)
        self.assertFalse(P.has_invented_number(P.belief_from_chips(c), note))

    def test_spread_not_used_chip(self):
        c, _ = chips({"sets": [], "confidence": "sure"}, "max speed Jolly Dragapult")
        self.assertIn(("spread", "not used"), [(x["field"], x["value"]) for x in c])

    def test_expected_action_needs_the_note(self):
        c, d = chips({"sets": [], "expects_action": {"kind": "switch", "species": "Clefable"}},
                     "Dragapult was Specs")
        self.assertFalse([x for x in c if x["field"] == "expects_action"])
        c, _ = chips({"sets": [], "expects_action": {"kind": "switch", "species": "Clefable"}},
                     "I thought they would switch to Clefable")
        self.assertEqual([x["id"] for x in c if x["field"] == "expects_action"],
                         [{"kind": "switch", "move": None, "species": "Clefable"}])

    def test_belief_after_edit(self):
        c, _ = chips({"sets": [{"species": "Dragapult", "item": "Choice Specs", "moves_has": ["Draco Meteor"]}],
                      "confidence": "sure"}, "Dragapult specs with Draco Meteor, sure")
        b = P.belief_from_chips([x for x in c if x["field"] != "item"])  # the user deletes the item chip
        self.assertEqual(b, {"sets": {"Dragapult": {"moves_has": ["dracometeor"]}}, "confidence": "sure"})


class TestAttribution(unittest.TestCase):
    """Ag-App regression (2026-10-27): the player's own move was credited to the opponent."""
    OPP2 = ["Ceruledge", "Dragapult", "Clefable", "Rillaboom", "Manaphy", "Iron Valiant"]
    OWN2 = ["Kingambit", "Great Tusk", "Gholdengo", "Dragonite", "Ogerpon-Wellspring", "Zamazenta"]

    def test_keyword_own_move_after_so(self):
        r = P.keyword_parse("I thought Ceruledge had no Fire move so Crunch was safe", self.OWN2, self.OPP2)
        self.assertFalse(any("crunch" in (x.get("moves_has", []) + x.get("moves_not", [])) for x in r["sets"]))

    def test_glimmer_own_move_dropped(self):
        note = "I thought Ceruledge had no Fire move so Crunch was safe"
        c, d = P.to_chips({"sets": [{"species": "Ceruledge", "moves_has": ["Crunch"]}]}, note, self.OWN2, self.OPP2,
                          "glimmer")
        self.assertFalse([x for x in c if x["field"] == "moves_has"])
        self.assertTrue(d and d[0]["reason"].startswith("not attributed to Ceruledge"))

    def test_i_used_and_my(self):
        for note in ["I used Knock Off because Dragapult was Choice Specs", "my Knock Off was fine, Dragapult was Specs"]:
            c, d = P.to_chips({"sets": [{"species": "Dragapult", "moves_has": ["Knock Off"], "item": "Choice Specs"}]},
                              note, self.OWN2, self.OPP2, "glimmer")
            self.assertEqual([x["field"] for x in c], ["item"], note)

    def test_move_on_players_side_needs_the_name(self):
        note = "Dragapult was Specs, and Knock Off was the play"
        c, _ = P.to_chips({"sets": [{"species": "Dragapult", "moves_has": ["Knock Off"]}]}, note, self.OWN2,
                          self.OPP2, "glimmer", own_moves={"knockoff"})
        self.assertFalse([x for x in c if x["field"] == "moves_has"])

    def test_pronoun_attribution_kept(self):
        note = "maybe Clefable was Leftovers, I didn't think it had Moonblast"
        c, _ = P.to_chips({"sets": [{"species": "Clefable", "item": "Leftovers", "moves_not": ["Moonblast"]}]},
                          note, self.OWN2, self.OPP2, "glimmer")
        self.assertEqual(sorted(x["field"] for x in c), ["item", "moves_not"])

    def test_pronoun_refers_to_other_species(self):
        note = "Dragapult was Specs and Clefable switched in, it had Moonblast"
        c, d = P.to_chips({"sets": [{"species": "Dragapult", "moves_has": ["Moonblast"]}]}, note, self.OWN2,
                          self.OPP2, "glimmer")
        self.assertFalse([x for x in c if x["field"] == "moves_has"])


class TestKeywordAndReadCheck(unittest.TestCase):
    def test_keyword_negation(self):
        r = P.keyword_parse("Dragapult was Choice Specs for sure, no way it had Will-O-Wisp", OWN, OPP)
        self.assertEqual(r["sets"], [{"species": "Dragapult", "item": "choicespecs", "moves_not": ["willowisp"]}])
        self.assertEqual(r["confidence"], "sure")

    def test_keyword_ignores_own_move(self):
        r = P.keyword_parse("I used Make It Rain because Clefable had Moonblast", OWN, OPP)
        self.assertEqual(r["sets"], [{"species": "Clefable", "moves_has": ["moonblast"]}])

    def test_read_check_copies_policy_numbers(self):
        rc = P.read_check(REVIEW, 7, 0, {"expects_action": {"kind": "switch", "species": "Clefable"}})
        self.assertEqual(rc["engine_prob_of_expected"], 0.22)
        self.assertTrue(rc["matched"])
        self.assertIn("The engine gave it 22%", rc["sentence"])
        self.assertIn("Draco Meteor at 41%", rc["sentence"])

    def test_read_check_unlisted(self):
        rc = P.read_check(REVIEW, 7, 0, {"expects_action": {"kind": "move", "move": "willowisp"}})
        self.assertIsNone(rc["engine_prob_of_expected"])
        self.assertEqual(rc["upper_bound_if_not_listed"], 0.22)
        self.assertFalse(rc["matched"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
