"""Only redundant prompt/context material is removed; source-linked QA stays strict."""
import json
from unittest.mock import patch
from tests.fixtures import valid_evidence,valid_payload
from qa_return import writer_revision_evidence
from writer_runtime import _prompt
from editorial_plan_review import independent_plan_review
from comparison_acceptance import digest


def test_writer_rework_only_passes_source_text_for_disputed_product():
    evidence = valid_evidence()
    for item in evidence['products']:
        item['sources'] = [{'verified':True,'excerpt':f'source-{item["ref"]}-' + ('x'*2200)}]
    evidence['product_acceptance'] = {'detailed_review':['redundant']*10000}
    feedback = {'independent_quality_gate': {'gate':'claim',
                'issues':['p2の「主成分は硫酸マグネシウム」には根拠がない']}}
    original = writer_revision_evidence(evidence,feedback)
    assert len(original['products'])==6
    assert 'sources' in original['products'][1]
    assert all('sources' not in p for i,p in enumerate(original['products']) if i!=1)
    assert len(evidence['products'][0]['sources'][0]['excerpt'])>2200
    assert 'product_acceptance' in evidence
    topic={'slug':'test-widgets-6-picks','category':'gadget','comparison_axes':[{'id':'portable','label':'携帯'}, {'id':'balanced','label':'通常'}],
           'reader_persona':{'purchase_goal':'用途に合う製品を見つける'}}
    prompt = _prompt(topic,evidence,previous_payload=valid_payload(),gate_feedback=feedback)
    assert 'source-p2-' in prompt
    assert 'source-p1-' not in prompt
    assert 'redundant' not in prompt
    assert '主成分は硫酸マグネシウム' in prompt
    assert 'PREVIOUS_PAYLOAD' in prompt


def test_ceo_plan_review_keeps_sources_but_omits_product_qa_history():
    plan={'requirements':[{'id':'C01','source':'CEO','instruction':'商品の推薦理由を具体的に'}]}
    payload={'products':[{'ref':'p1','description':'良い説明'}]}
    evidence={'products':[{'ref':'p1','sources':[{'excerpt':'事実の根拠'}]}],
              'product_acceptance':{'review':['historical-review']*10000}}
    prompts=[]
    def judge(prompt):
        prompts.append(prompt)
        return {'schema':'sellemy-editorial-plan-review/v1',
                'plan_hash':digest(plan),'payload_hash':digest(payload),
                'items':[{'id':'C01','verdict':'PASS','article_quote':'良い説明','reason':'根拠を確認'}]}
    receipt=independent_plan_review(plan,payload,judge=judge,evidence=evidence)
    assert receipt['items'][0]['verdict']=='PASS'
    assert len(prompts)==1
    assert '事実の根拠' in prompts[0]
    assert 'historical-review' not in prompts[0]
    assert len(prompts[0])<1500
