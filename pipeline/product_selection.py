from __future__ import annotations

from analytics_feedback import product_signal
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit


def _normalize_identity_text(value: str) -> str:
    value = unicodedata.normalize('NFKC', str(value or '')).lower()
    return re.sub(r'[^0-9a-zぁ-んァ-ヶ一-龥]+', '', value)


def _canonical_image_url(value: str) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    parts = urlsplit(raw)
    path = re.sub(r'\._[^/]+_(?=\.[A-Za-z0-9]+$)', '', parts.path)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, '', ''))


def content_identity(candidate: dict) -> tuple[str, str]:
    return (_normalize_identity_text(candidate.get('amazon_title') or ''), _canonical_image_url(candidate.get('image_url') or ''))


_COLOR_VARIANT = re.compile(r"(?:\s*[（(]\s*(?:アクア|ブルー|クリア|ホワイト|ブラック|グレー|ベージュ|ピンク|レッド|グリーン|イエロー|パープル|オレンジ|ネイビー|シルバー|ゴールド|透明|白|黒|青|赤|緑|灰色)\s*[）)])\s*$")


DUPLICATE_CHECK_ITEMS = (
    'same_product_body', 'color_design_only', 'sales_packaging_only',
    'size_capacity_difference', 'function_structure_difference',
    'series_generation_difference', 'manufacturer_brand_relation',
    'independent_comparison_value',
)


def _color_variant_identity(candidate: dict) -> str:
    title = str(candidate.get('amazon_title') or '').strip()
    stem = _COLOR_VARIANT.sub('', title)
    if stem == title:
        return ''
    brand = _normalize_identity_text(candidate.get('brand') or '')
    key = _normalize_identity_text(stem)
    return f'{brand}:{key}' if brand and len(key) >= 12 else ''


def duplicate_check(a: dict, b: dict) -> dict:
    """Itemized, evidence-conservative pair screen. UNKNOWN never proves duplication."""
    fields = {key: 'UNKNOWN' for key in DUPLICATE_CHECK_ITEMS}
    reasons = []
    left, right = content_identity(a), content_identity(b)
    left_asin, right_asin = str(a.get('asin') or ''), str(b.get('asin') or '')
    color_a, color_b = _color_variant_identity(a), _color_variant_identity(b)
    duplicate = False
    if left_asin and left_asin == right_asin:
        fields['same_product_body'] = 'FAIL'
        reasons.append('same_asin')
        duplicate = True
    elif left[0] and left[0] == right[0]:
        fields['same_product_body'] = 'FAIL'
        reasons.append('normalized_title_exact')
        duplicate = True
    elif left[1] and left[1] == right[1]:
        fields['same_product_body'] = 'FAIL'
        reasons.append('canonical_image_exact')
        duplicate = True
    elif color_a and color_a == color_b:
        fields['color_design_only'] = 'FAIL'
        reasons.append('same_named_model_color_variant')
        duplicate = True
    return {'status': 'DUPLICATE' if duplicate else 'UNDETERMINED',
            'items': fields, 'reasons': reasons, 'pair': [left_asin, right_asin]}


