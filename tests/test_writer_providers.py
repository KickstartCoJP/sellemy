from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import writer_runtime
from codex_provider import CodexProviderError
from fixtures import valid_evidence, valid_payload


def completed(returncode=0, *, stderr='', payload=None):
    stdout = json.dumps({'structured_output': payload or valid_payload(), 'modelUsage': 'test-model'})
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class WriterProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            'SELLEMY_WRITER_PRIMARY_COMMAND': '/bin/codex',
            'SELLEMY_WRITER_PRIMARY_KIND': 'codex',
            'SELLEMY_WRITER_PRIMARY_MODEL': 'codex-model',
            'SELLEMY_WRITER_SECONDARY_COMMAND': '/bin/claude',
            'SELLEMY_WRITER_SECONDARY_KIND': 'claude',
            'SELLEMY_WRITER_SECONDARY_MODEL': 'sonnet',
            'SELLEMY_WRITER_SECONDARY_CERTIFIED': 'true',
        }

    def test_codex_primary_success_does_not_call_claude(self):
        codex_meta = {'surface': 'bu-codex-sellemy-writer', 'session_id': 'sid', 'session_turn': 3}
        with patch.dict('os.environ', self.env, clear=True), \
             patch.object(writer_runtime, 'codex_generate_persistent', return_value=(valid_payload(), codex_meta)) as codex, \
             patch.object(writer_runtime.subprocess, 'run') as claude:
            payload, metadata = writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(payload, valid_payload())
        codex.assert_called_once()
        claude.assert_not_called()
        self.assertEqual(metadata['writer_provider_used'], 'codex')
        self.assertFalse(metadata['fallback_used'])
        self.assertTrue(metadata['session_persisted'])
        self.assertEqual(metadata['surface'], 'bu-codex-sellemy-writer')

    def test_codex_failure_falls_back_to_claude(self):
        with patch.dict('os.environ', self.env, clear=True), \
             patch.object(writer_runtime, 'codex_generate_persistent', side_effect=CodexProviderError('limit')), \
             patch.object(writer_runtime.subprocess, 'run', return_value=completed()) as claude:
            payload, metadata = writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(payload, valid_payload())
        self.assertEqual(claude.call_count, 1)
        self.assertTrue(metadata['fallback_used'])
        self.assertEqual(metadata['writer_provider_used'], 'claude')
        self.assertEqual(metadata['writer_attempt_count'], 2)

    def test_codex_failure_without_certified_secondary_fails_closed(self):
        env = dict(self.env)
        env['SELLEMY_WRITER_SECONDARY_CERTIFIED'] = 'false'
        with patch.dict('os.environ', env, clear=True), \
             patch.object(writer_runtime, 'codex_generate_persistent', side_effect=CodexProviderError('limit')):
            with self.assertRaisesRegex(writer_runtime.WriterInvocationError, 'certified Secondary Writer unavailable'):
                writer_runtime.invoke_writer({}, valid_evidence())

    def test_claude_secondary_schema_failure_fails_closed(self):
        bad = subprocess.CompletedProcess([], 0, stdout='{}', stderr='')
        with patch.dict('os.environ', self.env, clear=True), \
             patch.object(writer_runtime, 'codex_generate_persistent', side_effect=CodexProviderError('limit')), \
             patch.object(writer_runtime.subprocess, 'run', return_value=bad):
            with self.assertRaises(writer_runtime.WriterInvocationError):
                writer_runtime.invoke_writer({}, valid_evidence())


if __name__ == '__main__':
    unittest.main()
