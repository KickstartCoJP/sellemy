from __future__ import annotations

import html
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = pathlib.Path('/Users/kickstart/sellemy-growth-runtime/data/competitor_categories/sellemy_category_strategy_final_20261012.json')
GROUPS = {
    'beauty': ('美容', 6),
    'dailygoods': ('日用品', 9),
    'gadget': ('家電', 6),
}


class CategoryNavigationPreviewTests(unittest.TestCase):
    def test_three_previews_reflect_final_21_pillars_without_changing_live_tops(self):
        taxonomy = json.loads(DATA.read_text(encoding='utf-8'))
        total = 0
        for slug, (genre, count) in GROUPS.items():
            original = ROOT / 'article' / slug / 'index.html'
            preview = ROOT / 'preview/category-navigation' / (slug + '.html')
            source, page = original.read_text(), preview.read_text()
            groups = [p for p in taxonomy['pillars'] if p['parent_id'] == 'genre:' + genre]
            self.assertEqual(len(groups), count)
            self.assertEqual(page.count('class="category-discovery-links"'), 1)
            self.assertEqual(page.count('href="#preview-category-'), count)
            self.assertEqual(page.count('class="nav-preview-target"'), count)
            self.assertTrue(page.index('class="category-intro"') < page.index('class="category-discovery"') < page.index('id="articleList"'))
            self.assertIn('<details>', page)
            self.assertIn('<summary>気になるテーマから探す</summary>', page)
            self.assertNotIn('class="nav-preview-grid"', page)
            self.assertIn('STAGING PREVIEW', page)
            for i, group in enumerate(groups, 1):
                self.assertIn(html.escape(group['name']), page)
                self.assertIn('href="#preview-category-' + str(i) + '"', page)
                self.assertIn('id="preview-category-' + str(i) + '"', page)
            for preserve in ['<h1>', 'class="category-eyecatch"', 'class="category-intro"', 'id="articleList"']:
                self.assertIn(preserve, source)
                self.assertIn(preserve, page)
            total += len(groups)
        self.assertEqual(total, 21)


if __name__ == '__main__':
    unittest.main()
