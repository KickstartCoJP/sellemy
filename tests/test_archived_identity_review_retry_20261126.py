"""Obsolete archival review must not exhaust currently eligible products."""
import json
from unittest.mock import patch,Mock
import growth_role_business as business
from tests.test_product_selection_recovery_20261125 import candidate, AXES


def test_old_quote_only_review_is_removed_from_historical_reviewed_sets(tmp_path):
    slug='humidifier-legacy-citation'
    six=[candidate(i,axis) for axis,indices in zip(('popular','day','night'),((1,2),(11,12),(21,22))) for i in indices]
    signature='|'.join(sorted(p['asin'] for p in six))
    review={'asins':[p['asin'] for p in six],
            'evidence':{'products':[dict(p,ref=f'p{i}') for i,p in enumerate(six,1)]},
            'review':{'products':[{'ref':f'p{i}','topic_fit':True} for i in range(1,7)]},
            'issues':['independent review did not accept'] + [f'p{i}: identity has no source anchor' for i in range(1,7)]}
    path=tmp_path/('product-review-'+signature.replace('|','-')+'.json')
    path.write_text(json.dumps(review))
    saved={'topic.json':{'slug':slug,'category':'dailygoods','comparison_axes':AXES},
           'product-search-progress.json':{'candidates':six,'reviewed_sets':[signature],
           'researched_asins':[p['asin'] for p in six], 'queries':[], 'excluded_asins':[]},
           'evidence.json':{}}
    def read(_,name,default=None):return review if name==path.name else saved.get(name,default)
    with patch.object(business,'read_artifact',side_effect=read), \
         patch.object(business,'job_dir',return_value=tmp_path), \
         patch.object(business,'write_artifact',side_effect=lambda _,name,value:saved.__setitem__(name,value)), \
         patch.object(business,'load_feedback',return_value={}), \
         patch.object(business,'independent_review',return_value={'review':{'status':'ACCEPTED'}}), \
         patch.object(business,'product_issues',return_value=[]), \
         patch.object(business,'replace_product_evidence') as replace, \
         patch('product_candidate_registry.is_ng',return_value=False), \
         patch('safety_recall.unsafe_products',return_value=([],[])):
        business._reselect_products(slug,cache_only=False)
    replace.assert_called_once()
    assert saved['product-search-progress.json']['complete'] is True


def test_summary_only_semantic_failure_with_identity_quote_defect_can_be_rereviewed():
    # A generic summary emitted alongside invalid machine citations is not a
    # documented topical failure. The independent current QA must still pass.
    from growth_role_business import _obsolete_identity_review
    legacy={'issues':['independent review did not accept',
                      'p1: identity has no source anchor',
                      'independent semantic criteria not satisfied'],
            'review':{'products':[{'ref':'p1','topic_fit':True}]}}
    assert _obsolete_identity_review(legacy)
    actual={**legacy,'issues':legacy['issues']+['p1: product does not fit the topic']}
    assert not _obsolete_identity_review(actual)
    actual={**legacy,'review':{'products':[{'ref':'p1','topic_fit':False}]}}
    assert not _obsolete_identity_review(actual)
