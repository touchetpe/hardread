"""Focused presentation checks for generic switch expectations."""
import copy
import json
import unittest

import pipeline as P
from test_integration import REVIEW, CTX, ReadContextTests
import parse_note as PN


class GenericSwitchCopyTests(ReadContextTests):
    def check_case(self, policy, expected_text):
        review = copy.deepcopy(REVIEW)
        review['decisions'][0]['opponent_policy_top'] = policy
        original = copy.deepcopy(review)
        belief = {'sets': {}, 'expects_action': {'kind': 'switch'}}
        raw = self.verdict(belief)
        raw_before = copy.deepcopy(raw)
        parser_result = PN.read_check(review, 2, 0, belief)
        card = P.verdict_card(raw, CTX, review)
        result = card['read_check']
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['engine_top'], parser_result['engine_top'])
        self.assertEqual(result['matched'], parser_result['matched'])
        self.assertIsNone(result['engine_prob_of_expected'])
        self.assertIsNone(result['upper_bound_if_not_listed'])
        self.assertIn(expected_text, result['sentence'])
        self.assertIn('total probability of any switch is unavailable', result['sentence'])
        self.assertNotIn('%', result['sentence'])
        rendered = json.dumps(self.render(card))
        self.assertIn(expected_text, rendered)
        self.assertIn('total probability of any switch is unavailable', rendered)
        self.assertEqual(raw, raw_before)
        self.assertEqual(review, original)
        self.assertEqual(card['verdict'], 'Misplay')
        self.assertEqual(card['delta_U'], [8.0, 9.0, 7.0])

    def test_one_listed_switch_is_not_a_total(self):
        self.check_case([['shadowball', .6], ['switch ogerponwellspring', .1348], ['recover', .0528]],
                        "A switch was listed among the engine's top three actions.")

    def test_no_listed_switch_is_not_an_aggregate_bound(self):
        self.check_case([['shadowball', .6], ['recover', .1348], ['swordsdance', .0528]],
                        "No switch was listed among the engine's top three actions.")

    def test_multiple_listed_switches_are_not_summed(self):
        self.check_case([['switch pikachu', .2], ['switch greattusk', .15], ['recover', .1]],
                        "A switch was listed among the engine's top three actions.")

    def test_named_switch_and_move_keep_existing_result(self):
        for belief in ({'expects_action': {'kind': 'switch', 'species': 'Pikachu'}},
                       {'expects_action': {'kind': 'move', 'move': 'shadowball'}}):
            with self.subTest(belief=belief):
                self.assertEqual(P.read_check(REVIEW, 2, 0, belief), PN.read_check(REVIEW, 2, 0, belief))

    def test_no_choice_and_unavailable_data_unchanged(self):
        review = copy.deepcopy(REVIEW)
        review['decisions'][0]['opponent_policy_top'] = [['No Move', 1.0]]
        belief = {'expects_action': {'kind': 'switch'}}
        self.assertEqual(P.read_check(review, 2, 0, belief), PN.read_check(review, 2, 0, belief))
        self.assertEqual(P.read_check(review, 2, 0, belief)['status'], 'no_opponent_choice')
        review['decisions'][0]['opponent_policy_top'] = []
        self.assertIsNone(P.read_check(review, 2, 0, belief))


if __name__ == '__main__':
    unittest.main()
