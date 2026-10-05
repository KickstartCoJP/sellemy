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


def dedupe_content_products(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    kept, removed = [], []
    seen_titles: dict[str, str] = {}
    seen_images: dict[str, str] = {}
    for candidate in candidates:
        title_key, image_key = content_identity(candidate)
        duplicate_of = None
        reason = None
        if title_key and title_key in seen_titles:
            duplicate_of, reason = seen_titles[title_key], 'normalized_title_exact'
        elif image_key and image_key in seen_images:
            duplicate_of, reason = seen_images[image_key], 'canonical_image_exact'
        if duplicate_of:
            removed.append({'asin': candidate.get('asin'), 'duplicate_of_asin': duplicate_of, 'reason': reason})
            continue
        asin = str(candidate.get('asin') or '')
        if title_key:
            seen_titles[title_key] = asin
        if image_key:
            seen_images[image_key] = asin
        kept.append(candidate)
    return kept, removed


class ProductSelectionError(RuntimeError):
    pass


def _text(candidate: dict) -> str:
    return ' '.join(str(candidate.get(key, '')).lower() for key in ('amazon_title', 'brand'))


def _axis_matches(candidate: dict, axes: list[dict]) -> set[str]:
    text = _text(candidate)
    return {axis['id'] for axis in axes if any(str(word).lower() in text for word in axis.get('keywords', []))}


def select_six(candidates: list[dict], topic: dict, feedback: dict) -> tuple[list[dict], dict]:
    distinct_candidates, removed_duplicates = dedupe_content_products(candidates)
    if len(distinct_candidates) < 6:
        raise ProductSelectionError(
            f'product evidence incomplete after content dedupe: found {len(distinct_candidates)} distinct products, '
            f'require at least 6; removed={removed_duplicates}'
        )
    axes = topic['comparison_axes']
    remaining = list(distinct_candidates)
    selected, brands, covered = [], set(), set()
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
            score = relevance * .30 + identity * .25 + learning * .10 + brand_bonus + coverage_bonus + price_balance
            scored.append((score, candidate['asin'], candidate, matches))
        score, _asin, choice, matches = max(scored, key=lambda row: (row[0], row[1]))
        selected.append({**choice, 'selection_score': round(score, 6), 'matched_comparison_axes': sorted(matches)})
        remaining = [row for row in remaining if row['asin'] != choice['asin']]
        if choice.get('brand'):
            brands.add(choice['brand'].lower())
        covered.update(matches)
    if len(selected) != 6:
        raise ProductSelectionError('could not select six unique products')
    return selected, {'comparison_axes_covered': sorted(covered), 'brand_count': len(brands), 'price_used_as_constraint_only': True, 'content_duplicates_removed': removed_duplicates}
