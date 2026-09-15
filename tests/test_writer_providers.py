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

    def test_availability_error_uses_certified_secondary_once(self):
        with patch.dict('os.environ', self.env, clear=True), patch.object(writer_runtime.subprocess, 'run', side_effect=[completed(1, stderr='429 rate limit'), completed()]) as run:
            _payload, metadata = writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(run.call_count, 2)
        self.assertTrue(metadata['fallback_used'])
        self.assertEqual(metadata['writer_model_used'], 'secondary:secondary-model')
        self.assertEqual(metadata['writer_attempt_count'], 2)

    def test_quality_or_schema_error_never_falls_back(self):
        bad = subprocess.CompletedProcess([], 0, stdout='{}', stderr='')
        with patch.dict('os.environ', self.env, clear=True), patch.object(writer_runtime.subprocess, 'run', return_value=bad) as run:
            with self.assertRaises(writer_runtime.WriterInvocationError):
                writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(run.call_count, 1)

    def test_uncertified_secondary_is_not_used(self):
        env = {**self.env, 'SELLEMY_WRITER_SECONDARY_CERTIFIED': 'false'}
        with patch.dict('os.environ', env, clear=True), patch.object(writer_runtime.subprocess, 'run', return_value=completed(1, stderr='service unavailable')) as run:
            with self.assertRaises(writer_runtime.WriterInvocationError):
                writer_runtime.invoke_writer({}, valid_evidence())
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
