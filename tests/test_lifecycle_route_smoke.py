"""Role owned recovery smoke tests using existing artifact files and isolated job state."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_recovery as gr
import role_stage_exec as worker
import growth_runtime as runtime
from quality_fixtures import accepted_fixture


def _state_root(tmp_path):
    return [patch.object(gr,'STATE_ROOT',tmp_path),
            patch.object(gr,'JOBS_ROOT',tmp_path/'jobs'),
            patch.object(gr,'EVENTS_PATH',tmp_path/'events.jsonl'),
            patch.object(gr,'EXECUTION_EVENTS_PATH',tmp_path/'execution.jsonl')]


def test_accepted_product_qa_clear_old_blocker_but_missing_independent_receipts_blocks_publish(tmp_path):
    payload,evidence,acceptance=accepted_fixture()
    slug=evidence['slug']
    patches=_state_root(tmp_path)
    for item in patches:item.start()
    try:
        gr.create_job(topic={'slug':slug,'category':evidence['category']},
                      evidence=evidence,selection={},planning_provider={},viability_probes=[])
        gr.write_artifact(slug,'product-blocker.json',{'needs_product_reselection':True})
        with patch('comparison_acceptance.product_issues',return_value=[]):
            receipt=worker._inspect(slug,cache_only=True)
        assert receipt['action']=='continue'
        assert gr.read_artifact(slug,'product-blocker.json')=={}
        gr.write_artifact(slug,'payload.json',payload)
        gr.write_artifact(slug,'acceptance.json',acceptance)
        gr.set_stage(slug,'EYECATCH_PENDING')
        with pytest.raises(RuntimeError,match='independent QA revalidation'):
            worker._inspect(slug,cache_only=True)
        assert gr.load_job(slug)['current_stage']=='QA_FAILED'
    finally:
        for item in reversed(patches):item.stop()


def test_unaccepted_product_qa_never_reaches_writer_or_publish(tmp_path):
    payload,evidence,_=accepted_fixture()
    evidence.pop('product_acceptance',None)
    slug=evidence['slug']
    patches=_state_root(tmp_path)
    for item in patches:item.start()
    try:
        gr.create_job(topic={'slug':slug,'category':evidence['category']},
                      evidence=evidence,selection={},planning_provider={},viability_probes=[])
        gr.write_artifact(slug,'product-blocker.json',{'needs_product_reselection':True})
        with patch('semantic_review.independent_review',return_value={'review': {'status':'BLOCKED'}}), \
             patch('comparison_acceptance.product_issues',return_value=['missing product acceptance']):
            receipt=worker._inspect(slug,cache_only=True)
        assert receipt['action']=='product'
        assert gr.load_job(slug)['current_stage']=='WRITER_PENDING'
    finally:
        for item in reversed(patches):item.stop()
