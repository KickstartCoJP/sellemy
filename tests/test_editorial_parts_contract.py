"""Regression: Sellemy article parts, not article-total length, own editorial quality."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from editorial_plan import build_editorial_plan
from writer_runtime import _prompt
from qa import run_qa
from renderer import render_article
from quality_fixtures import accepted_fixture


def test_product_and_general_parts_have_distinct_editorial_responsibilities():
    payload, evidence, receipt = accepted_fixture()
    plan = build_editorial_plan({'slug': payload['slug']}, evidence)
    criteria = {r['id']: r for r in plan['requirements']}
    assert criteria['E01']['section'] == 'lead'
    assert '重複' in criteria['E02']['instruction']
    assert criteria['E03']['section'] == 'comparison_groups'
    assert '具体的' in criteria['E04']['instruction']
    assert '判断基準' in criteria['E05']['instruction']
    assert criteria['E06']['section'] == 'conclusion'
    prompt = _prompt({'slug': payload['slug']}, evidence)
    assert '記事全体や一般解説合計に文字数目標を設けず' in prompt
    assert '1800〜2800' not in prompt
    assert '700〜1100' not in prompt


def test_longer_valid_article_is_not_rejected_on_total_character_count():
    payload, evidence, receipt = accepted_fixture()
    qa = run_qa(render_article(payload, evidence), payload, evidence, acceptance=receipt)
    assert qa['main_len'] > 2800
    assert 'main_in_range' not in qa
    assert qa['overall_pass'] is True
