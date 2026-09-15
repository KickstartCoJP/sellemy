from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEEDBACK = ROOT / 'data' / 'analytics_feedback.json'


def load_feedback(path: Path = DEFAULT_FEEDBACK) -> dict:
    """Read a GA4/affiliate export adapter input without creating a second analytics store."""
    if not path.exists():
        return {'source': 'GA4', 'topic_metrics': [], 'product_metrics': []}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('analytics feedback must be an object')
    for key in ('topic_metrics', 'product_metrics'):
        if not isinstance(value.get(key, []), list):
            raise ValueError(f'analytics feedback {key} must be a list')
    return value


def topic_signal(feedback: dict, *, category: str, intent_key: str) -> float:
    scores = []
    for row in feedback.get('topic_metrics', []):
        if row.get('category') == category and row.get('intent_key') == intent_key:
            pv = max(0.0, float(row.get('page_views') or 0))
            ctr = max(0.0, float(row.get('affiliate_ctr') or 0))
            revenue = max(0.0, float(row.get('revenue') or 0))
            scores.append(min(1.0, pv / 10000) * .35 + min(1.0, ctr / .15) * .35 + min(1.0, revenue / 100000) * .30)
    return max(scores, default=0.0)


def product_signal(feedback: dict, asin: str) -> float:
    for row in feedback.get('product_metrics', []):
        if str(row.get('asin', '')).upper() == asin.upper():
            ctr = max(0.0, float(row.get('click_through_rate') or 0))
            cvr = max(0.0, float(row.get('conversion_rate') or 0))
            reward = max(0.0, float(row.get('average_reward') or 0))
            return min(1.0, ctr / .15) * .35 + min(1.0, cvr / .15) * .35 + min(1.0, reward / 1500) * .30
    return 0.0
