import unittest
from editorial_plan import build_editorial_plan

class EditorialPlanTests(unittest.TestCase):
    def test_structured_assignment_and_ceo_requirements(self):
        products = [{'ref':f'p{i}','amazon_title':f'item{i}','matched_comparison_axes':['a']} for i in range(1,7)]
        evidence = {'products':products,'comparison_axes':[{'id':'a','label':'A'},{'id':'b','label':'B'}]}
        plan = build_editorial_plan({'reader_persona':{'use_case':'home'}}, evidence, '指摘A。指摘B。')
        self.assertEqual(len(plan['products']),6)
        self.assertEqual([x['id'] for x in plan['requirements']][-2:],['C01','C02'])
        self.assertTrue(all(x['id'] and x['section'] for x in plan['requirements']))
        self.assertEqual(plan['reader']['use_case'],'home')
    def test_invalid_six_refs_fail_closed(self):
        with self.assertRaises(ValueError):
            build_editorial_plan({}, {'products':[{'ref':'p1'}]*6,'comparison_axes':[{'id':'a'},{'id':'b'}]})
