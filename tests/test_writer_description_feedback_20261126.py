"""The Writer must correct specific short product descriptions after QA."""
from writer_runtime import _prompt
from tests.quality_fixtures import accepted_fixture


def test_revision_explicitly_targets_short_descriptions_without_affecting_products():
    topic,evidence,_=accepted_fixture()
    prompt=_prompt(topic,evidence,previous_payload={'slug':topic['slug']}, gate_feedback={
        'qa_failures': {'description_hard_min_pass':False,'description_target_pass':False},
        'measurements': {'description_lens':[179,198,191,194,171,131]}})
    assert 'MANDATORY DESCRIPTION REVISION' in prompt
    assert 'p6 (131 characters)' in prompt
    assert 'p5 (171 characters)' not in prompt
    assert '160–260 characters' in prompt
    assert 'Preserve every unaffected product' in prompt


def test_revision_handles_multiple_short_products_and_no_length_issue():
    topic,evidence,_=accepted_fixture()
    prompt=_prompt(topic,evidence,previous_payload={'slug':topic['slug']}, gate_feedback={
        'qa_failures':{'description_target_pass':False},
        'measurements':{'description_lens':[158,175,190,159,148,155]}})
    for ref in ('p1 (158 characters)','p4 (159 characters)','p5 (148 characters)','p6 (155 characters)'):
        assert ref in prompt
    assert 'p2 (175 characters)' not in prompt
    clean=_prompt(topic,evidence,previous_payload={'slug':topic['slug']},gate_feedback={})
    assert 'MANDATORY DESCRIPTION REVISION' not in clean
