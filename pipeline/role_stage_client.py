"""Launch the owning role in an isolated process. Runtime does not open artifacts."""
from __future__ import annotations
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class RoleStageFailure(RuntimeError):
    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


def execute_role(role: str, slug: str = '', *, cache_only: bool = False, request: dict | None = None) -> dict:
    if role not in {'writer', 'reviewer', 'review_reset', 'product', 'inspect', 'designer', 'publish', 'postpublish', 'ceo_reconcile', 'planning', 'planning_commit', 'fresh_create', 'product_qa_disposition', 'already_published', 'slot_compensation', 'recovery', 'report', 'state_queue', 'scheduler'}:
        raise RoleStageFailure('unrecognized role')
    if slug and ('/' in slug or slug.startswith('.')):
        raise RoleStageFailure('invalid job identity')
    argv = [sys.executable, str(ROOT / 'pipeline' / 'role_stage_exec.py'), '--role', role]
    if slug:
        argv += ['--slug', slug]
    if cache_only:
        argv += ['--cache-only']
    # Each role gets its own 15-minute budget; a parent runtime has no shared timer.
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, cwd=ROOT,
                                   start_new_session=True)
        try:
            stdout, _stderr = process.communicate(
                input=json.dumps(request or {}, ensure_ascii=False), timeout=900)
        except subprocess.TimeoutExpired as exc:
            # Kill this role's entire subprocess tree, not any other BU process.
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise RoleStageFailure(f'{role} exceeded its 15-minute limit') from exc
    except OSError as exc:
        raise RoleStageFailure(f'{role} process unavailable: {exc}') from exc
    try:
        message = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RoleStageFailure(f'{role} did not return a process receipt ({process.returncode})') from exc
    if process.returncode or message.get('ok') is not True:
        raise RoleStageFailure(str(message.get('error', 'role stage failed')),
                               details={**(message.get('result') or {}),
                                        'error_type': message.get('error_type')})
    return message.get('result') or {}
