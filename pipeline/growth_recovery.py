from __future__ import annotations

import json
import hashlib
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

TERMINAL_STATUSES = {'PUBLISHED', 'DISCARDED', 'FAILED', 'RETIRED'}
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


def _hold_invalid_state(directory: Path, raw: bytes | None, reason: str) -> dict:
    """Preserve the unreadable checkpoint and keep the affected job non-runnable.

    Do not silently drop a damaged job or re-create it as a fresh article.
    The existing job directory remains the sole recovery authority.
    """
    backup = None
    if raw is not None:
        digest = hashlib.sha256(raw).hexdigest()
        backup = directory / f'state.invalid.{digest[:16]}.bin'
        if not backup.exists():
            _atomic_bytes(backup, raw)
    topic = read_artifact(directory.name, 'topic.json', {})
    category = topic.get('category') if isinstance(topic, dict) else None
    state = {
        'schema_version': 1, 'slug': directory.name, 'category': category,
        'status': 'ACTIVE', 'current_stage': 'STATE_HOLD',
        'created_at': _iso(), 'updated_at': _iso(), 'next_retry_at': None,
        'last_failure_class': 'runtime_infrastructure',
        'last_failure_code': 'STATE_CHECKPOINT_INVALID',
        'last_failure_reason': reason,
        'invalid_state_backup': str(backup) if backup else None,
        'invalid_state_sha256': hashlib.sha256(raw).hexdigest() if raw is not None else None,
    }
    return _save_state(state, event='STATE_CHECKPOINT_INVALID_HOLD', detail={
        'reason': reason, 'backup': str(backup) if backup else None,
    })


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', dir=path.parent, prefix='.' + path.name + '.', delete=False) as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def all_jobs() -> list[dict]:
    if not JOBS_ROOT.is_dir():
        return []
    rows = []
    for directory in sorted(path for path in JOBS_ROOT.iterdir() if path.is_dir()):
        state_file = directory / 'state.json'
        if not state_file.is_file() and not any(directory.iterdir()):
            continue
        raw = state_file.read_bytes() if state_file.is_file() else None
        try:
            row = json.loads(raw) if raw is not None else None
            if (not isinstance(row, dict) or row.get('schema_version') != 1
                    or row.get('slug') != directory.name
                    or row.get('status') not in {'ACTIVE', *TERMINAL_STATUSES}
                    or not isinstance(row.get('current_stage'), str)
                    or not row['current_stage']
                    or not all(isinstance(row.get(name), str) and row[name]
                               for name in ('created_at', 'updated_at'))
                    or not isinstance(row.get('next_retry_at'), (str, type(None)))):
                raise ValueError('wrong type, identity, or state fields')
        except (ValueError, UnicodeDecodeError, TypeError) as exc:
            row = _hold_invalid_state(directory, raw, f'{type(exc).__name__}: {exc}')
        rows.append(row)
    return rows



def reopen_product_failed_for_reselection(slug: str) -> dict:
    """Reopen a historical product-only failure under the new batch policy.

    Preserve original evidence, history and job identity. Discarded or published
    work must never be silently reopened.
    """
    state = load_job(slug)
    if (state.get('status'), state.get('current_stage')) != ('FAILED', 'PRODUCT_FAILED'):
        raise ValueError('only FAILED PRODUCT_FAILED jobs may be reopened')
    if not read_artifact(slug, 'topic.json') or not read_artifact(slug, 'evidence.json'):
        raise ValueError('cannot resume a job without topic and evidence')
    state['status'] = 'ACTIVE'
    state['current_stage'] = 'WRITER_PENDING'
    state['next_retry_at'] = _iso()
    ng = dict(state.get('quality_ng_by_gate') or {})
    ng['product'] = 0
    state['quality_ng_by_gate'] = ng
    state['reselection_batch_policy'] = 'initial_18_plus_9_batches'
    return _save_state(state, event='REOPENED_FOR_BATCH_RESELECTION',
                       detail={'previous_quality_ng': 3, 'policy': state['reselection_batch_policy']})


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
    eligible.sort(key=lambda row: (row.get('updated_at') or row.get('created_at') or '', row.get('slug') or ''))
    return eligible[0] if eligible else None


