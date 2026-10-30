import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
from comparison_acceptance import *
from quality_fixtures import fixture, accepted_fixture, independent, review
import semantic_review
import growth_recovery as gr
import growth_runtime as runtime
import growth_role_business as business
import role_execution_writer as role_writer
import role_stage_exec as stage_worker
from planning_runtime import PlanningError
import publish_payload as publisher
import publish_gate
import writer_runtime
import responses_provider as rp
from renderer import render_article
from qa import run_qa
from review_gate import run_review_gate
import test_growth_recovery as recovery_tests


class ContractTests(unittest.TestCase):
    def test_good_comparison_with_reasonable_uncertainty_passes_all_gates(self):
        payload, evidence, receipt = accepted_fixture()
        self.assertEqual(acceptance_issues(payload, evidence, receipt), [])
        self.assertEqual(run_review_gate(payload, evidence), [])
        qa = run_qa(render_article(payload, evidence), payload, evidence, acceptance=receipt)
        self.assertTrue(qa['overall_pass'], qa)

    def test_known_bad_fixtures_fail_even_without_hold_words(self):
        for slug, zero_count in [('replacement-bathtub-covers', 3),
                                  ('tabletop-insulated-serving-pots-6-picks', 5),
                                  ('book-reading-stands', 4)]:
            with self.subTest(slug=slug):
                payload, evidence = fixture(slug)
                self.assertEqual(sum(not p['matched_comparison_axes'] for p in evidence['products']), zero_count)
                # Missing axis facts are not a planning veto; independent QA still rejects
                # demonstrably duplicate/unrelated selections.
                self.assertTrue(acceptance_issues(payload, evidence, independent(evidence, payload)))
                payload['conclusion'] = 'ご自宅の使い方に合う候補を選んでください。'
                payload['h1'] = '用途に合わせた商品比較'
                self.assertTrue(acceptance_issues(payload, evidence, independent(evidence, payload)))

    def test_hold_leakage_and_local_uncertainty_are_distinct(self):
        bad = ['比較は六つの独立製品が確認できるまで保留',
               '六つの独立した製品を確認できないため、完成原稿としての採用は保留します。',
               '本記事は比較情報が不足するため公開を見送ります。']
        for text in bad:
            self.assertTrue(article_issues({'h1': text}), text)
        for text in ['耐荷重は確認できないため、重い本を置く前に確認してください。',
                     '寸法が合わない場合は購入を保留し、別のサイズを選びましょう。',
                     '実際の使い心地は本の重さによって異なります。',
                     '耐久性の比較は実測値がないので保留します。',
                     '本記事では耐久性を断定できません。']:
            self.assertEqual(article_issues({'conclusion': text}), [], text)

    def test_missing_stale_or_writer_self_assessment_cannot_authorize(self):
        payload, evidence, receipt = accepted_fixture()
        payload['acceptance'] = receipt
        self.assertTrue(acceptance_issues(payload, evidence, None))
        payload.pop('acceptance')
        payload['products'][0]['description'] += '追加'
        self.assertTrue(receipt_issues(receipt, evidence, payload))
        payload, evidence, receipt = accepted_fixture()
        evidence['products'][0]['amazon_title'] += '変更'
        self.assertTrue(acceptance_issues(payload, evidence, receipt))

    def test_different_asins_do_not_prove_distinct_identities(self):
        payload, evidence, receipt = accepted_fixture()
        receipt['review']['products'][1]['identity_key'] = receipt['review']['products'][0]['identity_key']
        self.assertIn('same independent product identity across listings', receipt_issues(receipt, evidence, payload))

    def test_different_asins_same_verified_manufacturer_series_fail(self):
        payload, evidence, receipt = accepted_fixture()
        for index in (0, 1):
            product = evidence['products'][index]
            product['sources'][0]['identity'] = {'manufacturer': 'SampleMaker', 'series': 'Series100', 'model': f'Model{index}'}
            product['sources'][0]['excerpt'] += f" SampleMaker Series100 Model{index}"
        issues = evidence_issues(evidence)
        self.assertTrue(any('same manufacturer series' in issue for issue in issues), issues)
        self.assertTrue(acceptance_issues(payload, evidence, receipt))

    def test_source_anchors_missing_product_and_unfulfilled_criteria_fail(self):
        for mutate in [lambda r: r['products'].pop(),
                       lambda r: r['products'][0]['axis_support'][0].update(quote='invented fact'),
                       lambda r: r.update(buying_guidance=False),
                       lambda r: r.update(claims_grounded=False),
                       lambda r: r.update(complete_comparison=False),
                       lambda r: r.update(recommendation_conclusion=False),
                       lambda r: r.update(reader_decision_outsourcing_absent=False),
                       lambda r: r.update(internal_qa_leak_absent=False),
                       lambda r: r.update(editorial_voice_appropriate=False)]:
            payload, evidence, receipt = accepted_fixture()
            mutate(receipt['review'])
            self.assertTrue(acceptance_issues(payload, evidence, receipt))

    def test_malformed_receipts_fail_closed(self):
        p, e, a = accepted_fixture()
        for value in [None, {}, True, [], {'review': 'pass'}, {**a, 'review': {'products': [None]*6}}]:
            self.assertTrue(receipt_issues(value, e, p))

    def test_independent_review_is_separate_request_and_rejects_generic_copy(self):
        p, e, _ = accepted_fixture()
        verdict = review(e)
        verdict.update(status='REVISION_REQUIRED', buying_guidance=False,
                       meaningful_comparison=False, reason='Generic claims do not compare the six source specifications.')
        with patch.object(semantic_review, 'generate', return_value=(verdict, {'model': 'mock'})) as call:
            receipt = semantic_review.independent_review(e, p)
        self.assertTrue(receipt_issues(receipt, e, p))
        self.assertIn('INDEPENDENT semantic reviewer', call.call_args.args[2])
        self.assertNotIn('product_acceptance', call.call_args.args[2])
        self.assertEqual(call.call_args.args[0], 'writer')

    def test_inconsistent_reviewer_status_cannot_escalate_missing_facts(self):
        p, e, _ = accepted_fixture()
        verdict = review(e)
        verdict['status'] = 'REVISION_REQUIRED'
        verdict['products'][0]['adequate_facts'] = False
        with patch.object(semantic_review, 'generate', return_value=(verdict, {})):
            result = semantic_review.independent_review(e, p)
        self.assertEqual(result['review']['status'], 'REVISION_REQUIRED')
        self.assertFalse(result['review']['needs_product_reselection'])

    def test_writer_short_circuits_missing_facts_without_any_provider(self):
        p, e = fixture('book-reading-stands')
        with patch.object(writer_runtime, 'route') as route:
            outcome, meta = writer_runtime.invoke_writer({'slug': p['slug'], 'category': p['category']}, e)
        route.assert_not_called()
        self.assertEqual(outcome['status'], 'BLOCKED')
        self.assertTrue(outcome['needs_product_reselection'])
        self.assertFalse(meta['writer_invoked'])

    def test_astra_output_exhaustion_does_not_fall_back_to_local(self):
        with patch.object(rp, 'require_responses_allowed', return_value={}), \
             patch.dict(os.environ, {'SELLEMY_WRITER_API_WEIGHT':'100', 'SELLEMY_WRITER_CODEX_WEIGHT':'0'}), \
             patch.object(rp, 'generate', side_effect=rp.ResponsesError('invalid', kind='output')) as generate:
            with self.assertRaises(rp.ResponsesError):
                rp.route('writer', {}, '', lambda: self.fail('quality exhaustion cannot invoke local fallback'))
        self.assertEqual(generate.call_count, 2)


