from comparison_acceptance import duplicate_pair_issues, DUPLICATE_ITEMS


def evidence():
    return {'products': [
        {'ref': 'p1', 'sources': [{'verified': True, 'title': 'スタッシャー EZハーフガロン アクア', 'excerpt': ''}]},
        {'ref': 'p2', 'sources': [{'verified': True, 'title': 'スタッシャー EZハーフガロン ブルー', 'excerpt': ''}]},
    ]}


def pair(anchored=True):
    items = []
    for key in DUPLICATE_ITEMS:
        items.append({'item': key, 'status': 'FAIL' if key == 'color_design_only' else 'UNKNOWN',
                      'left_quote': 'スタッシャー EZハーフガロン' if anchored else '架空',
                      'right_quote': 'スタッシャー EZハーフガロン', 'reason': '同じ商品で色のみ異なる'})
    return {'left_ref': 'p1', 'right_ref': 'p2', 'items': items, 'is_duplicate': True, 'reason': '色違い'}


def test_grounded_pair_passes():
    assert duplicate_pair_issues({'distinct_products': False, 'duplicate_pair_checks': [pair()]}, evidence()) == []


def test_unanchored_duplicate_fails():
    assert any('source anchors' in x for x in duplicate_pair_issues({'distinct_products': False, 'duplicate_pair_checks': [pair(False)]}, evidence()))


def test_unknown_is_not_duplicate_evidence():
    p = pair()
    p['items'][1]['status'] = 'UNKNOWN'
    assert any('without proven' in x for x in duplicate_pair_issues({'distinct_products': False, 'duplicate_pair_checks': [p]}, evidence()))


def test_missing_check_item_fails():
    p = pair()
    p['items'].pop()
    assert any('incomplete' in x for x in duplicate_pair_issues({'distinct_products': False, 'duplicate_pair_checks': [p]}, evidence()))


def test_six_products_require_all_fifteen_pairs():
    e = {'products': [
        {'ref': f'p{i}', 'sources': [{'verified': True, 'title': f'Product {i}', 'excerpt': ''}]}
        for i in range(1, 7)]}
    checks = []
    for i in range(1, 7):
        for j in range(i+1, 7):
            checks.append({'left_ref': f'p{i}', 'right_ref': f'p{j}', 'is_duplicate': False, 'reason': 'No duplicate established',
                           'items': [{'item': key, 'status': 'UNKNOWN', 'left_quote': '', 'right_quote': '', 'reason': 'No affirmative duplicate'} for key in DUPLICATE_ITEMS]})
    assert duplicate_pair_issues({'distinct_products': True, 'duplicate_pair_checks': checks}, e) == []
    assert any('coverage incomplete' in x for x in duplicate_pair_issues({'distinct_products': True, 'duplicate_pair_checks': checks[:-1]}, e))
