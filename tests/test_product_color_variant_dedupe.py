from product_selection import dedupe_content_products, select_six, ProductSelectionError


def test_same_stasher_model_color_variants_are_one_product():
    rows = [
        {'asin':'B0B4HCLVKF','brand':'Stasher','amazon_title':'【日本正規販売品】スタッシャーシリコーンバッグ EZハーフガロン (アクア)','image_url':'https://example.test/a.jpg'},
        {'asin':'B0B4HBW3PM','brand':'Stasher','amazon_title':'【日本正規販売品】スタッシャーシリコーンバッグ EZハーフガロン (ブルー)','image_url':'https://example.test/b.jpg'},
    ]
    kept, removed = dedupe_content_products(rows)
    assert len(kept) == 1
    assert removed[0]['reason'] == 'same_named_model_color_variant'
    assert removed[0]['check_items']['color_design_only'] == 'FAIL'


def test_different_sizes_and_models_are_not_conflated():
    rows = [
        {'asin':'B0B4HCLVKF','brand':'Stasher','amazon_title':'スタッシャーシリコーンバッグ EZハーフガロン (アクア)','image_url':'https://example.test/a.jpg'},
        {'asin':'B0B4GSRSY7','brand':'Stasher','amazon_title':'スタッシャーシリコーンバッグ EZサンドイッチ (クリア)','image_url':'https://example.test/b.jpg'},
        {'asin':'B0B4HCLVK2','brand':'Stasher','amazon_title':'スタッシャーシリコーンバッグ EZハーフガロン (1.89L)','image_url':'https://example.test/c.jpg'},
    ]
    kept, removed = dedupe_content_products(rows)
    assert len(kept) == 3
    assert not removed
