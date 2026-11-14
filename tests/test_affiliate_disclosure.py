from pathlib import Path
import unittest
from fixtures import valid_payload, valid_evidence
from renderer import render_article
from qa import main_text_length

ROOT=Path(__file__).resolve().parents[1]
COPY="本記事にはアフィリエイト広告が含まれます。"
AMAZON="Amazonのアソシエイトとして、Sellemyは適格販売により収入を得ています。"

class AffiliateDisclosureTests(unittest.TestCase):
    def test_renderer_placement_and_disclosure(self):
        page=render_article(valid_payload(),valid_evidence())
        self.assertEqual(page.count(COPY),1)
        self.assertEqual(page.count(AMAZON),0)
        self.assertTrue(page.index('class="breadcrumb"') < page.index(COPY) < page.index('<h1>'))
    def test_all_existing_articles(self):
        pages=[p for p in (ROOT/'article').rglob('*.html') if 'class="breadcrumb"' in p.read_text()]
        self.assertGreaterEqual(len(pages),390)
        for p in pages:
            s=p.read_text()
            with self.subTest(p=p):
                self.assertEqual(s.count(COPY),1)
                self.assertEqual(s.count(AMAZON),0)
                self.assertTrue(s.index('class="breadcrumb"') < s.index(COPY) < s.index('<h1>'))
    def test_disclosure_does_not_inflate_writer_length(self):
        page=render_article(valid_payload(),valid_evidence())
        html_without_disclosure=page.replace(
            '<p class="affiliate-disclosure">本記事にはアフィリエイト広告が含まれます。商品リンクを通じた購入により、Sellemyが紹介料を受け取ることがあります。</p>', '')
        self.assertEqual(main_text_length(page),main_text_length(html_without_disclosure))
    def test_amazon_disclosure_only_homepage(self):
        self.assertEqual((ROOT/"index.html").read_text().count(AMAZON),1)
        self.assertIn("site-advertising-notice",(ROOT/"index.html").read_text())
        for page in ("articles.html","products.html"):
            self.assertNotIn(AMAZON,(ROOT/page).read_text())
    def test_three_retailers_covered_by_generic_copy(self):
        tracked=0
        for p in (ROOT/"article").rglob("*.html"):
            page=p.read_text()
            if "tag=sellemy-22" in page:
                tracked+=1
                with self.subTest(article=p):
                    self.assertIn("hb.afl.rakuten.co.jp",page)
                    self.assertIn("ck.jp.ap.valuecommerce.com",page)
                    self.assertEqual(page.count(COPY),1)
        self.assertGreaterEqual(tracked,390)
    def test_font_size(self):
        style=(ROOT/'css/style.css').read_text()
        self.assertIn('.article-detail .affiliate-disclosure',style)
        self.assertIn('font-size: 14px',style)
