"""A previous claim PASS is reusable only for the identical product and source."""
from copy import deepcopy
from unittest.mock import patch
from claim_qa import make_receipt,reuse_snapshot,reusable_findings,validate_receipt
from independent_quality_gate import issue_receipts


def subject():
    return {'article':{'products':[{'ref':f'p{i}','h3':f'Product{i}',
               'description':f'容量{i}ml'} for i in range(1,7)],
            'lead':'選ぶための案内', 'summary':'用途別の案内'},
            'verified_sources':{f'p{i}':f'容量{i}ml' for i in range(1,7)},
            'comparison_axes':[]}


def passed_receipt(s):
    return make_receipt({'verdict':'PASS','reason':'six products grounded',
        'findings':[{'product_ref':f'p{i}','article_quote':f'容量{i}ml',
                     'evidence_quote':f'容量{i}ml', 'verdict':'SUPPORTED', 'reason':'same ref'}
                    for i in range(1,7)]},s)


def test_exact_subject_reuses_all_six_claims():
    s=subject(); snapshot=reuse_snapshot(s,passed_receipt(s))
    assert len(reusable_findings(s,snapshot))==6
    with patch('independent_quality_gate.require_acceptance'), \
         patch('independent_quality_gate.editorial_input',return_value={}), \
         patch('independent_quality_gate.editorial_prompt',return_value='editorial'), \
         patch('independent_quality_gate.editorial_receipt',return_value={'verdict':'PASS'}), \
         patch('independent_quality_gate.claim_input',return_value=s):
        calls=[]
        def reviewer(prompt):
            calls.append(prompt)
            return {'verdict':'PASS'}
        cert=issue_receipts({}, {}, {}, judge=reviewer, prior_claim_snapshot=snapshot)
    assert calls==['editorial']
    assert validate_receipt(cert['claim'],s)==[]


def test_changed_product_rechecks_only_changed_ref_and_merges_valid_full_receipt():
    old=subject(); current=deepcopy(old)
    current['article']['products'][1]['description']='容量2ml、交換機能'
    current['verified_sources']['p2']='容量2ml、交換機能'
    snapshot=reuse_snapshot(old,passed_receipt(old))
    assert len(reusable_findings(current,snapshot))==5
    with patch('independent_quality_gate.require_acceptance'), \
         patch('independent_quality_gate.editorial_input',return_value={}), \
         patch('independent_quality_gate.editorial_prompt',return_value='editorial'), \
         patch('independent_quality_gate.editorial_receipt',return_value={'verdict':'PASS'}), \
         patch('independent_quality_gate.claim_input',return_value=current):
        prompts=[]
        def reviewer(prompt):
            prompts.append(prompt)
            if prompt=='editorial':return {'verdict':'PASS'}
            assert 'Product1' not in prompt and 'Product2' in prompt
            return {'verdict':'PASS','reason':'changed ref checked',
                    'findings':[{'product_ref':'p2','article_quote':'交換機能',
                        'evidence_quote':'交換機能','verdict':'SUPPORTED','reason':'verified'}]}
        cert=issue_receipts({}, {}, {}, judge=reviewer, prior_claim_snapshot=snapshot)
    assert len(prompts)==2
    assert len(cert['claim']['findings'])==6
    assert validate_receipt(cert['claim'],current)==[]


def test_shared_prose_change_invalidates_all_six():
    s=subject(); snap=reuse_snapshot(s,passed_receipt(s)); changed=deepcopy(s)
    changed['article']['lead']='用途に合わせて使えます'
    assert reusable_findings(changed,snap)=={}


def test_source_change_invalidates_only_affected_product():
    s=subject(); snap=reuse_snapshot(s,passed_receipt(s)); changed=deepcopy(s)
    changed['verified_sources']['p5']='新しい別資料'
    assert set(reusable_findings(changed,snap)) == {'p1','p2','p3','p4','p6'}


def test_failed_or_invalid_old_claim_cannot_seed_reuse():
    s=subject(); old=passed_receipt(s)
    old['findings'][0]['evidence_quote']='捏造した値'
    assert reuse_snapshot(s,old)=={}
    assert reuse_snapshot(s,{'verdict':'FAIL'})=={}


def test_rechecked_product_fail_is_not_overridden_by_five_cached_passes():
    from independent_quality_gate import QualityGateError
    old=subject(); current=deepcopy(old)
    current['article']['products'][1]['description']='容量2ml、根拠のない性能'
    snapshot=reuse_snapshot(old,passed_receipt(old))
    with patch('independent_quality_gate.require_acceptance'), \
         patch('independent_quality_gate.editorial_input',return_value={}), \
         patch('independent_quality_gate.editorial_prompt',return_value='editorial'), \
         patch('independent_quality_gate.editorial_receipt',return_value={'verdict':'PASS'}), \
         patch('independent_quality_gate.claim_input',return_value=current):
        def reviewer(prompt):
            if prompt=='editorial':return {'verdict':'PASS'}
            return {'verdict':'FAIL','reason':'unsupported claim',
                    'findings':[{'product_ref':'p2','article_quote':'根拠のない性能',
                        'evidence_quote':'','verdict':'UNSUPPORTED','reason':'no source'}]}
        try:
            issue_receipts({}, {}, {},judge=reviewer,prior_claim_snapshot=snapshot)
        except QualityGateError as exc:
            assert exc.gate=='claim' and '根拠のない性能' in str(exc)
        else:
            raise AssertionError('changed unsupported claim must fail')


def test_valid_failed_claim_receipt_preserves_other_five_independently_passed_items():
    from claim_qa import merge_reuse_snapshots
    s=subject(); bad=passed_receipt(s)
    bad['verdict']='FAIL'; bad['reason']='p2 unsupported'
    bad['findings'][1]['verdict']='UNSUPPORTED'
    bad['findings'][1]['evidence_quote']=''
    bad=make_receipt(bad,s)
    snapshot=reuse_snapshot(s,bad)
    assert set(reusable_findings(s,snapshot))=={'p1','p3','p4','p5','p6'}
    next_subject=deepcopy(s)
    next_subject['article']['products'][1]['description']='容量2ml、修正済み'
    combined=merge_reuse_snapshots(next_subject,snapshot)
    assert set(reusable_findings(next_subject,combined))=={'p1','p3','p4','p5','p6'}


def test_failed_claim_return_retains_signed_partial_receipt_without_passing_gate():
    from independent_quality_gate import QualityGateError
    s=subject()
    with patch('independent_quality_gate.require_acceptance'), \
         patch('independent_quality_gate.editorial_input',return_value={}), \
         patch('independent_quality_gate.editorial_prompt',return_value='editorial'), \
         patch('independent_quality_gate.editorial_receipt',return_value={'verdict':'PASS'}), \
         patch('independent_quality_gate.claim_input',return_value=s):
        def reviewer(prompt):
            if prompt=='editorial':return {'verdict':'PASS'}
            out=passed_receipt(s)
            out['verdict']='FAIL';out['reason']='p2 unsupported'
            out['findings'][1]['verdict']='UNSUPPORTED'
            out['findings'][1]['evidence_quote']=''
            return out
        try:
            issue_receipts({}, {}, {},judge=reviewer)
        except QualityGateError as exc:
            assert exc.gate=='claim'
            assert exc.claim_receipt['verdict']=='FAIL'
            assert set(reusable_findings(s,reuse_snapshot(exc.claim_subject,exc.claim_receipt)))=={
                'p1','p3','p4','p5','p6'}
        else:
            raise AssertionError('FAIL must never authorize publication')
