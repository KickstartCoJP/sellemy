"""Staging-only 37 failed-job Planning candidate review contract."""
import json
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
FOLDER=ROOT/'staging/failed37-planning'

class Failed37PlanningStagingTest(unittest.TestCase):
    def test_all_and_only_failed37_planning_candidates_staged(self):
        manifest=json.loads((FOLDER/'manifest.json').read_text())
        self.assertEqual(manifest['total'],37)
        self.assertEqual((manifest['writer_failed'],manifest['product_failed']),(34,3))
        self.assertEqual(len(manifest['entries']),37)
        self.assertEqual(len({x['slug'] for x in manifest['entries']}),37)
        self.assertFalse(manifest['original_jobs_changed'])
        self.assertFalse(manifest['publication_changed'])
        self.assertFalse(manifest['production_qa_gate_changed'])
        for entry in manifest['entries']:
            p=FOLDER/(entry['slug']+'.json')
            v=json.loads(p.read_text())
            self.assertEqual(v['task_id'],manifest['task_id'])
            self.assertEqual(v['slug'],entry['slug'])
            self.assertEqual(v['independent_review_status'],'PENDING')
            self.assertFalse(v['execution_allowed'])
            self.assertFalse(v['production_article_changed'])
            self.assertEqual(len(v['planning_candidate']['introduction_angles']),3)
            self.assertEqual(len(v['original_product_inventory']),6)
            self.assertTrue(v['product_qa']['new_product_qa_required'])
            self.assertEqual(v['product_qa']['existing_independent_receipt'],'STALE_CONTRACT')
            self.assertEqual(len(v['original_job_sha256']),len(set(v['original_job_sha256'])))

    def test_first_independent_product_qa_receipt_is_readonly_and_redacted(self):
        receipt=json.loads((FOLDER/'first-product-qa-readonly.json').read_text())
        self.assertEqual(receipt['review_status'],'ACCEPTED')
        self.assertEqual(receipt['product_issues'],[])
        self.assertFalse(receipt['production_job_changed'])
        self.assertFalse(receipt['source_evidence_changed'])
        self.assertNotIn('metadata',receipt)
        self.assertNotIn('session_id',receipt)

    def test_first_independent_planning_qa_detects_real_replanning_need(self):
        receipt=json.loads((FOLDER/'first-planning-independent-qa.json').read_text())
        self.assertEqual(receipt['slug'],'insulated-lunch-bags-for-daily-use')
        self.assertEqual(receipt['verdict'],'REVISE')
        self.assertTrue(receipt['valid_product_ref_coverage'])
        self.assertFalse(receipt['original_job_changed'])
        self.assertFalse(receipt['review']['portfolio_distinctness_sufficient'])
        self.assertFalse(receipt['review']['three_discovery_angles_useful'])
        self.assertEqual({x['ref'] for x in receipt['review']['source_ref_findings']},
                         {'p1','p2','p3','p4','p5','p6'})

    def test_duplicate_family_hints_are_not_conflated_with_decisions(self):
        manifest=json.loads((FOLDER/'manifest.json').read_text())
        known={x['slug'] for x in manifest['entries']}
        for group in manifest['potential_overlapping_topic_families']:
            self.assertGreaterEqual(len(group['slugs']),2)
            self.assertTrue(set(group['slugs']) <= known)
        self.assertIn('not proven duplicate',manifest['topic_overlap_note'])

if __name__=='__main__':
    unittest.main()
