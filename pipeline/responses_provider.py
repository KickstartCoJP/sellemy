"""Stage-local Responses routing. No shared API key or project override is used."""
from __future__ import annotations

import json
import logging
import math
import os
import random
import urllib.request
from pathlib import Path

from codex_provider import _ensure_brief, _runtime_paths, _strict_schema, _validate_required_shape

LOG = logging.getLogger(__name__)


class ResponsesError(RuntimeError):
    def __init__(self, message: str, *, kind: str = 'output'):
        super().__init__(message)
        self.kind = kind


def validate_output(value, schema):
    """Validate the closed, required object/array schemas used by these stages."""
    _validate_required_shape(value, schema)
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ResponsesError('unexpected output properties')
        for key, child in value.items():
            if key in properties:
                validate_output(child, properties[key])
    elif isinstance(value, list):
        for child in value:
            validate_output(child, schema['items'])
    elif isinstance(value, float) and not math.isfinite(value):
        raise ResponsesError('non-finite output number')
    if 'enum' in schema and value not in schema['enum']:
        raise ResponsesError('invalid output enum')


def _shared_prompt(stage: str, prompt: str) -> str:
    root = Path(__file__).resolve().parents[1]
    surface = 'bu-codex-sellemy-planning' if stage == 'planner' else 'bu-codex-sellemy-writer'
    _, brief_path = _runtime_paths(root, surface)
    _ensure_brief(brief_path, surface)
    bridge_path = root / 'config' / 'sellemy-codex-canon-bootstrap.md'
    if not bridge_path.is_file():
        raise ResponsesError('shared canon bridge unavailable', kind='config')
    return (
        'Use the same Sellemy canon bridge and runtime brief used by the Codex route. '
        'Current task input and Evidence override stale memory.\n\n'
        'CANON_BRIDGE:\n' + bridge_path.read_text(encoding='utf-8') +
        '\n\nRUNTIME_BRIEF:\n' + brief_path.read_text(encoding='utf-8') +
        '\n\nCURRENT_TASK:\n' + prompt
    )


def generate(stage, schema, prompt, *, model: str | None = None):
    prefix = f'SELLEMY_{stage.upper()}'
    token = os.environ.get(prefix + '_OPENAI_API_KEY', '').strip()
    if not token:
        raise ResponsesError('stage credential unavailable', kind='config')
    model = model or os.environ.get(prefix + '_OPENAI_MODEL', 'gpt-6.1-sol')
    strict = _strict_schema(schema)
    body = {'model': model, 'input': _shared_prompt(stage, prompt), 'store': False,
            'text': {'format': {'type': 'json_schema', 'name': stage,
                                'strict': True, 'schema': strict}}}
    try:
        request = urllib.request.Request('https://api.openai.com/v1/responses',
            data=json.dumps(body).encode(), method='POST',
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=int(os.environ.get(prefix + '_OPENAI_TIMEOUT_SECONDS', '300'))) as response:
            envelope = json.load(response)
    except Exception:
        # Never propagate HTTP bodies, headers, tokens, or generated content to logs.
        raise ResponsesError('Responses transport failed', kind='transport') from None
    try:
        if envelope.get('status') != 'completed' or envelope.get('error'):
            raise ResponsesError('response did not complete', kind='output')
        chunks = []
        for item in envelope.get('output', []):
            if item.get('type') != 'message':
                continue
            for content in item.get('content', []):
                if content.get('type') == 'refusal':
                    raise ResponsesError('response refused', kind='output')
                if content.get('type') == 'output_text':
                    chunks.append(content['text'])
        value = json.loads(''.join(chunks))
        validate_output(value, strict)
    except ResponsesError:
        raise
    except Exception:
        raise ResponsesError('Responses output validation failed', kind='output') from None
    return value, {'runtime': 'openai_responses', 'model': model,
                   'session_persisted': False, 'usage': envelope.get('usage', {})}


def route(stage, schema, prompt, local, *, escalate: bool = False):
    """Route with Sol by default, Astra for quality escalation, then local fallback."""
    prefix = f'SELLEMY_{stage.upper()}'
    api = float(os.environ.get(prefix + '_API_WEIGHT', '100'))
    codex = float(os.environ.get(prefix + '_CODEX_WEIGHT', '0'))
    if not all(math.isfinite(w) and w >= 0 for w in (api, codex)) or api + codex <= 0:
        raise ValueError('routing weights must be finite, nonnegative, and have a positive total')
    selected = 'openai_responses' if random.random() * (api + codex) < api else 'codex'
    primary_model = os.environ.get(prefix + '_OPENAI_MODEL', 'gpt-6.1-sol')
    escalation_model = os.environ.get(prefix + '_OPENAI_ESCALATION_MODEL', 'gpt-6-astra')
    route_meta = {
        'route_selected': selected,
        'route_weights': {'api': api, 'codex': codex},
        'quality_escalation_requested': bool(escalate),
    }
    LOG.warning('stage=%s route_selected=%s api_weight=%s codex_weight=%s escalation=%s',
                stage, selected, api, codex, escalate)
    field = 'planning' if stage == 'planner' else 'writer'
    api_attempts: list[str] = []
    api_failed = False
    api_failure_reason = None

    if selected == 'openai_responses':
        models = [escalation_model] if escalate else [primary_model]
        for index, model in enumerate(models):
            try:
                value, meta = generate(stage, schema, prompt, model=model)
            except ResponsesError as exc:
                api_attempts.append('openai_responses:' + model)
                api_failure_reason = exc.kind
                if (not escalate and index == 0 and exc.kind == 'output'
                        and escalation_model != primary_model):
                    LOG.warning('stage=%s provider=openai_responses escalate=%s->%s reason=sol_output_error',
                                stage, primary_model, escalation_model)
                    models.append(escalation_model)
                    continue
                if exc.kind == 'output':
                    # Astra quality exhaustion must not become availability fallback.
                    raise
                api_failed = True
                break
            else:
                api_attempts.append('openai_responses:' + model)
                escalated = model == escalation_model and model != primary_model
                meta.update({
                    f'{field}_provider_requested': selected,
                    f'{field}_provider_used': selected,
                    f'{field}_model_requested': models[0],
                    f'{field}_model_used': model,
                    'fallback_used': bool(index > 0),
                    'fallback_reason': 'sol_output_error' if index > 0 else None,
                    f'{field}_attempt_count': len(api_attempts),
                    'provider_attempts': list(api_attempts),
                    'quality_escalated': escalated,
                })
                LOG.warning('stage=%s provider_used=openai_responses model=%s fallback=%s',
                            stage, model, bool(index > 0))
                return value, {**meta, **route_meta}

    try:
        value, meta = local()
    except Exception:
        LOG.warning('stage=%s route_selected=%s provider_used=none fallback_exhausted=true', stage, selected)
        raise

    used = meta.get(f'{field}_provider_used')
    chain = list(api_attempts)
    chain.append('codex')
    if meta.get('fallback_used'):
        chain.append('claude')
    if api_failed:
        meta.update({
            f'{field}_provider_requested': selected,
            f'{field}_model_requested': escalation_model if escalate else primary_model,
            'fallback_used': True,
            'fallback_reason': 'api_' + str(api_failure_reason or 'error'),
            f'{field}_attempt_count': meta.get(f'{field}_attempt_count', 1) + len(api_attempts),
        })
    LOG.warning('stage=%s provider_used=%s fallback=%s chain=%s',
                stage, used, meta.get('fallback_used'), '->'.join(chain))
    return value, {**meta, **route_meta, 'provider_attempts': chain}
