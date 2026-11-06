"""CEO-approved corrections to BU-002 canonical stage boundaries."""
from unittest.mock import patch
import pytest

import growth_role_business as business
import role_execution_writer as writer_role
from planning_runtime import PlanningError


def topic(slug):
    return {'slug': slug, 'category': 'dailygoods', 'title': slug, 'query': slug,
            'comparison_axes': [{'id': 'use', 'label': '用途', 'keywords': ['use']}]}


def test_next_novel_topic_succeeds_after_first_topic_unviable():
    first, second = topic('first'), topic('second')
    rows = [{'asin': f'B0000000{i}', 'sources': []} for i in range(6)]

    def discovery(current, *, cache_only):
        return ([], {'complete': False, 'attempts': []}) if current['slug'] == 'first' else (
            rows, {'complete': True, 'attempts': []})

    with (patch.object(business, 'rank_candidates', return_value=[first, second]),
          patch.object(business, 'collect_unique_products', side_effect=discovery) as collect,
          patch.object(business, 'select_six', return_value=(rows, {'axes': []})),
          patch.object(business, 'build_evidence', return_value={'products': rows}),
          patch.object(business, 'independent_review', return_value={'review': {'status': 'ACCEPTED'}}),
          patch.object(business, 'receipt_issues', return_value=[])):
        chosen, selected, probes = business.select_viable_topic([first, second], {}, cache_only=True)
    assert chosen['slug'] == 'second'
    assert selected == rows
    assert [entry['slug'] for entry in probes] == ['first', 'second']
    assert collect.call_count == 2


def test_six_products_with_no_excerpts_reach_independent_qa():
    one = topic('sources-missing')
    rows = [{'asin': f'B0000000{i}', 'amazon_title': f'Usable item {i}', 'sources': []} for i in range(6)]
    with (patch.object(business, 'rank_candidates', return_value=[one]),
          patch.object(business, 'collect_unique_products', return_value=(rows, {'complete': True, 'attempts': []})),
          patch.object(business, 'select_six', return_value=(rows, {})),
          patch.object(business, 'build_evidence', return_value={'products': rows}),
          patch.object(business, 'independent_review', return_value={'review': {'status': 'ACCEPTED'}}) as qa,
          patch.object(business, 'receipt_issues', return_value=[])):
        chosen, selected, probes = business.select_viable_topic([one], {}, cache_only=True)
    assert chosen['slug'] == one['slug']
    assert selected == rows
    assert probes[0]['unavailable_source_asins'] == [row['asin'] for row in rows]
    qa.assert_called_once()


@pytest.mark.parametrize('status,reselection,wants_product', [
    ('REVISION_REQUIRED', False, False),
    ('BLOCKED', False, False),
    ('REVISION_REQUIRED', True, False),
    ('BLOCKED', True, True),
])
def test_reviewer_only_sends_explicit_product_block_back_to_product(status, reselection, wants_product):
    artifacts = {'topic.json': {}, 'evidence.json': {}, 'payload.json': {},
                 'editorial-plan.json': {}, 'gate-feedback.json': {}}
    review = {'status': status, 'needs_product_reselection': reselection,
              'reason': 'article prose needs editing'}
    with (patch.object(writer_role, 'read_artifact', side_effect=lambda slug, name, default=None: artifacts.get(name, default)),
          patch.object(writer_role, 'write_artifact'),
          patch('recovery_provider_policy.active_policy', return_value={}),
          patch('semantic_review.independent_review', return_value={'review': review}),
          patch.object(writer_role, 'request_product_reselection') as product_request,
          patch.object(writer_role, 'evaluate_candidate', return_value=('', [], {'overall_pass': False})),
          patch.object(writer_role, 'save_writer_result') as saved,
          patch.object(writer_role, 'mark_failure') as failed,
          patch.object(writer_role, 'record_context_quality'),
          patch.object(writer_role, 'record_execution')):
        with pytest.raises(writer_role.GrowthRuntimeError):
            writer_role.reviewer('article-slug', {})
    if wants_product:
        product_request.assert_called_once()
        saved.assert_not_called()
    else:
        product_request.assert_not_called()
        assert saved.call_args.kwargs['feedback']['semantic_review'] == review
        assert failed.call_args.kwargs['stage'] == 'writer'


def test_repeat_only_topic_group_ends_without_retesting_same_topics():
    topics = [topic(f'subject-{i}') for i in range(3)]
    with (patch.object(business, 'discover_candidates', return_value=topics) as planner,
          patch.object(business, 'rank_candidates', return_value=topics),
          patch.object(business, 'collect_unique_products', return_value=([], {'complete': False, 'attempts': []})) as collect):
        with pytest.raises(PlanningError) as err:
            business.select_viable_topic(None, {}, cache_only=True)
    assert planner.call_count == 2
    assert collect.call_count == 3
    assert len(err.value.viability_probes) == 3


def test_production_can_generate_next_candidate_group_after_supplied_group_fails():
    first, second = topic('supplied-first'), topic('new-provider-group')
    rows = [{'asin': f'B0000000{i}', 'sources': []} for i in range(6)]
    def discovery(t, *, cache_only):
        return ([], {'complete': False, 'attempts': []}) if t['slug'] == first['slug'] else (
            rows, {'complete': True, 'attempts': []})
    with (patch.object(business, 'rank_candidates', side_effect=lambda source, _: source),
          patch.object(business, 'discover_candidates', return_value=[second]) as next_group,
          patch.object(business, 'collect_unique_products', side_effect=discovery),
          patch.object(business, 'select_six', return_value=(rows, {})),
          patch.object(business, 'build_evidence', return_value={'products': rows}),
          patch.object(business, 'independent_review', return_value={'review': {'status': 'ACCEPTED'}}),
          patch.object(business, 'receipt_issues', return_value=[])):
        chosen, _, probes = business.select_viable_topic(
            [first], {}, cache_only=False, regenerate_after_supplied=True)
    assert chosen['slug'] == second['slug']
    assert [p['slug'] for p in probes] == [first['slug'], second['slug']]
    next_group.assert_called_once()
