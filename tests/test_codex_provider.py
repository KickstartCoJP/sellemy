from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import codex_provider


class CodexProviderTests(unittest.TestCase):
    def test_ephemeral_read_only_schema_output(self):
        schema = {'type': 'object', 'properties': {'items': {'type': 'array', 'minItems': 2,
                  'items': {'type': 'object', 'properties': {'name': {'type': 'string'}}}}}}

        def fake_run(args, **kwargs):
            self.assertIn('--ephemeral', args)
            self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
            self.assertEqual(args[args.index('--cd') + 1], str(Path(args[args.index('--output-schema') + 1]).parent))
            self.assertEqual(args[-1], '-')
            self.assertEqual(kwargs['input'], 'prompt')
            strict = json.loads(Path(args[args.index('--output-schema') + 1]).read_text())
            self.assertNotIn('minItems', strict['properties']['items'])
            self.assertEqual(strict['properties']['items']['items']['required'], ['name'])
            Path(args[args.index('--output-last-message') + 1]).write_text('{"items": []}')
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

        with patch.object(codex_provider.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(codex_provider.generate(('/bin/codex',), 'model', schema, 'prompt', timeout=30), {'items': []})


if __name__ == '__main__':
    unittest.main()
