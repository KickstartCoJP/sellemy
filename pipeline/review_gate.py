from __future__ import annotations
import re

# The independent review gate checks CONTENT INTEGRITY: that Writer prose stays
# grounded in evidence and never leaks internal pipeline/ops vocabulary into
# public-facing copy. It runs after the renderer and before machine QA, and is
# intentionally a separate module/check set from qa.py's mechanical/structural
# checks (lengths, phrase-ban list, HTML structure, identity/canonical/affiliate).

INTERNAL_JARGON_TERMS = (
    '実在を確認', 'ストアを表示', 'identity', 'verified', 'fail-closed', 'fail closed',
    'discovery', 'retrieval', 'pipeline', 'evidence', 'legacy_asset', 'human_confirmation',
    'writer_status', 'qa_status', 'GROW-', 'ASIN', 'observed_price', '観測価格',
)

PROSE_FIELDS = ('h1', 'lead', 'summary', 'how_to_choose', 'conclusion')

# Amazon listing titles routinely bundle marketing superlatives and bracketed spec
# strings; treating any 20+ char run shared verbatim with the raw title as a "pasted
# listing" is a conservative heuristic that catches copy/paste without penalizing
# short, unavoidable overlaps like a model number or brand name.
RAW_TITLE_OVERLAP_MIN_LEN = 20


class ReviewGateFinding:
    def __init__(self, field: str, ref: str | None, message: str):
        self.field = field
        self.ref = ref
        self.message = message

    def __repr__(self):
        loc = f'{self.field}[{self.ref}]' if self.ref else self.field
        return f'{loc}: {self.message}'


def _find_jargon(text: str):
    lowered = text.lower()
    return [term for term in INTERNAL_JARGON_TERMS if term.lower() in lowered]


def _is_marketing_prose_run(chunk: str) -> bool:
    # A pure-ASCII run (model number, technology/feature name like "EVOPOWER SYSTEM
    # FIT+") is a legitimate shared fact, not copy-pasted listing prose. Only flag
    # runs that contain at least one non-ASCII (Japanese) character -- that's the
    # signal of descriptive marketing text lifted verbatim from the raw title.
    return any(ord(ch) > 127 for ch in chunk)


def _shares_long_raw_substring(description: str, amazon_title: str, min_len: int) -> bool:
    if len(amazon_title) < min_len:
        return amazon_title in description and _is_marketing_prose_run(amazon_title)
    for start in range(0, len(amazon_title) - min_len + 1):
        chunk = amazon_title[start:start + min_len]
        if chunk in description and _is_marketing_prose_run(chunk):
            return True
    return False


def run_review_gate(payload: dict, evidence: dict) -> list[ReviewGateFinding]:
    findings: list[ReviewGateFinding] = []

    for field in PROSE_FIELDS:
        for term in _find_jargon(payload[field]):
            findings.append(ReviewGateFinding(field, None, f'internal jargon "{term}" leaked into public prose'))
    for group in payload['comparison_groups']:
        for field in ('title', 'angle'):
            for term in _find_jargon(group[field]):
                findings.append(ReviewGateFinding(f'comparison_groups.{group["id"]}.{field}', None, f'internal jargon "{term}" leaked into public prose'))

    evidence_by_ref = {p['ref']: p for p in evidence['products']}
    for product in payload['products']:
        ref = product['ref']
        ev = evidence_by_ref.get(ref)
        for field in ('h3', 'description'):
            for term in _find_jargon(product[field]):
                findings.append(ReviewGateFinding(field, ref, f'internal jargon "{term}" leaked into public prose'))
        if ev is None:
            findings.append(ReviewGateFinding('products', ref, 'payload product has no matching evidence entry'))
            continue
        if product['h3'].strip() == ev['amazon_title'].strip():
            findings.append(ReviewGateFinding('h3', ref, 'h3 is a verbatim copy of the raw Amazon listing title'))
        if _shares_long_raw_substring(product['description'], ev['amazon_title'], RAW_TITLE_OVERLAP_MIN_LEN):
            findings.append(ReviewGateFinding('description', ref, 'description pastes a long verbatim run from the raw Amazon listing title'))
        if re.search(r'(?:[¥￥]\s*[0-9][0-9,]*|[0-9][0-9,]*\s*円)', product['description']):
            findings.append(ReviewGateFinding('description', ref, 'description states a specific yen price, which is not carried in evidence'))

    return findings
