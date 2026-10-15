import sys,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from ceo_audit_feedback import reconcile_feedback

def test_ceo_rework_and_idempotent_accepted_are_nonblocking():
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);(root/'role-communication/sent').mkdir(parents=True)
  docs={('a','postpublish-ceo-delivered.json'):{'message_id':'CEO-AUDIT-AAA'}}
  def read(slug,key,default=None):return docs.get((slug,key),default)
  def write(slug,key,value):docs[(slug,key)]=value
  row={'message_id':'CEO-REVIEW-CURRENT-123','from':'portal-ops','to':'sellemy-ops',
       'body':json.dumps({'review_submission_message_id':'CEO-AUDIT-AAA',
                           'review_complete':True,'review_decision':'差戻し',
                           'review_handoff':'Restore mismatched product proof'})}
  (root/'role-communication/sent/CEO-REVIEW-CURRENT-123.json').write_text(json.dumps(row))
  jobs=[{'slug':'a','published_commit':'sha1','status':'PUBLISHED','operating_profile':'normal'}]
  result=reconcile_feedback(canonical_root=root,jobs=jobs,read_artifact=read,write_artifact=write)
  assert result==[{'slug':'a','outcome':'REWORK'}]
  assert docs[('a','owner-ceo-rework.json')]['no_automatic_republish'] is True
  assert docs[('a','postpublish-ceo-result.json')]['published_article_remains_live'] is True
  assert reconcile_feedback(canonical_root=root,jobs=jobs,read_artifact=read,write_artifact=write)==[]
