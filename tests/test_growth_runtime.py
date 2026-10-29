from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import growth_runtime  # noqa: E402
import growth_role_business as business  # noqa: E402
import growth_recovery  # noqa: E402
import planning_runtime  # noqa: E402
import writer_runtime  # noqa: E402
from fixtures import valid_evidence, valid_payload  # noqa: E402


class ScheduledRouteTests(unittest.TestCase):
    def test_scheduler_uses_new_runtime_not_legacy_generator(self):
        script = (ROOT / 'ops' / 'run-growth.sh').read_text(encoding='utf-8')
        self.assertIn('pipeline/growth_runtime.py --publish --scheduled', script)
        self.assertIn('admit_scheduled()', script)
        self.assertLess(script.index('admit_scheduled()'), script.index('pipeline/growth_runtime.py --publish --scheduled'))
        self.assertIn('SELLEMY_PRECLAIMED_ADMISSION_ID', script)
        self.assertNotIn('pipeline/grow.py', script)
        self.assertNotIn('pipeline/run.py', script)

    def test_analytics_is_not_invoked_by_article_production(self):
        script = (ROOT / 'ops' / 'run-growth.sh').read_text(encoding='utf-8')
        for command in ('ga4_sync.py', 'firefox_collectors.py',
                        'affiliate_pipeline.py', 'actuals_adapter.py',
                        'article_publication_metrics.py'):
            self.assertNotIn(command, script)

    def test_recovery_does_not_starve_scheduled_planning(self):
        script = (ROOT / 'ops' / 'run-growth.sh').read_text(encoding='utf-8')
        self.assertIn('if [[ "$SELLEMY_PREFLIGHT_ALLOWED" != "1" ]]', script)
        self.assertIn('if [[ "$SELLEMY_PREFLIGHT_REASON" == "slot_not_enabled_for_target" ]]', script)
        self.assertIn('if [[ "$RECOVERY_DUE" == "1" ]]', script)
        self.assertIn('pipeline/growth_runtime.py --publish --recovery-only', script)
        self.assertIn('pipeline/growth_runtime.py --publish --scheduled', script)
        self.assertLess(script.index('if [[ "$SELLEMY_PREFLIGHT_ALLOWED" != "1" ]]'), script.index('pipeline/growth_runtime.py --publish --recovery-only'))
        self.assertLess(script.index('pipeline/growth_runtime.py --publish --recovery-only'), script.index('pipeline/growth_runtime.py --publish --scheduled'))

    def test_minute_scheduler_and_algorithmic_24_per_day_cap(self):
        plist = (ROOT / 'ops' / 'com.kickstart.sellemy-growth.plist').read_text(encoding='utf-8')
        self.assertIn('<key>StartInterval</key><integer>60</integer>', plist)
        self.assertNotIn('<key>StartCalendarInterval</key>', plist)
        config = json.loads((ROOT / 'config' / 'adaptive_publish.json').read_text(encoding='utf-8'))
        self.assertEqual(config['maximum_target_per_day'], 48)
        self.assertEqual(config['heartbeat_minute'], 0)
        self.assertEqual(config['slot_anchor_hour'], 0)
        self.assertNotIn('publish_slots_by_target', config)

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
        source = (ROOT / 'pipeline' / 'growth_role_business.py').read_text(encoding='utf-8')
        self.assertNotIn('growth_topics', source)
        self.assertNotIn('def next_topic', source)
        self.assertNotIn('TOPICS =', source)
        self.assertIn('select_viable_topic', source)

    def test_porcelain_parser_preserves_first_modified_path(self):
        output = ' M data/sellemy.db\n?? article/gadget/new.html\n'
        self.assertEqual(
            business._parse_porcelain_paths(output),
            {'data/sellemy.db', 'article/gadget/new.html'},
        )

    def test_compact_search_key_is_bounded(self):
        key = business._compact_search_key({
            'brand': 'Exampleのストアを表示',
            'amazon_title': '【2026新モデル】 加湿器 大容量 9L 超高機能 長時間運転 その他の非常に長い販売文句' * 3,
        })
        self.assertLessEqual(len(key), 48)
        self.assertNotIn('ストアを表示', key)
        self.assertNotIn('【', key)

    def test_publication_unit_includes_catalog_and_top(self):
        paths = business._expected_publish_paths('new', 'beauty')
        self.assertIn('json/products.json', paths)
        self.assertIn('index.html', paths)
        self.assertIn('data/sellemy.db', paths)
        self.assertIn('data/eyecatch-receipts/new.json', paths)


    def test_scheduler_auto_syncs_instead_of_aborting_on_behind_main(self):
        script = (ROOT / 'ops' / 'run-growth.sh').read_text(encoding='utf-8')
        self.assertIn('from publish_gate import sync_clean_main', script)
        self.assertNotIn('HEAD differs from origin/main; abort', script)

    def test_runtime_uses_common_publish_gate(self):
        source = (ROOT / 'pipeline' / 'role_execution_publish.py').read_text(encoding='utf-8')
        self.assertIn('with PublishGate(ROOT).acquire()', source)
        self.assertIn('receipt = ensure_routed_eyecatch', source)
        self.assertIn('commit = _publish', source)
        self.assertIn("if load_job(slug).get('current_stage') != 'PUBLISH_PENDING'", source)

    def test_existing_ga4_measurement_is_preserved(self):
        ga4 = (ROOT / 'js' / 'ga4.js').read_text(encoding='utf-8')
        self.assertIn('G-SZ5RQR5H7L', ga4)
        from renderer import render_article
        rendered = render_article(valid_payload(), valid_evidence())
        self.assertIn('G-SZ5RQR5H7L', rendered)


