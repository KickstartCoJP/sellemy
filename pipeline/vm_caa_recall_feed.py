"""Read one CAA government recall list page in isolated Chrome.

Each page is individually retried and verified so a transient 403 does not
throw away earlier successfully fetched official pages.
"""
from __future__ import annotations
import argparse,json,re,time
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ORIGIN='https://www.recall.caa.go.jp'


def _capture(page):
    return page.locator('table tbody tr').evaluate_all('''rows => rows.map(tr => {
        const td=Array.from(tr.querySelectorAll('td'));
        const a=td[2]?.querySelector('a[href*="detail.php"]');
        return {category:td[0]?.innerText.trim(),title:a?.innerText.trim(),
                url:a?.href,published:td[3]?.innerText.trim()};
    }).filter(row=>row.title&&row.url)''')


def read_page(page,screen,page_index):
    if screen not in ('02','03') or not 0<=page_index<100:
        raise ValueError('unsupported official page')
    uri=ORIGIN+'/result/index.php?screenkbn='+screen
    response=page.goto(uri,wait_until='domcontentloaded',timeout=18000)
    if not response or response.status!=200:raise RuntimeError('CAA HTTP unavailable')
    page.locator('#dataDisplay').select_option('60',timeout=9000)
    page.wait_for_selector('table tbody tr',timeout=20000)
    if page_index:
        page.evaluate('(index)=>goPageNum(index)',page_index)
        page.wait_for_selector('table tbody tr',timeout=22000)
    count=re.search(r'(\d+)件中',page.locator('body').inner_text())
    if not count:raise RuntimeError('CAA total count missing')
    total=int(count.group(1))
    rows=_capture(page)
    expected=max(0,min(60,total-page_index*60))
    if expected<=0 or len(rows)!=expected:
        raise RuntimeError(f'CAA truncated {screen}/{page_index}: {len(rows)}/{expected}')
    if any(urlsplit(r['url']).netloc!='www.recall.caa.go.jp' for r in rows):
        raise RuntimeError('CAA foreign recall URL')
    return {'source':'CAA','screen':screen,'page':page_index,'count':total,'rows':rows}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--screen',required=True,choices=['02','03'])
    parser.add_argument('--page',type=int,required=True)
    args=parser.parse_args()
    failures=[]
    with sync_playwright() as driver:
        browser=driver.chromium.launch(headless=False,executable_path='/usr/bin/google-chrome')
        try:
            page=browser.new_page(locale='ja-JP')
            for attempt in range(5):
                try:
                    value=read_page(page,args.screen,args.page)
                    print(json.dumps(value,ensure_ascii=False))
                    return
                except Exception as exc:
                    failures.append(str(exc)[:120])
                    if attempt<4:time.sleep(min(2*(attempt+1),8))
        finally:
            browser.close()
    raise RuntimeError('CAA official page failed after five attempts: '+'; '.join(failures))

if __name__=='__main__':main()
