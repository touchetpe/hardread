"""Synthetic-only tests; no application state or ladder data is read."""
import copy
from decimal import localcontext
import unittest

import own_ladder_review as review

REF = 'a' * 64

def cite(value, field):
    return {'value': value, 'ref': REF, 'field': field}

def decision(turn, index=0, chosen='Move A', a='0.50', b='0.20'):
    return {'turn': cite(turn, 'turn'), 'index': cite(index, 'index'),
            'own_active': cite('Friendly', 'own_active'), 'chosen': cite(chosen, 'chosen'),
            'score_order': cite('higher', 'score_order'), 'score_unit': None, 'candidates': [
                {'action': cite('Move A', 'candidates[0].action'), 'legal': cite(True, 'candidates[0].legal'),
                 'score': cite(a, 'candidates[0].score'), 'scale': cite('root', 'scale')},
                {'action': cite('Move B', 'candidates[1].action'), 'legal': cite(True, 'candidates[1].legal'),
                 'score': cite(b, 'candidates[1].score'), 'scale': cite('root', 'scale')}]}

def package(decisions):
    return {'schema': review.SCHEMA, 'games': [{'alias': 'G1', 'decisions': decisions},
            {'alias': 'G2', 'decisions': None}, {'alias': 'G3', 'decisions': None}]}

class ProjectionTests(unittest.TestCase):
    def test_ranking_distinct_turns_and_citations(self):
        data = package([decision(5, 0, a='0.4', b='0.3'), decision(2, 0, a='0.9', b='0.1'),
                        decision(2, 1, a='0.8', b='0.1'), decision(3, 0, a='0.7', b='0.1'),
                        decision(4, 0, a='0.6', b='0.1')])
        out = review.build_projection(data)
        selected = out['games'][0]['decisions']
        self.assertEqual([x['turn']['value'] for x in selected], [2, 3, 4])
        self.assertEqual(selected[0]['index']['value'], 0)
        self.assertEqual(selected[0]['chosen_score'], data['games'][0]['decisions'][1]['candidates'][0]['score'])
        self.assertEqual(out['games'][1]['decisions'], [])
        self.assertEqual(out['mode'], 'template-only')

    def test_ranking_is_exact_under_low_ambient_decimal_precision(self):
        # Differences appear beyond the default 28 significant digits; the
        # smallest gap belongs to the earliest turn and must be excluded.
        scores = ['0.100000000000000000000000000000000001',
                  '0.100000000000000000000000000000000002',
                  '0.100000000000000000000000000000000003',
                  '0.100000000000000000000000000000000004']
        with localcontext() as ctx:
            ctx.prec = 6
            selected = review.build_projection(package([
                decision(i + 1, a='0', b=score) for i, score in enumerate(scores)
            ]))['games'][0]['decisions']
        self.assertEqual([item['turn']['value'] for item in selected], [2, 3, 4])
        self.assertEqual([item['other_score']['value'] for item in selected], scores[1:])

    def test_equal_score_tie_preserves_logged_order(self):
        d = decision(1, a='0.50', b='0.50')
        c = copy.deepcopy(d['candidates'][1]); c['action'] = cite('Move C', 'candidates[2].action')
        d['candidates'].append(c)
        selected = review.build_projection(package([d]))['games'][0]['decisions'][0]
        self.assertEqual(selected['other']['value'], 'Move B')
        self.assertEqual(selected['chosen_score']['value'], '0.50')
        self.assertNotIn('win_probability', selected)

    def test_missing_chosen_legality_or_scores_are_unavailable(self):
        for change in ('illegal', 'missing', 'different_scale', 'unknown_chosen'):
            d = decision(1)
            if change == 'illegal': d['candidates'][0]['legal']['value'] = False
            if change == 'missing': d['candidates'][0]['score'] = None
            if change == 'different_scale': d['candidates'][1]['scale'] = cite('other', 'scale')
            if change == 'unknown_chosen': d['chosen'] = None
            self.assertEqual(review.build_projection(package([d]))['games'][0]['decisions'], [])

    def test_strict_projection_rejects_future_identity_and_missing_citations(self):
        for key in ('replay_id', 'opponent_name', 'url', 'raw_log', 'later_reveal', 'result'):
            data = package([decision(1)]); data['games'][0]['decisions'][0][key] = 'private'
            with self.assertRaises(ValueError): review.build_projection(data)
        data = package([decision(1)]); del data['games'][0]['decisions'][0]['turn']['field']
        with self.assertRaises(ValueError): review.build_projection(data)
        data = package([decision(1)]); data['games'][0]['decisions'][0]['chosen']['value'] = 'https://example.com'
        with self.assertRaises(ValueError): review.build_projection(data)
        data = package([decision(1)]); data['games'][0]['decisions'][0]['candidates'][0]['score_order'] = 'higher'
        with self.assertRaises(ValueError): review.build_projection(data)

    def test_duplicate_key_and_invalid_scores_rejected(self):
        with self.assertRaises(ValueError): review.build_projection(package([decision(1), decision(1)]))
        d = decision(1); d['candidates'][0]['score']['value'] = 'NaN'
        with self.assertRaises(ValueError): review.build_projection(package([d]))