class ProductViabilityPlanningTests(unittest.TestCase):
    def setUp(self):
        # Candidate ranking/scanning only. Batch discovery, independent facts and
        # source acceptance are covered in their own contract test modules.
        def discover(topic, *, cache_only):
            rows = business.discover_products(topic, cache_only=cache_only)
            return rows, {'complete': len(rows) >= 6, 'attempts': []}
        for item in [patch.object(business, 'collect_unique_products', side_effect=discover),
                     patch.object(business, 'select_six', side_effect=lambda rows, topic, feedback: (rows[:6], {})),
                     patch.object(business, 'build_evidence', side_effect=lambda topic, rows, selection: {'products': rows}),
                     patch.object(business, 'research_selected', side_effect=lambda rows, **kwargs: rows),
                     patch.object(business, 'independent_review', return_value={'review': {'status': 'ACCEPTED'}}),
                     patch.object(business, 'receipt_issues', return_value=[]),
                     patch.object(business, 'product_issues', return_value=[])]:
            item.start(); self.addCleanup(item.stop)

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
            patch.object(business, 'rank_candidates', return_value=[first, second]),
            patch.object(business, 'discover_products', side_effect=[[], products]) as discover,
        ):
            topic, rows, probes = business.select_viable_topic([first, second], {}, cache_only=False)
        self.assertEqual(topic['slug'], 'second-topic')
        self.assertIs(rows, products)
        self.assertEqual(discover.call_count, 2)
        self.assertEqual([p['candidate_count'] for p in probes], [0, 6])

    def test_live_candidate_research_runs_before_selection(self):
        topic = self._topic('research-topic')
        products = [{'asin': f'B0TEST{i:04d}', 'amazon_title': f'Model {i}',
                     'image_url': f'https://example.com/{i}.jpg', 'sources': []}
                    for i in range(6)]
        with (patch.object(business, 'rank_candidates', return_value=[topic]),
              patch.object(business, 'discover_products', return_value=products),
              patch.object(business, 'research_selected', return_value=products) as research,
              patch.object(business, 'select_six', return_value=([], {})) as select):
            business.select_viable_topic([topic], {}, cache_only=False)
        research.assert_called_once_with(products)
        select.assert_called_once_with(products, topic, {})

    def test_source_research_runs_again_for_next_topic_after_first_rejected(self):
        first, second = self._topic('research-first'), self._topic('research-second')
        rows = [{'asin': f'B0RESE{i:04d}', 'amazon_title': f'Model {i}',
                 'image_url': f'https://example.com/{i}.jpg', 'sources': []}
                for i in range(6)]
        with (patch.object(business, 'rank_candidates', return_value=[first, second]),
              patch.object(business, 'discover_products', return_value=rows),
              patch.object(business, 'research_selected', return_value=rows) as research,
              patch.object(business, 'replenish_products', return_value=(rows, [])),
              patch.object(business, 'select_six', side_effect=[
                  business.ProductSelectionError('not enough evidence'), (rows, {})])):
            topic, _, _ = business.select_viable_topic([first, second], {}, cache_only=False)
        self.assertEqual(topic['slug'], 'research-second')
        self.assertEqual(research.call_count, 2)

    def test_official_research_retries_only_after_independent_rejection(self):
        topic = self._topic('evidence-escalation')
        rows = [{'asin': f'B0TEST{i:04d}', 'amazon_title': f'Model {i}',
                 'image_url': f'https://example.com/{i}.jpg',
                 'sources': [{'source_type': 'marketplace_item', 'verified': True,
                              'excerpt': f'Model {i} details'}]} for i in range(6)]
        enriched = [{**p, 'sources': [*p['sources'], {'source_type': 'manufacturer_official',
                     'verified': True, 'excerpt': f'Model {i} official facts'}]}
                    for i, p in enumerate(rows)]
        with (patch.object(business, 'rank_candidates', return_value=[topic]),
              patch.object(business, 'discover_products', return_value=rows),
              patch.object(business, 'research_selected', side_effect=[rows, enriched]) as research,
              patch.object(business, 'select_six', side_effect=[(rows, {}), (enriched, {})]),
              patch.object(business, 'receipt_issues', side_effect=[['facts insufficient'], []]) as check,
              patch.object(business, 'independent_review') as review):
            chosen, candidates, _ = business.select_viable_topic([topic], {}, cache_only=False)
        self.assertEqual(chosen['slug'], 'evidence-escalation')
        self.assertEqual(candidates, enriched)
        self.assertEqual(research.call_count, 2)
        self.assertEqual(research.call_args_list[1].kwargs, {'prefer_official': True})
        self.assertEqual(review.call_count, 2)
        self.assertEqual(check.call_count, 2)

    def test_reviewer_rejected_identity_can_be_replaced_by_verified_alternate(self):
        topic = self._topic('alternate-evidence')
        products = [{'asin': f'B0ALT{i:05d}', 'amazon_title': f'Model {i}',
                     'image_url': f'https://example.com/{i}.jpg',
                     'sources': [{'verified': True, 'excerpt': f'Model {i} genuine facts'}]}
                    for i in range(7)]
        initial, replacement = products[:6], [*products[:5], products[6]]
        rejected_review = {'review': {'products': [
            {'ref': f'p{i+1}', 'adequate_facts': i != 5, 'topic_fit': True}
            for i in range(6)]}}
        accepted_review = {'review': {'status': 'ACCEPTED', 'products': []}}
        with (patch.object(business, 'rank_candidates', return_value=[topic]),
              patch.object(business, 'discover_products', return_value=products),
              patch.object(business, 'research_selected', side_effect=[products, initial]) as research,
              patch.object(business, 'select_six', side_effect=[(initial, {}), (replacement, {})]),
              patch.object(business, 'build_evidence', side_effect=lambda t, rows, selection: {
                  'products': [{'ref': f'p{i+1}', 'asin': p['asin']} for i, p in enumerate(rows)]}),
              patch.object(business, 'independent_review', side_effect=[rejected_review, accepted_review]) as reviewer,
              patch.object(business, 'receipt_issues', side_effect=[['p6 lacks evidence'], []])):
            selected_topic, candidates, _ = business.select_viable_topic([topic], {}, cache_only=False)
        self.assertEqual(selected_topic['slug'], 'alternate-evidence')
        self.assertEqual({p['asin'] for p in candidates}, {p['asin'] for p in products})
        self.assertEqual(research.call_count, 2)
        self.assertEqual(reviewer.call_count, 2)

    def test_axis_rejected_first_topic_scans_valid_second_without_creating_job(self):
        first, second = self._topic('first-topic'), self._topic('second-topic')
        products = [{'asin': f'B0TEST{i:04d}'} for i in range(8)]
        accepted = {'review': {'status': 'ACCEPTED'}}
        with (
            patch.object(business, 'rank_candidates', return_value=[first, second]),
            patch.object(business, 'discover_products', side_effect=[products, products]),
            patch.object(business, 'replenish_products', return_value=(products, [])),
            patch.object(business, 'select_six', side_effect=[
                business.ProductSelectionError('only 5 documented topic-axis matches'),
                (products[:6], {}),
            ]),
            patch.object(business, 'independent_review', return_value=accepted),
            patch.object(business, 'receipt_issues', return_value=[]),
            patch.object(business, 'create_job') as create,
        ):
            topic, rows, probes = business.select_viable_topic([first, second], {}, cache_only=False)
        self.assertEqual(topic['slug'], 'second-topic')
        self.assertIs(rows, products)
        self.assertEqual([probe['slug'] for probe in probes], ['first-topic', 'second-topic'])
        self.assertEqual(probes[0]['candidate_count'], 8)
        self.assertIn('only 5 documented topic-axis matches', str(probes[0]['acceptance']))
        create.assert_not_called()

    def test_scans_beyond_first_six_candidates(self):
        topics = [self._topic(f'topic-{i}') for i in range(7)]
        products = [{'asin': f'B0TEST{i:04d}'} for i in range(6)]
        with (
            patch.object(business, 'rank_candidates', return_value=topics),
            patch.object(business, 'discover_products', side_effect=[[], [], [], [], [], [], products]) as discover,
        ):
            topic, rows, probes = business.select_viable_topic(topics, {}, cache_only=False)
        self.assertEqual(topic['slug'], 'topic-6')
        self.assertIs(rows, products)
        self.assertEqual(discover.call_count, 7)
        self.assertEqual([p['candidate_count'] for p in probes], [0, 0, 0, 0, 0, 0, 6])

    def test_replans_after_full_candidate_set_is_unviable(self):
        first_round = [self._topic('round-one-a'), self._topic('round-one-b')]
        second_round = [self._topic('round-two-a')]
        products = [{'asin': f'B0TEST{i:04d}'} for i in range(6)]
        with (
            patch.object(business, 'discover_candidates', side_effect=[first_round, second_round]) as planning,
            patch.object(business, 'rank_candidates', side_effect=[first_round, second_round]),
            patch.object(business, 'discover_products', side_effect=[[], [], products]),
        ):
            topic, rows, probes = business.select_viable_topic(None, {}, cache_only=False, max_rounds=2)
        self.assertEqual(topic['slug'], 'round-two-a')
        self.assertIs(rows, products)
        self.assertEqual(planning.call_count, 2)
        self.assertEqual([p['round'] for p in probes], [1, 1, 2])

    def test_uncapped_planning_moves_past_three_failed_themes(self):
        batches = [[self._topic(f'fresh-{n}')] for n in range(5)]
        products = [{'asin': f'B0TEST{i:04d}'} for i in range(6)]
        with (
            patch.object(business, 'discover_candidates', side_effect=batches) as planning,
            patch.object(business, 'rank_candidates', side_effect=batches),
            patch.object(business, 'discover_products', side_effect=[[], [], [], [], products]),
        ):
            topic, _, probes = business.select_viable_topic(None, {}, cache_only=False)
        self.assertEqual(topic['slug'], 'fresh-4')
        self.assertEqual(planning.call_count, 5)
        self.assertEqual([p['round'] for p in probes], [1, 2, 3, 4, 5])

    def test_all_unviable_topics_fail_closed(self):
        topics = [self._topic('first-topic'), self._topic('second-topic')]
        with (
            patch.object(business, 'rank_candidates', return_value=topics),
            patch.object(business, 'discover_products', side_effect=[[], RuntimeError('temporary search failure')]),
        ):
            with self.assertRaises(planning_runtime.PlanningError) as failure:
                business.select_viable_topic(topics, {}, cache_only=False)
        self.assertEqual([probe['slug'] for probe in failure.exception.viability_probes],
                         ['first-topic', 'second-topic'])
        self.assertIn('require 6 documented products', str(failure.exception))


