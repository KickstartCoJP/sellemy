from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
import standard_work_fallback as swf

class StandardWorkFallbackTests(unittest.TestCase):
    def test_creates_deterministic_bu_work4_task_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            env={
                'SELLEMY_AI_OS_ROOT':str(root/'ai-os'),
                'SELLEMY_AI_OS_PYTHON':'/fake/python',
                'SELLEMY_AI_OS_CANONICAL_ROOT':str(root/'canonical'),
            }
            (root/'ai-os').mkdir()
            with patch.dict('os.environ',env,clear=True), patch.object(swf.subprocess,'run') as run:
                run.return_value=type('R',(),{'returncode':0,'stderr':'','stdout':'{}'})()
                with self.assertRaises(swf.StandardWorkFallbackPending) as cm:
                    swf.request_standard_work_fallback(stage='writer',prompt='same',schema={'type':'object'})
            payload=json.loads(run.call_args.kwargs['input'])
            self.assertEqual(payload['project_id'],'BU-002')
            self.assertEqual(payload['owner_role_id'],'sellemy-ops')
            self.assertEqual(payload['executor_role_id'],'bu-work4')
            self.assertEqual(payload['task_id'],cm.exception.task_id)
            self.assertIn('non-code task must not fall back to Codex',payload['initial_detail']['sellemy_standard_fallback_request']['reason'])

if __name__=='__main__': unittest.main()
