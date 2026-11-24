"""Independent, source-attributed product-recall screening, shared by Product and monitor.

No search snippet is treated as proof. A matching official notice is an alert,
not proof that every retail unit is affected. Unknown is never called safe.
"""
from __future__ import annotations
import hashlib,ipaddress,json,re,subprocess,unicodedata
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from growth_recovery import STATE_ROOT

ROOT=Path(__file__).resolve().parents[1]
CATALOG=ROOT/'config/safety-recalls.json'
CACHE=STATE_ROOT/'safety-recall-cache.json'
HIGH_RISK=re.compile(r'モバイルバッテリー|リチウム|ヒーター|暖房|ストーブ|ガス|コンロ|タイヤ|チャイルドシート|ヘルメット|充電式.{0,8}(?:ポンプ|空気入れ)|power bank|lithium battery|heater|inflator|car seat',re.I)
RECALL_WORD=re.compile(r'自主回収|リコール|製品回収|無償交換|recall',re.I)
OFFICIAL_GOV=('nite.go.jp','meti.go.jp','recall.caa.go.jp','caa.go.jp')


class ProductSafetyError(ValueError):
    def __init__(self, flags):
        self.flags=flags
        super().__init__('product safety review: '+json.dumps(flags,ensure_ascii=False)[:1000])


def normalize(value):
    return unicodedata.normalize('NFKC',str(value or '')).casefold()


def contains_model(text,model):
    """Avoid Soundcore 3 matching Soundcore 3i or Soundcore 30."""
    if not model or len(model)<3:return False
    words=re.findall(r'[a-z0-9]+|[ぁ-んァ-ヶ一-龥]+',normalize(model))
    if not words:return False
    pattern=r'[^a-z0-9ぁ-んァ-ヶ一-龥]*'.join(re.escape(w) for w in words)
    text=normalize(text)
    for matched in re.finditer(r'(?<![a-z0-9])'+pattern+r'(?![a-z0-9])',text):
        # The recalled base model is not automatically an upgraded variant.
        if not re.match(r'\s+(?:pro|plus|mini|max|lite)\b',text[matched.end():]):
            return True
    return False


def catalog_matches(product):
    text=' '.join(str(product.get(k) or '') for k in ('amazon_title','brand','model','title','identity_key'))
    data=json.loads(CATALOG.read_text(encoding='utf-8'))
    manufacturer=normalize(product.get('brand') or '')
    known_brand=('anker' in normalize(text) or 'soundcore' in manufacturer)
    return [{**entry,'asin':product['asin'],'reason':'official manufacturer recall for identified product model'}
            for entry in data['items'] if any(contains_model(text,model) for model in entry['models'])
            and known_brand]


def _public_host(url):
    try:
        v=urlsplit(url);host=(v.hostname or '').lower()
        if v.scheme!='https' or not host or v.username or v.password:return ''
        ip=ipaddress.ip_address(host)
        return host if ip.is_global else ''
    except ValueError:
        return host if not (host.endswith(('.local','.internal','.localhost')) or host=='localhost') else ''


def _is_official(url,product):
    host=_public_host(url)
    if not host:return False
    if any(host==gov or host.endswith('.'+gov) for gov in OFFICIAL_GOV):return True
    for source in product.get('sources') or []:
        if source.get('source_type')=='manufacturer_official' and source.get('verified') is True:
            official=_public_host(source.get('url') or '')
            if official and (host==official or host.endswith('.'+official) or official.endswith('.'+host)):
                return True
    # Established manufacturer domains for current recalled models only.
    brand=normalize(product.get('brand') or '')
    return 'anker' in brand and (host=='ankerjapan.com' or host.endswith('.ankerjapan.com'))


def _model_hint(product):
    title=str(product.get('amazon_title') or product.get('title') or '')
    code=re.search(r'(?<![A-Za-z0-9])(?:[A-Za-z]{1,8}-)?[A-Za-z0-9-]*\d[A-Za-z0-9-]{2,}(?![A-Za-z0-9])',title)
    if code:return code.group(0)
    return re.sub(r'\s+',' ',title)[:50]


