"""Purpose navigation and HTML card regression for built static pages."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'tests'))
from purpose_ui import ensure_theme_nav, render_related_features, upgrade_existing_related_block
from renderer import render_article
from fixtures import valid_evidence, valid_payload


def test_global_navigation_across_primary_surfaces():
    pages = [ROOT / 'index.html', ROOT / 'articles.html', ROOT / 'products.html',
             ROOT / 'article/beauty/index.html', ROOT / 'article/dailygoods/index.html',
             ROOT / 'article/gadget/index.html', ROOT / 'themes/index.html']
    pages += list((ROOT / 'article').glob('*/*.html'))
    for page in pages:
        text = page.read_text(encoding='utf-8')
        if '<header class="site-header"' not in text:
            continue  # Redirect stubs and empty fixture files are not content pages.
        nav = re.search(r'<nav[^>]*class="site-nav"[\s\S]*?</nav>', text)
        assert nav and nav.group(0).count('href="/themes/"') == 1, page
        assert ensure_theme_nav(text) == text, page


def test_purpose_cards_keep_accessible_normal_links_and_images():
    features = [{'title': 'デスク環境が整う', 'url': '/themes/desk-environment.html'}]
    block = render_related_features(features)
    assert '<a class="related-feature-card" href="/themes/desk-environment.html">' in block
    assert '<strong>デスク環境が整う</strong>' in block
    assert '/img/themes/desk-environment.png' in block
    assert 'デスク周り' in block
    assert 'role="button"' not in block
    assert block == upgrade_existing_related_block(block)
    article = (ROOT / 'article/gadget/led-desk-lights-6-picks.html').read_text(encoding='utf-8')
    assert block in article


def test_new_articles_render_nav_and_cards_without_affecting_main_content():
    rendered = render_article(valid_payload(), valid_evidence(), related_features=[
        {'title': 'デスク環境が整う', 'url': '/themes/desk-environment.html'}])
    assert '<nav class="site-nav">' in rendered
    assert 'href="/themes/"' in rendered
    assert '<a class="related-feature-card" href="/themes/desk-environment.html">' in rendered
    assert rendered.index('関連する特集') > rendered.index('選ぶときのポイント')
    assert rendered.index('Amazon') < rendered.index('関連する特集')


def test_existing_articles_keep_related_feature_count_and_links():
    for page in (ROOT / 'article').glob('*/*.html'):
        content = page.read_text(encoding='utf-8')
        if 'related-feature-card' not in content:
            continue
        links = re.findall(r'<a class="related-feature-card" href="([^"]+)">', content)
        assert 1 <= len(links) <= 3, page
        assert len(links) == len(set(links)), page
        assert all(url.startswith('/themes/') and url.endswith('.html') for url in links)
