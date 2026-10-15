import sys,tempfile,hashlib,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import ceo_audit_bridge

def test_audit_creates_separate_task_and_nonblocking_request():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        sample={'schema':'sellemy-ceo-sample/v1','slug':'test-slug','commit':'sha1','group_sha256':'a'*64}
        article=root/'test.html';article.write_text('<html>published</html>')
        evidence=root/'evidence.json'
        evidence.write_text(json.dumps({'products':[{'asin':f'B0TEST{i:04d}','amazon_title':f'Test {i}','image_url':f'https://example.org/{i}.png'} for i in range(6)]}))
        events=[]
        def fake_run(**kwargs):
            m=kwargs['module'];data=kwargs['payload'];events.append((m,data));
            if m.endswith('task_create'):
                d=root/'tasks'/data['task_id'];(d/'history').mkdir(parents=True,exist_ok=True);(d/'task.json').write_text('{}')
            return {'message_id':data.get('message_id'),'status':data.get('status')}
        with patch.object(ceo_audit_bridge,'_run_action',side_effect=fake_run),patch.dict('os.environ',{'SELLEMY_AI_OS_CANONICAL_ROOT':str(root)}):
            result=ceo_audit_bridge.submit_postpublish_audit(sample=sample,category='gadget',product_evidence_ref=str(evidence),article_file_path=str(article))
        assert result['required'] and len(events)==3
        assert events[-1][1]['status']=='review_required'
        assert events[-1][1]['role_id']=='os-chief'
        assert events[-1][1]['detail']['nonblocking_postpublish_audit'] is True
        assert 'CEO review artifact_url=https://www.sellemy.jp/article/gadget/test-slug.html' in events[-1][1]['detail']['next_action']
        assert events[0][1]['task_id'].startswith('TASK-BU002-POSTPUBLISH-CEO-AUDIT-')
