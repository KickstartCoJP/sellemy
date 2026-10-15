import tempfile,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_recovery
from fixtures import valid_evidence

def test_tenth_consecutive_normal_failed_requests_ceo_inspection():
 with tempfile.TemporaryDirectory() as tmp,patch.object(growth_recovery,'STATE_ROOT',Path(tmp)),patch.object(growth_recovery,'JOBS_ROOT',Path(tmp)/'jobs'),patch.object(growth_recovery,'EVENTS_PATH',Path(tmp)/'events.jsonl'),patch.object(growth_recovery,'EXECUTION_EVENTS_PATH',Path(tmp)/'execution.jsonl'),patch('operating_profile.normal_profile',return_value=True):
  for i in range(10):
   slug=f'failed-{i}'
   growth_recovery.create_job(topic={'slug':slug,'category':'gadget'},evidence=valid_evidence(),selection={},planning_provider=None,viability_probes=[])
   for _ in range(3):growth_recovery.mark_failure(slug,stage='writer',failure_class='role_quality',failure_code='QA_NG',reason='x')
  receipt=growth_recovery.read_artifact('failed-9','ceo-inspection-required.json')
  assert receipt['status']=='REQUIRES_CEO_INSPECTION' and len(receipt['slugs'])==10
  assert receipt['publication_blocked_by_this_receipt'] is False
  assert growth_recovery.read_artifact('failed-8','ceo-inspection-required.json',None) is None
