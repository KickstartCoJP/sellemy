"""Scheduled postpublication product safety monitoring; alerts are review tasks, not edits.

Only actual published evidence is monitored. No article is automatically changed
or re-approved. Every published product is matched against the full NITE corpus daily.
"""
from __future__ import annotations
import argparse,hashlib,json,time,traceback
from datetime import datetime,timezone
from pathlib import Path
from growth_recovery import ROOT,STATE_ROOT
from safety_recall import unsafe_products
from safety_government_feed import combined_registry

OUTPUT=STATE_ROOT/'safety-monitor'


def published_products(root=ROOT):
    rows=[]
    for path in sorted((root/'data'/'evidence').glob('*.json')):
        try:
            evidence=json.loads(path.read_text(encoding='utf-8'))
            slug=path.stem;category=evidence.get('category')
            if category not in {'beauty','dailygoods','gadget'} or evidence.get('slug')!=slug:
                continue
            if not (root/'article'/category/(slug+'.html')).exists():continue
            for product in evidence.get('products') or []:
                if not isinstance(product,dict) or not product.get('asin'):continue
                rows.append({'slug':slug,'category':category,'product':product})
        except (OSError,ValueError,TypeError):continue
    return rows


def run(*,root=ROOT,output=OUTPUT,limit=None,live=True,now=None,registry=None):
    """Refresh centralized official notices once; compare every published item."""
    now=now or datetime.now(timezone.utc)
    rows=published_products(root)
    grouped={}
    for row in rows:
        p=row['product'];grouped.setdefault(p['asin'],p)
    products=list(grouped.values())
    # No 80-product cursor or partial daily scan. A government feed failure
    # aborts before an all-clear can be recorded.
    registry=registry or combined_registry()
    if not registry.get('complete') or len(registry.get('details') or [])!=registry.get('count'):
        raise ValueError('government recall feed is incomplete')
    flags,records=unsafe_products(products,live=False,registry=registry)
    marked={r['asin']:r for r in flags}
    pending=[]
    for row in rows:
        asin=row['product']['asin']
        if asin not in marked:continue
        flag=marked[asin]
        signature=hashlib.sha256(json.dumps({'slug':row['slug'],'asin':asin,
                 'status':flag['status'],'signals':flag.get('signals')},sort_keys=True,
                 ensure_ascii=False).encode()).hexdigest()[:16]
        pending.append({'id':signature,'article_slug':row['slug'],'category':row['category'],
                        'article_url':f"https://www.sellemy.jp/article/{row['category']}/{row['slug']}.html",
                        'asin':asin,'product_title':row['product'].get('amazon_title'),
                        'status':flag['status'],'signals':flag.get('signals') or [],
                        'action':'CEO safety review: evaluate recommendation suspension / correction',
                        'detected_at':now.isoformat()})
    output.mkdir(parents=True,exist_ok=True)
    ledger=output/'alerts.json'
    try:existing=json.loads(ledger.read_text())
    except (OSError,ValueError):existing={'schema':'sellemy-safety-alerts/v1','items':[]}
    known={item['id']:item for item in existing.get('items',[]) if isinstance(item,dict) and item.get('id')}
    new=[]
    for item in pending:
        if item['id'] not in known:new.append(item)
        known.setdefault(item['id'],item)
    staged={'schema':'sellemy-safety-alerts/v1','items':sorted(known.values(),key=lambda r:r['detected_at'])}
    ledger.write_text(json.dumps(staged,ensure_ascii=False,indent=2),encoding='utf-8')
    result={'published_articles':len({r['slug'] for r in rows}),
            'unique_products':len(products),'scanned':len(products),
            'new_alerts':len(new),'total_alerts':len(known),
            'official_recall_notices':registry['count'],
            'official_index_at':registry['checked_at'],'alerts_file':str(ledger)}
    (output/'last-run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser()
    args=parser.parse_args()
    last_error=None
    for attempt in range(1,6):
        try:
            result=run()
            failure=OUTPUT/'last-error.json'
            if failure.exists():failure.unlink()
            print(json.dumps({**result,'attempt':attempt,'status':'OK'},ensure_ascii=False))
            return
        except Exception as exc:
            last_error={'status':'FAILED','attempt':attempt,
                        'detected_at':datetime.now(timezone.utc).isoformat(),
                        'error_type':type(exc).__name__,'error':str(exc)[:1000]}
            OUTPUT.mkdir(parents=True,exist_ok=True)
            (OUTPUT/'last-error.json').write_text(json.dumps(last_error,ensure_ascii=False,indent=2),encoding='utf-8')
            if attempt<5:time.sleep(min(3*attempt,12))
    raise RuntimeError('Safety monitoring failed after five attempts; persistent alert: '+str(OUTPUT/'last-error.json'))

if __name__=='__main__':main()
