from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


class StandardWorkFallbackPending(RuntimeError):
    def __init__(self, task_id: str):
        super().__init__(f'Standard Work fallback pending: {task_id}')
        self.task_id = task_id


def _read_accepted_result(task_dir: Path, *, stage: str) -> dict | None:
    history_dir = task_dir / 'history'
    if not history_dir.is_dir():
        return None
    events = []
    for path in sorted(history_dir.glob('*.json')):
        try:
            events.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, json.JSONDecodeError):
            raise RuntimeError(f'invalid Standard Work fallback history: {path}')
    if not events:
        return None
    latest = events[-1]
    status = str(latest.get('status') or '').lower()
    if status != 'completed':
        return None
    decision = str((latest.get('detail') or {}).get('decision') or '').upper()
    if decision != 'ACCEPT':
        raise RuntimeError(f'Standard Work fallback completed without ACCEPT: {latest.get("message_id")}')
    for event in reversed(events):
        detail = event.get('detail') or {}
        result = detail.get('result')
        if result is None:
            continue
        event_stage = detail.get('stage')
        if event_stage and event_stage != stage:
            continue
        if not isinstance(result, dict):
            raise RuntimeError('Standard Work fallback accepted RESULT is not an object')
        return result
    raise RuntimeError('Standard Work fallback completed ACCEPT but RESULT is missing')


def request_standard_work_fallback(*, stage: str, prompt: str, schema: dict) -> dict:
    """Create/reuse one deterministic BU-002 fallback Task.

    The first call stops while Standard Work runs asynchronously. After Owner
    Acceptance completes the Task, later calls return the accepted RESULT so the
    original Runtime can resume without self-accepting Work output.
    """
    digest = hashlib.sha256((stage + '\n' + prompt).encode('utf-8')).hexdigest()[:16].upper()
    task_id = f'TASK-BU002-STANDARD-FALLBACK-{stage.upper()}-{digest}'
    ai_os_root = Path(os.environ.get('SELLEMY_AI_OS_ROOT', '/Users/kickstart/ai-management-os')).expanduser()
    python = os.environ.get('SELLEMY_AI_OS_PYTHON', str(ai_os_root / '.venv' / 'bin' / 'python'))
    canonical_root = Path(os.environ.get(
        'SELLEMY_AI_OS_CANONICAL_ROOT',
        str(Path.home() / 'Library' / 'Application Support' / 'AIManagementOS' / 'canonical'),
    )).expanduser()
    task_dir = canonical_root / 'tasks' / task_id
    if (task_dir / 'task.json').exists():
        accepted = _read_accepted_result(task_dir, stage=stage)
        if accepted is not None:
            return accepted
    else:
        packet = {
            'fallback_kind': 'pro_availability_to_standard_work',
            'source_unit': 'BU-002',
            'source_role_id': 'sellemy-ops',
            'executor_role_id': 'bu-work4',
            'stage': stage,
            'reason': 'Claude availability / usage limit; non-code task must not fall back to Codex',
            'prompt': prompt,
            'required_output_schema': schema,
            'acceptance': [
                'Return structured JSON conforming exactly to required_output_schema',
                'Do not change code or Runtime configuration',
                'Return RESULT to sellemy-ops for Acceptance; do not self-complete the Task',
            ],
        }
        payload = {
            'task_id': task_id,
            'task_name': f'Sellemy Standard fallback: {stage}',
            'project_id': 'BU-002',
            'creator_role_id': 'sellemy-ops',
            'owner_role_id': 'sellemy-ops',
            'executor_role_id': 'bu-work4',
            'message_id': f'CREATE-{task_id}',
            'parent_task_id': None,
            'ceo_direct': False,
            'receiving_role_id': 'bu-work4',
            'initial_detail': {'sellemy_standard_fallback_request': packet},
        }
        completed = subprocess.run(
            [python, '-m', 'ai_management_os.actions.task_create'], cwd=ai_os_root,
            input=json.dumps(payload, ensure_ascii=False), text=True, capture_output=True,
            check=False, timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError('failed to create Standard Work fallback Task: ' + (completed.stderr or completed.stdout).strip()[:1000])
    raise StandardWorkFallbackPending(task_id)
