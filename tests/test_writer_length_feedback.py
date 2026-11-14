import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from writer_runtime import _prompt

def valid_editorial_evidence():
    return {'products':[{'ref':f'p{i}','amazon_title':f'Item {i}'} for i in range(1,7)],
            'comparison_axes':[{'id':'a','label':'A'},{'id':'b','label':'B'}]}

def test_prior_summary_overrun_does_not_trigger_length_rewrite():
    text=_prompt({'slug':'slug'}, valid_editorial_evidence(),previous_payload={'slug':'slug'},gate_feedback={
        'measurements':{'main_len':3469,'summary_len':198},
        'qa_failures':{'main_in_range':False,'summary_in_range':False}})
    assert 'exceeds the absolute maximum 3400' not in text
    assert 'CRITICAL summary LENGTH' not in text
    assert 'GATE_FEEDBACK' in text


def test_summary_below_minimum_still_triggers_writer_rewrite():
    text=_prompt({'slug':'slug'}, valid_editorial_evidence(),previous_payload={'slug':'slug'},gate_feedback={
        'measurements':{'summary_len':100,'how_to_choose_len':120},
        'qa_failures':{'summary_in_range':False,'how_to_choose_in_range':False}})
    assert 'summary LENGTH: prior 100, required at least 130' in text
    assert 'how_to_choose LENGTH: prior 120, required at least 190' in text


def test_lead_feedback_keeps_minimum_without_upper_limit():
    text=_prompt({'slug':'slug'}, valid_editorial_evidence(),previous_payload={'slug':'slug'},gate_feedback={
        'qa_failures':{'lead_in_range':False}})
    assert 'at least 180 Japanese characters' in text
    assert 'allowed 180-230' not in text
    assert '180〜230' not in text

def test_length_correction_is_last_instruction_after_prior_payload():
    text=_prompt({'slug':'slug'}, valid_editorial_evidence(),previous_payload={'slug':'slug'},gate_feedback={
        'measurements':{'main_len':3443}, 'qa_failures':{'main_in_range':False}})
    assert 'PREVIOUS_PAYLOAD' in text
    assert 'FINAL MANDATORY REVISION CHECKLIST' not in text

def test_short_article_after_unsupported_claim_removal_has_replacement_instruction():
    text=_prompt({'slug':'slug'},valid_editorial_evidence(),previous_payload={'slug':'slug'},gate_feedback={
        'measurements':{'main_len':2150},'qa_failures':{'main_in_range':False},
        'independent_quality_gate':{'gate':'claim','issues':['削除対象の具体文言: 容量300ml']}})
    assert 'below minimum 2400' not in text
    assert 'Delete unsupported' not in text
    assert '削除対象の具体文言' in text
