from __future__ import annotations

import re

from category_metadata import CATEGORIES

REQUIRED_ARTICLE_FIELDS = ('slug', 'category', 'h1', 'lead', 'summary', 'how_to_choose', 'comparison_groups', 'products', 'conclusion')
REQUIRED_GROUP_FIELDS = ('id', 'title', 'angle', 'product_refs')
REQUIRED_PRODUCT_FIELDS = ('ref', 'h3', 'description')
REQUIRED_EVIDENCE_FIELDS = ('slug', 'category', 'canonical_url', 'eyecatch_image', 'amazon_tag', 'comparison_axes', 'products')
REQUIRED_EVIDENCE_PRODUCT_FIELDS = ('ref', 'product_id', 'asin', 'image_url', 'amazon_title')
PRODUCT_COUNT = 6
ASIN_RE = re.compile(r'^[A-Z0-9]{10}$')


class PayloadValidationError(Exception):
    """Fail-closed validation error; renderers must never synthesize missing prose."""


def _require_nonempty_str(value, where):
    if not isinstance(value, str) or not value.strip():
        raise PayloadValidationError(f'{where} must be a non-empty string')


def _validate_category(value: str, where: str) -> None:
    if value not in CATEGORIES:
        raise PayloadValidationError(f'{where} must be one of {sorted(CATEGORIES)}')


def validate_payload(payload: dict) -> None:
    for field in REQUIRED_ARTICLE_FIELDS:
        if field not in payload:
            raise PayloadValidationError(f'payload missing required field: {field}')
    for field in ('slug', 'category', 'h1', 'lead', 'summary', 'how_to_choose', 'conclusion'):
        _require_nonempty_str(payload[field], f'payload.{field}')
    _validate_category(payload['category'], 'payload.category')

    products = payload['products']
    if not isinstance(products, list) or len(products) != PRODUCT_COUNT:
        raise PayloadValidationError(f'payload.products must contain exactly {PRODUCT_COUNT} entries')
    seen_refs = set()
    for i, product in enumerate(products):
        for field in REQUIRED_PRODUCT_FIELDS:
            if field not in product:
                raise PayloadValidationError(f'payload.products[{i}] missing required field: {field}')
            _require_nonempty_str(product[field], f'payload.products[{i}].{field}')
        if product['ref'] in seen_refs:
            raise PayloadValidationError(f'payload.products has duplicate ref: {product["ref"]}')
        seen_refs.add(product['ref'])

    groups = payload['comparison_groups']
    if not isinstance(groups, list) or not 1 <= len(groups) <= PRODUCT_COUNT:
        raise PayloadValidationError('payload.comparison_groups must contain 1-6 groups')
    group_ids, grouped_refs = set(), []
    for i, group in enumerate(groups):
        for field in REQUIRED_GROUP_FIELDS:
            if field not in group:
                raise PayloadValidationError(f'payload.comparison_groups[{i}] missing field: {field}')
        for field in ('id', 'title', 'angle'):
            _require_nonempty_str(group[field], f'payload.comparison_groups[{i}].{field}')
        if group['id'] in group_ids:
            raise PayloadValidationError(f'duplicate comparison group id: {group["id"]}')
        group_ids.add(group['id'])
        refs = group['product_refs']
        if not isinstance(refs, list) or not refs:
            raise PayloadValidationError(f'payload.comparison_groups[{i}].product_refs must be non-empty')
        grouped_refs.extend(refs)
    if len(grouped_refs) != len(set(grouped_refs)):
        raise PayloadValidationError('a product ref appears in more than one comparison group')
    if set(grouped_refs) != seen_refs:
        raise PayloadValidationError('comparison groups must partition all six product refs exactly once')


def validate_evidence(evidence: dict) -> None:
    for field in REQUIRED_EVIDENCE_FIELDS:
        if field not in evidence:
            raise PayloadValidationError(f'evidence missing required field: {field}')
    for field in ('slug', 'category', 'canonical_url', 'eyecatch_image', 'amazon_tag'):
        _require_nonempty_str(evidence[field], f'evidence.{field}')
    _validate_category(evidence['category'], 'evidence.category')
    axes = evidence['comparison_axes']
    if not isinstance(axes, list) or not axes:
        raise PayloadValidationError('evidence.comparison_axes must be a non-empty list')
    axis_ids = set()
    for i, axis in enumerate(axes):
        if not isinstance(axis, dict):
            raise PayloadValidationError(f'evidence.comparison_axes[{i}] must be an object')
        for field in ('id', 'label'):
            _require_nonempty_str(axis.get(field), f'evidence.comparison_axes[{i}].{field}')
        if axis['id'] in axis_ids:
            raise PayloadValidationError(f'duplicate comparison axis id: {axis["id"]}')
        axis_ids.add(axis['id'])

    products = evidence['products']
    if not isinstance(products, list) or len(products) != PRODUCT_COUNT:
        raise PayloadValidationError(f'evidence.products must contain exactly {PRODUCT_COUNT} entries')
    seen_asins, seen_refs = set(), set()
    for i, product in enumerate(products):
        for field in REQUIRED_EVIDENCE_PRODUCT_FIELDS:
            if field not in product:
                raise PayloadValidationError(f'evidence.products[{i}] missing required field: {field}')
            _require_nonempty_str(product[field], f'evidence.products[{i}].{field}')
        asin = product['asin'].upper()
        if not ASIN_RE.fullmatch(asin):
            raise PayloadValidationError(f'evidence.products[{i}].asin is invalid')
        if product['product_id'] != f'AMZ-{asin}':
            raise PayloadValidationError(f'evidence.products[{i}].product_id must use ASIN canonical identity')
        if asin in seen_asins:
            raise PayloadValidationError(f'evidence.products has duplicate asin: {asin}')
        seen_asins.add(asin)
        if product['ref'] in seen_refs:
            raise PayloadValidationError(f'evidence.products has duplicate ref: {product["ref"]}')
        seen_refs.add(product['ref'])


def match_refs(payload: dict, evidence: dict) -> None:
    payload_refs = {p['ref'] for p in payload['products']}
    evidence_refs = {p['ref'] for p in evidence['products']}
    if payload_refs != evidence_refs:
        raise PayloadValidationError(f'payload product refs {sorted(payload_refs)} do not match evidence refs {sorted(evidence_refs)}')
    if payload['slug'] != evidence['slug']:
        raise PayloadValidationError(f'payload.slug ({payload["slug"]}) does not match evidence.slug ({evidence["slug"]})')
    if payload['category'] != evidence['category']:
        raise PayloadValidationError('payload.category does not match evidence.category')