class RuntimeFailClosedTests(unittest.TestCase):
    """All public gates are retained across Role subprocess boundaries."""

    def test_recovery_only_never_plans_without_eligible_job(self):
        from test_role_artifact_io_run import test_recovery_only_does_not_admit_new_article
        test_recovery_only_does_not_admit_new_article()

    def test_recovery_only_deferred_reselection_does_not_admit_fresh_article(self):
        from role_stage_client import RoleStageFailure
        deferred = {'slug': 'held-job', 'current_stage': 'PRODUCT_HOLD',
                    'next_retry_at': None, 'status': 'ACTIVE'}
        requested = []
        def role(name, slug='', **kwargs):
            requested.append(name)
            if name == 'scheduler': return {'publish_paused': False, 'target_per_day': 2}
            if name == 'ceo_reconcile': return {}
            if name == 'recovery': return {'recovered_legacy_failed': []}
            if name == 'already_published': return {'slugs': []}
            if name == 'state_queue': return {'eligible': deferred, 'unfinished': [deferred]}
            raise AssertionError(name)
        with patch.object(growth_runtime, 'require_publishing_enabled'), \
             patch.object(growth_runtime, 'execute_role', side_effect=role), \
             patch.object(growth_runtime, 'load_job', return_value=deferred), \
             patch.object(growth_runtime, '_execute_job', side_effect=growth_runtime.GrowthRuntimeError('needs_product_reselection for held-job')):
            result = growth_runtime.run(publish=True, recovery_only=True, report_path=None)
        self.assertEqual(result['status'], 'recovery_deferred')
        self.assertNotIn('planning', requested)

    def test_writer_failure_prevents_apply(self):
        from test_role_artifact_io_run import test_writer_failed_does_not_enter_publish
        test_writer_failed_does_not_enter_publish()

    def test_review_failure_is_detected_before_publish(self):
        payload = valid_payload()
        payload['lead'] = 'Amazonで実在を確認した商品です。' * 12
        _rendered, findings, qa = business.evaluate_candidate(payload, valid_evidence())
        self.assertTrue(findings)
        self.assertFalse(qa['banned_phrase_pass'])

    def test_qa_failure_is_detected_before_publish(self):
        payload = valid_payload()
        payload['lead'] = '短い本文'
        _rendered, _findings, qa = business.evaluate_candidate(payload, valid_evidence())
        self.assertFalse(qa['overall_pass'])

    def test_independent_review_failure_prevents_apply_and_publish(self):
        from test_role_artifact_io_roles import test_reviewer_failed_quality_never_advances_to_ceo_review
        test_reviewer_failed_quality_never_advances_to_ceo_review()

    def test_machine_qa_failure_prevents_apply_and_publish(self):
        from test_role_artifact_io_roles import test_publish_never_mutates_repository_after_acceptance_rejection
        test_publish_never_mutates_repository_after_acceptance_rejection()

    def test_gate_feedback_contains_only_diagnostics_not_generated_prose(self):
        feedback = business._gate_feedback([], {
            'main_in_range': False, 'overall_pass': False,
            'main_len': 3520, 'lead_len': 188, 'summary_len': 152,
            'how_to_choose_len': 228,
            'description_lens': [322, 331, 341, 336, 334, 341],
            'h3_lens': [23, 27, 22, 24, 25, 23],
        })
        self.assertEqual(feedback['qa_failures']['main_in_range'], False)
        self.assertEqual(feedback['measurements']['main_len'], 3520)

    def test_publish_command_is_normal_push_only(self):
        source = (ROOT / 'pipeline' / 'growth_role_business.py').read_text(encoding='utf-8')
        self.assertIn("['git', 'push', 'origin', 'main']", source)
        self.assertNotIn("['git', 'push', '--force'", source)

    def test_remote_push_race_rebuilds_and_retries_inside_gate(self):
        from role_execution_publish import publish
        from growth_role_business import PublishRaceError
        gate = lambda: nullcontext({'head_after': 'base', 'origin_main': 'base', 'clean': True})
        with patch('role_execution_publish._preflight', return_value=({}, {}, {}, 'gadget')), \
             patch('role_execution_publish.load_job', return_value={'current_stage': 'PUBLISH_PENDING'}), \
             patch('role_execution_publish._existing_publish_commit', return_value=None), \
             patch('role_execution_publish._git', return_value='base'), \
             patch('role_execution_publish.PublishGate') as locker, \
             patch('role_execution_publish.materialize_for_publish'), \
             patch('publish_payload.run', return_value={'applied': True}) as apply, \
             patch('role_execution_publish._publish', side_effect=[
                 PublishRaceError('race', candidate_commit='candidate'), 'published-commit']) as push, \
             patch('role_execution_publish.recover_unpublished_commit_after_remote_race', return_value={'already_published': False}) as recovery, \
             patch('role_execution_publish.mark_published'), \
             patch('role_execution_publish.record_execution'):
            locker.return_value.acquire.side_effect = [gate(), gate()]
            result = publish('sample')
        self.assertEqual(result['commit'], 'published-commit')
        self.assertEqual(push.call_count, 2)
        self.assertEqual(apply.call_count, 2)
        recovery.assert_called_once_with(ROOT, 'candidate')

    def test_successful_publish_runs_post_publish_feedback_and_controller(self):
        from test_role_artifact_io_run import test_successful_publishing_dispatches_postpublish
        test_successful_publishing_dispatches_postpublish()


