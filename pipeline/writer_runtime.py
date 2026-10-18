from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from responses_provider import LOG, route
from comparison_acceptance import CONTRACT, blocked, product_issues
from dataclasses import dataclass
from pathlib import Path
from codex_provider import CodexProviderError, generate_isolated as codex_generate_persistent
from recovery_provider_policy import require_local_provider_allowed, active_policy
from local_ollama_provider import generate_json as local_generate_json
from responses_provider import validate_output


class WriterInvocationError(RuntimeError):
    def __init__(self, message: str, *, availability: bool = False):
        super().__init__(message)
        self.availability = availability


WRITER_JSON_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['status', 'needs_product_reselection', 'reasons', 'slug', 'category', 'h1', 'lead', 'summary', 'comparison_groups', 'products', 'how_to_choose', 'conclusion'],
    'properties': {
        'status': {'type': 'string', 'enum': ['READY', 'BLOCKED']},
        'needs_product_reselection': {'type': 'boolean'},
        'reasons': {'type': 'array', 'items': {'type': 'string'}},
        'slug': {'type': 'string'}, 'category': {'type': 'string'}, 'h1': {'type': 'string'},
        'lead': {'type': 'string'}, 'summary': {'type': 'string'},
        'comparison_groups': {
            'type': 'array', 'minItems': 0, 'maxItems': 3,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['id', 'title', 'angle', 'product_refs'],
                'properties': {
                    'id': {'type': 'string'}, 'title': {'type': 'string'}, 'angle': {'type': 'string'},
                    'product_refs': {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'string'}},
                },
            },
        },
        'products': {
            'type': 'array', 'minItems': 0, 'maxItems': 6,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['ref', 'h3', 'description'],
                'properties': {'ref': {'type': 'string'}, 'h3': {'type': 'string'}, 'description': {'type': 'string'}},
            },
        },
        'how_to_choose': {'type': 'string'}, 'conclusion': {'type': 'string'},
    },
}

AVAILABILITY_PATTERN = re.compile(
    r'\b(?:429|529|rate.?limit|usage.?limit|weekly limit|session limit|quota|overload|service unavailable|temporar(?:y|ily) unavailable|capacity)\b',
    re.I,
)


def _diagnostic(completed: subprocess.CompletedProcess) -> str:
    try:
        envelope = json.loads(completed.stdout)
        if isinstance(envelope, dict) and isinstance(envelope.get('result'), str):
            return envelope['result'][-500:]
    except (TypeError, json.JSONDecodeError):
        pass
    return re.sub(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', '[redacted-id]',
                  (completed.stderr or completed.stdout).strip()[-500:], flags=re.I)


@dataclass(frozen=True)
class WriterProvider:
    name: str
    command: tuple[str, ...]
    model: str
    certified: bool
    kind: str = 'claude'


def _default_executable() -> str | None:
    return next((str(p) for p in (Path('/opt/homebrew/bin/claude'), Path('/usr/local/bin/claude')) if p.is_file()), shutil.which('claude'))


def _provider(name: str) -> WriterProvider:
    upper = name.upper()
    configured = os.environ.get(f'SELLEMY_WRITER_{upper}_COMMAND', '').strip()
    if configured:
        command = tuple(shlex.split(configured))
    elif name == 'primary':
        executable = _default_executable()
        command = (executable,) if executable else ()
    else:
        command = ()
    model = os.environ.get(f'SELLEMY_WRITER_{upper}_MODEL', 'sonnet' if name == 'primary' else '').strip()
    certified = name == 'primary' or os.environ.get('SELLEMY_WRITER_SECONDARY_CERTIFIED', '').lower() in ('1', 'true', 'yes')
    kind = os.environ.get(f'SELLEMY_WRITER_{upper}_KIND', 'claude').strip().lower()
    return WriterProvider(name=name, command=command, model=model, certified=certified, kind=kind)


def _command() -> list[str]:
    """Compatibility helper for environment checks; production invocation uses providers."""
    primary = _provider('primary')
    if not primary.command:
        raise WriterInvocationError('Primary Writer runtime unavailable', availability=True)
    return list(primary.command)


