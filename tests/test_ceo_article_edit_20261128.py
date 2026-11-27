from copy import deepcopy
from unittest.mock import patch
import pytest

import ceo_article_edit as m
from comparison_acceptance import digest, evidence_content
from quality_fixtures import accepted_fixture


def _ready():
    payload, evidence, _ = accepted_fixture()
    article = deepcopy(payload)
    article['h1'] = '編集したタイトル'
    return payload, evidence, article


def test_exact_approved_edit_only_changes_prose_and_queues_qa():
    old, evidence, article = _ready()
    values = {'payload.json': old, 'evidence.json': evidence}
    updates = {}
    with patch.object(m, 'load_job', return_value={'status':'ACTIVE','current_stage':'COO_REVIEW_PENDING'}), \
         patch.object(m, 'read_artifact', side_effect=lambda slug,key,*args: values.get(key, {})), \
         patch.object(m, 'write_artifact', side_effect=lambda slug,key,value: updates.setdefault(key,value)), \
         patch.object(m, 'set_stage') as stage, patch.object(m, 'product_issues', return_value=[]):
        with pytest.raises(ValueError, match='changed'):
            m.apply_approved_edit('fixture-reading-stands','bad',{'h1':'title'})
        with pytest.raises(ValueError, match='unsupported'):
            m.apply_approved_edit('fixture-reading-stands',digest(old),{'category':'gadget'})
        assert updates == {}
        result=m.apply_approved_edit('fixture-reading-stands',digest(old),{'h1':article['h1']})
        assert result['approved_sha256']==digest(article)
        assert updates['payload.json']['h1']=='編集したタイトル'
        assert updates['payload.json']['products']==old['products']
        assert updates['qa-certification.json']=={}
        assert updates['chat-publish-approval.json']=={}
        stage.assert_called_once_with('fixture-reading-stands', 'CEO_EDIT_QA_PENDING',event='CEO_EDIT_APPROVED_QA_PENDING',detail={'approved_sha256':digest(article),'reviewer_role':'ceo'})


def test_edited_recheck_failure_holds_exact_ceo_copy():
    old,evidence,article=_ready()
    marker={'schema':'sellemy-ceo-edited-approval/v1','approved':True,
            'approved_sha256':digest(article), 'evidence_sha256':digest(evidence_content(evidence))}
    values={'ceo-edited-approval.json':marker,'payload.json':article,'evidence.json':evidence}
    with patch.object(m,'load_job',return_value={'status':'ACTIVE','current_stage':'CEO_EDIT_QA_PENDING'}), \
         patch.object(m,'read_artifact',side_effect=lambda slug,key,*args:values.get(key,{})), \
         patch.object(m,'write_artifact') as writer, patch.object(m,'set_stage') as stage, \
         patch('role_execution_writer.reviewer',side_effect=ValueError('editorial QA failure')):
        with pytest.raises(ValueError,match='editorial QA failure'):
            m.recheck_approved_edit('fixture-reading-stands')
        stage.assert_called_once_with('fixture-reading-stands','STATE_HOLD',event='CEO_EDIT_QA_FAILED_HOLD')
        assert any(c.args[1]=='ceo-edited-qa-failure.json' for c in writer.call_args_list)


def test_coo_marker_is_not_publication_authorization_without_qa():
    old,evidence,article=_ready()
    marker={'schema':'sellemy-ceo-edited-approval/v1','approved':True,
            'approved_sha256':'wrong','evidence_sha256':digest(evidence_content(evidence)),
            'reviewer_role':'coo'}
    values={'ceo-edited-approval.json':marker,'payload.json':article,'evidence.json':evidence}
    with patch.object(m,'load_job',return_value={'status':'ACTIVE','current_stage':'CEO_EDIT_QA_PENDING'}), \
         patch.object(m,'read_artifact',side_effect=lambda slug,key,*args:values.get(key,{})):
        with pytest.raises(ValueError,match='identity changed'):
            m.recheck_approved_edit('fixture-reading-stands')
