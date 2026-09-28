"""Constrained, stateless JSON generation through the local Codex CLI."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


class CodexProviderError(RuntimeError):
    pass


def _strict_schema(value: object) -> object:
    """Use the supported structured-output subset; downstream gates enforce lengths."""
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _strict_schema(item) for key, item in value.items()
              if key not in ('minItems', 'maxItems')}
    if result.get('type') == 'object':
        properties = result.get('properties', {})
        result['additionalProperties'] = False
        result['required'] = list(properties)
    return result


def generate(command: tuple[str, ...], model: str, schema: dict, prompt: str, *, timeout: int) -> dict:
    if not command or not model:
        raise CodexProviderError('Codex command or model is missing')
    with tempfile.TemporaryDirectory(prefix='sellemy-codex-') as directory:
        schema_path = Path(directory) / 'schema.json'
        output_path = Path(directory) / 'response.json'
        schema_path.write_text(json.dumps(_strict_schema(schema), ensure_ascii=False), encoding='utf-8')
        args = [*command, 'exec', '--ephemeral', '--ignore-user-config',
                '--sandbox', 'read-only', '--skip-git-repo-check', '--cd', directory,
                '--model', model,
                '--output-schema', str(schema_path),
                '--output-last-message', str(output_path), '-']
        try:
            completed = subprocess.run(args, input=prompt, text=True, capture_output=True,
                                       timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodexProviderError(f'Codex invocation failed: {type(exc).__name__}') from exc
        if completed.returncode:
            raise CodexProviderError(f'Codex exited {completed.returncode}')
        try:
            value = json.loads(output_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexProviderError('Codex returned no valid JSON response') from exc
        if not isinstance(value, dict):
            raise CodexProviderError('Codex response must be an object')
        return value
