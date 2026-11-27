"""CEO-approved text edits: one exact approved revision, independent QA before publication."""
from __future__ import annotations

from comparison_acceptance import digest, evidence_content, product_issues
from growth_recovery import load_job, read_artifact, write_artifact, set_stage
from payload_schema import validate_payload
from chat_publish_approval import make_chat_approval

TEXT_FIELDS = ('h1', 'lead', 'summary', 'how_to_choose', 'conclusion')
GROUP_FIELDS = ('title', 'angle')
PRODUCT_FIELDS = ('h3', 'description')


def apply_approved_edit(slug: str, payload_sha: str, edited: dict, *, reviewer_role: str = 'ceo') -> dict:
    if reviewer_role not in {'ceo', 'coo'}:
        raise ValueError('unauthorized reviewer role')
    state = load_job(slug)
    if state.get('status') != 'ACTIVE' or state.get('current_stage') not in ('COO_REVIEW_PENDING', 'CHAT_REVIEW_PENDING'):
        raise ValueError('article not in a review stage')
    original = read_artifact(slug, 'payload.json')
    evidence = read_artifact(slug, 'evidence.json')
    if not isinstance(original, dict) or not isinstance(evidence, dict) or digest(original) != payload_sha:
        raise ValueError('reviewed article identity changed')
    if product_issues(evidence):
        raise ValueError('product evidence integrity failed')
    if not isinstance(edited, dict) or not edited:
        raise ValueError('edited article fields required')
    unknown = set(edited) - set(TEXT_FIELDS) - {'comparison_groups', 'products'}
    if unknown:
        raise ValueError(f'unsupported edit fields: {sorted(unknown)}')
    revised = dict(original)
    for key in TEXT_FIELDS:
        if key in edited:
            value = edited[key]
            if not isinstance(value, str) or len(value) > 30000:
                raise ValueError(f'invalid edit: {key}')
            revised[key] = value
    for list_key, allowed in (('comparison_groups', GROUP_FIELDS), ('products', PRODUCT_FIELDS)):
        if list_key not in edited:
            continue
        group_edits = edited[list_key]
        if not isinstance(group_edits, list) or len(group_edits) != len(original[list_key]):
            raise ValueError(f'wrong {list_key} count')
        updated = []
        for previous, changes in zip(original[list_key], group_edits):
            if not isinstance(changes, dict) or set(changes) - set(allowed):
                raise ValueError(f'illegal {list_key} edit')
            for field, value in changes.items():
                if not isinstance(value, str) or len(value) > 30000:
                    raise ValueError(f'invalid {list_key}.{field}')
            updated.append({**previous, **changes})
        revised[list_key] = updated
    validate_payload(revised)
    if digest(revised) == payload_sha:
        raise ValueError('no actual article changes')
    # Keep exact original; never publish a QA certificate for another revision.
    write_artifact(slug, f'ceo-original-{payload_sha[:12]}.json', original)
    write_artifact(slug, 'payload.json', revised)
    write_artifact(slug, 'ceo-edited-approval.json', {
        'schema': 'sellemy-ceo-edited-approval/v1',
        'original_sha256': payload_sha, 'approved_sha256': digest(revised),
        'evidence_sha256': digest(evidence_content(evidence)),
        'reviewer_role': reviewer_role, 'approved': True,
    })
    for path in ('chat-publish-approval.json', 'qa-certification.json',
                 'independent-quality-receipts.json', 'acceptance.json', 'qa.json'):
        write_artifact(slug, path, {})
    set_stage(slug, 'CEO_EDIT_QA_PENDING', event='CEO_EDIT_APPROVED_QA_PENDING',
              detail={'approved_sha256': digest(revised), 'reviewer_role': reviewer_role})
    return {'ok': True, 'pending_independent_qa': True, 'slug': slug, 'approved_sha256': digest(revised)}


def recheck_approved_edit(slug: str) -> dict:
    state = load_job(slug)
    if state.get('current_stage') != 'CEO_EDIT_QA_PENDING' or state.get('status') != 'ACTIVE':
        raise ValueError('CEO approved edit not awaiting QA')
    marker = read_artifact(slug, 'ceo-edited-approval.json', {}) or {}
    payload = read_artifact(slug, 'payload.json', {}) or {}
    evidence = read_artifact(slug, 'evidence.json', {}) or {}
    if (marker.get('schema') != 'sellemy-ceo-edited-approval/v1'
            or marker.get('approved') is not True
            or marker.get('approved_sha256') != digest(payload)
            or marker.get('evidence_sha256') != digest(evidence_content(evidence))):
        raise ValueError('CEO edit approval identity changed')
    try:
        from role_execution_writer import reviewer
        result = reviewer(slug, {'reviewer_role': 'ceo_edited_qa'})
        if result.get('review_passed') is not True:
            raise ValueError('independent QA did not pass')
    except Exception as exc:
        # Do not regenerate CEO-edited text in the Writer retry loop.
        write_artifact(slug, 'ceo-edited-qa-failure.json', {'type': type(exc).__name__, 'reason': str(exc)[:3000]})
        set_stage(slug, 'STATE_HOLD', event='CEO_EDIT_QA_FAILED_HOLD')
        raise
    if digest(read_artifact(slug, 'payload.json', {}) or {}) != marker['approved_sha256']:
        set_stage(slug, 'STATE_HOLD', event='CEO_EDIT_PAYLOAD_CHANGED_HOLD')
        raise ValueError('approved revision mutated during QA')
    from comparison_acceptance import require_acceptance
    from independent_quality_gate import require_receipts
    from qa_certification import require_certification
    acceptance = read_artifact(slug, 'acceptance.json', {}) or {}
    qa = read_artifact(slug, 'qa.json', {}) or {}
    require_acceptance(payload, evidence, acceptance)
    require_receipts(payload, evidence, acceptance, read_artifact(slug, 'independent-quality-receipts.json', {}))
    require_certification(read_artifact(slug, 'qa-certification.json', {}), payload, evidence, qa, acceptance)
    receipt = make_chat_approval(payload=payload, evidence=evidence, decision='CERTIFIED',
                                 publish_allowed=True, reason='CEO or delegated COO approved edited exact article; independent QA passed',
                                 reviewer_role='coo' if marker.get('reviewer_role') == 'coo' else None)
    write_artifact(slug, 'chat-publish-approval.json', receipt)
    set_stage(slug, 'EYECATCH_PENDING', event='CEO_EDIT_INDEPENDENT_QA_PASSED')
    return {'review_passed': True, 'approved_sha256': marker['approved_sha256'], 'next': 'EYECATCH_PENDING'}