def dedupe_content_products(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    kept, removed = [], []
    for candidate in candidates:
        match = next(((previous, duplicate_check(previous, candidate)) for previous in kept
                      if duplicate_check(previous, candidate)['status'] == 'DUPLICATE'), None)
        if match:
            previous, check = match
            removed.append({'asin': candidate.get('asin'), 'duplicate_of_asin': previous.get('asin'),
                            'reason': check['reasons'][0], 'check_items': check['items']})
        else:
            kept.append(candidate)
    return kept, removed


class ProductSelectionError(RuntimeError):
    pass


def _text(candidate: dict) -> str:
    parts = []
    for source in candidate.get('sources') or []:
        if isinstance(source, dict) and source.get('verified') is True:
            parts.append(str(source.get('excerpt') or '').lower())
    return ' '.join(parts)


def _axis_matches(candidate: dict, axes: list[dict]) -> set[str]:
    text = _text(candidate)
    return {axis['id'] for axis in axes if any(str(word).lower() in text for word in axis.get('keywords', []))}


def select_six(candidates: list[dict], topic: dict, feedback: dict, *, pinned: list[dict] | None = None) -> tuple[list[dict], dict]:
    """Select six distinct products, retaining independently accepted members.

    Topic-axis coverage is evaluated over the complete six, not just the
    replacement subset.  Replacements still need their own grounded axis.
    """
    pinned = list(pinned or [])
    distinct_candidates, removed_duplicates = dedupe_content_products([*pinned, *candidates])
    if len(distinct_candidates) < 6:
        raise ProductSelectionError(
            f'product evidence incomplete after content dedupe: found {len(distinct_candidates)} distinct products, '
            f'require at least 6; removed={removed_duplicates}'
        )
    axes = topic['comparison_axes']
    # Planning's topic axes must be supported by verified product evidence.
    # A listing-only or axis-empty candidate must never consume a six-product
    # slot and then fail its first independent QA check for a known defect.
    from comparison_acceptance import source_text
    def has_grounded_axis(product):
        corpus = source_text(product).lower()
        return bool(corpus and any(any(str(word).lower() in corpus for word in axis.get('keywords', []))
                                   for axis in axes))
    pin_asins = {p['asin'] for p in pinned}
    selected = [p for p in distinct_candidates if p['asin'] in pin_asins]
    if len(selected) != len(pin_asins) or any(not has_grounded_axis(p) for p in selected):
        raise ProductSelectionError('retained product lacks verified topic-axis evidence')
    remaining = [p for p in distinct_candidates if p['asin'] not in pin_asins
                 and _text(p).strip() and has_grounded_axis(p)]
    if len(selected) + len(remaining) < 6:
        raise ProductSelectionError('needs_product_reselection: fewer than six products with verified topic-axis evidence')
    supported_axes = set().union(*(_axis_matches(p, axes) for p in [*selected,*remaining]))
    if len(supported_axes) < 2:
        raise ProductSelectionError('needs_product_reselection: fewer than two grounded comparison axes')
    brands = {str(p['brand']).lower() for p in selected if p.get('brand')}
    covered = set().union(*(_axis_matches(p, axes) for p in selected)) if selected else set()
    prices = sorted(p['observed_price'] for p in distinct_candidates if p.get('observed_price') is not None)
    median = prices[len(prices) // 2] if prices else None
    while remaining and len(selected) < 6:
        scored = []
        for candidate in remaining:
            matches = _axis_matches(candidate, axes)
            relevance = min(1.0, float(candidate.get('discovery_score') or 0) / max(1, len(str(topic.get('query', '')).split())))
            identity = sum(bool(candidate.get(k)) for k in ('asin', 'amazon_title', 'image_url')) / 3
            learning = product_signal(feedback, candidate['asin'])
            brand_bonus = .12 if candidate.get('brand') and candidate['brand'].lower() not in brands else 0
            coverage_bonus = .18 * len(matches - covered)
            price_balance = .05 if median and candidate.get('observed_price') and ((candidate['observed_price'] <= median) != any(p.get('observed_price', median) <= median for p in selected)) else 0
            # Prefer comparable products with independent official documentation;
            # source presence is a ranking signal, not a substitute for QA acceptance.
            sources = [source for source in candidate.get('sources') or []
                       if isinstance(source, dict) and source.get('verified') is True]
            source_bonus = .22 if any(s.get('source_type') == 'manufacturer_official'
                                          for s in sources) else (.08 if sources else 0)
            score = relevance * .30 + identity * .25 + learning * .10 + brand_bonus + coverage_bonus + price_balance + source_bonus
            scored.append((score, candidate['asin'], candidate, matches))
        score, _asin, choice, matches = max(scored, key=lambda row: (row[0], row[1]))
        selected.append({**choice, 'selection_score': round(score, 6), 'matched_comparison_axes': sorted(matches)})
        remaining = [row for row in remaining if row['asin'] != choice['asin']]
        if choice.get('brand'):
            brands.add(choice['brand'].lower())
        covered.update(matches)
    if len(selected) != 6:
        raise ProductSelectionError('could not select six unique products')
    return selected, {'comparison_axes_covered': sorted(covered), 'brand_count': len(brands), 'price_used_as_constraint_only': True, 'content_duplicates_removed': removed_duplicates,
                      'retained_qa_passed_asins': sorted(pin_asins)}
