"""Find manufacturer URL candidates in existing isolated Chrome; verify via existing source gate.

A search URL is never accepted as evidence. The established manufacturer identity
and HTTPS verification remain the only authority for source admission.
"""
from __future__ import annotations
import ipaddress
import json
import subprocess
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from product_sources import fetch_manufacturer_source


def _public_candidate(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme != 'https' or not host or parsed.username or parsed.password:
            return False
        if host in {'localhost'} or host.endswith(('.localhost','.local','.internal')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def _search_urls(products):
    """Bound Chrome lookups to six products per VM turn; no Codex/search tokens."""
    script = str(Path(__file__).with_name('vm_official_search.py'))
    found = {}
    for offset in range(0, len(products), 6):
        batch = products[offset:offset + 6]
        payload = [{'asin':p['asin'], 'brand':p.get('brand'), 'title':p.get('amazon_title')}
                   for p in batch]
        try:
            run = subprocess.run(['/opt/homebrew/bin/colima','ssh','--','xvfb-run','-a',
                '/home/kickstart.guest/.ai-role-playwright/bin/python',script],
                input=json.dumps(payload,ensure_ascii=False),text=True,
                capture_output=True,timeout=140,check=False)
            if run.returncode:
                continue
            rows = json.loads(run.stdout).get('items',[])
            allowed = {item['asin'] for item in batch}
            for row in rows:
                if not isinstance(row,dict) or row.get('asin') not in allowed:
                    continue
                urls = row.get('urls')
                if isinstance(urls,list):
                    found[row['asin']] = [url for url in urls
                                          if isinstance(url,str) and _public_candidate(url)][:4]
        except (OSError,subprocess.TimeoutExpired,ValueError,TypeError):
            continue
    return found


def research_selected(products, *, cache_only=False, prefer_official=False):
    if cache_only or not products:
        return products
    needs = [p for p in products[:30] if not any(
        isinstance(s,dict) and s.get('verified') is True and str(s.get('excerpt') or '').strip()
        and s.get('source_type') == 'manufacturer_official'
        for s in p.get('sources',[]))]
    if not needs:
        return products
    candidates = _search_urls(needs)
    def fetch(product):
        for url in candidates.get(product.get('asin'),[]):
            try:
                source = fetch_manufacturer_source(url,product)
            except Exception:
                source = None
            if source and source.get('verified') is True:
                return source
        return None
    with ThreadPoolExecutor(max_workers=6) as executor:
        sources = list(executor.map(fetch,needs))
    by_asin = {p['asin']:src for p,src in zip(needs,sources) if src}
    return [{**p,'sources':[*list(p.get('sources') or []),by_asin[p['asin']]],'provisional':False}
            if p.get('asin') in by_asin else p for p in products]
