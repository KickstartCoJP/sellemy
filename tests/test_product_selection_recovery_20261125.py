"""Deterministic regressions for the three 2026-11-25 real incident classes."""
import json
from types import SimpleNamespace
from unittest.mock import patch
from growth_role_business import _obsolete_identity_review
from product_selection import select_six

AXES=[{'id':id,'label':id,'keywords':[id]} for id in ('popular','day','night')]
TOPIC={'query':'hand cream','comparison_axes':AXES}

def candidate(i,axis):
    return {'asin':f'B{i:09d}', 'amazon_title':f'Brand{i} Hand Cream Model {i}',
            'brand':f'Brand{i}', 'image_url':f'https://example.org/{i}.jpg',
            'discovery_axis_id':axis,'sources':[]}


def products():
    return [candidate(i,axis) for axis,offset in [('popular',1),('day',11),('night',21)]
            for i in range(offset,offset+4)]


def test_old_identity_only_failure_can_be_retried_but_actual_product_failure_cannot():
    old={'issues':['independent review did not accept','p1: identity has no source anchor'],
         'review':{'products':[{'ref':'p1','topic_fit':True}]},'asins':['B000000001']}
    assert _obsolete_identity_review(old)
    genuine={**old,'issues':['p1: product does not fit the topic']}
    assert not _obsolete_identity_review(genuine)
    mixed={**old,'issues':[old['issues'][0],'p1: product does not fit the topic']}
    assert not _obsolete_identity_review(mixed)


def test_selector_corrects_invalid_json_and_normalizes_axis_order_without_losing_pool():
    pool=products()
    groups=[]
    for axis in AXES:
        chosen=[p for p in pool if p['discovery_axis_id']==axis['id']][:2]
        groups.append({'axis_id':axis['id'],'products':[{'asin':p['asin'],'reason':p['amazon_title']}
                                                     for p in chosen]})
    outputs=[{'axes':groups[:2]}, {'axes':list(reversed(groups))}]
    calls=[]
    def run(*args,**kwargs):
        calls.append(kwargs['input']); value=outputs[len(calls)-1]
        return SimpleNamespace(returncode=0,stdout=json.dumps({
            'type':'item.completed','item':{'type':'agent_message',
            'text':json.dumps(value)}},ensure_ascii=False),stderr='')
    with patch('product_selection.subprocess.run',side_effect=run), \
         patch('product_candidate_registry.is_ng',return_value=False), \
         patch('api_role_binding.require_codex_settings',return_value=('gpt-6','low')):
        selected,meta=select_six(pool,TOPIC,{})
    assert len(calls)==2 and 'SCHEMA CORRECTION' in calls[1]
    assert len(selected)==6 and len({p['asin'] for p in selected})==6
    assert [x['axis_id'] for x in meta['axes']]==[x['id'] for x in AXES]


def test_persistent_schema_errors_use_valid_candidates_but_still_require_product_qa():
    pool=products()
    def invalid(*args,**kwargs):
        return SimpleNamespace(returncode=0,stdout=json.dumps({
            'type':'item.completed','item':{'type':'agent_message','text':'{"axes": []}'}}),stderr='')
    with patch('product_selection.subprocess.run',side_effect=invalid) as selector, \
         patch('product_candidate_registry.is_ng',return_value=False), \
         patch('api_role_binding.require_codex_settings',return_value=('gpt-6','low')):
        selected,meta=select_six(pool,TOPIC,{})
    assert selector.call_count==3
    assert meta['method']=='schema_recovery_candidate_selection_pending_product_qa'
    assert len({p['asin'] for p in selected})==6
    assert all(sum(x['discovery_axis_id']==axis['id'] for x in selected)==2 for axis in AXES)


def test_legacy_quote_rejection_is_revisited_without_researching_six_products(tmp_path):
    import growth_role_business as business
    from unittest.mock import Mock
    slug='autumn-humidifiers-6-picks'
    pool=[candidate(i,axis['id']) for axis,index in zip(AXES,(1,11,21))
          for i in (index,index+1)]
    signature='|'.join(sorted(p['asin'] for p in pool))
    old={'issues':['independent review did not accept'] +
                  [f'p{i}: identity has no source anchor' for i in range(1,7)],
         'asins':[p['asin'] for p in pool],
         'review':{'products':[{'ref':f'p{i}','topic_fit':True} for i in range(1,7)]},
         'evidence':{'products':[dict(p,ref=f'p{i}') for i,p in enumerate(pool,1)]}}
    old_path=tmp_path/('product-review-'+signature.replace('|','-')+'.json')
    old_path.write_text(json.dumps(old))
    saved={'topic.json':dict(TOPIC,slug=slug,category='dailygoods'),
           'product-search-progress.json':{'candidates':pool,
                'reviewed_sets':[signature], 'researched_asins':[p['asin'] for p in pool],
                'excluded_asins':[],'queries':[]},
           'evidence.json':{}}
    review=Mock(return_value={'review':{'status':'ACCEPTED'}})
    replaced=Mock()
    def read(_slug,name,default=None):
        return old if name==old_path.name else saved.get(name,default)
    with patch.object(business,'read_artifact',side_effect=read), \
         patch.object(business,'job_dir',return_value=tmp_path), \
         patch.object(business,'write_artifact',side_effect=lambda _slug,name,value:saved.__setitem__(name,value)), \
         patch.object(business,'load_feedback',return_value={}), \
         patch.object(business,'independent_review',review), \
         patch.object(business,'product_issues',return_value=[]), \
         patch.object(business,'replace_product_evidence',replaced), \
         patch('safety_recall.unsafe_products',return_value=([],[])), \
         patch('product_candidate_registry.is_ng',return_value=False):
        business._reselect_products(slug,cache_only=False)
    assert review.call_count==1
    assert replaced.call_count==1
    assert saved['product-search-progress.json']['complete'] is True
