import unittest
from editorial_plan_review import independent_plan_review, EditorialPlanError
from comparison_acceptance import digest

class CriterionGateTests(unittest.TestCase):
    def setUp(self):
        self.plan={'requirements':[{'id':'C01','instruction':'CEO intro','source':'CEO'},
                                   {'id':'C02','instruction':'CEO recommendation','source':'CEO'}]}
        self.payload={'lead':'朝の利用場面に合わせます','summary':'Aをおすすめします'}

    def receipt(self, verdicts):
        items=[{'id':item['id'],'verdict':v,'article_quote':q,'reason':'judgement'}
               for item,v,q in zip(self.plan['requirements'],verdicts,
                                   [self.payload['lead'],self.payload['summary']])]
        return {'schema':'sellemy-editorial-plan-review/v1','plan_hash':digest(self.plan),
                'payload_hash':digest(self.payload),'items':items}

    def test_all_criteria_pass(self):
        got=independent_plan_review(self.plan,self.payload,judge=lambda _:self.receipt(['PASS','PASS']))
        self.assertEqual(len(got['items']),2)

    def test_one_unresolved_item_fails(self):
        with self.assertRaises(EditorialPlanError):
            independent_plan_review(self.plan,self.payload,judge=lambda _:self.receipt(['PASS','FAIL']))

    def test_stale_or_unrelated_quote_fails(self):
        r=self.receipt(['PASS','PASS']);r['items'][1]['article_quote']='invented'
        with self.assertRaises(EditorialPlanError):
            independent_plan_review(self.plan,self.payload,judge=lambda _:r)

    def test_generic_editorial_requirements_never_run_duplicate_ai_review(self):
        plan = {'requirements': [{'id':'E01','instruction':'natural prose'},
                                 {'id':'E06','instruction':'conclusion'},
                                 {'id':'E08','instruction':'claims'}]}
        def refuse(_):
            raise AssertionError('generic quality checks belong to Editorial/Claim QA')
        result = independent_plan_review(plan,self.payload,judge=refuse)
        self.assertEqual(result['items'],[])
        self.assertEqual(result['plan_hash'],digest(plan))

    def test_specific_ceo_instruction_is_still_enforced(self):
        plan={'requirements':[{'id':'E06','instruction':'generic conclusion'},
                              {'id':'C01','instruction':'CEO demands a specific point','source':'CEO'}]}
        receipt={'schema':'sellemy-editorial-plan-review/v1',
                 'plan_hash':digest(plan),'payload_hash':digest(self.payload),
                 'items':[{'id':'C01','verdict':'FAIL','article_quote':self.payload['lead'],
                           'reason':'CEO instruction absent'}]}
        with self.assertRaises(EditorialPlanError):
            independent_plan_review(plan,self.payload,judge=lambda _:receipt)
