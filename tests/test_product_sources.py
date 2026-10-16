from unittest.mock import Mock, patch
import requests
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))

from product_sources import fetch_amazon_item_source


ASIN = 'B012345678'
TITLE = 'Example Desk Light Model X 100'
BODY = '''<html><span id="productTitle">Example Desk Light Model X 100</span>
<div id="feature-bullets"><ul><li>USB-C charging</li><li>Three brightness levels</li></ul></div>
<script>"asin":"B012345678"</script></html>'''


def response(body=BODY, status=200, url=f'https://www.amazon.co.jp/dp/{ASIN}'):
    result = Mock(status_code=status, url=url, text=body)
    result.raise_for_status.side_effect = None if status == 200 else requests.HTTPError('HTTP error')
    return result


def test_marketplace_source_has_verbatim_identity_and_facts():
    with patch('product_sources.requests.get', return_value=response()):
        source = fetch_amazon_item_source(ASIN, TITLE)
    assert source['verified'] is True
    assert source['http_status'] == 200
    assert source['source_type'] == 'marketplace_item'
    assert 'Example Desk Light Model X 100' in source['excerpt']
    assert 'USB-C charging' in source['excerpt']


def test_rejects_missing_identity_and_unrelated_product():
    for body, listing in [(BODY.replace(ASIN, 'B099999999'), TITLE), (BODY, 'Unrelated refrigerator')]:
        with patch('product_sources.requests.get', return_value=response(body=body)):
            assert fetch_amazon_item_source(ASIN, listing) is None


def test_rejects_redirect_and_http_failure():
    for result in [response(url='https://example.com/item'), response(status=403)]:
        with patch('product_sources.requests.get', return_value=result):
            assert fetch_amazon_item_source(ASIN, TITLE) is None


def test_amazon_overview_brand_is_grounded_in_item_html():
    body = '''<html><span id="productTitle">AMBiQUE アンビーク スパークリングスカルプシャンプー 250g</span>
    <div id="productOverview_feature_div"><table><tr><td>ブランド</td><td>AMBiQUE</td></tr></table></div>
    <a id="bylineInfo">AMBiQUEのストアを表示</a><script>"asin":"B012345678"</script></html>'''
    with patch('product_sources.requests.get', return_value=response(body=body)):
        source = fetch_amazon_item_source(ASIN, 'AMBiQUE アンビーク スパークリングスカルプシャンプー 250g')
    assert source['brand'] == 'AMBiQUE'