def _prompt(topic: dict, evidence: dict, *, previous_payload: dict | None = None, gate_feedback: dict | None = None) -> str:
    prompt = CONTRACT + '\n' + (
        'You are the Writer stage for a Japanese product-comparison publication. Return only structured JSON. '
        'Write all public-facing Japanese prose yourself; scripts may not expand, paraphrase, or pad it. Ground every material claim only in supplied Evidence and preserve source uncertainty. Marketplace marketing text is not proof of performance or compatibility. '
        'Do not state prices, internal workflow terms, ASINs, or affiliate operations. Do not copy raw listing titles verbatim. '
        'Design exactly 3 semantic comparison groups from the topic-specific comparison_axes, with exactly 2 products in each group; groups must partition all six refs exactly once. '
        'Price may be discussed only as a non-specific constraint, never as a fixed low/mid/high article structure. Make each recommendation concrete about who should choose it and why, based on actual differences. If the six products do not represent one coherent product class or Evidence cannot support the promised comparison, return BLOCKED instead of writing cautious filler. '
        'Keep descriptions distinct. Targets: lead 180-230, summary 130-170, how_to_choose 190-270, descriptions 320-400, h3 6-45, rendered main 3000-3400. '
        'Preserve slug, category, and refs exactly.\n\n'
        f'TOPIC:\n{json.dumps(topic, ensure_ascii=False, sort_keys=True)}\n\n'
        f'EVIDENCE:\n{json.dumps(evidence, ensure_ascii=False, sort_keys=True)}'
    )
    if previous_payload is not None and isinstance(gate_feedback, dict):
        measures = gate_feedback.get('measurements') or {}
        failures = gate_feedback.get('qa_failures') or {}
        length = measures.get('main_len')
        if failures.get('main_in_range') is False and isinstance(length, int):
            if length > 3400:
                prompt += (f'\nCRITICAL LENGTH CORRECTION: previous rendered main = {length}, '
                           f'which exceeds the absolute maximum 3400 by {length - 3400}. '
                           'Shorten total public prose by at least the excess plus 120 Japanese '
                           'characters without removing grounded material facts; aim for rendered main '
                           '3150-3250. Never count source JSON as article prose.\n')
            elif length < 3000:
                prompt += (f'\nCRITICAL LENGTH CORRECTION: previous rendered main = {length}, '
                           f'which is short by {3000 - length}. Increase useful comparison '
                           'detail by at least the deficit plus 80 characters; aim 3150-3250.\n')
        for field, low, high in (('summary',130,170),('how_to_choose',190,270)):
            length = measures.get(field + '_len')
            if isinstance(length,int) and failures.get(field + '_in_range') is False:
                prompt += (f'\nCRITICAL {field} LENGTH: prior {length}, required '
                           f'{low}-{high}, aim {low + (high-low)//2}; revise this field.\n')
    if previous_payload is not None:
        prompt += (
            '\n\nRewrite the complete payload using the gate diagnostics. Do not patch with scripted filler.\n\n'
            f'PREVIOUS_PAYLOAD:\n{json.dumps(previous_payload, ensure_ascii=False, sort_keys=True)}\n\n'
            f'GATE_FEEDBACK:\n{json.dumps(gate_feedback or {}, ensure_ascii=False, sort_keys=True)}'
        )
    if previous_payload is not None and isinstance(gate_feedback, dict):
        # Last in the prompt so it cannot be buried below the large evidence and
        # previous payload. These are Writer instructions, not scripted edits.
        measurements = gate_feedback.get('measurements') or {}
        if (gate_feedback.get('qa_failures') or {}).get('main_in_range') is False:
            main_len = measurements.get('main_len')
            if isinstance(main_len,int) and main_len > 3400:
                prompt += ('\n\nFINAL MANDATORY REVISION CHECKLIST: The previous rendered main body '
                           f'contained {main_len} characters. The maximum is 3400. '
                           'Make the new rendered main body no more than 3300 by shortening the '
                           'editorial text, while preserving all grounded decision facts. '
                           'Choose concise product descriptions (320-335 each), comparison-group '
                           'angle text (max 65 each), summary (140-155), how_to_choose (200-225), '
                           'and conclusion (120-145). Keep each public field within its own '
                           'hard QA bounds; do not rely on source JSON or HTML markup in length calculations. '
                           'Return revised prose as the COMPLETE JSON payload, not a patch.\n')
    return prompt


