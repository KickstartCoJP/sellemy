from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = Path(os.environ.get(
    'SELLEMY_GROWTH_RECOVERY_ROOT',
    str(Path.home() / 'Library/Application Support/Sellemy/growth-recovery'),
)).expanduser()
JOBS_ROOT = STATE_ROOT / 'jobs'
EVENTS_PATH = STATE_ROOT / 'events.jsonl'
EXECUTION_EVENTS_PATH = STATE_ROOT / 'execution-events.jsonl'

TERMINAL_STATUSES = {'PUBLISHED', 'DISCARDED', 'FAILED'}
RETRYABLE_STAGES = {'WRITER_PENDING', 'QA_FAILED', 'EYECATCH_PENDING', 'PUBLISH_PENDING'}
WRITER_MAX_ATTEMPTS = int(os.environ.get('SELLEMY_WRITER_MAX_ATTEMPTS_PER_SLUG', '5'))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.' + path.name + '.', delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _append_jsonl(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def job_dir(slug: str) -> Path:
    return JOBS_ROOT / slug


def state_path(slug: str) -> Path:
    return job_dir(slug) / 'state.json'


def load_job(slug: str) -> dict:
    path = state_path(slug)
    if not path.is_file():
        raise FileNotFoundError(f'recovery job not found: {slug}')
    return json.loads(path.read_text(encoding='utf-8'))


def _save_state(state: dict, *, event: str, detail: dict | None = None) -> dict:
    state = dict(state)
    state['updated_at'] = _iso()
    slug = state['slug']
    _atomic_json(state_path(slug), state)
    event_row = {
        'at': state['updated_at'], 'slug': slug, 'event': event,
        'status': state.get('status'), 'current_stage': state.get('current_stage'),
        'detail': detail or {},
    }
    _append_jsonl(job_dir(slug) / 'history.jsonl', event_row)
    _append_jsonl(EVENTS_PATH, event_row)
    return state


def write_artifact(slug: str, name: str, value: Any) -> Path:
    path = job_dir(slug) / name
    if isinstance(value, (dict, list)):
        _atomic_json(path, value)
    elif isinstance(value, bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value), encoding='utf-8')
    return path


def read_artifact(slug: str, name: str, default: Any = None) -> Any:
    path = job_dir(slug) / name
    if not path.is_file():
        return default
    if path.suffix == '.json':
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            return default
    return path.read_bytes()


def create_job(*, topic: dict, evidence: dict, selection: dict, planning_provider: dict | None,
               viability_probes: list[dict], source: str = 'runtime') -> dict:
    slug = str(topic['slug'])
    if state_path(slug).is_file():
        return load_job(slug)
    now = _iso()
    state = {
        'schema_version': 1,
        'slug': slug,
        'category': topic['category'],
        'status': 'ACTIVE',
        'current_stage': 'WRITER_PENDING',
        'created_at': now,
        'updated_at': now,
        'next_retry_at': now,
        'retry_count_by_stage': {'writer': 0, 'eyecatch': 0, 'publish': 0},
        'role_returns': {'planning': 0, 'writer': 0, 'designer': 0},
        'runtime_retries': {'planning': 0, 'writer': 0, 'designer': 0, 'publish': 0},
        'last_failure_class': None,
        'last_failure_code': None,
        'last_failure_reason': None,
        'discard_reason': None,
        'published_commit': None,
        'source': source,
        'writer_session_id': None,
        'designer_session_id': None,
        'writer_turn_ids': [],
        'designer_turn_ids': [],
    }
    write_artifact(slug, 'topic.json', topic)
    write_artifact(slug, 'evidence.json', evidence)
    write_artifact(slug, 'selection.json', selection)
    write_artifact(slug, 'planning-provider.json', planning_provider or {})
    write_artifact(slug, 'viability-probes.json', viability_probes)
    return _save_state(state, event='JOB_CREATED', detail={'source': source})


def all_jobs() -> list[dict]:
    if not JOBS_ROOT.is_dir():
        return []
    rows = []
    for path in JOBS_ROOT.glob('*/state.json'):
        try:
            rows.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def eligible_job(*, now: datetime | None = None) -> dict | None:
    current = now or utcnow()
    eligible = []
    for row in all_jobs():
        if row.get('status') in TERMINAL_STATUSES:
            continue
        if row.get('current_stage') not in RETRYABLE_STAGES:
            continue
        raw = row.get('next_retry_at')
        try:
            due = datetime.fromisoformat(raw) if raw else current
        except ValueError:
            due = current
        if due <= current:
            eligible.append(row)
    eligible.sort(key=lambda row: (row.get('created_at') or '', row.get('slug') or ''))
    return eligible[0] if eligible else None


