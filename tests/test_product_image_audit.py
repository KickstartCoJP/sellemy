import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from product_image_audit import audit_product_images

class ProductImageAuditTests(unittest.TestCase):
    def test_same_image_url_holds_qa(self):
        p = [{'ref': f'p{i}', 'image_url': f'https://example.test/p{i}.jpg'} for i in range(6)]
        p[1]['image_url'] = p[0]['image_url']
        result = audit_product_images(p, {f'p{i}': b'test' + bytes([i]) for i in range(6)})
        self.assertEqual(result['status'], 'FAIL')
        self.assertTrue(any('same_image_url' in x['reasons'] for x in result['suspect_pairs']))

    def test_same_image_bytes_holds_qa(self):
        p = [{'ref': f'p{i}', 'image_url': f'https://example.test/p{i}.jpg'} for i in range(6)]
        result = audit_product_images(p, {f'p{i}': b'same' for i in range(6)})
        self.assertEqual(result['status'], 'FAIL')
        self.assertTrue(any('same_image_bytes' in x['reasons'] for x in result['suspect_pairs']))

    def test_missing_image_is_not_pass(self):
        p = [{'ref': f'p{i}', 'image_url': f'https://example.test/p{i}.jpg'} for i in range(6)]
        result = audit_product_images(p, {})
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertEqual(len(result['issues']), 6)

if __name__ == '__main__':
    unittest.main()

class IntegrationContractTests(unittest.TestCase):
    def test_image_gate_fails_closed_when_enabled_without_evidence(self):
        import os
        from unittest.mock import patch
        from qa import run_qa
        from tests.fixtures import valid_payload, valid_evidence
        from renderer import render_article
        payload, evidence = valid_payload(), valid_evidence()
        with patch.dict(os.environ, {'SELLEMY_REQUIRE_PRODUCT_IMAGE_AUDIT': '1'}):
            result = run_qa(render_article(payload, evidence), payload, evidence)
        self.assertFalse(result['product_image_audit_pass'])
        self.assertFalse(result['overall_pass'])
