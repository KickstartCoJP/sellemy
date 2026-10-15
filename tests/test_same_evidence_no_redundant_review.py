import sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_runtime as g
from fixtures import valid_evidence

def test_identical_rejected_evidence_does_not_charge_second_independent_review():
    e=valid_evidence()
    prior={**e,'product_acceptance':{'review':{'status':'BLOCKED'}}}
    with patch.object(g,'read_artifact',side_effect=lambda slug,name,default=None: {'topic.json':{'slug':'x','category':'gadget'},'evidence.json':prior}.get(name,default)),\
         patch.object(g,'discover_products',return_value=e['products']),\
         patch.object(g,'select_six',return_value=(e['products'],{})),\
         patch.object(g,'build_evidence',return_value=e),\
         patch.object(g,'product_issues',return_value=['insufficient facts']),\
         patch.object(g,'request_product_reselection') as reselect,\
         patch.object(g,'independent_review') as expensive_review:
        with pytest.raises(g.GrowthRuntimeError,match='exact evidence unchanged'):
            g._reselect_products('x',cache_only=False)
        reselect.assert_called_once()
        expensive_review.assert_not_called()
