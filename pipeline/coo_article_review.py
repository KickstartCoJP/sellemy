"""COO final editorial decision for an exact retained Sellemy article."""
import argparse
import json
import re

from chat_publish_approval import make_chat_approval
from comparison_acceptance import digest, evidence_content, require_acceptance
from growth_recovery import all_jobs, load_job, read_artifact, set_stage, write_artifact
from independent_quality_gate import require_receipts
from qa_certification import require_certification


def pending():
    return [row for row in all_jobs() if row.get('status') == 'ACTIVE'
            and row.get('current_stage') == 'COO_REVIEW_PENDING']


def decide(slug, *, payload_sha, evidence_sha, decision, reason, edited_payload=None):
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug):
        raise ValueError('invalid slug')
    state = load_job(slug)
    if state.get('status') != 'ACTIVE' or state.get('current_stage') != 'COO_REVIEW_PENDING':
        raise ValueError('article is not awaiting COO final review')
    payload = read_artifact(slug, 'payload.json')
    evidence = read_artifact(slug, 'evidence.json')
    if not isinstance(payload, dict) or not isinstance(evidence, dict):
        raise ValueError('article draft or evidence missing')
    if digest(payload) != payload_sha or digest(evidence_content(evidence)) != evidence_sha:
        raise ValueError('reviewed draft or evidence changed')
    if edited_payload is not None:
        if decision != 'CERTIFIED':
            raise ValueError('COO edited article requires certified approval')
        from ceo_article_edit import apply_approved_edit
        # CEO-authorized proxy: no separate CEO click or duplicate review stage.
        # Human-approved prose enters the normal publication route directly.
        return apply_approved_edit(slug, payload_sha, edited_payload, reviewer_role='coo')
    receipt = make_chat_approval(payload=payload, evidence=evidence, decision=decision,
                                 publish_allowed=decision == 'CERTIFIED',
                                 reason=reason, reviewer_role='coo')
    if decision == 'CERTIFIED':
        acceptance = read_artifact(slug, 'acceptance.json')
        require_acceptance(payload, evidence, acceptance)
        require_receipts(payload, evidence, acceptance,
                         read_artifact(slug, 'independent-quality-receipts.json', {}))
        require_certification(read_artifact(slug, 'qa-certification.json', {}),
                              payload, evidence, read_artifact(slug, 'qa.json', {}), acceptance)
    write_artifact(slug, 'chat-publish-approval.json', receipt)
    detail = {'reviewer_role': 'coo', 'decision': decision,
              'payload_sha256': payload_sha, 'evidence_sha256': evidence_sha,
              'reason': receipt['reason']}
    write_artifact(slug, 'coo-review-decision.json', detail)
    if decision == 'REWORK':
        write_artifact(slug, 'gate-feedback.json', {
            **(read_artifact(slug, 'gate-feedback.json', {}) or {}),
            'ceo_rework': receipt['reason'], 'reviewer_role': 'coo'})
    return set_stage(slug, {'CERTIFIED': 'EYECATCH_PENDING', 'REWORK': 'QA_FAILED',
                            'RETIRED': 'STATE_HOLD'}[decision],
                     event='COO_EXACT_VERSION_' + decision, detail=detail)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--slug')
    parser.add_argument('--payload-sha')
    parser.add_argument('--evidence-sha')
    parser.add_argument('--decision', choices=('CERTIFIED', 'REWORK', 'RETIRED'))
    parser.add_argument('--reason')
    parser.add_argument('--edited-json', help='JSON file containing only editable article fields')
    args = parser.parse_args()
    print(json.dumps(decide(args.slug, payload_sha=args.payload_sha,
                            evidence_sha=args.evidence_sha, decision=args.decision,
                            reason=args.reason,
                            edited_payload=json.loads(open(args.edited_json, encoding='utf-8').read()) if args.edited_json else None) if args.slug else pending(), ensure_ascii=False))
