"""Shared fail-closed comparison contract. No provider or side effects here."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parents[1] / 'config' / 'comparison-acceptance.md'
CONTRACT = CONTRACT_PATH.read_text(encoding='utf-8')
VALIDATOR_VERSION = 'comparison-acceptance-v1'
ALLOWED_INDEPENDENT_REVIEWERS = {'independent_responses_review', 'independent_chat_review'}
VERSION = hashlib.sha256((VALIDATOR_VERSION + '\n' + CONTRACT).encode()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def evidence_content(evidence):
    return {k: v for k, v in evidence.items() if k != 'product_acceptance'}


def blocked(reasons):
    return {'status': 'BLOCKED', 'needs_product_reselection': True, 'reasons': reasons}


def normalized(value):
    return re.sub(r'[^\wぁ-んァ-ヶ一-龥]', '', unicodedata.normalize('NFKC', value).lower())


def source_text(product):
    # Search listings are discovery leads, never QA evidence.
    parts = []
    for source in product.get('sources') or []:
        if not isinstance(source, dict) or source.get('verified') is not True:
            continue
        parts.append(str(source.get('title') or ''))
        parts.append(str(source.get('excerpt') or ''))
        for fact in source.get('facts') or []:
            if isinstance(fact, dict):
                parts.extend(str(fact.get(k) or '') for k in ('name', 'value', 'quote'))
    return ' '.join(x for x in parts if x).strip()


def _verified_series_identity(product):
    """Only structured, attributed source data can establish a series key."""
    for source in product.get('sources') or []:
        if not isinstance(source, dict) or source.get('verified') is not True:
            continue
        fields = source.get('identity') or {}
        if not isinstance(fields, dict):
            continue
        manufacturer = str(fields.get('manufacturer') or '').strip()
        series = str(fields.get('series') or '').strip()
        model = str(fields.get('model') or '').strip()
        text = source_text(product)
        if manufacturer and series and model and all(v in text for v in (manufacturer, series, model)):
            return (normalized(manufacturer), normalized(series))
    return None


def evidence_issues(evidence):
    from payload_schema import validate_evidence
    from product_selection import _axis_matches
    try:
        validate_evidence(evidence)
    except Exception as exc:
        return [f'invalid evidence: {exc}']
    axes = evidence['comparison_axes']
    issues, covered = [], set()
    seen_series = {}
    for product in evidence['products']:
        series_identity = _verified_series_identity(product)
        if series_identity is not None:
            if series_identity in seen_series:
                issues.append(f"{product['ref']}: same manufacturer series as {seen_series[series_identity]} (distinct ASIN is insufficient)")
            else:
                seen_series[series_identity] = product['ref']

        corpus = source_text(product).lower()
        matches = {
            axis['id'] for axis in axes
            if any(str(word).lower() in corpus for word in axis.get('keywords', []))
        }
        if not corpus.strip():
            issues.append(f"{product['ref']}: no attributable product facts")
        covered.update(matches)
        for source in product.get('sources') or []:
            if not isinstance(source, dict):
                issues.append(f"{product['ref']}: malformed source evidence")
                continue
            if source.get('verified') is True and not str(source.get('url') or '').startswith('https://'):
                issues.append(f"{product['ref']}: verified source lacks https URL")
    return issues


# Deliberately scoped to editorial/comparison hold declarations, not "保留" alone.
HOLD = re.compile(
    r'(?:完成原稿|本記事の公開|本稿の公開|記事の公開|公開判断|比較記事の公開)'
    r'[^。\n]{0,100}(?:保留|見送り|できません|できない)'
    r'|(?:本記事|本稿)(?:は|を)[^。\n]{0,80}(?:公開を(?:保留|見送り)|未完成|比較が成立しない)'
    r'|(?:六つ|6つ|六製品|6製品|六商品|6商品)[^。\n]{0,65}'
    r'(?:独立した製品を確認できない|比較できない|揃っていない)'
    r'|(?:comparison|article|publication)\s+(?:is\s+)?(?:on hold|not ready|cannot be completed)', re.I)


def public_texts(payload):
    for field in ('h1', 'lead', 'summary', 'how_to_choose', 'conclusion'):
        yield str(payload.get(field) or '')
    for group in payload.get('comparison_groups', []):
        yield str(group.get('title') or '')
        yield str(group.get('angle') or '')
    for product in payload.get('products', []):
        yield str(product.get('h3') or '')
        yield str(product.get('description') or '')


def article_issues(payload):
    issues = []
    if payload.get('status') in {'BLOCKED', 'REVISION_REQUIRED'} or payload.get('needs_product_reselection') is True:
        issues.append('article explicitly blocked')
    heading_hold = re.search(r'^比較は[^。\n]{0,100}(?:保留|見送り)', str(payload.get('h1') or ''))
    if heading_hold or any(HOLD.search(text) for text in public_texts(payload)):
        issues.append('completed comparison/publication hold leaked into public prose')
    return issues


DUPLICATE_ITEMS = (
    'same_product_body', 'color_design_only', 'sales_packaging_only',
    'size_capacity_difference', 'function_structure_difference',
    'series_generation_difference', 'manufacturer_brand_relation',
    'independent_comparison_value',
)


def machine_pair_screen(evidence):
    """Enumerate all pairs locally; only unresolved pairs require semantic review."""
    from product_selection import duplicate_check
    products = evidence.get('products', [])
    rows = []
    for index, left in enumerate(products):
        for right in products[index + 1:]:
            result = duplicate_check(left, right)
            rows.append({'left_ref': left['ref'], 'right_ref': right['ref'],
                         'status': result['status'], 'items': result['items'],
                         'reasons': result['reasons']})
    return rows


def duplicate_pair_issues(review, evidence):
    pairs = review.get('duplicate_pair_checks')
    if not isinstance(pairs, list):
        return ['unresolved duplicate pair checks missing']
    machine = machine_pair_screen(evidence)
    expected_rows = {tuple(sorted((p['left_ref'], p['right_ref']))): p for p in machine
                     if p['status'] == 'UNDETERMINED'}
    expected = set(expected_rows)
    sources = {p['ref']: source_text(p) for p in evidence.get('products', [])}
    issues, used = [], set()
    proven = any(p['status'] == 'DUPLICATE' for p in machine)
    for pair in pairs:
        if not isinstance(pair, dict):
            issues.append('invalid duplicate pair check'); continue
        a, b = pair.get('left_ref'), pair.get('right_ref')
        if a not in sources or b not in sources or a == b:
            issues.append('invalid duplicate pair refs'); continue
        key = tuple(sorted((a, b)))
        if key in used or key not in expected:
            issues.append('duplicate pair duplicated or not requested')
        used.add(key)
        items = pair.get('items')
        needed = {name for name, status in expected_rows.get(key, {}).get('items', {}).items() if status == 'UNKNOWN'}
        if not isinstance(items, list) or len(items) != len(needed) or {v.get('item') for v in items if isinstance(v,dict)} != needed:
            issues.append(f'{a}/{b}: incomplete duplicate check items'); continue
        if any(v.get('status') not in ('PASS', 'FAIL', 'UNKNOWN', 'N/A') for v in items):
            issues.append(f'{a}/{b}: invalid duplicate check status')
        if pair.get('is_duplicate') is True:
            grounded = any(v.get('status') == 'FAIL'
                and v.get('item') in ('same_product_body','color_design_only','sales_packaging_only')
                and v.get('left_quote') and v['left_quote'] in sources[a]
                and v.get('right_quote') and v['right_quote'] in sources[b]
                and str(v.get('reason') or '').strip() for v in items)
            if grounded: proven = True
            else: issues.append(f'{a}/{b}: duplicate conclusion lacks two grounded source anchors')
    if expected != used:
        issues.append(f'unresolved pair coverage incomplete: {len(expected - used)} missing')
    if review.get('distinct_products') is False and not proven:
        issues.append('distinct_products=false without proven duplicate pair')
    if review.get('distinct_products') is True and proven:
        issues.append('distinct_products=true contradicts confirmed duplicate pair')
    return issues


def review_issues(review, evidence, *, article=False):
    """Verify independent judgments and exact source anchors; never accept a bare pass."""
    if not isinstance(review, dict):
        return ['independent review missing']
    issues = []
    if review.get('status') != 'ACCEPTED' or review.get('needs_product_reselection') is not False:
        issues.append('independent review did not accept')
    checks = review.get('products')
    if not isinstance(checks, list) or len(checks) != 6:
        return issues + ['independent review must assess all six products']
    by_ref = {p['ref']: p for p in evidence.get('products', [])}
    refs, identities, covered = [], [], set()
    axes = {a['id'] for a in evidence.get('comparison_axes', [])}
    for check in checks:
        if not isinstance(check, dict):
            return issues + ['invalid product assessment']
        ref = check.get('ref')
        refs.append(ref)
        identity = normalized(str(check.get('identity_key') or ''))
        identities.append(identity)
        text = source_text(by_ref.get(ref, {}))
        quote = check.get('identity_quote')
        if (len(identity) < 3 or not isinstance(quote, str) or not quote.strip() or quote not in text
                or identity not in normalized(quote)
                or identity == normalized(str(by_ref.get(ref, {}).get('asin') or ''))):
            issues.append(f'{ref}: identity has no source anchor')
        if check.get('adequate_facts') is not True or check.get('topic_fit') is not True:
            issues.append(f'{ref}: insufficient facts or topic fit')
        supports = check.get('axis_support')
        if not isinstance(supports, list) or not supports:
            issues.append(f'{ref}: no grounded comparison axis')
        else:
            for support in supports:
                axis = support.get('axis_id')
                quote = support.get('quote')
                if axis not in axes or not isinstance(quote, str) or not quote.strip() or quote not in text:
                    issues.append(f'{ref}: invalid axis source anchor')
                else:
                    covered.add(axis)
        if not str(check.get('reason') or '').strip():
            issues.append(f'{ref}: missing review rationale')
    if len(set(refs)) != 6 or set(refs) != set(by_ref):
        issues.append('review refs do not partition evidence')
    if len(set(identities)) != 6:
        issues.append('same independent product identity across listings')
    if review.get('distinct_products') is False and not any(
        normalized(str(check.get('identity_key') or '')) == normalized(str(other.get('identity_key') or ''))
        for i, check in enumerate(checks) for other in checks[i+1:]
        if isinstance(check, dict) and isinstance(other, dict)
    ):
        issues.append('duplicate pair not substantiated by matching product identities')
    issues.extend(duplicate_pair_issues(review, evidence))
    if len(covered) < 2:
        issues.append('review lacks two grounded topic axes')
    required = ['distinct_products', 'meaningful_comparison', 'sufficient_evidence']
    if article:
        required += ['claims_grounded', 'buying_guidance', 'complete_comparison',
                     'recommendation_conclusion', 'reader_decision_outsourcing_absent',
                     'internal_qa_leak_absent', 'editorial_voice_appropriate']
    if any(review.get(key) is not True for key in required):
        issues.append('independent semantic criteria not satisfied')
    if not str(review.get('reason') or '').strip():
        issues.append('missing overall review rationale')
    return issues


def make_receipt(evidence, review, metadata, payload=None, *, reviewer='independent_responses_review'):
    if reviewer not in ALLOWED_INDEPENDENT_REVIEWERS:
        raise ValueError(f'unsupported independent reviewer: {reviewer}')
    return {'contract_version': VERSION, 'reviewer': reviewer,
            'evidence_sha256': digest(evidence_content(evidence)),
            'payload_sha256': digest(payload) if payload is not None else None,
            'review': review, 'metadata': metadata}


def receipt_issues(receipt, evidence, payload=None):
    if not isinstance(receipt, dict):
        return ['independent acceptance missing']
    if (receipt.get('contract_version') != VERSION
            or receipt.get('reviewer') not in ALLOWED_INDEPENDENT_REVIEWERS
            or receipt.get('evidence_sha256') != digest(evidence_content(evidence))
            or receipt.get('payload_sha256') != (digest(payload) if payload is not None else None)):
        return ['independent acceptance stale or invalid']
    try:
        return review_issues(receipt.get('review'), evidence, article=payload is not None)
    except (KeyError, TypeError, AttributeError):
        return ['malformed independent acceptance']


def product_issues(evidence):
    issues = evidence_issues(evidence)
    if issues:
        return issues
    return receipt_issues(evidence.get('product_acceptance'), evidence)


def acceptance_issues(payload, evidence, receipt):
    return product_issues(evidence) + article_issues(payload) + receipt_issues(receipt, evidence, payload)


def require_acceptance(payload, evidence, receipt):
    issues = acceptance_issues(payload, evidence, receipt)
    if issues:
        raise ValueError('comparison acceptance refused: ' + '; '.join(issues))


def load_receipt(root, slug):
    try:
        return json.loads((Path(root) / 'data' / 'acceptance' / f'{slug}.json').read_text())
    except (OSError, ValueError):
        return None
