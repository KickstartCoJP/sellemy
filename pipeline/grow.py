from __future__ import annotations
import argparse, html, json, re, shutil, sqlite3, subprocess, sys, urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'sellemy.db'; TOPICS=ROOT/'data'/'growth_topics.json'; ARTICLES=ROOT/'json'/'articles.json'; SITEMAP=ROOT/'sitemap.xml'; REPORT=ROOT/'data'/'growth_last_run.json'
BASE='https://www.sellemy.jp'; AMAZON_TAG='suzuron-22'; GENERIC=ROOT/'img'/'gadget-category-eyecatch.png'
RAK='https://hb.afl.rakuten.co.jp/hgc/47664979.04b12aff.4766497a.9509b1ad/?pc='
YAH='https://ck.jp.ap.valuecommerce.com/servlet/referral?sid=2770133&pid=891600365&vc_url='
sys.path.insert(0,str(ROOT/'pipeline'))
from discovery_adapters import AutositeDiscoveryAdapter

def pnum(v):
 m=re.search(r'[0-9][0-9,]*',str(v or '')); return int(m.group().replace(',','')) if m else None

def next_topic():
 used={x['slug'] for x in json.loads(ARTICLES.read_text(encoding='utf-8'))}
 fixed=json.loads(TOPICS.read_text(encoding='utf-8'))
 hit=next((x for x in fixed if x['slug'] not in used),None)
 if hit:return hit
 bases=[('コードレス掃除機','cordless-vacuum'),('ロボット掃除機','robot-vacuum'),('電気ケトル','electric-kettle'),('ワイヤレススピーカー','wireless-speaker'),('モバイルバッテリー','mobile-battery'),('USB-C充電器','usb-c-charger'),('デスクライト','desk-light'),('電動歯ブラシ','electric-toothbrush'),('ヘアドライヤー','hair-dryer'),('空気清浄機','air-purifier'),('サーキュレーター','air-circulator'),('コーヒーメーカー','coffee-maker'),('トースター','toaster'),('ホームプロジェクター','home-projector'),('ワイヤレスイヤホン','wireless-earphones')]
 mods=[('一人暮らし向け','single-living'),('省スペース','compact'),('静音重視','quiet'),('初心者向け','beginner'),('在宅ワーク向け','home-office'),('持ち運びやすい','portable'),('時短に便利','time-saving'),('シンプル操作','easy-use')]
 for base,bslug in bases:
  for mod,mslug in mods:
   slug=f'{mslug}-{bslug}-6-picks'
   if slug not in used:
    return {'category':'gadget','query':f'{base} {mod}','title':f'{mod}！{base}6選','slug':slug,'summary':f'{mod}の{base}を6商品比較し、用途に合う選び方を紹介します。'}
 return None

def discover(topic,live=True):
 a=AutositeDiscoveryAdapter(); rows=a.search_amazon_live(topic['query'],limit=18) if live else a.search_cache(topic['query'],limit=30)
 out=[]; seen=set()
 for x in rows:
  asin=(x.get('asin') or '').upper(); price=pnum(x.get('price')); title=x.get('title') or ''; image=x.get('image_url')
  if not re.fullmatch(r'[A-Z0-9]{10}',asin or '') or not title or not image or price is None or asin in seen: continue
  seen.add(asin); out.append({'asin':asin,'title':title,'brand':x.get('brand') or '', 'image_url':image,'observed_price':price})
 return sorted(out,key=lambda x:x['observed_price'])

