from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from codex_provider import CodexProviderError, generate_persistent


class UnitAIRouterError(RuntimeError):
    pass


class UnitAIRouter:
    """Unit-scoped model router. Codex is active now; API is an explicit future gate."""

    def __init__(self, *, root: Path, config_path: Path):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text(encoding='utf-8'))

    def stage(self, stage: str) -> dict[str, Any]:
        try:
            row = dict(self.config['stages'][stage])
        except (KeyError, TypeError) as exc:
            raise UnitAIRouterError(f'unknown AI stage: {stage}') from exc
        provider = str(row.get('provider') or '').strip()
        if provider not in {'codex', 'openai_api'}:
            raise UnitAIRouterError(f'unsupported provider: {provider}')
        return row

    def execute(self, stage: str, *, schema: dict[str, Any], prompt: str, usage_key: str = '') -> tuple[dict, dict]:
        row = self.stage(stage)
        provider = row['provider']
        if provider == 'openai_api':
            api = self.config.get('api') or {}
            if not api.get('enabled'):
                raise UnitAIRouterError('OpenAI API route is not enabled for this unit yet')
            raise UnitAIRouterError('OpenAI API adapter is intentionally not provisioned for this unit yet')

        surface = str(row.get('codex_surface') or '').strip()
        if not surface:
            raise UnitAIRouterError(f'Codex surface missing for stage: {stage}')
        model = str(os.environ.get(str(row.get('model_env') or ''), '') or row.get('model') or 'gpt-6-astra').strip()
        command = tuple(str(os.environ.get('SELLEMY_CODEX_COMMAND', '/opt/homebrew/bin/codex')).split())
        try:
            value, meta = generate_persistent(
                command, model, schema, prompt,
                timeout=int(row.get('timeout_seconds') or 300),
                root=self.root, surface_key=surface, stage=stage, usage_key=usage_key,
            )
        except CodexProviderError as exc:
            raise UnitAIRouterError(str(exc)) from exc
        return value, {'provider': 'codex', 'surface': surface, **meta}
