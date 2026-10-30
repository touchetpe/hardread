import unittest
from evidence_select import validate_selection

class Session:
    def resolve(self, ref):
        if ref not in {'#1.score', '#2.turn'}: raise KeyError(ref)
        return ({}, ref, 1)

class SelectTest(unittest.TestCase):
    def test_only_offered_scalar_refs(self):
        self.assertEqual(validate_selection(Session(), ['#1.score', '#2.turn'], {'refs': ['#2.turn']}), ['#2.turn'])
        for offered, response in [(['#1.score'], {'refs':['#2.turn']}),
                                  (['#1.score'], {'refs':['#1.score', '#1.score']}),
                                  (['#1.score'], {'refs':[{}]}),
                                  (['#1.score'], {'refs':['#1.score'], 'text':'invented'}),
                                  (['#3.fake'], {'refs':['#3.fake']})]:
            with self.assertRaises(ValueError): validate_selection(Session(), offered, response)

if __name__ == '__main__': unittest.main()

class ViewTest(unittest.TestCase):
    def test_numeric_server_records_only(self):
        from evidence_select import selection_view
        class S:
            def resolve(self, ref):
                values={'#1.turn':('turn',3),'#1.name':('name','secret'),'#1.gap_pts':('gap_pts',float('nan'))}
                field,value=values[ref]
                return {},field,value
        self.assertEqual(selection_view(S(),['#1.turn']),[{'ref':'#1.turn','value':3}])
        for refs in (['#1.name'],['#1.gap_pts'],['#1.turn','#1.turn'],[{}],['#2.turn']):
            with self.assertRaises(ValueError): selection_view(S(),refs)
