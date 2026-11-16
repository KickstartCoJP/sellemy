"""A technical feedback bridge timeout may recover only with real QA and Task receipt."""
from pathlib import Path
import json
from unittest.mock import patch
import pytest
from adaptive_publish import AdaptivePublishController
from feedback_task_bridge import _run_action

ROOT = Path(__file__).resolve().parents[1]


def _records(tmp_path):
    config=json.loads((ROOT / "config/adaptive_publish.json").read_text())
    config["target_seed_feedback_count"]=0
    config_path=tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    controller = AdaptivePublishController(ROOT,state_dir=tmp_path,config_path=config_path)
    original = {'schema_version':1,'event_id':'a'*64,'slug':'one','commit':'1'*40,
                'overall':'red','issues':['post_publish_machine_qa_failed'],
                'canonical_feedback_required':True,'artifacts':{'html_sha256':'a'}}
    controller.record_feedback(original)
    controller.record_bridge_failure(slug='one',commit='1'*40,
                                     growth_run={'published':True,'commit':'1'*40},
                                     error=TimeoutError('Task OS 30 seconds'))
    assert controller.current_state()['quality_control_fault']=='feedback_task_bridge_failed'
    return controller,original


def _verified():
    return {'schema_version':1,'event_id':'b'*64,'slug':'one','commit':'1'*40,
            'overall':'green','issues':[],'canonical_feedback_required':False,
            'content_quality':{'grade':'green','qa_overall_pass':True,'issues':[]},
            'topic_novelty':{'grade':'green'},
            'artifacts':{'html_sha256':'b'}}


def test_recovery_clears_fault_only_after_verified_qa_and_bridge(tmp_path):
    controller,original=_records(tmp_path)
    before=controller.current_state()['target_per_day']
    with (patch('feedback_task_bridge.sync_feedback_to_task_event',return_value={
            'required':True,'message_id':'FEEDBACK-BU-002-AAAAAAAAAAAAAAAA'}) as bridge,
          patch('adaptive_publish.evaluate_published_artifact',return_value=_verified()) as evaluation):
        receipt=controller.recover_verified_quality_control_fault()
    assert receipt['status']=='VERIFIED_RECOVERED'
    assert controller.current_state()['quality_control_fault'] is None
    assert controller.current_state()['target_per_day']==before
    assert controller.feedback()[-1]['evaluation_basis']=='quality_control_recovery'
    assert controller.feedback()[-1]['control_recovery_receipt']['verified_task_message_id']
    assert bridge.call_args.kwargs['feedback']==original
    evaluation.assert_called_once()


def test_recovery_fails_closed_when_published_article_qa_red(tmp_path):
    controller,_=_records(tmp_path)
    bad={**_verified(),'overall':'red','issues':['post_publish_machine_qa_failed'],
         'content_quality':{'grade':'red','qa_overall_pass':False,'issues':['post_publish_machine_qa_failed']}}
    with (patch('feedback_task_bridge.sync_feedback_to_task_event',return_value={
            'required':True,'message_id':'FEEDBACK-BU-002-AAAAAAAAAAAAAAAA'}),
          patch('adaptive_publish.evaluate_published_artifact',return_value=bad)):
        receipt=controller.recover_verified_quality_control_fault()
    assert receipt['status']=='PUBLISHED_ARTICLE_QA_STILL_FAILED'
    assert controller.current_state()['publish_paused']
    assert controller.feedback()[-1].get('evaluation_basis')!='quality_control_recovery'


def test_recovery_does_not_clear_fault_after_bridge_exception(tmp_path):
    controller,_=_records(tmp_path)
    with (patch('feedback_task_bridge.sync_feedback_to_task_event',side_effect=TimeoutError('OS still down')),
          patch('adaptive_publish.evaluate_published_artifact',side_effect=AssertionError('no QA before task delivery'))):
        with pytest.raises(TimeoutError):
            controller.recover_verified_quality_control_fault()
    assert controller.current_state()['publish_paused']


def test_bridge_subprocess_budget_covers_actual_35_second_task_append(monkeypatch):
    def run(args,**kwargs):
        assert kwargs['timeout']>=90
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0,stdout='{"status":"in_progress"}',stderr='')
    monkeypatch.setattr('feedback_task_bridge.subprocess.run',run)
    assert _run_action(python='python',ai_os_root=ROOT,module='task_append',payload={})['status']=='in_progress'