def _backoff(stage: str, attempt: int, failure_class: str) -> timedelta:
    if failure_class == 'role_quality':
        return timedelta(0)
    schedule = [0, 5, 15, 30, 60, 120]
    minutes = schedule[min(max(attempt, 1), len(schedule)) - 1]
    if stage == 'publish':
        minutes = min(minutes, 30)
    return timedelta(minutes=minutes)


def set_stage(slug: str, stage: str, *, event: str = 'STAGE_ADVANCED', detail: dict | None = None) -> dict:
    state = load_job(slug)
    state['current_stage'] = stage
    state['status'] = 'ACTIVE'
    state['next_retry_at'] = _iso()
    state['last_failure_class'] = None
    state['last_failure_code'] = None
    state['last_failure_reason'] = None
    return _save_state(state, event=event, detail=detail)


def save_writer_result(slug: str, *, payload: dict, qa: dict, findings: list[str], feedback: dict,
                       metadata: dict, passed: bool) -> dict:
    state = load_job(slug)
    write_artifact(slug, 'payload.json', payload)
    write_artifact(slug, 'qa.json', qa)
    write_artifact(slug, 'review-findings.json', findings)
    write_artifact(slug, 'gate-feedback.json', feedback)
    state['writer_session_id'] = metadata.get('session_id') or state.get('writer_session_id')
    turn = metadata.get('session_turn')
    if turn is not None:
        turns = list(state.get('writer_turn_ids') or [])
        marker = f"{state['writer_session_id']}:{turn}"
        if marker not in turns:
            turns.append(marker)
        state['writer_turn_ids'] = turns
    if passed:
        state['current_stage'] = 'EYECATCH_PENDING'
        state['next_retry_at'] = _iso()
        state['last_failure_class'] = None
        state['last_failure_code'] = None
        state['last_failure_reason'] = None
        return _save_state(state, event='WRITER_QA_PASSED')
    return _save_state(state, event='WRITER_RESULT_SAVED', detail={'passed': False})


def save_eyecatch(slug: str, *, receipt: dict, image_path: Path) -> dict:
    state = load_job(slug)
    write_artifact(slug, 'eyecatch-receipt.json', receipt)
    target = job_dir(slug) / 'eyecatch.png'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image_path, target)
    state['designer_session_id'] = receipt.get('thread_id') or state.get('designer_session_id')
    state['current_stage'] = 'PUBLISH_PENDING'
    state['next_retry_at'] = _iso()
    return _save_state(state, event='EYECATCH_PASSED')


def mark_failure(slug: str, *, stage: str, failure_class: str, failure_code: str,
                 reason: str, role_return: bool = False, role: str | None = None) -> dict:
    state = load_job(slug)
    retries = dict(state.get('retry_count_by_stage') or {})
    retries[stage] = int(retries.get(stage) or 0) + 1
    state['retry_count_by_stage'] = retries
    attempt = retries[stage]
    state['last_failure_class'] = failure_class
    state['last_failure_code'] = failure_code
    state['last_failure_reason'] = reason[:2000]
    if role_return and role:
        returns = dict(state.get('role_returns') or {})
        returns[role] = int(returns.get(role) or 0) + 1
        state['role_returns'] = returns
    elif failure_class == 'runtime_infrastructure':
        key = role or stage
        runtime = dict(state.get('runtime_retries') or {})
        runtime[key] = int(runtime.get(key) or 0) + 1
        state['runtime_retries'] = runtime

    if stage == 'writer':
        state['current_stage'] = 'QA_FAILED'
        if attempt >= WRITER_MAX_ATTEMPTS:
            state['status'] = 'FAILED'
            state['current_stage'] = 'WRITER_FAILED'
            state['next_retry_at'] = None
            return _save_state(state, event='WRITER_RETRY_EXHAUSTED', detail={'attempt': attempt})
    elif stage == 'eyecatch':
        state['current_stage'] = 'EYECATCH_PENDING'
    elif stage == 'publish':
        state['current_stage'] = 'PUBLISH_PENDING'
    state['next_retry_at'] = _iso(utcnow() + _backoff(stage, attempt, failure_class))
    return _save_state(state, event='STAGE_FAILED', detail={
        'stage': stage, 'attempt': attempt, 'failure_class': failure_class,
        'failure_code': failure_code, 'role_return': role_return,
    })


