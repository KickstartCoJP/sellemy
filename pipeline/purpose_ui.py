"""Reusable, server-rendered Purpose navigation and article-card markup."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ROOT / 'config' / 'purpose_features_seed.json'
START = '<!-- PURPOSE_RELATED_FEATURES_START -->'
END = '<!-- PURPOSE_RELATED_FEATURES_END -->'
NAV_ITEMS = (
    ('/article/beauty/', '美容'),
    ('/article/dailygoods/', '日用品'),
    ('/article/gadget/', '家電'),
    ('/categories/', 'テーマ'),
    ('/themes/', '特集'),
    ('/articles.html', '記事一覧'),
    ('/products.html', '商品一覧'),
)


def nav_for_path(path: str = '') -> str:
    """Single ordered navigation shared by article, genre, theme and feature pages."""
    from urllib.parse import urlsplit
    pathname = urlsplit(path).path
    if pathname.startswith('/article/beauty/') and '/topics/' not in pathname and pathname.endswith(('/index.html', '/')):
        current = '/article/beauty/'
    elif pathname.startswith('/article/dailygoods/') and '/topics/' not in pathname and pathname.endswith(('/index.html', '/')):
        current = '/article/dailygoods/'
    elif pathname.startswith('/article/gadget/') and '/topics/' not in pathname and pathname.endswith(('/index.html', '/')):
        current = '/article/gadget/'
    elif pathname.startswith('/categories/'):
        current = '/categories/'
    elif pathname.startswith('/themes/'):
        current = '/themes/'
    elif pathname == '/articles.html':
        current = pathname
    elif pathname == '/products.html':
        current = pathname
    else:
        current = ''
    links = ''.join(
        '<li><a href="' + url + '" class="nav-link' + (' active' if url == current else '') + '"' +
        (' aria-current="page"' if url == current else '') + '>' + name + '</a></li>'
        for url, name in NAV_ITEMS
    )
    return '<nav class="site-nav"><ul class="nav-links">' + links + '</ul></nav>'


NAV = nav_for_path()
NAV_LINK = '<li><a href="/themes/" class="nav-link">特集</a></li>'


def ensure_theme_nav(page: str, page_path: str | None = None) -> str:
    """Normalize site links and order; preserve only the navigation element."""
    if not re.search(r'<header\b[^>]*class=["\']site-header["\']', page):
        return page
    if page_path is None:
        canonical = re.search(r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', page)
        page_path = canonical.group(1) if canonical else ''
        if not page_path:
            category = re.search(r'<body\b[^>]*data-category=["\']([^"\']+)', page)
            value = category.group(1) if category else ''
            page_path = {'articles': '/articles.html', 'products': '/products.html',
                         'themes': '/themes/'}.get(value, '')
        if not page_path:
            selected = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*\bactive\b', page)
            if selected:
                page_path = selected.group(1)
    nav = nav_for_path(page_path)
    match = re.search(r'<nav\b[^>]*class=["\']site-nav["\'][\s\S]*?</nav>', page)
    if match:
        return page[:match.start()] + nav + page[match.end():]
    return page.replace('</header>', nav + '</header>', 1)


def render_related_features(features: list[dict]) -> str:
    if not features:
        return START + END
    seed = json.loads(FEATURES.read_text(encoding='utf-8'))['features']
    by_slug = {f['slug']: f for f in seed}
    cards = []
    for f in features[:3]:
        url = str(f['url'])
        slug = url.rsplit('/', 1)[-1].removesuffix('.html')
        info = by_slug.get(slug, {})
        title = html.escape(str(f['title']))
        purpose = html.escape(str(f.get('purpose_statement') or info.get('purpose_statement') or ''))
        image = str(f.get('hero_image') or info.get('hero_image') or '')
        image_markup = (f'<img class="related-feature-card-image" src="{html.escape(image, quote=True)}" alt="" loading="lazy" />' if image else '')
        cards.append(
            f'<a class="related-feature-card" href="{html.escape(url, quote=True)}">'
            f'{image_markup}<span class="related-feature-card-copy"><strong>{title}</strong>'
            f'<span class="related-feature-card-description">{purpose}</span>'
            '<span class="related-feature-card-cta" aria-hidden="true">特集を見る →</span></span></a>'
        )
    return START + '<aside class="related-features"><h2>関連する特集</h2><div class="related-feature-cards">' + ''.join(cards) + '</div></aside>' + END


def upgrade_existing_related_block(page: str) -> str:
    if START not in page or END not in page:
        return page
    left, tail = page.split(START, 1)
    current, right = tail.split(END, 1)
    if 'related-feature-card' in current:
        return page
    links = re.findall(r'<li>\s*<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*</li>', current, re.S)
    if not links:
        return page
    features = [{'url': html.unescape(url), 'title': html.unescape(re.sub('<[^>]*>', '', title))} for url, title in links]
    return left + render_related_features(features) + right
