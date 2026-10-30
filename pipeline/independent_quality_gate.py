"""Fail-closed Product/Editorial/Claim prepublication barrier."""
from __future__ import annotations
import json,subprocess,os,tempfile
from pathlib import Path
from editorial_qa import make_input as editorial_input,review_prompt as editorial_prompt,make_receipt as editorial_receipt,validate_verdict
from claim_qa import make_input as claim_input,review_prompt as claim_prompt,make_receipt as claim_receipt,validate_receipt
from comparison_acceptance import product_issues,require_acceptance

class QualityGateError(ValueError):
    def __init__(self, gate, issues):
        self.gate=gate;self.issues=issues if isinstance(issues,list) else [str(issues)]
        super().__init__(f'{gate}: '+ '; '.join(map(str,self.issues)))

def _judge(prompt):
    # Codex stdout can contain progress and multiple JSON objects; only its
    # documented final-message artifact is authoritative for a QA verdict.
    with tempfile.TemporaryDirectory(prefix='sellemy-qa-review-') as tmp:
        output=Path(tmp)/'last-message.txt'
        from api_role_binding import require_codex_settings
        model, effort = require_codex_settings('bu-codex-sellemy-reviewer', 'independent_review')
        command=[os.environ.get('SELLEMY_EDITORIAL_CLAIM_CODEX','/opt/homebrew/bin/codex'),'exec','--ephemeral','--skip-git-repo-check','--sandbox','read-only','--model',model,'--config',f'model_reasoning_effort={effort}','--output-last-message',str(output),'-']
        try:
            run=subprocess.run(command,input=prompt,text=True,capture_output=True,timeout=210)
        except (subprocess.TimeoutExpired,OSError) as exc:
            raise QualityGateError('reviewer_runtime',str(exc)) from exc
        if run.returncode:
            raise QualityGateError('reviewer_runtime',run.stderr[-500:] or 'review invocation failed')
        raw=output.read_text() if output.exists() else ''
        try:
            value=json.loads(raw.strip())
            if not isinstance(value,dict):raise ValueError('not an object')
            return value
        except (ValueError,json.JSONDecodeError) as exc:
            raise QualityGateError('reviewer_runtime','invalid final-message JSON reply') from exc

def issue_receipts(payload,evidence,acceptance,judge=None):
    """Never issue a passing certificate from an absent evidence set or model-only prose."""
    judge=judge or _judge
    try:
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
    claim_base_prompt=claim_prompt(cs)
    for attempt in range(2):
        try:
            cr=claim_receipt(judge(claim_base_prompt if attempt==0 else claim_base_prompt +
                '\nRETRY: The previous receipt contained a quotation absent from the same product entry or its source. '
                'For every finding copy article_quote VERBATIM from that finding product_ref description, '
                'and evidence_quote VERBATIM from the identical product_ref evidence. Never paraphrase.'),cs)
            break
        except QualityGateError:raise
        except Exception as exc:
            if attempt==1:raise QualityGateError('claim',str(exc)) from exc
    if cr['verdict']!='PASS':
        quotes=[x.get('article_quote') for x in cr.get('findings',[])
                if isinstance(x,dict) and x.get('verdict') in ('UNSUPPORTED','CONTRADICTED')
                and x.get('article_quote')]
        raise QualityGateError('claim',[cr.get('reason','FAIL'),
            '削除対象の具体文言: '+json.dumps(quotes,ensure_ascii=False),
            '該当文言を破棄し、根拠ある内容と一般的な選び方で所定文字数まで補って再編集'])
    return {'editorial':er,'claim':cr}

def require_receipts(payload,evidence,acceptance,receipts):
    try:
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
