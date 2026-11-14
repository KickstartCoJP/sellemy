import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUG = 'grooming-routine'
ARTICLE_SLUGS = [
    'autumn-hair-loss-care-shampoo-6-picks','autumn-hair-dryers-6-picks','electric-toothbrushes-6-picks',
    'electric-shavers-6-picks','autumn-hair-irons-6-picks','home-ipl-hair-removal-devices-6-picks',
    'gray-hair-color-treatments-6-picks','autumn-perfumes-6-picks','leave-in-hair-treatments-6-picks',
    'mens-hair-growth-tonics-6-picks','water-flossers-6-picks','deodorant-antiperspirants-6-picks',
    'hair-brushes-for-frizz-6-picks','hot-air-styling-brushes-6-picks','mouthwash-oral-care-6-picks',
    'rinse-out-hair-masks-6-picks','womens-hair-growth-serums-6-picks','hair-setting-sprays',
]

class PurposeGroomingReleaseTests(unittest.TestCase):
    def test_feature_page_and_navigation_contract(self):
        text=(ROOT/'themes'/f'{SLUG}.html').read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'<h2>(.*?)</h2>', text), ['髪を整える','ひげ・ムダ毛を整える','口元・汗・香りを整える'])
        links=set(re.findall(r'href="/article/[^\"]+\.html"', text))
        self.assertEqual(len(links),18)
        self.assertEqual(text.count('https://www.sellemy.jp/themes/grooming-routine.html'),1)
        self.assertEqual((ROOT/'themes/index.html').read_text(encoding='utf-8').count('/themes/grooming-routine.html'),1)
        self.assertEqual((ROOT/'sitemap.xml').read_text(encoding='utf-8').count('https://www.sellemy.jp/themes/grooming-routine.html'),1)

    def test_exact_18_relations_and_reverse_links(self):
        con=sqlite3.connect(ROOT/'data/sellemy.db')
        try:
            rows=con.execute("select a.slug,a.category,r.relation_type from article_feature_relations r join articles a on a.article_id=r.article_id where r.feature_id='PH-GROOMING' and r.active=1 order by a.slug").fetchall()
        finally:
            con.close()
        self.assertEqual({r[0] for r in rows}, set(ARTICLE_SLUGS))
        self.assertEqual(sum(r[2]=='core' for r in rows),13)
        self.assertEqual(sum(r[2]=='supporting' for r in rows),5)
        for slug,category,_ in rows:
            page=(ROOT/'article'/category/f'{slug}.html').read_text(encoding='utf-8')
            self.assertEqual(page.count('/themes/grooming-routine.html'),1,slug)

if __name__=='__main__':
    unittest.main()
