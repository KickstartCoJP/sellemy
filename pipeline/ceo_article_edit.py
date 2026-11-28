"""CEO-approved text edits: one exact human-approved revision, direct normal publication route."""
from __future__ import annotations

from comparison_acceptance import (digest, evidence_content, product_issues, human_edit_receipt,
                                   TEXT_EDIT_FIELDS, GROUP_EDIT_FIELDS, PRODUCT_EDIT_FIELDS,
                                   is_human_edit_receipt, require_acceptance)
from growth_recovery import load_job, read_artifact, write_artifact, set_stage
from payload_schema import validate_payload
from chat_publish_approval import make_chat_approval

TEXT_FIELDS = TEXT_EDIT_FIELDS
GROUP_FIELDS = GROUP_EDIT_FIELDS
PRODUCT_FIELDS = PRODUCT_EDIT_FIELDS


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
    marker = {
        'schema': 'sellemy-ceo-edited-approval/v1',
        'original_sha256': payload_sha, 'approved_sha256': digest(revised),
        'evidence_sha256': digest(evidence_content(evidence)),
        'reviewer_role': reviewer_role, 'approved': True,
    }
    approval = _approval(revised, evidence, reviewer_role)
    acceptance = human_edit_receipt(original, revised, evidence, marker, approval)
    write_artifact(slug, f'ceo-original-{payload_sha[:12]}.json', original)
    old_acceptance = read_artifact(slug, 'acceptance.json', {}) or {}
    if old_acceptance:
        write_artifact(slug, f'ceo-original-acceptance-{payload_sha[:12]}.json', old_acceptance)
    write_artifact(slug, 'payload.json', revised)
    write_artifact(slug, 'ceo-edited-approval.json', marker)
    return _finish(slug, marker, approval, acceptance)


def _approval(payload, evidence, role):
    return make_chat_approval(payload=payload, evidence=evidence, decision='CERTIFIED',
                              publish_allowed=True, reason='Exact article text approved by CEO or delegated COO',
                              reviewer_role='coo' if role == 'coo' else None)


def _finish(slug, marker, approval, acceptance):
    # Preserve previous QA artifacts as historical evidence; do not reissue or relabel them.
    write_artifact(slug, 'acceptance.json', acceptance)
    write_artifact(slug, 'chat-publish-approval.json', approval)
    set_stage(slug, 'EYECATCH_PENDING', event='CEO_EDIT_EXACT_VERSION_APPROVED',
              detail={'approved_sha256': marker['approved_sha256'], 'reviewer_role': marker['reviewer_role']})
    return {'ok': True, 'slug': slug, 'approved_sha256': marker['approved_sha256'], 'next': 'EYECATCH_PENDING'}


def _retained_edit(slug, payload, evidence):
    marker = read_artifact(slug, 'ceo-edited-approval.json', {}) or {}
    original_hash = marker.get('original_sha256') or ''
    if (marker.get('schema') != 'sellemy-ceo-edited-approval/v1' or marker.get('approved') is not True
            or marker.get('reviewer_role') not in ('ceo','coo') or len(original_hash) != 64
            or marker.get('approved_sha256') != digest(payload)
            or marker.get('evidence_sha256') != digest(evidence_content(evidence))):
        raise ValueError('retained edited article identity changed')
    original = read_artifact(slug, f'ceo-original-{original_hash[:12]}.json', {}) or {}
    if digest(original) != original_hash:
        raise ValueError('retained original identity changed')
    return original, marker


def resume_approved_edit(slug):
    """Migrate only a still-approved legacy edit; never reopen a CEO-revoked review."""
    state = load_job(slug)
    if state.get('status') != 'ACTIVE' or state.get('current_stage') != 'CEO_EDIT_QA_PENDING':
        raise ValueError('legacy edit is not awaiting publication')
    payload = read_artifact(slug, 'payload.json', {}) or {}
    evidence = read_artifact(slug, 'evidence.json', {}) or {}
    original, marker = _retained_edit(slug, payload, evidence)
    approval = _approval(payload, evidence, marker['reviewer_role'])
    return _finish(slug, marker, approval, human_edit_receipt(original, payload, evidence, marker, approval))


def ensure_edited_acceptance(slug, payload, evidence, acceptance):
    """A reopened edit needs a NEW exact approval, not its revoked marker.
    This is only called in an already approved publication stage.
    """
    from chat_publish_approval import require_chat_publish_approval
    approval = read_artifact(slug, 'chat-publish-approval.json', {}) or {}
    if is_human_edit_receipt(acceptance):
        require_acceptance(payload, evidence, acceptance)
        require_chat_publish_approval(approval, payload=payload, evidence=evidence)
        if acceptance['chat_approval'] != approval:
            raise ValueError('human edit approval receipt mismatch')
        return acceptance
    if acceptance or not read_artifact(slug, 'ceo-edited-approval.json', {}):
        return acceptance
    # Previously cleared QA files after a historical edit/reopen: only a fresh
    # exact chat approval can authorize the retained text, never the old marker.
    require_chat_publish_approval(approval, payload=payload, evidence=evidence)
    original, marker = _retained_edit(slug, payload, evidence)
    marker = {**marker, 'reviewer_role': approval.get('reviewer_role') or 'ceo'}
    acceptance = human_edit_receipt(original, payload, evidence, marker, approval)
    write_artifact(slug, 'acceptance.json', acceptance)
    return acceptance
