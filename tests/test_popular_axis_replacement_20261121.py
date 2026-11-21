from unittest.mock import patch
from growth_role_business import _product_queries, _replace_unproductive_axes
from planning_runtime import propose_replacement_axis, PlanningError


def topic():
    return {'slug':'2027-planners', 'query':'2027 手帳 仕事 家族 予定 管理',
            'product_theme':'2027年 手帳', 'intent_key':'家族と仕事の予定を一冊に記録',
            'comparison_axes':[
                {'id':'popular','label':'人気・定番の基準','keywords':['人気']},
                {'id':'family','label':'家族別管理','keywords':['家族手帳']},
                {'id':'time','label':'時間管理','keywords':['バーチカル']}],
            'reader_persona':{'reader':'予定を調整したい人'}}


def candidate(n, axis):
    return {'asin':f'B{n:09d}','amazon_title':f'手帳 {n}',
            'image_url':f'https://example.com/{n}', 'discovery_axis_id':axis,
            'sources':[{'verified':True,'excerpt':f'手帳 {n} 週間仕様'}]}


def test_popular_axis_search_is_product_theme_not_unverified_popularity_claim():
    queries=_product_queries(topic())
    assert queries==['2027年 手帳','2027年 手帳 家族手帳','2027年 手帳 バーチカル']


def test_missing_axis_is_replaced_with_new_planner_angle_and_preserves_other_axes():
    t=topic();initial=[candidate(1,'popular'),candidate(2,'popular'),candidate(3,'time'),candidate(4,'time')]
    replacement={'id':'portable','label':'持ち運びやすい手帳','keywords':['ポケットサイズ']}
    def collect(new_topic, *,cache_only,existing):
        assert new_topic['comparison_axes'][1]==replacement
        return [*existing,candidate(5,'portable'),candidate(6,'portable')],{'complete':True,'attempts':[{'axis_id':'portable'}]}
    with patch('planning_runtime.propose_replacement_axis',return_value=replacement) as propose, \
         patch('growth_role_business.collect_unique_products',side_effect=collect) as search:
        products,changes=_replace_unproductive_axes(t,initial,cache_only=False)
    assert len(products)==6
    assert [x['id'] for x in t['comparison_axes']]==['popular','portable','time']
    assert changes[0]['from']=='family' and changes[0]['to']=='portable'
    propose.assert_called_once();search.assert_called_once()


def test_failed_replacement_keeps_original_axis_and_products():
    t=topic();initial=[candidate(1,'popular'),candidate(2,'popular'),candidate(3,'time'),candidate(4,'time')]
    new={'id':'portable','label':'持ち運びやすい手帳','keywords':['ポケットサイズ']}
    with patch('planning_runtime.propose_replacement_axis',return_value=new), \
         patch('growth_role_business.collect_unique_products',return_value=(initial,{'complete':False,'attempts':[]})):
        products,changes=_replace_unproductive_axes(t,initial,cache_only=False)
    assert t['comparison_axes'][1]['id']=='family'
    assert products==initial
    assert changes[0]['found']==0


def test_cache_only_does_not_invoke_planner():
    t=topic()
    with patch('planning_runtime.propose_replacement_axis',side_effect=AssertionError('no provider')):
        products,changes=_replace_unproductive_axes(t,[],cache_only=True)
    assert products==[] and changes==[]


def test_planner_rejects_duplicate_axis():
    t=topic()
    with patch('planning_runtime._planning_provider',return_value=('codex',['unused'],'gpt',True)), \
         patch('planning_runtime._invoke_planning',return_value={'axis':t['comparison_axes'][0]}):
        try:
            propose_replacement_axis(t,t['comparison_axes'][1],tried_axis_ids=['popular','family','time'])
        except PlanningError:
            pass
        else:raise AssertionError('duplicate axis accepted')
