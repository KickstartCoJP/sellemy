import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from owner_brief_learning import project_stage_briefs,OwnerLearningError

def test_only_owner_and_independent_qa_promoted():
    a={'stage':'writer','rule_id':'W001','actionable_rule':'記事中の製品仕様はEvidence内の型番と完全一致を照合する','owner_verdict':'ACCEPTED','independent_qa_verdict':'PASS','evidence_refs':['case-1/qa.json']}
    x=project_stage_briefs([a,a])
    assert len(x['rules']['writer'])==1 and len(x['rules']['planning'])==0
    with pytest.raises(OwnerLearningError):project_stage_briefs([{**a,'owner_verdict':'PENDING'}])
    with pytest.raises(OwnerLearningError):project_stage_briefs([{**a,'evidence_refs':[]}])
    with pytest.raises(OwnerLearningError):project_stage_briefs([{**a,'independent_qa_verdict':'UNVERIFIED'}])
