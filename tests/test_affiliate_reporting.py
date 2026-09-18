import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
import affiliate_reporting

class AffiliateReportingTests(unittest.TestCase):
    def test_provider_pending_is_missing_not_zero(self):
        states=affiliate_reporting.provider_auth_state()
        self.assertEqual({x['provider'] for x in states},{'rakuten','valuecommerce'})
        self.assertTrue(all(x['state']=='source_auth_pending' for x in states))
        self.assertTrue(all('missing_not_zero' in x['zero_semantics'] for x in states))

    def test_refresh_keeps_revenue_profit_null_when_amazon_reports_empty(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state.json'
            amazon={'provider':'amazon','state':'api_verified_reports_empty','reports':[],
                    'report_count':0,'zero_semantics':'reports_empty_is_missing_not_zero'}
            with patch.object(affiliate_reporting,'STATE_PATH',state), \
                 patch.object(affiliate_reporting,'STATE_DIR',Path(d)), \
                 patch.object(affiliate_reporting,'amazon_inventory',return_value=amazon):
                result=affiliate_reporting.refresh_state()
            self.assertFalse(result['revenue_actual_ready'])
            self.assertIsNone(result['revenue_value'])
            self.assertFalse(result['profit_actual_ready'])
            self.assertEqual(json.loads(state.read_text())['providers'][0]['report_count'],0)


    def test_amazon_ineligible_state_preserves_missing_semantics(self):
        state={
            'provider':'amazon','state':'creators_api_ineligible','report_count':0,
            'eligibility_probe':{'state':'associate_not_eligible','http_status':403},
            'current_primary':'Associates Central official report export',
            'zero_semantics':'reports_empty_or_api_ineligible_is_missing_not_zero',
        }
        self.assertEqual(state['eligibility_probe']['http_status'],403)
        self.assertEqual(state['current_primary'],'Associates Central official report export')
        self.assertIn('missing_not_zero',state['zero_semantics'])

    def test_official_export_ingest_is_content_addressed_and_unparsed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); src=root/'amazon.csv'; src.write_text('x,y\n1,2\n')
            raw=root/'raw'
            with patch.object(affiliate_reporting,'RAW_DIR',raw):
                first=affiliate_reporting.ingest_official_export('amazon',src)
                second=affiliate_reporting.ingest_official_export('amazon',src)
            self.assertEqual(first['sha256'],second['sha256'])
            self.assertEqual(first['raw_file'],second['raw_file'])
            self.assertFalse(first['parsed'])
            self.assertTrue(Path(first['raw_file']).is_file())

if __name__=='__main__': unittest.main()
