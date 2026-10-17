from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_runtime as g


def _p(i):
    return {'asin':f'B{i:09d}','amazon_title':f'item {i}','image_url':f'https://example.com/{i}.jpg','sources':[]}


def test_search_continues_until_eighteen_unique_products():
    pre=[_p(i) for i in range(18)]
    first=[*pre[:17],_p(18)]
    second=[*pre[1:17],_p(19),_p(20)]
    third=[_p(i) for i in range(21,37)]
    with patch.object(g,'discover_products',side_effect=[first,second,third]) as search:
        added,receipt=g.collect_unique_products({'query':'SDカード 比較'},cache_only=False,existing=pre)
    assert receipt['complete'] is True
    assert receipt['added_unique']==18
    assert len(added)==18
    assert len({p['asin'] for p in [*pre,*added]})==36
    assert search.call_count==3
    assert all('比較' not in c.args[0]['query'] for c in search.call_args_list)


def test_insufficient_unique_does_not_pass_batch():
    with patch.object(g,'discover_products',return_value=[_p(0)]):
        added,receipt=g.collect_unique_products({'query':'シャンプー 比較'},cache_only=False,existing=[_p(0)])
    assert added==[]
    assert receipt['complete'] is False
    assert receipt['added_unique']==0
