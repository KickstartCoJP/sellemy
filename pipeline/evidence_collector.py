from __future__ import annotations
import argparse, hashlib, importlib.util, json, re, sqlite3, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from affiliate_config import amazon_url
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'sellemy.db'
REPORT=ROOT/'data'/'evidence_collection_last_run.json'
AUTOSITE=Path('/Users/suzukitakayuki/autosite')
AMAZON_CACHE=AUTOSITE/'amazon_item'/'product_data.json'
CATEGORY_TERMS={
 'air-purifiers-top-6.html':('空気清浄','イオン発生'),
 'beginner-friendly-coffee-makers.html':('コーヒー','バリスタ'),
 'best-high-quality-earphones-6-picks.html':('イヤホン','ヘッドホン','SOUNDCORE C30I'),
 'comfortable-morning-toasters.html':('トースター','コンベクションオーブン'),
 'enjoy-summer-portable-fans-6-picks.html':('ハンディファン','携帯扇風機','手持ち扇風機','腰掛け扇風機'),
 'high-performance-air-circulators-6.html':('サーキュレーター',),
 'home-projectors-6-recommendations.html':('プロジェクター',),
 'must-have-kitchen-appliances.html':('フードプロセッサー','ブレンダー','ミキサー','炊飯','圧力鍋','食器洗','オーブン'),
 'smart-home-appliances-top-6.html':('スマート','LED ランプ','LEDランプ','リモコン','ECHO DOT','バスマット'),
 'stay-cool-at-home-6-best-evaporative-coolers.html':('冷風','クーラー',),
}
def connect():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def migrate(c):
 cols={x[1] for x in c.execute('PRAGMA table_info(products)')}
 for name,typ in (('brand','TEXT'),('observed_price','INTEGER'),('observed_at','TEXT'),('amazon_title','TEXT')):
  if name not in cols:c.execute(f'ALTER TABLE products ADD COLUMN {name} {typ}')
def load_cache():
 try:rows=json.loads(AMAZON_CACHE.read_text(encoding='utf-8'))
 except Exception:rows=[]
 return {x.get('asin'):x for x in rows if x.get('asin')}
def load_scraper():
 path=AUTOSITE/'amazon_item'/'amazon_scraper_back.py'
 spec=importlib.util.spec_from_file_location('sellemy_amazon_scraper',path)
 mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
 return mod
def price_int(value):
 m=re.search(r'[0-9][0-9,]*',str(value or ''))
 return int(m.group().replace(',','')) if m else None
def category_ok(article_file,item):
 text=((item.get('title') or '')+' '+(item.get('category_path') or '')).upper()
 return any(term.upper() in text for term in CATEGORY_TERMS.get(article_file,()))
def valid(item,row):
 if not item:return False,'amazon_fetch_failed'
 if str(item.get('asin') or item.get('item_code') or '').upper()!=str(row['asin'] or '').upper():return False,'asin_mismatch'
 if not item.get('title') or not item.get('image_url'):return False,'amazon_identity_incomplete'
 if not category_ok(row['article_file'],item):return False,'category_mismatch'
 return True,'verified:amazon_asin_and_category'
def fetch_all(rows):
 cache=load_cache();scraper=load_scraper();found={};live=[]
 for row in rows:
  if row['amazon_title'] and row['image_url'] and row['observed_price'] and row['observed_at']:
   found[row['product_id']]={'asin':row['asin'],'title':row['amazon_title'],'brand':row['brand'],
    'image_url':row['image_url'],'price':str(row['observed_price']),'category_path':''}
   continue
  hit=cache.get(row['asin'])
  if hit:found[row['product_id']]=hit
  else:live.append(row)
 with ThreadPoolExecutor(max_workers=6) as pool:
  futures={pool.submit(scraper.scrape_item,row['asin']):row for row in live}
  for future in as_completed(futures):
   row=futures[future]
   try:found[row['product_id']]=future.result()
   except Exception:found[row['product_id']]=None
 return found,len(live)
def persist(c,row,item,observed_at):
 asin=row['asin'].upper();url=amazon_url(asin)
 title=re.sub(r'^Amazon.co.jp\s*[:：|]\s*','',item['title']).strip()
 brand=re.sub(r'^(?:ブランド:\s*)|(?:のストアを表示)$','',item.get('brand') or '').strip() or None
 if not brand:
  supported=('シャープ','ダイキン','パナソニック','HARIO','SHURE','ソニー','BenQ','Aladdin X','TOSHIBA')
  brand=next((x for x in supported if x.upper() in title.upper()),None)
 eid='AMZ-'+hashlib.sha256((row['product_id']+'|'+asin).encode()).hexdigest()[:24]
 value={'asin':asin,'title':title,'brand':brand,'image_url':item['image_url'],
  'observed_price':price_int(item.get('price')),'observed_at':observed_at,
  'verification_method':'amazon_asin_and_category','automated':True}
 c.execute("""INSERT OR REPLACE INTO evidence
  (evidence_id,product_id,source_type,source_url,field_path,value_json,verified)
  VALUES(?,?, 'amazon_product_page',?,'identity',?,1)""",(eid,row['product_id'],url,json.dumps(value,ensure_ascii=False)))
 c.execute("""UPDATE products SET canonical_name=?,brand=?,amazon_title=?,amazon_url=?,image_url=?,
  observed_price=?,observed_at=?,status='amazon_verified' WHERE product_id=?""",
  (title,brand,title,url,item['image_url'],value['observed_price'],observed_at,row['product_id']))
def collect(c,apply=False):
 migrate(c);rows=c.execute('SELECT * FROM products ORDER BY product_id').fetchall()
 found,live_count=fetch_all(rows);items=[];observed_at=datetime.now(timezone.utc).isoformat()
 for row in rows:
  item=found.get(row['product_id']);ok,reason=valid(item,row)
  if not ok and row['amazon_title'] and row['image_url'] and row['observed_price']:
   item={'asin':row['asin'],'title':row['amazon_title'],'brand':row['brand'],
    'image_url':row['image_url'],'price':str(row['observed_price']),'category_path':''}
   ok,reason=valid(item,row)
   if ok:reason='verified:prior_amazon_observation'
  if apply:
   if ok:persist(c,row,item,observed_at)
   else:c.execute("UPDATE products SET status='replacement_required' WHERE product_id=?",(row['product_id'],))
  items.append({'product_id':row['product_id'],'asin':row['asin'],'status':'verified' if ok else 'replacement_required',
   'reason':reason,'observed_price':price_int(item.get('price')) if item else None,'source':'amazon_live_or_cache'})
 if apply:c.commit()
 return {'mode':'apply' if apply else 'dry_run','checked':len(items),'verified':sum(x['status']=='verified' for x in items),
  'replacement_required':sum(x['status']!='verified' for x in items),'live_requests':live_count,'items':items}
def main():
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args()
 with connect() as c:r=collect(c,a.apply)
 REPORT.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:r[k] for k in ('mode','checked','verified','replacement_required','live_requests')},ensure_ascii=False))
if __name__=='__main__':main()
