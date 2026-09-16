from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import growth_runtime  # noqa: E402
import planning_runtime  # noqa: E402
import writer_runtime  # noqa: E402
from fixtures import valid_evidence, valid_payload  # noqa: E402


class ScheduledRouteTests(unittest.TestCase):
    def test_scheduler_uses_new_runtime_not_legacy_generator(self):
        script = (ROOT / 'ops' / 'run-growth.sh').read_text(encoding='utf-8')
        self.assertIn('pipeline/growth_runtime.py --publish --scheduled', script)
        self.assertNotIn('pipeline/grow.py', script)
        self.assertNotIn('pipeline/run.py', script)

    def test_fixed_heartbeat_preserves_initial_slots_and_exposes_growth_slots(self):
        plist = (ROOT / 'ops' / 'com.kickstart.sellemy-growth.plist').read_text(encoding='utf-8')
        for hour in (4, 10, 16, 22):
            self.assertIn(f'<key>Hour</key><integer>{hour}</integer>', plist)
        config = (ROOT / 'config' / 'adaptive_publish.json').read_text(encoding='utf-8')
        self.assertIn('"2": ["04:10", "16:10"]', config)

    def test_legacy_entry_points_fail_closed(self):
        for module in ('pipeline/grow.py', 'pipeline/run.py'):
            completed = subprocess.run(
                [sys.executable, module], cwd=ROOT, text=True, capture_output=True, check=False
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn('retired', completed.stderr)

    def test_legacy_modules_contain_no_public_prose_authoring(self):
        banned = ('実在を確認', '価格は変動するため', 'def card(', 'def render(')
        for module in ('pipeline/grow.py', 'pipeline/run.py'):
            source = (ROOT / module).read_text(encoding='utf-8')
            for phrase in banned:
                self.assertNotIn(phrase, source)

    def test_writer_command_resolves_with_launchd_minimal_path(self):
        with patch.dict('os.environ', {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}, clear=True):
            command = writer_runtime._command()
        self.assertTrue(Path(command[0]).is_file())

    def test_search_intent_normalization_removes_editorial_suffixes(self):
        self.assertEqual(
            planning_runtime._normalize('LED デスクライト おすすめ6選'),
            planning_runtime._normalize('LEDデスクライト 比較'),
        )

    def test_production_entry_has_no_finite_topic_queue(self):
        source = (ROOT / 'pipeline' / 'growth_runtime.py').read_text(encoding='utf-8')
        self.assertNotIn('growth_topics', source)
        self.assertNotIn('def next_topic', source)
        self.assertNotIn('TOPICS =', source)
        self.assertIn('select_viable_topic', source)

    def test_porcelain_parser_preserves_first_modified_path(self):
        output = ' M data/sellemy.db\n?? article/gadget/new.html\n'
        self.assertEqual(
            growth_runtime._parse_porcelain_paths(output),
            {'data/sellemy.db', 'article/gadget/new.html'},
        )

    def test_compact_search_key_is_bounded(self):
        key = growth_runtime._compact_search_key({
            'brand': 'Exampleのストアを表示',
            'amazon_title': '【2026新モデル】 加湿器 大容量 9L 超高機能 長時間運転 その他の非常に長い販売文句' * 3,
        })
        self.assertLessEqual(len(key), 48)
        self.assertNotIn('ストアを表示', key)
        self.assertNotIn('【', key)

    def test_publication_unit_includes_catalog_and_top(self):
        paths = growth_runtime._expected_publish_paths('new', 'beauty')
        self.assertIn('json/products.json', paths)
        self.assertIn('index.html', paths)
        self.assertIn('data/sellemy.db', paths)
        self.assertIn('data/eyecatch-receipts/new.json', paths)

    def test_existing_ga4_measurement_is_preserved(self):
        ga4 = (ROOT / 'js' / 'ga4.js').read_text(encoding='utf-8')
        self.assertIn('G-SZ5RQR5H7L', ga4)
        rendered = growth_runtime.render_article(valid_payload(), valid_evidence())
        self.assertIn('G-SZ5RQR5H7L', rendered)


class ProductViabilityPlanningTests(unittest.TestCase):
    def _topic(self, slug):
        return {
            'slug': slug, 'category': 'gadget', 'query': slug, 'title': slug, 'intent_key': slug,
            'comparison_axes': [{'id': 'use', 'label': '用途', 'keywords': ['用途']}],
            'signals': {key: .8 for key in ('search_demand', 'purchase_intent', 'product_viability', 'seasonality', 'profitability', 'evidence_availability')},
        }

    def test_skips_unviable_top_topic_and_reuses_viable_probe(self):
        first, second = self._topic('first-topic'), self._topic('second-topic')
        products = [{'asin': f'B0TEST{i:04d}'} for i in range(6)]
        with (
            patch.object(growth_runtime, 'rank_candidates', return_value=[first, second]),
            patch.object(growth_runtime, 'discover_products', side_effect=[[], products]) as discover,
        ):
            topic, rows, probes = growth_runtime.select_viable_topic([first, second], {}, cache_only=False)
        self.assertEqual(topic['slug'], 'second-topic')
        self.assertIs(rows, products)
        self.assertEqual(discover.call_count, 2)
        self.assertEqual([p['candidate_count'] for p in probes], [0, 6])

    def test_all_unviable_topics_fail_closed(self):
        topics = [self._topic('first-topic'), self._topic('second-topic')]
        with (
            patch.object(growth_runtime, 'rank_candidates', return_value=topics),
            patch.object(growth_runtime, 'discover_products', side_effect=[[], RuntimeError('temporary search failure')]),
        ):
            with self.assertRaises(planning_runtime.PlanningError):
                growth_runtime.select_viable_topic(topics, {}, cache_only=False)


class RuntimeFailClosedTests(unittest.TestCase):
    @staticmethod
    def _allowing_controller():
        controller = Mock()
        controller.current_state.return_value = {'publish_paused': False, 'target_per_day': 2}
        return controller

    @patch.object(growth_runtime, 'require_clean_current_main', return_value='abc')
    @patch.object(growth_runtime, 'select_viable_topic', return_value=({'slug': 'x', 'category': 'gadget', 'query': 'x', 'title': 'x', 'comparison_axes': [{'id': 'use', 'label': '用途'}]}, [{'asin': f'B0TEST{i:04d}'} for i in range(6)], []))
    @patch.object(growth_runtime, 'select_six', return_value=([{'asin': f'B0TEST{i:04d}'} for i in range(6)], {}))
    @patch.object(growth_runtime, 'build_evidence', return_value=valid_evidence())
    @patch.object(growth_runtime, 'invoke_writer', side_effect=RuntimeError('runtime unavailable'))
    @patch.object(growth_runtime, 'publish_payload')
    def test_writer_failure_prevents_apply(self, publish_stage, *_mocks):
        with self.assertRaises(RuntimeError):
            growth_runtime.run(publish=True, report_path=None, controller=self._allowing_controller())
        publish_stage.assert_not_called()

    def test_review_failure_is_detected_before_publish(self):
        payload = valid_payload()
        payload['lead'] = 'Amazonで実在を確認した商品です。' * 12
        _html, findings, qa = growth_runtime.evaluate_candidate(payload, valid_evidence())
        self.assertTrue(findings)
        self.assertFalse(qa['banned_phrase_pass'])

    def test_qa_failure_is_detected_before_publish(self):
        payload = valid_payload()
        payload['lead'] = '短い本文'
        _html, _findings, qa = growth_runtime.evaluate_candidate(payload, valid_evidence())
        self.assertFalse(qa['overall_pass'])

    def _run_with_gate_result(self, findings, qa):
        topic = {'slug': 'test-widgets-6-picks', 'category': 'gadget', 'query': 'x', 'title': 'x', 'comparison_axes': [{'id': 'use', 'label': '用途'}]}
        products = [{'asin': f'B0TEST{i:04d}'} for i in range(6)]
        with (
            patch.object(growth_runtime, 'require_clean_current_main', return_value='abc'),
            patch.object(growth_runtime, 'select_viable_topic', return_value=(topic, products, [])),
            patch.object(growth_runtime, 'select_six', return_value=(products, {})),
            patch.object(growth_runtime, 'build_evidence', return_value=valid_evidence()),
            patch.object(growth_runtime, 'invoke_writer', return_value=(valid_payload(), {'runtime': 'test'})),
            patch.object(growth_runtime, 'evaluate_candidate', return_value=('html', findings, qa)),
            patch.object(growth_runtime, 'publish_payload') as publish_stage,
        ):
            with self.assertRaises(growth_runtime.GrowthRuntimeError):
                growth_runtime.run(publish=True, report_path=None, controller=self._allowing_controller())
            publish_stage.assert_not_called()

    def test_independent_review_failure_prevents_apply_and_publish(self):
        self._run_with_gate_result(['finding'], {'overall_pass': True})

    def test_machine_qa_failure_prevents_apply_and_publish(self):
        self._run_with_gate_result([], {'overall_pass': False})

    def test_gate_feedback_contains_only_diagnostics_not_generated_prose(self):
        feedback = growth_runtime._gate_feedback([], {
            'main_in_range': False,
            'overall_pass': False,
            'main_len': 3520,
            'lead_len': 188,
            'summary_len': 152,
            'how_to_choose_len': 228,
            'description_lens': [322, 331, 341, 336, 334, 341],
            'h3_lens': [23, 27, 22, 24, 25, 23],
        })
        self.assertEqual(feedback['qa_failures']['main_in_range'], False)
        self.assertEqual(feedback['measurements']['main_len'], 3520)

    def test_publish_command_is_normal_push_only(self):
        source = (ROOT / 'pipeline' / 'growth_runtime.py').read_text(encoding='utf-8')
        self.assertIn("['git', 'push', 'origin', 'main']", source)
        self.assertNotIn('--force', source)

    def test_successful_publish_runs_post_publish_feedback_and_controller(self):
        topic = {'slug': 'test-widgets-6-picks', 'category': 'gadget', 'query': 'x', 'title': 'x', 'comparison_axes': [{'id': 'use', 'label': '用途'}]}
        products = [{'asin': f'B0TEST{i:04d}'} for i in range(6)]
        controller = Mock()
        controller.current_state.return_value = {'publish_paused': False, 'target_per_day': 2}
        controller.evaluate_and_record.return_value = ({'event_id': 'feedback-1'}, {'target_per_day': 2})
        with (
            patch.object(growth_runtime, 'require_clean_current_main', return_value='abc'),
            patch.object(growth_runtime, 'select_viable_topic', return_value=(topic, products, [])),
            patch.object(growth_runtime, 'select_six', return_value=(products, {})),
            patch.object(growth_runtime, 'build_evidence', return_value=valid_evidence()),
            patch.object(growth_runtime, 'invoke_writer', return_value=(valid_payload(), {'runtime': 'test'})),
            patch.object(growth_runtime, 'evaluate_candidate', return_value=('html', [], {'overall_pass': True})),
            patch.object(growth_runtime, '_write_json'),
            patch.object(growth_runtime, 'publish_payload', return_value={'applied': True}),
            patch.object(growth_runtime, '_publish', return_value='published-commit'),
        ):
            result = growth_runtime.run(publish=True, report_path=None, controller=controller)
        self.assertTrue(result['published'])
        self.assertEqual(result['post_publish_feedback']['event_id'], 'feedback-1')
        controller.evaluate_and_record.assert_called_once()


if __name__ == '__main__':
    unittest.main()
