"""Read-only end-to-end article lifecycle audit with explicit unverified stages.

This does not launch work, bypass a guard, create a second queue, or count a
non-published article as completed.
"""
from __future__ import annotations
import argparse
import json
from growth_recovery import load_job, read_artifact, all_jobs
from comparison_acceptance import product_issues, receipt_issues


def inspect(slug: str) -> dict:
    state = load_job(slug)
    topic = read_artifact(slug,'topic.json',{}) or {}
    search = read_artifact(slug,'product-search-progress.json',{}) or {}
    evidence = read_artifact(slug,'evidence.json',{}) or {}
    payload = read_artifact(slug,'payload.json',{}) or {}
    acceptance = read_artifact(slug,'acceptance.json',{}) or {}
    qa = read_artifact(slug,'qa.json',{}) or {}
    eyecatch = read_artifact(slug,'eyecatch-receipt.json',{}) or {}
    deliver = read_artifact(slug,'postpublish-ceo-delivered.json',{}) or {}
    parts=[]
    def record(name, status, reason=''):
        parts.append({'stage':name,'status':status,'reason':reason})
    record('planning','PASS' if topic.get('slug')==slug else 'BLOCKED','topic present' if topic.get('slug')==slug else 'missing topic')
    pool=search.get('candidates') or []
    if pool:
        from comparison_acceptance import source_text
        verified=sum(bool(source_text(p)) for p in pool)
        record('discovery','PASS' if verified>=6 else 'BLOCKED',f'{len(pool)} candidates; {verified} verified-source candidates')
    else:
        record('discovery','NOT_REACHED','no candidate progress available')
    if evidence.get('slug')==slug and len(evidence.get('products') or [])==6:
        issues=product_issues(evidence)
        record('product_evidence','PASS' if not issues else 'BLOCKED','; '.join(issues[:4]))
    else:
        record('product_evidence','NOT_REACHED','no validated six-product evidence')
    if payload:
        record('writer','PASS','structured payload present')
        if acceptance:
            issues=receipt_issues(acceptance,evidence,payload)
            record('independent_review','PASS' if not issues else 'BLOCKED','; '.join(str(x) for x in issues[:3]))
        else:
            record('independent_review','NOT_REACHED','no article acceptance receipt')
    else:
        record('writer','NOT_REACHED','no article payload')
        record('independent_review','NOT_REACHED','no article payload')
    if qa:
        failed=[k for k,v in qa.items() if v is False and (k.endswith('_pass') or k.endswith('_in_range'))]
        record('machine_qa','PASS' if qa.get('overall_pass') is True else 'BLOCKED',','.join(failed[:10]))
        record('image_qa','PASS' if qa.get('product_image_audit_pass') is True else 'BLOCKED',
               'verified image audit passed' if qa.get('product_image_audit_pass') is True else 'no passing image audit')
    else:
        record('machine_qa','NOT_REACHED','no machine QA result')
        record('image_qa','NOT_REACHED','no image QA result')
    record('eyecatch','PASS' if eyecatch else 'NOT_REACHED','receipt exists' if eyecatch else 'no verified eyecatch receipt')
    published=(state.get('status')=='PUBLISHED' and bool(state.get('published_commit')))
    record('publish','PASS' if published else 'NOT_REACHED',state.get('published_commit') if published else 'not published')
    record('postpublish_portal','PASS' if deliver else 'NOT_REACHED','delivery exists' if deliver else 'no postpublish CEO delivery receipt')
    return {'slug':slug,'job_status':state.get('status'),'current_stage':state.get('current_stage'),
            'updated_at':state.get('updated_at'),'last_failure':state.get('last_failure_code'),
            'stages':parts,'complete':published and all(p['status']=='PASS' for p in parts[:-1])}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('slugs',nargs='*')
    args=parser.parse_args()
    slugs=args.slugs or [x['slug'] for x in all_jobs() if x.get('status') not in {'DISCARDED','RETIRED'}]
    print(json.dumps([inspect(s) for s in slugs],ensure_ascii=False,indent=2))

if __name__=='__main__':main()
