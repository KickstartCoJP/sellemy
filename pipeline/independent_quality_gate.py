"""Fail-closed Product/Editorial/Claim prepublication barrier."""
from __future__ import annotations
import json,subprocess,os
from editorial_qa import make_input as editorial_input,review_prompt as editorial_prompt,make_receipt as editorial_receipt,validate_verdict
from claim_qa import make_input as claim_input,review_prompt as claim_prompt,make_receipt as claim_receipt,validate_receipt
from comparison_acceptance import product_issues,require_acceptance

class QualityGateError(ValueError):
    def __init__(self, gate, issues):
        self.gate=gate;self.issues=issues if isinstance(issues,list) else [str(issues)]
        super().__init__(f'{gate}: '+ '; '.join(map(str,self.issues)))

def _judge(prompt):
    command=[os.environ.get('SELLEMY_EDITORIAL_CLAIM_CODEX','/opt/homebrew/bin/codex'),'exec','--ephemeral','--skip-git-repo-check','--sandbox','read-only','--model',os.environ.get('SELLEMY_EDITORIAL_CLAIM_MODEL','gpt-6-sol'),'-']
    try:
        run=subprocess.run(command,input=prompt,text=True,capture_output=True,timeout=210)
    except (subprocess.TimeoutExpired,OSError) as exc:
        raise QualityGateError('reviewer_runtime',str(exc)) from exc
    if run.returncode:raise QualityGateError('reviewer_runtime',run.stderr[-500:] or 'review invocation failed')
    text=run.stdout
    try:return json.loads(text[text.index('{'):text.rindex('}')+1])
    except (ValueError,json.JSONDecodeError) as exc:raise QualityGateError('reviewer_runtime','invalid JSON reply') from exc

def issue_receipts(payload,evidence,acceptance,judge=None):
    """Never issue a passing certificate from an absent evidence set or model-only prose."""
    judge=judge or _judge
    try:
        if product_issues(evidence):raise QualityGateError('product',product_issues(evidence))
        require_acceptance(payload,evidence,acceptance)
    except QualityGateError:raise
    except Exception as exc:raise QualityGateError('product',str(exc)) from exc
    es=editorial_input(payload)
    try:er=editorial_receipt(judge(editorial_prompt(es)),es)
    except QualityGateError:raise
    except Exception as exc:raise QualityGateError('editorial',str(exc)) from exc
    if er['verdict']!='PASS':raise QualityGateError('editorial',er.get('reason','FAIL'))
    try:cs=claim_input(payload,evidence)
    except Exception as exc:raise QualityGateError('evidence',str(exc)) from exc
    try:cr=claim_receipt(judge(claim_prompt(cs)),cs)
    except QualityGateError:raise
    except Exception as exc:raise QualityGateError('claim',str(exc)) from exc
    if cr['verdict']!='PASS':raise QualityGateError('claim',cr.get('reason','FAIL'))
    return {'editorial':er,'claim':cr}

def require_receipts(payload,evidence,acceptance,receipts):
    try:
        if product_issues(evidence):raise QualityGateError('product',product_issues(evidence))
        require_acceptance(payload,evidence,acceptance)
    except QualityGateError:raise
    except Exception as exc:raise QualityGateError('product',str(exc)) from exc
    if not isinstance(receipts,dict):raise QualityGateError('receipts','missing independent quality receipts')
    if not receipts.get('editorial') or not receipts.get('claim'):
        raise QualityGateError('receipts','missing independent editorial or claim receipt')
    try:es=editorial_input(payload)
    except Exception as exc:raise QualityGateError('editorial',str(exc)) from exc
    er=receipts.get('editorial')
    problems=validate_verdict(er,es)
    if problems or er.get('verdict')!='PASS':raise QualityGateError('editorial',problems or er.get('reason','not passed'))
    try:cs=claim_input(payload,evidence)
    except Exception as exc:raise QualityGateError('evidence',str(exc)) from exc
    cr=receipts.get('claim');problems=validate_receipt(cr,cs)
    if problems or cr.get('verdict')!='PASS':raise QualityGateError('claim',problems or cr.get('reason','not passed'))
    return True
