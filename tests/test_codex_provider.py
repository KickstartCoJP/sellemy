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
            binding = {'current_url': '00000000-0000-4000-8000-000000000001', 'binding_revision': 7}
            calls = []
            def fake_queue(command, model, thread_id, root_arg, prompt, **kwargs):
                calls.append((thread_id, prompt, kwargs))
                return '{"ok": true}', {'input_tokens': 10, 'cached_input_tokens': 4, 'cache_write_input_tokens': 0, 'output_tokens': 2, 'reasoning_output_tokens': 0, 'uncached_input_tokens': 6}
            with patch.object(codex_provider, '_member_binding', return_value=binding), \
                 patch.object(codex_provider, '_queue_turn', side_effect=fake_queue):
                one, meta1 = codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'first', timeout=30, root=root, surface_key='bu-codex-sellemy-writer')
                two, meta2 = codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'second', timeout=30, root=root, surface_key='bu-codex-sellemy-writer')
            self.assertTrue(one['ok'] and two['ok'])
            self.assertEqual(meta1['session_id'], binding['current_url'])
            self.assertEqual(meta2['member_binding_revision'], 7)
            self.assertEqual(meta2['session_turn'], 2)
            self.assertEqual([x[0] for x in calls], [binding['current_url'], binding['current_url']])

    def test_member_binding_change_refreshes_old_thread_before_new_work(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bindings = [
                {'current_url': '00000000-0000-4000-8000-000000000001', 'binding_revision': 1},
                {'current_url': '00000000-0000-4000-8000-000000000002', 'binding_revision': 2},
            ]
            calls = []
            responses = iter([
                ('{"ok": true}', {}),
                ('{"learned":"比較軸ごとの差を先に整理すると説明の重複が減る。"}', {}),
                ('{"ok": true}', {}),
            ])
            def fake_queue(command, model, thread_id, root_arg, prompt, **kwargs):
                calls.append(thread_id)
                return next(responses)
            with patch.object(codex_provider, '_member_binding', side_effect=bindings), \
                 patch.object(codex_provider, '_queue_turn', side_effect=fake_queue):
                codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'first', timeout=30, root=root, surface_key='bu-codex-sellemy-writer')
                _, meta = codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'second', timeout=30, root=root, surface_key='bu-codex-sellemy-writer')
            self.assertTrue(meta['member_binding_changed'])
            self.assertTrue(meta['brief_refreshed'])
            self.assertEqual(meta['session_id'], bindings[1]['current_url'])
            self.assertEqual(calls, [bindings[0]['current_url'], bindings[0]['current_url'], bindings[1]['current_url']])
            self.assertIn('比較軸ごとの差を先に整理', (root / '.runtime/sellemy-codex/brief.md').read_text())

    def test_brief_checkpoint_does_not_change_member_thread(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binding = {'current_url': '00000000-0000-4000-8000-000000000001', 'binding_revision': 3}
            calls = []
            responses = iter([
                ('{"ok": true}', {}),
                ('{"learned":"差分だけを具体化する。"}', {}),
                ('{"ok": true}', {}),
            ])
            def fake_queue(command, model, thread_id, root_arg, prompt, **kwargs):
                calls.append(thread_id)
                return next(responses)
            with patch.object(codex_provider, '_member_binding', return_value=binding), \
                 patch.object(codex_provider, '_queue_turn', side_effect=fake_queue):
                codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'first', timeout=30, root=root, surface_key='bu-codex-sellemy-planning', max_turns=1)
                _, meta = codex_provider.generate_persistent(('/bin/codex',), 'model', schema, 'second', timeout=30, root=root, surface_key='bu-codex-sellemy-planning', max_turns=1)
            self.assertFalse(meta['member_binding_changed'])
            self.assertTrue(meta['brief_refreshed'])
            self.assertEqual(meta['session_id'], binding['current_url'])
            self.assertEqual(calls, [binding['current_url']] * 3)

    def test_queue_turn_uses_synchronous_exec_resume(self):
        stdout = '\n'.join([
            json.dumps({'type': 'thread.started', 'thread_id': 'thread-1'}),
            json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"ok":true}'}}),
            json.dumps({'type': 'turn.completed', 'usage': {
                'input_tokens': 100, 'cached_input_tokens': 60, 'cache_write_input_tokens': 0,
                'output_tokens': 10, 'reasoning_output_tokens': 2,
            }}),
        ])
        completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr='')
        with tempfile.TemporaryDirectory() as tmp, patch.object(codex_provider.subprocess, 'run', return_value=completed) as run:
            final, usage = codex_provider._queue_turn(
                ('/bin/codex',), 'model', 'thread-1', Path(tmp), 'PROMPT', timeout=30, effort='low')
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ['/bin/codex', 'exec', 'resume'])
        self.assertIn('thread-1', args)
        self.assertEqual(run.call_args.kwargs['input'], 'PROMPT')
        self.assertEqual(final, '{"ok":true}')
        self.assertEqual(usage['total_tokens'], 110)
        self.assertEqual(usage['uncached_input_tokens'], 40)

    def test_designer_uses_separate_brief_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, writer_brief = codex_provider._runtime_paths(root, 'bu-codex-sellemy-writer')
            _, designer_brief = codex_provider._runtime_paths(root, codex_provider.DESIGNER_SURFACE)
            self.assertEqual(writer_brief.name, 'brief.md')
            self.assertEqual(designer_brief.name, 'designer-brief.md')

    def test_designer_brief_update_archives_and_increments_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'designer-brief.md'
            path.write_text('# Sellemy Designer Brief\n\nVersion: 7\n\n## Learned\n- old\n\n## Brief maintenance\n- keep compact\n', encoding='utf-8')
            self.assertTrue(codex_provider._update_designer_brief(path, '- new'))
            updated = path.read_text(encoding='utf-8')
            self.assertIn('Version: 8', updated)
            self.assertIn('## Learned\n- new', updated)
            archived = path.parent / 'archive' / 'designer-brief-v007.md'
            self.assertTrue(archived.is_file())
            self.assertIn('Version: 7', archived.read_text(encoding='utf-8'))
            self.assertFalse(codex_provider._update_designer_brief(path, '- new'))
            self.assertIn('Version: 8', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
