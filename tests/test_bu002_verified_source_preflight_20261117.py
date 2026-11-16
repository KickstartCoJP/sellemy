"""Source-presence diagnostics stay available without imposing a universal Writer gate."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
from comparison_acceptance import verified_excerpt_issues
import role_stage_exec
from growth_role_business import GrowthRuntimeError


def evidence(with_sources=()):
    return {'products': [
        {'ref': f'p{i}', 'asin': f'ASIN{i}', 'sources': [
            {'verified': True, 'url': f'https://example.org/item/{i}', 'excerpt': f'actual source fact for product {i}'}]
            if i in with_sources else []}
        for i in range(1,7)]}


def test_missing_sources_are_specific_to_each_product_ref():
    assert verified_excerpt_issues(evidence((3,5,6))) == [
        'p1: verified source excerpt absent',
        'p2: verified source excerpt absent',
        'p4: verified source excerpt absent',
    ]
    assert verified_excerpt_issues(evidence(tuple(range(1,7)))) == []


def test_unverified_or_non_https_sources_are_not_counted():
    items=evidence(tuple(range(1,7)))
    items['products'][0]['sources'][0]['verified']=False
    items['products'][1]['sources'][0]['url']='http://example.org/item/2'
    items['products'][2]['sources'][0]['excerpt']=' '
    assert verified_excerpt_issues(items)==[
        'p1: verified source excerpt absent',
        'p2: verified source excerpt absent',
        'p3: verified source excerpt absent',
    ]


def test_absent_optional_excerpts_do_not_reopen_accepted_product_selection():
    item=evidence((3,5,6))
    artifacts={'evidence.json':item,'topic.json':{},'selection.json':{},'product-blocker.json':{}}
    with patch('growth_recovery.load_job',return_value={'source':'runtime','current_stage':'QA_FAILED','status':'ACTIVE'}), \
         patch('growth_recovery.read_artifact',side_effect=lambda slug,name,default=None:artifacts.get(name,default)), \
         patch('growth_recovery.request_product_reselection') as redirect:
        assert role_stage_exec._inspect('facial-oil-blotting-papers',cache_only=False)['action']=='writer'
        redirect.assert_not_called()


def test_complete_verified_sources_reach_writer():
    item=evidence(tuple(range(1,7)))
    artifacts={'evidence.json':item,'topic.json':{},'selection.json':{},'product-blocker.json':{}}
    with patch('growth_recovery.load_job',return_value={'source':'runtime','current_stage':'QA_FAILED','status':'ACTIVE'}), \
         patch('growth_recovery.read_artifact',side_effect=lambda slug,name,default=None:artifacts.get(name,default)), \
         patch('growth_recovery.request_product_reselection') as redirect:
        result=role_stage_exec._inspect('ready-six',cache_only=False)
        assert result['action']=='writer'
        redirect.assert_not_called()


def test_product_qa_pass_can_clear_stale_source_only_blocker():
    from comparison_acceptance import VERSION
    item=evidence((3,5,6))
    item['product_acceptance']={'contract_version':VERSION}
    artifacts={'evidence.json':item,'topic.json':{},'selection.json':{},
               'product-blocker.json':{'needs_product_reselection':True,'reasons':['missing excerpt']}}
    with patch('growth_recovery.load_job',return_value={'source':'runtime','current_stage':'WRITER_PENDING','status':'ACTIVE'}), \
         patch('growth_recovery.read_artifact',side_effect=lambda slug,name,default=None:artifacts.get(name,default)), \
         patch('growth_recovery.write_artifact') as write, \
         patch('comparison_acceptance.product_issues',return_value=[]):
        result=role_stage_exec._inspect('missing-source-blocker',cache_only=False)
    assert result['action']=='continue'
    write.assert_any_call('missing-source-blocker','product-blocker.json',{})
