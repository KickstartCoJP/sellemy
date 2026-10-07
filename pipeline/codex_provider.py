from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
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

DESIGNER_SURFACE = 'bu-codex-sellemy-designer'
PLANNING_SURFACE = 'bu-codex-sellemy-planning'
CONTEXT_ROTATION_DEFAULTS = {
    PLANNING_SURFACE: {'enabled': True, 'input_threshold': 120000, 'streak': 3},
    'bu-codex-sellemy-writer': {'enabled': True, 'input_threshold': 250000, 'streak': 3},
    DESIGNER_SURFACE: {'enabled': True, 'input_threshold': 250000, 'streak': 3},
}

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
        if not isinstance(usage, dict):
            continue
        latest = {}
        for key in ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                    'output_tokens', 'reasoning_output_tokens'):
            raw = usage.get(key)
            latest[key] = int(raw) if raw is not None else None
        if latest['input_tokens'] is not None and latest['cached_input_tokens'] is not None:
            latest['uncached_input_tokens'] = max(0, latest['input_tokens'] - latest['cached_input_tokens'])
        else:
            latest['uncached_input_tokens'] = None
        raw_total = usage.get('total_tokens')
        if raw_total is not None:
            latest['total_tokens'] = int(raw_total)
        elif latest['input_tokens'] is not None and latest['output_tokens'] is not None:
            latest['total_tokens'] = latest['input_tokens'] + latest['output_tokens']
        else:
            latest['total_tokens'] = None
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
    brief_name = 'designer-brief.md' if surface_key == DESIGNER_SURFACE else 'brief.md'
    return directory / f'{safe}.json', directory / brief_name


def _context_control_path(root: Path) -> Path:
    return root / '.runtime' / 'sellemy-codex' / 'context-controller.json'


def _context_policy(surface_key: str) -> dict:
    base = dict(CONTEXT_ROTATION_DEFAULTS.get(surface_key) or {
        'enabled': False, 'input_threshold': 140000, 'streak': 3,
    })
    enabled = {
        item.strip() for item in os.environ.get(
            'SELLEMY_CODEX_CONTEXT_ROTATION_SURFACES',
            ','.join(CONTEXT_ROTATION_DEFAULTS)
        ).split(',') if item.strip()
    }
    base['enabled'] = surface_key in enabled
    threshold_key = 'SELLEMY_CODEX_CONTEXT_THRESHOLD_' + re.sub(r'[^A-Za-z0-9]', '_', surface_key).upper()
    if os.environ.get(threshold_key):
        base['input_threshold'] = int(os.environ[threshold_key])
    if os.environ.get('SELLEMY_CODEX_CONTEXT_STREAK'):
        base['streak'] = int(os.environ['SELLEMY_CODEX_CONTEXT_STREAK'])
    return base


def _read_context_control(root: Path) -> dict:
    return _read_state(_context_control_path(root))


def _write_context_control(root: Path, value: dict) -> None:
    path = _context_control_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_state(path, value)


def _observe_context_usage(root: Path, surface_key: str, session_id: str, usage: dict) -> dict:
    control = _read_context_control(root)
    rows = control.setdefault('surfaces', {})
    row = dict(rows.get(surface_key) or {})
    policy = _context_policy(surface_key)
    input_tokens = int(usage.get('input_tokens') or 0)
    same_session = row.get('session_id') == session_id
    streak = int(row.get('oversize_streak') or 0) if same_session else 0
    streak = streak + 1 if input_tokens >= int(policy['input_threshold']) else 0
    row.update({
        'session_id': session_id,
        'last_input_tokens': input_tokens,
        'last_cached_input_tokens': int(usage.get('cached_input_tokens') or 0),
        'last_uncached_input_tokens': int(usage.get('uncached_input_tokens') or 0),
        'oversize_streak': streak,
        'rotation_due': bool(policy['enabled'] and streak >= int(policy['streak'])),
        'policy': policy,
        'observed_at': datetime.now(timezone.utc).isoformat(),
    })
    rows[surface_key] = row
    _write_context_control(root, control)
    return row


