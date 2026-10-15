import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from postpublish_ceo_sampling import select_reviews

def test_every_ten_only_completed_groups():
    values=[{'slug':f'article-{i}','commit':f'sha-{i}'} for i in range(21)]
    a=select_reviews(values)
    assert len(a)==2
    assert a==select_reviews(values)
    assert all(z['review_state']=='PENDING_POSTPUBLISH_NONBLOCKING' for z in a)
    assert all(z['slug'] in [x['slug'] for x in values[z['group_start']:z['group_start']+10]] for z in a)

def test_partial_group_is_not_selected():
    assert select_reviews([{'slug':'a','commit':'sha'}])==[]

def test_rejects_duplicates_and_missing_identity():
    with pytest.raises(ValueError):select_reviews([{'slug':'a','commit':'sha'}]*10)
    with pytest.raises(ValueError):select_reviews([{'slug':'a'}])
