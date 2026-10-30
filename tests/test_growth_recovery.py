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

    def test_corrupt_states_are_held_without_losing_bytes_or_starving_healthy_jobs(self):
        self._job('healthy-job')
        bad_inputs = {
            'bad-json': b'{', 'empty-json': b'', 'array-state': b'[]',
            'null-state': b'null',
            'bad-field-type': json.dumps({'schema_version': 1, 'slug': 'bad-field-type',
                                          'status': 'ACTIVE', 'current_stage': 'WRITER_PENDING',
                                          'created_at': '2026-10-25T01:00:00+00:00',
                                          'updated_at': '2026-10-25T01:00:00+00:00',
                                          'next_retry_at': []}).encode(),
            'wrong-slug': json.dumps({'slug': 'other', 'status': 'PUBLISHED',
                                     'current_stage': 'PUBLISHED'}).encode(),
        }
        for slug, contents in bad_inputs.items():
            directory = gr.JOBS_ROOT / slug
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'state.json').write_bytes(contents)
        missing = gr.JOBS_ROOT / 'missing-state'
        missing.mkdir(parents=True, exist_ok=True)
        (missing / 'topic.json').write_text('{"category": "gadget"}', encoding='utf-8')
        rows = gr.all_jobs()
        self.assertEqual(len(rows), 8)
        self.assertEqual(gr.eligible_job()['slug'], 'healthy-job')
        for slug, original in bad_inputs.items():
            held = gr.load_job(slug)
            self.assertEqual((held['status'], held['current_stage']), ('ACTIVE', 'STATE_HOLD'))
            self.assertIsNone(held['next_retry_at'])
            self.assertEqual(Path(held['invalid_state_backup']).read_bytes(), original)
            self.assertNotEqual(gr.eligible_job(now=datetime.now(timezone.utc))['slug'], slug)
        self.assertEqual(gr.load_job('missing-state')['current_stage'], 'STATE_HOLD')
        event_counts = {slug: (gr.job_dir(slug) / 'history.jsonl').read_text().count('STATE_CHECKPOINT_INVALID_HOLD')
                        for slug in bad_inputs}
        gr.all_jobs()
        self.assertTrue(all((gr.job_dir(slug) / 'history.jsonl').read_text().count('STATE_CHECKPOINT_INVALID_HOLD') == count
                            for slug, count in event_counts.items()))

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

    def test_writer_tenth_consecutive_failure_is_failed_not_discarded(self):
        self._job()
        row=None
        for _ in range(9):
            row=gr.mark_failure('same-slug',stage='writer',failure_class='role_quality',failure_code='QA',reason='bad',role_return=True,role='writer')
            self.assertEqual(row['status'], 'ACTIVE')
        row=gr.mark_failure('same-slug',stage='writer',failure_class='role_quality',failure_code='QA',reason='bad',role_return=True,role='writer')
        self.assertEqual(row['status'],'FAILED')
        self.assertEqual(row['current_stage'],'WRITER_FAILED')
        self.assertEqual(gr.read_artifact('same-slug','ceo-inspection-required.json')['failure_count'],10)
        self.assertIsNone(gr.eligible_job(now=datetime.now(timezone.utc)))
        self.assertIsNone(row['discard_reason'])

    def test_terminal_writer_failure_never_automatically_reopens(self):
        self._job()
        gr.write_artifact('same-slug', 'selection.json', {'axes': [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}]})
        for _ in range(10):
            row = gr.mark_failure('same-slug', stage='writer', failure_class='role_quality',
                                  failure_code='QA', reason='bad', role_return=True, role='writer')
        self.assertEqual((row['status'], row['current_stage']), ('FAILED', 'WRITER_FAILED'))
        self.assertEqual(gr.recover_legacy_failed_jobs(), [])
        self.assertEqual((gr.load_job('same-slug')['status'], gr.load_job('same-slug')['current_stage']),
                         ('FAILED', 'WRITER_FAILED'))
        self.assertIsNone(gr.eligible_job())

    def test_identical_product_reselection_is_held_without_repeating_qa(self):
        self._job('stuck')
        gr.write_artifact('stuck', 'product-search-progress.json', {
            'candidates': [{'asin': 'A1'}], 'queries': [{'query': 'x'}],
            'researched_asins': ['A1'], 'reviewed_sets': ['A1|B2']})
        first = gr.request_product_reselection('stuck', ['six evidence-ready products absent'])
        self.assertEqual(first['retry_count_by_stage']['product'], 1)
        second = gr.request_product_reselection('stuck', ['six evidence-ready products absent'])
        self.assertEqual(second['current_stage'], 'PRODUCT_HOLD')
        self.assertEqual(second['retry_count_by_stage']['product'], 1)
        self.assertIsNone(second['next_retry_at'])
        self.assertIsNone(gr.eligible_job())
        self._job('healthy')
        self.assertEqual(gr.eligible_job()['slug'], 'healthy')

    def test_query_variation_without_new_evidence_is_held(self):
        self._job('query-only')
        progress={'candidates':[{'asin':'A1','sources':[{'verified':True,'excerpt':'fixed'}]}], 'queries':[{'query':'first'}]}
        gr.write_artifact('query-only','product-search-progress.json',progress)
        gr.request_product_reselection('query-only',['unchanged facts'])
        progress['queries'].append({'query':'second'})
        gr.write_artifact('query-only','product-search-progress.json',progress)
        held=gr.request_product_reselection('query-only',['unchanged facts'])
        self.assertEqual(held['current_stage'],'PRODUCT_HOLD')
        self.assertEqual(held['retry_count_by_stage']['product'],1)

    def test_changed_product_candidates_do_not_trigger_hold(self):
        self._job('changed')
        gr.write_artifact('changed', 'product-search-progress.json', {'candidates': [{'asin':'A1','sources':[{'verified':True,'excerpt':'fact A1'}]}]})
        gr.request_product_reselection('changed', ['incomplete'])
        gr.write_artifact('changed', 'product-search-progress.json', {'candidates': [{'asin':'A1','sources':[{'verified':True,'excerpt':'fact A1'}]}, {'asin':'B2','sources':[{'verified':True,'excerpt':'fact B2'}]}]})
        row = gr.request_product_reselection('changed', ['incomplete'])
        self.assertEqual(row['current_stage'], 'WRITER_PENDING')
        self.assertEqual(row['retry_count_by_stage']['product'], 2)

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
