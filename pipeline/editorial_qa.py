"""Evidence-free editorial gate input and fail-closed receipt validation."""
from __future__ import annotations
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONTRACT=(ROOT/'config/editorial-qa.md').read_text()

# Product-description length is editorial guidance, not a QA pass/fail rule.
# Keep its numeric recommendation out of the receipt contract fingerprint.
# The substitution is intentionally narrow: any other policy change must
# invalidate old receipts and demand current independent QA.
SOFT_LENGTH_HINT = (r'(Aim for 2–3 readable sentences \(roughly )'
                    r'\d+–\d+'
                    r'( Japanese characters\) when enough verified detail exists, '
                    r'but one sentence or another length is acceptable\. '
                    r'The guide is NOT an acceptance gate\.)')
def _contract_for_version(contract: str) -> str:
    normalized, count = re.subn(SOFT_LENGTH_HINT, r'\g<1><nonbinding-range>\2', contract)
    if count != 1:
        return contract  # Unrecognized editing changes the signature (fail closed).
    return normalized

VERSION=hashlib.sha256(_contract_for_version(CONTRACT).encode()).hexdigest()
# Historic receipts were signed over the full unnormalized file. These two
# hashes differ ONLY in the nonbinding 60–120 / 90–150 recommendation.
# This compatibility is valid exclusively for the exact current normative QA
# policy; a substantive change must invalidate all historic certificates.
CANON_NONBINDING_BASE='fee3ff738c8b86b7c2973581cc06a2a4df8748ad8a1c54511361ac45aabcfb0d'
COMPATIBLE_HINT_ONLY_VERSIONS={
    'daa37fd2ad3d110626e5d17f524391b09d633970c72a44694fc0fb2fa5e847f7',
    '4a6a4fb38e9f095b9b66736486413561b6f8f2f80ea1224db0c55fa132991d3f',
}
def _compatible_contract(version: object) -> bool:
    return (version == VERSION or
            (VERSION == CANON_NONBINDING_BASE and version in COMPATIBLE_HINT_ONLY_VERSIONS))
FIELDS=('h1','lead','summary','conclusion')
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
    if not _compatible_contract(value.get('contract_version')):return ['stale editorial contract']
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
            'Read all six product descriptions, every group angle, lead, summary, any optional general explanation and conclusion. Compare each part against the reader goal and editorial brief; quote actual lines for unsupported generic recommendations, repeated claims or changed article intent. No ranking or universal best product is required.\nARTICLE:\n' +
            json.dumps(subject['article'],ensure_ascii=False))

def make_receipt(verdict,subject):
    """Reject, never sanitize or silently drop, invented citations."""
    if not isinstance(verdict,dict):raise ValueError('invalid reviewer response')
    value={**verdict,'article_sha256':fingerprint(subject),'contract_version':VERSION}
    problems=validate_verdict(value,subject)
    if problems:raise ValueError('editorial review invalid: '+', '.join(problems))
    return value
