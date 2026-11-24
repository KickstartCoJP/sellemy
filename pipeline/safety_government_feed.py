"""NITE SAFE-Lite governmental recall register. One daily collection, all pages.

Government list rows are candidates, not verified product-level matches. Fetch
complete official detail for relevant rows only. Never accept a partial register
as 'no recall'. CAA/METI notices linked from detail are supporting references.
"""
from __future__ import annotations
import json,re,tempfile,unicodedata
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
import requests
from growth_recovery import STATE_ROOT

BASE='https://safe-lite.nite.go.jp'
INDEX_URL=BASE+'/recall/search/index'
INDEX_PATH=STATE_ROOT/'government-recall-index.json'
PER_PAGE=100
MAX_PAGES=100

class GovernmentFeedUnavailable(RuntimeError):pass


def clean(value):
    return re.sub(r'[^a-z0-9ぁ-んァ-ヶ一-龥]','',unicodedata.normalize('NFKC',str(value or '')).casefold())


def _response_html(response):
    response.raise_for_status()
    value=response.json()
    if not isinstance(value,dict) or not isinstance(value.get('htmlContent'),str):
        raise GovernmentFeedUnavailable('invalid SAFE-Lite result')
    soup=BeautifulSoup(value['htmlContent'],'html.parser')
    count=soup.select_one('#recall-count')
    if not count or not count.get_text(strip=True).isdigit():
        raise GovernmentFeedUnavailable('SAFE-Lite count unavailable')
    return soup,int(count.get_text(strip=True))


def _parse_rows(soup):
    rows=[]
    for tr in soup.select('tbody tr'):
        cells=tr.select('td')
        if len(cells)<7:raise GovernmentFeedUnavailable('unexpected SAFE-Lite table structure')
        link=cells[3].select_one('a[href]')
        if not link:raise GovernmentFeedUnavailable('SAFE-Lite detail link missing')
        detail=urljoin(BASE+'/',link['href'].replace('./recall/','recall/').split('?',1)[0])
        if not detail.startswith(BASE+'/recall/detail/'):
            raise GovernmentFeedUnavailable('foreign SAFE-Lite detail URL')
        rows.append({'id':cells[0].get_text(' ',strip=True),
                     'date':cells[1].get_text(' ',strip=True),
                     'category':cells[3].get_text(' ',strip=True),
                     'model_summary':cells[4].get_text(' ',strip=True),
                     'manufacturer':cells[5].get_text(' ',strip=True),
                     'reason':cells[6].get_text(' ',strip=True),
                     'source':detail})
    return rows


def refresh(*,path=INDEX_PATH,session=None,now=None,force=False):
    """Refresh complete NITE official list, retaining last good snapshot on failure."""
    now=now or datetime.now(timezone.utc)
    try:
        cached=json.loads(path.read_text(encoding='utf-8'))
        checked=datetime.fromisoformat(cached['checked_at'])
        if (not force and checked>=now-timedelta(hours=24)
                and cached.get('complete') is True and cached.get('items')):
            return cached
    except (OSError,ValueError,KeyError,TypeError):pass
    session=session or requests.Session()
    try:
        session.get(BASE+'/',timeout=15).raise_for_status()
        first,count=_response_html(session.post(INDEX_URL,timeout=20,
            data={'searchWord':'','isFreewordSearch':'true','pagesize':PER_PAGE},
            headers={'X-Requested-With':'XMLHttpRequest'}))
        pages=(count+PER_PAGE-1)//PER_PAGE
        if not 1<=pages<=MAX_PAGES:raise GovernmentFeedUnavailable('unexpected count/pages')
        rows=_parse_rows(first)
        for page in range(2,pages+1):
            url=INDEX_URL+'?pagesize=100&isFreewordSearch=1&searchWord=&orderColumn=i_02_number&order=desc&page='+str(page)
            html,current=_response_html(session.get(url,timeout=20,headers={'X-Requested-With':'XMLHttpRequest'}))
            if current!=count:raise GovernmentFeedUnavailable('register changed during pagination')
            rows.extend(_parse_rows(html))
        ids=[row['id'] for row in rows]
        if len(rows)!=count or len(set(ids))!=count:
            raise GovernmentFeedUnavailable(f'incomplete official recall index: {len(rows)}/{count}')
        result={'schema':'sellemy-nite-recall-index/v1','source':INDEX_URL,
                'checked_at':now.isoformat(),'complete':True,'count':count,'items':rows}
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as handle:
            json.dump(result,handle,ensure_ascii=False)
            tmp=Path(handle.name)
        tmp.replace(path)
        return result
    except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
        raise GovernmentFeedUnavailable('official NITE index refresh failed: '+str(exc)[:240]) from exc