def _backoff(stage: str, attempt: int, failure_class: str) -> timedelta:
    if failure_class == 'role_quality':
        return timedelta(0)
    if stage == 'product' and failure_class == 'missing_evidence':
        return timedelta(minutes=15)
    if stage == 'product' and failure_class == 'runtime_infrastructure':
        return timedelta(minutes=1)
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
    from qa_certification import certify
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
        from operating_profile import normal_profile
        approval = certify(payload, read_artifact(slug, 'evidence.json'), qa, read_artifact(slug, 'acceptance.json'))
        write_artifact(slug, 'qa-certification.json', approval)
        state['current_stage'] = 'CHAT_REVIEW_PENDING'
        state['next_retry_at'] = None
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
    from operating_profile import normal_profile
    normal = normal_profile()
    if normal and failure_class in {'role_quality','missing_evidence'}:
        ng=dict(state.get('quality_ng_by_gate') or {})
        previous=int(ng.get(stage) or 0)
        if stage=='product' and not ng.get(stage) and retries.get(stage,0)>1:
            history=job_dir(slug)/'history.jsonl'
            if history.exists():
                for line in history.read_text().splitlines():
                    try: row=json.loads(line)
                    except ValueError:continue
                    detail=row.get('detail') or {}
                    if row.get('event')=='STAGE_FAILED' and detail.get('stage')=='product' and detail.get('failure_class')=='missing_evidence':
                        previous+=1
        ng[stage]=previous+1
        state['quality_ng_by_gate']=ng
        if ng[stage]>=3 and stage == 'eyecatch':
            state['status'] = 'ACTIVE'
            state['current_stage'] = 'OWNER_EYECATCH_PENDING'
            state['next_retry_at'] = None
            state['last_failure_code'] = failure_code
            state['last_failure_reason'] = reason[:2000]
            write_artifact(slug, 'owner-quality-case.json', {
                'schema': 'sellemy-owner-quality-case/v1', 'slug': slug,
                'stage': stage, 'ng_count': ng[stage], 'failure_code': failure_code,
                'case_state': 'OWNER_IMAGE_REQUESTED',
            })
            return _save_state(state, event='EYECATCH_OWNER_DELEGATED')
        if ng[stage]>=3 and stage != 'product':
            state['status']='FAILED'
            state['current_stage']=stage.upper()+'_FAILED'
            state['next_retry_at']=None
            state['last_failure_class']=failure_class
            state['last_failure_code']=failure_code
            state['last_failure_reason']=reason[:2000]
            state['operating_profile']='normal'
            receipt=_save_state(state,event='QUALITY_GATE_THREE_NG',detail={'stage':stage,'ng_count':ng[stage]})
            recent=sorted((x for x in all_jobs() if x.get('operating_profile')=='normal'
                           and x.get('status') in {'PUBLISHED','FAILED','DISCARDED','RETIRED'}),
                          key=lambda x:(x.get('updated_at') or '',x.get('slug') or ''))
            if len(recent)>=10 and all(x.get('status')=='FAILED' for x in recent[-10:]) and (len(recent)==10 or recent[-11].get('status')!='FAILED'):
                if recent[-1].get('slug') == slug:
                    write_artifact(slug,'ceo-inspection-required.json',{
                        'schema':'sellemy-failed-10-inspection/v1',
                        'status':'REQUIRES_CEO_INSPECTION',
                        'reason':'10 consecutive normal production turns FAILED',
                        'slugs':[x['slug'] for x in recent[-10:]],
                        'latest_failed_at':receipt.get('updated_at'),
                        'publication_blocked_by_this_receipt':False})
            write_artifact(slug,'owner-quality-case.json',{
                'schema':'sellemy-owner-quality-case/v1','slug':slug,
                'stage':stage,'ng_count':ng[stage],'failure_code':failure_code,
                'case_state':'AWAITING_OWNER_ANALYSIS','job_state_revision':receipt.get('updated_at')})
            return receipt
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
        if (not normal) and (attempt >= WRITER_MAX_ATTEMPTS or failure_code == 'ASTRA_QUALITY_EXHAUSTED'):
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
    state['published_at'] = state.get('published_at') or _iso()
    from operating_profile import normal_profile
    if normal_profile():
        state['operating_profile'] = 'normal'
        import hashlib
        selection_hash=hashlib.sha256(f'{slug}:{commit}'.encode()).hexdigest()
        if int(selection_hash[:8],16)%10 == 0:
            write_artifact(slug,'owner-success-case.json',{
                'schema':'sellemy-owner-learning-sample/v1',
                'slug':slug,'commit':commit,'selection_sha256':selection_hash,
                'sampling_rule':'sha256(slug:commit) modulo10 = 0',
                'case_state':'AWAITING_OWNER_ANALYSIS',
                'evidence_artifacts':['topic.json','selection.json','evidence.json','payload.json','qa.json','acceptance.json']})
    state['next_retry_at'] = None
    state['last_failure_class'] = None
    state['last_failure_code'] = None
    state['last_failure_reason'] = None
    return _save_state(state, event='PUBLISHED', detail={'commit': commit})


