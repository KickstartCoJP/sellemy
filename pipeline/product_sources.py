"""Fetch attributable marketplace product text; absence remains absence."""
from html.parser import HTMLParser
import re
import unicodedata

import requests


class _ItemText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = None
        self.depth = 0
        self.parts = []
        self.lines = []
        self.brand_lines = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.active is not None:
            self.depth += 1
        elif attributes.get('id') in {'productTitle', 'feature-bullets', 'productDetails_detailBullets_sections1', 'productDetails_techSpec_section_1', 'productOverview_feature_div', 'bylineInfo'}:
            self.active = attributes['id']
            self.depth = 1
        if self.active is not None and tag in {'li', 'tr', 'br'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if self.active is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if self.active is None:
            return
        self.depth -= 1
        if self.depth == 0:
            value = re.sub(r'[ \t]+', ' ', ''.join(self.parts))
            self.lines.extend(x.strip() for x in value.splitlines() if x.strip())
            if self.active in {'productOverview_feature_div', 'bylineInfo'}:
                self.brand_lines.extend(x.strip() for x in value.splitlines() if x.strip())
            self.active = None
            self.parts = []


def _normalized(value):
    return re.sub(r'[^a-z0-9ぁ-んァ-ヶ一-龥]', '', unicodedata.normalize('NFKC', value).lower())


def fetch_amazon_item_source(asin, listing_title):
    """Return only text fetched from the matching HTTPS item page."""
    if not re.fullmatch(r'[A-Z0-9]{10}', asin or ''):
        return None
    url = f'https://www.amazon.co.jp/dp/{asin}'
    try:
        response = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
    except requests.RequestException:
        return None
    if (not re.match(r'^https://www\.amazon\.co\.jp/(?:dp|gp/product)/' + re.escape(asin) + r'(?:[/?#]|$)', response.url, re.I)
            or response.status_code != 200):
        return None
    body = response.text
    # Amazon search/challenge pages and cross-ASIN redirects cannot establish identity.
    if not re.search(r'(?<![A-Z0-9])' + re.escape(asin) + r'(?![A-Z0-9])', body, re.I):
        return None
    parser = _ItemText()
    parser.feed(body)
    if not parser.lines:
        return None
    title = parser.lines[0]
    normalized_title = _normalized(title)
    listing = _normalized(listing_title)
    if len(normalized_title) < 8 or not any(normalized_title[i:i+8] in listing for i in range(len(normalized_title) - 7)):
        return None
    excerpt = '\n'.join(parser.lines)[:6000]
    brand = ''
    for index, line in enumerate(parser.brand_lines):
        match = re.search(r'(?:ブランド|Brand)\s*[:：]?\s*(.+)', line, re.I)
        if match:
            brand = match.group(1).strip()
            break
        if re.fullmatch(r'ブランド|Brand', line, re.I) and index + 1 < len(parser.brand_lines):
            brand = parser.brand_lines[index + 1]
            break
        match = re.match(r'(.+?)のストアを表示', line)
        if match:
            brand = match.group(1).strip()
            break
    return {'url': response.url, 'title': title, 'excerpt': excerpt,
            'source_type': 'marketplace_item', 'http_status': response.status_code,
            'verified': True, 'brand': brand}


def fetch_manufacturer_source(url, product):
    """Accept a search candidate only when the actual HTTPS page proves product identity."""
    from urllib.parse import urlsplit
    from bs4 import BeautifulSoup

    def allowed(value):
        parts = urlsplit(str(value))
        host = (parts.hostname or '').lower()
        return (parts.scheme == 'https' and host and not host.endswith((
            'amazon.co.jp', 'rakuten.co.jp', 'yahoo.co.jp', 'shopping.yahoo.co.jp',
            'qoo10.jp', 'wowma.jp', 'mercari.com', 'shopify.com', 'myshopify.com', 'stores.jp', 'base.shop',
            'shop-pro.jp', 'makeshop.jp'))
            and not any(x in host for x in ('amazon.', 'rakuten.', 'marketplace')))

    if not allowed(url):
        return None
    try:
        response = requests.get(url, timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
    except requests.RequestException:
        return None
    if response.status_code != 200 or not allowed(response.url):
        return None
    # Cross-domain redirects cannot prove that the researched official URL is still official.
    if urlsplit(url).hostname.lower() != urlsplit(response.url).hostname.lower():
        return None
    soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    ownership_text = ' '.join(x.get_text(' ', strip=True) for x in soup.select('main, footer'))
    image_text = ' '.join(x.get('alt', '') for x in soup.select('main img'))
    for node in soup(['script', 'style', 'nav', 'footer', 'header']):
        node.decompose()
    lines = [re.sub(r'\s+', ' ', x).strip() for x in soup.stripped_strings]
    lines = [x for x in lines if x and len(x) <= 500]
    body = '\n'.join(lines)
    title = title or (lines[0] if lines else '')
    listing = _normalized(product.get('amazon_title', ''))
    brand = _normalized(product.get('brand', ''))
    normalized = _normalized(body)
    # Require a distinctive product title segment, brand, and manufacturer attribution.
    title_tokens = re.findall(r'[A-Za-z][A-Za-z0-9-]{3,}|[ァ-ヶー]{4,}|[一-龥]{3,}',
                              unicodedata.normalize('NFKC', product.get('amazon_title', '')))
    distinctive = [t for t in title_tokens if len(_normalized(t)) >= 5 and _normalized(t) != brand]
    identity = next((t for t in distinctive if _normalized(t) in normalized), None)
    if not identity or not brand or brand not in _normalized(body + ' ' + image_text):
        return None
    official_title = bool(re.search(r'[【\[〖]公式[】\]〗]', title) and re.search(r'SOLIA SHOP|'+re.escape(product.get('brand', '')), title, re.I))
    attribution = bool(re.search(r'(?:製造販売元|販売元|会社名|製造元|メーカー|manufacturer|brand owner)\s*[:：]\s*[^\s:：]{2,}', ownership_text, re.I))
    if not (official_title or attribution):
        return None
    excerpt = '\n'.join(x for x in lines if brand in _normalized(x) or _normalized(identity) in _normalized(x)
                        or re.search(r'メーカー|製造販売元|販売元|会社名|製造元|manufacturer|brand owner|内容量|成分|容量|使用方法|洗浄|泡|頭皮|250g', x, re.I))[:6000]
    if not excerpt:
        return None
    return {'url': response.url, 'title': title, 'excerpt': excerpt,
            'source_type': 'manufacturer_official', 'http_status': 200, 'verified': True}
