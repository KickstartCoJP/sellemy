from __future__ import annotations
import html
import urllib.parse
from datetime import date

from payload_schema import validate_payload, validate_evidence, match_refs

BASE = 'https://www.sellemy.jp'
RAK = 'https://hb.afl.rakuten.co.jp/hgc/47664979.04b12aff.4766497a.9509b1ad/?pc='
YAH = 'https://ck.jp.ap.valuecommerce.com/servlet/referral?sid=2770133&pid=891600365&vc_url='

TIER_LABELS = (
    ('lowrange', '手頃な価格帯から選ぶ', '#lowrange', '手頃な価格帯'),
    ('midrange', '機能と価格のバランスで選ぶ', '#midrange', 'バランス重視'),
    ('highrange', '機能性を重視して選ぶ', '#highrange', '機能性重視'),
)

# Renderer inserts Writer-supplied prose and evidence-derived facts verbatim; it never
# generates, pads, or paraphrases public-facing copy. The only text authored here is
# fixed site chrome shared verbatim across every article on the site (nav labels,
# breadcrumb, footer, TOC/section labels) -- never a product- or article-specific claim.


def _affiliate_links(amazon_title: str, asin: str, amazon_tag: str):
    amazon_url = f'https://www.amazon.co.jp/dp/{asin}?tag={amazon_tag}'
    rakuten_url = RAK + urllib.parse.quote('https://search.rakuten.co.jp/search/mall/' + amazon_title + '/', safe='')
    yahoo_url = YAH + urllib.parse.quote('https://shopping.yahoo.co.jp/search?p=' + amazon_title, safe='')
    return amazon_url, rakuten_url, yahoo_url


def _render_card(product_payload: dict, product_evidence: dict, amazon_tag: str) -> str:
    h3 = html.escape(product_payload['h3'])
    description = html.escape(product_payload['description'])
    amazon_url, rakuten_url, yahoo_url = _affiliate_links(
        product_evidence['amazon_title'], product_evidence['asin'], amazon_tag
    )
    image_url = html.escape(product_evidence['image_url'])
    return (
        f'<div class="item-card" data-product-id="{html.escape(product_evidence["product_id"])}" '
        f'data-asin="{html.escape(product_evidence["asin"])}">'
        f'<img src="{image_url}" alt="{h3}">'
        f'<h3>{h3}</h3><p>{description}</p>'
        f'<div class="links">'
        f'<a href="{html.escape(amazon_url)}" target="_blank" rel="nofollow" class="link-button">Amazon</a>'
        f'<a href="{html.escape(rakuten_url)}" target="_blank" rel="nofollow" class="link-button">楽天</a>'
        f'<a href="{html.escape(yahoo_url)}" target="_blank" rel="nofollow" class="link-button">Yahoo</a>'
        f'</div></div>'
    )


def render_article(payload: dict, evidence: dict, *, today: date | None = None) -> str:
    """Render one article's HTML from a Writer payload plus its evidence file.

    Fails closed (raises PayloadValidationError) if required prose or facts are
    missing -- it never substitutes filler text to make the page look complete.
    """
    validate_payload(payload)
    validate_evidence(evidence)
    match_refs(payload, evidence)

    year = (today or date.today()).year
    h1 = html.escape(payload['h1'])
    lead = html.escape(payload['lead'])
    summary = html.escape(payload['summary'])
    how_to_choose = html.escape(payload['how_to_choose'])
    conclusion = html.escape(payload['conclusion'])
    canonical = html.escape(evidence['canonical_url'])
    eyecatch = html.escape(evidence['eyecatch_image'])
    category = html.escape(evidence['category'])
    amazon_tag = evidence['amazon_tag']

    payload_by_ref = {p['ref']: p for p in payload['products']}
    evidence_by_ref = {p['ref'] for p in evidence['products']}
    evidence_products = {p['ref']: p for p in evidence['products']}
    products_by_tier = {'lowrange': [], 'midrange': [], 'highrange': []}
    for ref in sorted(evidence_by_ref):
        ev = evidence_products[ref]
        products_by_tier[ev['tier']].append(ref)

    sections = []
    for tier, heading, anchor, _ in TIER_LABELS:
        angle = html.escape(payload['comparison_angles'][tier])
        cards = ''.join(
            _render_card(payload_by_ref[ref], evidence_products[ref], amazon_tag)
            for ref in products_by_tier[tier]
        )
        sections.append(
            f'<section id="{tier}"><div class="section-title"><h2>{heading}</h2></div>'
            f'<p class="comparison-angle">{angle}</p>'
            f'<div class="item-list">{cards}</div></section>'
        )

    toc_items = ''.join(f'<li><a href="{anchor}">{label}</a></li>' for _, _, anchor, label in TIER_LABELS)

    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0" />'
        f'<title>{h1} - Sellemy</title>'
        f'<meta name="description" content="{summary}" />'
        f'<meta property="og:title" content="{h1} - Sellemy" />'
        f'<meta property="og:description" content="{summary}" />'
        f'<meta property="og:image" content="{eyecatch}" />'
        '<meta property="og:type" content="article" />'
        f'<meta property="og:url" content="{canonical}" />'
        f'<link rel="stylesheet" href="{BASE}/css/style.css" />'
        '<script async src="https://www.googletagmanager.com/gtag/js?id=G-SZ5RQR5H7L"></script>'
        '<script src="/js/ga4.js"></script>'
        f'<link rel="canonical" href="{canonical}" /></head>'
        f'<body class="article-detail" data-category="{category}">'
        '<header class="site-header"><div class="brand"><a href="/index.html">'
        '<img src="/img/sellemy-logo.png" alt="Sellemyロゴ" class="logo" /></a>'
        '<span class="tagline">選びやすくて、わたしにちょうどいい情報ガイド</span></div></header>'
        '<main><nav class="breadcrumb"><ul><li><a href="/index.html">Top</a></li>'
        '<li><a href="/article/gadget/">家電</a></li>'
        f'<li><span>{h1}</span></li></ul></nav>'
        f'<div class="section-title"><h1>{h1}</h1></div>'
        f'<div class="item-eyecatch"><img src="{eyecatch}" alt="{h1}" /></div>'
        f'<p class="lead">{lead}</p><p class="summary">{summary}</p>'
        f'<nav class="toc"><strong>価格帯から選ぶ</strong><ul>{toc_items}</ul></nav>'
        + ''.join(sections) +
        '<section id="how-to-choose"><div class="section-title"><h2>選ぶときのポイント</h2></div>'
        f'<p>{how_to_choose}</p><p class="conclusion">{conclusion}</p></section>'
        f'</main><footer><p>&copy; {year} Sellemy. All rights reserved.</p></footer>'
        '</body></html>'
    )
