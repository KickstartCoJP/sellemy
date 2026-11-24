"""Scheduled postpublication product safety monitoring; alerts are review tasks, not edits.

Only actual published evidence is monitored. No article is automatically changed
or re-approved. The cursor rotates a bounded verified-source scan each day.
"""
from __future__ import annotations
import argparse,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from growth_recovery import ROOT,STATE_ROOT
from safety_recall import unsafe_products

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


def run(*,root=ROOT,output=OUTPUT,limit=80,live=True,now=None):
    now=now or datetime.now(timezone.utc)
    if limit<1:raise ValueError('limit must be positive')
    rows=published_products(root)
    grouped={}
    for row in rows:
        p=row['product'];grouped.setdefault(p['asin'],p)
    products=[grouped[k] for k in sorted(grouped)]
    cursor=output/'cursor.json'
    try:start=int(json.loads(cursor.read_text())['next_index'])%max(1,len(products))
    except (OSError,TypeError,ValueError,KeyError):start=0
    batch=(products[start:]+products[:start])[:limit]
    flags,records=unsafe_products(batch,live=live,cache_path=output/'recall-search-cache.json')
    # Every published identity is compared against verified recall bulletins on
    # each daily run. Only additional live web searches rotate in 80-item batches.
    from safety_recall import catalog_matches
    all_known=[{'asin':p['asin'],'status':'RECALL','signals':catalog_matches(p)}
               for p in products if catalog_matches(p)]
    marked={r['asin']:r for r in [*flags,*all_known]}
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
    (output/'alerts.json').write_text(json.dumps(staged,ensure_ascii=False,indent=2),encoding='utf-8')
    cursor.write_text(json.dumps({'next_index':(start+len(batch))%max(1,len(products)),
                         'scanned_at':now.isoformat(),'unique_product_count':len(products)},indent=2),encoding='utf-8')
    result={'published_articles':len({r['slug'] for r in rows}),
            'unique_products':len(products),'scanned':len(batch),
            'new_alerts':len(new),'total_alerts':len(known),
            'alerts_file':str(ledger),'next_cursor':(start+len(batch))%max(1,len(products))}
    (output/'last-run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=80)
    parser.add_argument('--offline',action='store_true',help='catalog-only verification test')
    args=parser.parse_args()
    print(json.dumps(run(limit=args.limit,live=not args.offline),ensure_ascii=False))

if __name__=='__main__':main()
