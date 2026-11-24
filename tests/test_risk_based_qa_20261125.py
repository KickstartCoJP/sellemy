"""The CEO's risk-based admission rules preserve actual safety/product vetoes."""
from unittest.mock import patch
from quality_fixtures import accepted_fixture
from comparison_acceptance import review_issues,product_issues
from growth_role_business import _verified_product,collect_unique_products
from product_selection import select_six,ProductSelectionError
from safety_recall import unsafe_products

AXES=[{'id':a,'label':a,'keywords':[a]} for a in ('popular','regular','compact')]
TOPIC={'query':'kitchen accessory','comparison_axes':AXES}


def candidate(i,axis):
    return {'asin':f'B{i:09d}', 'amazon_title':f'Brand{i} Kitchen Model-{i:02d}',
            'brand':f'Brand{i}', 'image_url':f'https://example.org/{i}.jpg',
            'discovery_axis_id':axis,'sources':[]}


def test_six_distinct_listings_can_be_collected_without_manufacturer_excerpts():
    result={key:[candidate(i,key) for i in range(offset,offset+2)]
            for key,offset in (('popular',1),('regular',3),('compact',5))}
    with patch('growth_role_business.discover_products',side_effect=lambda topic,cache_only:result[topic['query'].split()[-1]]), \
         patch('growth_role_business.fetch_amazon_item_source',return_value=None), \
         patch('growth_role_business.research_selected',side_effect=AssertionError('no manufacturer crawl required')):
        products,trace=collect_unique_products(TOPIC,cache_only=False)
    assert trace['complete'] and len(products)==6
    assert all(not row.get('sources') for row in products)
    selected,receipt=select_six(products,TOPIC,{})
    assert len(selected)==6
    assert all('Kitchen Model' in row['selection_reason'] for row in selected)


def test_independent_product_review_accepts_listing_identity_without_duplicate_proof_grid():
    _,e,a=accepted_fixture()
    review=a['review'].copy()
    review['products']=[{**p,'identity_quote':'','adequate_facts':False,'axis_support':[]}
                        for p in review['products']]
    review['duplicate_pair_checks']=[]
    assert review_issues(review,e)==[]


def test_real_duplicate_or_false_identity_remains_blocked():
    _,e,a=accepted_fixture()
    e['products'][1]['amazon_title']=e['products'][0]['amazon_title']
    assert any('same' in issue or 'duplicate' in issue
               for issue in product_issues(e))
    _,original,acceptance=accepted_fixture()
    review=acceptance['review'].copy()
    review['products']=[{**p,'identity_key':'fabricated-identity'} if p['ref']=='p1' else p
                        for p in review['products']]
    assert any('identity has no source anchor' in issue for issue in review_issues(review,original))


def test_unsupported_material_claim_still_requires_actual_source():
    from claim_qa import make_input,make_receipt
    p,e,_=accepted_fixture()
    subject=make_input(p,e)
    finding=[]
    for ref,row in [(p['ref'],p) for p in subject['article']['products']]:
        finding.append({'product_ref':ref,'article_quote':row['description'],
                        'evidence_quote':'', 'verdict':'NO_CLAIM','reason':'descriptive use only'})
    receipt=make_receipt({'verdict':'PASS','reason':'No consequential unsupported assertions','findings':finding},subject)
    assert receipt['verdict']=='PASS'
    unsupported=dict(finding[0],verdict='SUPPORTED')
    with __import__('pytest').raises(ValueError,match='source proof'):
        make_receipt({'verdict':'PASS','reason':'unsupported source','findings':[unsupported]+finding[1:]},subject)


def test_article_review_reuses_unchanged_accepted_product_receipt_not_second_llm():
    from semantic_review import independent_review
    p,e,a=accepted_fixture()
    with patch('semantic_review.generate',side_effect=AssertionError('duplicate Product LLM call')):
        receipt=independent_review(e,p)
    assert receipt['review']['status']=='ACCEPTED'
    assert receipt['metadata']['reused_product_qa'] is True
    assert receipt['payload_sha256'] is not None
    from comparison_acceptance import receipt_issues
    assert receipt_issues(receipt,e,p)==[]
