import sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_recovery as r
from tests.fixtures import valid_evidence

def test_owner_remediation_resumes_only_verified_failed_writer():
 with tempfile.TemporaryDirectory() as tmp,patch.object(r,'STATE_ROOT',Path(tmp)),patch.object(r,'JOBS_ROOT',Path(tmp)/'jobs'),patch.object(r,'EVENTS_PATH',Path(tmp)/'events.jsonl'),patch.object(r,'EXECUTION_EVENTS_PATH',Path(tmp)/'execution.jsonl'),patch('operating_profile.normal_profile',return_value=True),patch('comparison_acceptance.product_issues',return_value=[]):
  r.create_job(topic={'slug':'unit-test','category':'gadget'},evidence=valid_evidence(),selection={},planning_provider={},viability_probes=[])
  for _ in range(10):r.mark_failure('unit-test',stage='writer',failure_class='role_quality',failure_code='QA',reason='bad')
  state=r.load_job('unit-test')
  r.write_artifact('unit-test','owner-quality-case.json',{'case_state':'AWAITING_OWNER_ANALYSIS','job_state_revision':state['updated_at'],'ng_count':10})
  try:r.reopen_writer_failed_after_owner_remediation('unit-test',reason='',verified_revision='1234567');assert False
  except ValueError:pass
  x=r.reopen_writer_failed_after_owner_remediation('unit-test',reason='image audit actually executed at Writer QA',verified_revision='3df911c')
  assert x['current_stage']=='QA_FAILED' and x['status']=='ACTIVE'
  assert x['quality_ng_by_gate']['writer']==0
  assert r.read_artifact('unit-test','owner-quality-case.json')['case_state']=='REMEDIATED'
