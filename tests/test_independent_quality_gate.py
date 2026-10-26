import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from independent_quality_gate import require_receipts,issue_receipts,QualityGateError
class GateTests(unittest.TestCase):
 def test_no_receipts_blocks_publication(self):
  with patch('independent_quality_gate.product_issues',return_value=[]),patch('independent_quality_gate.require_acceptance'):
   with self.assertRaises(QualityGateError):require_receipts({}, {}, {}, {})
 def test_stale_receipts_block_publication(self):
  p={'h1':'A','lead':'B','summary':'C','how_to_choose':'D','conclusion':'E','comparison_groups':[{'title':'X','angle':'Y'}]*3,'products':[{'ref':str(i),'h3':'製品','description':'特徴'} for i in range(6)]}
  with patch('independent_quality_gate.product_issues',return_value=[]),patch('independent_quality_gate.require_acceptance'):
   with self.assertRaises(QualityGateError):require_receipts(p, {}, {}, {'editorial':{'verdict':'PASS','article_sha256':'old','contract_version':'old','reason':'fine','quotes':[]}})
 def test_evidence_missing_blocks_before_editorial_review(self):
  with patch('independent_quality_gate.product_issues',return_value=['product missing']):
   with self.assertRaises(QualityGateError) as err:issue_receipts({}, {}, {})
   self.assertEqual(err.exception.gate,'product')
if __name__=='__main__':unittest.main()

class PrelaunchNoPublishTests(unittest.TestCase):
 def test_real_reviewer_transport_failure_is_infrastructure_not_editorial(self):
  from independent_quality_gate import _judge,QualityGateError
  with patch('independent_quality_gate.subprocess.run',side_effect=__import__('subprocess').TimeoutExpired('codex',210)):
   with self.assertRaises(QualityGateError) as caught:_judge('article')
  self.assertEqual(caught.exception.gate,'reviewer_runtime')
 def test_product_fail_never_calls_reviewer(self):
  reviewer=unittest.mock.Mock(side_effect=AssertionError('should not call reviewer'))
  with patch('independent_quality_gate.product_issues',return_value=['missing evidence']):
   with self.assertRaises(QualityGateError):issue_receipts({}, {}, {}, judge=reviewer)
  reviewer.assert_not_called()
