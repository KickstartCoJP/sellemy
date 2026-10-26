import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from editorial_qa import make_input,fingerprint,validate_verdict,VERSION
class EditorialQATests(unittest.TestCase):
 def setUp(self):
  self.article={'h1':'比較','lead':'導入','summary':'要約','how_to_choose':'選び方','conclusion':'結論','comparison_groups':[{'title':str(i),'angle':'用途'} for i in range(3)],'products':[{'ref':'p'+str(i),'h3':'商品'+str(i),'description':'説明'+str(i)} for i in range(1,7)]}
 def test_no_evidence_in_editorial_input(self):
  s=make_input({**self.article,'secret_evidence':'should_not_pass'})
  self.assertNotIn('secret_evidence',str(s))
 def test_stale_article_fails(self):
  s=make_input(self.article);v={'contract_version':VERSION,'article_sha256':fingerprint(s),'verdict':'PASS','reason':'推薦が明確','quotes':[]}
  self.assertEqual(validate_verdict(v,s),[])
  edited={**self.article,'conclusion':'別の結論'}
  self.assertIn('stale article hash',validate_verdict(v,make_input(edited)))
 def test_unanchored_failure_fails_closed(self):
  s=make_input(self.article);v={'contract_version':VERSION,'article_sha256':fingerprint(s),'verdict':'FAIL','reason':'不適切','quotes':['存在しない表現']}
  self.assertIn('quote not grounded in article',validate_verdict(v,s))
if __name__=='__main__': unittest.main()

class EditorialIsolationTests(unittest.TestCase):
 def test_prompt_scoped_to_one_article(self):
  from editorial_qa import review_prompt,make_input
  p={'h1':'A','lead':'B','summary':'C','how_to_choose':'D','conclusion':'E','comparison_groups':[{'title':'X','angle':'Y'}]*3,'products':[{'ref':str(i),'h3':'製品','description':'特徴'} for i in range(6)]}
  self.assertIn('ONLY this one article',review_prompt(make_input(p)))
 def test_reject_other_article_quote(self):
  from editorial_qa import make_receipt,make_input
  p={'h1':'A','lead':'B','summary':'C','how_to_choose':'D','conclusion':'E','comparison_groups':[{'title':'X','angle':'Y'}]*3,'products':[{'ref':str(i),'h3':'製品','description':'特徴'} for i in range(6)]}
  with self.assertRaisesRegex(ValueError,'quote not grounded'):
   make_receipt({'verdict':'PASS','reason':'use-case recommendations','quotes':['違う記事の文章']},make_input(p))
