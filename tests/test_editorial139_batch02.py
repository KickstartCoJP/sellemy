import hashlib,json,pathlib,unittest
from html.parser import HTMLParser
ROOT=pathlib.Path(__file__).resolve().parents[1]
def digest(value):
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
class Tags(HTMLParser):
 def __init__(self):
  super().__init__();self.tags=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='meta' and (a.get('name')=='description' or a.get('property')=='og:description'):
   a['content']='EDITORIAL_DESCRIPTION'
  self.tags.append([tag,sorted(a.items())])
 def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs)
 def handle_endtag(self,tag):self.tags.append(['/ '+tag,[]])
class EditorialBatch02(unittest.TestCase):
 def test_exact_reviewed_versions_and_preservation(self):
  report=json.loads((ROOT/'reports/editorial139-batch02-20261115.json').read_text())
  self.assertEqual(len(report['articles']),9)
  self.assertEqual(len({r['slug'] for r in report['articles']}),9)
  for row in report['articles']:
   self.assertEqual(row['qa'],'PASS_CHANGED_PARTS_ONLY')
   for f in row['files']:
    with self.subTest(slug=row['slug'],path=f['path']):
     content=(ROOT/f['path']).read_bytes()
     self.assertEqual(hashlib.sha256(content).hexdigest(),f['after_sha256'])
     if f['path'].endswith('.html'):
      parser=Tags();parser.feed(content.decode())
      self.assertEqual(digest(parser.tags),row['preserved_html_signature'])
     else:
      payload=json.loads(content)
      self.assertEqual(digest(payload['products']),row['preserved_products_sha256'])
      self.assertEqual([g['product_refs'] for g in payload['comparison_groups']],row['preserved_group_refs'])
if __name__=='__main__':unittest.main()
