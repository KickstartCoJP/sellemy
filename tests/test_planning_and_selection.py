from __future__ import annotations

import json
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

import planning_runtime
from codex_provider import CodexProviderError
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
    def _env(self):
        return {
            'SELLEMY_PLANNER_API_WEIGHT': '0',
            'SELLEMY_PLANNER_CODEX_WEIGHT': '100',
            'SELLEMY_PLANNING_PRIMARY_COMMAND': '/bin/codex',
            'SELLEMY_PLANNING_PRIMARY_KIND': 'codex',
            'SELLEMY_PLANNING_PRIMARY_MODEL': 'codex-model',
            'SELLEMY_PLANNING_SECONDARY_COMMAND': '/bin/claude',
            'SELLEMY_PLANNING_SECONDARY_KIND': 'claude',
            'SELLEMY_PLANNING_SECONDARY_MODEL': 'sonnet',
            'SELLEMY_PLANNING_SECONDARY_CERTIFIED': 'true',
        }

    def test_codex_primary_success(self):
        value = {'candidates': [candidate('codex-topic', 'beauty')]}
        with patch.dict('os.environ', self._env(), clear=True), \
             patch.object(planning_runtime, 'codex_generate_persistent', return_value=(value, {'session_id': 'sid'})), \
             patch.object(planning_runtime.subprocess, 'run') as claude:
            rows = planning_runtime.discover_candidates()
        self.assertEqual(rows[0]['slug'], 'codex-topic')
        self.assertEqual(rows.provider_metadata['planning_provider_used'], 'codex:codex-model')
        self.assertFalse(rows.provider_metadata['fallback_used'])
        claude.assert_not_called()

    def test_codex_failure_falls_back_to_claude(self):
        accepted = {'candidates': [candidate('claude-topic', 'beauty')]}
        stdout = json.dumps({'structured_output': accepted})
        completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr='')
        with patch.dict('os.environ', self._env(), clear=True), \
             patch.object(planning_runtime, 'codex_generate_persistent', side_effect=CodexProviderError('limit')), \
             patch.object(planning_runtime.subprocess, 'run', return_value=completed) as claude:
            rows = planning_runtime.discover_candidates()
        self.assertEqual(rows[0]['slug'], 'claude-topic')
        self.assertTrue(rows.provider_metadata['fallback_used'])
        self.assertEqual(rows.provider_metadata['planning_provider_used'], 'claude:sonnet')
        self.assertEqual(claude.call_count, 1)

    def test_codex_failure_without_secondary_fails_closed(self):
        env = self._env(); env['SELLEMY_PLANNING_SECONDARY_CERTIFIED'] = 'false'
        with patch.dict('os.environ', env, clear=True), \
             patch.object(planning_runtime, 'codex_generate_persistent', side_effect=CodexProviderError('limit')):
            with self.assertRaisesRegex(planning_runtime.PlanningError, 'certified Secondary Planning unavailable'):
                planning_runtime.discover_candidates()

    def test_claude_secondary_schema_failure_fails_closed(self):
        bad = subprocess.CompletedProcess([], 0, stdout='{"structured_output": {}}', stderr='')
        with patch.dict('os.environ', self._env(), clear=True), \
             patch.object(planning_runtime, 'codex_generate_persistent', side_effect=CodexProviderError('limit')), \
             patch.object(planning_runtime.subprocess, 'run', return_value=bad):
            with self.assertRaises(planning_runtime.PlanningError):
                planning_runtime.discover_candidates()

    def test_planning_context_keeps_full_duplicate_guard_compact(self):
        with tempfile.TemporaryDirectory() as tmp:
            articles = Path(tmp) / 'articles.json'
            rows = [
                {'slug': f'topic-{i}-item', 'category': 'gadget', 'title': ('長いタイトル' * 30) + str(i), 'summary': 'x' * 500}
                for i in range(320)
            ]
            articles.write_text(json.dumps(rows, ensure_ascii=False))
            seeds = Path(tmp) / 'seeds.json'; seeds.write_text('{"categories": {}}')
            with patch.object(planning_runtime, 'ARTICLES', articles), patch.object(planning_runtime, 'SEEDS', seeds):
                context = planning_runtime._context()
        self.assertNotIn('existing_articles', context)
        self.assertEqual(len(context['existing_slugs']), 320)
        self.assertEqual(len(context['recent_articles']), 36)
        self.assertLess(len(json.dumps(context, ensure_ascii=False)), 30000)

    def test_ranking_rejects_existing_intent_and_balances_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            articles = Path(tmp) / 'articles.json'
            articles.write_text(json.dumps([{'slug': 'used-topic', 'category': 'gadget', 'title': '使用済み商品', 'summary': ''}]))
            with patch.object(planning_runtime, 'ARTICLES', articles):
                ranked = planning_runtime.rank_candidates([
                    candidate('used-topic', 'gadget', 1), candidate('fresh-beauty', 'beauty', .8), candidate('fresh-gadget', 'gadget', .8)
                ], {'topic_metrics': [], 'product_metrics': []})
        self.assertEqual([row['slug'] for row in ranked], ['fresh-beauty', 'fresh-gadget'])


    def test_ranking_materially_favors_underrepresented_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            articles = Path(tmp) / 'articles.json'
            rows = ([{'slug': f'gadget-{i}-topic', 'category': 'gadget', 'title': f'家電{i}', 'summary': ''} for i in range(12)] +
                    [{'slug': f'beauty-{i}-topic', 'category': 'beauty', 'title': f'美容{i}', 'summary': ''} for i in range(5)] +
                    [{'slug': 'daily-0-topic', 'category': 'dailygoods', 'title': '日用品0', 'summary': ''}])
            articles.write_text(json.dumps(rows))
            with patch.object(planning_runtime, 'ARTICLES', articles):
                ranked = planning_runtime.rank_candidates([
                    candidate('fresh-gadget-topic', 'gadget', .8),
                    candidate('fresh-daily-topic', 'dailygoods', .8),
                ], {'topic_metrics': [], 'product_metrics': []})
        self.assertEqual(ranked[0]['slug'], 'fresh-daily-topic')
        self.assertGreater(ranked[0]['portfolio_balance'], ranked[1]['portfolio_balance'])

    def test_ranking_penalizes_recent_repeated_topic_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            articles = Path(tmp) / 'articles.json'
            articles.write_text(json.dumps([
                {'slug': 'gaming-mice-6-picks', 'category': 'gadget', 'title': 'ゲーミングマウス6選', 'summary': ''},
                {'slug': 'gaming-keyboards-6-picks', 'category': 'gadget', 'title': 'ゲーミングキーボード6選', 'summary': ''},
            ]))
            with patch.object(planning_runtime, 'ARTICLES', articles):
                ranked = planning_runtime.rank_candidates([
                    candidate('gaming-headsets-fresh', 'gadget', .8),
                    candidate('air-purifiers-fresh', 'gadget', .8),
                ], {'topic_metrics': [], 'product_metrics': []})
        self.assertEqual(ranked[0]['slug'], 'air-purifiers-fresh')
        repeated = next(row for row in ranked if row['slug'] == 'gaming-headsets-fresh')
        self.assertLess(repeated['topic_diversity'], ranked[0]['topic_diversity'])
        self.assertEqual(len(repeated['recent_family_hits']), 2)

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
