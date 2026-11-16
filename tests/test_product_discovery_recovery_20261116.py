"""CEO: bounded fresh creation, durable progress and product introduction QA."""
import json
from unittest.mock import patch
from datetime import datetime, timezone
import pytest
import role_stage_exec as stage
import growth_role_business as business


def _topic(slug):
    return {'slug':slug,'category':'gadget', 'comparison_axes':[{'id':str(i),'keywords':['test']} for i in range(3)]}


def test_one_topic_per_fresh_create_and_durable_attempt(tmp_path):
    candidates=[_topic('one'),_topic('two')]
    reports=[]
    def failing(_, *args, **kwargs):
        reports.append((_[0]['slug'],kwargs['regenerate_after_supplied']))
        from planning_runtime import PlanningError
        err=PlanningError('no qualifying products')
        err.viability_probes=[{'slug':'one','candidate_count':0,'discovery':{'attempts':[]}}]
        raise err
    from planning_runtime import PlanningError
    with (patch('growth_recovery.STATE_ROOT', tmp_path),
          patch.object(stage,'_append_jsonl') if hasattr(stage,'_append_jsonl') else patch('growth_recovery._append_jsonl', wraps=__import__('growth_recovery')._append_jsonl),
          patch('growth_role_business.require_clean_current_main',return_value='head'),
          patch('growth_role_business.select_viable_topic',side_effect=failing)):
        with pytest.raises(PlanningError):
            stage._create_job(cache_only=False,candidates=candidates,provider=None,admission=None)
    assert reports==[('one',False)]
    journal=(tmp_path/'fresh-topic-attempts.jsonl').read_text().splitlines()
    assert [json.loads(row)['status'] for row in journal]==['STARTED','FAILED']
    assert json.loads(journal[1])['viability_probes'][0]['slug']=='one'


def test_recently_attempted_topic_excluded_from_following_slot(tmp_path):
    (tmp_path/'fresh-topic-attempts.jsonl').write_text(json.dumps({
        'at':datetime.now(timezone.utc).isoformat(),'slug':'one','status':'STARTED'})+'\n')
    with (patch('growth_recovery.STATE_ROOT',tmp_path),
          patch('planning_runtime.rank_candidates',side_effect=lambda x,*args:x),
          patch('analytics_feedback.load_feedback',return_value={})):
        result=stage._plan(provided=[_topic('one'),_topic('two')],exclude=[])
    assert [x['slug'] for x in result['candidates']]==['two']


def test_product_hold_with_valid_six_product_acceptance_resumes(tmp_path):
    evidence={'products':[{'ref':f'p{i}', 'sources':[{'verified':True,'excerpt':f'Product {i} facts'}]} for i in range(6)],'product_acceptance':{'review':{'status':'ACCEPTED'}}}
    journal=[];written=[];stage_calls=[]
    with (patch('growth_recovery.STATE_ROOT',tmp_path),
          patch('growth_recovery.all_jobs',return_value=[{'slug':'held','status':'ACTIVE','current_stage':'PRODUCT_HOLD'}]),
          patch('growth_recovery.read_artifact',return_value=evidence),
          patch('growth_recovery.write_artifact',side_effect=lambda *a: written.append(a)),
          patch('growth_recovery.set_stage',side_effect=lambda *a,**kw:stage_calls.append((a,kw))),
          patch('comparison_acceptance.product_issues',return_value=[]),
          patch('semantic_review.independent_review',side_effect=AssertionError('valid previous acceptance must stand'))):
        result=stage._recheck_one_product_hold()
    assert result['status']=='RESUMED'
    assert stage_calls[0][0]==('held','WRITER_PENDING')
    assert any(call[1]=='product-blocker.json' for call in written)


def test_unaccepted_product_hold_cannot_resume(tmp_path):
    evidence={'products':[{'ref':f'p{i}', 'sources':[{'verified':True,'excerpt':f'Product {i} facts'}]} for i in range(6)],'product_acceptance':{}}
    with (patch('growth_recovery.STATE_ROOT',tmp_path),
          patch('growth_recovery.all_jobs',return_value=[{'slug':'held','status':'ACTIVE','current_stage':'PRODUCT_HOLD'}]),
          patch('growth_recovery.read_artifact',return_value=evidence),
          patch('comparison_acceptance.product_issues',return_value=['independent acceptance invalid']),
          patch('semantic_review.independent_review',return_value={'review':{'status':'BLOCKED'}}),
          patch('growth_recovery.set_stage',side_effect=AssertionError('must stay held'))):
        result=stage._recheck_one_product_hold()
    assert result['status']=='STILL_HELD'


def test_discovery_search_has_bounded_retries():
    assert business.collect_unique_products.__kwdefaults__['max_searches']==1


def test_viability_implicit_planning_rounds_are_bounded():
    from planning_runtime import PlanningError
    batches=[[_topic('first')],[_topic('second')],[_topic('third')]]
    with (patch.object(business,'discover_candidates',side_effect=batches) as planner,
          patch.object(business,'rank_candidates',side_effect=lambda x,*args:x),
          patch.object(business,'collect_unique_products',return_value=([],{'complete':False,'attempts':[]}))):
        with pytest.raises(PlanningError) as exc:
            business.select_viable_topic(None,{},cache_only=True)
    assert planner.call_count==2
    assert [p['slug'] for p in exc.value.viability_probes]==['first','second']


def test_incomplete_sourcing_can_trigger_product_review_without_automatic_writer(tmp_path):
    evidence={'products':[{'ref':f'p{i}', 'sources':[] if i==0 else [
        {'verified':True,'excerpt':'Documented feature'}]} for i in range(6)],
        'product_acceptance':{}}
    with (patch('growth_recovery.STATE_ROOT',tmp_path),
          patch('growth_recovery.all_jobs',return_value=[{
              'slug':'requires-evidence','status':'ACTIVE','current_stage':'PRODUCT_HOLD'}]),
          patch('growth_recovery.read_artifact',return_value=evidence),
          patch('semantic_review.independent_review',side_effect=AssertionError('insufficient source')),
          patch('growth_recovery.set_stage',side_effect=AssertionError('must remain held'))):
        result=stage._recheck_one_product_hold()
    assert result['status']=='REVIEW_ERROR'
