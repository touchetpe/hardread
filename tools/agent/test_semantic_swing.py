"""Focused semantic regressions; synthetic receipts reconstruct the three recorded values."""
import copy
import unittest

from test_verifier import REVIEW, S
from tools import ToolSession
from verifier import verify
from render import render


class SemanticSwingTests(unittest.TestCase):
    def session(self, luck=-0.15, swing=-0.23, probability=0.3594, turn=2):
        review = copy.deepcopy(REVIEW)
        review['turns'][1].update(turn=turn, luck=luck, chance_swing_vs_counterfactual=swing,
                                  luck_realised_prob=probability)
        if turn in (22, 26):
            review['turns'][1]['luck_events'] = {}
        if turn == 16:
            review['turns'][1]['win_prob'] = .74
            review['turns'].append({'turn': 17, 'win_prob': .32, 'luck': 0})
        session = ToolSession(review)
        event = session.call('get_luck_events', {'turn': turn})
        return session, event['id']

    def check(self, session, text, slots, expected, fields=None, shown=None):
        payload = S((text, slots))
        result = verify(payload, session)
        if expected:
            self.assertTrue(result['pass'], result)
            output = render(payload, session)
            citations = output['sentences'][0]['citations']
            if fields is not None:
                self.assertEqual([c['field'] for c in citations], fields)
            if shown is not None:
                self.assertEqual([c['shown'] for c in citations], shown)
        else:
            self.assertIn(2, [f['rule'] for f in result['failures']], result)
        return result

    def test_ten_prespecified_synthetic_cases(self):
        cases = [
            (-.15, -.23, 'The swing was {x} luck points.', 'swing_pts', False),
            (.08, .23, 'Luck helped you by {x}.', 'swing_pts', False),
            (0, 0, 'Luck was {x}.', 'swing_pts', False),
            (-.15, -.23, 'The counterfactual swing was {x}.', 'luck_pts', False),
            (-.15, -.23, 'Luck swung {x} against you.', 'luck_pts', True),
            (.08, .23, 'Luck swung {x} toward you.', 'luck_pts', True),
            (-.15, -.23, 'The counterfactual swing was {x}.', 'swing_pts', True),
        ]
        for luck, swing, sentence, field, expected in cases:
            with self.subTest(sentence=sentence, field=field):
                session, rid = self.session(luck, swing)
                self.check(session, sentence, {'x': f'{rid}.{field}'}, expected,
                           [field] if expected else None)
        session, rid = self.session()
        self.check(session, 'Luck was {l}; the counterfactual swing was {s}.',
                   {'l': f'{rid}.luck_pts', 's': f'{rid}.swing_pts'}, True,
                   ['luck_pts', 'swing_pts'], ['−15 points', '−23 points'])
        self.check(session, 'Luck was {l}; the counterfactual swing was {s}.',
                   {'l': f'{rid}.swing_pts', 's': f'{rid}.luck_pts'}, False)
        self.check(session, 'The event had a {p} chance; the counterfactual swing was {s}.',
                   {'p': f'{rid}.realised_prob', 's': f'{rid}.swing_pts'}, True,
                   ['realised_prob', 'swing_pts'], ['36%', '−23 points'])

    def test_bare_swing_does_not_identify_counterfactual_metric(self):
        session, rid = self.session()
        report = self.check(session, 'The swing was {x}.', {'x': f'{rid}.swing_pts'}, False)
        self.assertTrue(any('counterfactual swing' in f['message'] for f in report['failures']))

    def test_recorded_cases_and_corrected_luck_citations(self):
        cases = [
            (22, -.08, .6598, 'The engine estimates the swing mattered by {x} luck points.', '−8'),
            (16, -.15, .3438, 'It was a {l} points luck swing that moved your win chance from {w0} to {w1} with luck {x} points.', '−15'),
            (26, -.15, .3594, 'The swing amounted to {x} in luck points with a realised chance of {p}.', '−15'),
        ]
        for turn, luck, prob, text, expected in cases:
            with self.subTest(turn=turn):
                session, event = self.session(luck, -.23, prob, turn)
                slots = {'x': f'{event}.swing_pts'}
                if turn == 16:
                    # A real scoped record provides the chance slots, distinct from the event receipt.
                    moment = session.call('list_key_moments', {'kind': 'luck', 'k': 1})['moments'][0]['id']
                    slots.update(l=f'{moment}.luck_pts', w0=f'{moment}.wp_before_pct', w1=f'{moment}.wp_after_pct')
                if turn == 26:
                    slots['p'] = f'{event}.realised_prob'
                original = S((text, slots))
                original_citations = render(original, session)['sentences'][0]['citations']
                self.assertEqual(next(c for c in original_citations if c['slot'] == 'x')['shown'], '−23')
                if turn == 16:
                    self.assertEqual([c['shown'] for c in original_citations], ['−15', '74%', '32%', '−23'])
                if turn == 26:
                    self.assertEqual(next(c for c in original_citations if c['slot'] == 'p')['shown'], '36%')
                report = self.check(session, text, slots, False)
                self.assertTrue(any(f['rule'] == 2 and f['slot'] == 'x' for f in report['failures']), report)
                if turn == 16:
                    self.assertFalse(any(f['rule'] == 2 and f['slot'] == 'l' for f in report['failures']))
                payload = S(('Luck was {x}.', {'x': f'{event}.luck_pts'}))
                self.assertTrue(verify(payload, session)['pass'])
                citation = render(payload, session)['sentences'][0]['citations'][0]
                self.assertEqual(citation['field'], 'luck_pts')
                self.assertTrue(citation['shown'].startswith(expected), citation)


if __name__ == '__main__':
    unittest.main()
