import json
import subprocess
import sys
import tempfile
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
            self.assertEqual(args[-1], '-')
            self.assertEqual(kwargs['input'], 'prompt')
            strict = json.loads(Path(args[args.index('--output-schema') + 1]).read_text())
            self.assertEqual(strict['properties']['items']['minItems'], 2)
            self.assertEqual(strict['properties']['items']['items']['required'], ['name'])
            Path(args[args.index('--output-last-message') + 1]).write_text('{"items": []}')
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

        with patch.object(codex_provider.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(codex_provider.generate(('/bin/codex',), 'model', schema, 'prompt', timeout=30), {'items': []})

    def test_persistent_session_resumes_same_surface(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []

            def fake_run(args, **kwargs):
                calls.append(args)
                out = Path(args[args.index('--output-last-message') + 1])
                out.write_text('{"ok": true}')
                stdout = '{"type":"thread.started","thread_id":"sid-1"}\n' if 'resume' not in args else ''
                return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr='')

            with patch.object(codex_provider.subprocess, 'run', side_effect=fake_run):
                one, meta1 = codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'first', timeout=30, root=root)
                two, meta2 = codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'second', timeout=30, root=root)
            self.assertTrue(one['ok'] and two['ok'])
            self.assertEqual(meta1['session_id'], 'sid-1')
            self.assertEqual(meta2['session_id'], 'sid-1')
            self.assertEqual(meta2['session_turn'], 2)
            self.assertIn('resume', calls[1])
            self.assertIn('sid-1', calls[1])
            self.assertTrue((root / '.runtime/sellemy-codex/brief.md').exists())

    def test_rotation_refreshes_compact_brief_before_new_session(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start_count = 0

            def fake_run(args, **kwargs):
                nonlocal start_count
                out = Path(args[args.index('--output-last-message') + 1])
                strict = json.loads(Path(args[args.index('--output-schema') + 1]).read_text())
                if 'learned' in strict.get('properties', {}):
                    out.write_text('{"learned":"比較軸ごとの差を先に整理すると説明の重複が減る。"}')
                    return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
                out.write_text('{"ok": true}')
                if 'resume' not in args:
                    start_count += 1
                    return subprocess.CompletedProcess(args, 0, stdout=json.dumps({'type':'thread.started','thread_id':f'sid-{start_count}'})+'\n', stderr='')
                return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

            with patch.object(codex_provider.subprocess, 'run', side_effect=fake_run):
                codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'first', timeout=30, root=root, max_turns=1)
                _, meta = codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'second', timeout=30, root=root, max_turns=1)
            self.assertTrue(meta['session_rotated'])
            self.assertTrue(meta['brief_refreshed_before_rotation'])
            self.assertEqual(meta['session_id'], 'sid-2')
            brief = (root / '.runtime/sellemy-codex/brief.md').read_text()
            self.assertIn('比較軸ごとの差を先に整理', brief)
            self.assertIn('Fixed rules', brief)


if __name__ == '__main__':
    unittest.main()
