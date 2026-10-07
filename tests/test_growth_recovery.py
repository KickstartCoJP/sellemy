import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import growth_recovery as gr


class GrowthRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(gr, 'STATE_ROOT', root),
            patch.object(gr, 'JOBS_ROOT', root / 'jobs'),
            patch.object(gr, 'EVENTS_PATH', root / 'events.jsonl'),
            patch.object(gr, 'EXECUTION_EVENTS_PATH', root / 'execution-events.jsonl'),
        ]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def _job(self, slug='same-slug'):
        topic={'slug':slug,'category':'gadget','title':'T','query':'Q','comparison_axes':[{'id':'a','label':'A'}]}
        evidence={'slug':slug,'products':[{'asin':str(i)} for i in range(6)]}
        return gr.create_job(topic=topic,evidence=evidence,selection={},planning_provider={},viability_probes=[])

    def test_six_product_commit_creates_durable_writer_job(self):
        row=self._job()
        self.assertEqual(row['current_stage'],'WRITER_PENDING')
        self.assertEqual(gr.eligible_job(now=datetime.now(timezone.utc))['slug'],'same-slug')
        self.assertTrue((gr.job_dir('same-slug')/'evidence.json').is_file())

    def test_writer_failure_keeps_same_slug_and_feedback(self):
        self._job()
        payload={'slug':'same-slug'}; qa={'overall_pass':False,'main_in_range':False}
        gr.save_writer_result('same-slug',payload=payload,qa=qa,findings=['x'],feedback={'qa_failures':{'main_in_range':False}},metadata={'session_id':'s','session_turn':1},passed=False)
        row=gr.mark_failure('same-slug',stage='writer',failure_class='role_quality',failure_code='QA',reason='bad',role_return=True,role='writer')
        self.assertEqual(row['current_stage'],'QA_FAILED')
        self.assertEqual(row['retry_count_by_stage']['writer'],1)
        self.assertEqual(row['role_returns']['writer'],1)
        self.assertEqual(gr.read_artifact('same-slug','payload.json')['slug'],'same-slug')

    def test_writer_fifth_failure_is_failed_not_discarded(self):
        self._job()
        row=None
        for _ in range(5):
            row=gr.mark_failure('same-slug',stage='writer',failure_class='role_quality',failure_code='QA',reason='bad',role_return=True,role='writer')
        self.assertEqual(row['status'],'FAILED')
        self.assertEqual(row['current_stage'],'WRITER_FAILED')
        self.assertIsNone(gr.eligible_job(now=datetime.now(timezone.utc)))
        self.assertIsNone(row['discard_reason'])

    def test_runtime_retry_does_not_increment_role_returns(self):
        self._job()
        row=gr.mark_failure('same-slug',stage='eyecatch',failure_class='runtime_infrastructure',failure_code='ATTEST',reason='parser mismatch',role='designer')
        self.assertEqual(row['role_returns']['designer'],0)
        self.assertEqual(row['runtime_retries']['designer'],1)

    def test_execution_summary_counts_unique_projects_not_turns(self):
        self._job('a'); self._job('b')
        gr.record_execution(slug='a',role='writer',event_type='turn',stage='writer',metadata={'total_tokens':100})
        gr.record_execution(slug='a',role='writer',event_type='turn',stage='writer',metadata={'total_tokens':50})
        gr.record_execution(slug='a',role='writer',event_type='return',stage='writer')
        gr.record_execution(slug='b',role='writer',event_type='turn',stage='writer',metadata={'total_tokens':70})
        gr.record_execution(slug='b',role='writer',event_type='success',stage='writer')
        row=gr.summarize_execution()['roles'][0]
        self.assertEqual(row['projects'],2)
        self.assertEqual(row['turns'],3)
        self.assertEqual(row['returns'],1)
        self.assertEqual(row['tokens'],220)
        self.assertEqual(row['avg_tokens'],110.0)


if __name__ == '__main__': unittest.main()