def _context_rotation_due(root: Path, surface_key: str, session_id: str) -> bool:
    row = (_read_context_control(root).get('surfaces') or {}).get(surface_key) or {}
    return bool(
        row.get('rotation_due')
        and row.get('session_id') == session_id
        and _context_policy(surface_key).get('enabled')
    )


def _archive_context(root: Path, surface_key: str, session_id: str, binding: dict,
                     state_path: Path, brief_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    archive = root / '.runtime' / 'sellemy-codex' / 'context-archive' / surface_key / f'{stamp}-{session_id}'
    archive.mkdir(parents=True, exist_ok=False)
    if state_path.exists():
        shutil.copy2(state_path, archive / state_path.name)
    if brief_path.exists():
        shutil.copy2(brief_path, archive / brief_path.name)
    sessions = sorted((Path.home() / '.codex' / 'sessions').glob(f'**/*{session_id}.jsonl'))
    copied = []
    for index, path in enumerate(sessions, 1):
        target = archive / f'session-{index:02d}-{path.name}'
        shutil.copy2(path, target)
        copied.append(str(target))
    metadata = {
        'archived_at': datetime.now(timezone.utc).isoformat(),
        'surface_key': surface_key,
        'old_thread': session_id,
        'binding_revision': binding.get('binding_revision'),
        'session_files': copied,
    }
    (archive / 'metadata.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    return archive


def _start_compacted_thread(command: tuple[str, ...], model: str, root: Path,
                            surface_key: str, brief_path: Path, *, timeout: int) -> tuple[str, dict]:
    canon_bridge = root / 'config' / 'sellemy-codex-canon-bootstrap.md'
    effort = os.environ.get('SELLEMY_CODEX_REASONING_EFFORT', 'low').strip() or 'low'
    prompt = (
        'Initialize a fresh compact Sellemy Codex session for canonical role ' + surface_key + '. '
        'Read only ' + str(canon_bridge) + ' and ' + str(brief_path) + '. '
        'Do not inspect prior session history or unrelated files. Preserve all current quality gates. '
        'Reply exactly READY.'
    )
    args = [*command, 'exec', '--json', '--skip-git-repo-check', '--model', model,
            '--config', f'model_reasoning_effort={effort}', '--cd', str(root), '-']
    try:
        completed = subprocess.run(
            args, input=prompt, text=True, capture_output=True, timeout=timeout,
            check=False, cwd=str(root),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexProviderError(f'Codex compact thread creation failed: {type(exc).__name__}') from exc
    if completed.returncode:
        diagnostic = (completed.stderr or completed.stdout or '').strip()[-1000:]
        raise CodexProviderError(f'Codex compact thread creation failed: {diagnostic or completed.returncode}')
    thread_id = _thread_id(completed.stdout)
    try:
        thread_id = str(uuid.UUID(str(thread_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CodexProviderError('Codex compact thread creation returned no canonical thread UUID') from exc
    return thread_id, _latest_completed_turn_usage(thread_id) or _usage(completed.stdout)


def _rotate_context(command: tuple[str, ...], model: str, root: Path, surface_key: str,
                    binding: dict, state_path: Path, brief_path: Path, *, timeout: int) -> dict:
    if not _context_policy(surface_key).get('enabled'):
        return {}
    lock_path = root / '.runtime' / 'sellemy-codex' / 'context-rotation.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+', encoding='utf-8') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        fresh = _member_binding(surface_key)
        if fresh['current_url'] != binding['current_url']:
            return {'skipped': 'binding_already_changed', 'session_id': fresh['current_url']}
        if not _context_rotation_due(root, surface_key, fresh['current_url']):
            return {}

        brief_usage = _refresh_brief(
            command, model, fresh['current_url'], root, brief_path,
            timeout=timeout, surface_key=surface_key,
        )
        archive = _archive_context(
            root, surface_key, fresh['current_url'], fresh, state_path, brief_path
        )
        new_thread, seed_usage = _start_compacted_thread(
            command, model, root, surface_key, brief_path, timeout=timeout
        )
        helper = Path(os.environ.get(
            'SELLEMY_CODEX_BINDING_ROTATOR',
            str(Path.home() / 'ai-management-os' / 'scripts' / 'rotate_sellemy_codex_binding.py'),
        ))
        if not helper.is_file():
            raise CodexProviderError(f'Codex binding rotator unavailable: {helper}')
        args = [
            str(Path.home() / 'ai-management-os' / '.venv' / 'bin' / 'python'),
            str(helper),
            '--role-id', surface_key,
            '--expected-old-thread', fresh['current_url'],
            '--new-thread', new_thread,
            '--archive-ref', str(archive),
        ]
        completed = subprocess.run(args, text=True, capture_output=True, timeout=30, check=False)
        if completed.returncode:
            diagnostic = (completed.stderr or completed.stdout or '').strip()[-1000:]
            raise CodexProviderError(f'Canonical Codex binding rotation failed: {diagnostic}')
        rotated = _json_object(completed.stdout)
        readback = _member_binding(surface_key)
        if readback['current_url'] != new_thread:
            raise CodexProviderError('Canonical Codex binding rotation read-back mismatch')
        _append_usage(
            root, surface_key, stage='brief_refresh', model=model,
            session_id=fresh['current_url'], session_turn=int((_read_state(state_path).get('turns') or 0)) + 1,
            usage=brief_usage,
        )
        _append_usage(
            root, surface_key, stage='context_seed', model=model,
            session_id=new_thread, session_turn=0, usage=seed_usage,
        )
        control = _read_context_control(root)
        row = dict((control.get('surfaces') or {}).get(surface_key) or {})
        row.update({
            'session_id': new_thread,
            'oversize_streak': 0,
            'rotation_due': False,
            'last_rotation_at': datetime.now(timezone.utc).isoformat(),
            'last_rotation_old_thread': fresh['current_url'],
            'last_rotation_new_thread': new_thread,
            'last_archive': str(archive),
            'probation_remaining': 10,
        })
        control.setdefault('surfaces', {})[surface_key] = row
        _write_context_control(root, control)
        _write_state(state_path, {
            'surface_key': surface_key, 'session_id': new_thread, 'turns': 0,
            'model': model, 'brief_path': str(brief_path),
            'member_binding_revision': readback['binding_revision'],
        })
        return {
            **rotated,
            'archive_path': str(archive),
            'seed_usage': seed_usage,
            'brief_usage': brief_usage,
        }


def _ensure_brief(path: Path, surface_key: str) -> None:
    if path.exists():
        return
    if surface_key == DESIGNER_SURFACE:
        raise CodexProviderError('Designer brief is missing')
    path.write_text(BRIEF_FIXED + '- まだ蓄積知見なし。\n', encoding='utf-8')


def _designer_brief_version(text: str) -> int:
    match = re.search(r'^Version:\s*(\d+)\s*$', text, re.MULTILINE)
    if not match:
        raise CodexProviderError('Designer brief has no Version')
    return int(match.group(1))


def _update_designer_brief(path: Path, learned: str) -> bool:
    current = path.read_text(encoding='utf-8')
    marker = '## Learned\n'
    maintenance = '\n## Brief maintenance\n'
    if marker not in current or maintenance not in current:
        raise CodexProviderError('Designer brief structure is invalid')
    prefix, remainder = current.split(marker, 1)
    current_learned, suffix = remainder.split(maintenance, 1)
    learned = learned.strip() or '- 追加の恒久知見なし。'
    if current_learned.strip() == learned:
        return False
    version = _designer_brief_version(current)
    archive = path.parent / 'archive'
    archive.mkdir(parents=True, exist_ok=True)
    archived = archive / f'designer-brief-v{version:03d}.md'
    if archived.exists():
        if archived.read_bytes() != path.read_bytes():
            raise CodexProviderError(f'Designer brief archive conflict: {archived}')
    else:
        shutil.copy2(path, archived)
    updated = prefix + marker + learned + maintenance + suffix
    updated = re.sub(r'^Version:\s*\d+\s*$', f'Version: {version + 1}', updated, count=1, flags=re.MULTILINE)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(updated, encoding='utf-8')
    temporary.replace(path)
    return True


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


def _latest_completed_turn_usage(thread_id: str) -> dict:
    path = _session_file(thread_id)
    if not path or not path.is_file():
        return {}
    previous = {key: 0 for key in (
        'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
        'output_tokens', 'reasoning_output_tokens', 'total_tokens',
    )}
    active = None
    accumulated = None
    latest = {}
    try:
        lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = row.get('payload') if isinstance(row, dict) else None
        payload = payload if isinstance(payload, dict) else {}
        if row.get('type') == 'event_msg' and payload.get('type') == 'task_started':
            active = payload.get('turn_id')
            accumulated = {key: 0 for key in previous}
        elif row.get('type') == 'event_msg' and payload.get('type') == 'token_count':
            info = payload.get('info') or {}
            raw = info.get('total_token_usage') if isinstance(info, dict) else None
            if not isinstance(raw, dict):
                continue
            current = {key: int(raw.get(key) or 0) for key in previous}
            if active:
                baseline = previous
                if any(current[key] < baseline[key] for key in previous):
                    baseline = {key: 0 for key in previous}
                if accumulated is None:
                    accumulated = {key: 0 for key in previous}
                accumulated = {
                    key: accumulated[key] + current[key] - baseline[key] for key in previous
                }
            previous = current
        elif row.get('type') == 'event_msg' and payload.get('type') in ('task_complete', 'turn_aborted'):
            if active and payload.get('turn_id') == active:
                if payload.get('type') == 'task_complete' and accumulated is not None:
                    latest = dict(accumulated)
                    latest['uncached_input_tokens'] = max(
                        0, latest['input_tokens'] - latest['cached_input_tokens']
                    )
                active = None
                accumulated = None
    return latest


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
                    return final.strip(), _latest_completed_turn_usage(thread_id) or usage
        time.sleep(0.5)
    raise CodexProviderError(f'queued Codex turn timed out after {timeout}s')


def _queue_turn(command: tuple[str, ...], model: str, thread_id: str, root: Path, prompt: str, *, timeout: int, effort: str) -> tuple[str, dict]:
    args = [*command, 'exec', 'resume', '--json', '--skip-git-repo-check',
            '--model', model, '--config', f'model_reasoning_effort={effort}', thread_id, '-']
    try:
        completed = subprocess.run(args, input=prompt, text=True, capture_output=True,
                                   timeout=timeout, check=False, cwd=str(root))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexProviderError(f'Codex resume invocation failed: {type(exc).__name__}') from exc
    if completed.returncode:
        diagnostic = (completed.stderr or completed.stdout or '').strip()[-1000:]
        if 'already has an active writer' in diagnostic:
            marker = f'[SELLEMY-RUNTIME-{uuid.uuid4()}]'
            message = marker + '\n' + prompt
            queue_args = [*command, 'queue', '--remote', 'unix://', '--thread', thread_id, '--message', message,
                          '--model', model, '--config', f'model_reasoning_effort={effort}', '--cd', str(root)]
            try:
                queued = subprocess.run(queue_args, text=True, capture_output=True, timeout=30,
                                        check=False, cwd=str(root))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CodexProviderError(f'Codex queue invocation failed: {type(exc).__name__}') from exc
            if queued.returncode:
                queue_diagnostic = (queued.stderr or queued.stdout or '').strip()[-1000:]
                raise CodexProviderError(f'Codex queue failed: {queue_diagnostic or queued.returncode}')
            return _collect_queued_turn(thread_id, marker, timeout=timeout)
        raise CodexProviderError(f'Codex resume failed: {diagnostic or completed.returncode}')
    final = ''
    for line in completed.stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = row.get('item') if isinstance(row, dict) else None
        if row.get('type') == 'item.completed' and isinstance(item, dict) and item.get('type') == 'agent_message':
            text = item.get('text')
            if isinstance(text, str) and text.strip():
                final = text.strip()
    if not final:
        raise CodexProviderError('Codex resume completed without final agent message')
    return final, _latest_completed_turn_usage(thread_id) or _usage(completed.stdout)


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
                   brief_path: Path, *, timeout: int, surface_key: str) -> dict:
    schema = {
        'type': 'object', 'additionalProperties': False, 'required': ['learned'],
        'properties': {'learned': {'type': 'string'}},
    }
    with tempfile.TemporaryDirectory(prefix='sellemy-codex-brief-') as directory:
        schema_path = Path(directory) / 'schema.json'
        output_path = Path(directory) / 'response.json'
        schema_path.write_text(json.dumps(_strict_schema(schema), ensure_ascii=False), encoding='utf-8')
        guidance = (
            'Prefer reusable visual-generation patterns that improved article specificity, brand fit, composition diversity, '
            'first-pass success, retry reduction, or token efficiency. Exclude canon rules already represented in the brief. '
            if surface_key == DESIGNER_SURFACE else
            'Prefer concrete writing/planning patterns that improved QA pass rate, factuality, distinctness, or token efficiency. '
        )
        prompt = (
            'Before this Sellemy Codex session is rotated, compress only durable useful learnings from this session. '
            'Return JSON {"learned":"..."}. Keep it concise (max about 1200 Japanese characters), deduplicate prior points, '
            'exclude transient product/task details, IDs, timestamps, and fixed canon rules. ' + guidance +
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
        if surface_key == DESIGNER_SURFACE:
            _update_designer_brief(brief_path, learned)
        else:
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
    _ensure_brief(brief_path, surface_key)
    binding = _member_binding(surface_key)
    session_id = binding['current_url']
    state = _read_state(state_path)
    context_rotation = {}
    if _context_rotation_due(root, surface_key, session_id):
        context_rotation = _rotate_context(
            command, model, root, surface_key, binding, state_path, brief_path, timeout=timeout
        )
        binding = _member_binding(surface_key)
        session_id = binding['current_url']
        state = _read_state(state_path)
    previous_session = state.get('session_id') if isinstance(state.get('session_id'), str) else None
    turns = int(state.get('turns') or 0) if previous_session == session_id else 0
    binding_changed = bool(previous_session and previous_session != session_id)
    brief_refreshed = bool(context_rotation)

    if binding_changed:
        brief_usage = _refresh_brief(command, model, previous_session, root, brief_path, timeout=timeout, surface_key=surface_key)
        _append_usage(root, surface_key, stage='brief_refresh', model=model, session_id=previous_session,
                      session_turn=int(state.get('turns') or 0) + 1, usage=brief_usage)
        brief_refreshed = True

    checkpoint = max_turns or int(os.environ.get('SELLEMY_CODEX_BRIEF_CHECKPOINT_TURNS', '10'))
    if not binding_changed and previous_session == session_id and turns >= checkpoint:
        brief_usage = _refresh_brief(command, model, session_id, root, brief_path, timeout=timeout, surface_key=surface_key)
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
        'The canon bootstrap is a non-canonical runtime bridge; its Authority order tells you what is authoritative. ' +
        ('Treat the Designer brief as compact non-canonical operational guidance; current Visual canon and current task input win. '
         if surface_key == DESIGNER_SURFACE else
         'Treat Fixed rules in the brief as mandatory and Learned as compact operational guidance. ') +
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
    context_state = _observe_context_usage(root, surface_key, session_id, usage)
    control = _read_context_control(root)
    current_control = dict((control.get('surfaces') or {}).get(surface_key) or {})
    probation = int(current_control.get('probation_remaining') or 0)
    if probation > 0:
        current_control['probation_remaining'] = probation - 1
        control.setdefault('surfaces', {})[surface_key] = current_control
        _write_context_control(root, control)
        context_state = current_control
    _write_state(state_path, {
        'surface_key': surface_key, 'session_id': session_id, 'turns': turns,
        'model': model, 'brief_path': str(brief_path),
        'member_binding_revision': binding['binding_revision'],
    })
    return value, {
        'surface': surface_key, 'session_id': session_id, 'session_turn': turns,
        'member_binding_revision': binding['binding_revision'],
        'member_binding_changed': binding_changed, 'reasoning_effort': effort,
        'brief_refreshed': brief_refreshed, 'brief_path': str(brief_path),
        'context_rotation': context_rotation, 'context_state': context_state, **usage,
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
