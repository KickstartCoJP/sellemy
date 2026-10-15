import sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import ceo_failure_bridge

def test_tenth_quality_failure_creates_separate_ceo_inspection_task():
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);events=[]
  def fake_run(**kwargs):
   data=kwargs['payload'];events.append(data)
   if kwargs['module'].endswith('task_create'):
    folder=root/'tasks'/data['task_id'];(folder/'history').mkdir(parents=True);(folder/'task.json').write_text('{}')
   return {'message_id':data['message_id']}
  receipt={'schema':'sellemy-failed-10-inspection/v1','status':'REQUIRES_CEO_INSPECTION','slugs':[f'x-{i}' for i in range(10)]}
  with patch.object(ceo_failure_bridge,'_run_action',side_effect=fake_run),patch.dict('os.environ',{'SELLEMY_AI_OS_CANONICAL_ROOT':str(root)}):
   result=ceo_failure_bridge.submit_failed_ten_inspection(receipt)
  assert result['required']
  assert len(events)==2 and events[-1]['status']=='user_decision_required'
  assert events[-1]['role_id']=='os-chief'
