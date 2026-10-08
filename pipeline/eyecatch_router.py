from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from codex_eyecatch import CodexEyecatchError, ensure_codex_eyecatch
from eyecatch_adapter import EyecatchGenerationError, EyecatchGenerator

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config' / 'eyecatch_routing.json'


class EyecatchRoutingError(RuntimeError):
    pass


def _load_config() -> dict:
    data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    return {
        'mode': str(data.get('mode') or 'weighted_random'),
        'codex_share': int(data.get('codex_share', 20)),
        'api_share': int(data.get('api_share', 80)),
        'codex_reserve_percent': float(data.get('codex_reserve_percent', 20)),
        'api_fallback_on_codex_failure': bool(data.get('api_fallback_on_codex_failure', True)),
        'codex_fallback_on_api_failure': bool(data.get('codex_fallback_on_api_failure', True)),
        'api_model': str(data.get('api_model') or 'gpt-image-2.5-sunburst'),
        'api_quality': str(data.get('api_quality') or 'high'),
    }


def _existing_valid(slug: str) -> dict | None:
    image = ROOT / 'img' / slug / f'{slug}.png'
    receipt_path = ROOT / 'data' / 'eyecatch-receipts' / f'{slug}.json'
    if not image.is_file() or not receipt_path.is_file():
        return None
    try:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        raw = image.read_bytes()
    except (OSError, json.JSONDecodeError):
        return None
    if receipt.get('image_sha256') != hashlib.sha256(raw).hexdigest():
        return None
    if receipt.get('width') != 1536 or receipt.get('height') != 1024:
        return None
    return receipt


def _latest_codex_rate_limit() -> dict | None:
    session_root = Path.home() / '.codex' / 'sessions'
    files = sorted(session_root.glob('**/*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:100]:
        try:
            lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
        except OSError:
            continue
        for raw in reversed(lines):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            payload = row.get('payload') or {}
            if payload.get('type') != 'token_count':
                continue
            rate_limits = payload.get('rate_limits') or {}
            primary = rate_limits.get('primary') or {}
            if primary.get('used_percent') is None:
                continue
            used = float(primary['used_percent'])
            return {
                'used_percent': used,
                'remaining_percent': max(0.0, 100.0 - used),
                'resets_at': primary.get('resets_at'),
                'window_minutes': primary.get('window_minutes'),
                'source': str(path),
            }
    return None


def _api_available() -> bool:
    token_env = os.environ.get('SELLEMY_EYECATCH_TOKEN_ENV', '').strip()
    if not token_env:
        return False
    value = os.environ.get(token_env, '').strip()
    return value.startswith('sk-') and len(value) > 20


def _choose_route(config: dict, usage: dict | None) -> tuple[str, dict]:
    remaining = usage.get('remaining_percent') if usage else None
    reserve = float(config['codex_reserve_percent'])
    if remaining is not None and remaining <= reserve:
        return 'openai_api', {
            'reason': 'codex_reserve_threshold',
            'roll': None,
            'remaining_percent': remaining,
        }

    codex_share = max(0, int(config['codex_share']))
    api_share = max(0, int(config['api_share']))
    total = codex_share + api_share
    if total <= 0:
        raise EyecatchRoutingError('eyecatch routing shares sum to zero')
    roll = secrets.randbelow(total)
    route = 'codex' if roll < codex_share else 'openai_api'
    return route, {
        'reason': 'weighted_random',
        'roll': roll,
        'remaining_percent': remaining,
        'total_weight': total,
    }


def _stamp_receipt(slug: str, receipt: dict, routing: dict) -> dict:
    path = ROOT / 'data' / 'eyecatch-receipts' / f'{slug}.json'
    stamped = dict(receipt)
    stamped['routing'] = routing
    stamped['routing_recorded_at'] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(stamped, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return stamped


def _generate_api(*, slug: str, payload: dict, evidence: dict, config: dict) -> dict:
    if not _api_available():
        raise EyecatchGenerationError('Sellemy OpenAI API key unavailable')
    generator = EyecatchGenerator(
        endpoint=os.environ.get('SELLEMY_EYECATCH_ENDPOINT', 'https://api.openai.com/v1/images/generations'),
        token_env=os.environ.get('SELLEMY_EYECATCH_TOKEN_ENV', 'SELLEMY_OPENAI_API_KEY'),
        provider='openai',
        model=os.environ.get('SELLEMY_EYECATCH_MODEL', config['api_model']),
        quality=os.environ.get('SELLEMY_EYECATCH_QUALITY', config['api_quality']),
        timeout_seconds=float(os.environ.get('SELLEMY_EYECATCH_TIMEOUT_SECONDS', '300')),
    )
    return generator.generate(
        payload=payload,
        evidence=evidence,
        output=ROOT / 'img' / slug / f'{slug}.png',
        receipt_path=ROOT / 'data' / 'eyecatch-receipts' / f'{slug}.json',
    )


def ensure_routed_eyecatch(*, slug: str, title: str, category: str, evidence: dict, payload: dict) -> dict:
    existing = _existing_valid(slug)
    if existing is not None:
        return existing

    config = _load_config()
    usage = _latest_codex_rate_limit()
    selected, decision = _choose_route(config, usage)
    routing = {
        'mode': config['mode'],
        'selected_route': selected,
        'codex_share': config['codex_share'],
        'api_share': config['api_share'],
        'codex_reserve_percent': config['codex_reserve_percent'],
        'codex_usage': usage,
        **decision,
        'fallback_from': None,
    }

    errors: list[str] = []

    def run_codex() -> dict:
        return ensure_codex_eyecatch(
            slug=slug, title=title, category=category, evidence=evidence, payload=payload,
        )

    def run_api() -> dict:
        return _generate_api(slug=slug, payload=payload, evidence=evidence, config=config)

    order = [selected]
    if selected == 'codex' and config['api_fallback_on_codex_failure']:
        order.append('openai_api')
    elif selected == 'openai_api' and config['codex_fallback_on_api_failure']:
        order.append('codex')

    for index, route in enumerate(order):
        try:
            receipt = run_codex() if route == 'codex' else run_api()
            routing['executed_route'] = route
            routing['fallback_from'] = selected if index else None
            routing['errors_before_success'] = errors
            return _stamp_receipt(slug, receipt, routing)
        except (CodexEyecatchError, EyecatchGenerationError, RuntimeError) as exc:
            errors.append(f'{route}:{type(exc).__name__}:{exc}')
            continue

    raise EyecatchRoutingError('all eyecatch routes failed: ' + ' | '.join(errors))
