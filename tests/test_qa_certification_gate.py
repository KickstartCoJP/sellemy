import copy
import pytest
from qa_certification import certify, require_certification, QAApprovalError
from fixtures import valid_payload,valid_evidence

def test_pass_receipt_is_bound_to_qa_review_payload_and_evidence():
    p,e=valid_payload(),valid_evidence()
    qa={'overall_pass':True,'checks':['ok']}
    accepted={'review':{'status':'ACCEPTED'},'reviewer':'independent_responses_review'}
    cert=certify(p,e,qa,accepted)
    require_certification(cert,p,e,qa,accepted)
    changed=copy.deepcopy(p);changed['lead'] += '改変'
    with pytest.raises(QAApprovalError):
        require_certification(cert,changed,e,qa,accepted)
    with pytest.raises(QAApprovalError):
        require_certification({},p,e,qa,accepted)
    with pytest.raises(QAApprovalError):
        require_certification(cert,p,e,{'overall_pass':False},accepted)
    with pytest.raises(QAApprovalError):
        require_certification(cert,p,e,qa,{'review':{'status':'BLOCKED'}})
