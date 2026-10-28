import unittest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from editorial_plan import build_editorial_plan
from editorial_plan_review import independent_plan_review
from comparison_acceptance import digest
import growth_recovery

class EditorialPlanTests(unittest.TestCase):
    def test_structured_assignment_and_ceo_requirements(self):
        products = [{'ref':f'p{i}','amazon_title':f'item{i}','matched_comparison_axes':['a']} for i in range(1,7)]
        evidence = {'products':products,'comparison_axes':[{'id':'a','label':'A'},{'id':'b','label':'B'}]}
        plan = build_editorial_plan({'reader_persona':{'use_case':'home'}}, evidence, '指摘A。指摘B。')
        self.assertEqual(len(plan['products']),6)
        self.assertEqual([x['id'] for x in plan['requirements']][-2:],['C01','C02'])
        self.assertTrue(all(x['id'] and x['section'] for x in plan['requirements']))
        self.assertEqual(plan['reader']['use_case'],'home')
    def test_editorial_review_receives_verified_evidence(self):
        products = [{'ref':f'p{i}','amazon_title':f'item{i}'} for i in range(1,7)]
        evidence = {'products':products,'comparison_axes':[{'id':'a'},{'id':'b'}], 'sources':['verified source marker']}
        plan = build_editorial_plan({}, evidence)
        self.assertIn('E08', [r['id'] for r in plan['requirements']])
        payload = {'lead':'読者の使用場面を説明する'}
        def judge(prompt):
            data=json.loads(prompt.split('INPUT:',1)[1].strip())
            self.assertEqual(data['evidence']['sources'],['verified source marker'])
            return {'schema':'sellemy-editorial-plan-review/v1','plan_hash':digest(plan),
                    'payload_hash':digest(payload),
                    'items':[{'id':r['id'],'verdict':'PASS','article_quote':payload['lead'], 'reason':'ok'} for r in plan['requirements']]}
        self.assertEqual(len(independent_plan_review(plan,payload,judge,evidence=evidence)['items']),8)

    def test_product_hold_is_retried_hourly(self):
        now=datetime(2026,10,28,5,tzinfo=timezone.utc)
        hold={'status':'ACTIVE','current_stage':'PRODUCT_HOLD','slug':'hold',
              'updated_at':(now-timedelta(minutes=59)).isoformat(),'next_retry_at':None}
        with patch.object(growth_recovery,'all_jobs',return_value=[hold]):
            self.assertIsNone(growth_recovery.eligible_job(now=now))
            self.assertEqual(growth_recovery.eligible_job(now=now+timedelta(minutes=2))['slug'],'hold')

    def test_invalid_six_refs_fail_closed(self):
        with self.assertRaises(ValueError):
            build_editorial_plan({}, {'products':[{'ref':'p1'}]*6,'comparison_axes':[{'id':'a'},{'id':'b'}]})
