import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from writer_runtime import _prompt

def test_prior_main_overrun_produces_quantified_repair_instruction():
    text=_prompt({'slug':'slug'}, {'products':[]},previous_payload={'slug':'slug'},gate_feedback={
        'measurements':{'main_len':3469,'summary_len':198},
        'qa_failures':{'main_in_range':False,'summary_in_range':False}})
    assert 'exceeds the absolute maximum 3400 by 69' in text
    assert 'summary LENGTH: prior 198, required 130-170' in text
    assert 'GATE_FEEDBACK' in text

def test_length_correction_is_last_instruction_after_prior_payload():
    text=_prompt({'slug':'slug'}, {'products':[]},previous_payload={'slug':'slug'},gate_feedback={
        'measurements':{'main_len':3443}, 'qa_failures':{'main_in_range':False}})
    assert text.index('PREVIOUS_PAYLOAD') < text.index('FINAL MANDATORY REVISION CHECKLIST')
    assert 'no more than 3300' in text
