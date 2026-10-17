"""Fetch attributable marketplace product text; absence remains absence."""
from html.parser import HTMLParser
import re
import unicodedata
from threading import BoundedSemaphore

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


def _manufacturer_excerpt(soup, brand, identity):
    """Keep verbatim product blocks from a page whose identity is already verified."""
    from bs4 import BeautifulSoup

    root = soup.select_one('main') or soup.select_one('article') or soup.body or soup
    content = BeautifulSoup(str(root), 'html.parser')
    for node in content.select('script, style, nav, header, footer, aside, form, noscript'):
        node.decompose()
    unrelated = re.compile(r'おすすめ|関連商品|こちらもチェック|人気商品|you may also like|related products|recommendations', re.I)
    for node in content.select('section, article, div'):
        if node.parent is None:
            continue
        label = ' '.join((node.get('id', ''), ' '.join(node.get('class', []))))
        heading = node.find(re.compile(r'^h[1-6]$'), recursive=False)
        if unrelated.search(label) or (heading and unrelated.search(heading.get_text(' ', strip=True))):
            node.decompose()
    blocks = []
    for node in content.find_all(re.compile(r'^h[1-6]$|^(p|li|dt|dd|tr)$')):
        if node.find_parent(['p', 'li', 'dt', 'dd', 'tr']):
            continue
        value = re.sub(r'\s+', ' ', node.get_text(' ', strip=True)).strip()
        if value and len(value) <= 1000 and value not in blocks:
            blocks.append(value)
    if not blocks:
        blocks = [re.sub(r'\s+', ' ', x).strip() for x in content.stripped_strings]
    # Keep the product heading and its adjacent facts; a model mention need not recur
    # in every specification row. The verified page, not search text, supplies all lines.
    excerpt = '\n'.join(blocks)
    return excerpt[:8000].rsplit('\n', 1)[0] if len(excerpt) > 8000 and '\n' in excerpt[:8000] else excerpt[:8000]


_ITEM_BROWSER_LIMIT = BoundedSemaphore(2)


def _fetch_item_via_browser(url):
    """Obtain a product page only when direct HTTP returned an access/error page."""
    from playwright.sync_api import sync_playwright
    with _ITEM_BROWSER_LIMIT:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                args=['--no-first-run'],
            )
            try:
                page = browser.new_page()
                response = page.goto(url, wait_until='domcontentloaded', timeout=25000)
                if response is None or response.status != 200:
                    return None
                return {'url': page.url, 'status_code': response.status, 'text': page.content()}
            finally:
                browser.close()


def fetch_amazon_item_source(asin, listing_title):
    """Return only text fetched from the matching HTTPS item page."""
    if not re.fullmatch(r'[A-Z0-9]{10}', asin or ''):
        return None
    url = f'https://www.amazon.co.jp/dp/{asin}'
    try:
        response = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
        direct = {'url': response.url, 'status_code': response.status_code, 'text': response.text}
    except requests.RequestException:
        direct = None
    valid_url = lambda value: bool(re.match(
        r'^https://www\.amazon\.co\.jp/(?:dp|gp/product)/' + re.escape(asin) + r'(?:[/?#]|$)',
        str(value or ''), re.I))
    if direct is not None and not valid_url(direct['url']):
        return None
    usable = bool(direct and direct['status_code'] == 200 and
                  re.search(r'(?<![A-Z0-9])' + re.escape(asin) + r'(?![A-Z0-9])', direct['text'], re.I) and
                  re.search(r'id=["\']productTitle["\']', direct['text'], re.I))
    if not usable:
        try:
            direct = _fetch_item_via_browser(url)
        except Exception:
            return None
    if not direct or direct['status_code'] != 200 or not valid_url(direct['url']):
        return None
    body = direct['text']
    if not re.search(r'(?<![A-Z0-9])' + re.escape(asin) + r'(?![A-Z0-9])', body, re.I):
        return None
    # Exclude page scripts and CSS from the attribution excerpt, retaining the real item fields.
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, 'html.parser')
    identifiers = ('productTitle', 'feature-bullets', 'productDetails_detailBullets_sections1',
                   'productDetails_techSpec_section_1', 'productOverview_feature_div', 'bylineInfo')
    nodes = [soup.find(id=identifier) for identifier in identifiers]
    nodes = [node for node in nodes if node is not None]
    if not nodes or nodes[0].get('id') != 'productTitle':
        return None
    parts = []
    for node in nodes:
        for unwanted in node.select('script, style, noscript'):
            unwanted.decompose()
        parts.append(str(node))
    clean_body = ''.join(parts)
    parser = _ItemText()
    parser.feed(clean_body)
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
    return {'url': direct['url'], 'title': title, 'excerpt': excerpt,
            'source_type': 'marketplace_item', 'http_status': direct['status_code'],
            'verified': True, 'brand': brand}


