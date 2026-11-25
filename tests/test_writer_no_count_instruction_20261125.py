"""Writer receives selected products, not article-size editorial instructions."""
from writer_runtime import _prompt
from quality_fixtures import accepted_fixture


def test_writer_prompt_does_not_call_out_product_count_or_group_count():
    topic, evidence, _ = accepted_fixture()
    prompt = _prompt(topic, evidence)
    directive_text = prompt.split('TOPIC:\n',1)[0]
    for banned in ('6つの商品','6件に反復','6つの異なる商品','2商品ずつ3組','3組の割当','3組の商品','6選','six product assignments'):
        assert banned not in directive_text
    assert 'Evidenceで確定した商品と掲載順・割当を維持' in directive_text
    assert '各商品の魅力と推薦理由' in directive_text


def test_writer_revision_prompt_keeps_assignments_without_count_language():
    topic, evidence, _ = accepted_fixture()
    prompt = _prompt(topic, evidence, previous_payload={'slug':topic.get('slug')}, gate_feedback={})
    assert 'change the six product assignments' not in prompt
    assert 'change the existing product assignments' in prompt
