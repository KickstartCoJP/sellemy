from __future__ import annotations

from comparison_acceptance import digest, evidence_content


class ChatApprovalError(ValueError):
    pass


def make_chat_approval(*, payload: dict, evidence: dict, decision: str, publish_allowed: bool,
                       reason: str, reviewer_surface: str = 'chatgpt') -> dict:
    if decision not in {'CERTIFIED', 'REWORK', 'RETIRED'}:
        raise ChatApprovalError(f'invalid decision: {decision}')
    if publish_allowed is not (decision == 'CERTIFIED'):
        raise ChatApprovalError('publish_allowed=true requires CERTIFIED; CERTIFIED requires publish_allowed=true')
    if not str(reason).strip():
        raise ChatApprovalError('review reason is required')
    if reviewer_surface != 'chatgpt':
        raise ChatApprovalError('final publication approval must come from ChatGPT chat surface')
    return {
        'schema': 'sellemy-chat-publish-approval/v1',
        'reviewer_surface': reviewer_surface,
        'decision': decision,
        'publish_allowed': bool(publish_allowed),
        'reason': str(reason).strip(),
        'payload_sha256': digest(payload),
        'evidence_sha256': digest(evidence_content(evidence)),
    }


def approval_issues(receipt, *, payload: dict, evidence: dict) -> list[str]:
    if not isinstance(receipt, dict) or not receipt:
        return ['ChatGPT final review missing']
    issues=[]
    if receipt.get('schema') != 'sellemy-chat-publish-approval/v1': issues.append('chat approval schema invalid')
    if receipt.get('reviewer_surface') != 'chatgpt': issues.append('final reviewer is not ChatGPT chat surface')
    if receipt.get('decision') != 'CERTIFIED': issues.append('article is not CERTIFIED')
    if receipt.get('publish_allowed') is not True: issues.append('publish_allowed is not true')
    if not str(receipt.get('reason') or '').strip(): issues.append('chat review reason missing')
    if receipt.get('payload_sha256') != digest(payload): issues.append('chat approval stale for payload')
    if receipt.get('evidence_sha256') != digest(evidence_content(evidence)): issues.append('chat approval stale for evidence')
    return issues


def require_chat_publish_approval(receipt, *, payload: dict, evidence: dict) -> None:
    issues=approval_issues(receipt, payload=payload, evidence=evidence)
    if issues:
        raise ChatApprovalError('publication refused: ' + '; '.join(issues))
