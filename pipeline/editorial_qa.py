"""Evidence-free editorial gate input and fail-closed receipt validation."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONTRACT=(ROOT/'config/editorial-qa.md').read_text()
VERSION=hashlib.sha256(CONTRACT.encode()).hexdigest()
FIELDS=('h1','lead','summary','how_to_choose','conclusion')
def article_text(payload):
    if not isinstance(payload,dict): raise ValueError('article must be an object')
    if any(not isinstance(payload.get(k),str) or not payload[k].strip() for k in FIELDS):raise ValueError('article prose incomplete')
    groups=payload.get('comparison_groups')
    products=payload.get('products')
    if not isinstance(groups,list) or len(groups)!=3 or not isinstance(products,list) or len(products)!=6:raise ValueError('article groups/products incomplete')
    return {'h1':payload['h1'],'lead':payload['lead'],'summary':payload['summary'],
            'comparison_groups':[{'title':g['title'],'angle':g['angle']} for g in groups],
            'products':[{'ref':p['ref'],'h3':p['h3'],'description':p['description']} for p in products],
            'how_to_choose':payload['how_to_choose'],'conclusion':payload['conclusion']}
def make_input(payload,brief=None):
    return {'contract_version':VERSION,'article':article_text(payload),'editorial_brief':brief or {}}
def fingerprint(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def validate_verdict(value,subject):
    if not isinstance(value,dict) or value.get('verdict') not in ('PASS','FAIL'):return ['invalid verdict']
    if value.get('article_sha256')!=fingerprint(subject):return ['stale article hash']
    if value.get('contract_version')!=VERSION:return ['stale editorial contract']
    if not isinstance(value.get('reason'),str) or not value['reason'].strip():return ['missing reason']
    if value['verdict']=='FAIL' and not value.get('quotes'):return ['FAIL requires exact quotes']
    if any(q not in json.dumps(subject['article'],ensure_ascii=False) for q in value.get('quotes',[])):return ['quote not grounded in article']
    return []

def review_prompt(subject):
    """One article per request; never permit cross-article quotation bleed."""
    if not isinstance(subject,dict) or not isinstance(subject.get('article'),dict):
        raise ValueError('invalid editorial subject')
    return (CONTRACT + '\nEvaluate ONLY this one article. Return JSON with verdict, reason, quotes. '
            'Every quote must be a literal substring from this same article; do not paraphrase quotes. '
            'PASS may return an empty quotes array. FAIL must include exact failing excerpts. '
            'No ranking or universal best product is required.\nARTICLE:\n' +
            json.dumps(subject['article'],ensure_ascii=False))

def make_receipt(verdict,subject):
    """Reject, never sanitize or silently drop, invented citations."""
    if not isinstance(verdict,dict):raise ValueError('invalid reviewer response')
    value={**verdict,'article_sha256':fingerprint(subject),'contract_version':VERSION}
    problems=validate_verdict(value,subject)
    if problems:raise ValueError('editorial review invalid: '+', '.join(problems))
    return value
