"""Public navigation is one shared seven-link system on all current content pages."""
from pathlib import Path
import sys
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'pipeline'))
from purpose_ui import NAV_ITEMS, ensure_theme_nav, nav_for_path


def public_pages():
    paths = [ROOT/'index.html', ROOT/'articles.html', ROOT/'products.html',
             ROOT/'categories/index.html']
    paths += list((ROOT/'article').glob('*/*.html'))
    paths += list((ROOT/'article').glob('*/topics/*.html'))
    paths += list((ROOT/'themes').glob('*.html'))
    return [p for p in paths if p.is_file() and '<header class="site-header"' in p.read_text()]


def test_all_public_headers_use_identical_order_links_and_one_current_state():
    pages = public_pages()
    assert len(pages)>=480
    expected = list(NAV_ITEMS)
    for path in pages:
        soup = BeautifulSoup(path.read_text(), 'html.parser')
        nodes = soup.select('.site-header .site-nav .nav-links > li > a')
        assert [(a['href'], a.get_text(strip=True)) for a in nodes] == expected, path
        assert len(soup.select('.site-header .site-nav a.active')) <= 1, path
        assert len(soup.select('.site-header .site-nav a[href="/categories/"]')) == 1, path
        assert len(soup.select('.site-header .site-nav a[href="/themes/"]')) == 1, path


def test_nav_normalizer_repairs_old_partial_and_is_idempotent():
    old = ('<header class="site-header"><nav class="site-nav">'
           '<ul class="nav-links"><a href="/" class="nav-link">Top</a></ul>'
           '</nav></header>')
    normalized = ensure_theme_nav(old, page_path='/article/beauty/dummy.html')
    assert normalized.count('class="site-nav"')==1
    assert normalized.count('href="/categories/"')==1
    assert normalized.count('href="/themes/"')==1
    assert ensure_theme_nav(normalized, page_path='/article/beauty/dummy.html')==normalized
    assert nav_for_path('/categories/').count('aria-current="page"')==1


def test_theme_and_feature_landing_have_genre_style_title_bands():
    landing = BeautifulSoup((ROOT/'categories/index.html').read_text(), 'html.parser')
    features = BeautifulSoup((ROOT/'themes/index.html').read_text(), 'html.parser')
    assert landing.select_one('main > .section-title h1').get_text(strip=True) == 'テーマで見る'
    assert features.select_one('main > .section-title h1').get_text(strip=True) == '特集'
    assert landing.select_one('main > p')
    assert features.select_one('.feature-index-heading p')
    stylesheet = (ROOT/'css/style.css').read_text()
    assert '.category-catalog>.section-title{max-width:1200px' in stylesheet
    assert '.feature-index main>.section-title{max-width:1200px' in stylesheet


def test_runtime_feature_generation_uses_same_site_nav():
    source = (ROOT/'pipeline/purpose_feature_runtime.py').read_text()
    assert "nav_for_path('/themes/')" in source
    assert 'def _ensure_top_link() -> None:' in source
    assert "ensure_theme_nav(text, page_path='/index.html')" in source
