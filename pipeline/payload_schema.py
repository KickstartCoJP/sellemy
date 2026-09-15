from __future__ import annotations

REQUIRED_ARTICLE_FIELDS = ('slug', 'category', 'h1', 'lead', 'summary', 'how_to_choose', 'comparison_angles', 'products', 'conclusion')
REQUIRED_ANGLE_TIERS = ('lowrange', 'midrange', 'highrange')
REQUIRED_PRODUCT_FIELDS = ('ref', 'h3', 'description')
REQUIRED_EVIDENCE_FIELDS = ('slug', 'category', 'canonical_url', 'eyecatch_image', 'amazon_tag', 'products')
REQUIRED_EVIDENCE_PRODUCT_FIELDS = ('ref', 'tier', 'product_id', 'asin', 'image_url', 'amazon_title')
PRODUCT_COUNT = 6


class PayloadValidationError(Exception):
    """Raised when a Writer payload (or its evidence pairing) is missing required prose or facts.

    The renderer must fail closed on this exception rather than substitute, pad, or
    synthesize replacement text for the missing field.
    """


def _require_nonempty_str(value, where):
    if not isinstance(value, str) or not value.strip():
        raise PayloadValidationError(f'{where} must be a non-empty string')


def validate_payload(payload: dict) -> None:
    for field in REQUIRED_ARTICLE_FIELDS:
        if field not in payload:
            raise PayloadValidationError(f'payload missing required field: {field}')
    for field in ('slug', 'category', 'h1', 'lead', 'summary', 'how_to_choose', 'conclusion'):
        _require_nonempty_str(payload[field], f'payload.{field}')

    angles = payload['comparison_angles']
    if not isinstance(angles, dict):
        raise PayloadValidationError('payload.comparison_angles must be an object')
    for tier in REQUIRED_ANGLE_TIERS:
        if tier not in angles:
            raise PayloadValidationError(f'payload.comparison_angles missing tier: {tier}')
        _require_nonempty_str(angles[tier], f'payload.comparison_angles.{tier}')

    products = payload['products']
    if not isinstance(products, list) or len(products) != PRODUCT_COUNT:
        raise PayloadValidationError(f'payload.products must contain exactly {PRODUCT_COUNT} entries')
    seen_refs = set()
    for i, product in enumerate(products):
        for field in REQUIRED_PRODUCT_FIELDS:
            if field not in product:
                raise PayloadValidationError(f'payload.products[{i}] missing required field: {field}')
        _require_nonempty_str(product['ref'], f'payload.products[{i}].ref')
        _require_nonempty_str(product['h3'], f'payload.products[{i}].h3')
        _require_nonempty_str(product['description'], f'payload.products[{i}].description')
        if product['ref'] in seen_refs:
            raise PayloadValidationError(f'payload.products has duplicate ref: {product["ref"]}')
        seen_refs.add(product['ref'])


def validate_evidence(evidence: dict) -> None:
    for field in REQUIRED_EVIDENCE_FIELDS:
        if field not in evidence:
            raise PayloadValidationError(f'evidence missing required field: {field}')
    for field in ('slug', 'category', 'canonical_url', 'eyecatch_image', 'amazon_tag'):
        _require_nonempty_str(evidence[field], f'evidence.{field}')

    products = evidence['products']
    if not isinstance(products, list) or len(products) != PRODUCT_COUNT:
        raise PayloadValidationError(f'evidence.products must contain exactly {PRODUCT_COUNT} entries')
    seen_asins = set()
    seen_refs = set()
    for i, product in enumerate(products):
        for field in REQUIRED_EVIDENCE_PRODUCT_FIELDS:
            if field not in product:
                raise PayloadValidationError(f'evidence.products[{i}] missing required field: {field}')
        for field in ('ref', 'tier', 'product_id', 'asin', 'image_url', 'amazon_title'):
            _require_nonempty_str(product[field], f'evidence.products[{i}].{field}')
        if product['asin'] in seen_asins:
            raise PayloadValidationError(f'evidence.products has duplicate asin: {product["asin"]}')
        seen_asins.add(product['asin'])
        if product['ref'] in seen_refs:
            raise PayloadValidationError(f'evidence.products has duplicate ref: {product["ref"]}')
        seen_refs.add(product['ref'])
    if len(seen_asins) != PRODUCT_COUNT:
        raise PayloadValidationError(f'evidence must identify exactly {PRODUCT_COUNT} unique ASINs')


def match_refs(payload: dict, evidence: dict) -> None:
    payload_refs = {p['ref'] for p in payload['products']}
    evidence_refs = {p['ref'] for p in evidence['products']}
    if payload_refs != evidence_refs:
        raise PayloadValidationError(
            f'payload product refs {sorted(payload_refs)} do not match evidence refs {sorted(evidence_refs)}'
        )
    if payload['slug'] != evidence['slug']:
        raise PayloadValidationError(f'payload.slug ({payload["slug"]}) does not match evidence.slug ({evidence["slug"]})')
