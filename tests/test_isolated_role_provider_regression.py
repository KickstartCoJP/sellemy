"""Regression: all isolated Sellemy business roles must remain importable and executable."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))

class IsolatedRoleProviderRegression(unittest.TestCase):
    def test_role_imports_resolve(self):
        from codex_provider import generate_isolated
        from semantic_review import independent_review
        from planning_runtime import plan_next_topic
        from writer_runtime import invoke_writer
        for action in (generate_isolated, independent_review, plan_next_topic, invoke_writer):
            self.assertTrue(callable(action))

    def test_reviewer_uses_fresh_readonly_ephemeral_turn(self):
        import codex_provider
        from api_role_binding import require_codex_route
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'config').mkdir()
            (root/'config/sellemy-codex-canon-bootstrap.md').write_text('Canonical test route')
            events=[
                {'type':'thread.started','thread_id':'00000000-0000-4000-8000-000000000001'},
                {'type':'item.completed','item':{'type':'agent_message','text':'{"accepted":true}'}},
                {'type':'turn.completed','usage':{'input_tokens':9,'output_tokens':3}},
            ]
            stdout='\n'.join(json.dumps(e) for e in events)
            with patch('api_role_binding.require_codex_route',return_value=('/bin/codex',)), \
                 patch.object(codex_provider,'_member_binding',return_value={'binding_revision':1}), \
                 patch.object(codex_provider,'_required_effort',return_value='medium'), \
                 patch.object(codex_provider,'_run',return_value=subprocess.CompletedProcess([],0,stdout,'')) as runner, \
                 patch.object(codex_provider,'_append_usage'):
                data,meta=codex_provider.generate_isolated(
                    ('/bin/codex',),'gpt-6-sol',
                    {'type':'object','properties':{'accepted':{'type':'boolean'}}},
                    'Review current evidence',timeout=10,root=root,
                    surface_key='bu-codex-sellemy-reviewer',stage='independent_review'
                )
            self.assertEqual(data,{'accepted':True})
            self.assertFalse(meta['session_persisted'])
            command=runner.call_args.args[0]
            self.assertIn('--ephemeral',command)
            self.assertEqual(command[command.index('--sandbox')+1],'read-only')
            self.assertNotIn('resume',command)

if __name__=='__main__':
    unittest.main()
