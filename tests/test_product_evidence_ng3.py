import tempfile,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_recovery
from fixtures import valid_evidence

def test_product_evidence_three_quality_ng_not_infrastructure():
 with tempfile.TemporaryDirectory() as tmp,patch.object(growth_recovery,'STATE_ROOT',Path(tmp)),patch.object(growth_recovery,'JOBS_ROOT',Path(tmp)/'jobs'),patch.object(growth_recovery,'EVENTS_PATH',Path(tmp)/'events.jsonl'),patch.object(growth_recovery,'EXECUTION_EVENTS_PATH',Path(tmp)/'execution.jsonl'),patch('operating_profile.normal_profile',return_value=True):
  growth_recovery.create_job(topic={'slug':'insufficient-product-facts','category':'gadget'},evidence=valid_evidence(),selection={},planning_provider=None,viability_probes=[])
  s=growth_recovery.mark_failure('insufficient-product-facts',stage='product',failure_class='runtime_infrastructure',failure_code='TRANSPORT',reason='timeout')
  assert s.get('quality_ng_by_gate',{}).get('product',0)==0
  for i in range(3):
   growth_recovery.write_artifact('insufficient-product-facts','product-search-progress.json',{'candidates':[{'asin':f'A{n}'} for n in range(i+1)]})
   s=growth_recovery.request_product_reselection('insufficient-product-facts',['facts insufficient'])
   assert s['status']=='ACTIVE'
  assert s['quality_ng_by_gate']['product']==3
  assert s['current_stage']=='WRITER_PENDING' and s['next_retry_at'] is not None
