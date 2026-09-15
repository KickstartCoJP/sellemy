from __future__ import annotations
import copy

VALID_PAYLOAD = {
    'slug': 'test-widgets-6-picks',
    'category': 'gadget',
    'h1': 'テスト用！ウィジェット6選',
    'lead': 'x' * 200,
    'summary': 'x' * 150,
    'how_to_choose': 'x' * 220,
    'comparison_groups': [
        {'id': 'portable', 'title': '持ち運びやすさ', 'angle': '軽さと設置性を比べる2モデルです。', 'product_refs': ['p1', 'p2']},
        {'id': 'balanced', 'title': '日常の使いやすさ', 'angle': '操作性と機能の違いを比べる2モデルです。', 'product_refs': ['p3', 'p4']},
        {'id': 'performance', 'title': '性能を優先する用途', 'angle': '負荷の高い用途への適性を比べる2モデルです。', 'product_refs': ['p5', 'p6']},
    ],
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
    'comparison_axes': [
        {'id': 'portable', 'label': '持ち運びやすさ', 'keywords': ['軽量']},
        {'id': 'performance', 'label': '性能', 'keywords': ['高性能']},
    ],
    'products': [
        {
            'ref': f'p{i}', 'product_id': f'AMZ-B0TEST{i:04d}', 'asin': f'B0TEST{i:04d}',
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
