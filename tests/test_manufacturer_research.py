import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from product_sources import fetch_manufacturer_source
from manufacturer_research import research_selected

PRODUCT = {'asin': 'B012345678', 'brand': 'AMBiQUE',
           'amazon_title': 'AMBiQUE スパークリングスカルプシャンプー 250g', 'sources': []}
URL = 'https://www.alo-organic.com/shop/products/amb008_1'
HTML = '<html><title>AMBiQUE 公式</title><main><h1>AMBiQUE アンビーク スパークリングスカルプシャンプー</h1><p>内容量：250g</p><p>販売元：株式会社アロオーガニック</p></main><footer>株式会社 AMBiQUE</footer></html>'


def response(url=URL, body=HTML):
    result = Mock(status_code=200, url=url, text=body)
    result.raise_for_status.return_value = None
    return result


def test_verified_official_page_uses_fetched_exact_text():
    with patch('product_sources.requests.get', return_value=response()):
        source = fetch_manufacturer_source(URL, PRODUCT)
    assert source['source_type'] == 'manufacturer_official'
    assert '内容量：250g' in source['excerpt']
    assert source['verified'] is True


def test_rejects_marketplace_redirect_and_identity_mismatch():
    cases = [(URL, response(url='https://other.example/product')),
             (URL, response(body=HTML.replace('スパークリングスカルプシャンプー', '別の商品'))),
             ('https://www.amazon.co.jp/dp/B012345678', response())]
    for url, result in cases:
        with patch('product_sources.requests.get', return_value=result):
            assert fetch_manufacturer_source(url, PRODUCT) is None


def test_official_solia_shop_structure_and_verbatim_usage():
    html = '''<html><head><title>AMBiQUE アンビーク スパークリングスカルプシャンプー｜【公式】SOLIA SHOP</title></head>
    <main><h1>アンビーク スパークリングスカルプシャンプー</h1>
    <img alt="AMBiQUE（アンビーク）スパークリングスカルプシャンプー" src="x">
    <p>内容量：250g</p><h2>使用方法</h2><p>レモン１個分程度の泡を手に取ります。</p>
    <p>頭皮全体をマッサージするように、優しく洗ってください。</p></main><footer>株式会社 AMBiQUE</footer></html>'''
    with patch('product_sources.requests.get', return_value=response(body=html)):
        source = fetch_manufacturer_source(URL, PRODUCT)
    assert source is not None
    assert '内容量：250g' in source['excerpt']
    assert 'レモン１個分程度の泡を手に取ります。' in source['excerpt']


def test_spoofed_official_text_and_brand_mismatch_rejected():
    spoof = '''<html><title>Generic shop</title><main><h1>AMBiQUE スパークリングスカルプシャンプー</h1>
    <p>公式っぽい 250g メーカー保証</p></main></html>'''
    mismatch = HTML.replace('AMBiQUE', 'OtherBrand')
    for body in (spoof, mismatch):
        with patch('product_sources.requests.get', return_value=response(body=body)):
            assert fetch_manufacturer_source(URL, PRODUCT) is None


def test_batch_search_response_is_candidate_only_and_cache_never_browses():
    events = '\n'.join([
        json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
                    'text': json.dumps({'items': [{'asin': PRODUCT['asin'], 'url': URL}]})}}),
        json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 30, 'output_tokens': 10}}),
    ])
    with patch('manufacturer_research.subprocess.run', return_value=Mock(returncode=0, stdout=events)) as cli, \
         patch('manufacturer_research.fetch_manufacturer_source', return_value={'source_type': 'manufacturer_official', 'verified': True}) as fetch, \
         patch('codex_provider._append_usage'):
        result = research_selected([PRODUCT])
    assert len(result[0]['sources']) == 1
    assert fetch.call_args.args == (URL, PRODUCT)
    assert '--search' in cli.call_args.args[0]
    with patch('manufacturer_research.subprocess.run') as cli:
        assert research_selected([PRODUCT], cache_only=True) == [PRODUCT]
        cli.assert_not_called()


def test_missing_brand_requires_model_company_and_host():
    product = {**PRODUCT, 'brand': '', 'amazon_title': 'Panasonic EH-NA0J ヘアドライヤー'}
    html = '<title>EH-NA0J</title><main><h1>Panasonic EH-NA0J</h1><p>メーカー：Panasonic</p><p>風量 1.6m3/分</p></main>'
    url = 'https://panasonic.jp/hair/products/EH-NA0J.html'
    with patch('product_sources.requests.get', return_value=response(url=url, body=html)):
        assert fetch_manufacturer_source(url, product)['verified'] is True
    with patch('product_sources.requests.get', return_value=response(url='https://fake.example/item', body=html)):
        assert fetch_manufacturer_source('https://fake.example/item', product) is None


def test_official_claim_on_unrelated_host_is_insufficient():
    html = '<title>AMBiQUE 【公式】</title><main><h1>AMBiQUE スパークリングスカルプシャンプー</h1></main>'
    url = 'https://unrelated.example/item'
    with patch('product_sources.requests.get', return_value=response(url=url, body=html)):
        assert fetch_manufacturer_source(url, PRODUCT) is None