def mark_published(slug: str, commit: str) -> dict:
    state = load_job(slug)
    state['status'] = 'PUBLISHED'
    state['current_stage'] = 'PUBLISHED'
    state['published_commit'] = commit
    state['next_retry_at'] = None
    return _save_state(state, event='PUBLISHED', detail={'commit': commit})


def discard(slug: str, reason: str) -> dict:
    state = load_job(slug)
    state['status'] = 'DISCARDED'
    state['current_stage'] = 'DISCARDED'
    state['discard_reason'] = reason
    state['next_retry_at'] = None
    return _save_state(state, event='DISCARDED', detail={'reason': reason})


def materialize_for_publish(slug: str, repo: Path = ROOT) -> None:
    evidence = read_artifact(slug, 'evidence.json')
    payload = read_artifact(slug, 'payload.json')
    if not isinstance(evidence, dict) or not isinstance(payload, dict):
        raise RuntimeError(f'recovery artifacts incomplete for publish: {slug}')
    _atomic_json(repo / 'data' / 'evidence' / f'{slug}.json', evidence)
    _atomic_json(repo / 'data' / 'payloads' / f'{slug}.json', payload)
    image = job_dir(slug) / 'eyecatch.png'
    receipt = job_dir(slug) / 'eyecatch-receipt.json'
    if not image.is_file() or not receipt.is_file():
        raise RuntimeError(f'recovery eyecatch artifacts incomplete: {slug}')
    image_target = repo / 'img' / slug / f'{slug}.png'
    image_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image, image_target)
    receipt_target = repo / 'data' / 'eyecatch-receipts' / f'{slug}.json'
    receipt_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(receipt, receipt_target)


def record_execution(*, slug: str, role: str, event_type: str, stage: str,
                     metadata: dict | None = None, failure_class: str | None = None,
                     duration_ms: int | None = None) -> None:
    meta = metadata or {}
    row = {
        'at': _iso(), 'slug': slug, 'role': role, 'event_type': event_type,
        'stage': stage, 'failure_class': failure_class, 'duration_ms': duration_ms,
        'session_id': meta.get('session_id'), 'session_turn': meta.get('session_turn'),
    }
    for key in ('input_tokens', 'cached_input_tokens', 'uncached_input_tokens',
                'output_tokens', 'reasoning_output_tokens', 'total_tokens'):
        if isinstance(meta.get(key), int):
            row[key] = meta[key]
    _append_jsonl(EXECUTION_EVENTS_PATH, row)


def summarize_execution() -> dict:
    if not EXECUTION_EVENTS_PATH.is_file():
        return {'roles': []}
    rows = []
    for raw in EXECUTION_EVENTS_PATH.read_text(encoding='utf-8').splitlines():
        if not raw.strip():
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    roles: dict[str, dict] = {}
    for row in rows:
        role = str(row.get('role') or '')
        item = roles.setdefault(role, {
            'role': role, 'projects': set(), 'returns': 0, 'turns': 0,
            'tokens': 0, 'success': 0, 'duration_ms': 0, 'duration_observed': 0,
            'runtime_retries': 0,
        })
        if row.get('slug'):
            item['projects'].add(row['slug'])
        kind = row.get('event_type')
        if kind == 'turn':
            item['turns'] += 1
            item['tokens'] += int(row.get('total_tokens') or 0)
        elif kind == 'return':
            item['returns'] += 1
        elif kind == 'success':
            item['success'] += 1
        elif kind == 'runtime_retry':
            item['runtime_retries'] += 1
        if isinstance(row.get('duration_ms'), int):
            item['duration_ms'] += row['duration_ms']
            item['duration_observed'] += 1
    result = []
    for item in roles.values():
        projects = len(item.pop('projects'))
        item['projects'] = projects
        item['avg_tokens'] = round(item['tokens'] / projects, 1) if projects else None
        item['duration'] = item.pop('duration_ms')
        item.pop('duration_observed', None)
        result.append(item)
    return {'roles': sorted(result, key=lambda row: row['role'])}
