from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


class CodexProviderError(RuntimeError):
    pass


BRIEF_FIXED = """# Sellemy Codex Brief

## Purpose
SellemyのPlanning / Writerを、Evidenceと現行Gateに従って高品質かつ省トークンで実行する。

## Fixed rules
- Planning: 既存記事との重複を避け、購入意図・商品成立性・Evidence成立性を優先する。
- Writer: 渡されたEvidence以外を事実根拠にしない。slug/category/refは変更しない。
- 6商品を必ず3グループ×2商品へ一意に分割する。比較軸はtopic固有の軸を使う。
- Amazon listing titleの丸写し、ASIN、内部運用語、固定価格帯構造、根拠のない断定は禁止。
- Writer本文は自然な日本語で、商品ごとの差を具体化する。scriptによる水増しは禁止。
- 後段Review / Machine QA / Publish Gateは緩和しない。情報不足はfail-closed。
- アイキャッチは担当外。production画像はsellemy-ops Chatで生成する。

## Learned
"""


def _strict_schema(value: object) -> object:
    """Normalize objects for strict structured output while preserving array cardinality gates."""
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _strict_schema(item) for key, item in value.items()}
    if result.get('type') == 'object':
        properties = result.get('properties', {})
        result['additionalProperties'] = False
        result['required'] = list(properties)
    return result


def _thread_id(stdout: str) -> str | None:
    def walk(value: object) -> str | None:
        if isinstance(value, dict):
            for key in ('thread_id', 'threadId'):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate
            if value.get('type') in ('thread.started', 'thread_started'):
                candidate = value.get('thread_id') or value.get('id')
                if isinstance(candidate, str) and candidate:
                    return candidate
            for item in value.values():
                found = walk(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return None
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = walk(value)
        if found:
            return found
    return None



def _usage(stdout: str) -> dict:
    latest: dict = {}
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = value.get('usage') if isinstance(value, dict) else None
        if isinstance(usage, dict):
            latest = {key: int(usage.get(key) or 0) for key in (
                'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'output_tokens', 'reasoning_output_tokens',
            )}
            latest['uncached_input_tokens'] = max(0, latest['input_tokens'] - latest['cached_input_tokens'])
    return latest


def _append_usage(root: Path, surface_key: str, *, stage: str, model: str, session_id: str,
                  session_turn: int, usage: dict) -> None:
    if not usage:
        return
    directory = root / '.runtime' / 'sellemy-codex'
    directory.mkdir(parents=True, exist_ok=True)
    row = {
        'surface': surface_key, 'stage': stage, 'model': model,
        'session_id': session_id, 'session_turn': session_turn, **usage,
    }
    with (directory / 'usage.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')

def _runtime_paths(root: Path, surface_key: str) -> tuple[Path, Path]:
    safe = ''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in surface_key)
    directory = root / '.runtime' / 'sellemy-codex'
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f'{safe}.json', directory / 'brief.md'


