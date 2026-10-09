"""Offline Stage 3 suite: no sockets, external programs, pushes or destructive git.

Run from runtime: PYTHONDONTWRITEBYTECODE=1 <python> tests/run_quality_offline.py
"""
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'pipeline'), str(ROOT / 'tests')]
MODULES = [
    'test_comparison_acceptance', 'test_planning_and_selection', 'test_growth_runtime',
    'test_growth_recovery', 'test_writer_providers', 'test_responses_provider',
    'test_review_gate', 'test_qa', 'test_payload_schema', 'test_renderer',
    'test_publication_unit', 'test_adaptive_publish', 'test_codex_provider',
]
original_popen = subprocess.Popen


def offline_popen(args, *a, **kw):
    # Tests may read this checkout's git status. All production and provider
    # mutations must be mocked; never execute a push even against a temp remote.
    if isinstance(args, (list, tuple)) and len(args) == 2 and args[0] == sys.executable and args[1] in {'pipeline/grow.py', 'pipeline/run.py'}:
        return original_popen(args, *a, **kw)
    if not isinstance(args, (list, tuple)) or not args or args[0] != 'git':
        raise AssertionError(f'offline suite blocked subprocess: {args!r}')
    if len(args) < 2 or args[1] not in {'status', 'rev-parse', 'ls-files', 'diff'}:
        raise AssertionError(f'offline suite blocked git mutation: {args!r}')
    return original_popen(args, *a, **kw)


def no_network(*a, **kw):
    raise AssertionError('offline suite blocked network access')


if __name__ == '__main__':
    # Existing provider tests supply fake credentials inside patched transports.
    clean_env = {k: v for k, v in os.environ.items()
                 if not any(x in k for x in ('API_KEY', 'TOKEN', 'SECRET', 'SELLEMY_'))}
    with patch.dict(os.environ, clean_env, clear=True), \
         patch.object(socket.socket, 'connect', no_network), \
         patch.object(socket, 'create_connection', no_network), \
         patch.object(subprocess, 'Popen', offline_popen):
        suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:] or MODULES)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
