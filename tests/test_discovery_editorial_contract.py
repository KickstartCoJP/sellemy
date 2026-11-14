"""Regression: discovery/editorial only; article schema and UI stay unchanged."""
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
from editorial_plan import build_editorial_plan
from writer_runtime import _prompt
from editorial_qa import review_prompt, make_input
class DiscoveryContractTests(unittest.TestCase):
 def setUp(self):
  self.topic={'slug':'example','category':'beauty','comparison_axes':[{'id':x,'label':x} for x in 'abc']}
  self.evidence={'products':[{'ref':f'p{i}','amazon_title':f'item{i}'} for i in range(1,7)],'comparison_axes':self.topic['comparison_axes']}
 def test_existing_schema_and_layout_preserved(self):
  from writer_runtime import WRITER_JSON_SCHEMA
  self.assertIn('comparison_groups',WRITER_JSON_SCHEMA['required'])
  self.assertIn('how_to_choose',WRITER_JSON_SCHEMA['required'])
  self.assertIn('conclusion',WRITER_JSON_SCHEMA['required'])
  self.assertEqual(WRITER_JSON_SCHEMA['properties']['comparison_groups']['maxItems'],3)
  self.assertEqual(WRITER_JSON_SCHEMA['properties']['products']['maxItems'],6)
 def test_plan_no_longer_demands_head_to_head_comparison(self):
  p=build_editorial_plan(self.topic,self.evidence)
  self.assertIn('比較や優劣を強制しない',next(x['instruction'] for x in p['requirements'] if x['id']=='E04'))
  self.assertIn('紹介切り口',next(x['instruction'] for x in p['requirements'] if x['id']=='E03'))
 def test_writer_prioritizes_discovery(self):
  s=_prompt(self.topic,self.evidence)
  self.assertIn('商品同士の比較・優劣・差異説明は必須ではありません',s)
  self.assertIn('紹介切り口',s)
 def test_independent_editorial_qa_does_not_demand_comparison(self):
  from editorial_qa import CONTRACT
  self.assertIn('Comparing differences is OPTIONAL',CONTRACT)
  self.assertIn('three existing two-product groups',CONTRACT)
if __name__=='__main__':unittest.main()
