"""Writer and Reviewer processes own their existing stage artifacts."""
from __future__ import annotations
import json
import os
import time
from growth_recovery import (read_artifact, write_artifact, save_writer_result,
                             request_product_reselection, mark_failure, record_execution)
from growth_role_business import (ROOT, GrowthRuntimeError, _runtime_failure,
                                  evaluate_candidate, _gate_feedback)
from codex_provider import record_context_quality


def reset_review_artifacts(slug: str) -> dict:
    """Revoke stale QA and publication approvals before any new writer attempt."""
    write_artifact(slug, 'qa-certification.json', {})
    write_artifact(slug, 'independent-quality-receipts.json', {})
    write_artifact(slug, 'chat-publish-approval.json', {})
    return {'review_authorizations_revoked': True}


def writer(slug: str) -> dict:
    from comparison_acceptance import product_issues
    from editorial_plan import build_editorial_plan
    from writer_runtime import invoke_writer, revise_length_fields, WriterInvocationError
    from responses_provider import ResponsesError
    from renderer import render_article
    from qa import main_text_length

    topic = read_artifact(slug, 'topic.json')
    evidence = read_artifact(slug, 'evidence.json')
    issues = product_issues(evidence)
    if issues:
        request_product_reselection(slug, issues)
        raise GrowthRuntimeError(f'needs_product_reselection for {slug}')
    previous = read_artifact(slug, 'payload.json')
    gate_feedback = read_artifact(slug, 'gate-feedback.json', {}) or {}
    plan = build_editorial_plan(topic, evidence, str(gate_feedback.get('ceo_rework') or ''))
    write_artifact(slug, 'editorial-plan.json', plan)
    started = time.monotonic()
    metadata = {}
    try:
        payload, metadata = invoke_writer(topic, evidence,
            previous_payload=previous if isinstance(previous, dict) else None,
            gate_feedback=gate_feedback if isinstance(gate_feedback, dict) else None)
        record_execution(slug=slug, role='writer', event_type='turn', stage='writer',
                         metadata=metadata,
                         duration_ms=int((time.monotonic() - started) * 1000))
        if payload.get('status') == 'BLOCKED' or payload.get('needs_product_reselection') is True:
            write_artifact(slug, 'writer-blocked.json', payload)
            request_product_reselection(slug, payload.get('reasons') or ['Writer reported missing facts'])
            raise GrowthRuntimeError(f'needs_product_reselection for {slug}')
        if os.environ.get('SELLEMY_ENABLE_WRITER_LENGTH_REPAIR', '1') == '1' and payload.get('status') == 'READY':
            measured_main = main_text_length(render_article(payload, evidence))
            if (measured_main > 3400 or not 130 <= len(payload.get('summary', '')) <= 170
                    or not 190 <= len(payload.get('how_to_choose', '')) <= 270
                    or any(not 200 <= len(p.get('description', '')) <= 400
                           for p in (payload.get('products') or []))):
                payload, repair_meta = revise_length_fields(payload, rendered_main_len=measured_main)
                metadata = {**metadata, 'length_repair_attempted': True,
                            'length_repair_metadata': repair_meta}
        write_artifact(slug, 'payload.json', payload)
        return {'writer_ready': True, 'metadata': metadata}
    except GrowthRuntimeError:
        raise
    except ResponsesError as exc:
        from operating_profile import normal_profile
        infrastructure = normal_profile() and exc.kind in {'transport', 'config'}
        mark_failure(slug, stage='writer', failure_class='runtime_infrastructure' if infrastructure else 'role_quality',
                     failure_code='WRITER_PROVIDER_UNAVAILABLE' if infrastructure else 'ASTRA_QUALITY_EXHAUSTED',
                     reason=str(exc), role_return=not infrastructure, role='writer')
        raise GrowthRuntimeError(f'Writer output quality exhausted for {slug}') from exc
    except Exception as exc:
        failure_class = 'runtime_infrastructure' if _runtime_failure(exc) or isinstance(exc, WriterInvocationError) else 'role_quality'
        role_return = failure_class == 'role_quality'
        mark_failure(slug, stage='writer', failure_class=failure_class,
                     failure_code='WRITER_INVOCATION_OR_OUTPUT_ERROR',
                     reason=f'{type(exc).__name__}: {exc}', role_return=role_return, role='writer')
        record_execution(slug=slug, role='writer',
            event_type='return' if role_return else 'runtime_retry', stage='writer',
            metadata=metadata, failure_class=failure_class,
            duration_ms=int((time.monotonic() - started) * 1000))
        raise GrowthRuntimeError(f'writer stage failed for {slug}: {exc}') from exc


