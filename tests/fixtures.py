from __future__ import annotations
import copy

VALID_PAYLOAD = {
    'slug': 'test-widgets-6-picks',
    'category': 'gadget',
    'h1': 'テスト用！ウィジェット6選',
    'lead': 'x' * 200,
    'summary': 'x' * 150,
    'how_to_choose': 'x' * 220,
    'comparison_angles': {
        'lowrange': '価格を抑えたい方向けの2モデルです。',
        'midrange': 'バランス重視の2モデルです。',
        'highrange': '性能重視の2モデルです。',
    },
    'products': [
        {'ref': f'p{i}', 'h3': f'テスト商品{i}の名前', 'description': 'テスト用の説明文です。' * 20}
        for i in range(1, 7)
    ],
    'conclusion': 'x' * 180,
}

VALID_EVIDENCE = {
    'slug': 'test-widgets-6-picks',
    'category': 'gadget',
    'canonical_url': 'https://www.sellemy.jp/article/gadget/test-widgets-6-picks.html',
    'eyecatch_image': 'https://www.sellemy.jp/img/test-widgets-6-picks/test-widgets-6-picks.png',
    'amazon_tag': 'suzuron-22',
    'products': [
        {
            'ref': f'p{i}', 'tier': ['lowrange', 'lowrange', 'midrange', 'midrange', 'highrange', 'highrange'][i - 1],
            'product_id': f'GROW-test-widgets-6-picks-{i}', 'asin': f'B0TEST{i:04d}',
            'image_url': f'https://m.media-amazon.com/images/I/test{i}.jpg',
            'amazon_title': f'テスト商品{i} 型番TEST-{i:03d} 高性能ウィジェット 2026年モデル',
            'brand': f'TestBrand{i}',
        }
        for i in range(1, 7)
    ],
}


def valid_payload():
    return copy.deepcopy(VALID_PAYLOAD)


def valid_evidence():
    return copy.deepcopy(VALID_EVIDENCE)
