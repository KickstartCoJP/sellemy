from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class TopLayoutTests(unittest.TestCase):
    def test_recent_links_have_scoped_alignment(self):
        css=(ROOT/"css/style.css").read_text()
        self.assertIn('body[data-category="top"] .recent-publications > p',css)
        self.assertIn('text-align: center;',css.split('body[data-category="top"] .recent-publications > p',1)[1].split('}',1)[0])
        html=(ROOT/"index.html").read_text()
        self.assertIn('美容・家電・日用品の比較記事をもっと見る',html)
        self.assertIn('紹介商品をカテゴリ別に探す',html)
