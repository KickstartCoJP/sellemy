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
    if len(sources)!=6 or any(not s for s in sources.values()):raise ValueError('six verified product sources required')
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
    all_article=json.dumps(subject['article'],ensure_ascii=False)
    for item in findings:
        if not isinstance(item,dict):problems.append('malformed finding');continue
        ref=item.get('product_ref');quote=item.get('article_quote');equote=item.get('evidence_quote')
        if ref not in subject['verified_sources']:problems.append('unknown product ref');continue
        if not isinstance(quote,str) or not quote or quote not in all_article:problems.append('unanchored article claim')
        if item.get('verdict') not in ('SUPPORTED','UNSUPPORTED','CONTRADICTED'):problems.append('invalid finding status')
        if item.get('verdict')=='SUPPORTED' and (not equote or equote not in subject['verified_sources'][ref]):problems.append('unanchored source proof')
    if receipt.get('verdict')=='PASS' and any(x.get('verdict')!='SUPPORTED' for x in findings if isinstance(x,dict)):
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
            'For each material product-specific factual claim give product_ref, exact article_quote, '
            'verdict SUPPORTED/UNSUPPORTED/CONTRADICTED, exact evidence_quote if supported, reason. '
            'Every quotation must be verbatim. If a quote is not in the provided evidence, mark UNSUPPORTED. '
            'Never infer a source.\nINPUT:\n'+json.dumps(subject,ensure_ascii=False))
