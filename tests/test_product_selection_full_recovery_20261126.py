"""Real incident regressions for changing axes and changed candidate pools."""
import copy
import json
from unittest.mock import Mock, patch

import growth_role_business as business
import growth_recovery as recovery
from tests.test_product_selection_recovery_20261125 import candidate, AXES


def test_current_axis_reselection_ignores_obsolete_pins_and_preserves_valid_pins(tmp_path):
    """Hand cream: an older review contains 3 obsolete-axis products and one absent ASIN."""
    slug = 'hand-cream-recovery'
    topic = {'slug': slug, 'category': 'beauty', 'comparison_axes': AXES}
    current = [candidate(i, axis['id']) for axis, offset in zip(AXES, (1, 11, 21))
               for i in range(offset, offset + 10)]
    old_rows = [candidate(1,'popular'), candidate(2,'popular'),
                candidate(50,'removed_day'), candidate(51,'removed_day'),
                candidate(52,'removed_night'), candidate(53,'removed_night')]
    old_rows = [dict(p, ref=f'p{i}', sources=[{'verified':True,'excerpt':p['amazon_title']}])
                for i,p in enumerate(old_rows,1)]
    old = {'issues':['independent review did not accept','p4: product does not fit the topic'],
           'asins':[p['asin'] for p in old_rows],
           'evidence':{'products':old_rows},
           'review':{'products':[{'ref':p['ref'],'topic_fit':i!=4,
                                 'identity_key':p['amazon_title']} for i,p in enumerate(old_rows,1)]}}
    path = tmp_path / 'product-review-prior.json'
    path.write_text(json.dumps(old))
    saved={'topic.json':topic,'evidence.json':{},
           'product-search-progress.json':{'candidates':current,'reviewed_sets':[],
           'researched_asins':[x['asin'] for x in current], 'queries':[], 'excluded_asins':[]}}
    def read(_slug,name,default=None):
        return old if name==path.name else saved.get(name,default)
    seen=[]
    def select(pool, topic, feedback, pinned=None):
        seen.extend(x['asin'] for x in (pinned or []))
        picked=[next(x for x in pool if x['asin']==f'B{n:09d}') for n in (1,2,11,12,21,22)]
        return picked,{'axes':[]}
    with patch.object(business,'read_artifact',side_effect=read), \
         patch.object(business,'job_dir',return_value=tmp_path), \
         patch.object(business,'write_artifact',side_effect=lambda _,name,value:saved.__setitem__(name,value)), \
         patch.object(business,'load_feedback',return_value={}), \
         patch.object(business,'select_six',side_effect=select), \
         patch.object(business,'build_evidence',return_value={'products':[]}), \
         patch.object(business,'independent_review',return_value={'review':{'status':'ACCEPTED'}}), \
         patch.object(business,'product_issues',return_value=[]), \
         patch.object(business,'replace_product_evidence',return_value={}) as replace, \
         patch('safety_recall.unsafe_products',return_value=([],[])), \
         patch('product_candidate_registry.is_ng',return_value=False), \
         patch('product_candidate_registry.set_ng') as set_ng:
        business._reselect_products(slug, cache_only=False)
    assert seen == ['B000000001','B000000002']
    set_ng.assert_not_called()
    replace.assert_called_once()
    assert saved['product-search-progress.json']['complete'] is True


def test_fingerprint_uses_candidate_and_axis_changes_not_only_verified_sources(tmp_path):
    slug='changed-pool'
    with patch.object(recovery,'STATE_ROOT',tmp_path), \
         patch.object(recovery,'JOBS_ROOT',tmp_path/'jobs'), \
         patch.object(recovery,'EVENTS_PATH',tmp_path/'events.jsonl'), \
         patch('operating_profile.normal_profile',return_value=False), \
         patch('product_candidate_registry.is_ng',return_value=False):
        from tests.fixtures import valid_evidence
        state=recovery.create_job(topic={'slug':slug,'category':'gadget'}, evidence=valid_evidence(),
                        selection={},planning_provider={},viability_probes=[])
        recovery.write_artifact(slug,'topic.json', {'slug':slug,'comparison_axes':[{'id':'popular','label':'Popular'}]})
        products=[candidate(1,'popular')]
        recovery.write_artifact(slug,'product-search-progress.json', {'candidates':products})
        recovery.request_product_reselection(slug,['product selection unavailable'])
        # Retrying identical data is stopped.
        recovery.request_product_reselection(slug,['product selection unavailable'])
        assert recovery.load_job(slug)['last_failure_code']=='UNCHANGED_PRODUCT_SEARCH'
        recovery.write_artifact(slug,'product-search-progress.json', {'candidates':[candidate(1,'popular'), candidate(2,'popular')]})
        recovery.request_product_reselection(slug,['product selection unavailable'])
        assert recovery.load_job(slug)['status']=='ACTIVE'
        recovery.write_artifact(slug,'topic.json', {'slug':slug,'comparison_axes':[{'id':'day','label':'Day'}]})
        recovery.request_product_reselection(slug,['product selection unavailable'])
        assert recovery.load_job(slug)['status']=='ACTIVE'


def test_editorial_feedback_targets_repeated_product_closings():
    from writer_runtime import _prompt
    from tests.quality_fixtures import accepted_fixture
    topic, evidence, _ = accepted_fixture()
    draft={'slug':topic['slug'],'products':[]}
    prompt=_prompt(topic,evidence,previous_payload=draft,gate_feedback={
        'independent_quality_gate':{'gate':'editorial','issues':[
            'p3: repeats the same recommendation in its closing sentence']}})
    assert 'MANDATORY EDITORIAL REVISION' in prompt
    assert 'remove that sentence or replace it' in prompt
    assert 'p3: repeats the same recommendation' in prompt
    assert 'change the six product assignments' not in prompt
