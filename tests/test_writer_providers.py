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
from fixtures import valid_evidence, valid_payload


def completed(returncode=0, *, stderr='', payload=None):
    stdout = json.dumps({'structured_output': payload or valid_payload(), 'modelUsage': 'test-model'})
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class WriterProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            'SELLEMY_WRITER_PRIMARY_COMMAND': '/bin/primary', 'SELLEMY_WRITER_PRIMARY_MODEL': 'primary-model',
            'SELLEMY_WRITER_SECONDARY_COMMAND': '/bin/secondary', 'SELLEMY_WRITER_SECONDARY_MODEL': 'secondary-model',
            'SELLEMY_WRITER_SECONDARY_CERTIFIED': 'true',
        }

    def test_primary_success_does_not_call_secondary(self):
        with patch.dict('os.environ', self.env, clear=True), patch.object(writer_runtime.subprocess, 'run', return_value=completed()) as run:
            _payload, metadata = writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(run.call_count, 1)
        self.assertFalse(metadata['fallback_used'])
        self.assertEqual(metadata['writer_model_requested'], 'primary:primary-model')

    def test_availability_error_routes_to_standard_work(self):
        with patch.dict('os.environ', self.env, clear=True), \
             patch.object(writer_runtime.subprocess, 'run', return_value=completed(1, stderr='429 rate limit')) as run, \
             patch.object(writer_runtime, 'request_standard_work_fallback', side_effect=RuntimeError('fallback-pending')) as fallback:
            with self.assertRaisesRegex(RuntimeError, 'fallback-pending'):
                writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(run.call_count, 1)
        fallback.assert_called_once()
        self.assertEqual(fallback.call_args.kwargs['stage'], 'writer')

    def test_quality_or_schema_error_never_falls_back(self):
        bad = subprocess.CompletedProcess([], 0, stdout='{}', stderr='')
        with patch.dict('os.environ', self.env, clear=True), patch.object(writer_runtime.subprocess, 'run', return_value=bad) as run:
            with self.assertRaises(writer_runtime.WriterInvocationError):
                writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(run.call_count, 1)

    def test_legacy_secondary_settings_do_not_change_standard_fallback(self):
        env = {**self.env, 'SELLEMY_WRITER_SECONDARY_CERTIFIED': 'false'}
        with patch.dict('os.environ', env, clear=True), \
             patch.object(writer_runtime.subprocess, 'run', return_value=completed(1, stderr='service unavailable')) as run, \
             patch.object(writer_runtime, 'request_standard_work_fallback', side_effect=RuntimeError('fallback-pending')) as fallback:
            with self.assertRaisesRegex(RuntimeError, 'fallback-pending'):
                writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(run.call_count, 1)
        fallback.assert_called_once()

    def test_claude_limit_invokes_standard_work_not_codex(self):
        env = {**self.env, 'SELLEMY_WRITER_SECONDARY_KIND': 'codex'}
        with patch.dict('os.environ', env, clear=True), \
             patch.object(writer_runtime.subprocess, 'run', return_value=completed(1, stderr='You have reached your weekly limit')) as primary, \
             patch.object(writer_runtime, 'request_standard_work_fallback', side_effect=RuntimeError('fallback-pending')) as fallback:
            with self.assertRaisesRegex(RuntimeError, 'fallback-pending'):
                writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(primary.call_count, 1)
        fallback.assert_called_once()

    def test_claude_content_failure_does_not_invoke_standard_work(self):
        with patch.dict('os.environ', self.env, clear=True), \
             patch.object(writer_runtime.subprocess, 'run', return_value=completed(1, stderr='invalid article schema')), \
             patch.object(writer_runtime, 'request_standard_work_fallback') as fallback:
            with self.assertRaises(writer_runtime.WriterInvocationError):
                writer_runtime.invoke_writer({}, valid_evidence())
        fallback.assert_not_called()


if __name__ == '__main__':
    unittest.main()
