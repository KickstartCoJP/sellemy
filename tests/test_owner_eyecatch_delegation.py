import tempfile,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import growth_recovery as r
from fixtures import valid_evidence

def test_three_eyecatch_rejections_delegate_without_article_failure():
    with tempfile.TemporaryDirectory() as tmp,patch.object(r,'STATE_ROOT',Path(tmp)),patch.object(r,'JOBS_ROOT',Path(tmp)/'jobs'),patch.object(r,'EVENTS_PATH',Path(tmp)/'events.jsonl'),patch.object(r,'EXECUTION_EVENTS_PATH',Path(tmp)/'execution.jsonl'),patch('operating_profile.normal_profile',return_value=True):
        r.create_job(topic={'slug':'eyecatch-owner-case','category':'gadget'},evidence=valid_evidence(),selection={},planning_provider=None,viability_probes=[])
        for _ in range(3):
            state=r.mark_failure('eyecatch-owner-case',stage='eyecatch',failure_class='role_quality',failure_code='EYECATCH_GENERATION_FAILED',reason='image failed',role_return=True,role='designer')
        assert state['status']=='ACTIVE'
        assert state['current_stage']=='OWNER_EYECATCH_PENDING'
        assert r.eligible_job() is None
        assert r.read_artifact('eyecatch-owner-case','owner-quality-case.json')['case_state']=='OWNER_IMAGE_REQUESTED'
