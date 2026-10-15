import tempfile
from pathlib import Path
from unittest.mock import patch

from chat_publish_approval import make_chat_approval, approval_issues, ChatApprovalError
import growth_recovery
from fixtures import valid_evidence, valid_payload


def test_certified_receipt_is_bound_to_exact_payload_and_evidence():
    payload, evidence = valid_payload(), valid_evidence()
    receipt = make_chat_approval(payload=payload, evidence=evidence, decision='CERTIFIED', publish_allowed=True, reason='full chat review passed')
    assert approval_issues(receipt, payload=payload, evidence=evidence) == []
    changed = dict(payload); changed['lead'] = changed['lead'] + 'x'
    assert 'chat approval stale for payload' in approval_issues(receipt, payload=changed, evidence=evidence)


def test_noncertified_cannot_set_publish_allowed_true():
    try:
        make_chat_approval(payload=valid_payload(), evidence=valid_evidence(), decision='REWORK', publish_allowed=True, reason='needs fixes')
    except ChatApprovalError:
        return
    raise AssertionError('REWORK was incorrectly allowed to publish')


def test_legacy_writer_pass_waits_for_chat_review():
    with patch('operating_profile.normal_profile', return_value=False), tempfile.TemporaryDirectory() as tmp, patch.object(growth_recovery, 'STATE_ROOT', Path(tmp)), patch.object(growth_recovery, 'JOBS_ROOT', Path(tmp)/'jobs'), patch.object(growth_recovery, 'EVENTS_PATH', Path(tmp)/'events.jsonl'), patch.object(growth_recovery, 'EXECUTION_EVENTS_PATH', Path(tmp)/'execution.jsonl'):
        topic={'slug':'x','category':'gadget'}
        growth_recovery.create_job(topic=topic,evidence=valid_evidence(),selection={},planning_provider=None,viability_probes=[])
        growth_recovery.save_writer_result('x', payload=valid_payload(), qa={'overall_pass':True}, findings=[], feedback={}, metadata={}, passed=True)
        state=growth_recovery.load_job('x')
        assert state['current_stage']=='CHAT_REVIEW_PENDING'
        assert growth_recovery.read_artifact('x','chat-publish-approval.json',{})=={}