class RecoveryCase(unittest.TestCase):
    setUp = recovery_tests.GrowthRecoveryTests.setUp
    tearDown = recovery_tests.GrowthRecoveryTests.tearDown

    def create(self, evidence=None):
        p, e, receipt = accepted_fixture()
        e = evidence or e
        topic = {'slug': e['slug'], 'category': e['category'], 'title': p['h1'],
                 'query': '読書台', 'comparison_axes': e['comparison_axes']}
        gr.create_job(topic=topic, evidence=e, selection={}, planning_provider={}, viability_probes=[])
        return p, e, receipt

class RecoveryContractTests(RecoveryCase):
    def test_missing_facts_use_existing_job_without_writer_retry(self):
        _payload, evidence = fixture('book-reading-stands')
        self.create(evidence)
        with patch('writer_runtime.invoke_writer') as writer:
            with self.assertRaisesRegex(business.GrowthRuntimeError, 'needs_product_reselection'):
                role_writer.writer(evidence['slug'])
        writer.assert_not_called()
        state = gr.load_job(evidence['slug'])
        self.assertEqual(state['current_stage'], 'WRITER_PENDING')
        self.assertEqual(state['retry_count_by_stage']['writer'], 0)
        self.assertEqual(state['retry_count_by_stage']['product'], 1)
        self.assertTrue(gr.read_artifact(evidence['slug'], 'product-blocker.json')['needs_product_reselection'])

    def test_same_slug_reselection_clears_stale_authorizations(self):
        p, e, a = self.create()
        slug=e['slug']
        gr.write_artifact(slug, 'acceptance.json', a)
        gr.write_artifact(slug, 'payload.json', p)
        gr.request_product_reselection(slug, ['missing evidence'])
        gr.replace_product_evidence(slug, e, {})
        self.assertEqual(gr.load_job(slug)['current_stage'], 'WRITER_PENDING')
        self.assertEqual(gr.read_artifact(slug, 'acceptance.json'), {})
        self.assertEqual(gr.read_artifact(slug, 'payload.json'), {})
        self.assertEqual(len(gr.all_jobs()), 1)

    def test_writer_and_reviewer_blocked_route_upstream_without_escalation(self):
        for source in ('writer', 'reviewer'):
            with self.subTest(source=source):
                payload, evidence, acceptance = self.create()
                slug = evidence['slug']
                if source == 'writer':
                    blocked_payload = {**payload, **blocked(['facts missing'])}
                    with patch('writer_runtime.invoke_writer', return_value=(blocked_payload, {})):
                        with self.assertRaisesRegex(business.GrowthRuntimeError, 'needs_product_reselection'):
                            role_writer.writer(slug)
                else:
                    gr.write_artifact(slug, 'payload.json', payload)
                    gr.write_artifact(slug, 'editorial-plan.json', {'outline': 'existing'})
                    acceptance['review'].update(blocked(['facts missing']))
                    with patch('semantic_review.independent_review', return_value=acceptance):
                        with self.assertRaisesRegex(business.GrowthRuntimeError, 'needs_product_reselection'):
                            role_writer.reviewer(slug, {})
                self.assertEqual(gr.load_job(slug)['retry_count_by_stage']['writer'], 0)

    def test_legacy_two_quality_failures_do_not_terminate_before_tenth(self):
        payload, evidence, acceptance = self.create()
        slug = evidence['slug']
        from growth_recovery import mark_failure
        with patch('operating_profile.normal_profile', return_value=True):
            for _ in range(2):
                mark_failure(slug, stage='writer', failure_class='role_quality',
                             failure_code='REVIEW_QA_FAILED', reason='editorial failure',
                             role_return=True, role='writer')
        self.assertEqual(gr.load_job(slug)['status'], 'ACTIVE')
        self.assertEqual(gr.load_job(slug)['current_stage'], 'QA_FAILED')

    def test_review_outage_never_authorizes_publication(self):
        payload, evidence, _acceptance = self.create()
        slug = evidence['slug']
        gr.write_artifact(slug, 'payload.json', payload)
        gr.write_artifact(slug, 'editorial-plan.json', {'outline': 'existing'})
        with patch('semantic_review.independent_review',
                   side_effect=rp.ResponsesError('transport', kind='transport')):
            with self.assertRaisesRegex(business.GrowthRuntimeError, 'independent review unavailable'):
                role_writer.reviewer(slug, {})
        self.assertEqual(gr.load_job(slug)['retry_count_by_stage']['writer'], 0)
        self.assertIsNone(gr.read_artifact(slug, 'acceptance.json'))

