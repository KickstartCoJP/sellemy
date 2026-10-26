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


def test_informative_length_floor_without_old_padding_targets():
    assert DESCRIPTION_HARD_MIN == 160
    assert MAIN_RANGE == (2400, 3400)
    prompt=_prompt({'slug':'example'}, {'products':[]})
    assert 'at least 160 meaningful characters' in prompt
    assert 'without filler' in prompt


def test_writer_stage_scope_excludes_evidence_audit_history():
    from codex_provider import _stage_brief_text
    from comparison_acceptance import CONTRACT
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    brief=root/'.runtime/sellemy-codex/brief.md'
    writer=_stage_brief_text(brief,'writer')
    assert 'source anchor' not in writer
    assert '明示仕様→用途との照合' not in writer
    assert '## Learned' in writer
    assert '冒頭と結び' in writer or '用途' in writer
    assert 'source anchor' in _stage_brief_text(brief,'planning')
    prompt=_prompt({'slug':'article'}, {'products':[]})
    assert CONTRACT not in prompt
    assert 'Ground material claims in supplied facts' in prompt


def test_historic_publish_does_not_clear_new_ceo_review(monkeypatch):
    import growth_runtime as g
    monkeypatch.setattr(g, '_unfinished_jobs', lambda: [
        {'slug': 'old-live', 'category': 'beauty', 'current_stage': 'CHAT_REVIEW_PENDING'},
        {'slug': 'rework', 'category': 'beauty', 'current_stage': 'QA_FAILED'},
    ])
    def forbid(*args, **kwargs):
        raise AssertionError('historic publication lookup must not run before current CEO certification')
    monkeypatch.setattr(g, '_existing_publish_commit', forbid)
    assert g._reconcile_already_published_jobs() == []
