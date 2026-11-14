import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from audit_category_article_links import build, DEFAULT_TAXONOMY


class CategoryArticleAssociationAuditTests(unittest.TestCase):
    def test_complete_candidate_inventory_has_no_automatic_publication(self):
        taxonomy = json.loads(DEFAULT_TAXONOMY.read_text())
        articles = json.loads((ROOT/'json/articles.json').read_text())
        report = build(taxonomy, articles)
        self.assertEqual(report['counts']['published_articles'],
                         sum(x.get('status') == 'published' for x in articles))
        self.assertEqual(report['counts']['active_category_tags'], 720)
        self.assertEqual(report['counts']['pillar_groups'], 21)
        self.assertTrue(all(not x['publication_eligible'] for x in report['articles']))
        self.assertTrue(all(x['approved_articles'] == 0 for x in report['pillars']))
        self.assertTrue(all(x['status'] in ('NO_LITERAL_MATCH', 'CANDIDATE_SINGLE_PILLAR',
                                            'AMBIGUOUS_PILLAR') for x in report['articles']))
        self.assertTrue(all(x['genre'] == next(p['genre'] for p in report['pillars']
                        if p['id'] == y) for x in report['articles']
                        for y in x['candidate_pillars']))

    def test_no_duplicate_article_slugs(self):
        taxonomy = json.loads(DEFAULT_TAXONOMY.read_text())
        article = {'slug': 'same', 'status': 'published', 'title': '髪', 'category': 'beauty'}
        with self.assertRaisesRegex(ValueError, 'duplicate published article'):
            build(taxonomy, [article, dict(article)])


if __name__ == '__main__':
    unittest.main()
