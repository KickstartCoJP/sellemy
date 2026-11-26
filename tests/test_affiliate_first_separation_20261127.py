"""Affiliate-first article policy: concise products, generic article, safety still gated."""
from copy import deepcopy
from quality_fixtures import accepted_fixture
from qa import run_qa
from renderer import render_article
from writer_runtime import _prompt
from editorial_plan import build_editorial_plan


def evaluate(payload, evidence):
    return run_qa(render_article(payload, evidence),payload,evidence,
                  acceptance=None,require_semantic_acceptance=False)


def test_short_cassette_passes_without_forcing_unverified_specs():
    payload,evidence,_=accepted_fixture()
    item=deepcopy(payload)
    item['products'][0]['description']='短く正確な商品紹介。'
    result=evaluate(item,evidence)
    assert result['overall_pass']
    assert result['description_lens'][0]<160


def test_product_brand_in_generic_group_or_buying_guide_fails():
    payload,evidence,_=accepted_fixture()
    brand='Mock Brand'
    evidence=deepcopy(evidence)
    evidence['products'][0]['brand']=brand
    for field in ('h1','lead','summary','how_to_choose','conclusion'):
        changed=deepcopy(payload);changed[field]+=' '+brand
        result=evaluate(changed,evidence)
        assert result['general_product_mentions'],field
        assert not result['overall_pass'],field
    for key in ('title','angle'):
        changed=deepcopy(payload);changed['comparison_groups'][0][key]+=' '+brand
        result=evaluate(changed,evidence)
        assert not result['overall_pass'],key


def test_general_topic_nutrition_or_ingredient_is_not_a_brand_violation():
    payload,evidence,_=accepted_fixture()
    candidate=deepcopy(payload)
    candidate['how_to_choose']+=' レチノールなど成分については一般的な使用上の注意を確認しましょう。'
    assert evaluate(candidate,evidence)['general_product_free_pass']


def test_writer_and_editorial_plan_do_not_force_sales_copy_or_description_length():
    topic,evidence,_=accepted_fixture()
    p=_prompt(topic,evidence)
    plan=build_editorial_plan(topic,evidence)
    e04=next(x['instruction'] for x in plan['requirements'] if x['id']=='E04')
    assert '文字数の最低・最高条件も' in p
    assert '商品説明の最低文字数' not in p
    assert '160〜260' not in p
    assert '文字数の下限を強制しない' in e04
    assert 'SEO・AEO' in p
    assert '商品カセット以外' in p


def test_indirect_link_to_article_products_in_general_section_fails():
    payload,evidence,_=accepted_fixture()
    candidate=deepcopy(payload)
    candidate['conclusion']+=' この記事で紹介した商品から選んでください。'
    result=evaluate(candidate,evidence)
    assert result['general_product_references']
    assert not result['overall_pass']


def test_existing_factual_claim_gates_remain_intact():
    from comparison_acceptance import product_issues
    payload,evidence,_=accepted_fixture()
    changed=deepcopy(evidence)
    changed['products'][1]['amazon_title']=changed['products'][0]['amazon_title']
    assert product_issues(changed)


def test_identical_short_endings_not_a_hard_failure_without_repeated_content():
    payload,evidence,_=accepted_fixture()
    candidate=deepcopy(payload)
    # Only the tail phrase is shared; product identity and facts remain unique.
    for product in candidate['products']:
        product['description']=product['description'].rstrip('。')+'という選択肢です。'
    qa=evaluate(candidate,evidence)
    assert qa['distinct_openings_endings_pass'] is False
    assert qa['no_repeated_sentences_pass'] is True
    assert qa['overall_pass'] is True