def discard(slug: str, reason: str) -> dict:
    state = load_job(slug)
    state['status'] = 'DISCARDED'
    state['current_stage'] = 'DISCARDED'
    state['discard_reason'] = reason
    state['next_retry_at'] = None
    return _save_state(state, event='DISCARDED', detail={'reason': reason})


def retire(slug: str, reason: str) -> dict:
    state = load_job(slug)
    state['status'] = 'RETIRED'
    state['current_stage'] = 'RETIRED'
    state['discard_reason'] = reason
    state['next_retry_at'] = None
    return _save_state(state, event='RETIRED', detail={'reason': reason})


def hold_product_review_conflict(slug: str, *, reasons: list[str], pinned_asins: list[str]) -> dict:
    """Stop a contradictory six-product QA outcome without losing its evidence."""
    state = load_job(slug)
    if state.get('status') != 'ACTIVE':
        raise ValueError('only active jobs may be placed on product hold')
    write_artifact(slug, 'product-hold.json', {
        'reason': 'all six products individually pass, but whole-set QA fails',
        'issues': reasons, 'retained_asins': sorted(pinned_asins),
        'resume_condition': 'reviewer criterion conflict resolved or material evidence revision',
    })
    state['current_stage'] = 'PRODUCT_HOLD'
    state['next_retry_at'] = None
    state['last_failure_class'] = 'missing_evidence'
    state['last_failure_code'] = 'PRODUCT_REVIEW_CRITERIA_CONFLICT'
    state['last_failure_reason'] = '; '.join(reasons)[:2000]
    return _save_state(state, event='PRODUCT_REVIEW_CONFLICT_HOLD',
                       detail={'pinned_count': len(pinned_asins)})