def select6(rows):
 if len(rows)<6:return []
 idx=[0,1,max(2,len(rows)//2-1),min(len(rows)-1,max(3,len(rows)//2)),len(rows)-2,len(rows)-1]
 out=[]; seen=set()
 for i in idx:
  x=rows[i]
  if x['asin'] not in seen:seen.add(x['asin']);out.append(x)
 for x in rows:
  if len(out)>=6:break
  if x['asin'] not in seen:seen.add(x['asin']);out.append(x)
 return out[:6]

def links(title):
 rak=RAK+urllib.parse.quote('https://search.rakuten.co.jp/search/mall/'+title+'/',safe='')
 yah=YAH+urllib.parse.quote('https://shopping.yahoo.co.jp/search?p='+title,safe='')
 return rak,yah

def card(pid,p):
 t=html.escape(p['title']); b=html.escape(p.get('brand') or ''); amazon=f'https://www.amazon.co.jp/dp/{p["asin"]}?tag={AMAZON_TAG}'; rak,yah=links(p['title'])
 s=f'Amazonで実在を確認した{b+"の" if b else ""}「{t}」です。価格は変動するため、最新の販売条件と仕様はリンク先で確認してください。'
 return f'<div class="item-card" data-product-id="{pid}" data-asin="{p["asin"]}"><img src="{html.escape(p["image_url"])}" alt="{t}"><h3>{t}</h3><p>{s}</p><div class="links"><a href="{amazon}" target="_blank" rel="nofollow" class="link-button">Amazon</a><a href="{rak}" target="_blank" rel="nofollow" class="link-button">楽天</a><a href="{yah}" target="_blank" rel="nofollow" class="link-button">Yahoo</a></div></div>'

def render(topic,products):
 slug=topic['slug']; title=html.escape(topic['title']); summary=html.escape(topic['summary']); url=f'/article/{topic["category"]}/{slug}.html'; img=f'{BASE}/img/{slug}/{slug}.png'
 sections=[]
 for n,(sid,h,ps) in enumerate([('lowrange','手頃な価格帯から選ぶ',products[:2]),('midrange','機能と価格のバランスで選ぶ',products[2:4]),('highrange','機能性を重視して選ぶ',products[4:])]):
  sections.append(f'<section id="{sid}"><div class="section-title"><h2>{h}</h2></div><div class="item-list">'+''.join(card(f'GROW-{slug}-{n*2+i+1}',p) for i,p in enumerate(ps))+'</div></section>')
 return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" /><title>{title} - Sellemy</title><meta name="description" content="{summary}" /><meta property="og:title" content="{title} - Sellemy" /><meta property="og:description" content="{summary}" /><meta property="og:image" content="{img}" /><meta property="og:type" content="article" /><meta property="og:url" content="{BASE}{url}" /><link rel="stylesheet" href="{BASE}/css/style.css" /><script async src="https://www.googletagmanager.com/gtag/js?id=G-SZ5RQR5H7L"></script><script src="/js/ga4.js"></script><link rel="canonical" href="{BASE}{url}" /></head><body class="article-detail" data-category="{topic['category']}"><header class="site-header"><div class="brand"><a href="/index.html"><img src="/img/sellemy-logo.png" alt="Sellemyロゴ" class="logo" /></a><span class="tagline">選びやすくて、わたしにちょうどいい情報ガイド</span></div></header><main><nav class="breadcrumb"><ul><li><a href="/index.html">Top</a></li><li><a href="/article/gadget/">家電</a></li><li><span>{title}</span></li></ul></nav><div class="section-title"><h1>{title}</h1></div><div class="item-eyecatch"><img src="{img}" alt="{title}" /></div><p class="lead">{summary}</p><p class="summary">{summary}</p><nav class="toc"><strong>価格帯から選ぶ</strong><ul><li><a href="#lowrange">手頃な価格帯</a></li><li><a href="#midrange">バランス重視</a></li><li><a href="#highrange">機能性重視</a></li></ul></nav>{''.join(sections)}<section id="how-to-choose"><div class="section-title"><h2>選ぶときのポイント</h2></div><p>用途、設置場所、必要な機能を先に決め、価格だけでなく使い勝手も含めて比較してください。価格は変動するため、最新情報は販売ページで確認してください。</p></section></main><footer><p>&copy; 2025 Sellemy. All rights reserved.</p></footer></body></html>"""

def audit(topic,products,text):
 c={'products_6':len(products)==6,'unique_asin_6':len({p['asin'] for p in products})==6,'cards_6':text.count('class="item-card"')==6,'amazon_6':text.count('>Amazon</a>')==6,'rakuten_6':text.count('>楽天</a>')==6,'yahoo_6':text.count('>Yahoo</a>')==6,'canonical':f'{BASE}/article/{topic["category"]}/{topic["slug"]}.html' in text,'h1':text.count('<h1>')==1,'fixed_price_0':not bool(re.search(r'(?:[¥￥]\s*[0-9][0-9,]*|[0-9][0-9,]*\s*円)',text))}
 return c,all(c.values())

def apply(topic,products,text):
 path=ROOT/'article'/topic['category']/(topic['slug']+'.html'); path.write_text(text,encoding='utf-8')
 d=ROOT/'img'/topic['slug']; d.mkdir(parents=True,exist_ok=True); shutil.copy2(GENERIC,d/(topic['slug']+'.png'))
 arr=json.loads(ARTICLES.read_text(encoding='utf-8')); arr.append({'title':topic['title'],'slug':topic['slug'],'category':topic['category'],'img':f'{BASE}/img/{topic["slug"]}/{topic["slug"]}.png','summary':topic['summary']}); ARTICLES.write_text(json.dumps(arr,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 sm=SITEMAP.read_text(encoding='utf-8'); loc=f'{BASE}/article/{topic["category"]}/{topic["slug"]}.html'; entry=f'  <url>\n    <loc>{loc}</loc>\n    <lastmod>{date.today().isoformat()}</lastmod>\n  </url>\n'; SITEMAP.write_text(sm.replace('</urlset>',entry+'</urlset>'),encoding='utf-8')
 now=datetime.now(timezone.utc).isoformat(); c=sqlite3.connect(DB)
 for i,p in enumerate(products,1):
  pid=f'GROW-{topic["slug"]}-{i}'; c.execute('INSERT OR REPLACE INTO products(product_id,canonical_name,asin,article_file,amazon_url,image_url,status,brand,observed_price,observed_at,amazon_title) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(pid,p['title'],p['asin'],topic['slug']+'.html',f'https://www.amazon.co.jp/dp/{p["asin"]}?tag={AMAZON_TAG}',p['image_url'],'amazon_verified',p.get('brand'),p['observed_price'],now,p['title']))
 c.commit(); c.close(); return path

def publish(topic):
 paths=[f'article/{topic["category"]}/{topic["slug"]}.html',f'img/{topic["slug"]}/{topic["slug"]}.png','json/articles.json','sitemap.xml','data/sellemy.db','data/growth_last_run.json']
 subprocess.run(['git','add',*paths],cwd=ROOT,check=True); subprocess.run(['git','commit','-m',f'Add Sellemy article: {topic["slug"]}'],cwd=ROOT,check=True); subprocess.run(['git','push','origin','main'],cwd=ROOT,check=True)
 return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); ap.add_argument('--publish',action='store_true'); ap.add_argument('--cache-only',action='store_true'); a=ap.parse_args()
 if a.publish and subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(): raise SystemExit('working tree is not clean; scheduled publish aborted')
 t=next_topic();
 if not t: raise SystemExit('no unpublished growth topic')
 rows=discover(t,live=not a.cache_only); selected=select6(rows); text=render(t,selected) if len(selected)==6 else ''; checks,ok=audit(t,selected,text) if text else ({'products_6':False},False)
 result={'topic':t,'candidate_count':len(rows),'selected_asins':[x['asin'] for x in selected],'audit':checks,'audit_pass':ok,'applied':False,'published':False}
 if not ok: REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); raise SystemExit(json.dumps(result,ensure_ascii=False))
 if a.apply: result['path']=str(apply(t,selected,text)); result['applied']=True
 REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 if a.publish:
  if not a.apply: raise SystemExit('--publish requires --apply')
  result['commit']=publish(t); result['published']=True; REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__': main()