def reviewer(slug: str, metadata: dict) -> dict:
    from comparison_acceptance import product_issues
    from responses_provider import ResponsesError
    from recovery_provider_policy import active_policy, require_local_provider_allowed
    from local_semantic_review import split_local_review
    from semantic_review import independent_review
    from editorial_plan_review import independent_plan_review, EditorialPlanError
    from independent_quality_gate import issue_receipts, QualityGateError

    topic = read_artifact(slug, 'topic.json')
    evidence = read_artifact(slug, 'evidence.json')
    payload = read_artifact(slug, 'payload.json')
    plan = read_artifact(slug, 'editorial-plan.json')
    if not isinstance(payload, dict) or not isinstance(plan, dict):
        raise GrowthRuntimeError(f'reviewer missing writer output for {slug}')
    feedback_prev = read_artifact(slug, 'gate-feedback.json', {}) or {}
    # Old QA/approval must never be reused for a new writer draft.
    write_artifact(slug, 'qa-certification.json', {})
    write_artifact(slug, 'independent-quality-receipts.json', {})
    write_artifact(slug, 'chat-publish-approval.json', {})

    policy = active_policy() or {}
    local_mode = policy.get('qa_provider') == 'ollama_split'
    acceptance = None
    if local_mode:
        require_local_provider_allowed('ollama')
        review = split_local_review(evidence, payload)
        write_artifact(slug, 'local-qa.json', review)
        write_artifact(slug, 'acceptance.json', {})
    else:
        try:
            acceptance = independent_review(evidence, payload, editorial_context={
                'editorial_plan': plan,
                'reader_persona': topic.get('reader_persona'),
                'ceo_rework': feedback_prev.get('ceo_rework'),
            })
        except ResponsesError as exc:
            mark_failure(slug, stage='review', failure_class='runtime_infrastructure',
                         failure_code='INDEPENDENT_REVIEW_UNAVAILABLE',
                         reason=str(exc), role='review')
            raise GrowthRuntimeError(f'independent review unavailable for {slug}') from exc
        write_artifact(slug, 'acceptance.json', acceptance)
        review = acceptance.get('review') or {}
    if review.get('needs_product_reselection') is True or review.get('status') == 'BLOCKED':
        request_product_reselection(slug, [review.get('reason') or 'Review found missing product facts'])
        raise GrowthRuntimeError(f'needs_product_reselection for {slug}')

    try:
        _rendered, findings, qa = evaluate_candidate(
            payload, evidence, acceptance=acceptance,
            require_semantic_acceptance=not local_mode)
    except Exception as exc:
        findings = [f'{type(exc).__name__}: {exc}']
        qa = {'overall_pass': False, 'evaluation_exception': findings[0]}
    feedback = _gate_feedback(findings, qa)
    if feedback_prev.get('ceo_rework'):
        feedback['ceo_rework'] = feedback_prev['ceo_rework']
    feedback['semantic_review'] = review
    if local_mode and review.get('status') == 'REVISION_REQUIRED':
        feedback['local_split_qa_requires_revision'] = True
        qa['overall_pass'] = False
    if not qa.get('overall_pass') and qa.get('evaluation_exception'):
        feedback['evaluation_exception'] = qa['evaluation_exception']
    passed = not findings and bool(qa.get('overall_pass'))
    if passed:
        try:
            plan_receipt = independent_plan_review(plan, payload, evidence=evidence)
            write_artifact(slug, 'editorial-plan-review.json', plan_receipt)
        except EditorialPlanError as exc:
            write_artifact(slug, 'editorial-plan-review.json', exc.receipt)
            feedback['editorial_plan_requirements'] = str(exc)
            save_writer_result(slug, payload=payload, qa={**qa, 'overall_pass': False},
                               findings=findings + [str(exc)], feedback=feedback,
                               metadata=metadata, passed=False, persist_payload=False)
            mark_failure(slug, stage='writer', failure_class='role_quality',
                         failure_code='EDITORIAL_PLAN_NOT_MET', reason=str(exc),
                         role_return=True, role='writer')
            raise GrowthRuntimeError(f'editorial plan review failed for {slug}: {exc}') from exc
        except QualityGateError as exc:
            mark_failure(slug, stage='review', failure_class='runtime_infrastructure',
                         failure_code='EDITORIAL_PLAN_REVIEW_UNAVAILABLE', reason=str(exc), role='review')
            raise GrowthRuntimeError(f'editorial plan reviewer unavailable for {slug}: {exc}') from exc
        try:
            receipts = issue_receipts(payload, evidence, acceptance)
            write_artifact(slug, 'independent-quality-receipts.json', receipts)
        except QualityGateError as exc:
            write_artifact(slug, 'independent-quality-receipts.json', {})
            if exc.gate in ('evidence', 'product'):
                request_product_reselection(slug, exc.issues)
                raise GrowthRuntimeError(f'needs_product_reselection for {slug}: {exc}') from exc
            if exc.gate == 'reviewer_runtime':
                mark_failure(slug, stage='review', failure_class='runtime_infrastructure',
                             failure_code='INDEPENDENT_QA_PROVIDER_FAILED', reason=str(exc), role='review')
                raise GrowthRuntimeError(f'independent QA provider unavailable for {slug}: {exc}') from exc
            feedback['independent_quality_gate'] = {'gate': exc.gate, 'issues': exc.issues}
            save_writer_result(slug, payload=payload, qa={**qa, 'overall_pass': False},
                               findings=findings + [str(exc)], feedback=feedback,
                               metadata=metadata, passed=False, persist_payload=False)
            mark_failure(slug, stage='writer', failure_class='role_quality',
                         failure_code='INDEPENDENT_QUALITY_FAILED', reason=str(exc),
                         role_return=True, role='writer')
            raise GrowthRuntimeError(f'independent quality failed for {slug}: {exc}') from exc
        save_writer_result(slug, payload=payload, qa=qa, findings=findings,
                           feedback=feedback, metadata=metadata,
                           passed=True, persist_payload=False)
        record_context_quality(ROOT, 'bu-codex-sellemy-writer', True,
                               session_id=metadata.get('session_id'))
        record_execution(slug=slug, role='writer', event_type='success',
                         stage='writer', metadata=metadata)
        return {'review_passed': True}
    save_writer_result(slug, payload=payload, qa=qa, findings=findings,
                       feedback=feedback, metadata=metadata,
                       passed=False, persist_payload=False)
    mark_failure(slug, stage='writer', failure_class='role_quality',
                 failure_code='ASTRA_QUALITY_EXHAUSTED' if metadata.get('quality_escalated')
                     else 'REVIEW_QA_FAILED',
                 reason=json.dumps(feedback, ensure_ascii=False),
                 role_return=True, role='writer')
    record_context_quality(ROOT, 'bu-codex-sellemy-writer', False,
                           session_id=metadata.get('session_id'), reason='Review/QA failed')
    record_execution(slug=slug, role='writer', event_type='return',
                     stage='writer', metadata=metadata, failure_class='role_quality')
    raise GrowthRuntimeError(f'writer/review/QA failed for {slug}; retained for retry')
