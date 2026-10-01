from __future__ import annotations

import fcntl
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path


class PublishGateError(RuntimeError):
    pass


def _git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ['git', *args], cwd=root, text=True, capture_output=True, check=False
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise PublishGateError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout.strip()


def sync_clean_main(root: Path) -> dict:
    """Bring a clean local main forward to origin/main without rewriting history."""
    dirty = _git(root, 'status', '--porcelain', '--untracked-files=all')
    if dirty:
        raise PublishGateError('working tree is not clean; publish sync refused')
    _git(root, 'fetch', 'origin', 'main', '--quiet')
    head_before = _git(root, 'rev-parse', 'HEAD')
    origin = _git(root, 'rev-parse', 'origin/main')
    fast_forwarded = False
    if head_before != origin:
        ancestor = subprocess.run(
            ['git', 'merge-base', '--is-ancestor', head_before, origin],
            cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if ancestor.returncode != 0:
            raise PublishGateError(
                f'local main diverged from origin/main; safe fast-forward impossible: '
                f'HEAD={head_before} origin/main={origin}'
            )
        _git(root, 'merge', '--ff-only', 'origin/main')
        fast_forwarded = True
    head_after = _git(root, 'rev-parse', 'HEAD')
    origin_after = _git(root, 'rev-parse', 'origin/main')
    dirty_after = _git(root, 'status', '--porcelain', '--untracked-files=all')
    if head_after != origin_after or dirty_after:
        raise PublishGateError('publish sync read-back failed: HEAD/origin/main/clean mismatch')
    return {
        'head_before': head_before,
        'head_after': head_after,
        'origin_main': origin_after,
        'fast_forwarded': fast_forwarded,
        'clean': True,
    }


def recover_unpublished_commit_after_remote_race(root: Path, candidate_commit: str) -> dict:
    """Discard only an unpublished local candidate after a remote writer won the push race."""
    _git(root, 'fetch', 'origin', 'main', '--quiet')
    origin = _git(root, 'rev-parse', 'origin/main')
    head = _git(root, 'rev-parse', 'HEAD')
    if head != candidate_commit:
        raise PublishGateError(
            f'publish-race recovery refused: HEAD changed unexpectedly: {head} != {candidate_commit}'
        )
    published = subprocess.run(
        ['git', 'merge-base', '--is-ancestor', candidate_commit, origin],
        cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if published.returncode == 0:
        return {'already_published': True, 'head_after': candidate_commit, 'origin_main': origin}
    parent = _git(root, 'rev-parse', f'{candidate_commit}^')
    parent_on_remote = subprocess.run(
        ['git', 'merge-base', '--is-ancestor', parent, origin],
        cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if parent_on_remote.returncode != 0:
        raise PublishGateError(
            'publish-race recovery refused: candidate parent is not contained in latest origin/main'
        )
    _git(root, 'reset', '--hard', 'origin/main')
    dirty = _git(root, 'status', '--porcelain', '--untracked-files=all')
    head_after = _git(root, 'rev-parse', 'HEAD')
    if dirty or head_after != origin:
        raise PublishGateError('publish-race recovery read-back failed')
    return {
        'already_published': False,
        'discarded_unpublished_commit': candidate_commit,
        'head_after': head_after,
        'origin_main': origin,
        'clean': True,
    }


class PublishGate:
    """Cross-process Sellemy publication critical section for this production host."""

    def __init__(self, root: Path, *, state_dir: Path | None = None):
        configured = os.environ.get('SELLEMY_PUBLISH_GATE_STATE_DIR', '').strip()
        self.root = root
        self.state_dir = state_dir or (
            Path(configured).expanduser()
            if configured
            else Path.home() / 'Library' / 'Application Support' / 'Sellemy' / 'publish-gate'
        )
        self.lock_path = self.state_dir / 'publish.lock'

    @contextmanager
    def acquire(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open('a+', encoding='utf-8') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                evidence = sync_clean_main(self.root)
                evidence['lock_path'] = str(self.lock_path)
                yield evidence
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
