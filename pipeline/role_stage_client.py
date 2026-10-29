"""Launch the owning role in an isolated process. Runtime does not open artifacts."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class RoleStageFailure(RuntimeError):
    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


def execute_role(role: str, slug: str = '', *, cache_only: bool = False, request: dict | None = None) -> dict:
    if role not in {'writer', 'reviewer', 'review_reset', 'product', 'inspect', 'designer', 'publish', 'postpublish', 'ceo_reconcile', 'planning', 'planning_commit', 'fresh_create', 'already_published', 'slot_compensation', 'recovery', 'report', 'state_queue', 'scheduler'}:
        raise RoleStageFailure('unrecognized role')
    if slug and ('/' in slug or slug.startswith('.')):
        raise RoleStageFailure('invalid job identity')
    argv = [sys.executable, str(ROOT / 'pipeline' / 'role_stage_exec.py'), '--role', role]
    if slug:
        argv += ['--slug', slug]
    if cache_only:
        argv += ['--cache-only']
    try:
        completed = subprocess.run(argv, input=json.dumps(request or {}, ensure_ascii=False), text=True, capture_output=True,
                                   cwd=ROOT, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RoleStageFailure(f'{role} process unavailable: {exc}') from exc
    try:
        message = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RoleStageFailure(f'{role} did not return a process receipt ({completed.returncode})') from exc
    if completed.returncode or message.get('ok') is not True:
        raise RoleStageFailure(str(message.get('error', 'role stage failed')),
                               details={**(message.get('result') or {}),
                                        'error_type': message.get('error_type')})
    return message.get('result') or {}
