from __future__ import annotations

import html
import urllib.parse
from datetime import date

from category_metadata import get_category
from payload_schema import validate_payload, validate_evidence, match_refs

BASE = 'https://www.sellemy.jp'
RAK = 'https://hb.afl.rakuten.co.jp/hgc/47664979.04b12aff.4766497a.9509b1ad/?pc='
YAH = 'https://ck.jp.ap.valuecommerce.com/servlet/referral?sid=2770133&pid=891600365&vc_url='


def _external_search_key(product_payload: dict, product_evidence: dict) -> str:
    brand = str(product_evidence.get('brand') or '').replace('のストアを表示', '').replace('ブランド:', '').replace('ブランド：', '').strip(' ：:')
    name = str(product_payload.get('h3') or '').strip()
    key = f'{brand} {name}'.strip()
    key = ''.join(ch if (ch.isalnum() or '\u3040' <= ch <= '\u30ff' or '\u3400' <= ch <= '\u9fff') else ' ' for ch in key)
    return ' '.join(key.split())[:40].strip()


def _affiliate_links(search_key: str, asin: str, amazon_tag: str):
    amazon_url = f'https://www.amazon.co.jp/dp/{asin}?tag={amazon_tag}'
    rakuten_url = RAK + urllib.parse.quote('https://search.rakuten.co.jp/search/mall/' + search_key + '/', safe='')
    yahoo_url = YAH + urllib.parse.quote('https://shopping.yahoo.co.jp/search?p=' + search_key, safe='')
    return amazon_url, rakuten_url, yahoo_url


def _render_card(product_payload: dict, product_evidence: dict, amazon_tag: str) -> str:
    h3 = html.escape(product_payload['h3'])
    description = html.escape(product_payload['description'])
    search_key = _external_search_key(product_payload, product_evidence)
    amazon_url, rakuten_url, yahoo_url = _affiliate_links(search_key, product_evidence['asin'], amazon_tag)
    return (
        f'<div class="item-card" data-product-id="{html.escape(product_evidence["product_id"])}" data-asin="{html.escape(product_evidence["asin"])}">'
        f'<img src="{html.escape(product_evidence["image_url"])}" alt="{h3}"><h3>{h3}</h3><p>{description}</p>'
        f'<div class="links"><a href="{html.escape(amazon_url)}" target="_blank" rel="nofollow" class="link-button">Amazon</a>'
        f'<a href="{html.escape(rakuten_url)}" target="_blank" rel="nofollow" class="link-button">楽天</a>'
        f'<a href="{html.escape(yahoo_url)}" target="_blank" rel="nofollow" class="link-button">Yahoo</a></div></div>'
    )


def render_article(payload: dict, evidence: dict, *, today: date | None = None) -> str:
    """Insert Writer prose and evidence facts verbatim; never synthesize or pad copy."""
    validate_payload(payload)
    validate_evidence(evidence)
    match_refs(payload, evidence)
    category = get_category(evidence['category'])
    year = (today or date.today()).year
    h1, summary = html.escape(payload['h1']), html.escape(payload['summary'])
    canonical, eyecatch = html.escape(evidence['canonical_url']), html.escape(evidence['eyecatch_image'])
    payload_by_ref = {p['ref']: p for p in payload['products']}
    evidence_by_ref = {p['ref']: p for p in evidence['products']}
    sections, toc = [], []
    for group in payload['comparison_groups']:
        group_id, title = html.escape(group['id']), html.escape(group['title'])
        toc.append(f'<li><a href="#{group_id}">{title}</a></li>')
        cards = ''.join(_render_card(payload_by_ref[ref], evidence_by_ref[ref], evidence['amazon_tag']) for ref in group['product_refs'])
        sections.append(f'<section id="{group_id}"><div class="section-title"><h2>{title}</h2></div><p class="comparison-angle">{html.escape(group["angle"])}</p><div class="item-list">{cards}</div></section>')
    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />'
        f'<title>{h1} - Sellemy</title><meta name="description" content="{summary}" /><meta property="og:title" content="{h1} - Sellemy" />'
        f'<meta property="og:description" content="{summary}" /><meta property="og:image" content="{eyecatch}" /><meta property="og:type" content="article" />'
        f'<meta property="og:url" content="{canonical}" /><link rel="stylesheet" href="{BASE}/css/style.css" />'
        '<script async src="https://www.googletagmanager.com/gtag/js?id=G-SZ5RQR5H7L"></script><script src="/js/ga4.js"></script>'
        f'<link rel="canonical" href="{canonical}" /></head><body class="article-detail" data-category="{category.slug}">'
        '<header class="site-header"><div class="brand"><a href="/index.html"><img src="/img/sellemy-logo.png" alt="Sellemyロゴ" class="logo" /></a>'
        '<span class="tagline">選びやすくて、わたしにちょうどいい情報ガイド</span></div></header><main>'
        f'<nav class="breadcrumb"><ul><li><a href="/index.html">Top</a></li><li><a href="{category.article_path}">{category.label}</a></li><li><span>{h1}</span></li></ul></nav>'
        f'<div class="section-title"><h1>{h1}</h1></div><div class="item-eyecatch"><img src="{eyecatch}" alt="{h1}" /></div>'
        f'<p class="lead">{html.escape(payload["lead"])}</p><p class="summary">{summary}</p><nav class="toc"><strong>比較軸から選ぶ</strong><ul>{"".join(toc)}</ul></nav>'
        + ''.join(sections)
        + f'<section id="how-to-choose"><div class="section-title"><h2>選ぶときのポイント</h2></div><p>{html.escape(payload["how_to_choose"])}</p><p class="conclusion">{html.escape(payload["conclusion"])}</p></section>'
        f'</main><footer><p>&copy; {year} Sellemy. All rights reserved.</p></footer></body></html>'
    )
