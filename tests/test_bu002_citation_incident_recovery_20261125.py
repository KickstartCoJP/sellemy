"""Reject actual product defects while fixing quoted product identity format."""
from unittest.mock import patch
from quality_fixtures import accepted_fixture
from comparison_acceptance import product_issues,receipt_issues
from semantic_review import independent_review


def test_repaired_identity_format_is_grounded_and_product_qa_passes():
    _,e,approved=accepted_fixture()
    review=dict(approved['review'])
    checks=[]
    for idx,row in enumerate(review['products'],1):
        checks.append({**row,'identity_key':f'brand {idx} invented phrase',
                       'identity_quote':'','topic_fit':True})
    review.update(products=checks,status='BLOCKED',needs_product_reselection=True,
                  reason='; '.join(f'p{i}: identity has no source anchor' for i in range(1,7)))
    with patch('semantic_review.generate',return_value=(review,{})) as model:
        receipt=independent_review(e)
    assert receipt['review']['status']=='ACCEPTED'
    assert receipt['metadata']['listing_identity_citations_repaired']==[f'p{i}' for i in range(1,7)]
    assert receipt_issues(receipt,e)==[]
    assert model.call_count==2 # initial model plus bounded repair request


def test_semantic_duplicate_veto_is_not_overwritten_by_format_repair():
    _,e,approved=accepted_fixture()
    review={**approved['review'],'status':'BLOCKED','needs_product_reselection':True,
            'distinct_products':False,
            'reason':'same base model among p1/p2; product identity requires investigation'}
    review['products']=[{**x,'identity_key':'invented product'} for x in review['products']]
    with patch('semantic_review.generate',return_value=(review,{})):
        result=independent_review(e)
    assert result['review']['status']=='BLOCKED'
    assert receipt_issues(result,e)


def test_unrelated_product_veto_survives_format_repair():
    _,e,approved=accepted_fixture()
    review={**approved['review'],'status':'BLOCKED','needs_product_reselection':True,
            'reason':'p2 does not fit article topic'}
    review['products']=[{**x,'topic_fit':False} if x['ref']=='p2' else x for x in review['products']]
    with patch('semantic_review.generate',return_value=(review,{})):
        result=independent_review(e)
    assert result['review']['status']=='BLOCKED'
    assert any('topic' in x for x in receipt_issues(result,e))


def test_scoped_reopen_restores_failed_product_job_without_publish(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ops'))
    import recover_product_selection_incidents_20261125 as fix
    import growth_recovery as gr
    from datetime import datetime, timezone
    import json
    slug='autumn-hand-creams-6-picks'
    job=tmp_path/'jobs'/slug
    job.mkdir(parents=True)
    now=datetime.now(timezone.utc).isoformat()
    (job/'state.json').write_text(json.dumps({
        'schema_version':1,'slug':slug,'status':'FAILED','current_stage':'PRODUCT_FAILED',
        'last_failure_code':'UNCHANGED_PRODUCT_SEARCH','created_at':now,'updated_at':now,
        'next_retry_at':None}))
    (job/'topic.json').write_text('{"slug":"autumn-hand-creams-6-picks"}')
    (job/'product-search-progress.json').write_text('{"candidates":[{"asin":"B000000001"}]}')
    (job/'product-attempt-fingerprint.json').write_text('{"signature":"stale"}')
    with patch.object(gr,'JOBS_ROOT',tmp_path/'jobs'), \
         patch.object(gr,'EVENTS_PATH',tmp_path/'events.jsonl'):
        assert 'eligible' in fix.recover(slug)
        assert gr.load_job(slug)['status']=='FAILED'
        assert 'recovered' in fix.recover(slug,dry_run=False)
        updated=gr.load_job(slug)
        assert updated['status']=='ACTIVE'
        assert updated['current_stage']=='WRITER_PENDING'
        assert gr.read_artifact(slug,'product-attempt-fingerprint.json')=={}
        assert gr.read_artifact(slug,'product-attempt-fingerprint-before-fix-20261125.json')=={'signature':'stale'}
        assert 'skip' in fix.recover(slug,dry_run=False)
