import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from product_sources import fetch_manufacturer_source
from manufacturer_research import research_selected

PRODUCT = {'asin': 'B012345678', 'brand': 'AMBiQUE',
           'amazon_title': 'AMBiQUE スパークリングスカルプシャンプー 250g', 'sources': []}
URL = 'https://www.alo-organic.com/shop/products/amb008_1'
HTML = '<html><title>AMBiQUE 公式</title><main><h1>AMBiQUE アンビーク スパークリングスカルプシャンプー</h1><p>内容量：250g</p><p>販売元：株式会社アロオーガニック</p></main></html>'


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
    html = '''<html><head><title>アンビーク スパークリングスカルプシャンプー｜【公式】SOLIA SHOP</title></head>
    <main><h1>アンビーク スパークリングスカルプシャンプー</h1>
    <img alt="AMBiQUE（アンビーク）スパークリングスカルプシャンプー" src="x">
    <p>内容量：250g</p><h2>使用方法</h2><p>レモン１個分程度の泡を手に取ります。</p>
    <p>頭皮全体をマッサージするように、優しく洗ってください。</p></main></html>'''
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
