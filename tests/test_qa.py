from __future__ import annotations
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from qa import (  # noqa: E402
    find_fixed_prices, find_banned_phrases, containment_similarity,
    find_near_duplicate_descriptions, find_repeated_sentences,
    find_shared_openings_endings, main_text_length, run_qa,
)
from renderer import render_article  # noqa: E402
from fixtures import valid_payload, valid_evidence  # noqa: E402
from datetime import date  # noqa: E402

FIXED_DATE = date(2026, 1, 1)


class FixedPriceDetectionTests(unittest.TestCase):
    def test_yen_symbol_amount_is_detected(self):
        self.assertEqual(find_fixed_prices('本体価格は¥3,980です。'), ['¥3,980'])

    def test_en_suffix_amount_is_detected(self):
        self.assertEqual(find_fixed_prices('本体価格は3980円です。'), ['3980円'])

    def test_pa_spec_is_not_a_false_positive(self):
        self.assertEqual(find_fixed_prices('吸引力は4000Paです。'), [])

    def test_liter_spec_is_not_a_false_positive(self):
        self.assertEqual(find_fixed_prices('容量は1.0Lです。'), [])

    def test_watt_spec_is_not_a_false_positive(self):
        self.assertEqual(find_fixed_prices('出力は1000Wです。'), [])

    def test_weight_spec_is_not_a_false_positive(self):
        self.assertEqual(find_fixed_prices('本体重量は0.5kgです。'), [])


class BannedPhraseTests(unittest.TestCase):
    def test_internal_verification_phrase_is_flagged(self):
        self.assertIn('実在を確認', find_banned_phrases('Amazonで実在を確認した商品です。'))

    def test_old_template_boilerplate_is_flagged(self):
        text = '価格は変動するため、最新の販売条件と仕様はリンク先で確認してください'
        self.assertTrue(find_banned_phrases(text))

    def test_normal_prose_is_not_flagged(self):
        self.assertEqual(find_banned_phrases('軽量で扱いやすいコードレス掃除機です。'), [])


class ContainmentSimilarityTests(unittest.TestCase):
    def test_identical_text_scores_one(self):
        text = 'これはテスト用の説明文です。' * 10
        self.assertAlmostEqual(containment_similarity(text, text), 1.0)

    def test_distinct_descriptions_score_below_threshold(self):
        a = '軽量な設計で持ち運びやすく、吸引力も十分にあるモデルです。日常のちょっとした掃除に向いています。'
        b = '大容量ダストカップと自走式ヘッドを備えた多機能モデルで、家全体をまとめて掃除したい方に向いています。'
        self.assertLess(containment_similarity(a, b), 0.9)

    def test_near_duplicate_with_minor_edits_is_flagged(self):
        # Bigram containment is set-based, so a short *repeated* string is a degenerate
        # fixture: tripling it doesn't triple the distinct-bigram vocabulary (repeats
        # dedupe away), so swapping a repeated word hits a disproportionate share of
        # that small vocabulary. Use a long, non-repeating passage with a single
        # localized word swap instead -- the realistic "near duplicate" shape this
        # check exists to catch.
        a = (
            '本体重量わずか0.5kgという圧倒的な軽さが際立つ1台です。腕への負担が少ないため、'
            '天井付近のホコリ取りや階段の掃除など、持ち上げて使う場面でも扱いやすいのが魅力です。'
            'それでいて吸引力は50000Paとパワフルで、フローリングのゴミやペットの毛もしっかり吸い込みます。'
            '薄型設計のフロアヘッドは家具の下やソファの隙間にも入り込みやすく、'
            '掃除の手が届きにくい場所までカバーできます。'
        )
        b = a.replace('圧倒的な軽さ', '驚くほどの軽さ')
        self.assertGreaterEqual(containment_similarity(a, b), 0.9)


class DuplicateDetectionAcrossSixDescriptionsTests(unittest.TestCase):
    def test_identical_placeholder_descriptions_are_caught(self):
        payload = valid_payload()
        descriptions = [p['description'] for p in payload['products']]
        # fixtures.py intentionally uses the same repeated placeholder text for all six,
        # so this should be caught -- proving the checker actually discriminates.
        self.assertTrue(find_near_duplicate_descriptions(descriptions))

    def test_genuinely_distinct_descriptions_pass(self):
        descriptions = [
            '軽量な設計で持ち運びやすく、吸引力も十分にあるモデルです。日常のちょっとした掃除に向いています。' * 3,
            '大容量ダストカップと自走式ヘッドを備えた多機能モデルで、家全体をまとめて掃除したい方に向いています。' * 3,
            '静音設計が特長で、早朝や夜間の使用でも周囲を気にせず使えるのが魅力のモデルです。' * 3,
            '自動ゴミ収集ドックに対応しており、日々のお手入れの手間を大幅に減らせる一台です。' * 3,
            'ブランドとしての実績が豊富で、初めて購入する方でも安心して選べるロングセラーモデルです。' * 3,
            '防水性能に優れており、屋外やキッチン周りでも気兼ねなく使えるのがポイントです。' * 3,
        ]
        self.assertEqual(find_near_duplicate_descriptions(descriptions), [])

    def test_repeated_sentence_across_descriptions_is_flagged(self):
        shared = '価格は変動するため、最新の販売条件と仕様はリンク先で確認してください。'
        descriptions = [f'これは商品{i}の説明です。{shared}' for i in range(6)]
        self.assertTrue(find_repeated_sentences(descriptions))

    def test_no_repeated_sentence_when_all_unique(self):
        descriptions = [f'これは商品{i}だけの固有の説明文です、内容も番号ごとに異なります。' for i in range(6)]
        self.assertEqual(find_repeated_sentences(descriptions), [])

    def test_shared_opening_is_flagged(self):
        descriptions = ['本体重量わずか0.5kgという軽さが際立つ商品Aです。' + 'x' * 40,
                         '本体重量わずか0.5kgという軽さが際立つ商品Bです。' + 'y' * 40]
        dup_open, _ = find_shared_openings_endings(descriptions)
        self.assertTrue(dup_open)


class MainTextLengthTests(unittest.TestCase):
    def test_strips_tags_and_counts_text_only(self):
        html = '<html><body><main><p>あいう</p><h2>えお</h2></main></body></html>'
        self.assertEqual(main_text_length(html), 5)

    def test_ignores_text_outside_main(self):
        html = '<header>Top</header><main><p>あいう</p></main><footer>foot</footer>'
        self.assertEqual(main_text_length(html), 3)


class RunQaIntegrationTests(unittest.TestCase):
    def test_valid_payload_structural_checks_pass(self):
        payload = valid_payload()
        evidence = valid_evidence()
        html = render_article(payload, evidence, today=FIXED_DATE)
        results = run_qa(html, payload, evidence)
        self.assertTrue(results['asin_pass'])
        self.assertTrue(results['affiliate_pass'])
        self.assertTrue(results['canonical_pass'])
        self.assertTrue(results['image_identity_pass'])
        self.assertTrue(results['nav_footer_pass'])
        self.assertTrue(results['category_pass'])
        self.assertTrue(results['fixed_price_pass'])
        # fixtures.py intentionally reuses one description string for all six products,
        # so uniqueness must fail here -- proving run_qa's duplicate check is wired in.
        self.assertFalse(results['description_uniqueness_pass'])


if __name__ == '__main__':
    unittest.main()
