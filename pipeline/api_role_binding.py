"""Read-only bridge from Portal Sellemy API row configuration to Codex runtime.

The live policy is Codex 100% / API 0%.  Configuration may not silently
activate API calls; any nonzero API weight fails closed until an API adapter
and explicit deployment are approved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DRAFTS = Path.home() / 'Library/Application Support/AIManagementOS/api-role-drafts.json'
STAGE_ROWS = {
    ('bu-codex-sellemy-planning', 'planning'): ('sellemy-planning',),
    ('bu-codex-sellemy-writer', 'writer'): ('sellemy-writer',),
    ('bu-codex-sellemy-reviewer', 'independent_review'): ('sellemy-reviewer',),
    ('bu-codex-sellemy-designer', 'eyecatch'): ('sellemy-eyecatch', 'sellemy-image-generation'),
}

class SellemyRouteConfigError(RuntimeError):
    pass


def require_codex_route(surface: str, stage: str, *, path: Path | None = None) -> tuple[str, ...]:
    keys = STAGE_ROWS.get((surface, stage), ())
    if not keys:
        return ()
    p = path or Path(os.environ.get('SELLEMY_API_ROLE_DRAFTS_PATH', str(DRAFTS)))
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SellemyRouteConfigError('Sellemy API catalogue unavailable') from exc
    for key in keys:
        row = data.get(key)
        if not isinstance(row, dict) or row.get('unit') not in {'BU-002', 'BU002：Sellemy'}:
            raise SellemyRouteConfigError(f'Sellemy route row invalid: {key}')
        if type(row.get('api_percent')) is not int or row['api_percent'] != 0:
            raise SellemyRouteConfigError(f'Sellemy route requires explicit API cutover: {key}')
        if key == 'sellemy-image-generation' and row.get('binding') != 'image-generation':
            raise SellemyRouteConfigError('Sellemy image generation binding missing')
        if key == 'sellemy-eyecatch' and row.get('binding') != 'eyecatch':
            raise SellemyRouteConfigError('Sellemy eyecatch briefing binding missing')
        # Portal role is a user-editable label, not the canonical Member Role ID.
    return keys


def require_codex_settings(surface: str, stage: str, *, model: str | None = None,
                           path: Path | None = None) -> tuple[str, str]:
    # Read the saved Portal execution settings; never infer defaults.
    keys = require_codex_route(surface, stage, path=path)
    if not keys:
        raise SellemyRouteConfigError(f'unknown Sellemy Codex execution binding: {surface}/{stage}')
    p = path or Path(os.environ.get('SELLEMY_API_ROLE_DRAFTS_PATH', str(DRAFTS)))
    try:
        rows = json.loads(p.read_text(encoding='utf-8'))
        row = rows[keys[0]]
        configured_model = row['codex_model']
        effort = row['codex_effort']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SellemyRouteConfigError('Sellemy Codex model/effort settings unavailable') from exc
    if not isinstance(configured_model, str) or not configured_model.strip():
        raise SellemyRouteConfigError(f'Sellemy Codex model unset: {keys[0]}')
    if effort not in {'low', 'medium', 'high', 'xhigh'}:
        raise SellemyRouteConfigError(f'Sellemy Codex effort unset or invalid: {keys[0]}')
    if model is not None and model != configured_model:
        raise SellemyRouteConfigError(f'Sellemy Codex model mismatch: {keys[0]}')
    return configured_model, effort
