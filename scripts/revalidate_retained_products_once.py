from concurrent.futures import ThreadPoolExecutor, as_completed
from growth_recovery import all_jobs, read_artifact, write_artifact, set_stage
from comparison_acceptance import VERSION, product_issues, blocked
from semantic_review import independent_review

jobs = [j['slug'] for j in all_jobs() if j['status']=='ACTIVE' and j['current_stage'] in ('PRODUCT_HOLD','WRITER_PENDING') and len((read_artifact(j['slug'],'evidence.json',{}) or {}).get('products') or [])==6]

def check(slug):
    evidence=read_artifact(slug,'evidence.json')
    before=[p['asin'] for p in evidence['products']]
    original=(evidence.get('product_acceptance') or {}).get('contract_version')
    receipt=independent_review(evidence)
    evidence['product_acceptance']=receipt
    issues=product_issues(evidence)
    if [p['asin'] for p in evidence['products']] != before: raise ValueError('product list changed')
    write_artifact(slug,'evidence.json',evidence)
    write_artifact(slug,'contract-revalidation.json',{'previous_version':original,'current_version':VERSION,'result':'ACCEPTED' if not issues else 'BLOCKED','issues':issues,'retained_asins':before,'review_reason':(receipt.get('review') or {}).get('reason')})
    if not issues:
        write_artifact(slug,'product-blocker.json',{})
        set_stage(slug,'WRITER_PENDING',event='RETAINED_PRODUCTS_CURRENT_REVIEW_PASSED')
    else:
        write_artifact(slug,'product-blocker.json',blocked(issues))
        set_stage(slug,'PRODUCT_HOLD',event='RETAINED_PRODUCTS_CURRENT_REVIEW_FAILED',detail={'issues':issues[:6]})
    return slug, 'PASS' if not issues else 'FAIL', issues[:5]

with ThreadPoolExecutor(max_workers=2) as pool:
    futures={pool.submit(check,s):s for s in jobs}
    for future in as_completed(futures):
        try:print('RESULT',*future.result(),flush=True)
        except Exception as exc:print('ERROR',futures[future],type(exc).__name__,str(exc)[:200],flush=True)
