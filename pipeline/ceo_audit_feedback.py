"""Project approved CEO post-publish decisions into original published job, no republish."""
from __future__ import annotations
import json
from pathlib import Path

DECISIONS={'承認':'ACCEPTED','否認':'REWORK','差戻し':'REWORK'}

def reconcile_feedback(*, canonical_root: Path, jobs: list[dict], read_artifact, write_artifact) -> list[dict]:
    root=canonical_root/'role-communication'
    responses=[]
    approved={}
    for job in jobs:
        slug=job.get('slug')
        if not slug or job.get('status')!='PUBLISHED' or job.get('operating_profile')!='normal':continue
        audit=read_artifact(slug,'postpublish-ceo-delivered.json',None)
        if audit and audit.get('message_id'):
            approved[audit['message_id']]=(slug,job)
    if not approved:return responses
    for folder in ('messages','sent','completed'):
        for path in (root/folder).glob('CEO-REVIEW-CURRENT-*.json'):
            try:row=json.loads(path.read_text())
            except (OSError,ValueError):continue
            if row.get('from')!='portal-ops' or row.get('to')!='sellemy-ops':continue
            try:payload=json.loads(row.get('body') or '{}')
            except (ValueError,TypeError):continue
            submission=payload.get('review_submission_message_id')
            if submission not in approved or payload.get('review_complete') is not True:continue
            decision=payload.get('review_decision')
            if decision not in DECISIONS:continue
            slug,job=approved[submission]
            existing=read_artifact(slug,'postpublish-ceo-result.json',None)
            if existing:
                if existing.get('source_message_id')!=row.get('message_id'):
                    responses.append({'slug':slug,'error':'conflicting_ceo_response'})
                continue
            outcome={'schema':'sellemy-ceo-postpublish-result/v1','slug':slug,
              'commit':job['published_commit'],'source_message_id':row['message_id'],
              'review_submission_message_id':submission,'decision':decision,
              'outcome':DECISIONS[decision],'handoff':payload.get('review_handoff') or '',
              'published_article_remains_live':True}
            write_artifact(slug,'postpublish-ceo-result.json',outcome)
            if DECISIONS[decision]=='REWORK':
                write_artifact(slug,'owner-ceo-rework.json',{
                    'schema':'sellemy-owner-ceo-rework/v1','slug':slug,'commit':job['published_commit'],
                    'case_state':'AWAITING_OWNER_REPAIR','source_message_id':row['message_id'],
                    'instruction':outcome['handoff'],'no_automatic_republish':True})
            responses.append({'slug':slug,'outcome':DECISIONS[decision]})
    return responses
