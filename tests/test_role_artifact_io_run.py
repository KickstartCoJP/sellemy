"""Fully isolated production-coordinator acceptance tests; no providers/publication."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
import growth_runtime as runtime
from role_stage_client import RoleStageFailure


def _base(role, slug='', **kwargs):
    if role=='ceo_reconcile': return {}
    if role=='scheduler':
        op=kwargs['request']['operation']
        if op=='current_state': return {'publish_paused':False,'target_per_day':2}
        if op=='admit_scheduled':return {'allowed':True,'slot':'09:00','receipt':{'admission_id':'today::09:00'}}
        if op=='reschedule_after_failure':return {'failed_slot':'09:00','next_slot':'09:30'}
    if role=='recovery':return {'recovered_legacy_failed':[]}
    if role=='already_published':return {'slugs':[]}
    if role=='product_hold_recheck':return {'status':'NO_HOLD_TO_RECHECK'}
    if role=='legacy_admit':return {'admitted':False}
    if role=='state_queue':return {'eligible':None,'unfinished':[]}
    if role=='planning':return {'candidates':[{'slug':'sample','category':'gadget'}],'provider':{}}
    if role=='fresh_create':return {'slug':'sample','base_head':'aabbcc',
                                    'topic':{'slug':'sample','category':'gadget'},
                                    'viability_probes':[]}
    if role=='planning_commit':return {'planning_committed':True}
    raise AssertionError(f'unexpected role {role}')


def test_staging_invokes_planning_product_and_owner_commit_only():
    called=[]
    def role(name,*args,**kwargs):
        called.append(name)
        return _base(name,*args,**kwargs)
    with patch.object(runtime,'execute_role',side_effect=role):
        result=runtime.run(publish=False,report_path=None)
    assert result['status']=='staged'
    assert called==['ceo_reconcile','scheduler','state_queue',
                    'planning','fresh_create','planning_commit']
    assert result['topic']['slug']=='sample'


def test_recovery_only_does_not_admit_new_article():
    called=[]
    def role(name,*args,**kwargs):
        called.append(name)
        return _base(name,*args,**kwargs)
    with patch.object(runtime,'execute_role',side_effect=role), \
         patch.object(runtime,'require_publishing_enabled'):
        result=runtime.run(publish=True,recovery_only=True,report_path=None)
    assert result['status']=='recovery_no_eligible_job'
    assert 'planning' not in called and 'fresh_create' not in called


def test_scheduled_denial_blocks_before_business_production():
    called=[]
    def role(name,*args,**kwargs):
        called.append(name)
        if name=='scheduler' and kwargs['request']['operation']=='admit_scheduled':
            return {'allowed':False,'reason':'slot_not_enabled_for_target'}
        return _base(name,*args,**kwargs)
    with patch.object(runtime,'execute_role',side_effect=role), \
         patch.object(runtime,'require_publishing_enabled'):
        result=runtime.run(publish=True,scheduled=True,report_path=None)
    assert result['status']=='skipped_by_controller'
    assert 'state_queue' not in called and 'fresh_create' not in called


def test_writer_failed_does_not_enter_publish():
    called=[]
    def role(name,*args,**kwargs):
        called.append(name)
        if name=='state_queue':return {'eligible':{'slug':'sample','status':'ACTIVE'},'unfinished':[]}
        if name=='inspect':return {'action':'writer'}
        if name=='review_reset':return {'review_authorizations_revoked':True}
        if name=='writer':raise RoleStageFailure('writer unavailable')
        return _base(name,*args,**kwargs)
    with patch.object(runtime,'execute_role',side_effect=role), \
         patch.object(runtime,'require_publishing_enabled'), \
         patch.object(runtime,'load_job',return_value={'status':'ACTIVE','current_stage':'WRITER_PENDING'}):
        with pytest.raises(runtime.GrowthRuntimeError,match='writer unavailable'):
            runtime.run(publish=True,report_path=None)
    assert 'designer' not in called and 'publish' not in called and 'postpublish' not in called


def test_successful_publishing_dispatches_postpublish():
    called=[]
    def role(name,*args,**kwargs):
        called.append(name)
        if name=='state_queue':return {'eligible':{'slug':'sample','status':'ACTIVE'},'unfinished':[]}
        if name=='inspect':return {'action':'publish'}
        if name=='publish':return {'commit':'abc123'}
        if name=='postpublish':return {'post_publish_feedback':{'event_id':'event-1'}}
        return _base(name,*args,**kwargs)
    with patch.object(runtime,'execute_role',side_effect=role), \
         patch.object(runtime,'require_publishing_enabled'), \
         patch.object(runtime,'load_job',return_value={'status':'ACTIVE','current_stage':'PUBLISH_PENDING'}):
        result=runtime.run(publish=True,report_path=None)
    assert result['status']=='published' and result['commit']=='abc123'
    assert result['post_publish_feedback']['event_id']=='event-1'
    assert called[-3:]==['inspect','publish','postpublish']


def test_runtime_must_reject_in_process_business_aware_controller():
    with patch.object(runtime,'execute_role',return_value={}):
        with pytest.raises(ValueError,match='in-process controller'):
            runtime.run(publish=False,controller=object(),report_path=None)


def test_postpublish_task_bridge_failure_preserves_published_commit():
    called=[]
    def role(name, *args, **kwargs):
        called.append(name)
        if name=='state_queue':return {'eligible':{'slug':'sample','status':'ACTIVE'},'unfinished':[]}
        if name=='inspect':return {'action':'publish'}
        if name=='publish':return {'commit':'published-sha'}
        if name=='postpublish':raise RoleStageFailure('feedback Task bridge timeout')
        return _base(name,*args,**kwargs)
    with patch.object(runtime,'execute_role',side_effect=role), \
         patch.object(runtime,'require_publishing_enabled'), \
         patch.object(runtime,'load_job',return_value={'status':'ACTIVE','current_stage':'PUBLISH_PENDING'}):
        result=runtime.run(publish=True,report_path=None)
    assert result['published'] is True
    assert result['commit']=='published-sha'
    assert result['status']=='published_feedback_failed'
    assert 'feedback Task bridge timeout' in result['postpublish_error']
    assert called.count('publish')==1


@pytest.mark.parametrize('failed_status', ['qa_rejected_topic', 'product_research_hold'])
def test_one_topic_failure_schedules_next_independent_launch(failed_status):
    calls=[]
    def role(name,*args,**kwargs):
        calls.append((name, kwargs.get('request',{})))
        if name=='fresh_create':
            raise RoleStageFailure('selected topic insufficient', details={
                'viability_probes':[{'slug':'sample','error':'product QA unavailable'}]})
        if name=='product_qa_disposition':
            return {'decision': 'BLOCKED' if failed_status=='qa_rejected_topic' else 'HOLD'}
        return _base(name,*args,**kwargs)
    with patch.object(runtime,'execute_role',side_effect=role), \
         patch.object(runtime,'require_publishing_enabled'):
        result=runtime.run(publish=True,scheduled=True,report_path=None)
    assert result['status']==failed_status
    assert result['recovery_schedule']['next_slot']=='09:30'
    assert len([1 for name,_ in calls if name=='planning'])==1
    assert len([1 for name,_ in calls if name=='fresh_create'])==1
    assert any(name=='scheduler' and item.get('operation')=='reschedule_after_failure' for name,item in calls)


def test_single_topic_discovery_schema():
    import planning_runtime
    schema=planning_runtime.PLANNING_SCHEMA['properties']['candidates']
    assert (schema['minItems'],schema['maxItems'])==(1,1)


def test_scheduled_slot_prioritizes_existing_repair_before_new_planning():
    called=[]
    def role(name,*args,**kwargs):
        called.append(name)
        if name=='state_queue':return {'eligible':{'slug':'existing','status':'ACTIVE'},'unfinished':[]}
        if name=='inspect':return {'action':'ceo_review'}
        return _base(name,*args,**kwargs)
    with patch.object(runtime,'execute_role',side_effect=role), \
         patch.object(runtime,'require_publishing_enabled'), \
         patch.object(runtime,'load_job',return_value={'status':'ACTIVE','current_stage':'CHAT_REVIEW_PENDING'}):
        out=runtime.run(publish=True,scheduled=True,report_path=None)
    assert out['status']=='awaiting_chatgpt_final_review'
    assert out['recovery_slug']=='existing'
    assert 'planning' not in called and 'fresh_create' not in called