class PublishContractTests(unittest.TestCase):
    def test_switch_default_off_missing_invalid_and_env_override(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'config').mkdir()
            path=root/'config/publishing-control.json'
            for text in [None, 'bad', '[]', '{}', '{"enabled":"true"}', '{"enabled":false}']:
                if text is not None: path.write_text(text)
                with self.assertRaises(publish_gate.PublishGateError):
                    publish_gate.require_publishing_enabled(root)
            path.write_text('{"enabled":true}')
            publish_gate.require_publishing_enabled(root)
            with patch.dict(os.environ, {'SELLEMY_PUBLISH_DISABLED':'1'}):
                with self.assertRaises(publish_gate.PublishGateError):
                    publish_gate.require_publishing_enabled(root)

    def test_scheduled_and_manual_stop_before_any_work_when_disabled(self):
        # Runtime production switch is enabled, but the environment kill switch
        # must remain authoritative for scheduled and manual invocations.
        with patch.dict(os.environ, {'SELLEMY_PUBLISH_DISABLED': '1'}):
            for scheduled in [True, False]:
                with patch.object(runtime, 'execute_role') as role:
                    with self.assertRaises(publish_gate.PublishGateError):
                        runtime.run(publish=True, scheduled=scheduled, report_path=None)
                    role.assert_not_called()
            with patch.object(publisher, '_load') as load:
                with self.assertRaises(publish_gate.PublishGateError):
                    publisher.run('anything', apply=True, allow_existing=True)
                load.assert_not_called()

    def test_publish_missing_blocked_stale_acceptance_before_render_or_mutation(self):
        p,e,a=accepted_fixture()
        rejected=copy.deepcopy(a); rejected['review']['status']='BLOCKED'
        stale=copy.deepcopy(a); stale['payload_sha256']='old'
        for receipt in [None, rejected, stale]:
            with patch.object(publisher, 'require_publishing_enabled'), \
                 patch.object(publisher, '_load', return_value=(p,e)), \
                 patch.object(publisher, 'load_receipt', return_value=receipt), \
                 patch.object(publisher, 'render_article') as render, \
                 patch.object(publisher, 'run_qa', return_value={'overall_pass':True}), \
                 patch.object(publisher, '_affiliate_preflight') as network:
                with self.assertRaisesRegex(ValueError, 'acceptance refused'):
                    publisher.run(p['slug'], apply=True, allow_existing=False)
                render.assert_not_called(); network.assert_not_called()

    def test_publish_good_dry_run_passes_all_gates_offline(self):
        p,e,a=accepted_fixture()
        with patch.object(publisher, '_load', return_value=(p,e)), \
             patch.object(publisher, 'load_receipt', return_value=a), \
             patch.object(publisher, 'article_related_features', return_value=[]), \
             patch.object(publisher, '_affiliate_preflight') as network:
            result=publisher.run(p['slug'], apply=False, allow_existing=False)
        network.assert_not_called()
        self.assertFalse(result['applied'])
        self.assertTrue(result['qa']['overall_pass'], result)


