"""Independent, source-attributed product-recall screening, shared by Product and monitor.

No search snippet is treated as proof. A matching official notice is an alert,
not proof that every retail unit is affected. Unknown is never called safe.
"""
from __future__ import annotations
import json,re,unicodedata
from datetime import datetime,timezone
from pathlib import Path
from safety_government_feed import combined_registry, cached_combined_registry, GovernmentFeedUnavailable

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


def _product_models(product):
    """Full SKU/JAN or distinctive named series; never dates, size or brand alone."""
    title=str(product.get('amazon_title') or product.get('title') or '')
    explicit=[str(product.get(key) or '').strip() for key in
              ('model','model_number','jan','jan_code','ean','ean_code')]
    code=re.findall(r'(?<![A-Za-z0-9-])[A-Za-z]{1,8}(?:-[A-Za-z]{1,8})?[- ]?\d{2,}[A-Za-z0-9-]*(?![A-Za-z0-9])',title)
    # Retail SKUs also include digit-leading 43E50S and short hyphenated RAO-3.
    code+=re.findall(r'(?<![A-Za-z0-9-])\d{2,4}[A-Za-z]{1,8}\d{1,8}[A-Za-z0-9-]*(?![A-Za-z0-9])',title)
    code+=re.findall(r'(?<![A-Za-z0-9-])[A-Za-z]{2,8}-\d[A-Za-z0-9-]*(?![A-Za-z0-9])',title)
    # Model prefixes such as NH-D503 must not be reduced to generic D503.
    series=re.findall(r'(?<![A-Za-z0-9])[A-Za-z]{4,}\s+\d{1,4}(?![A-Za-z0-9])',title)
    jan=re.findall(r'(?<![0-9])\d{13}(?![0-9])',title)
    brand=normalize(product.get('brand'))
    return list(dict.fromkeys(v for v in explicit+code+series+jan
        if len(normalize(v))>=4 and normalize(v)!=brand))


def _brand_evidence(product,official):
    """A short generic SKU must be anchored to the manufacturer or brand."""
    manufacturer=normalize(official.get('manufacturer') or '')
    body=normalize(official.get('text') or '')
    title=normalize(product.get('amazon_title') or '')
    brand=normalize(product.get('brand') or '')
    # A brand may be different from the legal importing entity, so title
    # fragments give an additional conservative clue.
    candidates=[brand]
    first=re.split(r'\s|（|\(|\[|【',title)[0]
    if first and len(first)>=3:candidates.append(first)
    return any(c and len(c)>=3 and (c in manufacturer or c in body)
               for c in candidates)


def _matched_official(product,official,*,models=None,known=None,prepared=None):
    content=str(official.get('text') or '')
    normalized=prepared if prepared is not None else normalize(content)
    if models is None:models=_product_models(product)
    for token in models:
        term=normalize(token)
        unique=(len(re.sub(r'[^a-z0-9]','',term))>=9 and
                bool(re.search(r'[a-z]',term)) and bool(re.search(r'\d',term)))
        jan=bool(re.fullmatch(r'\d{13}',term))
        if term in normalized and (unique or jan or _brand_evidence(product,official)) and contains_model(content,token):
            return token
    if known is None:known=catalog_matches(product)
    for match in known:
        for token in match['models']:
            if normalize(token) in normalized and contains_model(content,token):
                return token
    return None


def scan_products(products,*,live=True,cache_path=None,now=None,registry=None):
    """One complete NITE government snapshot is compared against EVERY product.

    Catalog is a safety fallback but never substitutes for refreshing the
    government register. Inability to collect all notices is NOT_CHECKED.
    """
    now=now or datetime.now(timezone.utc)
    if registry is None:
        try:
            registry=combined_registry() if live else cached_combined_registry()
        except GovernmentFeedUnavailable:
            registry=None
    complete=bool(registry and registry.get('complete') is True and registry.get('details')
                  and len(registry['details'])==registry.get('count'))
    result_details=[(entry,normalize(entry.get('text') or '')) for entry in registry['details']] if complete else []
    results=[]
    for p in products:
        asin=p['asin']
        known=catalog_matches(p)
        models=_product_models(p)
        signals=[{**v,'source_type':'official_recall_catalog'} for v in known]
        if complete:
            for official,prepared in result_details:
                match=_matched_official(p,official,models=models,known=known,prepared=prepared)
                if match:
                    signals.append({'asin':asin,'status':'POTENTIAL_RECALL',
                        'source':official['source'],'notice_id':official['id'],
                        'model':match,'excerpt':official['text'][:1800],
                        'reason':'official NITE recall notice contains the model; verify exact serial/date scope',
                        'manufacturer_notice_candidates':official.get('manufacturer_notice_candidates',[]),
                        'source_type':official.get('source_type','NITE_SAFE_LITE')})
        status=('RECALL' if known else 'POTENTIAL_RECALL' if signals else
                'NO_OFFICIAL_SIGNAL' if complete else 'NOT_CHECKED')
        results.append({'asin':asin,'status':status,'signals':signals,
                        'checked_at':now.isoformat(),
                        'official_index_at':registry.get('checked_at') if complete else None})
    return results


def unsafe_products(products,*,live=True,cache_path=None,registry=None):
    records=scan_products(products,live=live,cache_path=cache_path,registry=registry)
    flags=[r for r in records if r['status']!='NO_OFFICIAL_SIGNAL']
    return flags,records


def require_product_safety(products,*,live=True,cache_path=None,registry=None):
    flags,records=unsafe_products(products,live=live,cache_path=cache_path,registry=registry)
    if flags:raise ProductSafetyError(flags)
    return records