def candidates(product,index):
    """Collect all possible notices for a manufacturer, model or named series."""
    brand=clean(product.get('brand'))
    title=clean(product.get('amazon_title') or product.get('title'))
    models=re.findall(r'[a-z]+[- ]?\d[a-z0-9-]*|[a-z]*\d{4,}[a-z0-9-]*',
                      unicodedata.normalize('NFKC',str(product.get('amazon_title') or '')).casefold())
    brand_aliases=[brand] if len(brand)>=3 else []
    # Japanese importer names and retail brands can differ: include direct
    # model tokens and prominent product words, not manufacturer alone.
    tokens=[clean(v) for v in models if len(clean(v))>=4]
    tokens += [clean(v) for v in re.findall(r'[A-Za-z]{4,}(?:\s+\d+)?',
                       str(product.get('amazon_title') or '')) if len(clean(v))>=5]
    result=[]
    for entry in index['items']:
        searchable=clean(' '.join(str(entry.get(k) or '') for k in
                                  ('manufacturer','model_summary','category')))
        if any(v in searchable for v in brand_aliases+tokens):result.append(entry)
    return result


def detail(entry,*,session=None):
    session=session or requests.Session()
    url=entry['source']
    if not url.startswith(BASE+'/recall/detail/'):
        raise GovernmentFeedUnavailable('invalid official notice URL')
    try:
        response=session.get(url,timeout=18)
        response.raise_for_status()
        if not response.url.startswith(BASE+'/recall/detail/'):
            raise GovernmentFeedUnavailable('SAFE-Lite detail redirected outside official host')
        soup=BeautifulSoup(response.content,'html.parser')
        text=soup.get_text(' ',strip=True)
        if entry['id'] not in text or len(text)<100:
            raise GovernmentFeedUnavailable('incomplete SAFE-Lite detail')
        manufacturer_urls=[]
        for anchor in soup.select('a[href]'):
            href=str(anchor.get('href') or '')
            if not href.startswith('https://'):continue
            host=urlsplit(href).hostname or ''
            if not host.endswith(('.go.jp','nite.go.jp')) and href not in manufacturer_urls:
                manufacturer_urls.append(href)
        return {'id':entry['id'],'source':response.url,'text':text[:30000],
                'date':entry.get('date'),'manufacturer':entry.get('manufacturer'),
                'manufacturer_notice_candidates':manufacturer_urls[:5]}
    except (requests.RequestException,ValueError) as exc:
        raise GovernmentFeedUnavailable('official recall detail unreadable: '+str(exc)[:150]) from exc


DETAILS_PATH=STATE_ROOT/'government-recall-details.json'


def full_registry(*,index_path=INDEX_PATH,details_path=DETAILS_PATH,session=None,now=None,limit=None):
    """Entire official recall corpus, not top-N search results.

    First run backfills all notices; later runs fetch only new IDs. A partial
    backfill is saved for resumption but NEVER yields an all-clear verdict.
    """
    index=refresh(path=index_path,session=session,now=now)
    try:
        saved=json.loads(details_path.read_text(encoding='utf-8'))
        stored=saved.get('details',{}) if saved.get('schema')=='sellemy-nite-details/v1' else {}
    except (OSError,ValueError,TypeError):stored={}
    missing=[item for item in index['items'] if item['id'] not in stored]
    if limit is not None:missing=missing[:limit]
    errors=[]
    def fetch(row):
        try:
            # Requests Session is not shared across concurrent workers.
            return row['id'],detail(row,session=session if session is not None else requests.Session()),None
        except GovernmentFeedUnavailable as exc:
            return row['id'],None,str(exc)
    if missing:
        with ThreadPoolExecutor(max_workers=4) as executor:
            for identifier,notice,error in executor.map(fetch,missing):
                if error:errors.append(error)
                elif notice:stored[identifier]=notice
    details_path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=details_path.parent,delete=False) as handle:
        json.dump({'schema':'sellemy-nite-details/v1','details':stored},handle,ensure_ascii=False)
        tmp=Path(handle.name)
    tmp.replace(details_path)
    requested={item['id'] for item in index['items']}
    missing_ids=requested-set(stored)
    if missing_ids:
        raise GovernmentFeedUnavailable(f'official recall corpus incomplete: {len(missing_ids)}/{len(requested)} notices not loaded; '+ '; '.join(errors[:2]))
    return {'source':index['source'],'checked_at':index['checked_at'],
            'count':index['count'],'complete':True,
            'details':[stored[item['id']] for item in index['items']]}


def cached_registry(*,index_path=INDEX_PATH,details_path=DETAILS_PATH,now=None):
    """Use a complete government snapshot offline; reject stale/partial records."""
    now=now or datetime.now(timezone.utc)
    try:
        index=json.loads(index_path.read_text(encoding='utf-8'))
        saved=json.loads(details_path.read_text(encoding='utf-8'))
        if not index.get('complete') or saved.get('schema')!='sellemy-nite-details/v1':
            return None
        checked=datetime.fromisoformat(index['checked_at'])
        if checked<now-timedelta(hours=24):return None
        stored=saved['details'];rows=index['items']
        if len(rows)!=index['count'] or any(x['id'] not in stored for x in rows):return None
        return {'source':index['source'],'checked_at':index['checked_at'],
                'count':index['count'],'complete':True,
                'details':[stored[x['id']] for x in rows]}
    except (OSError,ValueError,KeyError,TypeError):return None
