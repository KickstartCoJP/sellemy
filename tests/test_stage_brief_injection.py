import sys,tempfile,json
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from owner_brief_learning import load_brief_rules,OwnerLearningError,project_stage_briefs

def test_empty_and_accepted_stage_scoping():
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);(root/'config').mkdir()
  entry={'stage':'writer','rule_id':'W1','actionable_rule':'Evidenceの製品型番と記事中の仕様値を照合し、一致しない製品は保留する','owner_verdict':'ACCEPTED','independent_qa_verdict':'PASS','evidence_refs':['QA-CASE-1']}
  (root/'config/growth-stage-briefs.json').write_text(json.dumps(project_stage_briefs([entry])))
  assert '型番' in load_brief_rules('writer',root=root)
  assert load_brief_rules('planning',root=root)==''
  broken=project_stage_briefs([entry]);broken['rules']['writer'][0]['owner_verdict']='PENDING'
  (root/'config/growth-stage-briefs.json').write_text(json.dumps(broken))
  with pytest.raises(OwnerLearningError):load_brief_rules('writer',root=root)