def _search(products):
    script=str(Path(__file__).with_name('vm_safety_search.py'))
    records=[]
    for offset in range(0,len(products),6):
        batch=products[offset:offset+6]
        payload=[{'asin':p['asin'],'brand':p.get('brand'),'model':_model_hint(p)} for p in batch]
        try:
            result=subprocess.run(['/opt/homebrew/bin/colima','ssh','--','xvfb-run','-a',
                    '/home/kickstart.guest/.ai-role-playwright/bin/python',script],
                    input=json.dumps(payload,ensure_ascii=False),text=True,capture_output=True,
                    timeout=145,check=False)
            if result.returncode:raise ValueError('Chrome search failed')
            rows=json.loads(result.stdout).get('items',[])
            if len(rows)!=len(batch):raise ValueError('incomplete search')
            records.extend(rows)
        except (OSError,ValueError,TypeError,subprocess.TimeoutExpired):
            records.extend({'asin':p['asin'],'searched':False,'urls':[]} for p in batch)
    return {r['asin']:r for r in records}


def _official_match(url,product):
    if not _is_official(url,product):return None
    try:
        res=requests.get(url,timeout=14,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=True)
        if res.status_code!=200 or not _is_official(res.url,product):return None
        soup=BeautifulSoup(res.content[:500000],'html.parser')
        for e in soup.select('script,style,nav,footer,header'):e.decompose()
        title=soup.title.get_text(' ',strip=True) if soup.title else ''
        body=soup.get_text(' ',strip=True)[:70000]
    except (requests.RequestException,ValueError):return None
    model=_model_hint(product)
    if not RECALL_WORD.search(title+' '+body):return None
    if not contains_model(body,model) and not contains_model(title,model):return None
    index=normalize(body).find(normalize(model))
    excerpt=body[max(0,index-280):index+480] if index>=0 else title
    # Search hits alone are not evidence: require an official page with a model
    # mention near an actual recall statement, or a relevant recall page title.
    if not (RECALL_WORD.search(excerpt) or RECALL_WORD.search(title)):
        return None
    return {'asin':product['asin'],'status':'POTENTIAL_RECALL',
            'source':res.url,'model':model,'excerpt':excerpt[:720],
            'reason':'official recall notice mentions this product; scope must be checked'}


def scan_products(products,*,live=True,cache_path=None,now=None):
    now=now or datetime.now(timezone.utc)
    path=cache_path or CACHE
    try:cache=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):cache={}
    results={};pending=[]
    for product in products:
        asin=product['asin']
        known=catalog_matches(product)
        if known:
            results[asin]={'asin':asin,'status':'RECALL','signals':known,'checked_at':now.isoformat()}
            continue
        key=hashlib.sha256(json.dumps({'asin':asin,'brand':product.get('brand'),
                        'title':product.get('amazon_title'),'source_urls':[
                        s.get('url') for s in product.get('sources') or [] if isinstance(s,dict)]},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        stored=cache.get(asin, {})
        try:fresh=datetime.fromisoformat(stored['checked_at'])>=now-timedelta(hours=24)
        except (KeyError,TypeError,ValueError):fresh=False
        if fresh and stored.get('cache_key')==key:
            results[asin]=stored
        elif live:pending.append((product,key))
        else:results[asin]={'asin':asin,'status':'NOT_CHECKED','signals':[],'checked_at':now.isoformat()}
    if pending:
        searches=_search([p for p,_ in pending])
        for product,key in pending:
            asin=product['asin'];result=searches.get(asin,{})
            signals=[]
            if result.get('searched') is True:
                for url in (result.get('urls') or [])[:4]:
                    hit=_official_match(url,product)
                    if hit:signals.append(hit)
            status=('POTENTIAL_RECALL' if signals else
                    'NO_OFFICIAL_SIGNAL' if result.get('searched') is True else 'SEARCH_UNAVAILABLE')
            row={'asin':asin,'status':status,'signals':signals,'checked_at':now.isoformat(),
                 'cache_key':key}
            results[asin]=row;cache[asin]=row
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(cache,ensure_ascii=False,sort_keys=True),encoding='utf-8')
        tmp.replace(path)
    return [results[p['asin']] for p in products]


def unsafe_products(products,*,live=True,cache_path=None):
    records=scan_products(products,live=live,cache_path=cache_path)
    by_asin={p['asin']:p for p in products}
    flags=[r for r in records if r['status'] in ('RECALL','POTENTIAL_RECALL') or
           (r['status'] in ('SEARCH_UNAVAILABLE','NOT_CHECKED') and
            HIGH_RISK.search(' '.join(str(by_asin[r['asin']].get(k) or '') for k in ('amazon_title','brand'))))]
    return flags,records


def require_product_safety(products,*,live=True,cache_path=None):
    flags,records=unsafe_products(products,live=live,cache_path=cache_path)
    if flags:raise ProductSafetyError(flags)
    return records
