"""Retrieve official recall URL candidates in the existing isolated Chrome VM."""
from __future__ import annotations
import json,sys
from urllib.parse import quote_plus
from vm_official_search import _candidate_urls
from playwright.sync_api import sync_playwright


def main():
    items=json.load(sys.stdin)
    results=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=False,executable_path='/usr/bin/google-chrome')
        try:
            page=browser.new_page(locale='ja-JP')
            for row in items[:6]:
                query=' '.join([str(row.get('brand') or '')[:50],str(row.get('model') or '')[:65],'リコール 自主回収'])
                try:
                    response=page.goto('https://www.bing.com/search?q='+quote_plus(query),
                                       wait_until='domcontentloaded',timeout=17000)
                    urls=_candidate_urls(page) if response and response.status==200 else []
                    results.append({'asin':row['asin'],'searched':True,'urls':urls})
                except Exception:
                    results.append({'asin':row['asin'],'searched':False,'urls':[]})
        finally:
            browser.close()
    print(json.dumps({'items':results},ensure_ascii=False))

if __name__=='__main__':main()
