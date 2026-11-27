from copy import deepcopy

from quality_fixtures import accepted_fixture
from payload_schema import validate_payload
from renderer import render_article
from qa import run_qa
from writer_runtime import _prompt
from editorial_plan import build_editorial_plan


def test_optional_general_section_not_forced_and_not_rendered():
    payload, evidence, _ = accepted_fixture()
    draft = deepcopy(payload)
    draft['how_to_choose'] = ''
    validate_payload(draft)
    page = render_article(draft, evidence)
    assert 'id="how-to-choose"' not in page
    assert draft['conclusion'] in page
    report = run_qa(page, draft, evidence, acceptance=None, require_semantic_acceptance=False)
    assert report['how_to_choose_in_range'] is False


def test_writer_instruction_no_mandatory_selection_section():
    payload, evidence, _ = accepted_fixture()
    plan = build_editorial_plan({'slug': payload['slug']}, evidence)
    rule = next(x for x in plan['requirements'] if x['id'] == 'E05')
    assert '任意' in rule['instruction']
    assert '空欄' in rule['instruction']
    prompt = _prompt({'slug': payload['slug']}, evidence)
    assert '空文字列' in prompt
    assert '補助欄の名称に合わせた見出しを作らない' in prompt
