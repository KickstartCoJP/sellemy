"""Article prose may exceed old ceilings but cannot fall below existing floors."""
from copy import deepcopy

from quality_fixtures import accepted_fixture
from renderer import render_article
from qa import run_qa, LEAD_MIN, SUMMARY_MIN, HOW_TO_CHOOSE_MIN


def test_long_lead_summary_and_buying_guide_are_not_machine_qa_failures():
    payload, evidence, receipt = accepted_fixture()
    lengthy = deepcopy(payload)
    for field in ('lead', 'summary', 'how_to_choose'):
        lengthy[field] += '商品ごとの利用場面に合う特徴を確認して選ぶことが重要です。' * 12
    metrics = run_qa(render_article(lengthy, evidence), lengthy, evidence,
                     acceptance=None, require_semantic_acceptance=False)
    assert metrics['lead_len'] > 230
    assert metrics['summary_len'] > 170
    assert metrics['how_to_choose_len'] > 270
    assert metrics['lead_in_range']
    assert metrics['summary_in_range']
    assert metrics['how_to_choose_in_range']
    assert metrics['overall_pass']


def test_existing_lower_bounds_still_reject_short_prose():
    payload, evidence, _ = accepted_fixture()
    for field, minimum, flag in (('lead', LEAD_MIN, 'lead_in_range'),):
        candidate = deepcopy(payload)
        candidate[field] = '短' * (minimum - 1)
        metrics = run_qa(render_article(candidate, evidence), candidate, evidence,
                         acceptance=None, require_semantic_acceptance=False)
        assert not metrics[flag], field
        assert not metrics['overall_pass'], field


def test_short_product_description_does_not_fail_article_and_conclusion_has_no_rigid_limit():
    payload, evidence, _ = accepted_fixture()
    candidate = deepcopy(payload)
    candidate['products'][0]['description'] = '製品名から確認できる特徴を紹介。'
    candidate['conclusion'] += '一般的な選び方を整理しましょう。' * 10
    metrics = run_qa(render_article(candidate, evidence), candidate, evidence,
                     acceptance=None, require_semantic_acceptance=False)
    assert metrics['description_lens'][0] < 160
    assert 'description_hard_min_pass' not in metrics
    assert 'conclusion_in_range' not in metrics
    assert metrics['overall_pass']


def test_short_summary_and_buying_guide_are_not_forced_to_fill_characters():
    payload,evidence,_=accepted_fixture()
    short=deepcopy(payload)
    short['summary']='要点を簡潔に整理します。'
    short['how_to_choose']='選び方は一般的な用途に沿って考えます。'
    qa=run_qa(render_article(short,evidence),short,evidence,
              acceptance=None,require_semantic_acceptance=False)
    assert qa['summary_in_range'] is False
    assert qa['how_to_choose_in_range'] is False
    assert qa['overall_pass'] is True
