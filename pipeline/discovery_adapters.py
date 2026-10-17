from __future__ import annotations
import importlib.util,json,re,sys,urllib.parse
from html.parser import HTMLParser
from pathlib import Path

AUTOSITE=Path("/Users/kickstart/autosite")
AMAZON_DATA=AUTOSITE/"amazon_item/product_data.json"
YAHOO_DATA=AUTOSITE/"yahoo_item/product_data_final.json"

def _load(path):
 try:return json.loads(path.read_text(encoding="utf-8"))
 except Exception:return []

def _module(name,path):
 spec=importlib.util.spec_from_file_location(name,path)
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
 return module

def normalize(value):
 return re.sub(r"[^a-z0-9ぁ-んァ-ヶ一-龠]","",(value or "").lower())

def terms(query):
 return [normalize(x) for x in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|[ぁ-んァ-ヶ一-龠]{2,}",query or "") if len(normalize(x))>=2]


class _AmazonSearchParser(HTMLParser):
 VOID_TAGS={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
 def __init__(self):
  super().__init__(convert_charrefs=True)
  self.depth=0;self.card_depth=None;self.card=None;self.in_h2=False;self.h2_depth=None;self.title_parts=[];self.rows=[]
 def handle_starttag(self,tag,attrs):
  if tag not in self.VOID_TAGS:self.depth+=1
  a=dict(attrs)
  if tag=='div' and a.get('data-component-type')=='s-search-result' and re.fullmatch(r'[A-Z0-9]{10}',a.get('data-asin','')):
   self.card_depth=self.depth;self.card={'asin':a['data-asin'],'title':'','image_url':'','price':None}
  if self.card is not None:
   if tag=='h2' and not self.in_h2:
    self.in_h2=True;self.h2_depth=self.depth;self.title_parts=[]
   if tag=='img' and 's-image' in (a.get('class') or '').split() and not self.card['image_url']:
    self.card['image_url']=(a.get('src') or '').strip()
 def handle_data(self,data):
  if self.card is not None and self.in_h2:
   value=data.strip()
   if value:self.title_parts.append(value)
 def handle_endtag(self,tag):
  if self.card is not None and self.in_h2 and tag=='h2' and self.depth==self.h2_depth:
   self.card['title']=' '.join(self.title_parts).strip();self.in_h2=False;self.h2_depth=None
  if self.card is not None and tag=='div' and self.depth==self.card_depth:
   if self.card['title'] and self.card['image_url']:
    self.card['item_url']=f"https://www.amazon.co.jp/dp/{self.card['asin']}"
    self.rows.append(self.card)
   self.card=None;self.card_depth=None;self.in_h2=False;self.h2_depth=None;self.title_parts=[]
  if tag not in self.VOID_TAGS:self.depth=max(0,self.depth-1)

def parse_amazon_search_results(body,limit=5):
 parser=_AmazonSearchParser();parser.feed(body)
 out=[];seen=set()
 for row in parser.rows:
  if row['asin'] in seen:continue
  seen.add(row['asin']);out.append(row)
  if len(out)>=limit:break
 return out

class AutositeDiscoveryAdapter:
 def __init__(self):
  self.amazon=_load(AMAZON_DATA);self.yahoo=_load(YAHOO_DATA)
  self.amazon_by_asin={x.get("asin"):x for x in self.amazon if x.get("asin")}
 def health(self):
  return {"amazon_cache":len(self.amazon),"yahoo_cache":len(self.yahoo),
   "amazon_source":str(AMAZON_DATA),"yahoo_source":str(YAHOO_DATA),
   "rakuten_required":False}
 def amazon_identity(self,asin,live=False):
  hit=self.amazon_by_asin.get(asin)
  if hit or not live:return hit
  mod=_module("autosite_amazon_scraper",AUTOSITE/"amazon_item/amazon_scraper_back.py")
  return mod.scrape_item(asin)
 def search_cache(self,query,limit=10):
  wanted=terms(query);rows=[]
  for platform,data in (("amazon",self.amazon),("yahoo",self.yahoo)):
   for item in data:
    title=item.get("title") or item.get("final_title") or "";norm=normalize(title)
    score=sum(t in norm for t in wanted)
    if score:rows.append({"platform":platform,"score":score,"title":title,
      "asin":item.get("asin"),"image_url":item.get("image_url"),
      "item_url":item.get("item_url"),"price":item.get("price")})
  rows.sort(key=lambda x:(-x["score"],x.get("price") is None))
  return rows[:limit]
 def search_amazon_live(self,query,limit=5):
  import requests
  base="https://www.amazon.co.jp/s?k="+urllib.parse.quote(query)
  headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131 Safari/537.36"}
  response=requests.get(base,headers=headers,timeout=20)
  response.raise_for_status()
  return parse_amazon_search_results(response.text,limit=limit)
 def search_yahoo_live(self,query,limit=5):
  collector=_module("autosite_yahoo_collector",AUTOSITE/"yahoo_item/yahoo_url_collector.py")
  scraper=_module("autosite_yahoo_scraper",AUTOSITE/"yahoo_item/yahoo_scraper.py")
  url="https://shopping.yahoo.co.jp/search?p="+urllib.parse.quote(query)
  return [x for x in (scraper.parse_product_page(u) for u in collector.extract_product_urls(url)[:limit]) if x]
