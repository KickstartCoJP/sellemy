from __future__ import annotations
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from renderer import render_article  # noqa: E402
from payload_schema import PayloadValidationError  # noqa: E402
from fixtures import valid_payload, valid_evidence  # noqa: E402

FIXED_DATE = date(2026, 1, 1)


class RendererVerbatimInsertionTests(unittest.TestCase):
    def test_lead_appears_verbatim(self):
        payload = valid_payload()
        html = render_article(payload, valid_evidence(), today=FIXED_DATE)
        self.assertIn(payload['lead'], html)

    def test_product_description_appears_verbatim(self):
        payload = valid_payload()
        html = render_article(payload, valid_evidence(), today=FIXED_DATE)
        self.assertIn(payload['products'][2]['description'], html)

    def test_h3_appears_verbatim_not_raw_amazon_title(self):
        payload = valid_payload()
        evidence = valid_evidence()
        html = render_article(payload, evidence, today=FIXED_DATE)
        self.assertIn(f"<h3>{payload['products'][0]['h3']}</h3>", html)
        self.assertNotIn(evidence['products'][0]['amazon_title'], html)

    def test_renderer_does_not_alter_prose_content(self):
        # A distinctive, non-generic marker string placed in the payload must survive
        # rendering unchanged -- proving the renderer copies text rather than
        # regenerating/paraphrasing it.
        payload = valid_payload()
        marker = 'ZZZ-UNIQUE-MARKER-テスト-ZZZ'
        payload['products'][4]['description'] = marker * 30
        html = render_article(payload, valid_evidence(), today=FIXED_DATE)
        self.assertIn(marker, html)

    def test_asin_and_affiliate_tag_come_from_evidence_not_payload(self):
        payload = valid_payload()
        evidence = valid_evidence()
        html = render_article(payload, evidence, today=FIXED_DATE)
        for product in evidence['products']:
            self.assertIn(f'data-asin="{product["asin"]}"', html)
            self.assertIn(f'amazon.co.jp/dp/{product["asin"]}?tag={evidence["amazon_tag"]}', html)


class RendererFailsClosedTests(unittest.TestCase):
    def test_missing_lead_raises_instead_of_rendering(self):
        payload = valid_payload()
        del payload['lead']
        with self.assertRaises(PayloadValidationError):
            render_article(payload, valid_evidence(), today=FIXED_DATE)

    def test_empty_description_raises_instead_of_padding(self):
        payload = valid_payload()
        payload['products'][0]['description'] = ''
        with self.assertRaises(PayloadValidationError):
            render_article(payload, valid_evidence(), today=FIXED_DATE)

    def test_missing_evidence_product_raises(self):
        payload = valid_payload()
        evidence = valid_evidence()
        evidence['products'] = evidence['products'][:5] + [dict(evidence['products'][5], ref='pX')]
        with self.assertRaises(PayloadValidationError):
            render_article(payload, evidence, today=FIXED_DATE)

    def test_slug_mismatch_raises(self):
        payload = valid_payload()
        payload['slug'] = 'other-slug'
        with self.assertRaises(PayloadValidationError):
            render_article(payload, valid_evidence(), today=FIXED_DATE)


class RendererStructureTests(unittest.TestCase):
    def test_exactly_six_cards_and_asins(self):
        html = render_article(valid_payload(), valid_evidence(), today=FIXED_DATE)
        self.assertEqual(html.count('class="item-card"'), 6)
        self.assertEqual(len(set(_extract_asins(html))), 6)

    def test_canonical_and_h1_present_once(self):
        html = render_article(valid_payload(), valid_evidence(), today=FIXED_DATE)
        evidence = valid_evidence()
        self.assertEqual(html.count(f'href="{evidence["canonical_url"]}"'), 1)
        self.assertEqual(html.count('<h1>'), 1)

    def test_all_three_category_breadcrumbs_render(self):
        expected = {'beauty': '美容', 'dailygoods': '日用品', 'gadget': '家電'}
        for category, label in expected.items():
            payload, evidence = valid_payload(), valid_evidence()
            payload['category'] = evidence['category'] = category
            evidence['canonical_url'] = f'https://www.sellemy.jp/article/{category}/test-widgets-6-picks.html'
            rendered = render_article(payload, evidence, today=FIXED_DATE)
            self.assertIn(f'href="/article/{category}/">{label}</a>', rendered)


def _extract_asins(html: str) -> list[str]:
    import re
    return re.findall(r'data-asin="([^"]+)"', html)


if __name__ == '__main__':
    unittest.main()
