from __future__ import annotations
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

from renderer import render_article  # noqa: E402
from qa import run_qa  # noqa: E402
from review_gate import run_review_gate  # noqa: E402

SLUGS = (
    'cordless-vacuum-cleaners-6-picks',
    'robot-vacuum-cleaners-6-picks',
    'electric-kettles-6-picks',
    'wireless-speakers-6-picks',
)


def _load(slug: str):
    payload = json.loads((ROOT / 'data' / 'payloads' / f'{slug}.json').read_text(encoding='utf-8'))
    evidence = json.loads((ROOT / 'data' / 'evidence' / f'{slug}.json').read_text(encoding='utf-8'))
    return payload, evidence


class GrowthArticlesRenderAndQaTests(unittest.TestCase):
    def test_all_four_articles_render_and_pass_review_gate(self):
        for slug in SLUGS:
            with self.subTest(slug=slug):
                payload, evidence = _load(slug)
                html = render_article(payload, evidence)
                findings = run_review_gate(payload, evidence)
                self.assertEqual(findings, [], msg=f'{slug}: {findings}')
                self.assertIn(payload['h1'], html)

    def test_all_four_articles_pass_machine_qa(self):
        for slug in SLUGS:
            with self.subTest(slug=slug):
                payload, evidence = _load(slug)
                html = render_article(payload, evidence)
                results = run_qa(html, payload, evidence)
                self.assertTrue(results['lead_in_range'], msg=f'{slug} lead_len={results["lead_len"]}')
                self.assertTrue(results['summary_in_range'], msg=f'{slug} summary_len={results["summary_len"]}')
                self.assertTrue(results['how_to_choose_in_range'], msg=f'{slug} how_to_choose_len={results["how_to_choose_len"]}')
                self.assertTrue(results['main_in_range'], msg=f'{slug} main_len={results["main_len"]}')
                self.assertTrue(results['description_hard_min_pass'], msg=f'{slug} description_lens={results["description_lens"]}')
                self.assertTrue(results['h3_len_pass'], msg=f'{slug} h3_lens={results["h3_lens"]}')
                self.assertTrue(results['banned_phrase_pass'], msg=f'{slug} hits={results["banned_phrase_hits"]}')
                self.assertTrue(results['description_uniqueness_pass'], msg=f'{slug} dupes={results["near_duplicate_descriptions"]}')
                self.assertTrue(results['no_repeated_sentences_pass'], msg=f'{slug} repeated={results["repeated_sentences"]}')
                self.assertTrue(results['asin_pass'], msg=f'{slug} asins={results["asin_count"]}/{results["asin_unique_count"]}')
                self.assertTrue(results['affiliate_pass'], msg=f'{slug} counts={results["affiliate_counts"]}')
                self.assertTrue(results['image_identity_pass'], msg=f'{slug} missing={results["missing_product_images"]}')
                self.assertTrue(results['canonical_pass'])
                self.assertTrue(results['fixed_price_pass'], msg=f'{slug} hits={results["fixed_price_hits"]}')
                self.assertTrue(results['nav_footer_pass'])

if __name__ == '__main__':
    unittest.main()
