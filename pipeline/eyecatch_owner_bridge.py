from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PENDING_ROOT = Path.home() / 'Library/Application Support/Sellemy/pending-eyecatch'
OWNER_ROLE = 'sellemy-ops'
PROJECT_ID = 'BU-002'


class EyecatchOwnerBridgeError(RuntimeError):
    pass


def _slug_key(slug: str) -> str:
    safe = re.sub(r'[^A-Z0-9]+', '-', slug.upper()).strip('-')[:48]
    digest = hashlib.sha256(slug.encode()).hexdigest()[:10].upper()
    return f'{safe}-{digest}'


def pending_dir(slug: str) -> Path:
    return PENDING_ROOT / slug


def unresolved_pending() -> list[dict[str, Any]]:
    if not PENDING_ROOT.exists():
        return []
    rows = []
    for path in sorted(PENDING_ROOT.iterdir()):
        if not path.is_dir() or (path / 'completed.json').exists():
            continue
        req = path / 'request.json'
        if not req.exists():
            continue
        try:
            value = json.loads(req.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(value)
    return rows


def _run(cmd: list[str], *, input_text: str | None = None, cwd: Path | None = None) -> str:
    completed = subprocess.run(cmd, input=input_text, text=True, capture_output=True, check=False, timeout=30, cwd=cwd)
    if completed.returncode != 0:
        raise EyecatchOwnerBridgeError((completed.stderr or completed.stdout).strip()[:1500])
    return completed.stdout


def _history_has_message(task_dir: Path, message_id: str) -> bool:
    hist = task_dir / 'history'
    if not hist.exists():
        return False
    for p in hist.glob('*.json'):
        try:
            if json.loads(p.read_text(encoding='utf-8')).get('message_id') == message_id:
                return True
        except (OSError, json.JSONDecodeError):
            continue
    return False


def ensure_owner_request(*, slug: str, title: str, category: str, evidence: dict, payload: dict) -> dict:
    root = pending_dir(slug)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (root / 'payload.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    key = _slug_key(slug)
    task_id = f'TASK-BU002-EYECATCH-{key}'
    message_id = f'REQUEST-BU002-EYECATCH-{key}'
    chat_id = f'CHAT-{task_id}'
    request = {
        'slug': slug, 'title': title, 'category': category, 'owner_role_id': OWNER_ROLE,
        'task_id': task_id, 'message_id': message_id, 'chat_id': chat_id,
        'pending_dir': str(root),
    }
    (root / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    ai_os_root = Path(os.environ.get('SELLEMY_AI_OS_ROOT', '/Users/kickstart/ai-management-os')).expanduser()
    py = os.environ.get('SELLEMY_AI_OS_PYTHON', str(ai_os_root / '.venv/bin/python'))
    canonical_root = Path.home() / 'Library/Application Support/AIManagementOS/canonical'
    task_dir = canonical_root / 'tasks' / task_id
    if not (task_dir / 'task.json').exists():
        payload_create = {
            'task_id': task_id, 'task_name': f'Sellemy article eyecatch: {slug}', 'project_id': PROJECT_ID,
            'creator_role_id': OWNER_ROLE, 'owner_role_id': OWNER_ROLE, 'executor_role_id': OWNER_ROLE,
            'message_id': f'CREATE-{task_id}', 'parent_task_id': None, 'ceo_direct': False,
            'receiving_role_id': OWNER_ROLE,
        }
        _run([py, '-m', 'ai_management_os.actions.task_create'], input_text=json.dumps(payload_create, ensure_ascii=False), cwd=ai_os_root)

    detail = {
        'slug': slug, 'title': title, 'category': category,
        'reason': 'Production publication requires an article-specific ChatGPT Chat eyecatch. Runtime detected no valid Chat receipt.',
        'pending_package': str(root),
        'required_actions': [
            'sellemy-ops Chat itself generates one article-specific 1536x1024 PNG with image generation; do not use OpenAI API or Codex.',
            f'Register the exact generated PNG into the pending package with: cd {ROOT} && .venv/bin/python pipeline/register_chat_eyecatch.py {slug} <PNG_PATH> --pending --chat-ref {message_id}',
            f'Resume the exact pending publication with: cd {ROOT} && .venv/bin/python pipeline/resume_pending_eyecatch.py {slug}',
            'Read back the published slug and published_at and return RESULT to the same Task.',
        ],
        'next_action': 'Generate, pending-register, resume, and verify the exact same article publication. Do not plan a replacement topic while this request is unresolved.',
        'next_responsibility_role_id': OWNER_ROLE, 'project_completed': False,
    }
    if not _history_has_message(task_dir, message_id):
        append_payload = {
            'task_id': task_id, 'message_id': message_id, 'actor_role_id': OWNER_ROLE,
            'role_id': OWNER_ROLE, 'status': 'pending', 'detail': detail,
        }
        _run([py, '-m', 'ai_management_os.actions.task_append'], input_text=json.dumps(append_payload, ensure_ascii=False), cwd=ai_os_root)

    role_script = ai_os_root / 'scripts' / 'role_chat_message.py'
    _run([py, str(role_script), 'create-chat', '--chat-id', chat_id, '--actor-role', OWNER_ROLE, '--task-id', task_id], cwd=ai_os_root)
    body = (
        f'Task ID: {task_id}\n'
        f'Sellemy article eyecatch production request for slug={slug}. Title={title}. Category={category}.\n'
        'You are sellemy-ops and must generate this eyecatch in this Owner Chat using image generation. '
        'Required: one article-specific 1536x1024 PNG; no category-copy, montage fallback, OpenAI API, or Codex. '
        f'Pending publication package: {root}. After generation, pending-register the exact PNG with '
        f'pipeline/register_chat_eyecatch.py {slug} <PNG_PATH> --pending --chat-ref {message_id}; then run '
        f'pipeline/resume_pending_eyecatch.py {slug}. Verify the real published slug and published_at and return RESULT.'
    )
    try:
        _run([py, str(role_script), 'send', '--chat-id', chat_id, '--message-id', message_id,
              '--from-role', OWNER_ROLE, '--to-role', OWNER_ROLE, '--body', body, '--reply-required'], cwd=ai_os_root)
    except EyecatchOwnerBridgeError as exc:
        if 'message_id already exists' not in str(exc):
            raise
    return {**request, 'queued': True}
