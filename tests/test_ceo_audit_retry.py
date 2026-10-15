import sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_runtime as g

def test_ceo_audit_transport_error_retries_nonblocking():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        job={'slug':'sample','status':'PUBLISHED','operating_profile':'normal','category':'gadget','published_commit':'one'}
        docs={('sample','postpublish-ceo-selection.json'):{'schema':'sellemy-ceo-sample/v1','slug':'sample','commit':'one','group_sha256':'a'*64}}
        dispatched=[]
        def read(slug,name,default=None):return docs.get((slug,name),default)
        def write(slug,name,val):docs[(slug,name)]=val
        with patch('operating_profile.normal_profile',return_value=True),patch.object(g,'ROOT',root),patch.object(g,'all_jobs',return_value=[job]),patch.object(g,'read_artifact',side_effect=read),patch.object(g,'write_artifact',side_effect=write),patch('ceo_audit_bridge.submit_postpublish_audit',side_effect=[ConnectionError('offline'),{'task_id':'TASK-OK'}]) as submit:
            (root/'config').mkdir();(root/'config/operating-profile.json').write_text('{"postpublish_ceo_enabled":true}')
            result={};g._reconcile_ceo_audit_delivery(result)
            assert len(result['postpublish_ceo_delivery_errors'])==1
            assert ('sample','postpublish-ceo-delivered.json') not in docs
            result={};g._reconcile_ceo_audit_delivery(result)
            assert docs[('sample','postpublish-ceo-delivered.json')]['task_id']=='TASK-OK'
            g._reconcile_ceo_audit_delivery({})
            assert submit.call_count==2
