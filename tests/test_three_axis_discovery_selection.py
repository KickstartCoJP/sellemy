import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from growth_runtime import _product_queries, collect_unique_products
from product_selection import ProductSelectionError, select_six

AXES = [{'id': x, 'keywords': [x]} for x in ('compact', 'quiet', 'safe')]
TOPIC = {'query': 'kettle', 'title': 'A kettle for small homes', 'comparison_axes': AXES}

def product(i, axis):
    return {'asin': f'B{i:09d}', 'amazon_title': f'Unique model {i}',
            'image_url': f'https://example.com/{i}.jpg', 'brand': f'Brand {i}',
            'discovery_axis_id': axis,
            'sources': [{'verified': True, 'excerpt': f'{axis} kettle specification', 'source_type': 'manufacturer_official'}]}

def search_factory(groups):
    seen = []
    def fake(topic, *, cache_only):
        seen.append(topic['query'])
        axis = topic['query'].split()[-1]
        return [product(i, axis) for i in groups[axis]]
    return fake, seen

def test_exactly_three_queries():
    assert _product_queries(TOPIC) == ['kettle compact', 'kettle quiet', 'kettle safe']
    with pytest.raises(ProductSelectionError):
        _product_queries({**TOPIC, 'comparison_axes': AXES[:2]})

def test_each_axis_capped_at_ten_and_total_thirty():
    fake, seen = search_factory({a['id']: range(i*11, i*11+11) for i,a in enumerate(AXES)})
    with patch('growth_runtime.discover_products', fake):
        products, status = collect_unique_products(TOPIC, cache_only=False)
    assert [a['new'] for a in status['attempts']] == [10,10,10]
    assert len(products) == 30 and len(set(p['asin'] for p in products)) == 30
    assert status['complete'] and len(seen) == 3

def test_cross_axis_duplicates_and_floor():
    fake, seen = search_factory({'compact':[1,2,3], 'quiet':[3,4,5], 'safe':[5,6,7]})
    with patch('growth_runtime.discover_products', fake):
        products, status = collect_unique_products(TOPIC, cache_only=False)
    assert [a['new'] for a in status['attempts']] == [3,2,2]
    assert len(products) == 7 and status['complete'] and len(seen)==3

def test_one_axis_under_two_blocks_topic():
    fake, _ = search_factory({'compact':[1,2], 'quiet':[2,3], 'safe':[4,5]})
    with patch('growth_runtime.discover_products', fake):
        products, status = collect_unique_products(TOPIC, cache_only=False)
    assert not status['complete']
    assert [a['new'] for a in status['attempts']] == [2,1,2]

def fake_ai(selected):
    def run(command, **kwargs):
        groups = [{'axis_id': a['id'], 'products': [{'asin': asin, 'reason': f'購入目的と{a["id"]}の仕様に適合'} for asin in selected[i*2:i*2+2]]} for i,a in enumerate(AXES)]
        return SimpleNamespace(returncode=0,stdout=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'axes':groups})}})+'\n')
    return run

def six():
    return [product(2*i+j,a['id']) for i,a in enumerate(AXES) for j in range(2)]

def test_ai_selects_six_two_per_axis():
    rows=six()
    with patch('product_selection.subprocess.run', fake_ai([r['asin'] for r in rows])):
        selected, meta=select_six(rows,TOPIC,{})
    assert len(selected)==6 and meta['candidate_count']==6
    assert {a['id']:sum(r['discovery_axis_id']==a['id'] for r in selected) for a in AXES} == {a['id']:2 for a in AXES}

def test_ai_rejects_imbalanced_six():
    rows=six()+[product(10,'compact')]
    selected=[r['asin'] for r in rows[:4]]+[rows[4]['asin'],rows[-1]['asin']]
    with patch('product_selection.subprocess.run', fake_ai(selected)):
        with pytest.raises(ProductSelectionError,match='ASIN/axis/reason invalid'):
            select_six(rows,TOPIC,{})

def test_pinned_retention():
    rows=six(); pinned=rows[:2]
    with patch('product_selection.subprocess.run', fake_ai([r['asin'] for r in rows])):
        selected,_=select_six(rows,TOPIC,{},pinned=pinned)
    assert {r['asin'] for r in pinned}.issubset({r['asin'] for r in selected})

def test_insufficient_evidence_blocks_selection():
    rows=six();rows[0]['sources']=[{'verified':True,'excerpt':'irrelevant fact'}]
    with pytest.raises(ProductSelectionError):
        select_six(rows,TOPIC,{})

def test_duplicate_different_asin_same_product_title_rejected():
    fake, _ = search_factory({'compact':[1,2], 'quiet':[3,4], 'safe':[5,6]})
    def same_title(topic,*,cache_only):
        rows=fake(topic,cache_only=cache_only)
        if topic['query'].endswith('quiet'):
            rows[0]['amazon_title']='Unique model 1'
        return rows
    with patch('growth_runtime.discover_products', same_title):
        products,status=collect_unique_products(TOPIC,cache_only=False)
    assert [x['new'] for x in status['attempts']]==[2,1,2]
    assert not status['complete']

def test_model_management_used_for_ai_selection():
    rows=six(); captured=[]
    def run(command,**kwargs):
        captured.append((command,kwargs))
        return fake_ai([r['asin'] for r in rows])(command,**kwargs)
    with patch('product_selection.subprocess.run',run):
        select_six(rows,TOPIC,{})
    from api_role_binding import require_codex_settings
    model,effort=require_codex_settings('bu-codex-sellemy-planning','planning')
    args=captured[0][0]
    assert args[args.index('--model')+1]==model
    assert args[args.index('--config')+1]==f'model_reasoning_effort={effort}'

def test_ai_unknown_asin_rejected():
    rows=six()
    with patch('product_selection.subprocess.run',fake_ai([r['asin'] for r in rows[:5]]+['B999999999'])):
        with pytest.raises(ProductSelectionError,match='ASIN/axis/reason invalid'):
            select_six(rows,TOPIC,{})
