from __future__ import annotations
import argparse,json,re,sqlite3,urllib.parse
from datetime import datetime,timezone
from pathlib import Path
from product_discovery import discover,replace_required
from evidence_collector import collect

ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'data'/'sellemy.db'
CONFIG=ROOT/'pipeline_config.json';VALIDATION=ROOT/'data'/'article_validation_set.json'
LAST=ROOT/'data'/'pipeline_last_run.json';METRICS=ROOT/'data'/'pipeline_quality_cost_last_run.json'
def connect():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def canonical(html,url):
 href='https://www.sellemy.jp'+url
 if 'rel="canonical"' in html:return re.sub(r'<link rel="canonical" href="[^"]+"\s*/?>',f'<link rel="canonical" href="{href}" />',html,count=1)
 return html.replace('</head>',f'  <link rel="canonical" href="{href}" />\n</head>',1)
def products(c,file):return c.execute('SELECT * FROM products WHERE article_file=? ORDER BY product_id',(file,)).fetchall()
def amazon_evidence(c,pid):
 row=c.execute("""SELECT 1 FROM evidence WHERE product_id=? AND verified=1
  AND source_type='amazon_product_page' LIMIT 1""",(pid,)).fetchone()
 return bool(row)
def clean_title(value):
 return re.sub(r'\s+',' ',value or '').strip()
def sync_card(html,p):
 pid=re.escape(p['product_id'])
 match=re.search(r'(<div class="item-card"[^>]*data-product-id="'+pid+r'"[^>]*>)(.*?)(?=<div class="item-card"|</section>)',html,re.S)
 if not match:return html,False
 block=match.group(1)+match.group(2)
 opening=f'<div class="item-card" data-product-id="{p["product_id"]}" data-asin="{p["asin"]}">'
 block=re.sub(r'^<div class="item-card"[^>]*>',opening,block,count=1)
 title=clean_title(p['canonical_name']);brand=clean_title(p['brand'])
 block=re.sub(r'<img\b[^>]*>',f'<img src="{p["image_url"]}" alt="{title}">',block,count=1)
 block=re.sub(r'<h3>.*?</h3>',f'<h3>{title}</h3>',block,count=1,flags=re.S)
 summary=f'Amazonで実在と商品identityを確認した{brand+"の" if brand else ""}「{title}」です。価格は変動するため、最新の販売条件と仕様はリンク先で確認してください。'
 block=re.sub(r'<p>.*?</p>',f'<p>{summary}</p>',block,count=1,flags=re.S)
 block=re.sub(r'(<a href=")[^"]+(" target="_blank" rel="nofollow" class="link-button">Amazon</a>)',
  r'\1'+p['amazon_url']+r'\2',block,count=1)
 return html[:match.start()]+block+html[match.end():],True
def identity_audit(html,p):
 card=re.search(r'<div class="item-card"[^>]*data-product-id="'+re.escape(p['product_id'])+r'"[^>]*>.*?(?=<div class="item-card"|</section>)',html,re.S)
 block=card.group(0) if card else ''
 checks={'name':clean_title(p['canonical_name']) in block,'asin_attr':f'data-asin="{p["asin"]}"' in block,
  'amazon_identity':bool(p['asin'] and p['asin'] in (p['amazon_url'] or '')),
  'affiliate':bool('tag=' in (p['amazon_url'] or '')),'image':bool(p['image_url'] and p['image_url'] in block),
  'brand':bool(p['brand'] and p['brand'] in block)}
 return checks,all(checks.values())
def fixed_price_hits(html):
 text=re.sub(r'<script\b.*?</script>|<style\b.*?</style>',' ',html,flags=re.S|re.I)
 return re.findall(r'(?:[¥￥]\s*[0-9][0-9,]*|[0-9][0-9,]*\s*円)',text)
def html_audit(html):
 cards=len(re.findall(r'<div class="item-card"',html));h1=len(re.findall(r'<h1\b',html))
 secs=[x for x in ('lowrange','midrange','highrange') if f'id="{x}"' in html]
 links=len(re.findall(r'class="link-button"',html))
 return {'cards':cards,'h1':h1,'sections':secs,'affiliate_links':links,
  'structure_pass':cards==6 and h1==1 and len(secs)==3 and links>=18}
