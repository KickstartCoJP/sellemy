import os,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from tests.fixtures import valid_payload,valid_evidence
from growth_runtime import evaluate_candidate

def test_writer_qa_receives_real_image_audit_when_mandatory():
    p,e=valid_payload(),valid_evidence()
    with patch.dict(os.environ,{'SELLEMY_REQUIRE_PRODUCT_IMAGE_AUDIT':'1'}), \
         patch('product_image_audit.audit_remote_product_images',return_value={'status':'PASS'}) as audit:
        _,_,q=evaluate_candidate(p,e,require_semantic_acceptance=False)
    audit.assert_called_once()
    assert q['product_image_audit_pass'] is True

def test_writer_qa_does_not_bypass_failed_image_audit():
    p,e=valid_payload(),valid_evidence()
    with patch.dict(os.environ,{'SELLEMY_REQUIRE_PRODUCT_IMAGE_AUDIT':'1'}), \
         patch('product_image_audit.audit_remote_product_images',return_value={'status':'FAIL'}):
        _,_,q=evaluate_candidate(p,e,require_semantic_acceptance=False)
    assert q['product_image_audit_pass'] is False
    assert not q['overall_pass']