def _invoke(provider: WriterProvider, prompt: str, *, usage_key: str = "") -> tuple[dict, dict]:
    if not provider.command or not provider.command[0]:
        raise WriterInvocationError(f'{provider.name} Writer command unavailable', availability=True)
    if not provider.model:
        raise WriterInvocationError(f'{provider.name} Writer model is not configured', availability=True)
    if provider.kind in {'codex', 'claude'}:
        require_local_provider_allowed(provider.kind)
    if provider.kind == 'codex':
        try:
            payload, codex_meta = codex_generate_persistent(
                provider.command, provider.model, WRITER_JSON_SCHEMA, prompt,
                timeout=int(os.environ.get('SELLEMY_WRITER_TIMEOUT_SECONDS', '600')),
                root=Path(__file__).resolve().parents[1], surface_key='bu-codex-sellemy-writer', stage='writer', usage_key=usage_key,
            )
        except CodexProviderError as exc:
            raise WriterInvocationError(f'{provider.name} Writer {exc}', availability=True) from exc
        return payload, {'runtime': provider.command[0], 'model': provider.model,
                         'cost_usd': None, 'duration_api_ms': None, 'session_persisted': False,
                         **codex_meta}
    if provider.kind != 'claude':
        raise WriterInvocationError(f'unknown Writer provider kind: {provider.kind}')
    budget = os.environ.get('SELLEMY_WRITER_MAX_BUDGET_USD', '1.00')
    timeout = int(os.environ.get('SELLEMY_WRITER_TIMEOUT_SECONDS', '600'))
    args = list(provider.command) + ['-p', '--safe-mode', '--no-session-persistence', '--tools', '', '--model', provider.model, '--max-budget-usd', budget, '--output-format', 'json', '--json-schema', json.dumps(WRITER_JSON_SCHEMA)]
    try:
        completed = subprocess.run(args, input=prompt, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WriterInvocationError(f'{provider.name} Writer unavailable: {exc}', availability=True) from exc
    diagnostic = _diagnostic(completed)
    if completed.returncode != 0:
        raise WriterInvocationError(
            f'{provider.name} Writer exited {completed.returncode}: {diagnostic or "no diagnostic"}',
            availability=bool(AVAILABILITY_PATTERN.search(diagnostic)),
        )
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WriterInvocationError(f'{provider.name} Writer returned non-JSON output') from exc
    payload = envelope.get('structured_output')
    if not isinstance(payload, dict):
        try:
            payload = json.loads(envelope.get('result')) if isinstance(envelope.get('result'), str) else None
        except json.JSONDecodeError:
            payload = None
    if not isinstance(payload, dict):
        raise WriterInvocationError(f'{provider.name} Writer returned no structured_output object')
    metadata = {
        'runtime': provider.command[0], 'model': envelope.get('modelUsage') or provider.model,
        'cost_usd': envelope.get('total_cost_usd'), 'duration_api_ms': envelope.get('duration_api_ms'),
        'session_persisted': False,
    }
    return payload, metadata


def _invoke_local_writer(topic: dict, evidence: dict, *, previous_payload: dict | None = None, gate_feedback: dict | None = None) -> tuple[dict, dict]:
    primary, secondary = _provider('primary'), _provider('secondary')
    prompt = _prompt(topic, evidence, previous_payload=previous_payload, gate_feedback=gate_feedback)
    requested = f'{primary.name}:{primary.model}'
    try:
        payload, metadata = _invoke(primary, prompt, usage_key=str(topic.get('slug') or ''))
        return payload, {**metadata, 'writer_model_requested': requested, 'writer_model_used': requested, 'writer_provider_requested': primary.kind, 'writer_provider_used': primary.kind, 'fallback_used': False, 'fallback_reason': None, 'writer_attempt_count': 1}
    except WriterInvocationError as exc:
        if not exc.availability:
            raise
        if not secondary.command or not secondary.model or not secondary.certified:
            raise WriterInvocationError(f'{exc}; certified Secondary Writer unavailable', availability=True) from exc
        LOG.warning('stage=writer provider=codex fallback=claude reason=local_provider_error')
        payload, metadata = _invoke(secondary, prompt, usage_key=str(topic.get('slug') or ''))
        used = f'{secondary.name}:{secondary.model}'
        return payload, {**metadata, 'writer_model_requested': requested, 'writer_model_used': used, 'writer_provider_requested': primary.kind, 'writer_provider_used': secondary.kind, 'fallback_used': True, 'fallback_reason': 'primary_availability_error', 'writer_attempt_count': 2}


def invoke_writer(topic: dict, evidence: dict, *, previous_payload: dict | None = None, gate_feedback: dict | None = None) -> tuple[dict, dict]:
    issues = product_issues(evidence)
    if issues:
        return {**blocked(issues), 'slug': topic.get('slug'), 'category': topic.get('category')}, {'writer_invoked': False}
    prompt = _prompt(topic, evidence, previous_payload=previous_payload, gate_feedback=gate_feedback)
    policy = active_policy() or {}
    if policy.get('writer_provider') == 'ollama':
        require_local_provider_allowed('ollama')
        payload, metadata = local_generate_json(schema=WRITER_JSON_SCHEMA, prompt=prompt)
        validate_output(payload, WRITER_JSON_SCHEMA)
        metadata.update({
            'writer_provider_requested': 'ollama', 'writer_provider_used': 'ollama',
            'writer_model_requested': metadata.get('model'), 'writer_model_used': metadata.get('model'),
            'fallback_used': False, 'fallback_reason': None, 'writer_attempt_count': 1,
        })
        return payload, metadata
    return route(
        'writer', WRITER_JSON_SCHEMA, prompt,
        lambda: _invoke_local_writer(
            topic, evidence, previous_payload=previous_payload, gate_feedback=gate_feedback
        ),
        escalate=bool(gate_feedback),
    )
