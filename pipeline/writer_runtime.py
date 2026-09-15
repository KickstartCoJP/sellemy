from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WriterInvocationError(RuntimeError):
    def __init__(self, message: str, *, availability: bool = False):
        super().__init__(message)
        self.availability = availability


WRITER_JSON_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['slug', 'category', 'h1', 'lead', 'summary', 'comparison_groups', 'products', 'how_to_choose', 'conclusion'],
    'properties': {
        'slug': {'type': 'string'}, 'category': {'type': 'string'}, 'h1': {'type': 'string'},
        'lead': {'type': 'string'}, 'summary': {'type': 'string'},
        'comparison_groups': {
            'type': 'array', 'minItems': 1, 'maxItems': 6,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['id', 'title', 'angle', 'product_refs'],
                'properties': {
                    'id': {'type': 'string'}, 'title': {'type': 'string'}, 'angle': {'type': 'string'},
                    'product_refs': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
                },
            },
        },
        'products': {
            'type': 'array', 'minItems': 6, 'maxItems': 6,
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
    r'\b(?:429|529|rate.?limit|usage.?limit|quota|overload|service unavailable|temporar(?:y|ily) unavailable|capacity)\b',
    re.I,
)


@dataclass(frozen=True)
class WriterProvider:
    name: str
    command: tuple[str, ...]
    model: str
    certified: bool


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
    return WriterProvider(name=name, command=command, model=model, certified=certified)


def _command() -> list[str]:
    """Compatibility helper for environment checks; production invocation uses providers."""
    primary = _provider('primary')
    if not primary.command:
        raise WriterInvocationError('Primary Writer runtime unavailable', availability=True)
    return list(primary.command)


def _prompt(topic: dict, evidence: dict, *, previous_payload: dict | None = None, gate_feedback: dict | None = None) -> str:
    prompt = (
        'You are the Writer stage for a Japanese product-comparison publication. Return only structured JSON. '
        'Write all public-facing Japanese prose yourself; scripts may not expand, paraphrase, or pad it. Ground claims only in evidence. '
        'Do not state prices, internal workflow terms, ASINs, or affiliate operations. Do not copy raw listing titles verbatim. '
        'Design 1-6 semantic comparison groups from the topic-specific comparison_axes; groups must partition all six refs exactly once. '
        'Price may be discussed only as a non-specific constraint, never as a fixed low/mid/high article structure. '
        'Keep descriptions distinct. Targets: lead 180-230, summary 130-170, how_to_choose 190-270, descriptions 320-400, h3 6-45, rendered main 3000-3400. '
        'Preserve slug, category, and refs exactly.\n\n'
        f'TOPIC:\n{json.dumps(topic, ensure_ascii=False, sort_keys=True)}\n\n'
        f'EVIDENCE:\n{json.dumps(evidence, ensure_ascii=False, sort_keys=True)}'
    )
    if previous_payload is not None:
        prompt += (
            '\n\nRewrite the complete payload using the gate diagnostics. Do not patch with scripted filler.\n\n'
            f'PREVIOUS_PAYLOAD:\n{json.dumps(previous_payload, ensure_ascii=False, sort_keys=True)}\n\n'
            f'GATE_FEEDBACK:\n{json.dumps(gate_feedback or {}, ensure_ascii=False, sort_keys=True)}'
        )
    return prompt


def _invoke(provider: WriterProvider, prompt: str) -> tuple[dict, dict]:
    if not provider.command or not provider.command[0]:
        raise WriterInvocationError(f'{provider.name} Writer command unavailable', availability=True)
    if not provider.model:
        raise WriterInvocationError(f'{provider.name} Writer model is not configured', availability=True)
    budget = os.environ.get('SELLEMY_WRITER_MAX_BUDGET_USD', '1.00')
    timeout = int(os.environ.get('SELLEMY_WRITER_TIMEOUT_SECONDS', '600'))
    args = list(provider.command) + ['-p', '--safe-mode', '--no-session-persistence', '--tools', '', '--model', provider.model, '--max-budget-usd', budget, '--output-format', 'json', '--json-schema', json.dumps(WRITER_JSON_SCHEMA)]
    try:
        completed = subprocess.run(args, input=prompt, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WriterInvocationError(f'{provider.name} Writer unavailable: {exc}', availability=True) from exc
    diagnostic = (completed.stderr or completed.stdout).strip()[-1000:]
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


def invoke_writer(topic: dict, evidence: dict, *, previous_payload: dict | None = None, gate_feedback: dict | None = None) -> tuple[dict, dict]:
    primary, secondary = _provider('primary'), _provider('secondary')
    prompt = _prompt(topic, evidence, previous_payload=previous_payload, gate_feedback=gate_feedback)
    requested = f'{primary.name}:{primary.model}'
    try:
        payload, metadata = _invoke(primary, prompt)
        return payload, {**metadata, 'writer_model_requested': requested, 'writer_model_used': requested, 'fallback_used': False, 'fallback_reason': None, 'writer_attempt_count': 1}
    except WriterInvocationError as exc:
        if not exc.availability:
            raise
        if not secondary.command or not secondary.model or not secondary.certified:
            raise WriterInvocationError(f'{exc}; certified Secondary Writer unavailable', availability=True) from exc
        payload, metadata = _invoke(secondary, prompt)
        used = f'{secondary.name}:{secondary.model}'
        return payload, {**metadata, 'writer_model_requested': requested, 'writer_model_used': used, 'fallback_used': True, 'fallback_reason': str(exc), 'writer_attempt_count': 2}
