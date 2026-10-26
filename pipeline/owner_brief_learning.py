"""Owner-approved, stage-scoped lesson promotion. Never ingest raw case logs into Brief."""
from __future__ import annotations
import json,re
from pathlib import Path

ALLOWED_STAGES={'planning','product_evidence','writer','qa','designer','publish'}

class OwnerLearningError(ValueError):pass

def verify_owner_lesson(lesson:dict)->dict:
    if lesson.get('stage') not in ALLOWED_STAGES:raise OwnerLearningError('invalid stage')
    if lesson.get('owner_verdict')!='ACCEPTED':raise OwnerLearningError('not owner-approved')
    rule=str(lesson.get('actionable_rule') or '').strip()
    if not 12<=len(rule)<=450:raise OwnerLearningError('lesson must be compact and actionable')
    evidence=lesson.get('evidence_refs') or []
    if not isinstance(evidence,list) or not evidence or any(not str(x).strip() for x in evidence):
        raise OwnerLearningError('independent evidence refs required')
    if lesson.get('independent_qa_verdict') not in {'PASS','VERIFIED_DEFECT'}:
        raise OwnerLearningError('independent QA confirmation required')
    return {'stage':lesson['stage'],'rule_id':lesson['rule_id'],'rule':rule,
            'evidence_refs':evidence[:5],'owner_verdict':'ACCEPTED',
            'independent_qa_verdict':lesson['independent_qa_verdict']}

def project_stage_briefs(accepted:list[dict],*,max_rules:int=8)->dict:
    if max_rules<1 or max_rules>20:raise OwnerLearningError('invalid bound')
    result={s:[] for s in sorted(ALLOWED_STAGES)}
    seen=set()
    for entry in accepted:
        item=verify_owner_lesson(entry)
        key=(item['stage'],item['rule_id'])
        if key in seen:continue
        seen.add(key)
        if len(result[item['stage']])<max_rules:result[item['stage']].append(item)
    return {'schema':'sellemy-stage-brief-projection/v1','max_rules_per_stage':max_rules,'rules':result}


def load_brief_rules(stage: str, *, root: Path | None = None) -> str:
    if stage not in ALLOWED_STAGES:raise OwnerLearningError('invalid stage')
    root=Path(root or Path(__file__).resolve().parents[1])
    path=root/'config/growth-stage-briefs.json'
    try:doc=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError) as ex:raise OwnerLearningError('stage brief unavailable') from ex
    if doc.get('schema')!='sellemy-stage-brief-projection/v1':raise OwnerLearningError('stage brief schema invalid')
    max_rules=doc.get('max_rules_per_stage')
    if not isinstance(max_rules,int) or not 1<=max_rules<=20:raise OwnerLearningError('stage brief limit invalid')
    rows=doc.get('rules') or {}
    if not isinstance(rows,dict):raise OwnerLearningError('brief rules malformed')
    selected=rows.get(stage,[])
    if not isinstance(selected,list) or len(selected)>max_rules:raise OwnerLearningError('brief overflow')
    baseline=(doc.get('baseline') or {}).get(stage, [])
    if not isinstance(baseline,list) or len(baseline)>4 or any(not isinstance(x,str) or not x.strip() or len(x)>250 for x in baseline):
        raise OwnerLearningError('baseline brief malformed')
    rules=list(baseline)
    for record in selected:
        item=verify_owner_lesson({'stage':record.get('stage'),'rule_id':record.get('rule_id'),
           'actionable_rule':record.get('rule'),'owner_verdict':record.get('owner_verdict'),
           'independent_qa_verdict':record.get('independent_qa_verdict'),'evidence_refs':record.get('evidence_refs')})
        if item['stage']!=stage:raise OwnerLearningError('cross-stage brief contamination')
        rules.append(item['rule'])
    return '\n'.join(f'- {r}' for r in rules)
