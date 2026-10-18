import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import semantic_review

def test_independent_review_reasks_malformed_citations_once():
    products=[{'ref':f'p{i}','adequate_facts':True,'topic_fit':True} for i in range(1,7)]
    raw={'status':'BLOCKED','products':products}
    good={'status':'ACCEPTED','products':products}
    with patch.object(semantic_review,'evidence_issues',return_value=[]),\
         patch.object(semantic_review,'generate',side_effect=[(raw,{}),(good,{})]) as gen,\
         patch.object(semantic_review,'review_issues',side_effect=[['p5: identity has no source anchor'],[],[]]),\
         patch.object(semantic_review,'make_receipt',side_effect=lambda e,r,m,p:r):
        result=semantic_review.independent_review({'products':[]})
    assert result['status']=='ACCEPTED'
    assert gen.call_count==2
    assert 'REVIEW CORRECTION REQUIRED' in gen.call_args.args[2]
