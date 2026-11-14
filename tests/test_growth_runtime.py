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
        def discover(topic, *, cache_only, **kwargs):
            rows = business.discover_products(topic, cache_only=cache_only)
            for row in rows:
                row.setdefault('sources', [{'verified': True, 'excerpt': 'fixture evidence'}])
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

    def test_first_candidate_failure_does_not_switch_to_second(self):
        first, second = self._topic('first-topic'), self._topic('second-topic')
        with (patch.object(business, 'rank_candidates', return_value=[first, second]),
              patch.object(business, 'discover_products', return_value=[]) as discover):
            with self.assertRaises(planning_runtime.PlanningError) as failure:
                business.select_viable_topic([first, second], {}, cache_only=False)
        self.assertEqual([row['slug'] for row in failure.exception.viability_probes], ['first-topic', 'second-topic'])
        self.assertEqual(discover.call_count, 2)

    def test_initial_six_go_directly_to_selection(self):
        topic = self._topic('research-topic')
        products = [{'asin': f'B0TEST{i:04d}', 'amazon_title': f'Model {i}',
                     'image_url': f'https://example.com/{i}.jpg', 'sources': [{'verified':True,'excerpt':'fixture facts'}]}
                    for i in range(6)]
        with (patch.object(business, 'rank_candidates', return_value=[topic]),
              patch.object(business, 'discover_products', return_value=products),
              patch.object(business, 'research_selected', return_value=products) as research,
              patch.object(business, 'select_six', return_value=([], {})) as select):
            business.select_viable_topic([topic], {}, cache_only=False)
        research.assert_not_called()
        select.assert_called_once_with(products, topic, {})

    def test_selector_rejection_ends_this_one_topic(self):
        first, second = self._topic('research-first'), self._topic('research-second')
        rows = [{'asin': f'B0RESE{i:04d}', 'amazon_title': f'Model {i}',
                 'image_url': f'https://example.com/{i}.jpg',
                 'sources': [{'verified':True,'excerpt':'fixture facts'}]} for i in range(6)]
        with (patch.object(business, 'rank_candidates', return_value=[first, second]),
              patch.object(business, 'discover_products', return_value=rows) as discover,
              patch.object(business, 'select_six', side_effect=business.ProductSelectionError('not enough evidence')) as selector):
            with self.assertRaises(planning_runtime.PlanningError) as failure:
                business.select_viable_topic([first, second], {}, cache_only=False)
        self.assertEqual([row['slug'] for row in failure.exception.viability_probes], ['research-first', 'research-second'])
        self.assertEqual(discover.call_count, 2)
        self.assertEqual(selector.call_count, 2)

    def test_missing_sources_are_recorded_and_sent_to_independent_qa(self):
        topic = self._topic('missing-sources')
        usable = lambda i: {'asin': f'B0GOOD{i:04d}', 'sources': [{'verified': True, 'excerpt': 'verified facts'}]}
        initial = [usable(i) for i in range(4)] + [
            {'asin': 'B0BAD00001', 'sources': []}, {'asin': 'B0BAD00002', 'sources': []}]
        replacement = [*initial[:4], usable(4), usable(5)]
        calls = []
        def collect(t, *, cache_only, existing=None, rejected_asins=None, **kwargs):
            calls.append((existing, rejected_asins))
            return (initial if existing is None else replacement), {'complete': True, 'attempts': []}
        with (patch.object(business, 'rank_candidates', return_value=[topic]),
              patch.object(business, 'collect_unique_products', side_effect=collect),
              patch.object(business, 'select_six', side_effect=lambda rows,*args,**kwargs:(rows, {})),
              patch.object(business, 'build_evidence', side_effect=lambda t, rows, selection: {'products': rows}),
              patch.object(business, 'independent_review', return_value={'review': {'status': 'ACCEPTED'}}) as review,
              patch.object(business, 'receipt_issues', return_value=[])):
            chosen, products, probes = business.select_viable_topic([topic], {}, cache_only=False)
        self.assertEqual(chosen['slug'], topic['slug'])
        self.assertEqual(products, initial)
        self.assertEqual(len(calls), 1)
        self.assertEqual(probes[0]['unavailable_source_asins'], ['B0BAD00001', 'B0BAD00002'])
        review.assert_called_once()

    def test_proven_topic_mismatch_replaces_failed_six_before_rechecking(self):
        topic = self._topic('global-qa-retry')
        initial = [{'asin': f'B0TEST{i:04d}', 'sources': [{'verified':True,'excerpt':'facts'}]} for i in range(6)]
        replacement = [{'asin': f'B0NEW{i:05d}', 'sources': [{'verified':True,'excerpt':'facts'}]} for i in range(6)]
        calls = []
        def collect(t, *, cache_only, existing=None, rejected_asins=None, **kwargs):
            calls.append((existing, rejected_asins))
            return (initial if existing is None else replacement), {'complete': True, 'attempts': []}
        with (patch.object(business, 'rank_candidates', return_value=[topic]),
              patch.object(business, 'collect_unique_products', side_effect=collect),
              patch.object(business, 'select_six', side_effect=lambda rows, *args, **kwargs:(rows, {})),
              patch.object(business, 'build_evidence', side_effect=lambda t, rows, selection: {
                  'products': [{'ref': f'p{i+1}', 'asin': p['asin']} for i,p in enumerate(rows)]}),
              patch.object(business, 'independent_review', return_value={'review': {'products': [
                  {'ref': f'p{i+1}', 'topic_fit': False} for i in range(6)]}}) as review,
              patch.object(business, 'receipt_issues', side_effect=[['global QA failure'], []])):
            chosen, products, probes = business.select_viable_topic([topic], {}, cache_only=False)
        self.assertEqual(chosen['slug'], topic['slug'])
        self.assertEqual(products, replacement)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], [])
        self.assertEqual(calls[1][1], {p['asin'] for p in initial})
        self.assertEqual(review.call_count, 2)
        self.assertEqual(probes[0]['qa_research'][0]['replaced'], 6)

    def test_reviewer_rejected_identity_keeps_five_and_replaces_one(self):
        topic = self._topic('alternate-evidence')
        initial = [{'asin': f'B0ALT{i:05d}', 'sources': [{'verified':True,'excerpt':'facts'}]} for i in range(6)]
        new = {'asin': 'B0ALT00006', 'sources': [{'verified':True,'excerpt':'facts'}]}
        calls = []
        def collect(t, *, cache_only, existing=None, rejected_asins=None, **kwargs):
            calls.append((existing, rejected_asins))
            return (initial if existing is None else [*existing,new]), {'complete': True, 'attempts': []}
        reviewed = {'review': {'products': [
            {'ref': f'p{i+1}', 'adequate_facts': i != 5, 'topic_fit': i != 5}
            for i in range(6)]}}
        with (patch.object(business, 'rank_candidates', return_value=[topic]),
              patch.object(business, 'collect_unique_products', side_effect=collect),
              patch.object(business, 'select_six', side_effect=lambda rows,*args,**kwargs:(rows, {})),
              patch.object(business, 'build_evidence', side_effect=lambda t, rows, selection: {
                  'products': [{'ref': f'p{i+1}', 'asin': p['asin']} for i,p in enumerate(rows)]}),
              patch.object(business, 'independent_review', side_effect=[reviewed, {'review': {'status': 'ACCEPTED'}}]) as reviewer,
              patch.object(business, 'receipt_issues', side_effect=[['p6 lacks evidence'], []])):
            selected, products, probes = business.select_viable_topic([topic], {}, cache_only=False)
        self.assertEqual(selected['slug'], topic['slug'])
        self.assertEqual({p['asin'] for p in products}, {p['asin'] for p in initial[:5]} | {new['asin']})
        self.assertEqual(len(calls[1][0]), 5)
        self.assertEqual(calls[1][1], {initial[5]['asin']})
        self.assertEqual(reviewer.call_count, 2)
        self.assertEqual(probes[0]['qa_research'][0]['replaced'], 1)

    def test_selection_rejection_does_not_scan_the_next_topic(self):
        first, second = self._topic('first-topic'), self._topic('second-topic')
        products = [{'asin': f'B0TEST{i:04d}', 'sources': [{'verified': True, 'excerpt': 'facts'}]} for i in range(6)]
        with (patch.object(business, 'rank_candidates', return_value=[first, second]),
              patch.object(business, 'discover_products', return_value=products) as discover,
              patch.object(business, 'select_six', side_effect=business.ProductSelectionError('only 5 documented topic-axis matches')),
              patch.object(business, 'create_job') as create):
            with self.assertRaises(planning_runtime.PlanningError) as failure:
                business.select_viable_topic([first, second], {}, cache_only=False)
        self.assertEqual([p['slug'] for p in failure.exception.viability_probes], ['first-topic', 'second-topic'])
        self.assertIn('only 5 documented topic-axis matches', str(failure.exception.viability_probes[0]['acceptance']))
        self.assertEqual(discover.call_count, 2)
        create.assert_not_called()

    def test_one_run_evaluates_all_seven_novel_topics(self):
        topics = [self._topic(f'topic-{i}') for i in range(7)]
        with (patch.object(business, 'rank_candidates', return_value=topics),
              patch.object(business, 'discover_products', return_value=[]) as discover):
            with self.assertRaises(planning_runtime.PlanningError) as failure:
                business.select_viable_topic(topics, {}, cache_only=False)
        self.assertEqual([p['slug'] for p in failure.exception.viability_probes], [f'topic-{i}' for i in range(7)])
        self.assertEqual(discover.call_count, 7)

    def test_direct_discovery_stops_when_provider_repeats_old_topics(self):
        first_round = [self._topic('round-one-a'), self._topic('round-one-b')]
        with (patch.object(business, 'discover_candidates', return_value=first_round) as planning,
              patch.object(business, 'rank_candidates', return_value=first_round),
              patch.object(business, 'discover_products', return_value=[])):
            with self.assertRaises(planning_runtime.PlanningError):
                business.select_viable_topic(None, {}, cache_only=False, max_rounds=20)
        self.assertEqual(planning.call_count, 2)

    def test_replanning_rejects_duplicate_topic_groups(self):
        topics = [self._topic(f'fresh-{n}') for n in range(5)]
        with (patch.object(business, 'discover_candidates', return_value=topics) as planning,
              patch.object(business, 'rank_candidates', return_value=topics),
              patch.object(business, 'discover_products', return_value=[])):
            with self.assertRaises(planning_runtime.PlanningError) as failure:
                business.select_viable_topic(None, {}, cache_only=False)
        self.assertEqual(planning.call_count, 2)
        self.assertEqual([p['slug'] for p in failure.exception.viability_probes], [f'fresh-{i}' for i in range(5)])

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
        self.assertIn('novel planning candidates', str(failure.exception))


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

    def test_fresh_creation_failure_never_triggers_another_planning(self):
        from role_stage_client import RoleStageFailure
        requested = []
        def role(name, slug='', **kwargs):
            requested.append(name)
            if name == 'scheduler': return {'publish_paused': False, 'target_per_day': 2}
            if name == 'ceo_reconcile': return {}
            if name == 'recovery': return {'recovered_legacy_failed': []}
            if name == 'already_published': return {'slugs': []}
            if name == 'state_queue': return {'eligible': None, 'unfinished': []}
            if name == 'planning': return {'candidates': [{'slug': 'first'}], 'provider': None}
            if name == 'fresh_create':
                raise RoleStageFailure('first topic rejected', details={
                    'viability_probes': [{'slug': 'first', 'candidate_count': 3}]})
            if name == 'product_qa_disposition':
                return {'decision': 'BLOCKED', 'slug': 'first', 'qa': {'status': 'BLOCKED'}}
            raise AssertionError(name)
        with patch.object(growth_runtime, 'require_publishing_enabled'), \
             patch.object(growth_runtime, 'execute_role', side_effect=role):
            result = growth_runtime.run(publish=True, report_path=None)
        self.assertEqual(result['status'], 'qa_rejected_topic')
        self.assertEqual(requested.count('planning'), 1)
        self.assertEqual(requested.count('fresh_create'), 1)
        self.assertEqual(requested.count('product_qa_disposition'), 1)

    def test_deferred_recovery_does_not_admit_new_topic_same_run(self):
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
            result = growth_runtime.run(publish=True, report_path=None)
        self.assertEqual(result['status'], 'recovery_deferred')
        self.assertEqual(requested.count('planning'), 0)

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
            'overall_pass': False,
            'main_len': 3520, 'lead_len': 188, 'summary_len': 152,
            'how_to_choose_len': 228,
            'description_lens': [322, 331, 341, 336, 334, 341],
            'h3_lens': [23, 27, 22, 24, 25, 23],
        })
        self.assertNotIn('main_in_range', feedback['qa_failures'])
        self.assertEqual(feedback['measurements']['main_len'], 3520)

    def test_publish_uses_verified_pr_release_not_direct_main_push(self):
        source = (ROOT / 'pipeline' / 'growth_role_business.py').read_text(encoding='utf-8')
        self.assertIn("from article_release import ArticleReleaseError, release", source)
        self.assertNotIn("['git', 'push', 'origin', 'main']", source)
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