def run_article(c,row):
 path=ROOT/'article'/'gadget'/row['file'];html=path.read_text(encoding='utf-8');ps=products(c,row['file'])
 synced=True
 for p in ps:
  html,ok=sync_card(html,p);synced &= ok
 html=canonical(html.replace(' - Sellemy - Sellemy',' - Sellemy'),row['url'])
 structure=html_audit(html);prices=fixed_price_hits(html);ids=[];identity_ok=True;evidence_ok=True
 for p in ps:
  checks,ok=identity_audit(html,p);ids.append({'product_id':p['product_id'],'checks':checks,'pass':ok})
  identity_ok &= ok;evidence_ok &= amazon_evidence(c,p['product_id'])
 if structure['structure_pass'] and synced:path.write_text(html,encoding='utf-8')
 ready=len(ps)==6 and synced and identity_ok and evidence_ok and not prices
 comparison_ok=ready and len({p['asin'] for p in ps})==6 and len(structure['sections'])==3
 return {'url':row['url'],'article_file':row['file'],'product_count':len(ps),
  'amazon_evidence_ready':evidence_ok,'identity_consistency_pass':identity_ok,'public_fixed_price_hits':prices,
  'writer_status':'pass' if ready else 'fail','fact_audit':'pass' if ready else 'fail',
  'comparison_audit':'pass' if comparison_ok else 'fail','seo_spam_audit':'pass' if structure['structure_pass'] else 'fail',
  'identity_audit':ids,**structure}
def main():
 p=argparse.ArgumentParser();p.add_argument('--publish',action='store_true');a=p.parse_args()
 cfg=json.loads(CONFIG.read_text(encoding='utf-8'))
 if a.publish and not cfg.get('publish_enabled'):raise SystemExit('publish is disabled by production pipeline config')
 discovery=discover(apply=True)
 with connect() as c:ev=collect(c,apply=True)
 replacement={'replaced':[],'failed':[]}
 if ev['replacement_required']:
  replacement=replace_required([x['product_id'] for x in ev['items'] if x['status']!='verified'])
  with connect() as c:ev=collect(c,apply=True)
 (ROOT/'data'/'evidence_collection_last_run.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2),encoding='utf-8')
 articles=json.loads(VALIDATION.read_text(encoding='utf-8'))
 with connect() as c:
  results=[run_article(c,x) for x in articles]
  run_id=datetime.now(timezone.utc).strftime('RUN-%Y%m%dT%H%M%SZ')
  ready=all(x['writer_status']=='pass' and x['fact_audit']=='pass' and x['comparison_audit']=='pass' and x['seo_spam_audit']=='pass' for x in results)
  status='ready_for_acceptance' if ready else 'blocked_before_publish'
  c.execute('INSERT OR REPLACE INTO pipeline_runs(run_id,publish_enabled,article_count,status) VALUES(?,?,?,?)',(run_id,0,len(results),status));c.commit()
 report={'run_id':run_id,'status':status,'publish_executed':False,'route':cfg['single_route'],
  'discovery':{k:discovery[k] for k in ('candidates','identity_ready','replacement_required')},
  'replacement_loop':replacement,'evidence':{k:ev[k] for k in ('checked','verified','replacement_required')},'results':results}
 LAST.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 checks=sum(len(x['identity_audit']) for x in results);human=ev['replacement_required']
 metrics={'run_id':run_id,'quality':{'articles_passed':sum(x['writer_status']=='pass' for x in results),
  'articles_total':len(results),'identity_checks_passed':sum(y['pass'] for x in results for y in x['identity_audit']),
  'identity_checks_total':checks,'amazon_verified_products':ev['verified'],'public_fixed_price_hits':sum(len(x['public_fixed_price_hits']) for x in results)},
  'human_confirmation':{'products_requiring_human_confirmation':human,'rate':human/max(checks,1)},
  'generation_cost':{'llm_calls':0,'estimated_usd':0.0,'amazon_live_requests':ev.get('live_requests',0),
   'note':'deterministic production pipeline; observed prices stored internally only'}}
 METRICS.write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'run_id':run_id,'status':status,'articles':len(results),'verified':ev['verified'],
  'replacement':replacement,'publish_executed':False},ensure_ascii=False))
if __name__=='__main__':main()
