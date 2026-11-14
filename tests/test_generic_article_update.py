from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
import publish_payload
import role_execution_publish as publisher
import growth_role_business as business
import production_deploy as deploy


class GenericArticleUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site=Path(self.tmp.name)
        self.slug='article-one'
        self.category='gadget'
        self.url='https://www.sellemy.jp/article/gadget/article-one.html'
        (self.site/'json').mkdir()
        (self.site/'article/gadget').mkdir(parents=True)
        (self.site/'data/evidence').mkdir(parents=True)
        (self.site/'article/gadget/article-one.html').write_text('<html>original</html>')
        (self.site/'json/articles.json').write_text(json.dumps([
            {'slug':self.slug,'article_id':self.slug,'category':self.category,
             'status':'published','published_at':'2026-10-01T00:00:00+09:00'}]))
        (self.site/'data/evidence/article-one.json').write_text(json.dumps({
            'slug':self.slug,'category':self.category,'canonical_url':self.url}))
        self.payload={'slug':self.slug,'category':self.category,'h1':'New Title','summary':'test summary'}
        self.evidence={'slug':self.slug,'category':self.category,'canonical_url':self.url, 'eyecatch_image':'/img/article-one/article-one.png'}

    def test_update_accepts_identical_published_identity(self):
        self.assertEqual(publish_payload.require_update_identity(self.payload,self.evidence,root=self.site),self.url)

    def test_update_refuses_canonical_change(self):
        evidence={**self.evidence,'canonical_url':'https://sellemy.jp/article/article-one/'}
        with self.assertRaisesRegex(ValueError,'canonical'):
            publish_payload.require_update_identity(self.payload,evidence,root=self.site)

    def test_update_refuses_category_change(self):
        payload={**self.payload,'category':'beauty'}
        with self.assertRaisesRegex(ValueError,'identity'):
            publish_payload.require_update_identity(payload,self.evidence,root=self.site)

    def test_update_refuses_new_article_without_baseline(self):
        self.payload['slug']='new-article'
        self.evidence['slug']='new-article'
        with self.assertRaisesRegex(ValueError,'exactly one'):
            publish_payload.require_update_identity(self.payload,self.evidence,root=self.site)

    def test_update_refuses_duplicate_registry_identity(self):
        f=self.site/'json/articles.json'
        rows=json.loads(f.read_text());rows.append(dict(rows[0]));f.write_text(json.dumps(rows))
        with self.assertRaisesRegex(ValueError,'exactly one'):
            publish_payload.require_update_identity(self.payload,self.evidence,root=self.site)

    def test_update_preserves_index_identity_and_original_publication_date(self):
        import datetime
        ts=datetime.datetime(2026,11,15,tzinfo=datetime.timezone.utc)
        with patch.object(publish_payload,'ARTICLES',self.site/'json/articles.json'):
            result=publish_payload._update_articles(self.payload,self.evidence,allow_existing=True,now=ts)
        row=json.loads((self.site/'json/articles.json').read_text())[0]
        self.assertEqual(result['action'],'updated')
        self.assertEqual(row['slug'],self.slug)
        self.assertEqual(row['published_at'],'2026-10-01T00:00:00+09:00')

    def _dispatch(self, update):
        calls=[]
        class Gate:
            def __enter__(self):return {'test':True}
            def __exit__(self,*args):return False
        class Lock:
            def acquire(self):return Gate()
        def fake_apply(slug,*,apply,allow_existing):
            calls.append(('apply',allow_existing));return {'applied':True}
        def fake_publish(slug,category,*,update):
            calls.append(('publish',update));return 'a'*40
        with (patch.object(publisher,'_preflight',return_value=({},self.evidence,self.payload,self.category)),
              patch.object(publisher,'load_job',return_value={'current_stage':'PUBLISH_PENDING','published_commit':'b'*40 if update else None}),
              patch.object(publish_payload,'require_update_identity',return_value=self.url) as identity,
              patch.object(publisher,'_existing_publish_commit',return_value=None) as existing,
              patch.object(publisher,'_git',return_value='c'*40),
              patch.object(publisher,'PublishGate',return_value=Lock()),
              patch.object(publisher,'materialize_for_publish'),
              patch.object(publish_payload,'run',side_effect=fake_apply),
              patch.object(publisher,'_publish',side_effect=fake_publish),
              patch.object(publisher,'mark_published'),
              patch.object(publisher,'record_execution')):
            publisher.publish(self.slug)
        return calls,identity.call_count,existing.call_count

    def test_existing_article_update_does_not_shortcircuit_prior_publish(self):
        calls,identity,existing=self._dispatch(True)
        self.assertEqual(calls,[('apply',True),('publish',True)])
        self.assertEqual(identity,1)
        self.assertEqual(existing,0)

    def test_new_article_uses_strict_create(self):
        calls,identity,existing=self._dispatch(False)
        self.assertEqual(calls,[('apply',False),('publish',False)])
        self.assertEqual(identity,0)
        self.assertEqual(existing,1)

    def test_update_publishes_changed_subset_and_checks_exact_deployed_bytes(self):
        import hashlib
        html=self.site/'article/gadget/article-one.html'
        html.write_bytes(b'<html>updated revision</html>\n')
        payload_path=self.site/'data/payloads/article-one.json'
        payload_path.parent.mkdir(parents=True,exist_ok=True)
        payload_path.write_text(json.dumps(self.payload))
        evidence_path=self.site/'data/evidence/article-one.json'
        evidence_path.write_text(json.dumps(self.evidence))
        changed=' M article/gadget/article-one.html\n M json/articles.json\n'
        def command(argv,**kwargs):
            if argv[:3]==['git','status','--porcelain']:
                return changed
            raise AssertionError(argv)
        calls=[]
        def subprocess_run(argv,**kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0)
        def git(*args):
            return 'a'*40 if args==('rev-parse','HEAD') or args==('rev-parse','origin/main') else ''
        with (patch.object(business,'ROOT',self.site),
              patch.object(business,'require_publishing_enabled'),
              patch.object(business,'load_receipt',return_value={}),
              patch.object(business,'require_acceptance'),
              patch.object(business.subprocess,'check_output',side_effect=command),
              patch.object(business.subprocess,'run',side_effect=subprocess_run),
              patch.object(business,'_git',side_effect=git),
              patch('article_release.release',return_value='a'*40),
              patch.object(business,'verify_production_deploy') as verify):
            self.assertEqual(business._publish(self.slug,self.category,update=True),'a'*40)
        self.assertIn(['git','add','--','article/gadget/article-one.html','json/articles.json'],calls)
        self.assertEqual(verify.call_args.kwargs['expected_html_sha256'],hashlib.sha256(html.read_bytes()).hexdigest())

    def test_update_rejects_unrelated_changed_files_before_git_commit(self):
        payload_path=self.site/'data/payloads/article-one.json'
        payload_path.parent.mkdir(parents=True,exist_ok=True)
        payload_path.write_text(json.dumps(self.payload))
        evidence_path=self.site/'data/evidence/article-one.json'
        evidence_path.write_text(json.dumps(self.evidence))
        with (patch.object(business,'ROOT',self.site),
              patch.object(business,'require_publishing_enabled'),
              patch.object(business,'load_receipt',return_value={}),
              patch.object(business,'require_acceptance'),
              patch.object(business.subprocess,'check_output',
                           return_value=' M article/gadget/article-one.html\n M pipeline/unsafe.py\n'),
              patch.object(business.subprocess,'run',side_effect=AssertionError('git write attempted'))):
            with self.assertRaisesRegex(business.GrowthRuntimeError,'publication unit mismatch'):
                business._publish(self.slug,self.category,update=True)

    def test_readback_rejects_changed_html_bytes(self):
        import hashlib
        with patch.object(deploy,'_gh',side_effect=[
            {'status':'built'},[{'commit':'a'*40,'status':'built'}]]):
            with patch.object(deploy,'_get',return_value=(200,self.url,b'wrong')):
                with self.assertRaisesRegex(deploy.ProductionDeployError,'HTML differs'):
                    deploy.verify(self.slug,self.category,'a'*40,
                                  expected_html_sha256=hashlib.sha256(b'expected').hexdigest(),
                                  timeout_seconds=1,poll_seconds=0)


if __name__=='__main__':
    unittest.main()
