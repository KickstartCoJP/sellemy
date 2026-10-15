"""Deterministic audit selection from sequential *published* article identities.

Pure selection only; no review queue, no publication block, and no external send.
"""
from __future__ import annotations
import hashlib,json

def select_reviews(published: list[dict], *, every_n: int = 10) -> list[dict]:
    if not isinstance(every_n,int) or every_n<1:
        raise ValueError('invalid CEO sample interval')
    if any(not row.get('slug') or not row.get('commit') for row in published):
        raise ValueError('unverified published article identity')
    identities=[(r['slug'],r['commit']) for r in published]
    if len(set(identities))!=len(identities):
        raise ValueError('duplicate published article identity')
    samples=[]
    for first in range(0,len(published)-len(published)%every_n,every_n):
        group=published[first:first+every_n]
        snapshot=json.dumps(identities[first:first+every_n],ensure_ascii=False,separators=(',',':'))
        digest=hashlib.sha256(snapshot.encode()).hexdigest()
        chosen=int(digest,16)%every_n
        samples.append({'schema':'sellemy-ceo-sample/v1','group_start':first,'group_size':every_n,
                        'group_sha256':digest,'selection_index':chosen,'slug':group[chosen]['slug'],
                        'commit':group[chosen]['commit'],'review_state':'PENDING_POSTPUBLISH_NONBLOCKING'})
    return samples


def pending_review_receipt(*, jobs: list[dict], current_slug: str, every_n: int = 10) -> dict:
    """Submit on the Nth successful publication, even if another group item is selected."""
    eligible=sorted((r for r in jobs if r.get('status')=='PUBLISHED'
                     and r.get('operating_profile')=='normal' and r.get('published_commit')
                     and r.get('slug') and (r.get('published_at') or r.get('updated_at'))),
                    key=lambda r:(r.get('published_at') or r.get('updated_at'),r['slug']))
    selected=select_reviews([{'slug':r['slug'],'commit':r['published_commit']} for r in eligible],every_n=every_n)
    new_group_complete=(len(eligible)>0 and len(eligible)%every_n==0
                        and eligible[-1]['slug']==current_slug)
    return {'selected':next((r for r in selected if r['slug']==current_slug),None),
            'ready_to_dispatch':selected[-1] if new_group_complete and selected else None,
            'published_normal_count':len(eligible),'groups_completed':len(selected)}