class BoundaryRecoveryTests(RecoveryCase):
    def test_initial_weak_selection_is_probe_only_without_semantic_api_or_writer(self):
        p, e = fixture('book-reading-stands')
        topic = {'slug': e['slug'], 'category': e['category'], 'title': p['h1'],
                 'query': '読書台', 'comparison_axes': e['comparison_axes']}
        with patch.object(business, 'rank_candidates', return_value=[topic]), \
             patch.object(business, 'discover_products', return_value=e['products']), \
             patch.object(business, 'collect_unique_products', return_value=(e['products'], {'complete': True, 'attempts': []})), \
             patch.object(business, 'independent_review') as reviewer:
            with self.assertRaisesRegex(PlanningError, 'no planned topic passed product acceptance') as failure:
                business.select_viable_topic([topic], {}, cache_only=True)
        reviewer.assert_not_called()
        self.assertEqual(len(failure.exception.viability_probes), 1)
        self.assertTrue(failure.exception.viability_probes[0]['acceptance'])
        self.assertEqual(gr.all_jobs(), [])

    def test_initial_semantic_rejection_is_not_a_viable_topic(self):
        p, e, a = accepted_fixture()
        topic = {'slug': e['slug'], 'category': e['category'], 'title': p['h1'],
                 'query': '読書台', 'comparison_axes': e['comparison_axes']}
        def reject(evidence, payload=None):
            receipt=independent(evidence, payload)
            receipt['review'].update(status='BLOCKED', needs_product_reselection=True,
                                     reason='Unresolved product variants', distinct_products=False)
            return receipt
        with patch.object(business, 'rank_candidates', return_value=[topic]), \
             patch.object(business, 'discover_products', return_value=e['products']), \
             patch.object(business, 'collect_unique_products', return_value=(e['products'], {'complete': True, 'attempts': []})), \
             patch.object(business, 'independent_review', side_effect=reject):
            with self.assertRaisesRegex(PlanningError, 'no planned topic passed product acceptance') as failure:
                business.select_viable_topic([topic], {}, cache_only=True)
        self.assertTrue(failure.exception.viability_probes[0]['acceptance'])
        self.assertEqual(gr.all_jobs(), [])

    def test_viable_selection_receipt_survives_final_evidence_build(self):
        p, e, a = accepted_fixture()
        topic = {'slug': e['slug'], 'category': e['category'], 'title': p['h1'],
                 'query': '読書台', 'comparison_axes': e['comparison_axes']}
        with patch.object(business, 'rank_candidates', return_value=[topic]), \
             patch.object(business, 'discover_products', return_value=e['products']), \
             patch.object(business, 'collect_unique_products', return_value=(e['products'], {'complete': True, 'attempts': []})), \
             patch.object(business, 'select_six',return_value=(e['products'], {})), \
             patch.object(business, 'receipt_issues',return_value=[]), \
             patch.object(business, 'independent_review', side_effect=independent):
            chosen, candidates, _ = business.select_viable_topic([topic], {}, cache_only=True)
        selected, selection = candidates[:6], {}
        final = business.build_evidence(chosen, selected, selection)
        self.assertEqual(product_issues(final), [])

    def test_reselection_revokes_even_previously_accepted_article(self):
        p, e, a = self.create()
        gr.write_artifact(e['slug'], 'payload.json', p)
        gr.write_artifact(e['slug'], 'acceptance.json', a)
        gr.request_product_reselection(e['slug'], ['new missing fact'])
        self.assertEqual(gr.read_artifact(e['slug'], 'acceptance.json')['status'], 'BLOCKED')
        with tempfile.TemporaryDirectory() as d, patch.object(publish_gate, 'require_publishing_enabled'):
            with self.assertRaisesRegex(ValueError, 'needs_product_reselection'):
                gr.materialize_for_publish(e['slug'], Path(d))
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_reselection_executes_upstream_and_respects_cache_only(self):
        p, e, a = self.create()
        slug=e['slug']
        gr.request_product_reselection(slug, ['needs new facts'])
        gr.write_artifact(slug, 'acceptance.json', a)
        with patch.object(business, 'discover_products', return_value=e['products']) as discover, \
             patch.object(business, 'collect_unique_products',return_value=(e['products'], {'complete':True, 'attempts':[]})), \
             patch.object(business, 'load_feedback', return_value={}), \
             patch.object(business, 'select_six',return_value=(e['products'], {})), \
             patch.object(business, 'build_evidence',return_value=e), \
             patch.object(business, 'product_issues',return_value=[]), \
             patch.object(business, 'receipt_issues',return_value=[]), \
             patch.object(business, 'independent_review', side_effect=independent):
            business._reselect_products(slug, cache_only=True)
        self.assertTrue(gr.read_artifact(slug, 'evidence.json') is not None)
        self.assertEqual(gr.read_artifact(slug, 'product-blocker.json'), {})
        self.assertEqual(gr.read_artifact(slug, 'acceptance.json'), {})
        self.assertEqual(product_issues(gr.read_artifact(slug, 'evidence.json')), [])

    def test_legacy_publish_job_returns_to_product_reselection(self):
        p, e = fixture('book-reading-stands')
        self.create(e)
        gr.set_stage(e['slug'], 'PUBLISH_PENDING')
        with patch('role_stage_exec.product_issues', create=True, return_value=['insufficient facts']):
            with self.assertRaisesRegex(business.GrowthRuntimeError, 'needs_product_reselection'):
                stage_worker._inspect(e['slug'], cache_only=True)
        self.assertEqual(gr.load_job(e['slug'])['current_stage'], 'WRITER_PENDING')

    def test_stale_article_acceptance_returns_to_qa_before_designer(self):
        p,e,a=self.create()
        gr.write_artifact(e['slug'], 'payload.json', p)
        gr.write_artifact(e['slug'], 'acceptance.json', a)
        p['summary'] += '変更'
        gr.write_artifact(e['slug'], 'payload.json', p)
        gr.set_stage(e['slug'], 'EYECATCH_PENDING')
        with self.assertRaisesRegex(business.GrowthRuntimeError, 'acceptance revalidation'):
            stage_worker._inspect(e['slug'], cache_only=True)
        self.assertEqual(gr.load_job(e['slug'])['current_stage'], 'QA_FAILED')

    def test_materialization_rejects_missing_acceptance_before_any_repo_write(self):
        p,e,a=self.create()
        gr.write_artifact(e['slug'], 'payload.json', p)
        with tempfile.TemporaryDirectory() as d, patch.object(publish_gate, 'require_publishing_enabled'):
            with self.assertRaisesRegex(ValueError, 'acceptance refused'):
                gr.materialize_for_publish(e['slug'], Path(d))
            self.assertEqual(list(Path(d).iterdir()), [])


