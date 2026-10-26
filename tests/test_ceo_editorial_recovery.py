from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from writer_runtime import _prompt
from planning_runtime import PLANNING_SCHEMA, validate_candidate, PlanningError
from qa import DESCRIPTION_HARD_MIN, MAIN_RANGE
import pytest


def test_ceo_rework_is_final_mandatory_instruction_and_persona_is_transferred():
    topic={'slug':'example','reader_persona':{
        'reader':'mobile photographers','knowledge_level':'expert',
        'purchase_goal':'dual card workflow','editorial_depth':'retain bus standards'}}
    prompt=_prompt(topic, {'products':[]}, previous_payload={'h1':'old'},
        gate_feedback={'ceo_rework':'Remove reader-facing QA disclaimers.'})
    assert 'READER_PERSONA' in prompt
    assert 'mobile photographers' in prompt
    assert 'MANDATORY CEO REWORK' in prompt
    assert prompt.index('PREVIOUS_PAYLOAD') < prompt.index('MANDATORY CEO REWORK')
    assert 'Remove reader-facing QA disclaimers.' in prompt


def test_new_planning_requires_reader_persona():
    assert 'reader_persona' in PLANNING_SCHEMA['properties']['candidates']['items']['required']
    from test_planning_and_selection import candidate
    item=candidate()
    validate_candidate(item)
    del item['reader_persona']
    with pytest.raises(PlanningError, match='reader_persona'):
        validate_candidate(item)


def test_no_artificial_per_product_or_total_main_minimum():
    assert DESCRIPTION_HARD_MIN == 0
    assert MAIN_RANGE[0] == 1
    prompt=_prompt({'slug':'example'}, {'products':[]})
    assert 'No per-product minimum' in prompt
    assert 'no minimum or filler' in prompt
