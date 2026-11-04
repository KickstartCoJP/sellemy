import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from discovery_adapters import AutositeDiscoveryAdapter,AmazonSearchUnavailable
from growth_recovery import _backoff
from discovery_adapters import _amazon_search_browser


def test_visible_chrome_uses_isolated_vm_display():
    rows = [{'asin': 'B012345678', 'title': 'Item'}]
    with patch('subprocess.run', return_value=Mock(returncode=0, stdout=__import__('json').dumps(rows), stderr='')) as run:
        assert _amazon_search_browser('https://www.amazon.co.jp/s?k=test', 1) == rows
    argv = run.call_args.args[0]
    assert argv[:5] == ['/opt/homebrew/bin/colima', 'ssh', '--', 'xvfb-run', '-a']
    assert '/home/kickstart.guest/.ai-role-playwright/bin/python' in argv
    assert 'Google Chrome.app' not in ' '.join(argv)

def test_503_browser_failure_is_typed_retryable_outage():
    with patch('requests.get',return_value=Mock(status_code=503)),patch('discovery_adapters._amazon_search_browser',side_effect=AmazonSearchUnavailable('Amazon browser search status=503')):
        try:
            AutositeDiscoveryAdapter().search_amazon_live('camera SD')
        except AmazonSearchUnavailable as exc:
            assert '503' in str(exc)
        else:
            assert False,'Expected a retryable outage'

def test_product_infrastructure_outage_uses_backoff():
    assert _backoff('product',1,'runtime_infrastructure') == timedelta(minutes=1)
    assert _backoff('product',1,'missing_evidence') == timedelta(minutes=15)


def test_amazon_search_recovery_queue_rotates_without_dropping_jobs(tmp_path):
    import json
    from unittest.mock import patch
    from amazon_search_retry import next_themes, mark_restarted
    queue = tmp_path / 'queue.json'
    queue.write_text(json.dumps({'cursor': 0, 'entries': [
        {'slug': 'a', 'status': 'pending'},
        {'slug': 'b', 'status': 'pending'},
        {'slug': 'c', 'status': 'pending'}]}))
    with patch('amazon_search_retry.QUEUE', queue):
        assert next_themes(2) == ['a', 'b']
        assert next_themes(2) == ['c', 'a']
        mark_restarted('a')
        assert next_themes(2) == ['c', 'b']
    state = json.loads(queue.read_text())
    assert len(state['entries']) == 3
    assert state['entries'][0]['status'] == 'restarted'
