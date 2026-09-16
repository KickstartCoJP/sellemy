from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class FeedbackTaskBridgeError(RuntimeError):
    pass


def _run_action(*, python: str, ai_os_root: Path, module: str, payload: dict) -> dict:
    completed = subprocess.run(
        [python, '-m', module],
        cwd=ai_os_root,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise FeedbackTaskBridgeError(
            f'{module} failed rc={completed.returncode}: {(completed.stderr or completed.stdout).strip()[:1200]}'
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FeedbackTaskBridgeError(f'{module} returned invalid JSON') from exc
    if not isinstance(value, dict):
        raise FeedbackTaskBridgeError(f'{module} returned non-object JSON')
    return value


def sync_feedback_to_task_event(*, feedback: dict, controller_state: dict, config: dict) -> dict:
    """Project adaptive-quality signals into the existing AI Management OS Task/Event model.

    No queue or business status is introduced here. Green/yellow feedback that does not require
    canonical work remains only in the append-only feedback evidence. A structural feedback signal
    creates one deterministic BU task; a minimum-frequency CEO alert makes that task
    user_decision_required.
    """
    canonical_required = bool(feedback.get('canonical_feedback_required'))
    ceo_alert = bool(controller_state.get('ceo_alert_required'))
    if not canonical_required and not ceo_alert:
        return {'required': False, 'created': False, 'message_id': None, 'task_id': None}

    project_id = str(config.get('feedback_task_project_id') or 'BU-002')
    role_id = str(config.get('feedback_task_role_id') or 'sellemy-ops')
    prefix = str(config.get('feedback_task_prefix') or 'TASK-BU002-QUALITY-FEEDBACK')
    event_id = str(feedback.get('event_id') or '').strip()
    if len(event_id) < 12:
        raise FeedbackTaskBridgeError('feedback event_id is required for Task/Event idempotency')
    suffix = event_id[:16].upper()
    task_id = f'{prefix}-{suffix}'
    create_message_id = f'TASKCREATE-{project_id}-QUALITY-FEEDBACK-{suffix}'
    event_message_id = f'FEEDBACK-{project_id}-{suffix}'

    ai_os_root = Path(os.environ.get('SELLEMY_AI_OS_ROOT', '/Users/suzukitakayuki/ai-management-os')).expanduser()
    ai_os_python = os.environ.get('SELLEMY_AI_OS_PYTHON', str(ai_os_root / '.venv' / 'bin' / 'python'))
    canonical_root = Path(
        os.environ.get(
            'SELLEMY_AI_OS_CANONICAL_ROOT',
            str(Path.home() / 'Library' / 'Application Support' / 'AIManagementOS' / 'canonical'),
        )
    ).expanduser()
    task_dir = canonical_root / 'tasks' / task_id

    created = False
    if not (task_dir / 'task.json').exists():
        _run_action(
            python=ai_os_python,
            ai_os_root=ai_os_root,
            module='ai_management_os.actions.task_create',
            payload={
                'task_id': task_id,
                'task_name': f'Sellemy品質Feedback: {feedback.get("slug") or suffix}',
                'project_id': project_id,
                'creator_role_id': role_id,
                'owner_role_id': role_id,
                'executor_role_id': role_id,
                'message_id': create_message_id,
                'parent_task_id': None,
                'ceo_direct': False,
                'receiving_role_id': role_id,
            },
        )
        created = True

    history_dir = task_dir / 'history'
    if history_dir.exists():
        for path in history_dir.glob('*.json'):
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            if row.get('message_id') == event_message_id:
                return {
                    'required': True,
                    'created': created,
                    'duplicate': True,
                    'task_id': task_id,
                    'message_id': event_message_id,
                    'status': row.get('status'),
                }

    if ceo_alert:
        status = 'user_decision_required'
        detail = {
            'source_feedback_event_id': event_id,
            'source_publication_commit': feedback.get('commit'),
            'source_slug': feedback.get('slug'),
            'issues': feedback.get('issues') or [],
            'improvement_suggestions': feedback.get('improvement_suggestions') or [],
            'controller_state': controller_state,
            'reason': '最低投稿頻度でもred品質Feedbackが継続し、Runtimeが公開を停止した。',
            'requested_to': 'ceo',
            'required_actions': [
                '品質回復策を優先実施して停止を維持するか、例外的な公開再開条件を指定する。'
            ],
            'resume_role_id': role_id,
            'next_action': 'CEO判断後、Sellemy運用GPTが改善Taskへ接続し、品質回復確認後に公開再開を判断する。',
            'next_responsibility_role_id': role_id,
            'project_completed': False,
        }
    else:
        status = 'in_progress'
        detail = {
            'source_feedback_event_id': event_id,
            'source_publication_commit': feedback.get('commit'),
            'source_slug': feedback.get('slug'),
            'issues': feedback.get('issues') or [],
            'improvement_suggestions': feedback.get('improvement_suggestions') or [],
            'controller_state': controller_state,
            'canonical_feedback_required': True,
            'next_action': 'Sellemy運用GPTがFeedbackを検品し、必要な正本またはRuntime改善を同一Taskから起案・実施する。',
            'next_responsibility_role_id': role_id,
            'project_completed': False,
        }

    appended = _run_action(
        python=ai_os_python,
        ai_os_root=ai_os_root,
        module='ai_management_os.actions.task_append',
        payload={
            'task_id': task_id,
            'message_id': event_message_id,
            'actor_role_id': role_id,
            'role_id': role_id,
            'status': status,
            'detail': detail,
        },
    )
    if appended.get('message_id') != event_message_id or appended.get('status') != status:
        raise FeedbackTaskBridgeError('Task/Event bridge read-back mismatch')
    return {
        'required': True,
        'created': created,
        'duplicate': False,
        'task_id': task_id,
        'message_id': event_message_id,
        'status': status,
    }