def fetch_manufacturer_source(url, product):
    """Accept a search candidate only when the actual HTTPS page proves product identity."""
    from urllib.parse import urlsplit
    from bs4 import BeautifulSoup

    def site_name(host):
        labels = host.lower().split('.')
        return labels[-3] if len(labels) >= 3 and labels[-2:] in (['co', 'jp'], ['com', 'jp']) else labels[-2] if len(labels) >= 2 else ''

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
    excerpt_soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    ownership_text = ' '.join(x.get_text(' ', strip=True) for x in soup.select('main, footer'))
    footer_text = ' '.join(x.get_text(' ', strip=True) for x in soup.select('footer'))
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
    # Require a distinctive product identity and independent manufacturer attribution.
    title_tokens = re.findall(r'[A-Za-z][A-Za-z0-9-]{3,}|[0-9]+[A-Za-z][A-Za-z0-9-]*|[ァ-ヶー]{4,}|[一-龥]{3,}',
                              unicodedata.normalize('NFKC', product.get('amazon_title', '')))
    distinctive = [t for t in title_tokens if len(_normalized(t)) >= 5 and _normalized(t) != brand]
    identity = next((t for t in distinctive if _normalized(t) in normalized), None)
    if brand and brand not in _normalized(body + ' ' + image_text):
        return None
    site = _normalized(site_name(urlsplit(response.url).hostname))
    domain_brand = bool(brand and len(brand) >= 3 and brand in site)
    official_title = bool(brand and brand in _normalized(title) and
                          re.search(r'公式|official', title, re.I))
    # Corporate footers can use legal-name syntax without a labeled field.
    corporate_footer = bool(re.search(r'株式会社|(?:co\.?\s*,?\s*ltd\.?|corporation|inc\.?|合同会社)', footer_text, re.I))
    footer_brand = bool(brand and brand in _normalized(footer_text))
    # Japanese legal names sometimes transliterate a Roman brand (UGREEN -> ユーグリーン).
    romanized_brand = {'ugreen': 'ユーグリーン'}
    footer_brand = footer_brand or bool(brand in romanized_brand and
                                         _normalized(romanized_brand[brand]) in _normalized(footer_text))
    corporate_relation = bool(corporate_footer and footer_brand and official_title)
    if not identity:
        # Short model names need two separate exact product tokens, with stronger ownership proof.
        page_text = unicodedata.normalize('NFKC', body).lower()
        short_tokens = {t.lower() for t in title_tokens if len(_normalized(t)) >= 3
                        and _normalized(t) != brand}
        matches = [t for t in short_tokens if re.search(
            r'(?<![a-z0-9])' + re.escape(t) + r'(?![a-z0-9])', page_text)]
        if not (len(matches) >= 2 and domain_brand and corporate_relation):
            return None
        identity = matches[0]
    attribution = bool(re.search(r'(?:製造販売元|販売元|会社名|製造元|メーカー|manufacturer|brand owner)\s*[:：]\s*[^\s:：]{2,}', ownership_text, re.I))
    if not brand:
        # Without a listing brand, a generic product name on an arbitrary shop is insufficient.
        # A model code plus a named company and its matching domain establishes the association.
        model = next((t for t in title_tokens if re.search(r'[A-Za-z]', t) and re.search(r'\d', t)
                      and len(_normalized(t)) >= 5 and _normalized(t) in normalized), None)
        host_label = urlsplit(response.url).hostname.lower().split('.')[-2]
        company = re.search(r'(?:製造販売元|販売元|会社名|製造元|メーカー|manufacturer|brand owner)\s*[:：]\s*([^\s:：]{2,})', ownership_text, re.I)
        company_text = _normalized(company.group(1)) if company else ''
        if not (model and company and len(host_label) >= 4 and
                _normalized(host_label) in company_text + _normalized(body + ' ' + title)):
            return None
    if brand and not (domain_brand or corporate_relation):
        return None
    if not (domain_brand or corporate_relation or (not brand and attribution)):
        return None
    excerpt = _manufacturer_excerpt(excerpt_soup, brand, identity)
    if not excerpt:
        return None
    return {'url': response.url, 'title': title, 'excerpt': excerpt,
            'source_type': 'manufacturer_official', 'http_status': 200, 'verified': True}
