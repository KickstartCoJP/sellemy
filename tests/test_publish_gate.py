from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))

from publish_gate import (  # noqa: E402
    PublishGateError, recover_unpublished_commit_after_remote_race, sync_clean_main,
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


class PublishGateSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.remote = base / 'remote.git'
        self.writer = base / 'writer'
        self.other = base / 'other'
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(self.remote), str(self.writer)], check=True, capture_output=True)
        for repo in (self.writer,):
            git(repo, 'config', 'user.email', 'test@example.com')
            git(repo, 'config', 'user.name', 'Test')
        (self.writer / 'seed.txt').write_text('seed\n')
        git(self.writer, 'add', 'seed.txt')
        git(self.writer, 'commit', '-m', 'seed')
        git(self.writer, 'branch', '-M', 'main')
        git(self.writer, 'push', '-u', 'origin', 'main')
        subprocess.run(['git', '--git-dir', str(self.remote), 'symbolic-ref', 'HEAD', 'refs/heads/main'], check=True)
        subprocess.run(['git', 'clone', str(self.remote), str(self.other)], check=True, capture_output=True)
        git(self.other, 'config', 'user.email', 'test@example.com')
        git(self.other, 'config', 'user.name', 'Test')

    def tearDown(self):
        self.temp.cleanup()

    def test_clean_behind_main_fast_forwards(self):
        (self.other / 'remote.txt').write_text('remote\n')
        git(self.other, 'add', 'remote.txt')
        git(self.other, 'commit', '-m', 'remote advance')
        git(self.other, 'push', 'origin', 'main')
        before = git(self.writer, 'rev-parse', 'HEAD')
        evidence = sync_clean_main(self.writer)
        self.assertTrue(evidence['fast_forwarded'])
        self.assertNotEqual(before, evidence['head_after'])
        self.assertEqual(git(self.writer, 'rev-parse', 'HEAD'), git(self.writer, 'rev-parse', 'origin/main'))
        self.assertEqual(git(self.writer, 'status', '--porcelain'), '')

    def test_dirty_tree_fails_closed(self):
        (self.writer / 'dirty.txt').write_text('dirty\n')
        with self.assertRaises(PublishGateError):
            sync_clean_main(self.writer)


    def test_remote_race_recovery_discards_only_unpublished_candidate(self):
        (self.writer / 'candidate.txt').write_text('candidate\n')
        git(self.writer, 'add', 'candidate.txt')
        git(self.writer, 'commit', '-m', 'candidate')
        candidate = git(self.writer, 'rev-parse', 'HEAD')
        (self.other / 'remote.txt').write_text('remote\n')
        git(self.other, 'add', 'remote.txt')
        git(self.other, 'commit', '-m', 'remote winner')
        git(self.other, 'push', 'origin', 'main')
        evidence = recover_unpublished_commit_after_remote_race(self.writer, candidate)
        self.assertFalse(evidence['already_published'])
        self.assertEqual(evidence['discarded_unpublished_commit'], candidate)
        self.assertEqual(git(self.writer, 'rev-parse', 'HEAD'), git(self.writer, 'rev-parse', 'origin/main'))
        self.assertFalse((self.writer / 'candidate.txt').exists())
        self.assertTrue((self.writer / 'remote.txt').exists())
        self.assertEqual(git(self.writer, 'status', '--porcelain'), '')

    def test_diverged_main_fails_without_rewrite(self):
        (self.writer / 'local.txt').write_text('local\n')
        git(self.writer, 'add', 'local.txt')
        git(self.writer, 'commit', '-m', 'local advance')
        (self.other / 'remote.txt').write_text('remote\n')
        git(self.other, 'add', 'remote.txt')
        git(self.other, 'commit', '-m', 'remote advance')
        git(self.other, 'push', 'origin', 'main')
        local_before = git(self.writer, 'rev-parse', 'HEAD')
        with self.assertRaises(PublishGateError):
            sync_clean_main(self.writer)
        self.assertEqual(git(self.writer, 'rev-parse', 'HEAD'), local_before)


if __name__ == '__main__':
    unittest.main()
