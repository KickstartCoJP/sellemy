from __future__ import annotations

from analytics_feedback import product_signal


class ProductSelectionError(RuntimeError):
    pass


def _text(candidate: dict) -> str:
    return ' '.join(str(candidate.get(key, '')).lower() for key in ('amazon_title', 'brand'))


def _axis_matches(candidate: dict, axes: list[dict]) -> set[str]:
    text = _text(candidate)
    return {axis['id'] for axis in axes if any(str(word).lower() in text for word in axis.get('keywords', []))}


def select_six(candidates: list[dict], topic: dict, feedback: dict) -> tuple[list[dict], dict]:
    if len(candidates) < 6:
        raise ProductSelectionError(f'product evidence incomplete: found {len(candidates)}, require at least 6')
    axes = topic['comparison_axes']
    remaining = list(candidates)
    selected, brands, covered = [], set(), set()
    prices = sorted(p['observed_price'] for p in candidates if p.get('observed_price') is not None)
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
    return selected, {'comparison_axes_covered': sorted(covered), 'brand_count': len(brands), 'price_used_as_constraint_only': True}
