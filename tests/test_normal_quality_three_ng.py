import tempfile,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_recovery
from fixtures import valid_evidence

def test_tenth_quality_ng_requests_ceo_inspection_but_infra_does_not():
    with tempfile.TemporaryDirectory() as tmp,patch.object(growth_recovery,'STATE_ROOT',Path(tmp)),patch.object(growth_recovery,'JOBS_ROOT',Path(tmp)/'jobs'),patch.object(growth_recovery,'EVENTS_PATH',Path(tmp)/'events.jsonl'),patch.object(growth_recovery,'EXECUTION_EVENTS_PATH',Path(tmp)/'execution.jsonl'),patch('operating_profile.normal_profile',return_value=True):
        growth_recovery.create_job(topic={'slug':'quality-test','category':'gadget'},evidence=valid_evidence(),selection={},planning_provider=None,viability_probes=[])
        for i in range(6):
            state=growth_recovery.mark_failure('quality-test',stage='writer',failure_class='runtime_infrastructure',failure_code='TRANSPORT',reason='temporary')
        assert state['status']=='ACTIVE'
        assert state.get('quality_ng_by_gate',{}).get('writer',0)==0
        for i in range(9):
            state=growth_recovery.mark_failure('quality-test',stage='writer',failure_class='role_quality',failure_code='QA_NG',reason='contradiction')
            assert state['status']=='ACTIVE'
        state=growth_recovery.mark_failure('quality-test',stage='writer',failure_class='role_quality',failure_code='QA_NG',reason='contradiction')
        assert state['status']=='FAILED' and state['quality_ng_by_gate']['writer']==10
        assert growth_recovery.read_artifact('quality-test','ceo-inspection-required.json')['failure_count']==10
