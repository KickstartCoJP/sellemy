import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from discovery_adapters import AutositeDiscoveryAdapter,AmazonSearchUnavailable
from growth_recovery import _backoff

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