@pytest.mark.parametrize('url,brand,listing,title,footer', [
    ('https://www.ankerjapan.com/products/a2663', 'Anker', 'Anker Nano II 65W 急速充電器',
     'Anker Nano II 65W | Anker GaN II搭載の急速充電器 | Anker Japan 公式オンラインストア',
     '© 2026 Anker Japan Co., Ltd.'),
    ('https://connectinternationalone.co.jp/cioproduct/adapter/novaport/novaport-trio/',
     'CIO', 'CIO NovaPort TRIO 65W 急速充電器',
     'NovaPort TRIO 65W | 株式会社CIO（シーアイオー）公式HP', 'Copyright © CIO Co.,Ltd.'),
    ('https://ugreen.jp/products/nexode-pro-65w-gan-charger', 'UGREEN',
     'UGREEN Nexode Pro 65W GaN 急速充電器', 'UGREEN Nexode Pro 65W GaN 急速充電器',
     '株式会社ユーグリーン・ジャパン'),
])
def test_current_official_charger_pages(url, brand, listing, title, footer):
    html = f'<html><title>{title}</title><main><h1>{listing}</h1></main><footer>{footer}</footer></html>'
    product = {'brand': brand, 'amazon_title': listing}
    with patch('product_sources.requests.get', return_value=response(url=url, body=html)):
        source = fetch_manufacturer_source(url, product)
    assert source is not None
    assert source['title'] == title
    assert listing in source['excerpt']


def test_verified_charger_excerpt_keeps_spec_blocks_and_excludes_chrome():
    url = 'https://www.ankerjapan.com/products/a2663'
    product = {'brand': 'Anker', 'amazon_title': 'Anker Nano II 65W 急速充電器'}
    html = '''<html><title>Anker Nano II 65W | Anker 公式</title>
    <header>会員登録でお得</header><nav>全商品一覧</nav><main>
    <h1>Anker Nano II 65W 急速充電器</h1>
    <p>USB-Cポートの最大出力は65Wです。</p>
    <h2>製品仕様</h2><table><tr><th>ポート数</th><td>USB-C 1ポート</td></tr>
    <tr><th>対応規格</th><td>USB PD / PPS</td></tr></table>
    <ul><li>GaN IIを採用しています。</li></ul>
    <section class="related-products"><h2>関連商品</h2><p>別の充電器をおすすめします。</p></section>
    </main><footer>© Anker Japan Co., Ltd.</footer></html>'''
    with patch('product_sources.requests.get', return_value=response(url=url, body=html)):
        source = fetch_manufacturer_source(url, product)
    assert source is not None
    for fact in ('USB-Cポートの最大出力は65Wです。', '製品仕様', 'USB-C 1ポート',
                 'USB PD / PPS', 'GaN IIを採用しています。'):
        assert fact in source['excerpt']
    assert '会員登録でお得' not in source['excerpt']
    assert '別の充電器' not in source['excerpt']


def test_verified_shampoo_excerpt_keeps_usage_without_keyword():
    html = HTML.replace('</main>', '<h2>使い方</h2><p>適量を手に取り、よくすすいでください。</p></main>')
    with patch('product_sources.requests.get', return_value=response(body=html)):
        source = fetch_manufacturer_source(URL, PRODUCT)
    assert '適量を手に取り、よくすすいでください。' in source['excerpt']


def test_unrelated_host_cannot_claim_official_with_corporate_footer():
    url = 'https://unrelated.example/products/nexode-pro'
    html = ('<title>UGREEN Nexode Pro 65W 公式</title><main>UGREEN Nexode Pro 65W</main>'
            '<footer>Copyright © Unrelated Co., Ltd.</footer>')
    product = {'brand': 'UGREEN', 'amazon_title': 'UGREEN Nexode Pro 65W'}
    with patch('product_sources.requests.get', return_value=response(url=url, body=html)):
        assert fetch_manufacturer_source(url, product) is None


def test_anker_nano_short_name_requires_two_exact_tokens_and_official_ownership():
    url = 'https://www.ankerjapan.com/products/a2663'
    title = 'Anker Nano II 65W | Anker GaN II搭載の急速充電器 | Anker Japan 公式オンラインストア'
    product = {'brand': 'Anker', 'amazon_title': 'Anker Nano II 65W'}
    footer = '<footer>© 2026 Anker Japan Co., Ltd.</footer>'
    html = f'<title>{title}</title><main><h1>Anker Nano II 65W</h1></main>{footer}'
    with patch('product_sources.requests.get', return_value=response(url=url, body=html)):
        source = fetch_manufacturer_source(url, product)
    assert source is not None
    assert source['verified'] is True
    assert source['title'] == title

    for body in (html.replace('Nano', 'Nanos'),
                 f'<title>Anker 公式オンラインストア</title><main>Anker</main>{footer}'):
        with patch('product_sources.requests.get', return_value=response(url=url, body=body)):
            assert fetch_manufacturer_source(url, product) is None
    with patch('product_sources.requests.get', return_value=response(url='https://fake.example/products/a2663', body=html)):
        assert fetch_manufacturer_source(url, product) is None


def test_verified_marketplace_item_still_checks_for_official_evidence():
    product = {**PRODUCT, 'sources': [{'source_type': 'marketplace_item', 'verified': True,
                                     'url': 'https://www.amazon.co.jp/dp/B012345678',
                                     'excerpt': 'AMBiQUE スパークリングスカルプシャンプー 容量250g'}]}
    with patch('manufacturer_research.subprocess.run', return_value=Mock(returncode=1, stdout='')) as codex:
        assert research_selected([product]) == [product]
    codex.assert_called_once()


def test_official_escalation_researches_marketplace_only_source():
    product = {**PRODUCT, 'sources': [{'source_type': 'marketplace_item', 'verified': True,
                                     'url': 'https://www.amazon.co.jp/dp/B012345678',
                                     'excerpt': 'AMBiQUE 250g'}]}
    with patch('manufacturer_research.subprocess.run') as codex:
        codex.return_value = Mock(returncode=1, stdout='')
        assert research_selected([product], prefer_official=True) == [product]
    codex.assert_called_once()
