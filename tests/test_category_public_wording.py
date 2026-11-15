from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]


def test_category_public_experience_matches_brand_words():
    for path in ['index.html','categories/index.html','article/beauty/index.html',
                 'article/dailygoods/index.html','article/gadget/index.html']:
        source=(ROOT/path).read_text()
        soup=BeautifulSoup(source,'html.parser')
        nav=soup.select_one('a[href="/categories/"]')
        if nav: assert nav.get_text(strip=True)=='テーマ'
        assert 'カテゴリ</' not in source
        assert '気になるテーマから探す' not in source
        assert soup.select_one('a[href="/themes/"]').get_text(strip=True)=='特集'
    landing=BeautifulSoup((ROOT/'categories/index.html').read_text(),'html.parser')
    assert landing.select_one('h1').get_text(strip=True)=='テーマで見る'
    assert landing.select_one('.site-nav .active').get_text(strip=True)=='テーマ'
    assert len(landing.select('.category-tiles .category-tile'))==21
    assert len(landing.select('.category-tiles a.category-tile'))==19
    assert [x.get_text(strip=True) for x in landing.select('.category-tile small')] == ['記事準備中', '記事準備中']
    assert not landing.select('a.category-tile small')


def test_equal_width_genre_bands_and_reusable_title_style():
    css=(ROOT/'css/style.css').read_text()
    assert '.article-list {' in css and 'max-width: 1200px' in css
    assert '.genre-article-heading{max-width:1200px;margin:2rem auto}' in css
    assert '.category-discovery,.category-catalog{max-width:1200px' in css
    assert '.category-discovery .section-title{margin:2rem 0}' in css
    for cat in ('beauty','dailygoods','gadget'):
        html=BeautifulSoup((ROOT/f'article/{cat}/index.html').read_text(),'html.parser')
        assert html.select_one('.category-discovery .section-title h2')
        assert html.select_one('.genre-article-heading.section-title h2')
