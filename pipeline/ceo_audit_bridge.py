"""Nonblocking post-publication audit submitted through existing canonical Task/Event.

Separate audit-only Task; no article authorization or production gate mutation.
"""
from __future__ import annotations
import hashlib,os,json
from pathlib import Path
from feedback_task_bridge import _run_action

class CEOAuditBridgeError(RuntimeError):
    pass

def submit_postpublish_audit(*, sample: dict, category: str, product_evidence_ref: str = '', article_file_path: str = '',
                             ai_os_root: Path | None = None, ai_os_python: str | None = None) -> dict:
    if sample.get('schema') != 'sellemy-ceo-sample/v1' or not sample.get('slug') or not sample.get('commit'):
        raise CEOAuditBridgeError('invalid sample identity')
    if category not in {'beauty','dailygoods','gadget'}:
        raise CEOAuditBridgeError('invalid article category')
    slug=sample['slug'];commit=sample['commit'];group_sha=sample['group_sha256']
    if not len(group_sha)==64 or not all(c in '0123456789abcdef' for c in group_sha):
        raise CEOAuditBridgeError('invalid group hash')
    fingerprint=hashlib.sha256(f'{slug}:{commit}:{group_sha}'.encode()).hexdigest()[:20].upper()
    task_id='TASK-BU002-POSTPUBLISH-CEO-AUDIT-'+fingerprint
    ai_os_root=Path(ai_os_root or os.environ.get('SELLEMY_AI_OS_ROOT','/Users/kickstart/ai-management-os'))
    ai_os_python=ai_os_python or os.environ.get('SELLEMY_AI_OS_PYTHON',str(ai_os_root/'.venv/bin/python'))
    canonical=Path(os.environ.get('SELLEMY_AI_OS_CANONICAL_ROOT',str(Path.home()/'Library/Application Support/AIManagementOS/canonical')))
    task_dir=canonical/'tasks'/task_id
    url=f'https://www.sellemy.jp/article/{category}/{slug}.html'
    evidence_path=Path(product_evidence_ref) if product_evidence_ref else None
    try:
        evidence=json.loads(evidence_path.read_text(encoding='utf-8')) if evidence_path else None
    except (OSError,ValueError) as exc:
        raise CEOAuditBridgeError('selected article evidence unreadable') from exc
    if not isinstance(evidence,dict) or len(evidence.get('products') or [])!=6:
        raise CEOAuditBridgeError('exact six-product evidence is required for CEO audit')
    product_summary=[]
    for i,product in enumerate(evidence['products'],1):
        asin=str(product.get('asin') or '').strip()
        if len(asin)!=10:
            raise CEOAuditBridgeError('product ASIN absent from review evidence')
        product_summary.append(f'{i}:ASIN={asin} title={str(product.get("amazon_title") or product.get("canonical_name") or "")[:100]} image={str(product.get("image_url") or "")[:220]}')
    evidence_sha=hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    article_path=Path(article_file_path) if article_file_path else None
    if not article_path or not article_path.is_file():
        raise CEOAuditBridgeError('published article HTML unavailable for exact audit')
    article_sha=hashlib.sha256(article_path.read_bytes()).hexdigest()
    review_points=['公開済み記事の全文・6商品・比較軸・画像・購入リンクの品質監査']+product_summary+[f'Evidence SHA256={evidence_sha}',f'HTML SHA256={article_sha}']
    def act(module,payload):
        return _run_action(python=ai_os_python,ai_os_root=ai_os_root,module=module,payload=payload)
    if not (task_dir/'task.json').is_file():
        act('ai_management_os.actions.task_create',{'task_id':task_id,
            'task_name':'Sellemy公開後CEO監査: '+slug,'project_id':'BU-002',
            'creator_role_id':'sellemy-ops','owner_role_id':'sellemy-ops',
            'executor_role_id':'sellemy-ops','message_id':'TASKCREATE-'+fingerprint,
            'parent_task_id':None,'ceo_direct':False,'receiving_role_id':'sellemy-ops'})
    def already_seen(message_id: str) -> bool:
        for event_path in (task_dir/'history').glob('*.json'):
            try:
                import json
                event=json.loads(event_path.read_text())
            except (ValueError,OSError):
                continue
            if event.get('message_id') == message_id:
                return True
        return False
    if already_seen('CEO-AUDIT-'+fingerprint):
        return {'required':True,'duplicate':True,'task_id':task_id,'article_url':url}
    # TaskFiles enforces the canonical lifecycle, not a new audit queue.
    if not already_seen('CEO-AUDIT-PREP-'+fingerprint):
        act('ai_management_os.actions.task_append',{'task_id':task_id,
            'message_id':'CEO-AUDIT-PREP-'+fingerprint,'actor_role_id':'sellemy-ops',
            'role_id':'sellemy-ops','status':'in_progress',
            'detail':{'summary':'公開済み記事の抜き取り監査準備', 'slug':slug,'commit':commit}})
    detail={'next_action':f'CEO review artifact_url={url} 公開後監査。公開・次記事制作は停止しない。 review_points={json.dumps(review_points,ensure_ascii=False,separators=(",",":"))}',
            'slug':slug,'commit':commit,'source_group_sha256':group_sha,
            'article_url':url,'product_evidence_ref':product_evidence_ref,'evidence_sha256':evidence_sha,'html_sha256':article_sha,'product_count':6,
            'nonblocking_postpublish_audit':True,'ceo_check_only':True,'from_role_id':'system',
            'required_actions':['公開済み記事の品質を事後確認'],
            'next_responsibility_role_id':'sellemy-ops'}
    result=act('ai_management_os.actions.task_append',{'task_id':task_id,
        'message_id':'CEO-AUDIT-'+fingerprint,'actor_role_id':'sellemy-ops',
        'role_id':'sellemy-ops','status':'review_required','detail':detail})
    if result.get('message_id')!='CEO-AUDIT-'+fingerprint:
        raise CEOAuditBridgeError('audit Task/Event readback mismatch')
    return {'required':True,'duplicate':False,'task_id':task_id,'article_url':url,'message_id':result['message_id']}
