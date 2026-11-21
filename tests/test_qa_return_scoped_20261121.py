"""CEO QA-return-only change: compact feedback without weakening any QA gate."""
import json

from qa_return import writer_feedback, writer_evidence
from writer_runtime import _prompt


def test_claim_failure_keeps_action_and_omits_successful_semantic_receipt():
    feedback = {
        'semantic_review': {'status': 'ACCEPTED', 'duplicate_pair_checks': [{'items': ['x'] * 3000}]},
        'independent_quality_gate': {'gate': 'claim', 'issues': [
            '削除対象の具体文言: ["主成分は硫酸マグネシウムで"]']},
        'qa_failures': {'overall_pass': False},
    }
    result = writer_feedback(feedback)
    assert result == {'independent_quality_gate': feedback['independent_quality_gate']}
    assert len(json.dumps(result, ensure_ascii=False)) < 250


def test_editorial_and_machine_qa_failures_keep_all_actionable_issues():
    feedback = {
        'independent_quality_gate': {'gate': 'editorial', 'issues': ['p5 repeats the same benefit']},
        'qa_failures': {'summary_in_range': False, 'overall_pass': False,
                        'distinct_openings_endings_pass': False},
        'measurements': {'summary_len': 103, 'main_len': 2200},
        'ceo_rework': 'Preserve six products; strengthen individual recommendations.',
        'duplicate_endings': {'したい人におすすめです。': [0, 5]},
        'semantic_review': {'status': 'ACCEPTED', 'duplicate_pair_checks': ['large'] * 1000},
    }
    result = writer_feedback(feedback)
    assert result['measurements'] == {'summary_len': 103}
    assert 'distinct_openings_endings_pass' in result['qa_failures']
    assert result['duplicate_endings']['したい人におすすめです。'] == [0, 5]
    assert result['independent_quality_gate']['issues'] == ['p5 repeats the same benefit']
    assert result['ceo_rework'] == feedback['ceo_rework']
    assert 'semantic_review' not in result


def test_unresolved_semantic_issue_is_returned_without_pair_details():
    result = writer_feedback({'semantic_review': {
        'status': 'REVISION_REQUIRED', 'reason': 'p5 identity has no source anchor',
        'needs_product_reselection': False, 'duplicate_pair_checks': ['large'] * 1000}})
    assert result['semantic_issue']['reason'] == 'p5 identity has no source anchor'
    assert 'duplicate_pair_checks' not in json.dumps(result)


def test_product_sources_preserved_without_product_qa_receipt():
    evidence = {'products': [{'ref': 'p1', 'sources': [{'excerpt': 'verified'}]}],
                'product_acceptance': {'review': {'duplicate_pair_checks': ['large'] * 1000}}}
    reduced = writer_evidence(evidence)
    assert reduced['products'] is evidence['products']
    assert 'product_acceptance' not in reduced
    assert 'product_acceptance' in evidence


def test_writer_repair_prompt_keeps_claim_instruction_and_previous_prose():
    topic = {'slug': 'sample', 'category': 'dailygoods', 'reader_persona': {}}
    evidence = {'products': [{'ref': f'p{i}', 'amazon_title': f'Product {i}'} for i in range(1, 7)], 'comparison_axes': [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}, {'id': 'c', 'label': 'C'}], 'product_acceptance': {'duplicate_pair_checks': ['large'] * 1000}}
    old = {'products': [], 'summary': 'keep unchanged'}
    feedback = {'independent_quality_gate': {'gate': 'claim',
                'issues': ['削除対象の具体文言: ["主成分は硫酸マグネシウムで"]']},
                'semantic_review': {'status': 'ACCEPTED', 'duplicate_pair_checks': ['huge'] * 1000}}
    prompt = _prompt(topic, evidence, previous_payload=old, gate_feedback=feedback)
    assert '主成分は硫酸マグネシウムで' in prompt
    assert 'preserving every unaffected field verbatim' in prompt
    assert 'keep unchanged' in prompt
    assert 'duplicate_pair_checks' not in prompt