def request_product_reselection(slug: str, reasons: list[str]) -> dict:
    # Control outcome inside the existing job, not a new queue or state enum.
    from comparison_acceptance import blocked
    progress = read_artifact(slug, 'product-search-progress.json', {}) or {}
    # A source/candidate change permits a new attempt; the identical failed
    # search state must not consume another QA turn or block other articles.
    signal = {
        'qualified_evidence': sorted((str(x.get('asin')), hashlib.sha256(
            json.dumps([z for z in (x.get('sources') or []) if isinstance(z, dict) and z.get('verified') is True and str(z.get('excerpt') or '').strip()],
                       sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest())
            for x in progress.get('candidates', []) if isinstance(x, dict)
            and any(isinstance(z, dict) and z.get('verified') is True and str(z.get('excerpt') or '').strip() for z in x.get('sources') or [])),
        'reviewed': sorted(progress.get('reviewed_sets') or []),
        'reason': sorted(set(str(x) for x in reasons)),
    }
    signature = hashlib.sha256(json.dumps(signal, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    previous = read_artifact(slug, 'product-attempt-fingerprint.json', {}) or {}
    if previous.get('signature') == signature:
        state = load_job(slug)
        state['current_stage'] = 'PRODUCT_HOLD'
        state['status'] = 'ACTIVE'
        state['next_retry_at'] = None
        state['last_failure_class'] = 'missing_evidence'
        state['last_failure_code'] = 'UNCHANGED_PRODUCT_SEARCH'
        state['last_failure_reason'] = '; '.join(reasons)[:2000]
        write_artifact(slug, 'product-blocker.json', blocked(reasons))
        write_artifact(slug, 'product-hold.json', {'signature': signature, 'reason': 'unchanged input and failure', 'resume_condition': 'new product or evidence or owner intervention'})
        return _save_state(state, event='PRODUCT_NO_PROGRESS_HOLD', detail={'signature': signature})
    write_artifact(slug, 'product-attempt-fingerprint.json', {'signature': signature})
    write_artifact(slug, 'product-blocker.json', blocked(reasons))
    write_artifact(slug, 'acceptance.json', blocked(reasons))
    set_stage(slug, 'WRITER_PENDING', event='PRODUCT_RESELECTION_REQUESTED')
    return mark_failure(slug, stage='product', failure_class='missing_evidence',
                        failure_code='NEEDS_PRODUCT_RESELECTION', reason='; '.join(reasons), role='planning')


def replace_product_evidence(slug: str, evidence: dict, selection: dict) -> dict:
    # Write fresh inputs and invalidate ALL derived authorizations before clearing
    # blocker. A crash at any point leaves either the blocker or hash mismatch.
    from comparison_acceptance import product_issues
    issues = product_issues(evidence)
    if evidence.get('slug') != slug or issues:
        raise ValueError('replacement evidence not accepted: ' + '; '.join(issues))
    write_artifact(slug, 'evidence.json', evidence)
    write_artifact(slug, 'selection.json', selection)
    for name in ('payload.json', 'acceptance.json', 'qa.json', 'qa-certification.json', 'gate-feedback.json',
                 'eyecatch-receipt.json', 'chat-publish-approval.json'):
        write_artifact(slug, name, {})
    write_artifact(slug, 'product-blocker.json', {})
    return set_stage(slug, 'WRITER_PENDING', event='PRODUCT_RESELECTION_ACCEPTED')


def materialize_for_publish(slug: str, repo: Path = ROOT) -> None:
    evidence = read_artifact(slug, 'evidence.json')
    payload = read_artifact(slug, 'payload.json')
    if not isinstance(evidence, dict) or not isinstance(payload, dict):
        raise RuntimeError(f'recovery artifacts incomplete for publish: {slug}')
    from comparison_acceptance import require_acceptance
    from publish_gate import require_publishing_enabled
    from chat_publish_approval import require_chat_publish_approval
    acceptance = read_artifact(slug, 'acceptance.json')
    if (read_artifact(slug, 'product-blocker.json', {}) or {}).get('needs_product_reselection'):
        raise ValueError('comparison acceptance refused: needs_product_reselection')
    require_publishing_enabled(repo)
    require_acceptance(payload, evidence, acceptance)
    from qa_certification import require_certification
    require_certification(read_artifact(slug, 'qa-certification.json', {}), payload, evidence,
                          read_artifact(slug, 'qa.json', {}), acceptance)
    from operating_profile import normal_profile
    require_chat_publish_approval(read_artifact(slug, 'chat-publish-approval.json'), payload=payload, evidence=evidence)
    _atomic_json(repo / 'data' / 'acceptance' / f'{slug}.json', acceptance)
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


def reopen_writer_failed_after_owner_remediation(slug: str, *, reason: str, verified_revision: str) -> dict:
    """Resume a specific owner-analyzed Writer failure, retaining its history.

    This requires an existing quality incident and a concrete remediation revision.
    It is not an automatic bypass of the three-NG quality gate.
    """
    state = load_job(slug)
    case = read_artifact(slug, 'owner-quality-case.json', {}) or {}
    if (state.get('status'), state.get('current_stage')) != ('FAILED', 'WRITER_FAILED'):
        raise ValueError('only FAILED WRITER_FAILED jobs may be resumed')
    if case.get('case_state') != 'AWAITING_OWNER_ANALYSIS' or case.get('job_state_revision') != state.get('updated_at'):
        raise ValueError('matching owner quality case required')
    if not str(reason).strip() or len(str(verified_revision).strip()) < 7:
        raise ValueError('owner remediation reason and deployed revision required')
    from comparison_acceptance import product_issues
    evidence = read_artifact(slug, 'evidence.json', {}) or {}
    if product_issues(evidence) or (read_artifact(slug, 'product-blocker.json', {}) or {}).get('needs_product_reselection'):
        raise ValueError('product QA must still be accepted before Writer recovery')
    write_artifact(slug, 'owner-quality-case.json', {**case, 'case_state': 'REMEDIATED',
        'remediation_reason': reason, 'verified_revision': verified_revision})
    state['status'] = 'ACTIVE'
    state['current_stage'] = 'QA_FAILED'
    state['next_retry_at'] = _iso()
    state['quality_ng_by_gate'] = {**(state.get('quality_ng_by_gate') or {}), 'writer': 0}
    state['last_failure_code'] = None
    state['last_failure_class'] = None
    state['last_failure_reason'] = None
    return _save_state(state, event='OWNER_REMEDIATION_WRITER_RESUMED',
        detail={'reason': reason, 'verified_revision': verified_revision,
                'prior_quality_ng': case.get('ng_count')})
