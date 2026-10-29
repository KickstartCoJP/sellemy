import sys
from pathlib import Path
from threading import Barrier, Lock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import growth_role_business as growth_runtime


def _row(index):
    return {'asin': f'B{index:09d}', 'title': f'Distinct product {index}', 'image_url': f'https://example.com/{index}.jpg', 'rating': 4.5, 'review_count': 200}


def test_live_sources_are_bounded_parallel_and_keep_search_order():
    rows = [_row(i) for i in range(20)]
    barrier = Barrier(6, timeout=3)
    lock = Lock()
    active = peak = 0
    called = []

    def fetch(asin, title):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            called.append(asin)
        if int(asin[1:]) < 6:
            barrier.wait()
        with lock:
            active -= 1
        return None if asin == rows[2]['asin'] else {'url': f'https://www.amazon.co.jp/dp/{asin}', 'verified': True}

    with patch.object(growth_runtime.AutositeDiscoveryAdapter, 'search_amazon_live', return_value=rows), \
         patch.object(growth_runtime, 'fetch_amazon_item_source', side_effect=fetch):
        products = growth_runtime.discover_products({'query': 'desk light'}, cache_only=False)

    assert peak == 6
    assert len(called) == 18
    assert [product['asin'] for product in products] == [row['asin'] for row in rows[:18]]
    assert products[2]['sources'] == [] and products[2]['provisional'] is True
    assert all(product['sources'][0]['verified'] for index, product in enumerate(products) if index != 2)


def test_cache_only_never_fetches_live_sources():
    with patch.object(growth_runtime.AutositeDiscoveryAdapter, 'search_cache', return_value=[_row(0)]), \
         patch.object(growth_runtime.AutositeDiscoveryAdapter, 'search_amazon_live') as live, \
         patch.object(growth_runtime, 'fetch_amazon_item_source') as fetch:
        products = growth_runtime.discover_products({'query': 'desk light'}, cache_only=True)

    assert len(products) == 1
    assert 'sources' not in products[0]
    live.assert_not_called()
    fetch.assert_not_called()


def test_failed_source_does_not_discard_other_products():
    rows = [_row(i) for i in range(6)]

    def fetch(asin, title):
        if asin == rows[1]['asin']:
            raise RuntimeError('source unavailable')
        return {'url': f'https://www.amazon.co.jp/dp/{asin}', 'verified': True}

    with patch.object(growth_runtime.AutositeDiscoveryAdapter, 'search_amazon_live', return_value=rows), \
         patch.object(growth_runtime, 'fetch_amazon_item_source', side_effect=fetch):
        products = growth_runtime.discover_products({'query': 'desk light'}, cache_only=False)

    assert [product['asin'] for product in products] == [row['asin'] for row in rows]
    assert products[1]['sources'] == [] and products[1]['provisional'] is True


def test_verified_item_brand_populates_blank_search_brand():
    row = _row(0)
    with patch.object(growth_runtime.AutositeDiscoveryAdapter, 'search_amazon_live', return_value=[row]), \
         patch.object(growth_runtime, 'fetch_amazon_item_source', return_value={
             'url': 'https://www.amazon.co.jp/dp/B000000000', 'verified': True, 'brand': 'AMBiQUE'}):
        products = growth_runtime.discover_products({'query': 'shampoo'}, cache_only=False)
    assert products[0]['brand'] == 'AMBiQUE'