def fact_record(turn, ordinal, key=None):
    return {'source_key': key or format(ordinal, '064x'), 'ordinal': ordinal,
            'turn': cite(turn, 'turn'), 'chosen': cite('Move A', 'chosen'),
            'duration': None, 'visits': []}

def fact_package(records):
    return {'schema': review.FACTS_SCHEMA, 'games': [{'alias': 'G1', 'records': records},
            {'alias': 'G2', 'records': None}, {'alias': 'G3', 'records': None}]}

class RecordedFactsTests(unittest.TestCase):
    def test_short_and_even_odd_selection(self):
        expected = {0: [], 1: [1], 2: [1, 2], 3: [1, 2, 3],
                    4: [1, 2, 4], 5: [1, 3, 5], 6: [1, 3, 6]}
        for count, turns in expected.items():
            records = [fact_record(i, i) for i in range(1, count + 1)]
            out = review.build_projection(fact_package(list(reversed(records))))
            self.assertEqual([d['turn']['value'] for d in out['games'][0]['decisions']], turns)
            self.assertEqual(out['presentation'], 'recorded-facts')
            self.assertEqual(out['games'][1]['decisions'], [])

    def test_duplicate_turns_earliest_ordinal_and_conflicts_excluded(self):
        first = fact_record(1, 1); later = fact_record(1, 2); later['chosen']['value'] = 'Move B'
        duplicate = fact_record(2, 3); duplicate_copy = fact_record(2, 4, duplicate['source_key'])
        conflict = fact_record(3, 5); conflicting = fact_record(4, 6, conflict['source_key'])
        records = [conflicting, later, duplicate_copy, conflict, first, duplicate, fact_record(5, 7)]
        game = review.build_projection(fact_package(records))['games'][0]
        self.assertEqual([d['turn']['value'] for d in game['decisions']], [1, 2, 5])
        self.assertEqual(game['decisions'][0]['chosen']['value'], 'Move A')
        self.assertTrue(game['conflicts_unavailable'])
        self.assertNotIn('source_key', game['decisions'][0])
        self.assertNotIn('ordinal', game['decisions'][0])

    def test_duplicate_optional_facts_are_omitted_when_conflicting(self):
        original = fact_record(1, 1)
        original['duration'] = {'value': cite('0.25', 'search.duration'), 'unit': cite('seconds', 'search.unit')}
        original['visits'] = [{'value': cite('12', 'visits.count'), 'scope': cite('world 1', 'visits.scope')}]
        same = copy.deepcopy(original); same['ordinal'] = 2
        out = review.build_projection(fact_package([same, original]))['games'][0]['decisions'][0]
        self.assertEqual(out['duration'], original['duration'])
        self.assertEqual(out['visits'], original['visits'])
        different = copy.deepcopy(same); different['duration']['value']['value'] = '0.26'
        out = review.build_projection(fact_package([original, different]))['games'][0]['decisions'][0]
        self.assertIsNone(out['duration'])
        self.assertEqual(out['visits'], original['visits'])
        different = copy.deepcopy(same); different['visits'][0]['value']['value'] = '13'
        out = review.build_projection(fact_package([original, different]))['games'][0]['decisions'][0]
        self.assertEqual(out['duration'], original['duration'])
        self.assertEqual(out['visits'], [])
        missing = copy.deepcopy(same); missing['duration'] = None; missing['visits'] = []
        out = review.build_projection(fact_package([original, missing]))['games'][0]['decisions'][0]
        self.assertIsNone(out['duration'])
        self.assertEqual(out['visits'], [])
        self.assertEqual(out['turn']['value'], 1)

    def test_missing_choice_and_optional_cited_facts(self):
        missing = fact_record(1, 1); missing['chosen'] = None
        record = fact_record(2, 2)
        record['duration'] = {'value': cite('0.250', 'search.duration'), 'unit': cite('seconds', 'search.unit')}
        record['visits'] = [{'value': cite('012', 'visits.count'), 'scope': cite('world 1', 'visits.scope')}]
        with self.assertRaises(ValueError): review.build_projection(fact_package([missing, record]))
        record['visits'][0]['value']['value'] = '12'
        out = review.build_projection(fact_package([missing, record]))['games'][0]['decisions']
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['duration']['value']['value'], '0.250')
        self.assertEqual(out[0]['visits'][0]['scope']['field'], 'visits.scope')

    def test_private_future_and_invented_comparison_fields_rejected(self):
        for key in ('opponent_name', 'replay_id', 'url', 'raw_log', 'later_reveal', 'legal', 'score'):
            d = fact_record(1, 1); d[key] = 'private'
            with self.assertRaises(ValueError): review.build_projection(fact_package([d]))
        d = fact_record(1, 1); d['duration'] = {'value': cite('2', 'search.duration'), 'unit': None}
        with self.assertRaises(ValueError): review.build_projection(fact_package([d]))
        d = fact_record(1, 1); d['chosen']['field'] = 'future.reveal'
        with self.assertRaises(ValueError): review.build_projection(fact_package([d]))

if __name__ == '__main__':
    unittest.main()
