from copy import deepcopy
from unittest.mock import patch
import pytest
import ceo_article_edit as m
from comparison_acceptance import digest, evidence_content, require_acceptance, is_human_edit_receipt
from chat_publish_approval import require_chat_publish_approval
from quality_fixtures import accepted_fixture


def _ready():
    old, evidence, acceptance = accepted_fixture()
    revised = deepcopy(old)
    revised['h1'] = 'Edited article title'
    return old, evidence, acceptance, revised


def _artifacts(old, evidence, acceptance):
    return {'payload.json':old, 'evidence.json':evidence, 'acceptance.json':acceptance,
            'qa.json':{'original_qa':True}, 'qa-certification.json':{'original_cert':True},
            'independent-quality-receipts.json':{'original_receipts':True}}


@pytest.mark.parametrize('role,review_stage',[('ceo','CHAT_REVIEW_PENDING'),('coo','COO_REVIEW_PENDING')])
def test_edit_goes_directly_to_normal_publication_without_qa(role, review_stage):
    old,evidence,acceptance,revised = _ready()
    values = _artifacts(old,evidence,acceptance)
    with patch.object(m,'load_job',return_value={'status':'ACTIVE','current_stage':review_stage}), \
         patch.object(m,'read_artifact',side_effect=lambda slug,key,*args:values.get(key, {})), \
         patch.object(m,'write_artifact',side_effect=lambda slug,key,value:values.__setitem__(key,value)), \
         patch.object(m,'set_stage') as stage, patch('role_execution_writer.reviewer') as reviewer:
        with pytest.raises(ValueError,match='changed'):
            m.apply_approved_edit('fixture-reading-stands','bad',{'h1':'title'},reviewer_role=role)
        with pytest.raises(ValueError,match='unsupported'):
            m.apply_approved_edit('fixture-reading-stands',digest(old),{'category':'gadget'},reviewer_role=role)
        with pytest.raises(ValueError,match='illegal'):
            m.apply_approved_edit('fixture-reading-stands',digest(old),{'products':[{'ref':'other'}]*6},reviewer_role=role)
        result=m.apply_approved_edit('fixture-reading-stands',digest(old),{'h1':revised['h1']},reviewer_role=role)
        assert result['next']=='EYECATCH_PENDING'
        assert values['payload.json']==revised
        assert values['qa-certification.json']=={'original_cert':True}
        assert values['independent-quality-receipts.json']=={'original_receipts':True}
        assert values[f'ceo-original-acceptance-{digest(old)[:12]}.json']==acceptance
        assert is_human_edit_receipt(values['acceptance.json'])
        require_acceptance(revised,evidence,values['acceptance.json'])
        require_chat_publish_approval(values['chat-publish-approval.json'],payload=revised,evidence=evidence)
        assert m.ensure_edited_acceptance('fixture-reading-stands',revised,evidence,values['acceptance.json'])==values['acceptance.json']
        stage.assert_called_once_with('fixture-reading-stands','EYECATCH_PENDING',event='CEO_EDIT_EXACT_VERSION_APPROVED',detail={'approved_sha256':digest(revised),'reviewer_role':role})
        reviewer.assert_not_called()
        bad=deepcopy(revised);bad['h1']='Unapproved changed title'
        with pytest.raises(ValueError,match='stale'):
            require_acceptance(bad,evidence,values['acceptance.json'])
        bad=deepcopy(evidence);bad['amazon_tag']='changed'
        with pytest.raises(ValueError):
            require_acceptance(revised,bad,values['acceptance.json'])
        values['chat-publish-approval.json']={}
        with pytest.raises(ValueError):
            m.ensure_edited_acceptance('fixture-reading-stands',revised,evidence,values['acceptance.json'])


def _legacy():
    old,evidence,_,revised=_ready()
    marker={'schema':'sellemy-ceo-edited-approval/v1','approved':True,'original_sha256':digest(old),
            'approved_sha256':digest(revised),'evidence_sha256':digest(evidence_content(evidence)),'reviewer_role':'coo'}
    values={'payload.json':revised,'evidence.json':evidence,'ceo-edited-approval.json':marker,
            f'ceo-original-{digest(old)[:12]}.json':old,'acceptance.json':{},'chat-publish-approval.json':{}}
    return values,revised,evidence


def test_legacy_still_approved_edit_resumes_without_qa_but_reopened_is_not_approved():
    values,payload,evidence=_legacy()
    with patch.object(m,'load_job',return_value={'status':'ACTIVE','current_stage':'CHAT_REVIEW_PENDING'}) as state, \
         patch.object(m,'read_artifact',side_effect=lambda slug,key,*args:values.get(key,{})), \
         patch.object(m,'write_artifact',side_effect=lambda slug,key,value:values.__setitem__(key,value)), \
         patch.object(m,'set_stage') as stage, patch('role_execution_writer.reviewer') as reviewer:
        with pytest.raises(ValueError,match='not awaiting'):
            m.resume_approved_edit('fixture-reading-stands')
        stage.assert_not_called()
        with pytest.raises(ValueError):
            m.ensure_edited_acceptance('fixture-reading-stands',payload,evidence,{})
        state.return_value={'status':'ACTIVE','current_stage':'CEO_EDIT_QA_PENDING'}
        assert m.resume_approved_edit('fixture-reading-stands')['next']=='EYECATCH_PENDING'
        reviewer.assert_not_called()
        require_acceptance(payload,evidence,values['acceptance.json'])


