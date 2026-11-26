"""Nonbinding description guidance never reopens exact CEO-approved articles."""
import hashlib
from editorial_qa import (CONTRACT, VERSION, CANON_NONBINDING_BASE,
                          COMPATIBLE_HINT_ONLY_VERSIONS, _contract_for_version,
                          _compatible_contract, make_input, make_receipt, validate_verdict)
from quality_fixtures import accepted_fixture


def test_nonbinding_length_change_has_stable_policy_fingerprint():
    before=CONTRACT.replace('90–150 Japanese characters','60–120 Japanese characters')
    after=CONTRACT.replace('90–150 Japanese characters','120–180 Japanese characters')
    assert before!=CONTRACT!=after
    assert hashlib.sha256(_contract_for_version(before).encode()).hexdigest()==VERSION
    assert hashlib.sha256(_contract_for_version(after).encode()).hexdigest()==VERSION
    assert VERSION==CANON_NONBINDING_BASE
    assert all(_compatible_contract(v) for v in COMPATIBLE_HINT_ONLY_VERSIONS)


def test_real_editorial_policy_change_invalidates_historic_receipts():
    altered=CONTRACT.replace('Material false claims','Material false or missing claims')
    assert altered!=CONTRACT
    alternate=hashlib.sha256(_contract_for_version(altered).encode()).hexdigest()
    assert alternate!=VERSION
    assert not _compatible_contract(alternate)
    assert not _compatible_contract('untrusted-version')


def test_legacy_qa_receipt_requires_same_payload_subject_and_pass():
    payload,_,_=accepted_fixture()
    subject=make_input(payload)
    receipt=make_receipt({'verdict':'PASS','reason':'Grounded copy','quotes':[]},subject)
    assert not validate_verdict(receipt,subject)
    historic={**receipt,'contract_version':next(iter(COMPATIBLE_HINT_ONLY_VERSIONS))}
    assert not validate_verdict(historic,subject)
    altered={**subject,'article':{**subject['article'],'summary':'Different summary'}}
    assert 'stale article hash' in validate_verdict(historic,altered)
    revoked={**historic,'verdict':'FAIL','quotes':[]}
    assert validate_verdict(revoked,subject)
