from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / 'config' / 'recovery-provider-policy.json'


class RecoveryProviderPolicyError(RuntimeError):
    pass


def load_policy() -> dict:
    try:
        value = json.loads(POLICY_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RecoveryProviderPolicyError('recovery provider policy unavailable') from exc
    if not isinstance(value, dict):
        raise RecoveryProviderPolicyError('recovery provider policy invalid')
    return value


def active_policy() -> dict | None:
    value = load_policy()
    return value if value.get('active') is True else None


def require_local_provider_allowed(kind: str) -> None:
    policy = active_policy()
    if policy is None:
        return
    key = {'codex': 'codex_allowed', 'claude': 'claude_allowed', 'ollama': 'local_ollama_allowed'}.get(kind)
    if key and policy.get(key) is not True:
        raise RecoveryProviderPolicyError(f'{kind} is prohibited by active recovery provider policy')


def require_responses_allowed() -> dict:
    policy = active_policy()
    if policy is not None and policy.get('responses_api_allowed') is not True:
        raise RecoveryProviderPolicyError('Responses API is prohibited by active recovery provider policy')
    return policy or {}
