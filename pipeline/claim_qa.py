"""Claim-only QA subject and fail-closed evidence-linked receipt validation."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from editorial_qa import article_text,fingerprint
from comparison_acceptance import source_text
ROOT=Path(__file__).resolve().parents[1]
CONTRACT=(ROOT/'config/claim-qa.md').read_text()
VERSION=hashlib.sha256(CONTRACT.encode()).hexdigest()

def make_input(payload,evidence):
    article=article_text(payload)
    sources={p['ref']:source_text(p) for p in evidence.get('products',[])}
    if len(sources)!=6 or any(not s for s in sources.values()):raise ValueError('six identifiable product listings required')
    refs={p['ref'] for p in article['products']}
    if refs!=set(sources):raise ValueError('article and evidence refs differ')
    return {'contract_version':VERSION,'article':article,'verified_sources':sources,'comparison_axes':evidence.get('comparison_axes',[])}

def validate_receipt(receipt,subject):
    problems=[]
    if not isinstance(receipt,dict):return ['missing claim receipt']
    if receipt.get('contract_version')!=VERSION:problems.append('stale claim contract')
    if receipt.get('subject_sha256')!=fingerprint(subject):problems.append('stale claim input')
    if receipt.get('verdict') not in ('PASS','FAIL'):problems.append('invalid verdict')
    if not str(receipt.get('reason') or '').strip():problems.append('reason absent')
    findings=receipt.get('findings')
    if not isinstance(findings,list):return problems+['findings absent']
    if not findings:problems.append('empty claim findings')
    covered={x.get('product_ref') for x in findings if isinstance(x,dict)}
    expected={p['ref'] for p in subject.get('article',{}).get('products',[]) if isinstance(p,dict)}
    if receipt.get('verdict')=='PASS' and covered!=expected:problems.append('PASS without all six products assessed')
    article_by_ref={p['ref']:p for p in subject['article'].get('products',[]) if isinstance(p,dict) and p.get('ref')}
    shared_article=json.dumps({k:v for k,v in subject['article'].items() if k != 'products'},ensure_ascii=False)
    for item in findings:
        if not isinstance(item,dict):problems.append('malformed finding');continue
        ref=item.get('product_ref');quote=item.get('article_quote');equote=item.get('evidence_quote')
        if ref not in subject['verified_sources']:problems.append('unknown product ref');continue
        if not isinstance(quote,str) or not quote or (quote not in json.dumps(article_by_ref.get(ref,{}),ensure_ascii=False) and quote not in shared_article):problems.append('unanchored same-product article claim')
        if item.get('verdict') not in ('SUPPORTED','UNSUPPORTED','CONTRADICTED','NO_CLAIM'):problems.append('invalid finding status')
        if item.get('verdict')=='SUPPORTED' and (not equote or equote not in subject['verified_sources'][ref]):problems.append('unanchored source proof')
    if receipt.get('verdict')=='PASS' and any(x.get('verdict') not in ('SUPPORTED','NO_CLAIM') for x in findings if isinstance(x,dict)):
        problems.append('PASS with adverse finding')
    if receipt.get('verdict')=='FAIL' and not any(x.get('verdict') in ('UNSUPPORTED','CONTRADICTED') for x in findings if isinstance(x,dict)):
        problems.append('FAIL without concrete adverse finding')
    return problems

def make_receipt(verdict,subject):
    receipt={**verdict,'subject_sha256':fingerprint(subject),'contract_version':VERSION}
    issues=validate_receipt(receipt,subject)
    if issues:raise ValueError('; '.join(issues))
    return receipt

def review_prompt(subject):
    return (CONTRACT+'\nVerify only this one article against the corresponding product sources. '
            'Return one JSON object: verdict PASS/FAIL, reason, findings array. '
            'Evaluate decision-material factual claims (performance, compatibility, safety, durability, efficacy and precise specifications). Do not demand evidence merely for a subjective recommendation or ordinary use-case description with no testable factual assertion. For each material product-specific factual claim give product_ref, exact article_quote, '
            'verdict SUPPORTED/UNSUPPORTED/CONTRADICTED/NO_CLAIM, exact evidence_quote if supported, reason. '
            'Every quotation must be verbatim. Product listing titles can establish the identity, model, stated dimensions, item count and labeled specifications explicitly present in the title; they do NOT prove actual performance, efficacy, safety or durability. Distinguish an attributed listing specification from a verified real-world outcome. If a concrete claim lacks a matching same-product quote, mark UNSUPPORTED. If a product has no objective concrete claim, emit one NO_CLAIM finding with an exact quote from its description and empty evidence_quote. Do not invent a supporting claim to fill coverage. '
            'Never infer a source.\nINPUT:\n'+json.dumps(subject,ensure_ascii=False))


def reuse_snapshot(subject, receipt):
    """Only source-grounded per-product PASS findings from a valid review can be reused."""
    if not isinstance(receipt,dict) or receipt.get('verdict') not in ('PASS','FAIL'):
        return {}
    if validate_receipt(receipt,subject):
        return {}
    shared = {k:v for k,v in subject['article'].items() if k != 'products'}
    axes = subject.get('comparison_axes',[])
    results = {}
    for product in subject['article']['products']:
        ref = product['ref']
        findings = [x for x in receipt['findings'] if isinstance(x,dict) and x.get('product_ref') == ref]
        if not findings or any(x.get('verdict') not in ('SUPPORTED','NO_CLAIM') for x in findings):
            continue
        results[ref] = {'hash': fingerprint({'product':product,'source':subject['verified_sources'][ref],
                                             'shared':shared,'comparison_axes':axes}),
                        'findings':findings}
    return {'schema':'sellemy-claim-reuse/v1','items':results} if results else {}


def merge_reuse_snapshots(subject, *snapshots):
    """Merge only receipts still valid for the current product and source hashes."""
    items = {}
    for snapshot in snapshots:
        if not isinstance(snapshot,dict) or snapshot.get('schema') != 'sellemy-claim-reuse/v1':
            continue
        for ref in reusable_findings(subject,snapshot):
            items[ref] = snapshot['items'][ref]
    return {'schema':'sellemy-claim-reuse/v1','items':items} if items else {}


def reusable_findings(subject, snapshot):
    if not isinstance(snapshot,dict) or snapshot.get('schema') != 'sellemy-claim-reuse/v1':
        return {}
    shared = {k:v for k,v in subject['article'].items() if k != 'products'}
    axes = subject.get('comparison_axes',[])
    result = {}
    for product in subject['article']['products']:
        ref = product['ref']; row = (snapshot.get('items') or {}).get(ref) or {}
        expected = fingerprint({'product':product,'source':subject['verified_sources'][ref],
                                'shared':shared,'comparison_axes':axes})
        if row.get('hash') == expected and isinstance(row.get('findings'),list):
            result[ref] = row['findings']
    return result