def test_reopened_article_fresh_exact_approval_can_publish_without_reviewer():
    values,payload,evidence=_legacy()
    values['chat-publish-approval.json']=m._approval(payload,evidence,'ceo')
    with patch.object(m,'read_artifact',side_effect=lambda slug,key,*args:values.get(key,{})), \
         patch.object(m,'write_artifact',side_effect=lambda slug,key,value:values.__setitem__(key,value)):
        receipt=m.ensure_edited_acceptance('fixture-reading-stands',payload,evidence,{})
        require_acceptance(payload,evidence,receipt)
        assert receipt['edit_marker']['reviewer_role']=='ceo'


def test_coo_edit_adapter_uses_direct_route():
    import coo_article_review as coo
    old,evidence,_,revised=_ready()
    rows={'payload.json':old,'evidence.json':evidence}
    with patch.object(coo,'load_job',return_value={'status':'ACTIVE','current_stage':'COO_REVIEW_PENDING'}), \
         patch.object(coo,'read_artifact',side_effect=lambda slug,key,*args:rows.get(key,{})), \
         patch('ceo_article_edit.apply_approved_edit',return_value={'ok':True,'next':'EYECATCH_PENDING'}) as call:
        result=coo.decide('fixture-reading-stands',payload_sha=digest(old),evidence_sha=digest(evidence_content(evidence)),decision='CERTIFIED',reason='proxy',edited_payload={'h1':revised['h1']})
        assert result['next']=='EYECATCH_PENDING'
        call.assert_called_once_with('fixture-reading-stands',digest(old),{'h1':revised['h1']},reviewer_role='coo')
        with pytest.raises(ValueError,match='changed'):
            coo.decide('fixture-reading-stands',payload_sha='stale',evidence_sha=digest(evidence_content(evidence)),decision='CERTIFIED',reason='proxy',edited_payload={'h1':'new'})
        with pytest.raises(ValueError,match='requires certified'):
            coo.decide('fixture-reading-stands',payload_sha=digest(old),evidence_sha=digest(evidence_content(evidence)),decision='REWORK',reason='proxy',edited_payload={'h1':'new'})


def test_normal_designer_and_publish_preflight_only_require_exact_approval():
    import growth_recovery as recovery
    import role_stage_exec as stage_exec
    import role_execution_publish as publication
    old,evidence,acceptance,revised=_ready()
    values=_artifacts(old,evidence,acceptance)
    state={'status':'ACTIVE','current_stage':'CHAT_REVIEW_PENDING','category':old['category']}
    def read(slug,key,*default): return values.get(key, default[0] if default else None)
    def write(slug,key,value): values[key]=value
    def advance(slug,stage,**kwargs): state['current_stage']=stage
    with patch.object(m,'load_job',return_value=state), patch.object(m,'read_artifact',side_effect=read), \
         patch.object(m,'write_artifact',side_effect=write),patch.object(m,'set_stage',side_effect=advance), \
         patch.object(recovery,'load_job',return_value=state),patch.object(recovery,'read_artifact',side_effect=read), \
         patch.object(publication,'load_job',return_value=state),patch.object(publication,'read_artifact',side_effect=read), \
         patch('publish_gate.require_publishing_enabled'),patch('independent_quality_gate.require_receipts') as qa, \
         patch('qa_certification.require_certification') as cert:
        m.apply_approved_edit('fixture-reading-stands',digest(old),{'h1':revised['h1']})
        assert stage_exec._inspect('fixture-reading-stands',cache_only=True)['action']=='designer'
        assert publication._preflight('fixture-reading-stands')[2]==revised
        state['current_stage']='PUBLISH_PENDING'
        assert stage_exec._inspect('fixture-reading-stands',cache_only=True)['action']=='publish'
        assert publication._preflight('fixture-reading-stands')[2]==revised
        qa.assert_not_called();cert.assert_not_called()
        values['chat-publish-approval.json']={}
        with pytest.raises(ValueError):publication._preflight('fixture-reading-stands')
        # Ordinary approved articles also avoid repeated QA.
        values.update(_artifacts(old,evidence,acceptance))
        values['chat-publish-approval.json']=m._approval(old,evidence,'ceo')
        assert publication._preflight('fixture-reading-stands')[2]==old
        qa.assert_not_called();cert.assert_not_called()
