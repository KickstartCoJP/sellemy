import sys,tempfile,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import pytest
if not Path('/Users/kickstart/ai-management-os').exists():
    pytest.skip('AI OS integration test requires connected Mac Studio runtime',allow_module_level=True)
sys.path.insert(0,'/Users/kickstart/ai-management-os')
from ai_management_os.task_files import TaskFiles
import ceo_audit_bridge,ceo_failure_bridge

class Members:
 def member(self,role):return {'active':True,'role_id':role}
 def project(self,project_id):return {'chief_role_id':'sellemy-ops'} if project_id=='BU-002' else None

def test_real_taskfiles_accept_postpublish_audit_and_failed_ten():
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);tasks=TaskFiles(root/'tasks',members=Members())
  article=root/'test.html';article.write_text('<html>published</html>')
  evidence=root/'evidence.json'
  evidence.write_text(json.dumps({'products':[{'asin':f'B0TEST{i:04d}','amazon_title':f'Test {i}','image_url':f'https://example.org/{i}.png'} for i in range(6)]}))
  def fake_action(**kwargs):
   module=kwargs['module'];payload=kwargs['payload']
   if module.endswith('task_create'):return tasks.create(**payload)
   if module.endswith('task_append'):return tasks.append(**payload)
   raise AssertionError('unknown module')
  with patch.object(ceo_audit_bridge,'_run_action',side_effect=fake_action),patch.object(ceo_failure_bridge,'_run_action',side_effect=fake_action),patch.dict('os.environ',{'SELLEMY_AI_OS_CANONICAL_ROOT':str(root)}):
   sample={'schema':'sellemy-ceo-sample/v1','slug':'test-article','commit':'sha','group_sha256':'a'*64}
   a=ceo_audit_bridge.submit_postpublish_audit(sample=sample,category='gadget',product_evidence_ref=str(evidence),article_file_path=str(article))
   assert tasks.read(a['task_id'])['latest']['status']=='review_required'
   assert tasks.read(a['task_id'])['latest']['role_id']=='sellemy-ops'
   b=ceo_audit_bridge.submit_postpublish_audit(sample=sample,category='gadget',product_evidence_ref=str(evidence),article_file_path=str(article))
   assert b['duplicate'] is True
   failed={'schema':'sellemy-failed-10-inspection/v1','status':'REQUIRES_CEO_INSPECTION','slugs':[f'failed-{i}' for i in range(10)]}
   c=ceo_failure_bridge.submit_failed_ten_inspection(failed)
   assert tasks.read(c['task_id'])['latest']['status']=='user_decision_required'
   d=ceo_failure_bridge.submit_failed_ten_inspection(failed)
   assert d['duplicate'] is True