if __name__ == '__main__':
    unittest.main()

class AmazonSearchDiscoveryTests(unittest.TestCase):
    def test_verification_interstitial_is_not_zero_products(self):
        from discovery_adapters import AutositeDiscoveryAdapter
        response = Mock(status_code=200, text='<meta http-equiv="refresh" content="bm-verify=abc">')
        rows = [{'asin': 'B012345678', 'title': 'Item', 'image_url': 'https://example.com/a.jpg'}]
        with (patch('requests.get') as request,
              patch('discovery_adapters._amazon_search_browser', return_value=rows) as browser):
            request.return_value = response
            self.assertEqual(AutositeDiscoveryAdapter().search_amazon_live('USB C', limit=18), rows)
        browser.assert_called_once()

    def test_real_http_search_cards_do_not_open_browser(self):
        from discovery_adapters import AutositeDiscoveryAdapter
        html = '<div data-component-type="s-search-result" data-asin="B012345678"><h2>Item A</h2><img class="s-image" src="https://example.com/a.jpg"></div>'
        with (patch('requests.get', return_value=Mock(status_code=200, text=html)) as request,
              patch('discovery_adapters._amazon_search_browser') as browser):
            rows = AutositeDiscoveryAdapter().search_amazon_live('USB C', limit=18)
        self.assertEqual(len(rows), 1)
        browser.assert_not_called()

    def test_http_503_uses_browser_without_accepting_unverified_facts(self):
        from discovery_adapters import AutositeDiscoveryAdapter
        rows = [{'asin': 'B012345678', 'title': 'Item', 'image_url': 'https://example.com/a.jpg'}]
        with (patch('requests.get', return_value=Mock(status_code=503)) as request,
              patch('discovery_adapters._amazon_search_browser', return_value=rows)):
            self.assertEqual(AutositeDiscoveryAdapter().search_amazon_live('USB C'), rows)
        self.assertNotIn('verified', rows[0])

    def test_amazon_search_html_yields_product_identity_without_autosite_repo(self):
        from discovery_adapters import parse_amazon_search_results
        html = '''<div role="listitem" data-asin="B012345678" data-component-type="s-search-result"><h2><span>商品 A</span></h2><img class="s-image" src="https://example.com/a.jpg"></div><div role="listitem" data-asin="B087654321" data-component-type="s-search-result"><h2><span>商品 B</span></h2><img class="s-image" src="https://example.com/b.jpg"></div>'''
        rows = parse_amazon_search_results(html, limit=8)
        self.assertEqual([row['asin'] for row in rows], ['B012345678', 'B087654321'])
        self.assertEqual(rows[0]['title'], '商品 A')
        self.assertEqual(rows[0]['image_url'], 'https://example.com/a.jpg')

