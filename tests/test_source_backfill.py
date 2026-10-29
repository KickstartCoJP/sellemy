import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from growth_role_business import _backfill_missing_amazon_sources

def test_unsourced_old_candidates_get_verified_sources_without_research_reset():
    pool=[{'asin':'B012345678','amazon_title':'Brand Model Shampoo', 'brand':'Brand', 'sources':[]},
          {'asin':'B123456789','amazon_title':'Verified shampoo','brand':'Brand', 'sources':[{'verified':True,'excerpt':'facts'}]}]
    source={'verified':True,'url':'https://www.amazon.co.jp/dp/B012345678','excerpt':'Real product detail','title':'Brand Model Shampoo'}
    with patch('growth_role_business.fetch_amazon_item_source',return_value=source) as fetch:
        result,attempted=_backfill_missing_amazon_sources(pool,set())
    assert fetch.call_count==1
    assert result[0]['sources']==[source]
    assert result[1]==pool[1]
    assert attempted=={'B012345678'}

def test_rejected_item_source_attempts_are_bounded():
    pool=[{'asin':'B012345678','amazon_title':'Unknown','sources':[]}]
    with patch('growth_role_business.fetch_amazon_item_source',return_value=None) as fetch:
        first,attempted=_backfill_missing_amazon_sources(pool,set())
        second,attempted=_backfill_missing_amazon_sources(first,attempted)
    assert fetch.call_count==1 and attempted=={'B012345678'} and second==pool
