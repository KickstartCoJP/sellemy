"""HTML links remain available without JavaScript and survive publish regeneration."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

from pipeline import listing_static


ROOT = Path(__file__).resolve().parents[1]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.append(dict(attrs).get('href'))


def test_static_article_and_product_links_match_json():
    articles = json.loads((ROOT / 'json/articles.json').read_text())
    products = json.loads((ROOT / 'json/products.json').read_text())
    for name, rows, kind, slug_key in [
        ('articles.html', articles, 'article-card', 'slug'),
        ('products.html', products, 'item-card', 'articleSlug'),
    ]:
        content = (ROOT / name).read_text()
        assert content.count(listing_static.START) == 1
        assert content.count(listing_static.END) == 1
        block = content.split(listing_static.START)[1].split(listing_static.END)[0]
        parser = Links()
        parser.feed(block)
        expected = [f"/article/{row['category']}/{row[slug_key]}.html" for row in rows]
        assert parser.hrefs == expected
        assert block.count(f'class="{kind}"') == len(rows)
        assert all((ROOT / href.lstrip('/')).is_file() for href in expected)


def test_html_escaping_and_idempotence(tmp_path):
    path = tmp_path / 'articles.html'
    path.write_text('before' + listing_static.START + listing_static.END + 'after')
    row = {'category': 'beauty', 'slug': 'abc', 'title': 'A<&"B', 'summary': '<script>alert(1)</script>'}
    assert listing_static.update(path, [row], kind='article') == 1
    first = path.read_bytes()
    assert b'&lt;script&gt;' in first
    assert b'&lt;&amp;&quot;' in first
    listing_static.update(path, [row], kind='article')
    assert path.read_bytes() == first


def test_home_link_labels_and_amazon_only_in_footer():
    top = (ROOT / 'index.html').read_text()
    assert '美容・家電・日用品の比較記事をもっと見る</a>' in top
    assert '紹介商品をカテゴリ別に探す</a>' in top
    assert 'Amazonのアソシエイトとして' in top
    assert top.count('Amazonのアソシエイトとして') == 1
    assert top.index('<footer>') < top.index('Amazonのアソシエイトとして') < top.index('</footer>')
    assert top.count('site-advertising-notice') == 1


def test_publish_updates_catalogs_and_top_links():
    source = (ROOT / 'pipeline/publish_payload.py').read_text()
    assert "result['static_catalogs'] = sync_static_catalogs()" in source
    assert '美容・家電・日用品の比較記事をもっと見る' in source
    assert "result['top'] = _update_top()" in source


def test_publication_gate_exact_paths_updated():
    from ops.change_contract_gate import publication_paths
    paths = publication_paths('x', 'beauty')
    assert {'articles.html', 'products.html'} <= paths
    assert 'pipeline/listing_static.py' not in paths
