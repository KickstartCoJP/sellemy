from __future__ import annotations
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from payload_schema import validate_payload, validate_evidence, match_refs, PayloadValidationError  # noqa: E402
from fixtures import valid_payload, valid_evidence  # noqa: E402


class ValidatePayloadTests(unittest.TestCase):
    def test_valid_payload_passes(self):
        validate_payload(valid_payload())  # must not raise

    def test_missing_lead_fails_closed(self):
        payload = valid_payload()
        del payload['lead']
        with self.assertRaises(PayloadValidationError):
            validate_payload(payload)

    def test_empty_lead_fails_closed(self):
        payload = valid_payload()
        payload['lead'] = '   '
        with self.assertRaises(PayloadValidationError):
            validate_payload(payload)

    def test_missing_product_description_fails_closed(self):
        payload = valid_payload()
        del payload['products'][0]['description']
        with self.assertRaises(PayloadValidationError):
            validate_payload(payload)

    def test_empty_product_description_fails_closed(self):
        payload = valid_payload()
        payload['products'][3]['description'] = ''
        with self.assertRaises(PayloadValidationError):
            validate_payload(payload)

    def test_comparison_groups_must_partition_refs(self):
        payload = valid_payload()
        payload['comparison_groups'][1]['product_refs'] = ['p1', 'p4']
        with self.assertRaises(PayloadValidationError):
            validate_payload(payload)

    def test_variable_group_count_passes(self):
        payload = valid_payload()
        payload['comparison_groups'] = [{
            'id': 'all-use-cases', 'title': '用途別に比較', 'angle': '6商品を用途で比較します。',
            'product_refs': [f'p{i}' for i in range(1, 7)],
        }]
        validate_payload(payload)

    def test_wrong_product_count_fails_closed(self):
        payload = valid_payload()
        payload['products'] = payload['products'][:5]
        with self.assertRaises(PayloadValidationError):
            validate_payload(payload)

    def test_duplicate_ref_fails_closed(self):
        payload = valid_payload()
        payload['products'][1]['ref'] = payload['products'][0]['ref']
        with self.assertRaises(PayloadValidationError):
            validate_payload(payload)


class ValidateEvidenceTests(unittest.TestCase):
    def test_valid_evidence_passes(self):
        validate_evidence(valid_evidence())  # must not raise

    def test_duplicate_asin_fails_closed(self):
        evidence = valid_evidence()
        evidence['products'][1]['asin'] = evidence['products'][0]['asin']
        with self.assertRaises(PayloadValidationError):
            validate_evidence(evidence)

    def test_noncanonical_product_id_fails_closed(self):
        evidence = valid_evidence()
        evidence['products'][0]['product_id'] = 'article-local-id'
        with self.assertRaises(PayloadValidationError):
            validate_evidence(evidence)

    def test_missing_canonical_url_fails_closed(self):
        evidence = valid_evidence()
        del evidence['canonical_url']
        with self.assertRaises(PayloadValidationError):
            validate_evidence(evidence)

    def test_wrong_evidence_product_count_fails_closed(self):
        evidence = valid_evidence()
        evidence['products'] = evidence['products'][:6] + [dict(evidence['products'][0])]
        # 7 entries now, still unique asin issue not guaranteed; force count check via truncation instead
        evidence['products'] = evidence['products'][:7]
        with self.assertRaises(PayloadValidationError):
            validate_evidence(evidence)


class MatchRefsTests(unittest.TestCase):
    def test_matching_refs_passes(self):
        match_refs(valid_payload(), valid_evidence())  # must not raise

    def test_mismatched_refs_fails_closed(self):
        payload = valid_payload()
        payload['products'][0]['ref'] = 'p99'
        with self.assertRaises(PayloadValidationError):
            match_refs(payload, valid_evidence())

    def test_mismatched_slug_fails_closed(self):
        payload = valid_payload()
        payload['slug'] = 'different-slug'
        with self.assertRaises(PayloadValidationError):
            match_refs(payload, valid_evidence())


if __name__ == '__main__':
    unittest.main()
