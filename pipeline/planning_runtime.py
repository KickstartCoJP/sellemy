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
from writer_runtime import AVAILABILITY_PATTERN, _diagnostic
from standard_work_fallback import request_standard_work_fallback

ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / 'json' / 'articles.json'
SEEDS = ROOT / 'data' / 'planning_seeds.json'
RECENT_PORTFOLIO_WINDOW = 24
DIVERSITY_STOPWORDS = {
    'autumn', 'winter', 'summer', 'spring', 'home', 'electric', 'portable', 'smart',
    'wireless', 'automatic', 'desktop', 'personal', 'mini', 'compact', 'daily', 'for',
    'with', 'and', 'picks', 'selection', 'choices',
}

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


class CandidateBatch(list):
    def __init__(self, candidates: list[dict], metadata: dict):
        super().__init__(candidates)
        self.provider_metadata = metadata


def _normalize(value: str) -> str:
    value = value.lower().replace('６', '6')
    value = re.sub(r'(おすすめ|比較|選び方|6選|6picks|top6)', '', value)
    return re.sub(r'[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+', '', value)


def _topic_tokens(value: str) -> set[str]:
    parts = re.findall(r'[a-z0-9]+', value.lower().replace('_', '-'))
    return {part for part in parts if len(part) >= 4 and part not in DIVERSITY_STOPWORDS and not part.isdigit()}


def _portfolio_signals(articles: list[dict]) -> dict:
    category_counts = {category: sum(row.get('category') == category for row in articles) for category in CATEGORIES}
    recent = articles[-RECENT_PORTFOLIO_WINDOW:]
    recent_category_counts = {category: sum(row.get('category') == category for row in recent) for category in CATEGORIES}
    token_counts: dict[str, int] = {}
    for row in recent:
        for token in _topic_tokens(' '.join(str(row.get(k, '')) for k in ('slug', 'title'))):
            token_counts[token] = token_counts.get(token, 0) + 1
    return {
        'category_counts': category_counts,
        'recent_category_counts': recent_category_counts,
        'recent_topic_tokens': dict(sorted(token_counts.items(), key=lambda item: (-item[1], item[0]))[:20]),
    }


def _context() -> dict:
    articles = json.loads(ARTICLES.read_text(encoding='utf-8'))
    return {
        'today': date.today().isoformat(), 'allowed_categories': list(CATEGORIES),
        'exploration_seeds': json.loads(SEEDS.read_text(encoding='utf-8'))['categories'],
        'existing_articles': [{k: row.get(k) for k in ('slug', 'category', 'title', 'summary')} for row in articles],
        'portfolio': _portfolio_signals(articles),
        'instruction': 'Explore fresh search and purchase intents every cycle. Seeds are inspiration, never a queue. Actively diversify underrepresented categories and avoid repeating the same distinctive topic family in the recent portfolio. Return novel candidates across all categories.',
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
    timeout = int(os.environ.get('SELLEMY_PLANNING_TIMEOUT_SECONDS', '300'))
    requested = f'claude:{model}'
    metadata = {'planning_provider_requested': requested, 'planning_provider_used': requested,
                'fallback_used': False, 'fallback_reason': None, 'planning_attempt_count': 1}
    try:
        completed = subprocess.run(command + ['-p', '--safe-mode', '--no-session-persistence', '--tools', '', '--model', model, '--output-format', 'json', '--json-schema', json.dumps(PLANNING_SCHEMA)], input=prompt, text=True, capture_output=True, timeout=timeout, check=False)
        diagnostic = _diagnostic(completed)
        if completed.returncode:
            if not AVAILABILITY_PATTERN.search(diagnostic):
                raise PlanningError(f'planning provider failed: {diagnostic}')
            raise PlanningError('planning primary availability failure')
        envelope = json.loads(completed.stdout)
        value = envelope.get('structured_output')
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlanningError(f'planning provider invocation failed: {type(exc).__name__}') from exc
    except PlanningError as exc:
        if str(exc) != 'planning primary availability failure':
            raise
        value = request_standard_work_fallback(stage='planning', prompt=prompt, schema=PLANNING_SCHEMA)
        metadata.update({'planning_provider_used': 'standard_work:bu-work4',
                         'fallback_used': True, 'fallback_reason': 'primary_availability_error',
                         'planning_attempt_count': 2})
    if not isinstance(value, dict) or not isinstance(value.get('candidates'), list):
        raise PlanningError('planning provider returned no candidate set')
    return CandidateBatch(value['candidates'], metadata)


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
    portfolio = _portfolio_signals(existing)
    category_counts = portfolio['category_counts']
    recent_category_counts = portfolio['recent_category_counts']
    recent = existing[-RECENT_PORTFOLIO_WINDOW:]
    max_category_count = max(category_counts.values(), default=0)
    max_recent_category_count = max(recent_category_counts.values(), default=0)
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
        category = candidate['category']
        global_gap = ((max_category_count - category_counts[category]) / max_category_count) if max_category_count else 1.0
        recent_gap = ((max_recent_category_count - recent_category_counts[category]) / max_recent_category_count) if max_recent_category_count else 1.0
        portfolio_balance = global_gap * .65 + recent_gap * .35
        candidate_tokens = _topic_tokens(' '.join(str(candidate.get(k, '')) for k in ('slug', 'intent_key', 'title')))
        recent_family_hits = []
        for row in recent:
            row_tokens = _topic_tokens(' '.join(str(row.get(k, '')) for k in ('slug', 'title')))
            if candidate_tokens & row_tokens:
                recent_family_hits.append(row.get('slug'))
        topic_diversity = max(0.0, 1.0 - min(1.0, len(recent_family_hits) / 3.0))
        score = base * .65 + learning * .10 + portfolio_balance * .15 + topic_diversity * .10
        ranked.append({
            **candidate, 'planning_score': round(score, 6), 'feedback_signal': learning,
            'portfolio_balance': round(portfolio_balance, 6), 'topic_diversity': round(topic_diversity, 6),
            'recent_family_hits': recent_family_hits[-6:],
        })
    return sorted(ranked, key=lambda row: (-row['planning_score'], row['slug']))


def plan_next_topic(candidates: list[dict] | None = None, feedback: dict | None = None) -> dict:
    ranked = rank_candidates(candidates if candidates is not None else discover_candidates(), feedback)
    if not ranked:
        raise PlanningError('continuous planning produced no eligible non-duplicate topic; no publish')
    selected = ranked[0]
    return {**selected, 'selection_reason': 'highest eligible multi-signal planning score after duplicate and category checks'}
