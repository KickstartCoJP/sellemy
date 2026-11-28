"""Evidence source-policy and final Article QA ownership regressions."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
import role_stage_exec as stages
import growth_recovery as recovery
from growth_role_business import GrowthRuntimeError
from writer_runtime import _prompt
from claim_qa import validate_receipt, make_input


def test_product_hold_recheck_uses_existing_product_qa_pass_not_six_excerpts(tmp_path):
    evidence={'products':[{'ref':f'p{i}','sources':[]} for i in range(1,7)],
              'product_acceptance':{'review':{'status':'ACCEPTED'}}}
    with (patch.object(recovery,'STATE_ROOT',tmp_path),
          patch.object(recovery,'all_jobs',return_value=[{'slug':'approved-listings','status':'ACTIVE','current_stage':'PRODUCT_HOLD'}]),
          patch.object(recovery,'read_artifact',return_value=evidence),
          patch.object(recovery,'write_artifact') as write,
          patch.object(recovery,'set_stage') as advance,
          patch('comparison_acceptance.product_issues',return_value=[])):
        result=stages._recheck_one_product_hold()
    assert result=={'slug':'approved-listings','status':'RESUMED'}
    advance.assert_called_once_with('approved-listings','WRITER_PENDING',event='PRODUCT_HOLD_RECHECK_ACCEPTED')
    write.assert_any_call('approved-listings','product-blocker.json',{})


def test_publish_preflight_does_not_route_any_integrity_failure_to_product():
    artifacts={'evidence.json':{'products':[]},'topic.json':{},'selection.json':{},'product-blocker.json':{}}
    with (patch.object(recovery,'load_job',return_value={'current_stage':'PUBLISH_PENDING','status':'ACTIVE','source':'runtime'}),
          patch.object(recovery,'read_artifact',side_effect=lambda s,n,d=None:artifacts.get(n,d)),
          patch('comparison_acceptance.product_issues',return_value=['old approval invalid']),
          patch.object(recovery,'request_product_reselection') as reselect):
        assert stages._inspect('approved-article',cache_only=False)['action']=='publish'
        reselect.assert_not_called()


def test_writer_prompt_rejects_unproven_performance_without_mandating_six_excerpts():
    prompt=_prompt({'slug':'product'}, {'products':[{'ref':f'p{i}','amazon_title':f'Item {i}','sources':[]}
                                                  for i in range(1,7)],
                                     'comparison_axes':[{'id':'a','label':'A'},{'id':'b','label':'B'}]})
    assert '詳細出典がない場合は性能・耐久性・効能などを断定しない' in prompt
    assert '追加の仕様が不明なだけで記事をBLOCKEDにせず' in prompt


def test_exhausted_product_failed_is_not_eligible_for_scheduled_resume(tmp_path):
    with (patch.object(recovery,'STATE_ROOT',tmp_path),patch.object(recovery,'JOBS_ROOT',tmp_path/'jobs'),
          patch.object(recovery,'EVENTS_PATH',tmp_path/'events.jsonl')):
        recovery.create_job(topic={'slug':'exhausted','category':'gadget'},evidence={'products':[]},
                            selection={},planning_provider={},viability_probes=[])
        recovery.request_product_reselection('exhausted',['unchanged QA issue'])
        final=recovery.request_product_reselection('exhausted',['unchanged QA issue'])
        assert (final['status'],final['current_stage'])==('FAILED','PRODUCT_FAILED')
        assert final['next_retry_at'] is None
        assert final['retry_count_by_stage']['product']==1
        assert recovery.eligible_job() is None


def test_fresh_explicit_product_qa_rejection_is_terminal_without_counter_reset(tmp_path):
    evidence={'products':[{'ref':f'p{i}','sources':[]} for i in range(1,7)],
              'product_acceptance':{'review':{'status':'ACCEPTED'}}}
    rejected={'review':{'status':'BLOCKED','needs_product_reselection':True,
                        'reason':'documented unrelated product'}}
    with (patch.object(recovery,'STATE_ROOT',tmp_path),
          patch.object(recovery,'all_jobs',return_value=[{'slug':'definitely-rejected','status':'ACTIVE','current_stage':'PRODUCT_HOLD'}]),
          patch.object(recovery,'read_artifact',return_value=evidence),
          patch('semantic_review.independent_review',return_value=rejected),
          patch('comparison_acceptance.product_issues',return_value=['unrelated p3']),
          patch.object(recovery,'finalize_rechecked_product_failure') as reject):
        result=stages._recheck_one_product_hold()
    assert result['status']=='FAILED'
    reject.assert_called_once_with('definitely-rejected',['unrelated p3'])
