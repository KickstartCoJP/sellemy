import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import lifecycle_audit as a

def test_qa_block_does_not_misreport_publish_or_portal():
    artifacts={'topic.json':{'slug':'slug'},'product-search-progress.json':{'candidates':[]},
      'evidence.json':{'slug':'slug','products':[{'ref':f'p{i}'} for i in range(6)]},
      'payload.json':{'slug':'slug'},'acceptance.json':{'review':{'status':'ACCEPTED'}},
      'qa.json':{'overall_pass':False,'main_in_range':False,'product_image_audit_pass':True}}
    with patch.object(a,'load_job',return_value={'status':'ACTIVE','current_stage':'QA_FAILED'}),\
         patch.object(a,'read_artifact',side_effect=lambda s,k,d=None:artifacts.get(k,d)),\
         patch.object(a,'product_issues',return_value=[]),\
         patch.object(a,'receipt_issues',return_value=[]):
         result=a.inspect('slug')
    stages={r['stage']:r['status'] for r in result['stages']}
    assert stages['product_evidence']=='PASS'
    assert stages['independent_review']=='PASS'
    assert stages['machine_qa']=='BLOCKED'
    assert stages['image_qa']=='PASS'
    assert stages['publish']=='NOT_REACHED'
    assert stages['postpublish_portal']=='NOT_REACHED'
    assert not result['complete']

def test_not_reached_writer_is_not_a_pass():
    with patch.object(a,'load_job',return_value={'status':'ACTIVE','current_stage':'WRITER_PENDING'}),\
         patch.object(a,'read_artifact',side_effect=lambda s,k,d=None: {'slug':'slug'} if k=='topic.json' else {}):
        result=a.inspect('slug')
    stages={r['stage']:r['status'] for r in result['stages']}
    assert stages['writer']=='NOT_REACHED'
    assert stages['machine_qa']=='NOT_REACHED'
    assert not result['complete']
