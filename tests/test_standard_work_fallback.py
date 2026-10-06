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

    def test_completed_accepted_task_returns_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            canonical=root/'canonical'
            prompt='same'
            import hashlib
            digest=hashlib.sha256(('planning\n'+prompt).encode()).hexdigest()[:16].upper()
            task_id=f'TASK-BU002-STANDARD-FALLBACK-PLANNING-{digest}'
            task_dir=canonical/'tasks'/task_id
            hist=task_dir/'history'
            hist.mkdir(parents=True)
            (task_dir/'task.json').write_text('{}')
            result={'candidates':[{'slug':'x-topic'}]}
            (hist/'000000000001.json').write_text(json.dumps({'status':'pending','detail':{}}))
            (hist/'000000000002.json').write_text(json.dumps({'status':'review_required','detail':{'stage':'planning','result':result}}))
            (hist/'000000000003.json').write_text(json.dumps({'status':'completed','message_id':'accept','detail':{'decision':'ACCEPT'}}))
            env={'SELLEMY_AI_OS_ROOT':str(root/'ai-os'),'SELLEMY_AI_OS_PYTHON':'/fake/python','SELLEMY_AI_OS_CANONICAL_ROOT':str(canonical)}
            with patch.dict('os.environ',env,clear=True), patch.object(swf.subprocess,'run') as run:
                actual=swf.request_standard_work_fallback(stage='planning',prompt=prompt,schema={'type':'object'})
            self.assertEqual(actual,result)
            run.assert_not_called()

    def test_completed_without_accept_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            canonical=root/'canonical'
            prompt='same'
            import hashlib
            digest=hashlib.sha256(('writer\n'+prompt).encode()).hexdigest()[:16].upper()
            task_id=f'TASK-BU002-STANDARD-FALLBACK-WRITER-{digest}'
            task_dir=canonical/'tasks'/task_id
            hist=task_dir/'history'
            hist.mkdir(parents=True)
            (task_dir/'task.json').write_text('{}')
            (hist/'000000000001.json').write_text(json.dumps({'status':'review_required','detail':{'stage':'writer','result':{'slug':'x'}}}))
            (hist/'000000000002.json').write_text(json.dumps({'status':'completed','message_id':'reject','detail':{'decision':'REJECT'}}))
            env={'SELLEMY_AI_OS_ROOT':str(root/'ai-os'),'SELLEMY_AI_OS_PYTHON':'/fake/python','SELLEMY_AI_OS_CANONICAL_ROOT':str(canonical)}
            with patch.dict('os.environ',env,clear=True):
                with self.assertRaisesRegex(RuntimeError,'without ACCEPT'):
                    swf.request_standard_work_fallback(stage='writer',prompt=prompt,schema={'type':'object'})

if __name__=='__main__': unittest.main()
