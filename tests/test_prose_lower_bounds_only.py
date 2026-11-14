"""Article prose may exceed old ceilings but cannot fall below existing floors."""
from copy import deepcopy

from quality_fixtures import accepted_fixture
from renderer import render_article
from qa import run_qa, LEAD_MIN, SUMMARY_MIN, HOW_TO_CHOOSE_MIN, DESCRIPTION_HARD_MIN


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
    assert metrics['description_hard_min_pass']
    assert metrics['overall_pass']


def test_existing_lower_bounds_still_reject_short_prose():
    payload, evidence, _ = accepted_fixture()
    for field, minimum, flag in (
        ('lead', LEAD_MIN, 'lead_in_range'),
        ('summary', SUMMARY_MIN, 'summary_in_range'),
        ('how_to_choose', HOW_TO_CHOOSE_MIN, 'how_to_choose_in_range'),
    ):
        candidate = deepcopy(payload)
        candidate[field] = '短' * (minimum - 1)
        metrics = run_qa(render_article(candidate, evidence), candidate, evidence,
                         acceptance=None, require_semantic_acceptance=False)
        assert not metrics[flag], field
        assert not metrics['overall_pass'], field


def test_product_description_minimum_and_conclusion_no_bounds_remain():
    payload, evidence, _ = accepted_fixture()
    assert DESCRIPTION_HARD_MIN == 160
    candidate = deepcopy(payload)
    candidate['conclusion'] += '読者にとって大切な条件を整理しましょう。' * 20
    metrics = run_qa(render_article(candidate, evidence), candidate, evidence,
                     acceptance=None, require_semantic_acceptance=False)
    assert metrics['description_hard_min_pass']
    assert 'conclusion_in_range' not in metrics
