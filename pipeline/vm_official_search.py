"""Retrieve URL candidates in the existing isolated VM Chrome, never a source receipt."""
from __future__ import annotations
import base64
import json
import re
import sys
from urllib.parse import quote_plus, urlparse, parse_qs
from playwright.sync_api import sync_playwright


def _candidate_urls(page):
    urls = []
    for anchor in page.locator('li.b_algo h2 a').all()[:14]:
        href = anchor.get_attribute('href') or ''
        parsed = urlparse(href)
        if parsed.netloc in {'www.bing.com','bing.com'} and parsed.path == '/ck/a':
            encoded = parse_qs(parsed.query).get('u',[''])[0]
            if not encoded.startswith('a1'):
                continue
            try:
                href = base64.urlsafe_b64decode(encoded[2:] + '===').decode('utf-8')
            except (ValueError,UnicodeDecodeError):
                continue
            parsed = urlparse(href)
        if parsed.scheme != 'https' or not parsed.hostname:
            continue
        hostname = parsed.hostname.lower()
        if hostname.endswith(('google.com','google.co.jp','bing.com','youtube.com','amazon.co.jp',
                              'rakuten.co.jp','yahoo.co.jp','facebook.com','instagram.com',
                              'x.com','tiktok.com','mercari.com')):
            continue
        if href not in urls:
            urls.append(href)
        if len(urls) == 4:
            break
    return urls


def main():
    products = json.load(sys.stdin)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, executable_path='/usr/bin/google-chrome')
        try:
            page = browser.new_page(locale='ja-JP')
            for item in products[:6]:
                query = ' '.join(filter(None,[str(item.get('brand') or ''),
                                                str(item.get('title') or '')[:95], '公式']))
                urls = []
                # A shorter brand/model query is attempted only when the full
                # title found nothing; it is still ordinary Chrome search.
                model = re.search(r'(?<![A-Za-z0-9])(?:[A-Za-z]{1,8}[- ]?)?[A-Za-z0-9-]*\d[A-Za-z0-9-]{2,}(?![A-Za-z0-9])',
                                  str(item.get('title') or ''))
                short_query = ' '.join(filter(None,[str(item.get('brand') or ''),
                        model.group(0) if model else str(item.get('title') or '')[:35], '公式']))
                for search in dict.fromkeys([query, short_query]):
                    try:
                        response = page.goto('https://www.bing.com/search?q='+quote_plus(search),
                                             wait_until='domcontentloaded', timeout=17000)
                        urls = _candidate_urls(page) if response and response.status == 200 else []
                    except Exception:
                        urls = []
                    if urls:
                        break
                results.append({'asin':item['asin'],'urls':urls})
        finally:
            browser.close()
    print(json.dumps({'items':results},ensure_ascii=False))


if __name__ == '__main__':
    main()
