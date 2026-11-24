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
    def save_progress():
        details_path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=details_path.parent,delete=False) as handle:
            json.dump({'schema':'sellemy-nite-details/v1','details':stored},handle,ensure_ascii=False)
            tmp=Path(handle.name)
        tmp.replace(details_path)
    def fetch(row):
        for attempt in range(3):
            try:
                # Requests Session is not shared across concurrent workers.
                return row['id'],detail(row,session=session if session is not None else requests.Session()),None
            except GovernmentFeedUnavailable as exc:
                error=str(exc)
        return row['id'],None,error
    for start in range(0,len(missing),40):
        chunk=missing[start:start+40]
        with ThreadPoolExecutor(max_workers=6) as executor:
            for identifier,notice,error in executor.map(fetch,chunk):
                if error:errors.append(error)
                elif notice:stored[identifier]=notice
        save_progress()
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

CAA_BASE='https://www.recall.caa.go.jp'
CAA_INDEX_PATH=STATE_ROOT/'government-caa-recalls.json'


CAA_PARTIAL_PATH=STATE_ROOT/'government-caa-recalls-pages.json'


def caa_registry(*,path=CAA_INDEX_PATH,now=None,force=False,runner=None,
                 partial_path=CAA_PARTIAL_PATH,script_path=None):
    """Read all official priority/new rows with resumable, exact page counts."""
    now=now or datetime.now(timezone.utc)
    try:
        prior=json.loads(path.read_text(encoding='utf-8'))
        updated=datetime.fromisoformat(prior['checked_at'])
        if (prior.get('complete') and len(prior.get('details',[]))==prior.get('count')
                and updated>=now-timedelta(hours=24) and not force):
            return prior
    except (OSError,KeyError,ValueError,TypeError):pass
    import subprocess
    script=script_path or Path(__file__).with_name('vm_caa_recall_feed.py')
    runner=runner or subprocess.run
    try:
        progress=json.loads(partial_path.read_text(encoding='utf-8'))
        if datetime.fromisoformat(progress['started_at'])<now-timedelta(hours=8):
            progress={}
    except (OSError,ValueError,KeyError,TypeError):progress={}
    pages=progress.get('pages',{})
    def fetch(screen,page_index):
        cmd=['/opt/homebrew/bin/colima','ssh','--','xvfb-run','-a',
             '/home/kickstart.guest/.ai-role-playwright/bin/python',str(script),
             '--screen',screen,'--page',str(page_index)]
        try:
            outcome=runner(cmd,capture_output=True,text=True,check=False,timeout=180)
            if outcome.returncode:
                raise GovernmentFeedUnavailable('CAA browser error: '+outcome.stderr[-260:])
            result=json.loads(outcome.stdout)
            total=int(result['count']);rows=result['rows']
            if (result['screen']!=screen or result['page']!=page_index or
                    not isinstance(rows,list) or
                    len(rows)!=max(0,min(60,total-page_index*60))):
                raise GovernmentFeedUnavailable('CAA incomplete returned page')
            return result
        except (OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as exc:
            raise GovernmentFeedUnavailable('CAA page retrieval failed: '+str(exc)[:240]) from exc
    def checkpoint():
        partial_path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=partial_path.parent,delete=False) as handle:
            json.dump({'started_at':progress.setdefault('started_at',now.isoformat()),
                       'pages':pages},handle,ensure_ascii=False)
            tmp=Path(handle.name)
        tmp.replace(partial_path)
    for screen in ('02','03'):
        # Always recheck page zero: it controls the official total count.
        first=fetch(screen,0)
        count=first['count']
        keys=[k for k in pages if k.startswith(screen+':')]
        # A same-size official list can still have new or amended notices.
        # Never combine yesterday's later pages with a changed first page.
        prior_first=pages.get(screen+':0') or {}
        if (any(pages[k].get('count')!=count for k in keys)
                or prior_first.get('rows')!=first['rows']):
            for k in keys:pages.pop(k,None)
        pages[screen+':0']=first
        checkpoint()
        for page_index in range(1,(count+59)//60):
            key=f'{screen}:{page_index}'
            if key not in pages:
                result=fetch(screen,page_index)
                if result['count']!=count:
                    for k in list(pages):
                        if k.startswith(screen+':'):pages.pop(k,None)
                    checkpoint()
                    raise GovernmentFeedUnavailable('CAA count changed during collection')
                pages[key]=result
                checkpoint()
        items=[row for page_index in range((count+59)//60)
               for row in pages[f'{screen}:{page_index}']['rows']]
        if len(items)!=count or len({x['url'] for x in items})!=count:
            raise GovernmentFeedUnavailable('CAA duplicate/missing records across pages')
    notices={}
    for page in pages.values():
        for row in page['rows']:
            url=row.get('url') or ''
            if not url.startswith(CAA_BASE+'/result/detail.php?') or not row.get('title'):
                raise GovernmentFeedUnavailable('CAA invalid official URL')
            notices[url]={'id':'caa-'+url.split('rcl=',1)[-1].split('&',1)[0],
                          'source':url,'text':row['title'],'date':row.get('published'),
                          'manufacturer':'','source_type':'CAA',
                          'category':row.get('category')}
    try:
        previous=json.loads(path.read_text(encoding='utf-8'))
        if previous.get('complete') is True:
            for old in previous.get('details',[]):
                if isinstance(old,dict) and old.get('source'):
                    notices.setdefault(old['source'],old)
    except (OSError,ValueError,TypeError):pass
    result={'schema':'sellemy-caa-official-recalls/v1',
            'source':CAA_BASE+'/result/index.php',
            'checked_at':now.isoformat(),'complete':True,
            'count':len(notices),'details':list(notices.values())}
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as handle:
        json.dump(result,handle,ensure_ascii=False)
        tmp=Path(handle.name)
    tmp.replace(path)
    partial_path.unlink(missing_ok=True)
    return result


def combined_registry(*,now=None):
    """NITE full notice corpus + CAA priority/new official registrations."""
    nite=full_registry(now=now)
    caa=caa_registry(now=now)
    return {'source':[nite['source'],caa['source']],
            'checked_at':min(nite['checked_at'],caa['checked_at']),
            'complete':True,'count':nite['count']+caa['count'],
            'source_counts':{'NITE':nite['count'],'CAA':caa['count']},
            'details':nite['details']+caa['details']}


def cached_combined_registry(*,now=None):
    nite=cached_registry(now=now)
    if nite is None:return None
    now=now or datetime.now(timezone.utc)
    try:
        caa=json.loads(CAA_INDEX_PATH.read_text(encoding='utf-8'))
        updated=datetime.fromisoformat(caa['checked_at'])
        if (not caa['complete'] or len(caa['details'])!=caa['count'] or
                updated<now-timedelta(hours=24)):
            return None
        return {'source':[nite['source'],caa['source']],
                'checked_at':min(nite['checked_at'],caa['checked_at']),
                'complete':True,'count':nite['count']+caa['count'],
                'source_counts':{'NITE':nite['count'],'CAA':caa['count']},
                'details':nite['details']+caa['details']}
    except (OSError,ValueError,KeyError,TypeError):return None


def last_complete_registry(*,now=None,max_age_days=7):
    """Bounded outage fallback; preserves last validated official snapshot.

    Never creates a new PASS merely from a failed request. Consumers receive
    explicit stale evidence and the independent daily monitor keeps retrying.
    """
    now=now or datetime.now(timezone.utc)
    try:
        index=json.loads(INDEX_PATH.read_text(encoding='utf-8'))
        stored=json.loads(DETAILS_PATH.read_text(encoding='utf-8'))
        caa=json.loads(CAA_INDEX_PATH.read_text(encoding='utf-8'))
        nite_rows=index['items'];details=stored['details']
        if (not index['complete'] or not caa['complete'] or
            len(nite_rows)!=index['count'] or
            any(row['id'] not in details for row in nite_rows) or
            len(caa['details'])!=caa['count']):
            return None
        checked=min(datetime.fromisoformat(index['checked_at']),
                    datetime.fromisoformat(caa['checked_at']))
        if checked>now or checked<now-timedelta(days=max_age_days):return None
        return {'source':[index['source'],caa['source']],
                'checked_at':checked.isoformat(),'stale':True,'complete':True,
                'count':index['count']+caa['count'],
                'source_counts':{'NITE':index['count'],'CAA':caa['count']},
                'details':[details[row['id']] for row in nite_rows]+caa['details']}
    except (OSError,ValueError,KeyError,TypeError):return None
