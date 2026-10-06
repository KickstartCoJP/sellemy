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

    def test_persistent_session_resolves_canonical_member_binding(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []

            def fake_run(args, **kwargs):
                calls.append(args)
                Path(args[args.index('--output-last-message') + 1]).write_text('{"ok": true}')
                return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

            binding = {'current_url': '00000000-0000-4000-8000-000000000001', 'binding_revision': 7}
            with patch.object(codex_provider, '_member_binding', return_value=binding), \
                 patch.object(codex_provider.subprocess, 'run', side_effect=fake_run):
                one, meta1 = codex_provider.generate_persistent(
                    ('/bin/codex',), 'model', schema, 'first', timeout=30, root=root,
                    surface_key='bu-codex-sellemy-writer')
                two, meta2 = codex_provider.generate_persistent(
                    ('/bin/codex',), 'model', schema, 'second', timeout=30, root=root,
                    surface_key='bu-codex-sellemy-writer')
            self.assertTrue(one['ok'] and two['ok'])
            self.assertEqual(meta1['session_id'], binding['current_url'])
            self.assertEqual(meta2['member_binding_revision'], 7)
            self.assertEqual(meta2['session_turn'], 2)
            self.assertTrue(all('resume' in call for call in calls))
            self.assertTrue(all(binding['current_url'] in call for call in calls))

    def test_member_binding_change_refreshes_old_thread_before_new_work(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []
            bindings = [
                {'current_url': '00000000-0000-4000-8000-000000000001', 'binding_revision': 1},
                {'current_url': '00000000-0000-4000-8000-000000000002', 'binding_revision': 2},
            ]

            def fake_run(args, **kwargs):
                calls.append(args)
                out = Path(args[args.index('--output-last-message') + 1])
                strict = json.loads(Path(args[args.index('--output-schema') + 1]).read_text())
                if 'learned' in strict.get('properties', {}):
                    out.write_text('{"learned":"比較軸ごとの差を先に整理すると説明の重複が減る。"}')
                else:
                    out.write_text('{"ok": true}')
                return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

            with patch.object(codex_provider, '_member_binding', side_effect=bindings), \
                 patch.object(codex_provider.subprocess, 'run', side_effect=fake_run):
                codex_provider.generate_persistent(
                    ('/bin/codex',), 'model', schema, 'first', timeout=30, root=root,
                    surface_key='bu-codex-sellemy-writer')
                _, meta = codex_provider.generate_persistent(
                    ('/bin/codex',), 'model', schema, 'second', timeout=30, root=root,
                    surface_key='bu-codex-sellemy-writer')
            self.assertTrue(meta['member_binding_changed'])
            self.assertTrue(meta['brief_refreshed'])
            self.assertEqual(meta['session_id'], bindings[1]['current_url'])
            self.assertIn(bindings[0]['current_url'], calls[1])
            self.assertIn(bindings[1]['current_url'], calls[2])
            self.assertIn('比較軸ごとの差を先に整理', (root / '.runtime/sellemy-codex/brief.md').read_text())

    def test_brief_checkpoint_does_not_change_member_thread(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []
            binding = {'current_url': '00000000-0000-4000-8000-000000000001', 'binding_revision': 3}

            def fake_run(args, **kwargs):
                calls.append(args)
                out = Path(args[args.index('--output-last-message') + 1])
                strict = json.loads(Path(args[args.index('--output-schema') + 1]).read_text())
                out.write_text('{"learned":"差分だけを具体化する。"}' if 'learned' in strict.get('properties', {}) else '{"ok": true}')
                return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

            with patch.object(codex_provider, '_member_binding', return_value=binding), \
                 patch.object(codex_provider.subprocess, 'run', side_effect=fake_run):
                codex_provider.generate_persistent(
                    ('/bin/codex',), 'model', schema, 'first', timeout=30, root=root,
                    surface_key='bu-codex-sellemy-planning', max_turns=1)
                _, meta = codex_provider.generate_persistent(
                    ('/bin/codex',), 'model', schema, 'second', timeout=30, root=root,
                    surface_key='bu-codex-sellemy-planning', max_turns=1)
            self.assertFalse(meta['member_binding_changed'])
            self.assertTrue(meta['brief_refreshed'])
            self.assertEqual(meta['session_id'], binding['current_url'])
            self.assertTrue(all(binding['current_url'] in call for call in calls))



if __name__ == '__main__':
    unittest.main()
