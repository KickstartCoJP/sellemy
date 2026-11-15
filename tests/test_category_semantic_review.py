import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from stage_category_semantic_review import TAXONOMY,combine,render_pages


class CategorySemanticTests(unittest.TestCase):
    def setUp(self):
        self.tax = json.loads(TAXONOMY.read_text())
        self.art = json.loads((ROOT/'json/articles.json').read_text())
        self.published = [x for x in self.art if x.get('status')=='published']
        self.by_genre = {'beauty':'美容','dailygoods':'日用品','gadget':'家電'}
        self.first = {g:next(p['id'] for p in self.tax['pillars'] if p['parent_id']=='genre:'+v)
                      for g,v in self.by_genre.items()}
        self.results = {a['slug']:{'pillar_id':self.first[a['category']],'confidence':'high',
                                   'reason':'test'} for a in self.published}

    def test_all_published_articles_and_no_public_link_approval(self):
        report = combine(self.tax,self.art,self.results)
        self.assertEqual(report['counts']['published_articles'],447)
        self.assertEqual(report['counts']['public_mappings_approved'],0)
        self.assertTrue(all(not a['public_mapping_approved'] for a in report['articles']))

    def test_source_assignments_have_product_word_or_hold_rationale(self):
        for genre in ('beauty', 'dailygoods', 'gadget'):
            p = ROOT/'staging/category-association/source'/f'{genre}.json'
            data = json.loads(p.read_text())
            self.assertTrue(data['assignments'])
            for row in data['assignments']:
                self.assertTrue(row['reason'])
                if row['confidence'] == 'high':
                    self.assertIn('商品語「', row['reason'])

    def test_cross_genre_mapping_fails_closed(self):
        target = self.published[0]
        foreign = next(p['id'] for p in self.tax['pillars'] if p['parent_id'] != 'genre:'+self.by_genre[target['category']])
        self.results[target['slug']]['pillar_id']=foreign
        with self.assertRaisesRegex(ValueError,'cross-genre'):
            combine(self.tax,self.art,self.results)

    def test_missing_output_fails_closed(self):
        del self.results[self.published[0]['slug']]
        with self.assertRaisesRegex(ValueError,'incomplete semantic review'):
            combine(self.tax,self.art,self.results)

    def test_preview_page_is_noindex_and_real_published_links_only(self):
        report=combine(self.tax,self.art,self.results)
        with tempfile.TemporaryDirectory() as td:
            pages=render_pages(report,Path(td))
            self.assertTrue(pages)
            content=pages[0].read_text()
            self.assertIn('noindex,nofollow',content)
            self.assertIn('STAGING PREVIEW',content)
            self.assertNotIn('rel="canonical"',content)


if __name__ == '__main__':unittest.main()
