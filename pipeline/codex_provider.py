from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


class CodexProviderError(RuntimeError):
    pass



MEMBER_CANON_DB = Path(os.environ.get(
    'SELLEMY_MEMBER_CANON_DB',
    str(Path.home() / 'Library/Application Support/AIManagementOS/canonical/ai_management_os.db'),
))


def _member_binding(role_id: str, *, db_path: Path | None = None) -> dict:
    path = Path(db_path or MEMBER_CANON_DB).expanduser().resolve()
    if not path.is_file():
        raise CodexProviderError(f'Member canon unavailable: {path}')
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT role_id,surface_type,project_id,execution_account,create_status,current_url,active,binding_revision '
            'FROM members WHERE role_id=?', (role_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise CodexProviderError(f'Member canon read failed: {exc}') from exc
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass
    if row is None:
        raise CodexProviderError(f'Codex Member not found: {role_id}')
    value = dict(row)
    if (value['surface_type'] != 'Codex' or value['project_id'] != 'BU-002'
            or value['create_status'] != 'Created' or int(value['active'] or 0) != 1):
        raise CodexProviderError(f'Codex Member is not production-ready: {role_id}')
    if value['execution_account'] != 'chatgpt-pro':
        raise CodexProviderError(f'Unexpected Codex execution account: {role_id}')
    try:
        thread_id = str(uuid.UUID(value['current_url']))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CodexProviderError(f'Codex Member has no canonical thread binding: {role_id}') from exc
    if thread_id != value['current_url']:
        raise CodexProviderError(f'Codex Member thread is not canonical UUID: {role_id}')
    return value

BRIEF_FIXED = """# Sellemy Codex Brief

## Purpose
SellemyのPlanning / Writer / Designerを、Evidenceと現行Gateに従って高品質かつ省トークンで実行する。

## Fixed rules
- Planning: 既存記事との重複を避け、購入意図・商品成立性・Evidence成立性を優先する。
- Writer: 渡されたEvidence以外を事実根拠にしない。slug/category/refは変更しない。
- 6商品を必ず3グループ×2商品へ一意に分割する。比較軸はtopic固有の軸を使う。
- Amazon listing titleの丸写し、ASIN、内部運用語、固定価格帯構造、根拠のない断定は禁止。
- Writer本文は自然な日本語で、商品ごとの差を具体化する。scriptによる水増しは禁止。
- 後段Review / Machine QA / Publish Gateは緩和しない。情報不足はfail-closed。
- Planning / Writerはアイキャッチ生成を担当しない。production画像はbu-codex-sellemy-designerがCodex CLI上のbuilt-in image_genで生成する。

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
                'output_tokens', 'reasoning_output_tokens', 'total_tokens',
            )}
            latest['uncached_input_tokens'] = max(0, latest['input_tokens'] - latest['cached_input_tokens'])
    return latest


def _append_usage(root: Path, surface_key: str, *, stage: str, model: str, session_id: str,
                  session_turn: int, usage: dict, operation_key: str = '') -> None:
    if not usage:
        return
    directory = root / '.runtime' / 'sellemy-codex'
    directory.mkdir(parents=True, exist_ok=True)
    row = {
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'surface': surface_key, 'stage': stage, 'operation_key': operation_key or None, 'model': model,
        'session_id': session_id, 'session_turn': session_turn,
        'usage_source': 'codex_rollout.turn_token_usage', **usage,
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


def _session_file(thread_id: str) -> Path | None:
    root = Path.home() / '.codex' / 'sessions'
    files = list(root.glob(f'**/*{thread_id}*.jsonl'))
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def _turn_usage(payload: dict) -> dict:
    raw = payload.get('turn_token_usage') if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    value = {key: int(raw.get(key) or 0) for key in (
        'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
        'output_tokens', 'reasoning_output_tokens', 'total_tokens',
    )}
    value['uncached_input_tokens'] = max(0, value['input_tokens'] - value['cached_input_tokens'])
    return value


def _collect_queued_turn(thread_id: str, marker: str, *, timeout: int) -> tuple[str, dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = _session_file(thread_id)
        if path and path.is_file():
            active = False
            usage: dict = {}
            try:
                lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = row.get('payload') if isinstance(row, dict) else None
                payload = payload if isinstance(payload, dict) else {}
                if row.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
                    text = ''.join(item.get('text', '') for item in payload.get('content', []) if isinstance(item, dict))
                    active = marker in text
                    usage = {} if active else usage
                elif active and row.get('type') == 'token_usage_record':
                    current = _turn_usage(payload)
                    if current:
                        usage = current
                elif active and row.get('type') == 'event_msg' and payload.get('type') == 'task_complete':
                    final = payload.get('last_agent_message')
                    if not isinstance(final, str) or not final.strip():
                        raise CodexProviderError('queued Codex turn completed without final message')
                    return final.strip(), usage
        time.sleep(0.5)
    raise CodexProviderError(f'queued Codex turn timed out after {timeout}s')


def _queue_turn(command: tuple[str, ...], model: str, thread_id: str, root: Path, prompt: str, *, timeout: int, effort: str) -> tuple[str, dict]:
    marker = f'[SELLEMY-RUNTIME-{uuid.uuid4()}]'
    message = marker + '\n' + prompt
    args = [*command, 'queue', '--remote', 'unix://', '--thread', thread_id, '--message', message,
            '--model', model, '--config', f'model_reasoning_effort={effort}', '--cd', str(root)]
    try:
        completed = subprocess.run(args, text=True, capture_output=True, timeout=30, check=False, cwd=str(root))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexProviderError(f'Codex queue invocation failed: {type(exc).__name__}') from exc
    if completed.returncode:
        diagnostic = (completed.stderr or completed.stdout or '').strip()[-1000:]
        raise CodexProviderError(f'Codex queue failed: {diagnostic or completed.returncode}')
    return _collect_queued_turn(thread_id, marker, timeout=timeout)


def _json_object(text: str) -> dict:
    raw = text.strip()
    if raw.startswith('```') and raw.endswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodexProviderError('Codex final response was not a JSON object') from exc
    if not isinstance(value, dict):
        raise CodexProviderError('Codex response must be an object')
    return value


def _validate_required_shape(value: object, schema: object, path: str = '$') -> None:
    if not isinstance(schema, dict):
        return
    expected = schema.get('type')
    if expected == 'object':
        if not isinstance(value, dict):
            raise CodexProviderError(f'Codex schema mismatch at {path}: object required')
        for key in schema.get('required', []):
            if key not in value:
                raise CodexProviderError(f'Codex schema mismatch at {path}: missing {key}')
        properties = schema.get('properties', {})
        for key, child in properties.items():
            if key in value:
                _validate_required_shape(value[key], child, path + '.' + key)
    elif expected == 'array':
        if not isinstance(value, list):
            raise CodexProviderError(f'Codex schema mismatch at {path}: array required')
        if isinstance(schema.get('minItems'), int) and len(value) < schema['minItems']:
            raise CodexProviderError(f'Codex schema mismatch at {path}: too few items')
        if isinstance(schema.get('maxItems'), int) and len(value) > schema['maxItems']:
            raise CodexProviderError(f'Codex schema mismatch at {path}: too many items')
        child = schema.get('items')
        for index, item in enumerate(value):
            _validate_required_shape(item, child, f'{path}[{index}]')
    elif expected == 'string' and not isinstance(value, str):
        raise CodexProviderError(f'Codex schema mismatch at {path}: string required')
    elif expected == 'boolean' and not isinstance(value, bool):
        raise CodexProviderError(f'Codex schema mismatch at {path}: boolean required')
    elif expected == 'integer' and (not isinstance(value, int) or isinstance(value, bool)):
        raise CodexProviderError(f'Codex schema mismatch at {path}: integer required')
    elif expected == 'number' and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise CodexProviderError(f'Codex schema mismatch at {path}: number required')


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
        effort = os.environ.get('SELLEMY_CODEX_REASONING_EFFORT', 'low').strip() or 'low'
        queue_prompt = prompt + '\n\nReturn one raw JSON object only, no Markdown. OUTPUT_SCHEMA:\n' + json.dumps(_strict_schema(schema), ensure_ascii=False)
        final, usage = _queue_turn(command, model, session_id, root, queue_prompt, timeout=timeout, effort=effort)
        value = _json_object(final)
        _validate_required_shape(value, _strict_schema(schema))
        learned = value.get('learned')
        if not isinstance(learned, str):
            raise CodexProviderError('Codex brief refresh returned no learned text')
        learned = learned.strip()
        if len(learned) > 1200:
            raise CodexProviderError('Codex brief refresh exceeded compactness limit')
        brief_path.write_text(BRIEF_FIXED + (learned or '- 追加の恒久知見なし。') + '\n', encoding='utf-8')
        return usage


def generate_persistent(command: tuple[str, ...], model: str, schema: dict, prompt: str, *,
                        timeout: int, root: Path, surface_key: str = 'bu-codex-sellemy',
                        max_turns: int | None = None, stage: str = '', usage_key: str = '') -> tuple[dict, dict]:
    """Generate on the Codex thread currently bound to a canonical BU-002 Member.

    Member canon is authoritative for thread identity. Local state only remembers the
    previous binding and turn count so that a changed Member binding can harvest the
    outgoing thread into the compact brief before the new thread does any work.
    """
    if not command or not model:
        raise CodexProviderError('Codex command or model is missing')
    root = Path(root).resolve()
    state_path, brief_path = _runtime_paths(root, surface_key)
    _ensure_brief(brief_path)
    binding = _member_binding(surface_key)
    session_id = binding['current_url']
    state = _read_state(state_path)
    previous_session = state.get('session_id') if isinstance(state.get('session_id'), str) else None
    turns = int(state.get('turns') or 0) if previous_session == session_id else 0
    binding_changed = bool(previous_session and previous_session != session_id)
    brief_refreshed = False

    if binding_changed:
        brief_usage = _refresh_brief(command, model, previous_session, root, brief_path, timeout=timeout)
        _append_usage(root, surface_key, stage='brief_refresh', model=model, session_id=previous_session,
                      session_turn=int(state.get('turns') or 0) + 1, usage=brief_usage)
        brief_refreshed = True

    checkpoint = max_turns or int(os.environ.get('SELLEMY_CODEX_BRIEF_CHECKPOINT_TURNS', '10'))
    if not binding_changed and previous_session == session_id and turns >= checkpoint:
        brief_usage = _refresh_brief(command, model, session_id, root, brief_path, timeout=timeout)
        _append_usage(root, surface_key, stage='brief_refresh', model=model, session_id=session_id,
                      session_turn=turns + 1, usage=brief_usage)
        brief_refreshed = True
        turns = 0

    canon_bridge = Path(__file__).resolve().parents[1] / 'config' / 'sellemy-codex-canon-bootstrap.md'
    if not canon_bridge.is_file():
        raise CodexProviderError('Sellemy Codex canon bootstrap is missing')
    strict_schema = _strict_schema(schema)
    task_prompt = (
        'You are executing as the canonical BU-002 Codex Member ' + surface_key + '. '
        'Before executing the task, read ' + str(canon_bridge) + ' and ' + str(brief_path.relative_to(root)) + '. '
        'The canon bootstrap is a non-canonical runtime bridge; its Authority order tells you what is authoritative. '
        'Treat Fixed rules in the brief as mandatory and Learned as compact operational guidance. '
        'Do not edit either file during normal task turns.\n\n' + prompt +
        '\n\nYour final response MUST be one raw JSON object only, with no Markdown or commentary. OUTPUT_SCHEMA:\n' +
        json.dumps(strict_schema, ensure_ascii=False)
    )
    effort = os.environ.get('SELLEMY_CODEX_REASONING_EFFORT', 'low').strip() or 'low'
    final, usage = _queue_turn(command, model, session_id, root, task_prompt, timeout=timeout, effort=effort)
    value = _json_object(final)
    _validate_required_shape(value, strict_schema)
    turns += 1
    _append_usage(root, surface_key, stage=stage or 'generation', model=model, session_id=session_id,
                  session_turn=turns, usage=usage, operation_key=usage_key)
    _write_state(state_path, {
        'surface_key': surface_key, 'session_id': session_id, 'turns': turns,
        'model': model, 'brief_path': str(brief_path),
        'member_binding_revision': binding['binding_revision'],
    })
    return value, {
        'surface': surface_key, 'session_id': session_id, 'session_turn': turns,
        'member_binding_revision': binding['binding_revision'],
        'member_binding_changed': binding_changed, 'reasoning_effort': effort,
        'brief_refreshed': brief_refreshed, 'brief_path': str(brief_path), **usage,
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
