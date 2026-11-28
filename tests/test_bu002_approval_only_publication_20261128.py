"""Exact human approval, no repeated business QA in the publishing path."""
import json
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
import pytest
import growth_recovery as recovery
import role_execution_publish as publication
import role_stage_exec as stages
import publish_payload as publish
from quality_fixtures import accepted_fixture
from chat_publish_approval import make_chat_approval


def seed(tmp, *, role=None, receipt=True):
    payload,evidence,_=accepted_fixture()
    slug=payload['slug']; folder=tmp/'jobs'/slug; folder.mkdir(parents=True)
    approval=make_chat_approval(payload=payload,evidence=evidence,decision='CERTIFIED',publish_allowed=True,reason='offline test only',reviewer_role=role)
    values={'state.json':{'slug':slug,'category':payload['category'],'current_stage':'PUBLISH_PENDING','status':'ACTIVE'},'payload.json':payload,'evidence.json':evidence,'chat-publish-approval.json':approval,'acceptance.json':{'obsolete':'receipt'},'product-blocker.json':{'needs_product_reselection':True}}
    if receipt: values['eyecatch-receipt.json']={'generation_method':'generative_ai','provider':'legacy'}
    for name,data in values.items(): (folder/name).write_text(json.dumps(data))
    (folder/'eyecatch.png').write_bytes(b'existing-image-data')
    return slug,folder,payload,evidence


@pytest.mark.parametrize('role,receipt',[(None,True),('coo',False)])
def test_approved_article_materializes_and_applies_without_any_repeat_qa(tmp_path,role,receipt):
    slug,folder,payload,evidence=seed(tmp_path,role=role,receipt=receipt)
    site=tmp_path/'site'; (site/'article'/payload['category']).mkdir(parents=True)
    meta={'published_at':'2026-11-28T00:00:00+09:00','updated_at':'2026-11-28T00:00:00+09:00'}
    with ExitStack() as stack:
        for ctx in (
            patch.object(recovery,'JOBS_ROOT',tmp_path/'jobs'), 
         patch.object(publish,'ROOT',site),patch.object(publish,'require_publishing_enabled'), 
         patch('publish_gate.require_publishing_enabled'),patch('comparison_acceptance.product_issues',side_effect=AssertionError('product QA repeated')), 
         patch('comparison_acceptance.require_acceptance',side_effect=AssertionError('acceptance repeated')), 
         patch('independent_quality_gate.require_receipts',side_effect=AssertionError('QA receipts repeated')), 
         patch('qa_certification.require_certification',side_effect=AssertionError('QA certification repeated')), 
         patch.object(publish,'run_qa',side_effect=AssertionError('machine QA repeated')), 
         patch.object(publish,'run_review_gate',side_effect=AssertionError('review repeated')), 
         patch('renderer.validate_payload',side_effect=AssertionError('payload QA repeated')), 
         patch('renderer.validate_evidence',side_effect=AssertionError('evidence QA repeated')), 
         patch('renderer.match_refs',side_effect=AssertionError('ref QA repeated')), 
         patch('product_image_audit.audit_remote_product_images',side_effect=AssertionError('image QA repeated')), 
         patch.object(publish,'_affiliate_preflight',side_effect=AssertionError('affiliate network repeated')), 
         patch.object(publish,'article_related_features',return_value=[]), 
         patch.object(publish,'_update_articles',return_value={'metadata':meta}), 
         patch.object(publish,'_upsert_products'),patch.object(publish,'_sync_products_json'), 
         patch.object(publish,'_update_top'),patch.object(publish,'sync_static_catalogs'),patch.object(publish,'_update_sitemap')
        ):
            stack.enter_context(ctx)
        assert stages._inspect(slug,cache_only=True)['action']=='publish'
        assert publication._preflight(slug)[2]==payload
        recovery.materialize_for_publish(slug,site)
        assert publish.run(slug,apply=True,allow_existing=False)['applied'] is True
        assert (site/'article'/payload['category']/f'{slug}.html').is_file()
        assert (site/'img'/slug/f'{slug}.png').read_bytes()==b'existing-image-data'
        retained=json.loads((site/'data'/'eyecatch-receipts'/f'{slug}.json').read_text())
        assert retained==({'generation_method':'generative_ai','provider':'legacy'} if receipt else {})
        assert json.loads((site/'data'/'acceptance'/f'{slug}.json').read_text())=={'obsolete':'receipt'}


@pytest.mark.parametrize('fault',['no_approval','stale_payload','no_image','empty_image','wrong_stage'])
def test_release_refuses_only_missing_data_or_invalid_approval_and_stage(tmp_path,fault):
    slug,folder,payload,evidence=seed(tmp_path)
    if fault=='no_approval': (folder/'chat-publish-approval.json').unlink()
    elif fault=='stale_payload': (folder/'payload.json').write_text(json.dumps({**payload,'h1':'not approved'}))
    elif fault=='no_image': (folder/'eyecatch.png').unlink()
    elif fault=='empty_image': (folder/'eyecatch.png').write_bytes(b'')
    elif fault=='wrong_stage': (folder/'state.json').write_text(json.dumps({'current_stage':'QA_FAILED'}))
    site=tmp_path/'site'; site.mkdir()
    with patch.object(recovery,'JOBS_ROOT',tmp_path/'jobs'),patch('publish_gate.require_publishing_enabled'):
        with pytest.raises((ValueError,RuntimeError)):
            recovery.materialize_for_publish(slug,site)
    assert not list(site.iterdir())


