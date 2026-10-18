import sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_recovery as recovery
import growth_runtime as runtime
from quality_fixtures import accepted_fixture


def recovery_state_context(temp):
    root=Path(temp)
    return [patch.object(recovery,'STATE_ROOT',root),patch.object(recovery,'JOBS_ROOT',root/'jobs'),
            patch.object(recovery,'EVENTS_PATH',root/'events.jsonl'),
            patch.object(recovery,'EXECUTION_EVENTS_PATH',root/'execution.jsonl')]


def test_accepted_product_qa_clear_old_blocker_writer_review_and_reach_publish_gate():
    payload,evidence,receipt=accepted_fixture();slug=evidence['slug']
    with tempfile.TemporaryDirectory() as temp:
        patches=recovery_state_context(temp)
        for p in patches:p.start()
        try:
            recovery.create_job(topic={'slug':slug,'category':evidence['category']},evidence=evidence,selection={},planning_provider={},viability_probes=[])
            recovery.write_artifact(slug,'product-blocker.json',{'needs_product_reselection':True})
            def writer(s,r):
                recovery.write_artifact(s,'payload.json',payload)
                recovery.write_artifact(s,'acceptance.json',receipt)
                recovery.set_stage(s,'EYECATCH_PENDING')
            with patch.object(runtime,'_reselect_products') as reselection, \
                 patch.object(runtime,'_writer_stage',side_effect=writer) as writer_call, \
                 patch.object(runtime,'_eyecatch_and_publish',return_value='test-commit') as publish:
                commit=runtime._execute_job(slug,{})
            assert commit=='test-commit'
            assert writer_call.call_count==1
            assert publish.call_count==1
            reselection.assert_not_called()
            assert not recovery.read_artifact(slug,'product-blocker.json',{})
            assert recovery.read_artifact(slug,'product-search-progress.json',{})['complete']
        finally:
            for p in reversed(patches):p.stop()


def test_unaccepted_product_qa_never_reaches_writer_or_publish():
    payload,evidence,_=accepted_fixture();evidence.pop('product_acceptance');slug=evidence['slug']
    with tempfile.TemporaryDirectory() as temp:
        patches=recovery_state_context(temp)
        for p in patches:p.start()
        try:
            recovery.create_job(topic={'slug':slug,'category':evidence['category']},evidence=evidence,selection={},planning_provider={},viability_probes=[])
            recovery.write_artifact(slug,'product-blocker.json',{'needs_product_reselection':True})
            with patch.object(runtime,'_reselect_products',side_effect=runtime.GrowthRuntimeError('need research')) as research, \
                 patch.object(runtime,'_writer_stage') as writer, \
                 patch.object(runtime,'_eyecatch_and_publish') as publish:
                try:runtime._execute_job(slug,{})
                except runtime.GrowthRuntimeError:pass
                else:assert False, 'unaccepted product evidence must fail closed'
            research.assert_called_once();writer.assert_not_called();publish.assert_not_called()
        finally:
            for p in reversed(patches):p.stop()
