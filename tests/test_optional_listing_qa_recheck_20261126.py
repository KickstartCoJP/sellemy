"""Independent review must reconsider policy-only missing-source rejection."""
from unittest.mock import patch
from tests.quality_fixtures import accepted_fixture
from semantic_review import independent_review
from comparison_acceptance import receipt_issues


def test_rechecks_listing_only_product_revision_without_autopassing():
    topic,evidence,receipt=accepted_fixture()
    valid=receipt['review']
    old={**valid,'status':'REVISION_REQUIRED',
         'reason':'p1–p6 掲載タイトルはあるが、対応する確認済みの出典本文がなく引用できない',
         'products':[{**p,'adequate_facts':False} for p in valid['products']]}
    with patch('semantic_review.generate',side_effect=[(old,{}),(valid,{})]) as generate:
        result=independent_review(evidence)
    assert generate.call_count==2
    assert 'INDEPENDENT POLICY CONSISTENCY RECHECK' in generate.call_args.args[2]
    assert result['review']['status']=='ACCEPTED'
    assert receipt_issues(result,evidence)==[]


def test_does_not_retry_actual_off_topic_product():
    topic,evidence,receipt=accepted_fixture()
    valid=receipt['review']
    bad={**valid,'status':'BLOCKED','needs_product_reselection':True,
         'reason':'p1: product does not fit the topic',
         'products':[{**row,'topic_fit':False} if row['ref']=='p1' else row
                    for row in valid['products']]}
    with patch('semantic_review.generate',return_value=(bad,{})) as generate:
        result=independent_review(evidence)
    assert generate.call_count==1
    assert result['review']['status']=='BLOCKED'