def _ensure_brief(path: Path) -> None:
    if not path.exists():
        path.write_text(BRIEF_FIXED + '- まだ蓄積知見なし。\n', encoding='utf-8')


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(path: Path, value: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(path)


def _run(command: list[str], *, prompt: str, timeout: int, cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, input=prompt, text=True, capture_output=True,
                              timeout=timeout, check=False, cwd=str(cwd))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexProviderError(f'Codex invocation failed: {type(exc).__name__}') from exc


def _parse_output(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexProviderError('Codex returned no valid JSON response') from exc
    if not isinstance(value, dict):
        raise CodexProviderError('Codex response must be an object')
    return value


def _refresh_brief(command: tuple[str, ...], model: str, session_id: str, root: Path,
                   brief_path: Path, *, timeout: int) -> dict:
    schema = {
        'type': 'object', 'additionalProperties': False, 'required': ['learned'],
        'properties': {'learned': {'type': 'string'}},
    }
    with tempfile.TemporaryDirectory(prefix='sellemy-codex-brief-') as directory:
        schema_path = Path(directory) / 'schema.json'
        output_path = Path(directory) / 'response.json'
        schema_path.write_text(json.dumps(_strict_schema(schema), ensure_ascii=False), encoding='utf-8')
        prompt = (
            'Before this Sellemy Codex session is rotated, compress only durable useful learnings from this session. '
            'Return JSON {"learned":"..."}. Keep it concise (max about 1200 Japanese characters), deduplicate prior points, '
            'exclude transient product/task details, IDs, timestamps, and anything already covered by Fixed rules. '
            'Prefer concrete writing/planning patterns that improved QA pass rate, factuality, distinctness, or token efficiency. '
            'If there is no durable new learning, preserve the current Learned section meaning without adding filler.\n\n'
            'CURRENT_BRIEF:\n' + brief_path.read_text(encoding='utf-8')
        )
        args = [*command, 'exec', 'resume', '--ignore-user-config', '--ignore-rules', '--skip-git-repo-check',
                '--model', model, '--output-schema', str(schema_path),
                '--output-last-message', str(output_path), '--json', session_id, '-']
        completed = _run(args, prompt=prompt, timeout=timeout, cwd=root)
        if completed.returncode:
            raise CodexProviderError(f'Codex brief refresh exited {completed.returncode}')
        learned = _parse_output(output_path).get('learned')
        if not isinstance(learned, str):
            raise CodexProviderError('Codex brief refresh returned no learned text')
        learned = learned.strip()
        if len(learned) > 1200:
            raise CodexProviderError('Codex brief refresh exceeded compactness limit')
        brief_path.write_text(BRIEF_FIXED + (learned or '- 追加の恒久知見なし。') + '\n', encoding='utf-8')
        return _usage(completed.stdout)


def generate_persistent(command: tuple[str, ...], model: str, schema: dict, prompt: str, *,
                        timeout: int, root: Path, surface_key: str = 'bu-codex-sellemy',
                        max_turns: int | None = None, stage: str = '') -> tuple[dict, dict]:
    """Generate structured output on a durable Codex session with compact cross-session memory.

    The session is rotated after max_turns. Immediately before rotation, the old session
    compresses durable learnings into .runtime/sellemy-codex/brief.md. New sessions are
    explicitly instructed to read that file before the task.
    """
    if not command or not model:
        raise CodexProviderError('Codex command or model is missing')
    root = Path(root).resolve()
    state_path, brief_path = _runtime_paths(root, surface_key)
    _ensure_brief(brief_path)
    state = _read_state(state_path)
    max_turns = max_turns or int(os.environ.get('SELLEMY_CODEX_SESSION_MAX_TURNS', '20'))
    session_id = state.get('session_id') if isinstance(state.get('session_id'), str) else None
    turns = int(state.get('turns') or 0)
    rotated = False
    brief_refreshed = False

    if session_id and turns >= max_turns:
        brief_usage = _refresh_brief(command, model, session_id, root, brief_path, timeout=timeout)
        _append_usage(root, surface_key, stage='brief_refresh', model=model, session_id=session_id,
                      session_turn=turns + 1, usage=brief_usage)
        brief_refreshed = True
        session_id = None
        turns = 0
        rotated = True

    with tempfile.TemporaryDirectory(prefix='sellemy-codex-') as directory:
        schema_path = Path(directory) / 'schema.json'
        output_path = Path(directory) / 'response.json'
        schema_path.write_text(json.dumps(_strict_schema(schema), ensure_ascii=False), encoding='utf-8')
        task_prompt = (
            'This is the fixed BU-002 Sellemy Codex surface. Before executing the task, read '
            f'{brief_path.relative_to(root)}. Treat Fixed rules as mandatory and Learned as compact operational guidance. '
            'Do not edit the brief during normal task turns.\n\n' + prompt
        )
        if session_id:
            args = [*command, 'exec', 'resume', '--ignore-user-config', '--ignore-rules', '--skip-git-repo-check',
                    '--model', model, '--output-schema', str(schema_path),
                    '--output-last-message', str(output_path), '--json', session_id, '-']
        else:
            args = [*command, 'exec', '--ignore-user-config', '--ignore-rules', '--sandbox', 'read-only',
                    '--skip-git-repo-check', '--cd', str(root), '--model', model,
                    '--output-schema', str(schema_path), '--output-last-message', str(output_path),
                    '--json', '-']
        completed = _run(args, prompt=task_prompt, timeout=timeout, cwd=root)
        if completed.returncode:
            diagnostic = (completed.stderr or completed.stdout or '').strip()[-1000:]
            raise CodexProviderError(f'Codex exited {completed.returncode}: {diagnostic}')
        value = _parse_output(output_path)
        discovered = _thread_id(completed.stdout)
        if session_id is None:
            if not discovered:
                raise CodexProviderError('Codex persistent session id was not observable')
            session_id = discovered
        turns += 1
        usage = _usage(completed.stdout)
        _append_usage(root, surface_key, stage=stage or 'generation', model=model, session_id=session_id,
                      session_turn=turns, usage=usage)
        _write_state(state_path, {
            'surface_key': surface_key, 'session_id': session_id, 'turns': turns,
            'model': model, 'brief_path': str(brief_path),
        })
        return value, {
            'surface': surface_key, 'session_id': session_id, 'session_turn': turns,
            'session_rotated': rotated, 'brief_refreshed_before_rotation': brief_refreshed,
            'brief_path': str(brief_path), **usage,
        }


def generate(command: tuple[str, ...], model: str, schema: dict, prompt: str, *, timeout: int) -> dict:
    """Legacy stateless helper retained for tests/other callers."""
    if not command or not model:
        raise CodexProviderError('Codex command or model is missing')
    with tempfile.TemporaryDirectory(prefix='sellemy-codex-') as directory:
        schema_path = Path(directory) / 'schema.json'
        output_path = Path(directory) / 'response.json'
        schema_path.write_text(json.dumps(_strict_schema(schema), ensure_ascii=False), encoding='utf-8')
        args = [*command, 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules',
                '--sandbox', 'read-only', '--skip-git-repo-check', '--cd', directory,
                '--model', model, '--output-schema', str(schema_path),
                '--output-last-message', str(output_path), '-']
        completed = _run(args, prompt=prompt, timeout=timeout, cwd=Path(directory))
        if completed.returncode:
            raise CodexProviderError(f'Codex exited {completed.returncode}')
        return _parse_output(output_path)
