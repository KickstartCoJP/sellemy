from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'tests'))

from eyecatch_adapter import EyecatchGenerationError, EyecatchGenerator
from fixtures import valid_evidence, valid_payload


def fake_png(width=1536, height=1024):
    return b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + width.to_bytes(4, 'big') + height.to_bytes(4, 'big') + b'\x08\x06\x00\x00\x00' + b'x' * 64


class _Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        return json.dumps(self.payload).encode()


class EyecatchAdapterTests(unittest.TestCase):
    def test_generation_is_ai_receipted_and_idempotent(self):
        payload, evidence = valid_payload(), valid_evidence()
        raw = fake_png()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'EYECATCH_TEST_TOKEN': 'secret'}):
            out = Path(tmp) / 'hero.png'; receipt = Path(tmp) / 'receipt.json'
            generator = EyecatchGenerator(endpoint='https://images.example/generate', token_env='EYECATCH_TEST_TOKEN', provider='test-provider', model='test-model')
            with patch('eyecatch_adapter.urllib.request.urlopen', return_value=_Response({'image_base64': base64.b64encode(raw).decode()})) as call:
                first = generator.generate(payload=payload, evidence=evidence, output=out, receipt_path=receipt)
                second = generator.generate(payload=payload, evidence=evidence, output=out, receipt_path=receipt)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(first, second)
            self.assertEqual(first['generation_method'], 'generative_ai')
            self.assertEqual((first['width'], first['height']), (1536, 1024))
            self.assertEqual(out.read_bytes(), raw)

    def test_missing_endpoint_or_token_fails_closed(self):
        payload, evidence = valid_payload(), valid_evidence()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'hero.png'; receipt = Path(tmp) / 'receipt.json'
            generator = EyecatchGenerator(endpoint='', token_env='', provider='', model='')
            with self.assertRaises(EyecatchGenerationError):
                generator.generate(payload=payload, evidence=evidence, output=out, receipt_path=receipt)

    def test_wrong_dimensions_fail_closed(self):
        payload, evidence = valid_payload(), valid_evidence()
        raw = fake_png(1024, 1024)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'EYECATCH_TEST_TOKEN': 'secret'}):
            out = Path(tmp) / 'hero.png'; receipt = Path(tmp) / 'receipt.json'
            generator = EyecatchGenerator(endpoint='https://images.example/generate', token_env='EYECATCH_TEST_TOKEN', provider='test-provider', model='test-model')
            with patch('eyecatch_adapter.urllib.request.urlopen', return_value=_Response({'image_base64': base64.b64encode(raw).decode()})):
                with self.assertRaises(EyecatchGenerationError):
                    generator.generate(payload=payload, evidence=evidence, output=out, receipt_path=receipt)


if __name__ == '__main__':
    unittest.main()
