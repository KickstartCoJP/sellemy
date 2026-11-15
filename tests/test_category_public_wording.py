from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]


def test_category_public_experience_matches_brand_words():
    for path in ['index.html','categories/index.html','article/beauty/index.html',
                 'article/dailygoods/index.html','article/gadget/index.html']:
        source=(ROOT/path).read_text()
        soup=BeautifulSoup(source,'html.parser')
        nav=soup.select_one('a[href="/categories/"]')
        if nav: assert nav.get_text(strip=True)=='モノとの出会い'
        assert 'カテゴリ</' not in source
        assert '気になるテーマから探す' not in source
        assert soup.select_one('a[href="/themes/"]').get_text(strip=True)=='特集'
    landing=BeautifulSoup((ROOT/'categories/index.html').read_text(),'html.parser')
    assert landing.select_one('h1').get_text(strip=True)=='モノとの出会い'
    assert landing.select_one('.site-nav .active').get_text(strip=True)=='モノとの出会い'
    assert len(landing.select('.category-tiles .category-tile'))==21
    assert len(landing.select('.category-tiles a.category-tile'))==19
    assert not landing.select('.category-tile small')