class ProductContentIdentityTests(unittest.TestCase):
    def test_content_dedupe_rejects_same_title_with_different_asin(self):
        rows = [
            {'asin': 'B0AAAA0001', 'amazon_title': '同じ 商品 タイトル', 'image_url': 'https://m.media-amazon.com/images/I/a._AC_UL320_.jpg'},
            {'asin': 'B0BBBB0002', 'amazon_title': '同じ　商品タイトル', 'image_url': 'https://m.media-amazon.com/images/I/b._AC_UL320_.jpg'},
        ]
        kept, removed = business.dedupe_content_products(rows)
        self.assertEqual([x['asin'] for x in kept], ['B0AAAA0001'])
        self.assertEqual(removed[0]['duplicate_of_asin'], 'B0AAAA0001')
        self.assertEqual(removed[0]['reason'], 'normalized_title_exact')

    def test_content_dedupe_rejects_same_canonical_amazon_image(self):
        rows = [
            {'asin': 'B0AAAA0001', 'amazon_title': '商品 A', 'image_url': 'https://m.media-amazon.com/images/I/abc._AC_UL320_.jpg'},
            {'asin': 'B0BBBB0002', 'amazon_title': '商品 B', 'image_url': 'https://m.media-amazon.com/images/I/abc._AC_UL640_.jpg'},
        ]
        kept, removed = business.dedupe_content_products(rows)
        self.assertEqual([x['asin'] for x in kept], ['B0AAAA0001'])
        self.assertEqual(removed[0]['reason'], 'canonical_image_exact')

