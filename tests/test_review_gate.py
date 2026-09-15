from __future__ import annotations
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from review_gate import run_review_gate  # noqa: E402
from fixtures import valid_payload, valid_evidence  # noqa: E402


class ReviewGateJargonTests(unittest.TestCase):
    def test_clean_payload_has_no_findings_for_jargon(self):
        findings = run_review_gate(valid_payload(), valid_evidence())
        jargon_findings = [f for f in findings if 'jargon' in f.message]
        self.assertEqual(jargon_findings, [])

    def test_internal_jargon_in_lead_is_flagged(self):
        payload = valid_payload()
        payload['lead'] = 'Amazonで実在を確認したこの商品は、' + payload['lead']
        findings = run_review_gate(payload, valid_evidence())
        self.assertTrue(any(f.field == 'lead' for f in findings))

    def test_internal_jargon_in_description_is_flagged(self):
        payload = valid_payload()
        payload['products'][0]['description'] = 'このpipelineで検証済みの商品です。' + payload['products'][0]['description']
        findings = run_review_gate(payload, valid_evidence())
        self.assertTrue(any(f.field == 'description' and f.ref == 'p1' for f in findings))


class ReviewGateRawTitlePasteTests(unittest.TestCase):
    def test_h3_identical_to_raw_amazon_title_is_flagged(self):
        payload = valid_payload()
        evidence = valid_evidence()
        payload['products'][0]['h3'] = evidence['products'][0]['amazon_title']
        findings = run_review_gate(payload, evidence)
        self.assertTrue(any(f.field == 'h3' and f.ref == 'p1' for f in findings))

    def test_human_written_h3_is_not_flagged(self):
        payload = valid_payload()
        evidence = valid_evidence()
        findings = run_review_gate(payload, evidence)
        h3_findings = [f for f in findings if f.field == 'h3']
        self.assertEqual(h3_findings, [])

    def test_long_verbatim_title_run_pasted_into_description_is_flagged(self):
        payload = valid_payload()
        evidence = valid_evidence()
        payload['products'][2]['description'] = evidence['products'][2]['amazon_title'] + 'です。' * 20
        findings = run_review_gate(payload, evidence)
        self.assertTrue(any(f.field == 'description' and f.ref == 'p3' for f in findings))


class ReviewGatePriceInDescriptionTests(unittest.TestCase):
    def test_yen_price_in_description_is_flagged(self):
        payload = valid_payload()
        payload['products'][4]['description'] = '価格は¥2,980です。' + payload['products'][4]['description']
        findings = run_review_gate(payload, valid_evidence())
        self.assertTrue(any(f.field == 'description' and f.ref == 'p5' and 'yen' in f.message for f in findings))

    def test_spec_numbers_without_currency_are_not_flagged_as_price(self):
        payload = valid_payload()
        payload['products'][4]['description'] = '吸引力は4000Pa、容量は1.0Lです。' + payload['products'][4]['description']
        findings = run_review_gate(payload, valid_evidence())
        price_findings = [f for f in findings if f.ref == 'p5' and 'yen' in f.message]
        self.assertEqual(price_findings, [])


if __name__ == '__main__':
    unittest.main()
