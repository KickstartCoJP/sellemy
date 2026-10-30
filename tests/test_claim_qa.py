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

class ClaimCoverageTests(unittest.TestCase):
 def setUp(self):
  self.subject={'article':{'products':[{'ref':f'p{i}','description':f'容量{i}ml'} for i in range(1,7)]},'verified_sources':{f'p{i}':f'容量{i}ml' for i in range(1,7)},'comparison_axes':[]}
 def test_empty_findings_cannot_pass(self):
  from claim_qa import make_receipt
  with self.assertRaisesRegex(ValueError,'empty claim findings'):
   make_receipt({'verdict':'PASS','reason':'all fine','findings':[]},self.subject)
 def test_partial_coverage_cannot_pass(self):
  from claim_qa import make_receipt
  with self.assertRaisesRegex(ValueError,'all six products'):
   make_receipt({'verdict':'PASS','reason':'fine','findings':[{'product_ref':'p1','article_quote':'容量1ml','evidence_quote':'容量1ml','verdict':'SUPPORTED'}]},self.subject)
 def test_complete_grounded_coverage(self):
  from claim_qa import make_receipt
  v={'verdict':'PASS','reason':'all supported','findings':[{'product_ref':f'p{i}','article_quote':f'容量{i}ml','evidence_quote':f'容量{i}ml','verdict':'SUPPORTED'} for i in range(1,7)]}
  self.assertEqual(make_receipt(v,self.subject)['verdict'],'PASS')

class ClaimCrossRefTests(unittest.TestCase):
 def test_article_quote_must_belong_to_same_product(self):
  from claim_qa import make_receipt
  subject={'article':{'products':[{'ref':'p1','description':'容量100ml'},{'ref':'p2','description':'容量200ml'}]},'verified_sources':{'p1':'容量200ml','p2':'容量200ml'},'comparison_axes':[]}
  v={'verdict':'PASS','reason':'claimed','findings':[{'product_ref':'p1','article_quote':'容量200ml','evidence_quote':'容量200ml','verdict':'SUPPORTED'},{'product_ref':'p2','article_quote':'容量200ml','evidence_quote':'容量200ml','verdict':'SUPPORTED'}]}
  with self.assertRaisesRegex(ValueError,'same-product'):make_receipt(v,subject)

def test_unverified_product_with_only_general_guidance_can_pass_claim_check():
    subject={'article':{'products':[{'ref':'p1','description':'日々の使い方を考えて選びましょう'}]},
             'verified_sources':{'p1':'商品名だけが判明'},'comparison_axes':[]}
    verdict={'verdict':'PASS','reason':'no concrete specifications asserted',
             'findings':[{'product_ref':'p1','article_quote':'日々の使い方を考えて選びましょう',
                          'evidence_quote':'','verdict':'NO_CLAIM','reason':'general buying guidance'}]}
    assert make_receipt(verdict,subject)['verdict']=='PASS'

def test_editorial_rework_receives_exact_unsupported_quote():
    from unittest.mock import patch
    from independent_quality_gate import issue_receipts,QualityGateError
    claim={'verdict':'FAIL','reason':'unsupported detail',
           'findings':[{'product_ref':'p1','article_quote':'容量300ml','verdict':'UNSUPPORTED'}]}
    with patch('independent_quality_gate.require_acceptance'), \
         patch('independent_quality_gate.editorial_input',return_value={}), \
         patch('independent_quality_gate.editorial_prompt',return_value='editorial'), \
         patch('independent_quality_gate.editorial_receipt',return_value={'verdict':'PASS'}), \
         patch('independent_quality_gate.claim_input',return_value={}), \
         patch('independent_quality_gate.claim_prompt',return_value='claim'), \
         patch('independent_quality_gate.claim_receipt',return_value=claim):
        try:
            issue_receipts({}, {}, {},judge=lambda prompt: {})
        except QualityGateError as error:
            assert error.gate=='claim'
            assert '容量300ml' in str(error)
            assert '削除' in str(error)
        else:
            raise AssertionError('unsupported statement must be rejected')