def test_split_queue_and_publication_has_no_production_admission():
    from datetime import datetime,timezone
    import growth_runtime as runtime
    rows=[{'slug':'ready','status':'ACTIVE','current_stage':'PUBLISH_PENDING'},
          {'slug':'writer','status':'ACTIVE','current_stage':'WRITER_PENDING'}]
    with patch.object(recovery,'all_jobs',return_value=rows):
        assert recovery.eligible_job(lane='publication')['slug']=='ready'
        assert recovery.eligible_job(lane='production')['slug']=='writer'
    calls=[]
    def role(name,slug='',**kw):
        calls.append((name,kw.get('request')))
        if name=='ceo_reconcile':return {}
        if name=='scheduler':return {'publish_paused':False,'target_per_day':1}
        if name=='state_queue':return {'eligible':rows[0]}
        if name=='publish':return {'commit':'test'}
        if name=='postpublish':return {}
        raise AssertionError(name)
    with patch.object(runtime,'require_publishing_enabled'),patch.object(runtime,'execute_role',side_effect=role):
        result=runtime.run(publish=True,lane='publication',report_path=None)
    assert result['published'] is True
    assert [n for n,_ in calls]==['ceo_reconcile','scheduler','state_queue','publish','postpublish']
    assert calls[1][1]['operation']=='current_state'
    assert calls[2][1]=={'lane':'publication'}


def test_recovery_production_does_not_consult_publish_controls_or_queue():
    import growth_runtime as runtime
    calls=[]
    def role(name,slug='',**kw):
        calls.append(name)
        if name=='recovery':return {}
        if name=='state_queue':
            assert kw['request']=={'lane':'production'}
            return {'eligible':{'slug':'draft'},'unfinished':[]}
        if name=='legacy_admit':return {'admitted':False}
        if name=='inspect':return {'action':'coo_review'}
        raise AssertionError(name)
    with patch.object(runtime,'execute_role',side_effect=role),patch.object(runtime,'require_publishing_enabled',side_effect=AssertionError('publication stop consulted')),patch.object(runtime,'load_job',return_value={'status':'ACTIVE','current_stage':'COO_REVIEW_PENDING'}):
        result=runtime.run(publish=True,lane='production',recovery_only=True,report_path=None)
    assert result['status']=='awaiting_coo_final_review'
    assert 'scheduler' not in calls and 'publish' not in calls and 'ceo_reconcile' not in calls


def test_designer_hands_off_without_calling_publish():
    import growth_runtime as runtime
    calls=[]
    def role(name,*args,**kw):
        calls.append(name)
        if name=='inspect':return {'action':'designer'}
        if name=='designer':return {'eyecatch':{}}
        raise AssertionError(name)
    with patch.object(runtime,'execute_role',side_effect=role),patch.object(runtime,'load_job',return_value={'status':'ACTIVE','current_stage':'EYECATCH_PENDING'}):
        out={}; assert runtime._execute_job('draft',out,lane='production') is None
    assert calls==['inspect','designer'] and out['status']=='ready_for_publication'


def test_production_target_counts_review_arrival_not_publications(tmp_path):
    from datetime import datetime,timezone
    from zoneinfo import ZoneInfo
    from adaptive_publish import AdaptivePublishController
    c=AdaptivePublishController(Path(__file__).resolve().parents[1],state_dir=tmp_path)
    records=[{'evaluation_basis':'ceo_review_arrival','slug':'new','source':'runtime','evaluated_at':'2026-11-28T01:00:00+09:00'},
             {'evaluation_basis':'ceo_review_arrival','slug':'new','source':'runtime','evaluated_at':'2026-11-28T02:00:00+09:00'},
             {'evaluation_basis':'ceo_review_arrival','slug':'repair','source':'legacy_repair','evaluated_at':'2026-11-28T03:00:00+09:00'},
             {'slug':'published','commit':'123','evaluated_at':'2026-11-28T04:00:00+09:00'}]
    with patch.object(c,'feedback',return_value=records):
        assert c._produced_articles_for_local_date(datetime(2026,11,28).date(),ZoneInfo('Asia/Tokyo'))=={'new'}


@pytest.mark.parametrize('verdict',['PASS','FAIL'])
def test_visual_qa_attaches_actual_image_and_handles_anatomy_verdict(tmp_path,verdict):
    from PIL import Image
    from types import SimpleNamespace
    from eyecatch_visual_qa import review_image, EyecatchVisualError
    p=tmp_path/'image.png';Image.new('RGB',(32,32),'white').save(p)
    def call(command,**kw):
        assert command[command.index('--image')+1]==str(p.resolve())
        assert 'fingers' in kw['input'] and 'occlusion' in kw['input']
        Path(command[command.index('--output-last-message')+1]).write_text(json.dumps({'verdict':verdict,'reason':'visible anatomy observation'}))
        return SimpleNamespace(returncode=0,stderr='')
    with patch('api_role_binding.require_codex_settings',return_value=('test','medium')),patch('eyecatch_visual_qa.subprocess.run',side_effect=call):
        if verdict=='PASS': assert review_image(p,title='fixture')['verdict']=='PASS'
        else:
            with pytest.raises(EyecatchVisualError,match='visual QA failed'):review_image(p,title='fixture')
