"""Article QA pass receipt, bound to the exact reviewed article and evidence."""
from comparison_acceptance import digest, evidence_content, receipt_issues

class QAApprovalError(ValueError):
    pass

def certify(payload, evidence, qa, acceptance):
    if not isinstance(qa, dict) or qa.get('overall_pass') is not True:
        raise QAApprovalError('QA did not pass')
    if not isinstance(acceptance, dict) or (acceptance.get('review') or {}).get('status') != 'ACCEPTED':
        raise QAApprovalError('independent review not accepted')
    return {'schema': 'sellemy-qa-certification/v1', 'passed': True,
            'payload_sha256': digest(payload),
            'evidence_sha256': digest(evidence_content(evidence)),
            'qa_sha256': digest(qa), 'acceptance_sha256': digest(acceptance)}

def require_certification(receipt, payload, evidence, qa, acceptance):
    expected = certify(payload, evidence, qa, acceptance)
    if not isinstance(receipt, dict) or receipt != expected:
        raise QAApprovalError('missing or stale article QA certification')
