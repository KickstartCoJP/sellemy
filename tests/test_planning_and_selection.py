from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

import planning_runtime
from product_selection import select_six


def candidate(slug='new-topic', category='beauty', score=.8):
    return {
        'slug': slug, 'category': category, 'query': '新しい商品テーマ', 'title': '新しい商品テーマ6選',
        'intent_key': slug, 'comparison_axes': [
            {'id': 'gentle', 'label': '使い心地', 'keywords': ['やさしい']},
            {'id': 'power', 'label': '性能', 'keywords': ['高性能']},
        ],
        'signals': {key: score for key in ('search_demand', 'purchase_intent', 'product_viability', 'seasonality', 'profitability', 'evidence_availability')},
    }


class ContinuousPlanningTests(unittest.TestCase):
    def test_ranking_rejects_existing_intent_and_balances_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            articles = Path(tmp) / 'articles.json'
            articles.write_text(json.dumps([{'slug': 'used-topic', 'category': 'gadget', 'title': '使用済み商品', 'summary': ''}]))
            with patch.object(planning_runtime, 'ARTICLES', articles):
                ranked = planning_runtime.rank_candidates([
                    candidate('used-topic', 'gadget', 1), candidate('fresh-beauty', 'beauty', .8), candidate('fresh-gadget', 'gadget', .8)
                ], {'topic_metrics': [], 'product_metrics': []})
        self.assertEqual([row['slug'] for row in ranked], ['fresh-beauty', 'fresh-gadget'])

    def test_no_candidate_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            articles = Path(tmp) / 'articles.json'; articles.write_text('[]')
            with patch.object(planning_runtime, 'ARTICLES', articles):
                with self.assertRaises(planning_runtime.PlanningError):
                    planning_runtime.plan_next_topic([], {'topic_metrics': [], 'product_metrics': []})


class VariableProductSelectionTests(unittest.TestCase):
    def test_selection_is_not_fixed_price_order(self):
        topic = candidate()
        rows = []
        for i in range(8):
            rows.append({
                'asin': f'B0TEST{i:04d}', 'amazon_title': ('高性能 ' if i in (6, 7) else 'やさしい ') + str(i),
                'brand': f'Brand{i % 4}', 'image_url': f'https://example.com/{i}.jpg',
                'observed_price': (i + 1) * 1000, 'discovery_score': 1,
            })
        selected, metadata = select_six(rows, topic, {'product_metrics': []})
        self.assertEqual(len(selected), 6)
        self.assertEqual(len({row['asin'] for row in selected}), 6)
        self.assertTrue(metadata['price_used_as_constraint_only'])
        self.assertNotEqual([row['observed_price'] for row in selected], sorted(row['observed_price'] for row in selected))
        self.assertEqual(metadata['comparison_axes_covered'], ['gentle', 'power'])


if __name__ == '__main__':
    unittest.main()
