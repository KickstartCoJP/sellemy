from pathlib import Path
from bs4 import BeautifulSoup
import json
import re

ROOT = Path(__file__).resolve().parents[1]
GENRES = {'beauty':'美容','dailygoods':'日用品','gadget':'家電'}


def test_category_pages_are_real_and_linked_by_unchanged_genre_tops():
    indexed = json.loads((ROOT/'json/articles.json').read_text())
    published = {a['slug'] for a in indexed if a['status'] == 'published'}
    assert len(published) >= 447
    seen_links=set()
    for category in GENRES:
        top = BeautifulSoup((ROOT/'article'/category/'index.html').read_text(), 'html.parser')
        assert top.select_one('section.category-intro')
        assert top.select_one('#articleList')
        nav = top.select_one('nav.category-discovery')
        assert nav and nav.select_one('details summary').get_text(strip=True) == '気になるテーマから探す'
        for a in nav.select('.category-discovery-links a'):
            href = a['href']
            assert href.startswith(f'/article/{category}/topics/')
            dest = ROOT / href.lstrip('/')
            assert dest.is_file()
            seen_links.add(href)
        for path in (ROOT/'article'/category/'topics').glob('*.html'):
            doc=BeautifulSoup(path.read_text(), 'html.parser')
            assert doc.select_one('link[rel=canonical]')['href'] == 'https://www.sellemy.jp/' + str(path.relative_to(ROOT))
            assert not doc.select_one('meta[name=robots]')
            assert len(doc.select('.article-list .article-card')) >= 3
            assert doc.select_one('.site-header') and doc.select_one('footer')
            assert doc.select_one('.breadcrumb a')['href'] == '/index.html'
            for a in doc.select('.article-list a.article-card'):
                assert re.fullmatch(r'/article/(beauty|dailygoods|gadget)/[a-z0-9-]+\.html',a['href'])
                slug=Path(a['href']).stem
                assert slug in published
                assert (ROOT/a['href'].lstrip('/')).is_file()
                assert a.select_one('img') and (ROOT/a.select_one('img')['src'].lstrip('/')).is_file()
    assert len(seen_links)==18


def test_temporary_preview_pages_are_removed_and_source_articles_intact():
    assert not (ROOT/'preview/category-pages').exists()
    assert not (ROOT/'preview/category-navigation').exists()
    assert 'class="category-discovery"' not in (ROOT/'article/beauty/autumn-cheek-blush-6-picks.html').read_text()
