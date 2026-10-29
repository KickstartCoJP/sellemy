"""Owner-stage tests; never run providers, publish, or touch production state."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))


def test_writer_only_saves_writer_artifacts():
    from role_execution_writer import writer
    topic = {'slug': 'test-item', 'category': 'gadget', 'title': 'test'}
    evidence = {'slug': 'test-item', 'products': []}
    payload = {'status': 'READY', 'summary': '', 'how_to_choose': '', 'products': []}
    def artifact(slug, name, default=None):
        return {'topic.json': topic, 'evidence.json': evidence}.get(name, default)
    with patch('role_execution_writer.read_artifact', side_effect=artifact), \
         patch('role_execution_writer.write_artifact') as save, \
         patch('role_execution_writer.record_execution'), \
         patch('comparison_acceptance.product_issues', return_value=[]), \
         patch('editorial_plan.build_editorial_plan', return_value={'outline': 'a'}), \
         patch('writer_runtime.invoke_writer', return_value=(payload, {'session_id': 's1'})), \
         patch.dict(os.environ, {'SELLEMY_ENABLE_WRITER_LENGTH_REPAIR': '0'}):
        receipt = writer('test-item')
    assert receipt['writer_ready'] is True
    names = [c.args[1] for c in save.call_args_list]
    assert names == ['editorial-plan.json', 'payload.json']
    assert not set(names).intersection({'qa.json', 'acceptance.json', 'qa-certification.json'})


def test_reviewer_saves_qa_not_writer_payload():
    from role_execution_writer import reviewer
    topic = {'slug': 'test-item', 'category': 'gadget'}
    evidence = {'slug': 'test-item', 'products': []}
    payload = {'status': 'READY', 'products': []}
    plan = {'outline': 'a'}
    def artifact(slug, name, default=None):
        return {'topic.json': topic, 'evidence.json': evidence,
                'payload.json': payload, 'editorial-plan.json': plan}.get(name, default)
    with patch('role_execution_writer.read_artifact', side_effect=artifact), \
         patch('role_execution_writer.write_artifact') as save, \
         patch('role_execution_writer.save_writer_result') as completed, \
         patch('role_execution_writer.evaluate_candidate', return_value=('', [], {'overall_pass': True})), \
         patch('role_execution_writer.record_context_quality'), \
         patch('role_execution_writer.record_execution'), \
         patch('recovery_provider_policy.active_policy', return_value={}), \
         patch('semantic_review.independent_review', return_value={'review': {'status': 'ACCEPTED'}}), \
         patch('editorial_plan_review.independent_plan_review', return_value={'pass': True}), \
         patch('independent_quality_gate.issue_receipts', return_value={'pass': True}):
        receipt = reviewer('test-item', {'session_id': 's1'})
    assert receipt['review_passed'] is True
    saved = [c.args[1] for c in save.call_args_list]
    assert 'acceptance.json' in saved and 'independent-quality-receipts.json' in saved
    assert 'payload.json' not in saved
    assert completed.call_args.kwargs['persist_payload'] is False
    assert completed.call_args.kwargs['passed'] is True


def test_coordinator_invokes_writer_then_independent_reviewer():
    from growth_runtime import _execute_job
    actions = iter([{'action': 'writer'}, {'action': 'ceo_review'}])
    called = []
    def role(name, slug='', **kwargs):
        called.append(name)
        if name == 'inspect':
            return next(actions)
        if name == 'review_reset':
            return {'review_authorizations_revoked': True}
        if name == 'writer':
            return {'writer_ready': True, 'metadata': {'session_id': 's1'}}
        if name == 'reviewer':
            assert kwargs['request']['metadata']['session_id'] == 's1'
            return {'review_passed': True}
        raise AssertionError(name)
    with patch('growth_runtime.load_job', return_value={'current_stage': 'WRITER_PENDING'}), \
         patch('growth_runtime.execute_role', side_effect=role):
        result = {}
        commit = _execute_job('test-item', result)
    assert commit is None
    assert result['status'] == 'awaiting_chatgpt_final_review'
    assert called == ['inspect', 'review_reset', 'writer', 'reviewer', 'inspect']


def test_coordinator_separates_designer_and_publish():
    from growth_runtime import _execute_job
    actions = iter([{'action': 'designer'}, {'action': 'publish'}])
    called = []
    def role(name, slug='', **kwargs):
        called.append(name)
        if name == 'inspect':
            return next(actions)
        if name == 'designer':
            return {'eyecatch': {'generation_method': 'test'}}
        if name == 'publish':
            return {'commit': 'deadbeef'}
        raise AssertionError(name)
    with patch('growth_runtime.load_job', return_value={'current_stage': 'EYECATCH_PENDING'}), \
         patch('growth_runtime.execute_role', side_effect=role):
        result = {}
        commit = _execute_job('test-item', result)
    assert commit == 'deadbeef'
    assert called == ['inspect', 'designer', 'inspect', 'publish']


def test_reviewer_failed_quality_never_advances_to_ceo_review():
    from role_execution_writer import reviewer
    from growth_role_business import GrowthRuntimeError
    payload = {'status': 'READY', 'products': []}
    def artifact(slug, name, default=None):
        return {'topic.json': {'slug': 'x'}, 'evidence.json': {'slug': 'x', 'products': []},
                'payload.json': payload, 'editorial-plan.json': {'outline': 'x'}}.get(name, default)
    with patch('role_execution_writer.read_artifact', side_effect=artifact), \
         patch('role_execution_writer.write_artifact'), \
         patch('role_execution_writer.save_writer_result') as save_result, \
         patch('role_execution_writer.mark_failure') as failed, \
         patch('role_execution_writer.record_context_quality'), \
         patch('role_execution_writer.record_execution'), \
         patch('recovery_provider_policy.active_policy', return_value={}), \
         patch('semantic_review.independent_review', return_value={'review': {'status': 'ACCEPTED'}}), \
         patch('role_execution_writer.evaluate_candidate', return_value=('', ['unsupported claim'], {'overall_pass': False})):
        try:
            reviewer('x', {'session_id': 's1'})
        except GrowthRuntimeError as exc:
            assert 'failed' in str(exc)
        else:
            raise AssertionError('QA rejection was not enforced')
    assert save_result.call_args.kwargs['passed'] is False
    assert save_result.call_args.kwargs['persist_payload'] is False
    assert failed.call_args.kwargs['failure_class'] == 'role_quality'


def test_publish_never_mutates_repository_after_acceptance_rejection():
    from role_execution_publish import publish
    with patch('role_execution_publish._preflight', side_effect=ValueError('acceptance rejected')), \
         patch('role_execution_publish._publish') as push, \
         patch('role_execution_publish.materialize_for_publish') as materialize:
        try:
            publish('x')
        except ValueError as exc:
            assert 'acceptance rejected' in str(exc)
        else:
            raise AssertionError('invalid publication was accepted')
    push.assert_not_called()
    materialize.assert_not_called()
