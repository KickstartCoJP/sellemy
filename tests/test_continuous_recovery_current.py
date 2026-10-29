import inspect
from role_execution_writer import writer as _writer_stage
from growth_recovery import mark_failure

def test_writer_has_no_retry_limit():
    assert 'writer retry exhausted' not in inspect.getsource(_writer_stage)

def test_quality_failures_are_not_terminal():
    text = inspect.getsource(mark_failure)
    assert "state['status']='FAILED'" not in text
    assert "state['status'] = 'FAILED'" not in text
    assert 'consecutive_quality_failures' in text

def test_tenth_quality_failure_is_nonblocking(tmp_path, monkeypatch):
    import growth_recovery as gr
    monkeypatch.setattr(gr, 'JOBS_ROOT', tmp_path/'jobs')
    monkeypatch.setattr(gr, 'EVENTS_PATH',tmp_path/'events.jsonl')
    monkeypatch.setattr(gr, 'STATE_ROOT',tmp_path)
    initial={'schema_version':1,'slug':'testcase','status':'ACTIVE','current_stage':'WRITER_PENDING',
             'created_at':gr._iso(),'updated_at':gr._iso(),'next_retry_at':gr._iso()}
    gr._save_state(initial,event='CREATED')
    for n in range(10):
        result=gr.mark_failure('testcase',stage='writer',failure_class='role_quality',
                               failure_code='REVIEW_QA_FAILED',reason='needs revision',role='writer',role_return=True)
    assert result['status']=='ACTIVE'
    assert result['current_stage']=='QA_FAILED'
    assert gr.read_artifact('testcase','ceo-inspection-required.json')['failure_count']==10

def test_legacy_selection_requires_new_planning(tmp_path, monkeypatch):
    import growth_recovery as gr
    monkeypatch.setattr(gr,'JOBS_ROOT',tmp_path/'jobs')
    monkeypatch.setattr(gr,'EVENTS_PATH',tmp_path/'events.jsonl')
    s={'schema_version':1,'slug':'old-case','status':'FAILED','current_stage':'WRITER_FAILED',
       'created_at':gr._iso(),'updated_at':gr._iso(),'next_retry_at':None}
    gr._save_state(s,event='CREATED')
    gr.write_artifact('old-case','topic.json',{'slug':'old-case'})
    assert gr.recover_legacy_failed_jobs()==['old-case']
    assert gr.load_job('old-case')['status']=='DISCARDED'
    assert gr.read_artifact('old-case','planning-restart-required.json')['previous_stage']=='WRITER_FAILED'