class EvidenceContentDuplicateGateTests(unittest.TestCase):
    def test_evidence_validation_rejects_content_duplicate_across_different_asins(self):
        evidence = valid_evidence()
        evidence['products'][1]['amazon_title'] = evidence['products'][0]['amazon_title']
        evidence['products'][1]['image_url'] = 'https://example.com/other.jpg'
        from payload_schema import PayloadValidationError, validate_evidence
        with self.assertRaisesRegex(PayloadValidationError, 'content-duplicate title'):
            validate_evidence(evidence)


class DesignerEyecatchRouteTests(unittest.TestCase):
    def test_publish_invokes_designer_after_writer_qa_pass(self):
        from test_role_artifact_io_roles import test_coordinator_separates_designer_and_publish
        test_coordinator_separates_designer_and_publish()

    def test_designer_failure_fails_closed_before_publication(self):
        from role_stage_client import RoleStageFailure
        invoked = []
        def execute(name, slug='', **kwargs):
            invoked.append(name)
            if name == 'inspect': return {'action': 'designer'}
            if name == 'designer': raise RoleStageFailure('Eyecatch generation failed')
            raise AssertionError(f'unexpected stage after designer failure: {name}')
        with patch.object(growth_runtime, 'load_job', return_value={'current_stage': 'EYECATCH_PENDING'}), \
             patch.object(growth_runtime, 'execute_role', side_effect=execute):
            with self.assertRaisesRegex(growth_runtime.GrowthRuntimeError, 'Eyecatch generation failed'):
                growth_runtime._execute_job('sample', {})
        self.assertNotIn('publish', invoked)
