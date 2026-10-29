"""Designer and Publish own their existing artifact reads and saves."""
from __future__ import annotations
import time
from pathlib import Path

from growth_recovery import (load_job, read_artifact, save_eyecatch, mark_failure,
                             mark_published, record_execution, materialize_for_publish)
from publish_gate import PublishGate, recover_unpublished_commit_after_remote_race
from growth_role_business import (
    ROOT, GrowthRuntimeError, PublishRaceError, _git,
    _existing_publish_commit, _publish, _cleanup_expected_dirty, _runtime_failure)
from codex_provider import (DESIGNER_SURFACE, context_session_id, record_context_quality)


def _preflight(slug: str) -> tuple[dict, dict, dict, str]:
    from comparison_acceptance import require_acceptance
    from independent_quality_gate import require_receipts
    from qa_certification import require_certification
    from chat_publish_approval import require_chat_publish_approval
    from publish_gate import require_publishing_enabled

    state = load_job(slug)
    category = state['category']
    topic = read_artifact(slug, 'topic.json')
    evidence = read_artifact(slug, 'evidence.json')
    payload = read_artifact(slug, 'payload.json')
    acceptance = read_artifact(slug, 'acceptance.json')
    require_publishing_enabled(ROOT)
    require_acceptance(payload, evidence, acceptance)
    require_receipts(payload, evidence, acceptance,
                     read_artifact(slug, 'independent-quality-receipts.json', {}))
    require_certification(read_artifact(slug, 'qa-certification.json', {}), payload,
                          evidence, read_artifact(slug, 'qa.json', {}), acceptance)
    require_chat_publish_approval(read_artifact(slug, 'chat-publish-approval.json'),
                                  payload=payload, evidence=evidence)
    return topic, evidence, payload, category


def designer(slug: str) -> dict:
    from eyecatch_router import ensure_routed_eyecatch
    topic, evidence, payload, category = _preflight(slug)
    if load_job(slug).get('current_stage') != 'EYECATCH_PENDING':
        raise GrowthRuntimeError('designer cannot execute outside EYECATCH_PENDING')
    started = time.monotonic()
    try:
        with PublishGate(ROOT).acquire():
            receipt = ensure_routed_eyecatch(slug=slug, title=topic['title'],
                                             category=category, evidence=evidence, payload=payload)
            save_eyecatch(slug, receipt=receipt,
                          image_path=ROOT / 'img' / slug / f'{slug}.png')
        if receipt.get('thread_id'):
            record_context_quality(ROOT, DESIGNER_SURFACE, True,
                                   session_id=receipt.get('thread_id'))
        record_execution(slug=slug, role='designer', event_type='turn', stage='eyecatch',
                         metadata={**(receipt.get('token_usage') or {}),
                                   'session_id': receipt.get('thread_id'),
                                   'generation_method': receipt.get('generation_method'),
                                   'provider': receipt.get('provider'),
                                   'model': receipt.get('model'),
                                   'routing': receipt.get('routing')},
                         duration_ms=int((time.monotonic() - started) * 1000))
        record_execution(slug=slug, role='designer', event_type='success', stage='eyecatch')
        return {'eyecatch': receipt}
    except Exception as exc:
        failure_class = 'runtime_infrastructure' if _runtime_failure(exc) else 'role_quality'
        role_return = failure_class == 'role_quality'
        mark_failure(slug, stage='eyecatch', failure_class=failure_class,
                     failure_code='EYECATCH_GENERATION_FAILED',
                     reason=f'{type(exc).__name__}: {exc}',
                     role_return=role_return, role='designer')
        owner = {}
        if load_job(slug).get('current_stage') == 'OWNER_EYECATCH_PENDING':
            from eyecatch_owner_bridge import ensure_owner_request
            try:
                owner['owner_eyecatch_request'] = ensure_owner_request(
                    slug=slug, title=topic['title'], category=category,
                    evidence=evidence, payload=payload)
            except Exception as owner_exc:
                owner['owner_eyecatch_request_error'] = f'{type(owner_exc).__name__}: {owner_exc}'
        if role_return:
            record_context_quality(ROOT, DESIGNER_SURFACE, False,
                                   session_id=context_session_id(DESIGNER_SURFACE), reason=str(exc))
        record_execution(slug=slug, role='designer',
                         event_type='return' if role_return else 'runtime_retry',
                         stage='eyecatch', failure_class=failure_class,
                         duration_ms=int((time.monotonic() - started) * 1000))
        raise GrowthRuntimeError(f'Eyecatch generation failed for {slug}; retained for retry: {exc}') from exc


def publish(slug: str) -> dict:
    from publish_payload import run as publish_payload
    topic, evidence, payload, category = _preflight(slug)
    if load_job(slug).get('current_stage') != 'PUBLISH_PENDING':
        raise GrowthRuntimeError('publish cannot execute before the designer passed')
    result = {}
    try:
        existing = _existing_publish_commit(slug, category)
    except Exception as exc:
        existing = None
        result['existing_publish_verification_error'] = f'{type(exc).__name__}: {exc}'
    if existing:
        mark_published(slug, existing)
        return {**result, 'commit': existing}

    original_head = _git('rev-parse', 'HEAD')
    try:
        for publish_attempt in range(1, 3):
            with PublishGate(ROOT).acquire() as gate_evidence:
                result.setdefault('publish_gate_attempts', []).append({
                    **gate_evidence, 'attempt': publish_attempt})
                materialize_for_publish(slug, ROOT)
                applied = publish_payload(slug, apply=True, allow_existing=False)
                if not applied['applied']:
                    raise GrowthRuntimeError('publication stage refused retained recovery candidate')
                result['applied'] = applied
                try:
                    commit = _publish(slug, category)
                except PublishRaceError as race:
                    recovery = recover_unpublished_commit_after_remote_race(ROOT, race.candidate_commit)
                    result.setdefault('publish_race_recoveries', []).append(recovery)
                    if recovery.get('already_published'):
                        commit = race.candidate_commit
                    elif publish_attempt < 2:
                        continue
                    else:
                        raise GrowthRuntimeError('remote publish race recurred after bounded recovery') from race
                mark_published(slug, commit)
                record_execution(slug=slug, role='publish', event_type='success', stage='publish')
                return {**result, 'commit': commit}
        raise GrowthRuntimeError('publish gate exhausted without a published commit')
    except Exception as exc:
        current = load_job(slug)
        if current.get('current_stage') == 'PUBLISH_PENDING' and current.get('status') != 'PUBLISHED':
            mark_failure(slug, stage='publish', failure_class='runtime_infrastructure',
                         failure_code='PUBLISH_FAILED', reason=f'{type(exc).__name__}: {exc}', role='publish')
            record_execution(slug=slug, role='publish', event_type='runtime_retry',
                             stage='publish', failure_class='runtime_infrastructure')
        _cleanup_expected_dirty(slug, category, original_head)
        raise GrowthRuntimeError(f'publish failed for {slug}; retained for retry: {exc}') from exc
