"""Local-source legacy recovery admission tests. No production data or APIs."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import growth_recovery as recovery
import legacy_article_recovery as legacy


class LegacyArticleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'site'
        self.jobs = self.base / 'jobs'
        self.source = self.base / 'source'
        self.articles = self.source / 'articles'
        self.slug = 'sample-article'
        (self.articles / self.slug).mkdir(parents=True)
        (self.repo / 'article' / 'gadget').mkdir(parents=True)
        self.jobs.mkdir()
        (self.jobs / self.slug).mkdir()
        self._save(self.repo / 'article/gadget/sample-article.html', 'old')
        self._save(self.articles/self.slug/'body.json',
                   {'slug': self.slug, 'category': 'gadget', 'products': []})
        self._save(self.articles/self.slug/'evidence.json',
                   {'slug': self.slug, 'category': 'gadget', 'products': [
                       {'ref': f'P{i}', 'sources': [{'verified': True,
                         'url': f'https://example.org/p{i}', 'excerpt': f'Proven item {i}'}]}
                       for i in range(6)],
                    'eyecatch_image': '/img/sample-article/sample-article.png'})
        self._save(self.articles/self.slug/'selection.json',
                   {'slug': self.slug, 'products': []})
        self._save(self.articles/self.slug/'article.html', 'new')
        self._save(self.source/'final-validation.json', {'accepted_bodies': 1})
        self._save(self.jobs/self.slug/'topic.json',
                   {'slug': self.slug, 'category': 'gadget', 'title': 'Previous title'})
        self._save(self.jobs/self.slug/'evidence.json',
                   {'slug': self.slug, 'category': 'gadget',
                    'eyecatch_image': '/img/sample-article/sample-article.png'})
        self._save(self.jobs/self.slug/'state.json',
                   {'schema_version': 1, 'slug': self.slug, 'category': 'gadget',
                    'status': 'PUBLISHED', 'current_stage': 'PUBLISHED',
                    'created_at': '2026-10-10T00:00:00+00:00',
                    'updated_at': '2026-10-10T00:00:00+00:00',
                    'next_retry_at': None, 'published_commit': 'old'})
        self._save(self.jobs/self.slug/'chat-publish-approval.json',
                   {'publish_allowed': True})
        self.patches = [patch.object(recovery, 'JOBS_ROOT', self.jobs),
                        patch.object(recovery, 'EVENTS_PATH', self.base/'events.jsonl'),
                        patch.object(legacy, 'ROOT', self.repo),
                        patch.object(legacy, 'JOBS_ROOT', self.jobs)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _save(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) if not isinstance(data,str) else data)

    def test_admit_one_reuses_published_job_and_revokes_approval(self):
        self.assertEqual(legacy.candidates(self.source), [self.slug])
        result = legacy.import_one(self.source)
        self.assertTrue(result['admitted'])
        state = recovery.load_job(self.slug)
        self.assertEqual((state['status'], state['current_stage']),
                         ('ACTIVE', 'WRITER_PENDING'))
        self.assertEqual(state['source'], 'legacy_repair')
        self.assertEqual(recovery.read_artifact(self.slug, 'chat-publish-approval.json'), {})
        self.assertEqual((self.repo / 'article/gadget/sample-article.html').read_text(), 'old')
        self.assertEqual(recovery.read_artifact(self.slug, 'legacy-original-20261115/state.json')['status'], 'PUBLISHED')
        self.assertEqual(legacy.import_one(self.source), {'admitted': False})

    def test_incomplete_sources_route_to_planning_and_preserve_original_approval(self):
        evidence = json.loads((self.articles/self.slug/'evidence.json').read_text())
        evidence['products'][2]['sources'][0]['verified'] = False
        self._save(self.articles/self.slug/'evidence.json', evidence)
        self.assertTrue(legacy.has_pending(self.source))
        self.assertTrue(legacy.import_one(self.source)['replanning'])
        state = recovery.load_job(self.slug)
        self.assertEqual((state['status'], state['current_stage']), ('ACTIVE', 'PLANNING_PENDING'))
        self.assertEqual(recovery.read_artifact(self.slug, 'chat-publish-approval.json'),
                         {'publish_allowed': True})
        self.assertEqual((self.repo/'article/gadget/sample-article.html').read_text(), 'old')
        self.assertEqual(legacy.import_one(self.source), {'admitted': False})

    def test_unverified_titles_or_unattributed_sources_replan_instead_of_writer(self):
        evidence = json.loads((self.articles/self.slug/'evidence.json').read_text())
        evidence['products'][0]['sources'] = [{'verified': True, 'url': 'http://bad',
                                              'excerpt': 'Product details'}]
        self._save(self.articles/self.slug/'evidence.json', evidence)
        self.assertTrue(legacy.has_pending(self.source))
        result = legacy.import_one(self.source)
        self.assertTrue(result['replanning'])
        self.assertEqual(recovery.load_job(self.slug)['current_stage'], 'PLANNING_PENDING')

    def test_planning_failure_keeps_original_evidence_and_gate(self):
        evidence = json.loads((self.articles/self.slug/'evidence.json').read_text())
        evidence['products'][0]['sources'] = []
        self._save(self.articles/self.slug/'evidence.json', evidence)
        legacy.import_one(self.source)
        previous_evidence = (self.jobs/self.slug/'evidence.json').read_bytes()
        previous_approval = (self.jobs/self.slug/'chat-publish-approval.json').read_bytes()
        with patch('planning_runtime.replan_published_topic', side_effect=ValueError('source unavailable')):
            result = legacy.replan_one(self.slug)
        self.assertEqual(result['action'], 'hold')
        self.assertEqual(recovery.load_job(self.slug)['current_stage'], 'PLANNING_HOLD')
        self.assertEqual((self.jobs/self.slug/'evidence.json').read_bytes(), previous_evidence)
        self.assertEqual((self.jobs/self.slug/'chat-publish-approval.json').read_bytes(), previous_approval)
        self.assertFalse((self.jobs/self.slug/'product-attempt-fingerprint.json').exists())
        self.assertEqual((self.repo/'article/gadget/sample-article.html').read_text(), 'old')

    def test_planning_product_qa_pass_resumes_writer_and_revokes_old_gate(self):
        evidence = json.loads((self.articles/self.slug/'evidence.json').read_text())
        evidence['products'][0]['sources'] = []
        self._save(self.articles/self.slug/'evidence.json', evidence)
        legacy.import_one(self.source)
        new_products = [{'ref': f'P{i}', 'asin': f'B00000000{i}',
                         'sources': [{'verified': True, 'url': f'https://example.org/new{i}',
                                      'excerpt': f'Product {i}'}]} for i in range(6)]
        topic = {'slug': self.slug, 'category': 'gadget', 'title': 'New title',
                 'comparison_axes': [{'id': f'axis{i}', 'keywords': ['product'],
                                      'label': f'Axis {i}'} for i in range(3)]}
        fresh = {'slug': self.slug, 'category': 'gadget', 'products': new_products}
        with patch('planning_runtime.replan_published_topic', return_value=(topic, {'provider': 'test'})), \
             patch('growth_role_business.collect_unique_products', return_value=(new_products, {'complete': True})), \
             patch('product_selection.select_six', return_value=(new_products, {'axes': []})), \
             patch('growth_role_business.build_evidence', return_value=fresh), \
             patch('semantic_review.independent_review', return_value={'status': 'ACCEPTED'}), \
             patch('comparison_acceptance.product_issues', return_value=[]):
            result = legacy.replan_one(self.slug)
        self.assertEqual(result['product_qa'], 'PASS')
        self.assertEqual(recovery.load_job(self.slug)['current_stage'], 'WRITER_PENDING')
        self.assertEqual(recovery.read_artifact(self.slug, 'topic.json')['title'], 'New title')
        self.assertEqual(recovery.read_artifact(self.slug, 'chat-publish-approval.json'), {})
        self.assertEqual(recovery.read_artifact(self.slug, legacy.MARKER)['phase'], 'regular_repair')
        self.assertEqual((self.repo/'article/gadget/sample-article.html').read_text(), 'old')
        self.assertEqual(recovery.read_artifact(self.slug, 'legacy-original-20261115/state.json')['status'], 'PUBLISHED')

    def test_existing_legacy_product_hold_restart_uses_planning(self):
        self.assertTrue(legacy.import_one(self.source)['admitted'])
        state = recovery.load_job(self.slug)
        state.update(current_stage='PRODUCT_HOLD', status='ACTIVE', source='legacy_repair',
                     next_retry_at=None)
        recovery._atomic_json(recovery.state_path(self.slug), state)
        evidence = recovery.read_artifact(self.slug, 'evidence.json')
        evidence['products'][0]['sources'] = []
        recovery.write_artifact(self.slug, 'evidence.json', evidence)
        result = legacy.import_one(self.source)
        self.assertTrue(result['replanning'])
        self.assertEqual(recovery.load_job(self.slug)['current_stage'], 'PLANNING_PENDING')

    def test_skip_ordinary_active_job(self):
        state = recovery.load_job(self.slug)
        state['status'] = 'ACTIVE'
        recovery._atomic_json(recovery.state_path(self.slug), state)
        self.assertEqual(legacy.import_one(self.source), {'admitted': False})
        self.assertFalse((self.jobs/self.slug/legacy.MARKER).exists())

    def test_missing_checkpoint_recovered_from_existing_published_site(self):
        import shutil
        from types import SimpleNamespace
        shutil.rmtree(self.jobs / self.slug)
        self._save(self.repo/'data/payloads/sample-article.json',
                   {'slug': self.slug, 'category': 'gadget', 'h1': 'Existing Article'})
        self._save(self.repo/'data/evidence/sample-article.json',
                   {'slug': self.slug, 'category': 'gadget', 'selection': {},
                    'eyecatch_image': '/img/sample-article/sample-article.png'})
        with patch.object(legacy.subprocess if hasattr(legacy, 'subprocess') else
                          __import__('subprocess'), 'run',
                          return_value=SimpleNamespace(stdout='a'*40)):
            result = legacy.import_one(self.source)
        self.assertTrue(result['admitted'])
        state = recovery.load_job(self.slug)
        self.assertEqual(state['source'], 'legacy_repair')
        original = self.jobs/self.slug/legacy.BACKUP/'state.json'
        self.assertEqual(json.loads(original.read_text())['published_commit'], 'a'*40)
        self.assertEqual((self.repo/'article/gadget/sample-article.html').read_text(), 'old')

    def test_discarded_duplicate_job_is_restored_only_from_live_site(self):
        from types import SimpleNamespace
        state = recovery.load_job(self.slug)
        state.update(status='DISCARDED', current_stage='DISCARDED',
                     discard_reason='duplicate slug already exists in production')
        recovery._atomic_json(recovery.state_path(self.slug), state)
        self._save(self.repo/'data/payloads/sample-article.json',
                   {'slug': self.slug, 'category': 'gadget', 'h1': 'Existing Article'})
        self._save(self.repo/'data/evidence/sample-article.json',
                   {'slug': self.slug, 'category': 'gadget',
                    'eyecatch_image': '/img/sample-article/sample-article.png'})
        with patch.object(__import__('subprocess'), 'run',
                          return_value=SimpleNamespace(stdout='b'*40)):
            receipt = legacy.import_one(self.source)
        self.assertTrue(receipt['admitted'])
        self.assertEqual(recovery.load_job(self.slug)['status'], 'ACTIVE')
        self.assertEqual(recovery.read_artifact(self.slug, legacy.BACKUP+'/state.json')['status'], 'DISCARDED')
        self.assertEqual((self.repo/'article/gadget/sample-article.html').read_text(), 'old')

    def test_source_validation_rejects_missing_file(self):
        (self.articles/self.slug/'article.html').unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            legacy.candidates(self.source)

    def test_precheck_routing_requires_current_review(self):
        self.assertTrue(legacy.import_one(self.source)['admitted'])
        marker = recovery.read_artifact(self.slug, legacy.MARKER)
        self.assertEqual(marker['phase'], 'initial_precheck')
        marker['phase'] = 'regular_repair'
        recovery.write_artifact(self.slug, legacy.MARKER, marker)
        self.assertEqual(legacy.precheck(self.slug)['action'], 'writer')


if __name__ == '__main__':
    unittest.main()
