"""Product Role's three-axis collection against the existing candidate schema."""
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_role_business as g


def _p(i):
    return {'asin':f'B{i:09d}','amazon_title':f'item {i}',
            'image_url':f'https://example.com/{i}.jpg','sources':[]}


def _topic(query):
    return {'query':query, 'comparison_axes':[
        {'id':'small','keywords':['コンパクト'],'label':'小型'},
        {'id':'fast','keywords':['高速'],'label':'速度'},
        {'id':'light','keywords':['軽量'],'label':'重量'}]}


def test_search_continues_until_eighteen_unique_products():
    previous=[_p(i) for i in range(18)]
    first=[*previous[:5],*[_p(i) for i in range(18,24)]]
    second=[*previous[5:10],*[_p(i) for i in range(24,30)]]
    third=[*previous[10:15],*[_p(i) for i in range(30,36)]]
    with patch.object(g,'discover_products',side_effect=[first,second,third]) as search:
        added,receipt=g.collect_unique_products(_topic('SDカード 比較'),cache_only=False,existing=previous)
    assert receipt['complete'] is True
    assert receipt['added_unique']==18
    assert len(added)==18
    assert len({p['asin'] for p in [*previous,*added]})==36
    assert search.call_count==3
    assert [row['axis_id'] for row in receipt['attempts']]==['small','fast','light']
    assert all('比較' not in call.args[0]['query'] for call in search.call_args_list)


def test_insufficient_unique_does_not_pass_batch():
    with patch.object(g,'discover_products',return_value=[_p(0)]) as search:
        added,receipt=g.collect_unique_products(_topic('シャンプー 比較'),cache_only=False,existing=[_p(0)])
    assert added==[]
    assert receipt['complete'] is False
    assert receipt['added_unique']==0
    assert search.call_count==3
