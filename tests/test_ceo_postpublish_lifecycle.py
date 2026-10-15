import sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import growth_recovery
from postpublish_ceo_sampling import pending_review_receipt
from fixtures import valid_evidence

def test_tenth_normal_publication_only_and_legacy_not_counted():
    with tempfile.TemporaryDirectory() as tmp, patch.object(growth_recovery,'STATE_ROOT',Path(tmp)), patch.object(growth_recovery,'JOBS_ROOT',Path(tmp)/'jobs'), patch.object(growth_recovery,'EVENTS_PATH',Path(tmp)/'events.jsonl'),patch.object(growth_recovery,'EXECUTION_EVENTS_PATH',Path(tmp)/'execution.jsonl'):
        for i in range(12):
            slug=f'normal-example-{i}'
            growth_recovery.create_job(topic={'slug':slug,'category':'gadget'},evidence=valid_evidence(),selection={},planning_provider=None,viability_probes=[])
            with patch('operating_profile.normal_profile',return_value=i!=0):
                growth_recovery.mark_published(slug,f'testsha-{i}')
        jobs=growth_recovery.all_jobs()
        assert len(jobs)==12
        sample=pending_review_receipt(jobs=jobs,current_slug='normal-example-10',every_n=10)
        assert sample['published_normal_count']==11
        assert sample['groups_completed']==1
        selected=[pending_review_receipt(jobs=jobs,current_slug=f'normal-example-{i}',every_n=10)['selected'] for i in range(12)]
        assert sum(bool(x) for x in selected)==1
        assert selected[0] is None
        assert sum(x is not None for x in selected[1:])==1

def test_tenth_publish_dispatches_a_previous_article_sample():
    jobs=[{'slug':f'x-{i}','published_commit':f'c-{i}','published_at':f'2026-10-16T00:{i:02d}:00+00:00','status':'PUBLISHED','operating_profile':'normal'} for i in range(10)]
    ready=pending_review_receipt(jobs=jobs,current_slug='x-9',every_n=10)
    assert ready['ready_to_dispatch'] is not None
    assert ready['ready_to_dispatch']['slug'].startswith('x-')
    assert pending_review_receipt(jobs=jobs,current_slug='x-2',every_n=10)['ready_to_dispatch'] is None