class AdditionalBoundaryTests(unittest.TestCase):
    def test_qa_mechanical_pass_does_not_hide_missing_semantic_acceptance(self):
        p,e,a=accepted_fixture()
        qa=run_qa(render_article(p,e),p,e)
        self.assertFalse(qa['overall_pass'])
        self.assertFalse(qa['semantic_acceptance_pass'])
        self.assertTrue(all(v for k,v in qa.items() if (k.endswith('_pass') or k.endswith('_in_range'))
                            and k not in {'overall_pass','semantic_acceptance_pass'}))

    def test_no_literal_leakage_but_unsupported_buying_advice_rejected(self):
        p,e,a=accepted_fixture()
        p['products'][0]['description'] = p['products'][0]['description'].replace('250g', '100g')
        self.assertEqual(article_issues(p), [])
        verdict=review(e)
        verdict.update(status='REVISION_REQUIRED', claims_grounded=False,
                       reason='p1 prose claims 100g; source states 250g.')
        with patch.object(semantic_review, 'generate', return_value=(verdict, {})):
            a=semantic_review.independent_review(e,p)
        self.assertFalse(run_qa(render_article(p,e),p,e,acceptance=a)['semantic_acceptance_pass'])

    def test_gate_disabled_precedes_lock_and_git_sync(self):
        with tempfile.TemporaryDirectory() as d, patch.object(publish_gate, 'sync_clean_main') as sync:
            root=Path(d)
            with self.assertRaises(publish_gate.PublishGateError):
                with publish_gate.PublishGate(root, state_dir=root/'state').acquire():
                    self.fail('disabled gate yielded')
            sync.assert_not_called()
            self.assertFalse((root/'state').exists())

    def test_identity_key_cannot_be_asin_or_unanchored_label(self):
        p,e,a=accepted_fixture()
        for key in ['unique-product-one', e['products'][0]['asin'], 'p1']:
            a['review']['products'][0]['identity_key']=key
            self.assertTrue(receipt_issues(a,e,p))

    def test_writer_blocked_response_validates_strict_schema_without_article(self):
        value={**blocked(['missing facts']), 'slug':'x', 'category':'gadget',
               **{k:'' for k in ('h1','lead','summary','how_to_choose','conclusion')},
               'products':[], 'comparison_groups':[]}
        rp.validate_output(value, writer_runtime.WRITER_JSON_SCHEMA)
        from payload_schema import validate_payload, PayloadValidationError
        with self.assertRaises(PayloadValidationError):
            validate_payload(value)



    def test_chat_surface_independent_receipt_is_accepted(self):
        payload, evidence = fixture('good-reading-stands')
        assessment = review(evidence, article=True)
        receipt = make_receipt(evidence, assessment, {'surface': 'chat'}, payload, reviewer='independent_chat_review')
        self.assertEqual(receipt_issues(receipt, evidence, payload), [])

if __name__ == '__main__': unittest.main()

def test_missing_product_spec_sources_do_not_fail_planning_acceptance():
    payload,evidence=fixture()
    for item in evidence['products']:
        item['sources']=[]
    assessed=review(evidence)
    assessed['sufficient_evidence']=False
    for item in assessed['products']:
        item['adequate_facts']=False
    evidence['product_acceptance']=make_receipt(evidence,assessed,{})
    assert product_issues(evidence)==[]
    assert all(not item['sources'] for item in evidence['products'])
