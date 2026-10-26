import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from api_role_binding import require_codex_settings, SellemyRouteConfigError
from codex_provider import _required_effort

SURFACE = 'bu-codex-sellemy-writer'
STAGE = 'writer'

def fixture(tmp_path, *, effort='medium', model='gpt-6-sol'):
    p=tmp_path/'roles.json'
    p.write_text(json.dumps({'sellemy-writer':{'unit':'BU-002','api_percent':0,
        'codex_model':model,'codex_effort':effort}}))
    return p

def test_saved_medium_is_used_without_environment_fallback(tmp_path):
    p=fixture(tmp_path)
    assert require_codex_settings(SURFACE, STAGE, path=p)==('gpt-6-sol','medium')
    with patch.dict('os.environ', {'SELLEMY_API_ROLE_DRAFTS_PATH':str(p)}, clear=False):
        assert _required_effort(SURFACE,STAGE,'gpt-6-sol')=='medium'

@pytest.mark.parametrize('effort', ['',None,'invalid','auto'])
def test_missing_and_invalid_effort_error(tmp_path, effort):
    p=fixture(tmp_path,effort=effort)
    with pytest.raises(SellemyRouteConfigError):
        require_codex_settings(SURFACE,STAGE,path=p)

def test_missing_file_error(tmp_path):
    with pytest.raises(SellemyRouteConfigError):
        require_codex_settings(SURFACE,STAGE,path=tmp_path/'missing.json')

def test_wrong_model_error(tmp_path):
    with pytest.raises(SellemyRouteConfigError, match='mismatch'):
        require_codex_settings(SURFACE,STAGE,model='other',path=fixture(tmp_path))

def test_unknown_stage_fails_closed(tmp_path):
    with pytest.raises(SellemyRouteConfigError,match='unknown'):
        require_codex_settings(SURFACE,'no-such-stage',path=fixture(tmp_path))

def test_api_not_zero_error(tmp_path):
    p=fixture(tmp_path)
    d=json.loads(p.read_text());d['sellemy-writer']['api_percent']=20;p.write_text(json.dumps(d))
    with pytest.raises(SellemyRouteConfigError):
        require_codex_settings(SURFACE,STAGE,path=p)
