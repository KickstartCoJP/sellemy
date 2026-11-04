"""Non-network acceptance checks for separate stage process ownership."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))


def test_runtime_has_zero_business_artifact_io():
    source = (ROOT / 'pipeline' / 'growth_runtime.py').read_text()
    for expression in ('read_artifact(', 'write_artifact(', 'save_writer_result(',
                       'save_eyecatch(', 'materialize_for_publish(',
                       'open(', '.read_text(', '.read_bytes(', '.write_text(', '.write_bytes('):
        assert expression not in source


def test_stage_worker_reads_existing_artifact_for_ceo_hold_only():
    from role_stage_client import execute_role
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        folder = root / 'jobs' / 'sample-article'
        folder.mkdir(parents=True)
        (folder / 'state.json').write_text(json.dumps({
            'schema_version': 1, 'slug': 'sample-article', 'category': 'gadget',
            'status': 'ACTIVE', 'current_stage': 'CHAT_REVIEW_PENDING'}))
        (folder / 'topic.json').write_text(json.dumps({'slug': 'sample-article',
                                                       'category': 'gadget'}))
        (folder / 'evidence.json').write_text(json.dumps({'products': []}))
        with patch.dict(os.environ, {'SELLEMY_GROWTH_RECOVERY_ROOT': temp}):
            receipt = execute_role('inspect', 'sample-article')
        assert receipt['action'] == 'ceo_review'
        assert receipt['candidate_count'] == 0


def test_reviewer_does_not_rewrite_writer_payload():
    from growth_recovery import save_writer_result
    import inspect
    source = inspect.getsource(save_writer_result)
    assert 'persist_payload: bool = True' in source
    assert 'if persist_payload:' in source


def test_role_operations_preserve_existing_file_names():
    from role_execution_writer import writer, reviewer
    from role_execution_publish import designer, publish
    from role_stage_client import execute_role
    assert callable(writer) and callable(reviewer)
    assert callable(designer) and callable(publish) and callable(execute_role)


def test_reviewer_revocation_is_owned_by_real_subprocess_and_keeps_payload(tmp_path):
    from role_stage_client import execute_role
    root = tmp_path / 'recovery'
    directory = root / 'jobs' / 'sample-article'
    directory.mkdir(parents=True)
    existing_payload = '{"status":"READY","signature":"unchanged"}\n'
    (directory / 'payload.json').write_text(existing_payload, encoding='utf-8')
    with patch.dict(os.environ, {'SELLEMY_GROWTH_RECOVERY_ROOT': str(root)}):
        receipt = execute_role('review_reset', 'sample-article')
    assert receipt == {'review_authorizations_revoked': True}
    for name in ('qa-certification.json', 'independent-quality-receipts.json',
                 'chat-publish-approval.json'):
        assert (directory / name).read_text(encoding='utf-8').strip() == '{}'
    assert (directory / 'payload.json').read_text(encoding='utf-8') == existing_payload


def test_each_role_has_own_900_second_timeout_and_kills_only_its_tree():
    from role_stage_client import execute_role, RoleStageFailure
    from unittest.mock import MagicMock
    process = MagicMock()
    process.pid = 123456
    process.communicate.side_effect = [subprocess.TimeoutExpired('role', 900), ('', '')]
    with patch('role_stage_client.subprocess.Popen', return_value=process) as opened, \
         patch('role_stage_client.os.killpg') as kill_group:
        try:
            execute_role('fresh_create', request={'candidates': []})
        except RoleStageFailure as exc:
            assert 'fresh_create exceeded its 15-minute limit' in str(exc)
        else:
            raise AssertionError('role timeout was ignored')
    assert opened.call_args.kwargs['start_new_session'] is True
    assert process.communicate.call_args_list[0].kwargs['timeout'] == 900
    kill_group.assert_called_once_with(123456, __import__('signal').SIGKILL)


def test_runtime_shell_no_longer_caps_entire_job_at_900_seconds():
    script = (ROOT / 'ops' / 'run-growth.sh').read_text()
    assert 'run_with_timeout.py 900' not in script
    assert 'pipeline/growth_runtime.py --publish --scheduled' in script
    assert 'pipeline/growth_runtime.py --publish --recovery-only' in script
