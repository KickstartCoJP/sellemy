import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from claim_qa import make_receipt,validate_receipt,VERSION
from editorial_qa import fingerprint
class ClaimQATests(unittest.TestCase):
 def setUp(self):
  self.subject={'article':{'products':[{'ref':'p1','description':'容量300ml'}]},'verified_sources':{'p1':'容量300ml'},'comparison_axes':[]}
 def test_supported_claim(self):
  v={'verdict':'PASS','reason':'source verified','findings':[{'product_ref':'p1','article_quote':'容量300ml','evidence_quote':'容量300ml','verdict':'SUPPORTED','reason':'same model'}]}
  self.assertEqual(validate_receipt(make_receipt(v,self.subject),self.subject),[])
 def test_other_product_evidence_rejected(self):
  v={'verdict':'PASS','reason':'verified','findings':[{'product_ref':'p1','article_quote':'容量300ml','evidence_quote':'別の資料','verdict':'SUPPORTED','reason':'test'}]}
  with self.assertRaises(ValueError):make_receipt(v,self.subject)
 def test_unsupported_cannot_pass(self):
  v={'verdict':'PASS','reason':'verified','findings':[{'product_ref':'p1','article_quote':'容量300ml','evidence_quote':'','verdict':'UNSUPPORTED','reason':'missing'}]}
  with self.assertRaises(ValueError):make_receipt(v,self.subject)
if __name__=='__main__':unittest.main()
