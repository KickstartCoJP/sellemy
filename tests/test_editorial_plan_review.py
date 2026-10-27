import unittest
from editorial_plan_review import independent_plan_review, EditorialPlanError
from comparison_acceptance import digest

class CriterionGateTests(unittest.TestCase):
    def setUp(self):
        self.plan={'requirements':[{'id':'E01','instruction':'intro'},
                                   {'id':'E02','instruction':'recommendation'}]}
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
