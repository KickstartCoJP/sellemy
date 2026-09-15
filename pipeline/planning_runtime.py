from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import date
from pathlib import Path

from analytics_feedback import load_feedback, topic_signal
from category_metadata import CATEGORIES

ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / 'json' / 'articles.json'
SEEDS = ROOT / 'data' / 'planning_seeds.json'

PLANNING_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['candidates'],
    'properties': {'candidates': {'type': 'array', 'minItems': 6, 'maxItems': 30, 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['slug', 'category', 'query', 'title', 'intent_key', 'comparison_axes', 'signals'],
        'properties': {
            'slug': {'type': 'string'}, 'category': {'type': 'string'}, 'query': {'type': 'string'},
            'title': {'type': 'string'}, 'intent_key': {'type': 'string'},
            'comparison_axes': {'type': 'array', 'minItems': 2, 'maxItems': 6, 'items': {'type': 'object', 'required': ['id', 'label', 'keywords'], 'properties': {'id': {'type': 'string'}, 'label': {'type': 'string'}, 'keywords': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}}}}},
            'signals': {'type': 'object', 'required': ['search_demand', 'purchase_intent', 'product_viability', 'seasonality', 'profitability', 'evidence_availability'], 'properties': {key: {'type': 'number'} for key in ('search_demand', 'purchase_intent', 'product_viability', 'seasonality', 'profitability', 'evidence_availability')}},
        },
    }}}
}


class PlanningError(RuntimeError):
    pass


def _normalize(value: str) -> str:
    value = value.lower().replace('６', '6')
    value = re.sub(r'(おすすめ|比較|選び方|6選|6picks|top6)', '', value)
    return re.sub(r'[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+', '', value)


def _context() -> dict:
    articles = json.loads(ARTICLES.read_text(encoding='utf-8'))
    return {
        'today': date.today().isoformat(), 'allowed_categories': list(CATEGORIES),
        'exploration_seeds': json.loads(SEEDS.read_text(encoding='utf-8'))['categories'],
        'existing_articles': [{k: row.get(k) for k in ('slug', 'category', 'title', 'summary')} for row in articles],
        'instruction': 'Explore fresh search and purchase intents every cycle. Seeds are inspiration, never a queue. Return novel candidates across all categories.',
    }


def discover_candidates() -> list[dict]:
    configured = os.environ.get('SELLEMY_PLANNING_COMMAND', '').strip()
    if not configured:
        from writer_runtime import _command
        command = _command()
    else:
        command = shlex.split(configured)
    model = os.environ.get('SELLEMY_PLANNING_MODEL', os.environ.get('SELLEMY_WRITER_PRIMARY_MODEL', 'sonnet'))
    prompt = 'You plan Japanese Sellemy product-comparison topics. Generate fresh candidates from current season, search/purchase intent, product viability and the supplied context. Return structured JSON only.\n' + json.dumps(_context(), ensure_ascii=False)
    completed = subprocess.run(command + ['-p', '--safe-mode', '--no-session-persistence', '--tools', '', '--model', model, '--output-format', 'json', '--json-schema', json.dumps(PLANNING_SCHEMA)], input=prompt, text=True, capture_output=True, timeout=int(os.environ.get('SELLEMY_PLANNING_TIMEOUT_SECONDS', '300')), check=False)
    if completed.returncode != 0:
        raise PlanningError(f'planning provider failed: {(completed.stderr or completed.stdout)[-800:]}')
    envelope = json.loads(completed.stdout)
    value = envelope.get('structured_output')
    if not isinstance(value, dict) or not isinstance(value.get('candidates'), list):
        raise PlanningError('planning provider returned no candidate set')
    return value['candidates']


def validate_candidate(candidate: dict) -> None:
    for field in ('slug', 'category', 'query', 'title', 'intent_key', 'comparison_axes', 'signals'):
        if field not in candidate:
            raise PlanningError(f'candidate missing {field}')
    if candidate['category'] not in CATEGORIES:
        raise PlanningError(f'candidate category outside canon: {candidate["category"]}')
    if not isinstance(candidate['comparison_axes'], list) or len(candidate['comparison_axes']) < 2:
        raise PlanningError('candidate requires at least two topic-specific comparison axes')
    axis_ids = set()
    for axis in candidate['comparison_axes']:
        if not isinstance(axis, dict) or not axis.get('id') or not axis.get('label') or not axis.get('keywords'):
            raise PlanningError('each comparison axis requires id, label, and non-empty keywords')
        if axis['id'] in axis_ids:
            raise PlanningError(f'duplicate comparison axis id: {axis["id"]}')
        axis_ids.add(axis['id'])
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)+', candidate['slug']):
        raise PlanningError(f'invalid candidate slug: {candidate["slug"]}')


def rank_candidates(candidates: list[dict], feedback: dict | None = None) -> list[dict]:
    feedback = feedback or load_feedback()
    existing = json.loads(ARTICLES.read_text(encoding='utf-8'))
    existing_slugs = {row['slug'] for row in existing}
    existing_intents = {_normalize(' '.join(str(row.get(k, '')) for k in ('slug', 'title', 'summary'))) for row in existing}
    category_counts = {category: sum(row.get('category') == category for row in existing) for category in CATEGORIES}
    ranked = []
    for candidate in candidates:
        validate_candidate(candidate)
        intent = _normalize(candidate['intent_key'])
        if candidate['slug'] in existing_slugs or not intent or any(intent in old or old in intent for old in existing_intents if old):
            continue
        signals = candidate['signals']
        base = sum(max(0.0, min(1.0, float(signals.get(k, 0)))) * weight for k, weight in {
            'search_demand': .20, 'purchase_intent': .20, 'product_viability': .20,
            'seasonality': .10, 'profitability': .15, 'evidence_availability': .15,
        }.items())
        learning = topic_signal(feedback, category=candidate['category'], intent_key=candidate['intent_key'])
        balance = 1 / (1 + category_counts[candidate['category']])
        ranked.append({**candidate, 'planning_score': round(base * .75 + learning * .15 + balance * .10, 6), 'feedback_signal': learning})
    return sorted(ranked, key=lambda row: (-row['planning_score'], row['slug']))


def plan_next_topic(candidates: list[dict] | None = None, feedback: dict | None = None) -> dict:
    ranked = rank_candidates(candidates if candidates is not None else discover_candidates(), feedback)
    if not ranked:
        raise PlanningError('continuous planning produced no eligible non-duplicate topic; no publish')
    selected = ranked[0]
    return {**selected, 'selection_reason': 'highest eligible multi-signal planning score after duplicate and category checks'}
