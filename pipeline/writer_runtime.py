from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path


class WriterInvocationError(RuntimeError):
    """The external Writer runtime did not return a usable structured payload."""


WRITER_JSON_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': [
        'slug', 'category', 'h1', 'lead', 'summary', 'comparison_angles',
        'products', 'how_to_choose', 'conclusion',
    ],
    'properties': {
        'slug': {'type': 'string'},
        'category': {'type': 'string'},
        'h1': {'type': 'string'},
        'lead': {'type': 'string'},
        'summary': {'type': 'string'},
        'comparison_angles': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['lowrange', 'midrange', 'highrange'],
            'properties': {
                'lowrange': {'type': 'string'},
                'midrange': {'type': 'string'},
                'highrange': {'type': 'string'},
            },
        },
        'products': {
            'type': 'array',
            'minItems': 6,
            'maxItems': 6,
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['ref', 'h3', 'description'],
                'properties': {
                    'ref': {'type': 'string'},
                    'h3': {'type': 'string'},
                    'description': {'type': 'string'},
                },
            },
        },
        'how_to_choose': {'type': 'string'},
        'conclusion': {'type': 'string'},
    },
}


def _command() -> list[str]:
    configured = os.environ.get('SELLEMY_WRITER_COMMAND', '').strip()
    if configured:
        command = shlex.split(configured)
    else:
        executable = next(
            (
                str(path)
                for path in (
                    Path('/opt/homebrew/bin/claude'),
                    Path('/usr/local/bin/claude'),
                )
                if path.is_file()
            ),
            shutil.which('claude'),
        )
        command = [executable] if executable else []
    if not command or not command[0]:
        raise WriterInvocationError(
            'Writer runtime unavailable: install Claude CLI or set SELLEMY_WRITER_COMMAND'
        )
    return command


def _prompt(
    topic: dict,
    evidence: dict,
    *,
    previous_payload: dict | None = None,
    gate_feedback: dict | None = None,
) -> str:
    prompt = (
        'You are the Writer stage for a Japanese product-comparison publication. '
        'Return only the requested structured JSON. Write all public-facing Japanese prose yourself; '
        'do not ask scripts to expand, paraphrase, or pad it. Ground every product claim only in the '
        'provided Amazon evidence. Do not state prices, internal workflow terms, verification claims, '
        'ASINs, or affiliate operations in public prose. Do not copy raw listing titles verbatim. '
        'Keep all six descriptions genuinely distinct, with no repeated sentences or formulaic shared '
        'openings/endings. Required Japanese character targets: lead 180-230, summary 130-170, '
        'how_to_choose 190-270, each product description 320-400, h3 6-45. The complete rendered main '
        'text must land between 3000 and 3400 characters, so write comparison angles and conclusion at '
        'useful editorial length. Preserve slug, category, and product refs exactly. Cover each product\'s '
        'specific use case, tradeoffs, and evidence-supported specifications.\n\n'
        f'TOPIC:\n{json.dumps(topic, ensure_ascii=False, sort_keys=True)}\n\n'
        f'EVIDENCE:\n{json.dumps(evidence, ensure_ascii=False, sort_keys=True)}'
    )
    if previous_payload is not None:
        prompt += (
            '\n\nYour previous payload failed mandatory gates. Rewrite the complete payload; '
            'do not patch it with scripted filler or return commentary. Preserve evidence-grounded '
            'facts and all identifiers while fixing every reported issue.\n\n'
            f'PREVIOUS_PAYLOAD:\n{json.dumps(previous_payload, ensure_ascii=False, sort_keys=True)}\n\n'
            f'GATE_FEEDBACK:\n{json.dumps(gate_feedback or {}, ensure_ascii=False, sort_keys=True)}'
        )
    return prompt


def invoke_writer(
    topic: dict,
    evidence: dict,
    *,
    previous_payload: dict | None = None,
    gate_feedback: dict | None = None,
) -> tuple[dict, dict]:
    command = _command()
    model = os.environ.get('SELLEMY_WRITER_MODEL', 'sonnet')
    budget = os.environ.get('SELLEMY_WRITER_MAX_BUDGET_USD', '1.00')
    timeout = int(os.environ.get('SELLEMY_WRITER_TIMEOUT_SECONDS', '600'))
    args = command + [
        '-p', '--safe-mode', '--no-session-persistence', '--tools', '',
        '--model', model, '--max-budget-usd', budget,
        '--output-format', 'json', '--json-schema', json.dumps(WRITER_JSON_SCHEMA),
    ]
    try:
        completed = subprocess.run(
            args,
            input=_prompt(
                topic,
                evidence,
                previous_payload=previous_payload,
                gate_feedback=gate_feedback,
            ),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WriterInvocationError(f'Writer invocation failed: {exc}') from exc
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout).strip()[-1000:]
        raise WriterInvocationError(
            f'Writer runtime exited {completed.returncode}: {diagnostic or "no diagnostic"}'
        )
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WriterInvocationError('Writer runtime returned non-JSON output') from exc
    payload = envelope.get('structured_output')
    if not isinstance(payload, dict):
        result = envelope.get('result')
        try:
            payload = json.loads(result) if isinstance(result, str) else None
        except json.JSONDecodeError:
            payload = None
    if not isinstance(payload, dict):
        raise WriterInvocationError('Writer runtime returned no structured_output object')
    metadata = {
        'runtime': command[0],
        'model': envelope.get('modelUsage') or model,
        'cost_usd': envelope.get('total_cost_usd'),
        'duration_api_ms': envelope.get('duration_api_ms'),
        'session_persisted': False,
    }
    return payload, metadata
