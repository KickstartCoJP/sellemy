"""Reproduces the previously minute-by-minute identical product-selection failure."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import growth_recovery as recovery
import growth_role_business as business


def test_selector_failure_transitions_out_of_stale_writer_pending(tmp_path):
    root = tmp_path / 'recovery'
    slug = 'selector-schema-failure-test'
    products = [
        {'asin': f'B0SCHEMA{i:03d}', 'amazon_title': f'Product {i}',
         'image_url': f'https://example.com/{i}.png',
         'discovery_axis_id': f'axis_{i // 2}',
         'sources': [{'verified': True, 'excerpt': f'Product {i} details'}]}
        for i in range(6)
    ]
    job = root / 'jobs' / slug
    job.mkdir(parents=True)
    (job / 'state.json').write_text(json.dumps({
        'slug':slug, 'status':'ACTIVE', 'current_stage':'WRITER_PENDING',
        'retry_count_by_stage':{'product':0},
        'created_at':datetime.now(timezone.utc).isoformat(),
        'updated_at':datetime.now(timezone.utc).isoformat()}))
    (job / 'topic.json').write_text(json.dumps({'slug':slug,'comparison_axes':[
        {'id':f'axis_{i}','label':f'Axis {i}'} for i in range(3)]}))
    (job / 'product-search-progress.json').write_text(json.dumps({
        'candidates':products,'researched_asins':[p['asin'] for p in products],
        'reviewed_sets':[],'source_backfill_attempted':[],'queries':[],'excluded_asins':[]}))
    (job / 'evidence.json').write_text('{}')
    with (patch.object(recovery,'STATE_ROOT',root),
          patch.object(recovery,'JOBS_ROOT',root/'jobs'),
          patch.object(recovery,'EVENTS_PATH',root/'events.jsonl'),
          patch.object(business,'load_feedback',return_value={}),
          patch.object(business,'_backfill_missing_amazon_sources',
                       side_effect=lambda pool,attempted:(pool,attempted)),
          patch('product_candidate_registry.is_ng',return_value=False),
          patch('operating_profile.normal_profile',return_value=False),
          patch.object(business,'select_six',side_effect=business.ProductSelectionError(
              'AI product selection failed: KeyError'))):
        with pytest.raises(business.GrowthRuntimeError, match='planning_restart_required'):
            business._reselect_products(slug, cache_only=False)
        first = recovery.load_job(slug)
        assert first['current_stage'] == 'WRITER_PENDING'
        assert first['retry_count_by_stage']['product'] == 1
        assert datetime.fromisoformat(first['next_retry_at']) > datetime.now(timezone.utc)
        with pytest.raises(business.GrowthRuntimeError, match='planning_restart_required'):
            business._reselect_products(slug, cache_only=False)
        second = recovery.load_job(slug)
        assert second['current_stage'] == 'PRODUCT_HOLD'
        assert second['last_failure_code'] == 'UNCHANGED_PRODUCT_SEARCH'
        assert second['retry_count_by_stage']['product'] == 1
        assert recovery.eligible_job(now=datetime.now(timezone.utc)) is None
        events = [json.loads(row) for row in (job/'history.jsonl').read_text().splitlines()]
        assert 'PRODUCT_NO_PROGRESS_HOLD' in [e['event'] for e in events]


def test_six_accepted_products_with_conflicting_set_review_are_held(tmp_path):
    """Exact regression for wireless-lavalier's 7,306 stale-minute retry turns."""
    root = tmp_path / 'recovery'
    slug = 'six-reviewed-but-set-rejected'
    products = [
        {'ref': f'p{i+1}', 'asin': f'B0CONFL{i:04d}',
         'amazon_title': f'Product Model {i}',
         'image_url': f'https://example.com/{i}.png',
         'discovery_axis_id': f'axis_{i // 2}',
         'sources': [{'verified':True,'excerpt':f'Model {i} verified details'}]}
        for i in range(6)
    ]
    job = root / 'jobs' / slug
    job.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    (job / 'state.json').write_text(json.dumps({
        'schema_version':1, 'slug':slug, 'status':'ACTIVE',
        'current_stage':'WRITER_PENDING', 'created_at':now, 'updated_at':now,
        'next_retry_at':now, 'retry_count_by_stage':{'product':0}}))
    (job / 'topic.json').write_text(json.dumps({'slug':slug,'comparison_axes':[
        {'id':f'axis_{i}','label':f'Axis {i}'} for i in range(3)]}))
    (job / 'product-search-progress.json').write_text(json.dumps({
        'candidates':products,'researched_asins':[p['asin'] for p in products],
        'reviewed_sets':[],'source_backfill_attempted':[],'queries':[],'excluded_asins':[]}))
    (job / 'evidence.json').write_text('{}')
    review = {'issues':['independent semantic criteria not satisfied'],
              'asins':[p['asin'] for p in products],
              'evidence': {'products':products},
              'review': {'products':[
                  {'ref':p['ref'], 'identity_key':f'Model {i}',
                   'identity_quote':f'Model {i} verified details',
                   'adequate_facts':True,'topic_fit':True}
                  for i,p in enumerate(products)]}}
    (job / 'product-review-source.json').write_text(json.dumps(review))
    with (patch.object(recovery,'STATE_ROOT',root),
          patch.object(recovery,'JOBS_ROOT',root/'jobs'),
          patch.object(recovery,'EVENTS_PATH',root/'events.jsonl'),
          patch.object(business,'load_feedback',return_value={}),
          patch.object(business,'_backfill_missing_amazon_sources',
                       side_effect=lambda pool,attempted:(pool,attempted)),
          patch('product_candidate_registry.is_ng',return_value=False)):
        with pytest.raises(business.GrowthRuntimeError, match='incompatible product review'):
            business._reselect_products(slug, cache_only=False)
        state = recovery.load_job(slug)
        assert state['current_stage'] == 'PRODUCT_HOLD'
        assert state['last_failure_code'] == 'PRODUCT_REVIEW_CRITERIA_CONFLICT'
        assert recovery.read_artifact(slug,'product-hold.json')['retained_asins'] == sorted(
            p['asin'] for p in products)
        assert recovery.eligible_job(now=datetime.now(timezone.utc)) is None
